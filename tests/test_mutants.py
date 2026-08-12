"""Mutation-harness engine core: findings, specs, and the Mutant quadruple.

Normalizes canvas.py's lint prose and the ported instruments (see
instruments.py) into one finding shape, then provides the vocabulary a
mutation catalogue is written in: a `FindingSpec`/`Silence` asserts a
finding fired (or didn't) with the right magnitude and direction, a
`Neighbour` is the control scene an operator must leave untouched, and a
`Mutant` pairs a broken scene with both expectations. Detector crashes
never take down a run — they surface as `detector-error` findings.
"""
from __future__ import annotations

import copy
import math
import re
import sys
import unittest
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] /
                       "skills" / "wysiwyg-grilling" / "scripts"))
import canvas
import instruments
from tests_helpers import el

# ---------------------------------------------------------------------------
# Detector registry: lint detectors carry a compiled regex run over every
# lint channel (errors + warnings + notes); instrument detectors carry an
# adapter over the ported instruments.* functions. Message templates below
# were verified against canvas.py (lines 5083, 5097-5102, 5475).
# ---------------------------------------------------------------------------

_ENDPOINT_RE = re.compile(
    r"arrow (?P<element>[\w-]+) claims to bind .+ but its "
    r"(?:start|end) point ends (?P<mag>\d+)px "
    r"(?P<dir>away|inside the shape)")
_RUNS_INSIDE_RE = re.compile(
    r"arrow (?P<element>[\w-]+) enters .+ and runs (?P<mag>\d+)px inside")
_PASSES_THROUGH_RE = re.compile(
    r"arrow (?P<element>[\w-]+) passes through .+, which is neither its "
    r"source nor destination")


def _collect_crossings(els: list[dict]) -> list[dict]:
    """One finding whose magnitude is the (buggy) crossing-pair count.

    Args:
        els: The scene's element list.

    Returns:
        A single-item findings list for the `crossings_count` check.
    """
    n, _pairs = instruments.edge_crossing_pairs(els)
    return [{"check": "crossings_count", "element": None,
             "magnitude": float(n), "direction": None,
             "raw": "crossing pairs=%d" % n}]


def _collect_corridors(els: list[dict]) -> list[dict]:
    """One finding per arrow pair sharing a collinear corridor.

    Args:
        els: The scene's element list.

    Returns:
        A findings list for the `shared_corridor` check, one per hit.
    """
    hits = instruments.shared_corridors(els)
    return [{"check": "shared_corridor",
             "element": "%s+%s" % (h["a"], h["b"]),
             "magnitude": h["overlap"], "direction": None,
             "raw": "corridor %s~%s overlap=%.1fpx"
                    % (h["a"], h["b"], h["overlap"])}
            for h in hits]


def _collect_false_bidi(els: list[dict]) -> list[dict]:
    """One finding per arrow pair that reads as a false bidirectional line.

    Args:
        els: The scene's element list.

    Returns:
        A findings list for the `false_bidi` check, one per hit.
    """
    hits = instruments.false_bidi(els)
    return [{"check": "false_bidi", "element": "%s+%s" % (h["a"], h["b"]),
             "magnitude": None, "direction": None,
             "raw": "false bidi %s~%s" % (h["a"], h["b"])}
            for h in hits]


def _collect_float_diamond(els: list[dict]) -> list[dict]:
    """One finding per arrow endpoint floating off its diamond's boundary.

    Args:
        els: The scene's element list.

    Returns:
        A findings list for the `float_diamond` check, one per hit.
    """
    hits = instruments.float_diamond(els)
    return [{"check": "float_diamond", "element": h["arrow"],
             "magnitude": h["gap"], "direction": None,
             "raw": "float diamond arrow=%s node=%s gap=%.1fpx"
                    % (h["arrow"], h["node"], h["gap"])}
            for h in hits]


DETECTORS: dict[str, dict] = {
    "endpoint_gap": {"lint_re": _ENDPOINT_RE,
                     "dirmap": {"away": "outside",
                                "inside the shape": "inside"}},
    "crosses_through_bound": {"lint_re": _RUNS_INSIDE_RE},
    "passes_through_foreign": {"lint_re": _PASSES_THROUGH_RE},
    "crossings_count": {"collect": _collect_crossings},
    "shared_corridor": {"collect": _collect_corridors},
    "false_bidi": {"collect": _collect_false_bidi},
    "float_diamond": {"collect": _collect_float_diamond},
    # render tier registers "ablation_existence" / "ablation_continuity"
    # in test_mutants_render.py by updating this dict at import.
}


def collect_findings(elements: list[dict],
                     registry: dict[str, dict] | None = None) -> list[dict]:
    """Run every detector over a scene and normalize their findings.

    `canvas.lint_layout` runs once; each registry entry then runs
    independently so one detector's crash can't take the others down —
    it contributes a `detector-error` finding named after the detector
    instead.

    Args:
        elements: The scene's element list.
        registry: Detector registry to use in place of the module-level
            `DETECTORS`; mainly for tests that inject a broken detector.

    Returns:
        The normalized findings list: `{"check", "element", "magnitude",
        "direction", "raw"}` per hit, plus one `detector-error` finding
        per detector (including the lint pass itself) that raised.
    """
    reg = DETECTORS if registry is None else registry
    findings: list[dict] = []
    try:
        lint = canvas.lint_layout(elements, artifact_type="flow")
        lines = lint["errors"] + lint["warnings"] + lint["notes"]
    except Exception as exc:
        lines = []
        findings.append({"check": "detector-error", "element": "lint_layout",
                         "magnitude": None, "direction": None,
                         "raw": "%s: %s" % (type(exc).__name__, exc)})
    for dname, spec in reg.items():
        try:
            lint_re = spec.get("lint_re")
            if lint_re is not None:
                for line in lines:
                    m = lint_re.search(line)
                    if not m:
                        continue
                    gd = m.groupdict()
                    mag = gd.get("mag")
                    raw_dir = gd.get("dir")
                    findings.append({
                        "check": dname, "element": gd.get("element"),
                        "magnitude": float(mag) if mag is not None else None,
                        "direction": (spec.get("dirmap", {})
                                     .get(raw_dir, raw_dir)
                                     if raw_dir is not None else None),
                        "raw": line})
            collect = spec.get("collect")
            if collect is not None:
                findings.extend(collect(elements))
        except Exception as exc:
            findings.append({"check": "detector-error", "element": dname,
                             "magnitude": None, "direction": None,
                             "raw": "%s: %s" % (type(exc).__name__, exc)})
    return findings


class FindingSpec:
    """An expected finding: check, optional element, magnitude, direction.

    Args:
        check: The finding's `check` name.
        element: If set, only findings with a matching `element` count.
        magnitude: Optional `(value, rel_tol)` pair; a candidate finding
            must have `abs(magnitude - value) <= rel_tol * value`.
        direction: Optional expected `direction` string.
    """

    def __init__(self, check: str, element: str | None = None,
                magnitude: tuple[float, float] | None = None,
                direction: str | None = None) -> None:
        """Store the expected check, element, magnitude, and direction."""
        self.check = check
        self.element = element
        self.magnitude = magnitude
        self.direction = direction

    def matches(self, findings: list[dict]) -> str | None:
        """Check whether some finding satisfies this spec.

        Args:
            findings: The normalized findings list from `collect_findings`.

        Returns:
            None if a finding matches on check, element, magnitude
            tolerance, and direction; otherwise a one-line description
            of the closest candidate's mismatch.
        """
        candidates = [f for f in findings if f["check"] == self.check
                     and (self.element is None
                          or f["element"] == self.element)]
        if not candidates:
            where = " element=%r" % self.element if self.element else ""
            return "no finding of check=%r%s" % (self.check, where)
        for f in candidates:
            if self._satisfies(f):
                return None
        nearest = self._nearest(candidates)
        return "fired but %s; expected %s" % (
            self._describe(nearest), self._want())

    def _satisfies(self, f: dict) -> bool:
        """Whether one candidate finding meets this spec's constraints.

        Args:
            f: A single normalized finding with the right check/element.

        Returns:
            True if the finding's magnitude (if constrained) and
            direction (if constrained) both match.
        """
        if self.magnitude is not None:
            value, rel_tol = self.magnitude
            if f["magnitude"] is None:
                return False
            if abs(f["magnitude"] - value) > rel_tol * value:
                return False
        return self.direction is None or f["direction"] == self.direction

    def _nearest(self, candidates: list[dict]) -> dict:
        """The candidate closest to this spec's expected magnitude.

        Args:
            candidates: Findings already filtered to check/element.

        Returns:
            The candidate with the smallest magnitude distance (or the
            first candidate, when magnitude is not part of this spec).
        """
        if self.magnitude is None:
            return candidates[0]
        target = self.magnitude[0]

        def dist(f: dict) -> float:
            """Distance from a candidate's magnitude to the target.

            Returns:
                A large sentinel when the candidate has no magnitude,
                else the absolute difference from the target value.
            """
            return (abs(f["magnitude"] - target)
                    if f["magnitude"] is not None else float("inf"))
        return min(candidates, key=dist)

    def _describe(self, f: dict) -> str:
        """Human-readable summary of what a candidate finding actually said.

        Args:
            f: The nearest candidate finding.

        Returns:
            A phrase like "said 15px inside", falling back to the raw
            detector text when this spec constrains neither field.
        """
        parts = []
        if self.magnitude is not None and f["magnitude"] is not None:
            parts.append("%.0fpx" % f["magnitude"])
        if self.direction is not None:
            parts.append(f["direction"] or "no direction")
        return "said " + " ".join(parts) if parts else f["raw"]

    def _want(self) -> str:
        """Human-readable summary of what this spec required.

        Returns:
            A phrase like "~50px outside" built from whichever of
            magnitude/direction this spec constrains.
        """
        parts = []
        if self.magnitude is not None:
            parts.append("~%.0fpx" % self.magnitude[0])
        if self.direction is not None:
            parts.append(self.direction)
        return " ".join(parts) if parts else "a match"


class Silence:
    """An expectation that a given check never fires.

    Args:
        check: The finding `check` name that must be absent.
    """

    def __init__(self, check: str) -> None:
        """Store the check name that must never fire."""
        self.check = check

    def matches(self, findings: list[dict]) -> str | None:
        """Check that no finding of this check fired.

        A crash anywhere in the run invalidates any silence claim: a
        detector that raised before emitting anything looks identical
        to one that ran cleanly and found nothing, so "no finding of
        this check" alone cannot be trusted as coverage — a crash is
        not coverage.

        Args:
            findings: The normalized findings list from `collect_findings`.

        Returns:
            None if silent AND no detector crashed; otherwise a
            description of what broke the silence claim.
        """
        hits = [f for f in findings if f["check"] == self.check]
        if hits:
            return "expected silence on check=%r but it fired: %s" % (
                self.check, hits[0]["raw"])
        errors = [f for f in findings if f["check"] == "detector-error"]
        if errors:
            names = ", ".join(sorted({f["element"] for f in errors}))
            return ("cannot claim silence on check=%r: detector(s) %s "
                    "crashed mid-run — a crash is not coverage"
                    % (self.check, names))
        return None


@dataclass
class Neighbour:
    """The control scene an operator's mutant must leave unaffected.

    Attributes:
        build: Zero-arg factory returning the neighbour scene's elements.
        expect: The spec (usually `Silence`) the neighbour's findings
            must satisfy after the mutant's operator is applied near it.
    """

    build: Callable[[], list[dict]]
    expect: FindingSpec | Silence


class EngineError(RuntimeError):
    """Raised when a Mutant or its supporting pieces are misconfigured."""


def validate_scene(scene: list[dict]) -> None:
    """Reject a scene whose arrows have degenerate geometry or bindings.

    Every non-deleted arrow must have at least two points, no two
    consecutive points closer than 1e-6, and a final segment at least
    1e-6 long; every element's `startBinding`/`endBinding` (if set)
    must name an id present in the scene.

    Args:
        scene: The candidate scene to validate.

    Raises:
        EngineError: Naming the element and what is degenerate about
            it — missing points, coincident points, a zero-length
            final segment, or a binding to a missing element.
    """
    ids = {e["id"] for e in scene}
    for e in scene:
        if e.get("type") != "arrow" or e.get("isDeleted"):
            continue
        points = e.get("points") or []
        if len(points) < 2:
            raise EngineError("%s: fewer than two points" % e["id"])
        for i in range(len(points) - 1):
            (x1, y1), (x2, y2) = points[i], points[i + 1]
            if math.hypot(x2 - x1, y2 - y1) < 1e-6:
                raise EngineError("%s: points %d and %d coincide"
                                  % (e["id"], i, i + 1))
        (x1, y1), (x2, y2) = points[-2], points[-1]
        if math.hypot(x2 - x1, y2 - y1) < 1e-6:
            raise EngineError("%s: final segment is degenerate" % e["id"])
    for e in scene:
        for key in ("startBinding", "endBinding"):
            binding = e.get(key)
            if binding and binding.get("elementId") not in ids:
                raise EngineError(
                    "%s: %s points at missing element %r"
                    % (e.get("id"), key, binding.get("elementId")))


def _operator(fn: Callable[..., list[dict]]) -> Callable[..., list[dict]]:
    """Deep-copy in, validate out — no operator escapes either.

    Args:
        fn: An operator body taking a mutable scene copy plus keyword
            args and returning the mutated scene.

    Returns:
        A wrapped operator with the same name and docstring as `fn`.
    """
    def wrapped(scene: list[dict], **args: Any) -> list[dict]:
        """Deep-copy `scene`, run the operator, then validate the result.

        Args:
            scene: The input scene; never mutated in place.
            **args: Operator-specific keyword arguments.

        Returns:
            The mutated, validated scene.
        """
        out = fn(copy.deepcopy(scene), **args)
        validate_scene(out)
        return out
    wrapped.__name__ = fn.__name__
    wrapped.__doc__ = fn.__doc__
    return wrapped


@_operator
def delete_arrowhead(scene: list[dict], arrow_id: str) -> list[dict]:
    """Strip an arrow's end arrowhead.

    Args:
        scene: The scene copy to mutate.
        arrow_id: The arrow's element id.

    Returns:
        The mutated scene.
    """
    arr = next(e for e in scene if e["id"] == arrow_id)
    arr["endArrowhead"] = None
    return scene


@_operator
def shift_label(scene: list[dict], text_id: str, dx: float,
                dy: float) -> list[dict]:
    """Nudge a text element's position by (dx, dy).

    Args:
        scene: The scene copy to mutate.
        text_id: The text element's id.
        dx: Offset added to the element's `x`.
        dy: Offset added to the element's `y`.

    Returns:
        The mutated scene.
    """
    txt = next(e for e in scene if e["id"] == text_id)
    txt["x"] += dx
    txt["y"] += dy
    return scene


@_operator
def move_endpoint_to(scene: list[dict], arrow_id: str, end: str, x: float,
                     y: float) -> list[dict]:
    """Move one absolute endpoint of an arrow, leaving the other in place.

    Args:
        scene: The scene copy to mutate.
        arrow_id: The arrow's element id.
        end: Which endpoint to move — `"start"` or `"end"`.
        x: The endpoint's new absolute x coordinate.
        y: The endpoint's new absolute y coordinate.

    Returns:
        The mutated scene.

    Raises:
        EngineError: If `end` is neither `"start"` nor `"end"`.
    """
    arr = next(e for e in scene if e["id"] == arrow_id)
    if end == "end":
        arr["points"][-1] = [x - arr["x"], y - arr["y"]]
    elif end == "start":
        # The start point IS the element origin by convention, so moving
        # it means moving the origin itself. The other points' OLD
        # absolute positions (computed under the OLD origin, before it
        # moves) are what must be preserved — rebase only those against
        # the NEW origin; points[0] simply becomes [0, 0].
        old_x, old_y = arr["x"], arr["y"]
        rest_abs = [(old_x + px, old_y + py) for px, py in
                   arr["points"][1:]]
        arr["x"], arr["y"] = x, y
        arr["points"] = [[0, 0]] + [[ax - x, ay - y] for ax, ay in
                                    rest_abs]
    else:
        raise EngineError("move_endpoint_to: unknown end %r" % end)
    return scene


@_operator
def decurve(scene: list[dict], arrow_id: str) -> list[dict]:
    """Clear an arrow's roundness, straightening its segments.

    Args:
        scene: The scene copy to mutate.
        arrow_id: The arrow's element id.

    Returns:
        The mutated scene.
    """
    arr = next(e for e in scene if e["id"] == arrow_id)
    arr["roundness"] = None
    return scene


@_operator
def encurve(scene: list[dict], arrow_id: str) -> list[dict]:
    """Give an arrow type-2 (curved) roundness.

    Args:
        scene: The scene copy to mutate.
        arrow_id: The arrow's element id.

    Returns:
        The mutated scene.
    """
    arr = next(e for e in scene if e["id"] == arrow_id)
    arr["roundness"] = {"type": 2}
    return scene


@_operator
def merge_corridors(scene: list[dict], a_id: str, b_id: str) -> list[dict]:
    """Slide arrow `b` onto arrow `a`'s horizontal corridor.

    Args:
        scene: The scene copy to mutate.
        a_id: The corridor-defining arrow's id.
        b_id: The arrow to merge onto it.

    Returns:
        The mutated scene.
    """
    a = next(e for e in scene if e["id"] == a_id)
    b = next(e for e in scene if e["id"] == b_id)
    b["y"] = a["y"]
    return scene


@_operator
def drop_edge(scene: list[dict], arrow_id: str) -> list[dict]:
    """Delete an arrow and every reference to it in `boundElements`.

    Args:
        scene: The scene copy to mutate.
        arrow_id: The arrow's element id.

    Returns:
        The scene with the arrow, and dangling references to it,
        removed.
    """
    kept = [e for e in scene if e["id"] != arrow_id]
    for e in kept:
        if e.get("boundElements"):
            e["boundElements"] = [b for b in e["boundElements"]
                                  if b.get("id") != arrow_id]
    return kept


@_operator
def flip_direction(scene: list[dict], arrow_id: str) -> list[dict]:
    """Reverse an arrow's direction, swapping bindings and rebasing points.

    Args:
        scene: The scene copy to mutate.
        arrow_id: The arrow's element id.

    Returns:
        The mutated scene.
    """
    arr = next(e for e in scene if e["id"] == arrow_id)
    arr["startBinding"], arr["endBinding"] = (arr["endBinding"],
                                              arr["startBinding"])
    last = arr["points"][-1]
    new_points = [[px - last[0], py - last[1]]
                 for px, py in reversed(arr["points"])]
    arr["x"] += last[0]
    arr["y"] += last[1]
    arr["points"] = new_points
    return scene


@_operator
def rename_node(scene: list[dict], node_id: str, text: str) -> list[dict]:
    """Rewrite a node's bound label text.

    Args:
        scene: The scene copy to mutate.
        node_id: The node element's id.
        text: The new label text.

    Returns:
        The mutated scene.

    Raises:
        EngineError: If the node has no bound text label.
    """
    node = next(e for e in scene if e["id"] == node_id)
    label_id = next((b["id"] for b in (node.get("boundElements") or [])
                     if b.get("type") == "text"), None)
    if label_id is None:
        raise EngineError("rename_node: %r has no bound label" % node_id)
    label = next(e for e in scene if e["id"] == label_id)
    label["text"] = text
    label["originalText"] = text
    return scene


@_operator
def move_node_onto_rank(scene: list[dict], node_id: str,
                        y: float) -> list[dict]:
    """Move a node onto a rank line and straighten its 2-point arrows.

    This manufactures a shared rank line — it is not a faithful
    re-route. Every 2-point arrow bound to the node (at either end)
    gets BOTH its endpoints' y-coordinates rewritten to the node's new
    centerline, so the far endpoint deliberately follows the moved
    node's centerline even when the far node's own centerline differs.
    That blanket rewrite is what lets a discovery sweep manufacture a
    deterministic straight-through rank line.

    Args:
        scene: The scene copy to mutate.
        node_id: The node element's id.
        y: The node's new `y` position.

    Returns:
        The mutated scene, with every 2-point arrow bound to the node
        rewritten so both its endpoint y-coordinates sit on the
        node's new centerline.
    """
    node = next(e for e in scene if e["id"] == node_id)
    node["y"] = y
    centerline = y + node.get("height", 0) / 2
    for e in scene:
        if e.get("type") != "arrow" or len(e.get("points") or []) != 2:
            continue
        bound = any((e.get(k) or {}).get("elementId") == node_id
                   for k in ("startBinding", "endBinding"))
        if not bound:
            continue
        rel_y = centerline - e["y"]
        for p in e["points"]:
            p[1] = rel_y
    return scene


@_operator
def swap_endpoints(scene: list[dict], a_id: str, b_id: str) -> list[dict]:
    """Exchange two arrows' end bindings and absolute end points.

    Args:
        scene: The scene copy to mutate.
        a_id: The first arrow's id.
        b_id: The second arrow's id.

    Returns:
        The mutated scene.
    """
    a = next(e for e in scene if e["id"] == a_id)
    b = next(e for e in scene if e["id"] == b_id)
    a["endBinding"], b["endBinding"] = b["endBinding"], a["endBinding"]
    a_end = (a["x"] + a["points"][-1][0], a["y"] + a["points"][-1][1])
    b_end = (b["x"] + b["points"][-1][0], b["y"] + b["points"][-1][1])
    a["points"][-1] = [b_end[0] - a["x"], b_end[1] - a["y"]]
    b["points"][-1] = [a_end[0] - b["x"], a_end[1] - b["y"]]
    return scene


@_operator
def unchanged(scene: list[dict]) -> list[dict]:
    """Return the scene unmodified.

    Exists for mutants whose base scene is itself the defect trigger
    — no operator needs to touch it for a finding to be expected.

    Args:
        scene: The scene copy to return as-is.

    Returns:
        The same (already-copied) scene, unmodified.
    """
    return scene


# Registered mutation operators.
OPERATORS: dict = {
    "delete_arrowhead": delete_arrowhead,
    "shift_label": shift_label,
    "move_endpoint_to": move_endpoint_to,
    "decurve": decurve,
    "encurve": encurve,
    "merge_corridors": merge_corridors,
    "drop_edge": drop_edge,
    "flip_direction": flip_direction,
    "rename_node": rename_node,
    "move_node_onto_rank": move_node_onto_rank,
    "swap_endpoints": swap_endpoints,
    "unchanged": unchanged,
}


class Mutant:
    """One mutation-catalogue entry: a broken scene paired with a control.

    Args:
        mid: Stable identifier for this mutant.
        build: Zero-arg factory returning the mutant scene's elements.
        op: The mutation operator's name.
        args: Arguments passed to the operator.
        expect: The spec the mutant scene's own findings must satisfy.
        neighbour: The paired `Neighbour` control; required, since an
            operator that isn't proven innocent elsewhere isn't proven
            at all.

    Raises:
        EngineError: If `neighbour` or `expect` is None, or if `op` is
            not a name registered in `OPERATORS`.
    """

    def __init__(self, mid: str, build: Callable[[], list[dict]], op: str,
                args: dict, expect: FindingSpec | Silence,
                neighbour: Neighbour | None) -> None:
        """Validate and store one mutation-catalogue entry.

        Raises:
            EngineError: If `neighbour` or `expect` is None, or `op` is
                not a registered operator.
        """
        if neighbour is None:
            raise EngineError("mutant %r has no neighbour" % mid)
        if expect is None:
            raise EngineError("mutant %r has no expectation" % mid)
        if op not in OPERATORS:
            raise EngineError("mutant %r uses unregistered op %r"
                              % (mid, op))
        self.mid = mid
        self.build = build
        self.op = op
        self.args = args
        self.expect = expect
        self.neighbour = neighbour


class TestEngineRules(unittest.TestCase):
    """The loader-enforcement and normalization rules from the spec."""

    def test_mutant_without_neighbour_fails_to_load(self) -> None:
        """A Mutant built with neighbour=None refuses to load."""
        with self.assertRaises(EngineError):
            Mutant("m", build=lambda: [], op="decurve",
                   args={"arrow_id": "a"}, expect=Silence("false_bidi"),
                   neighbour=None)

    def test_mutant_with_unregistered_op_fails_to_load(self) -> None:
        """A Mutant naming an op absent from OPERATORS refuses to load."""
        with self.assertRaises(EngineError):
            Mutant("m", build=lambda: [], op="not_a_real_op", args={},
                   expect=Silence("false_bidi"),
                   neighbour=Neighbour(build=lambda: [],
                                       expect=Silence("false_bidi")))

    def test_crashing_detector_reports_detector_error(self) -> None:
        """A detector that raises yields a detector-error, not a crash."""
        bad = dict(DETECTORS)
        bad["boom"] = {"collect": lambda els: 1 / 0}
        finds = collect_findings([], registry=bad)
        self.assertIn("detector-error",
                      {f["check"] for f in finds})
        errf = next(f for f in finds if f["check"] == "detector-error")
        self.assertEqual(errf["element"], "boom")

    def test_findingspec_rejects_wrong_direction(self) -> None:
        """A finding with the right magnitude but wrong direction fails."""
        spec = FindingSpec("endpoint_gap", element="a1",
                           magnitude=(50, 0.4), direction="outside")
        finds = [{"check": "endpoint_gap", "element": "a1",
                  "magnitude": 15.0, "direction": "inside", "raw": "x"}]
        self.assertIsNotNone(spec.matches(finds))   # wrong dir AND mag

    def test_silence_matches_only_absence(self) -> None:
        """Silence passes on an empty findings list, fails when it fires."""
        s = Silence("endpoint_gap")
        self.assertIsNone(s.matches([]))
        self.assertIsNotNone(s.matches(
            [{"check": "endpoint_gap", "element": "a1", "magnitude": 1.0,
              "direction": "inside", "raw": "x"}]))

    def test_silence_does_not_match_over_a_crashed_detector(self) -> None:
        """A crashed detector must not read as silence for its own check.

        `Silence("float_diamond")` on a run where the `float_diamond`
        collector raised (and so never got the chance to emit a
        finding) must NOT match — that would let a crash masquerade
        as "ran clean and found nothing".
        """
        bad = dict(DETECTORS)
        bad["float_diamond"] = {"collect": lambda els: 1 / 0}
        finds = collect_findings([], registry=bad)
        mismatch = Silence("float_diamond").matches(finds)
        self.assertIsNotNone(mismatch)
        self.assertIn("float_diamond", mismatch)


class TestOperators(unittest.TestCase):
    """Every operator does what its name says and emits a valid scene."""

    def _chain(self) -> list[dict]:
        """A -> N -> Z, N offset below the A/Z rank line.

        Returns:
            The five-element scene: nodes A, N, Z and arrows e1, e2.
        """
        a = el(id="A", type="rectangle", x=0, y=100, width=80, height=40,
               customData={"role": "node"})
        n = el(id="N", type="rectangle", x=200, y=180, width=80, height=40,
               customData={"role": "node"})
        z = el(id="Z", type="rectangle", x=400, y=100, width=80, height=40,
               customData={"role": "node"})
        e1 = el(id="e1", type="arrow", x=80, y=120, width=120, height=80,
                points=[[0, 0], [120, 80]],
                startBinding={"elementId": "A", "focus": 0, "gap": 1},
                endBinding={"elementId": "N", "focus": 0, "gap": 1},
                customData={"role": "edge"})
        e2 = el(id="e2", type="arrow", x=280, y=200, width=120, height=-80,
                points=[[0, 0], [120, -80]],
                startBinding={"elementId": "N", "focus": 0, "gap": 1},
                endBinding={"elementId": "Z", "focus": 0, "gap": 1},
                customData={"role": "edge"})
        return [a, n, z, e1, e2]

    def test_delete_arrowhead_clears_the_end_arrowhead(self) -> None:
        """delete_arrowhead nulls out endArrowhead on the named arrow."""
        out = OPERATORS["delete_arrowhead"](
            self._chain(), arrow_id="e1")
        arr = next(e for e in out if e["id"] == "e1")
        self.assertIsNone(arr["endArrowhead"])

    def test_shift_label_moves_the_text_element(self) -> None:
        """shift_label adds (dx, dy) to the text element's x/y."""
        label = el(id="lbl", type="text", x=10, y=20, width=40, height=20,
                  text="A", originalText="A", containerId="A")
        node = el(id="A", type="rectangle", x=0, y=0, width=80, height=40,
                 boundElements=[{"id": "lbl", "type": "text"}])
        out = OPERATORS["shift_label"](
            [node, label], text_id="lbl", dx=5, dy=-3)
        lbl = next(e for e in out if e["id"] == "lbl")
        self.assertEqual((lbl["x"], lbl["y"]), (15, 17))

    def test_move_endpoint_to_is_absolute_and_keeps_binding(self) -> None:
        """Moving an endpoint by absolute coords leaves its binding alone."""
        out = OPERATORS["move_endpoint_to"](
            self._chain(), arrow_id="e1", end="end", x=310, y=305)
        arr = next(e for e in out if e["id"] == "e1")
        ax, ay = arr["x"], arr["y"]
        self.assertEqual((ax + arr["points"][-1][0],
                          ay + arr["points"][-1][1]), (310, 305))
        self.assertEqual(arr["endBinding"]["elementId"], "N")

    def test_move_endpoint_to_start_shifts_origin_not_far_endpoint(
            self) -> None:
        """Moving "start" relocates the origin; the far end doesn't move.

        e1's start is bound to A, end to N. Moving the start to (999,
        999) must land the new absolute start there, leave points[0]
        at [0, 0] (the origin IS the start), leave the far endpoint's
        absolute position at its old (200, 200), and leave the
        (unrelated) startBinding untouched.
        """
        out = OPERATORS["move_endpoint_to"](
            self._chain(), arrow_id="e1", end="start", x=999, y=999)
        arr = next(e for e in out if e["id"] == "e1")
        self.assertEqual((arr["x"], arr["y"]), (999, 999))
        self.assertEqual(arr["points"][0], [0, 0])
        far_x = arr["x"] + arr["points"][-1][0]
        far_y = arr["y"] + arr["points"][-1][1]
        self.assertEqual((far_x, far_y), (200, 200))
        self.assertEqual(arr["startBinding"]["elementId"], "A")

    def test_move_endpoint_to_rejects_unknown_end(self) -> None:
        """An `end` value that is neither "start" nor "end" is refused."""
        with self.assertRaises(EngineError):
            OPERATORS["move_endpoint_to"](
                self._chain(), arrow_id="e1", end="middle", x=0, y=0)

    def test_decurve_clears_roundness(self) -> None:
        """Decurve sets roundness to None."""
        arr = el(id="e1", type="arrow", x=0, y=0, width=100, height=50,
                 points=[[0, 0], [100, 0], [100, 50]],
                 roundness={"type": 2})
        out = OPERATORS["decurve"]([arr], arrow_id="e1")
        self.assertIsNone(
            next(e for e in out if e["id"] == "e1")["roundness"])

    def test_encurve_sets_roundness_type_2(self) -> None:
        """Encurve sets roundness to {"type": 2}."""
        arr = el(id="e1", type="arrow", x=0, y=0, width=100, height=50,
                 points=[[0, 0], [100, 0], [100, 50]])
        out = OPERATORS["encurve"]([arr], arrow_id="e1")
        self.assertEqual(
            next(e for e in out if e["id"] == "e1")["roundness"],
            {"type": 2})

    def test_merge_corridors_aligns_b_onto_a(self) -> None:
        """merge_corridors sets b's y to a's y."""
        a = el(id="a", type="arrow", x=0, y=100, width=100, height=0,
              points=[[0, 0], [100, 0]])
        b = el(id="b", type="arrow", x=0, y=150, width=100, height=0,
              points=[[0, 0], [100, 0]])
        out = OPERATORS["merge_corridors"](
            [a, b], a_id="a", b_id="b")
        self.assertEqual(next(e for e in out if e["id"] == "b")["y"], 100)

    def test_drop_edge_removes_arrow_and_bound_reference(self) -> None:
        """drop_edge removes the arrow and its id from boundElements."""
        node = el(id="A", type="rectangle", x=0, y=0, width=80, height=40,
                 boundElements=[{"id": "e1", "type": "arrow"}])
        arr = el(id="e1", type="arrow", x=80, y=20, width=40, height=0,
                points=[[0, 0], [40, 0]],
                startBinding={"elementId": "A", "focus": 0, "gap": 0})
        out = OPERATORS["drop_edge"]([node, arr], arrow_id="e1")
        self.assertNotIn("e1", [e["id"] for e in out])
        self.assertEqual(
            next(e for e in out if e["id"] == "A")["boundElements"], [])

    def test_flip_direction_swaps_bindings_and_reverses_points(self) -> None:
        """flip_direction swaps bindings and reverses point order."""
        out = OPERATORS["flip_direction"](self._chain(), arrow_id="e1")
        arr = next(e for e in out if e["id"] == "e1")
        self.assertEqual(arr["startBinding"]["elementId"], "N")
        self.assertEqual(arr["endBinding"]["elementId"], "A")
        # absolute endpoint set is preserved, order reversed
        pts = [(arr["x"] + p[0], arr["y"] + p[1]) for p in arr["points"]]
        self.assertEqual(pts[0], (200, 200))
        self.assertEqual(pts[-1], (80, 120))

    def test_rename_node_rewrites_label_text(self) -> None:
        """rename_node rewrites text/originalText on the bound label."""
        label = el(id="lbl", type="text", x=10, y=10, width=40, height=20,
                  text="old", originalText="old", containerId="A")
        node = el(id="A", type="rectangle", x=0, y=0, width=80, height=40,
                 boundElements=[{"id": "lbl", "type": "text"}])
        out = OPERATORS["rename_node"](
            [node, label], node_id="A", text="new")
        lbl = next(e for e in out if e["id"] == "lbl")
        self.assertEqual((lbl["text"], lbl["originalText"]), ("new", "new"))

    def test_move_node_onto_rank_straightens_its_arrows(self) -> None:
        """move_node_onto_rank re-levels its bound 2-point arrows."""
        out = OPERATORS["move_node_onto_rank"](
            self._chain(), node_id="N", y=100)
        n = next(e for e in out if e["id"] == "N")
        self.assertEqual(n["y"], 100)
        for aid in ("e1", "e2"):
            arr = next(e for e in out if e["id"] == aid)
            ys = {arr["y"] + p[1] for p in arr["points"]}
            self.assertEqual(len(ys), 1)   # horizontal now

    def test_move_node_onto_rank_follows_movers_centerline_not_fars(
            self) -> None:
        """The rewrite is blanket, not bound-end-only.

        A, N, Z have deliberately mismatched heights (40, 80, 20), so
        their own centerlines differ. After moving N onto y=100, both
        e1 and e2 must land horizontal at N's new centerline
        (100 + 80/2 = 140) — NOT at A's centerline (120) or Z's
        centerline (110). A bound-end-only implementation would leave
        the far endpoints at their own node's centerline instead.
        """
        a = el(id="A", type="rectangle", x=0, y=100, width=80, height=40,
               customData={"role": "node"})
        n = el(id="N", type="rectangle", x=200, y=180, width=80, height=80,
               customData={"role": "node"})
        z = el(id="Z", type="rectangle", x=400, y=100, width=80, height=20,
               customData={"role": "node"})
        e1 = el(id="e1", type="arrow", x=80, y=120, width=120, height=80,
                points=[[0, 0], [120, 80]],
                startBinding={"elementId": "A", "focus": 0, "gap": 1},
                endBinding={"elementId": "N", "focus": 0, "gap": 1},
                customData={"role": "edge"})
        e2 = el(id="e2", type="arrow", x=280, y=200, width=120, height=-80,
                points=[[0, 0], [120, -80]],
                startBinding={"elementId": "N", "focus": 0, "gap": 1},
                endBinding={"elementId": "Z", "focus": 0, "gap": 1},
                customData={"role": "edge"})
        out = OPERATORS["move_node_onto_rank"](
            [a, n, z, e1, e2], node_id="N", y=100)
        centerline = 100 + 80 / 2   # N's new centerline, not A's or Z's
        for aid in ("e1", "e2"):
            arr = next(e for e in out if e["id"] == aid)
            ys = {arr["y"] + p[1] for p in arr["points"]}
            self.assertEqual(ys, {centerline})

    def test_swap_endpoints_exchanges_end_bindings_and_points(self) -> None:
        """swap_endpoints exchanges endBinding and the end points."""
        a = el(id="a", type="arrow", x=0, y=0, width=100, height=0,
              points=[[0, 0], [100, 0]],
              endBinding={"elementId": "X", "focus": 0, "gap": 0})
        b = el(id="b", type="arrow", x=0, y=200, width=50, height=0,
              points=[[0, 0], [50, 0]],
              endBinding={"elementId": "Y", "focus": 0, "gap": 0})
        x = el(id="X", type="rectangle", x=100, y=-20, width=40, height=40)
        y = el(id="Y", type="rectangle", x=50, y=180, width=40, height=40)
        out = OPERATORS["swap_endpoints"]([a, b, x, y], a_id="a", b_id="b")
        arr_a = next(e for e in out if e["id"] == "a")
        arr_b = next(e for e in out if e["id"] == "b")
        self.assertEqual(arr_a["endBinding"]["elementId"], "Y")
        self.assertEqual(arr_b["endBinding"]["elementId"], "X")
        self.assertEqual(
            (arr_a["x"] + arr_a["points"][-1][0],
             arr_a["y"] + arr_a["points"][-1][1]), (50, 200))
        self.assertEqual(
            (arr_b["x"] + arr_b["points"][-1][0],
             arr_b["y"] + arr_b["points"][-1][1]), (100, 0))

    def test_unchanged_returns_equal_but_distinct_scene(self) -> None:
        """Unchanged returns an equal scene that is a distinct object."""
        chain = self._chain()
        out = OPERATORS["unchanged"](chain)
        self.assertEqual(out, chain)
        self.assertIsNot(out, chain)

    def test_operator_output_is_validated(self) -> None:
        """A degenerate final segment raises instead of returning."""
        # An operator producing a zero-length final segment must raise,
        # not return: drive move_endpoint_to onto its own penultimate pt.
        chain = self._chain()
        arr = next(e for e in chain if e["id"] == "e1")
        px = arr["x"] + arr["points"][0][0]
        py = arr["y"] + arr["points"][0][1]
        with self.assertRaises(EngineError):
            OPERATORS["move_endpoint_to"](
                chain, arrow_id="e1", end="end", x=px, y=py)

    def test_mutation_does_not_alias_input(self) -> None:
        """An operator's output never aliases the caller's input scene."""
        chain = self._chain()
        OPERATORS["drop_edge"](chain, arrow_id="e1")
        self.assertIn("e1", [e["id"] for e in chain])


class TestDetectorsAgainstRealLint(unittest.TestCase):
    """Regexes parse canvas.py's actual lint prose, not synthetic strings."""

    def test_endpoint_gap_parses_from_real_lint_output(self) -> None:
        """A floating endpoint's real lint error yields an endpoint_gap hit."""
        node1 = el(id="n1", type="rectangle", x=0, y=0, width=100,
                   height=100)
        node2 = el(id="n2", type="rectangle", x=300, y=0, width=100,
                   height=100)
        # 2-point, unmarked customData -> server_owns_geometry is True,
        # so this lands in `errors`, not a "user-shaped" warning.
        arrow = el(id="a1", type="arrow", x=100, y=50, width=150, height=0,
                  points=[[0, 0], [150, 0]],
                  startBinding={"elementId": "n1", "focus": 0, "gap": 0},
                  endBinding={"elementId": "n2", "focus": 0, "gap": 0})
        finds = collect_findings([node1, node2, arrow])
        hits = [f for f in finds if f["check"] == "endpoint_gap"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["element"], "a1")
        self.assertEqual(hits[0]["direction"], "outside")
        self.assertAlmostEqual(hits[0]["magnitude"], 50.0, delta=1.0)

    def test_crosses_through_bound_parses_from_real_lint_output(self) -> None:
        """An endpoint near the border after a long interior approach.

        Reads as crossing through, not merely gapped.
        """
        node2 = el(id="n2", type="rectangle", x=300, y=0, width=200,
                   height=200)
        # Enters the box's right edge (x=499) and runs ~189px inside
        # before stopping just 10px past the LEFT edge — near the
        # border (inside <= TOL), but only after a long interior run.
        arrow = el(id="a1", type="arrow", x=0, y=0, width=600, height=0,
                  points=[[600, 100], [310, 100]],
                  endBinding={"elementId": "n2", "focus": 0, "gap": 0})
        finds = collect_findings([node2, arrow])
        hits = [f for f in finds if f["check"] == "crosses_through_bound"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["element"], "a1")
        self.assertIsNone(hits[0]["direction"])
        self.assertGreater(hits[0]["magnitude"], 100)

    def test_passes_through_foreign_parses_from_real_lint_output(self) -> None:
        """An arrow routed through an uninvolved node parses correctly."""
        node1 = el(id="n1", type="rectangle", x=0, y=0, width=100,
                   height=100)
        node2 = el(id="n2", type="rectangle", x=400, y=0, width=100,
                   height=100)
        bystander = el(id="n3", type="rectangle", x=200, y=0, width=100,
                       height=100)
        arrow = el(id="a1", type="arrow", x=50, y=50, width=400, height=0,
                  points=[[0, 0], [400, 0]],
                  startBinding={"elementId": "n1", "focus": 0, "gap": 0},
                  endBinding={"elementId": "n2", "focus": 0, "gap": 0})
        finds = collect_findings([node1, node2, bystander, arrow])
        hits = [f for f in finds if f["check"] == "passes_through_foreign"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["element"], "a1")
        self.assertIsNone(hits[0]["magnitude"])
        self.assertIsNone(hits[0]["direction"])


# ---------------------------------------------------------------------------
# Detector-coverage gate: every detector is proven by a firing mutant or
# named in UNCOVERED with a reason — the table can never rot quietly.
# CATALOGUE is empty until Task 5 populates it with Mutant instances;
# render-tier detectors get their proving mutants from an env-gated file
# Task 8 adds, and are reported as "render-tier", never as UNCOVERED.
# ---------------------------------------------------------------------------

CATALOGUE: dict = {}   # Task 5 populates this.

RENDER_TIER = {"ablation_existence", "ablation_continuity"}

UNCOVERED: dict[str, str] = {
    # DETECTORS placeholders: CATALOGUE is empty until Task 5, so every
    # current detector is unproven for now — Task 5 drains these rows as
    # it gives each detector its first proving mutant.
    "endpoint_gap": "no proving mutant yet; Task 5 drains this",
    "crosses_through_bound": "no proving mutant yet; Task 5 drains this",
    "passes_through_foreign": "no proving mutant yet; Task 5 drains this",
    "crossings_count": "no proving mutant yet; Task 5 drains this",
    "shared_corridor": "no proving mutant yet; Task 5 drains this",
    "false_bidi": "no proving mutant yet; Task 5 drains this",
    "float_diamond": "no proving mutant yet; Task 5 drains this",

    # lint_layout message templates with no DETECTORS entry (enumerated
    # 2026-08-12 by grepping errors.append/warnings.append/notes.append
    # over canvas.py:4868-5800 — lint_layout's body. The three templates
    # DETECTORS already covers via lint_re — endpoint_gap,
    # crosses_through_bound, passes_through_foreign — are excluded here;
    # project_lint (canvas.py:6361-6403) delegates to lint_layout,
    # lint_glossary and lint_registry and has no direct appends of its
    # own, so it contributes no rows).
    "budget_override_note":
        "enumerated 2026-08-12; no proving mutant yet — canvas.py:4900",
    "decoration_overhang":
        "enumerated 2026-08-12; no proving mutant yet — canvas.py:4956",
    "half_unbound_endpoint":
        "enumerated 2026-08-12; no proving mutant yet — canvas.py:4982",
    "unbound_arrow":
        "enumerated 2026-08-12; no proving mutant yet — canvas.py:4993",
    "dangling_binding":
        "enumerated 2026-08-12; no proving mutant yet — canvas.py:5016",
    "source_to_sink":
        "enumerated 2026-08-12; no proving mutant yet — canvas.py:5130",
    "flow_black_hole":
        "enumerated 2026-08-12; no proving mutant yet — canvas.py:5140",
    "flow_miracle":
        "enumerated 2026-08-12; no proving mutant yet — canvas.py:5146",
    "message_travels_up":
        "enumerated 2026-08-12; no proving mutant yet — canvas.py:5158",
    "message_budget":
        "enumerated 2026-08-12; no proving mutant yet — canvas.py:5163",
    "duplicate_screen_title":
        "enumerated 2026-08-12; no proving mutant yet — canvas.py:5188",
    "variant_label_mismatch":
        "enumerated 2026-08-12; no proving mutant yet — canvas.py:5231",
    "submit_precedes_inputs":
        "enumerated 2026-08-12; no proving mutant yet — canvas.py:5250",
    "input_missing_label":
        "enumerated 2026-08-12; no proving mutant yet — canvas.py:5261",
    "input_asterisk_required":
        "enumerated 2026-08-12; no proving mutant yet — canvas.py:5266",
    "uniform_input_widths":
        "enumerated 2026-08-12; no proving mutant yet — canvas.py:5275",
    "sticky_bar_over_inputs":
        "enumerated 2026-08-12; no proving mutant yet — canvas.py:5286",
    "help_presence_missing":
        "enumerated 2026-08-12; no proving mutant yet — canvas.py:5300",
    "help_slot_drift":
        "enumerated 2026-08-12; no proving mutant yet — canvas.py:5318",
    "target_size_too_close":
        "enumerated 2026-08-12; no proving mutant yet — canvas.py:5366",
    "progress_indicator_present":
        "enumerated 2026-08-12; no proving mutant yet — canvas.py:5413",
    "label_wider_than_run":
        "enumerated 2026-08-12; no proving mutant yet — canvas.py:5436",
    "arrow_points_both_ways":
        "enumerated 2026-08-12; no proving mutant yet — canvas.py:5442",
    "diagonal_arrow":
        "enumerated 2026-08-12; no proving mutant yet — canvas.py:5453",
    "grown_label_overlap":
        "enumerated 2026-08-12; no proving mutant yet — canvas.py:5513",
    "shape_overlap":
        "enumerated 2026-08-12; no proving mutant yet — canvas.py:5518",
    "annotation_budget":
        "enumerated 2026-08-12; no proving mutant yet — canvas.py:5526",
    "label_label_overlap":
        "enumerated 2026-08-12; no proving mutant yet — canvas.py:5558",
    "label_on_foreign_node":
        "enumerated 2026-08-12; no proving mutant yet — canvas.py:5579",
    "annotation_overlaps_node":
        "enumerated 2026-08-12; no proving mutant yet — canvas.py:5592",
    "text_overflow":
        "enumerated 2026-08-12; no proving mutant yet — canvas.py:5637",
    "shared_attach_point":
        "enumerated 2026-08-12; no proving mutant yet — canvas.py:5688",
    "stranded_element":
        "enumerated 2026-08-12; no proving mutant yet — canvas.py:5707",
    "offgrid_elements":
        "enumerated 2026-08-12; no proving mutant yet — canvas.py:5721",
    "opacity_not_style":
        "enumerated 2026-08-12; no proving mutant yet — canvas.py:5727",
    "unlabeled_decision_branch":
        "enumerated 2026-08-12; no proving mutant yet — canvas.py:5743",
    "unconnected_nodes":
        "enumerated 2026-08-12; no proving mutant yet — canvas.py:5756",
    "screen_node_budget":
        "enumerated 2026-08-12; no proving mutant yet — canvas.py:5765",
    "node_budget_whole_artifact":
        "enumerated 2026-08-12; no proving mutant yet — canvas.py:5770",
    "arrow_budget":
        "enumerated 2026-08-12; no proving mutant yet — canvas.py:5775",

    # ART-### repair codes: validate_scene (canvas.py:374-495) and the
    # project-load JSON guard (canvas.py:6504).
    "not_a_json_object": "enumerated 2026-08-12; no proving mutant yet — "
                         "ART-000, canvas.py:378",
    "elements_not_a_list": "enumerated 2026-08-12; no proving mutant yet — "
                           "ART-001, canvas.py:394",
    "malformed_element_dropped":
        "enumerated 2026-08-12; no proving mutant yet — "
        "ART-002, canvas.py:402",
    "duplicate_element_id_dropped":
        "enumerated 2026-08-12; no proving mutant yet — "
        "ART-003, canvas.py:406",
    "dangling_container_detached":
        "enumerated 2026-08-12; no proving mutant yet — "
        "ART-004, canvas.py:415",
    "dangling_binding_cleared":
        "enumerated 2026-08-12; no proving mutant yet — "
        "ART-005, canvas.py:421",
    "invalid_json_ignored": "enumerated 2026-08-12; no proving mutant yet — "
                            "ART-006, canvas.py:6504",
    "detached_label_recentered":
        "enumerated 2026-08-12; no proving mutant yet — "
        "ART-007, canvas.py:471",
    "label_in_text_element_merged":
        "enumerated 2026-08-12; no proving mutant yet — "
        "ART-010, canvas.py:448",
    "label_wider_than_container_refit":
        "enumerated 2026-08-12; no proving mutant yet — "
        "ART-011, canvas.py:491",
}


def coverage_table() -> list[tuple[str, str, str]]:
    """Report each detector's proof status against the mutant catalogue.

    A detector is "proven" once at least one `CATALOGUE` mutant's
    `expect` is a `FindingSpec` naming that detector's check — a mutant
    whose `expect` is a `Silence` proves only the check's quiet half, so
    a check whose only mutants are Silences is still reported UNCOVERED
    for firing. Detectors named in `RENDER_TIER` are reported
    "render-tier" instead: their proving mutants live in a separate
    env-gated catalogue (Task 8) that this function does not read, so
    they are never reported UNCOVERED.

    Returns:
        One `(detector, status, evidence)` tuple per name currently in
        `DETECTORS`, sorted by name. `status` is one of "proven",
        "render-tier", "UNCOVERED". `evidence` is the proving mutant id
        for "proven", a placeholder note for "render-tier", or the
        `UNCOVERED` reason (empty string if the detector carries none).
    """
    proven_by: dict[str, str] = {}
    for mid, mutant in CATALOGUE.items():
        expect = mutant.expect
        if isinstance(expect, FindingSpec) and expect.check not in proven_by:
            proven_by[expect.check] = mid
    rows: list[tuple[str, str, str]] = []
    for name in sorted(DETECTORS):
        if name in RENDER_TIER:
            rows.append((name, "render-tier",
                        proven_by.get(name, "pending Task 8")))
        elif name in proven_by:
            rows.append((name, "proven", proven_by[name]))
        else:
            rows.append((name, "UNCOVERED", UNCOVERED.get(name, "")))
    return rows


class TestCoverage(unittest.TestCase):
    """Spec §3: every detector is proven or named, never silently unproven."""

    def test_every_detector_is_proven_or_named(self) -> None:
        """Every DETECTORS entry is either proven or carries an UNCOVERED reason."""
        gaps = [name for name, status, _ in coverage_table()
                if status == "UNCOVERED" and name not in UNCOVERED]
        self.assertEqual(gaps, [],
                         "detectors with no firing mutant and no "
                         "UNCOVERED reason: %s" % gaps)

    def test_uncovered_entries_all_carry_reasons(self) -> None:
        """No UNCOVERED entry has a blank or whitespace-only reason."""
        empty = [k for k, v in UNCOVERED.items() if not str(v).strip()]
        self.assertEqual(empty, [])

    def test_silence_only_mutant_does_not_prove_its_check(self) -> None:
        """A check whose only catalogue mutant is a Silence stays UNCOVERED.

        `Silence` only proves a check's quiet half — a mutant that never
        expects the check to fire cannot stand in for one that does.
        """
        silence = Silence("endpoint_gap")
        neighbour = Neighbour(build=lambda: [], expect=silence)
        mutant = Mutant("synthetic-silence-only", build=lambda: [],
                        op="unchanged", args={}, expect=silence,
                        neighbour=neighbour)
        saved = dict(CATALOGUE)
        CATALOGUE.clear()
        CATALOGUE["synthetic-silence-only"] = mutant
        try:
            row = next(r for r in coverage_table() if r[0] == "endpoint_gap")
        finally:
            CATALOGUE.clear()
            CATALOGUE.update(saved)
        self.assertEqual(row[1], "UNCOVERED")

    def test_findingspec_mutant_proves_its_check(self) -> None:
        """A catalogue mutant whose expect is a FindingSpec proves it."""
        neighbour = Neighbour(build=lambda: [], expect=Silence("endpoint_gap"))
        mutant = Mutant("synthetic-proof", build=lambda: [], op="unchanged",
                        args={}, expect=FindingSpec("endpoint_gap"),
                        neighbour=neighbour)
        saved = dict(CATALOGUE)
        CATALOGUE.clear()
        CATALOGUE["synthetic-proof"] = mutant
        try:
            row = next(r for r in coverage_table() if r[0] == "endpoint_gap")
        finally:
            CATALOGUE.clear()
            CATALOGUE.update(saved)
        self.assertEqual(row[1], "proven")
        self.assertEqual(row[2], "synthetic-proof")


if __name__ == "__main__":
    if "--coverage" in sys.argv:
        for name, status, ev in coverage_table():
            print("%-28s %-12s %s" % (name, status, ev))
    else:
        unittest.main()
