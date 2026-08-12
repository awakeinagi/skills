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
import datetime
import hashlib
import json
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

    def test_delete_arrowhead_clears_the_end_arrowhead(self) -> None:
        """delete_arrowhead nulls out endArrowhead on the named arrow."""
        out = OPERATORS["delete_arrowhead"](
            _chain(), arrow_id="e1")
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
            _chain(), arrow_id="e1", end="end", x=310, y=305)
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
            _chain(), arrow_id="e1", end="start", x=999, y=999)
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
                _chain(), arrow_id="e1", end="middle", x=0, y=0)

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
        out = OPERATORS["flip_direction"](_chain(), arrow_id="e1")
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
            _chain(), node_id="N", y=100)
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
        chain = _chain()
        out = OPERATORS["unchanged"](chain)
        self.assertEqual(out, chain)
        self.assertIsNot(out, chain)

    def test_operator_output_is_validated(self) -> None:
        """A degenerate final segment raises instead of returning."""
        # An operator producing a zero-length final segment must raise,
        # not return: drive move_endpoint_to onto its own penultimate pt.
        chain = _chain()
        arr = next(e for e in chain if e["id"] == "e1")
        px = arr["x"] + arr["points"][0][0]
        py = arr["y"] + arr["points"][0][1]
        with self.assertRaises(EngineError):
            OPERATORS["move_endpoint_to"](
                chain, arrow_id="e1", end="end", x=px, y=py)

    def test_mutation_does_not_alias_input(self) -> None:
        """An operator's output never aliases the caller's input scene."""
        chain = _chain()
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
# Scene builders. Every coordinate here was measured against live canvas.py
# and instruments.py output, so the numbers are frozen: move a point and you
# move the finding the catalogue asserts. The diamond is 200x100 at
# (300,300), i.e. center (400,350) with half-axes a=100, b=50, so the
# rhombus boundary is |x-400|/100 + |y-350|/50 == 1 and the horizontal gap
# from a point to that boundary at its own y is 100*(1 - |y-350|/50) wide.
# ---------------------------------------------------------------------------


def _chain() -> list[dict]:
    """A -> N -> Z, N offset below the A/Z rank line.

    The operator tests' workbench and the discovery sweep's chain base:
    N sits 80px below A and Z, so both arrows read as diagonals until
    something moves N onto the rank line the other two share.

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


def _diamond_stage() -> list[dict]:
    """A bound rect->diamond arrow, endpoint at the top-left facet midpoint.

    (350,325) lies exactly ON the rhombus boundary (0.5 + 0.5 == 1), so
    this base drawing is visually perfect — which is what makes it a
    probe for the bbox-shaped lint's over-fire.

    Returns:
        The four-element scene: source rect, diamond, its label, arrow.
    """
    dia = el(id="d1", type="diamond", x=300, y=300, width=200, height=100,
             customData={"role": "node"})
    lbl = el(id="t1", type="text", x=340, y=340, width=120, height=20,
             text="Decide?", fontSize=16, fontFamily=1, textAlign="center",
             verticalAlign="middle", containerId="d1",
             originalText="Decide?")
    dia["boundElements"] = [{"id": "t1", "type": "text"}]
    src = el(id="s1", type="rectangle", x=60, y=300, width=80, height=50,
             customData={"role": "node"})
    arr = el(id="a1", type="arrow", x=140, y=325, width=210, height=0,
             points=[[0, 0], [210, 0]],
             startBinding={"elementId": "s1", "focus": 0, "gap": 1},
             endBinding={"elementId": "d1", "focus": 0, "gap": 1},
             customData={"role": "edge"})
    return [src, dia, lbl, arr]


def _rect_stage() -> list[dict]:
    """Three arrows fanned onto one rectangle's left edge, 20px apart.

    The endpoint mutants' control: a rectangle's bbox IS its shape, so a
    correct attachment on its edge must stay silent — and so must the
    fanned siblings at (300,305) and (300,345), the r4-1 over-fire guard.

    Returns:
        The eight-element scene: target rect, its label, and three
        source rects each with an arrow onto the shared left edge.
    """
    tgt = el(id="r1", type="rectangle", x=300, y=300, width=180, height=60,
             customData={"role": "node"})
    lbl = el(id="t1", type="text", x=310, y=320, width=160, height=20,
             text="Review", fontSize=16, fontFamily=1, textAlign="center",
             verticalAlign="middle", containerId="r1",
             originalText="Review")
    tgt["boundElements"] = [{"id": "t1", "type": "text"}]
    scene = [tgt, lbl]
    # (source y, attach y): the middle arrow runs straight in; the other
    # two elbow via x=250 onto attach points 20px above and below it.
    for i, (sy, ay) in enumerate(((300, 325), (180, 305), (420, 345))):
        sid, aid, cy = "s%d" % (i + 1), "a%d" % (i + 1), sy + 25
        scene.append(el(id=sid, type="rectangle", x=60, y=sy, width=80,
                        height=50, customData={"role": "node"}))
        dy = ay - cy
        points = ([[0, 0], [160, 0]] if dy == 0 else
                  [[0, 0], [110, 0], [110, dy], [160, dy]])
        scene.append(el(id=aid, type="arrow", x=140, y=cy, width=160,
                        height=abs(dy), points=points,
                        startBinding={"elementId": sid, "focus": 0,
                                      "gap": 1},
                        endBinding={"elementId": "r1", "focus": 0,
                                    "gap": 1},
                        customData={"role": "edge"}))
    return scene


def _foreign_corner_stage() -> list[dict]:
    """The diamond stage plus an arrow threading its empty bbox corner.

    `ax` clips the top-left corner of the diamond's (inset) bounding box
    between (302,309) and (312,302) — roughly 36px of clear white space
    from the rhombus edge at its closest, since |x-400|/100 + |y-350|/50
    stays above 1.8 along that whole run.

    Returns:
        The diamond stage plus the unbound corner-threading arrow `ax`.
    """
    scene = _diamond_stage()
    scene.append(el(id="ax", type="arrow", x=210, y=370, width=180,
                    height=120, points=[[0, 0], [180, -120]],
                    customData={"role": "edge"}))
    return scene


def _foreign_body_stage() -> list[dict]:
    """The diamond stage plus an arrow driven through the rhombus body.

    Returns:
        The diamond stage plus the unbound arrow `ax` running along
        y=350, the diamond's own centerline.
    """
    scene = _diamond_stage()
    scene.append(el(id="ax", type="arrow", x=210, y=350, width=380,
                    height=0, points=[[0, 0], [380, 0]],
                    customData={"role": "edge"}))
    return scene


def _crossing_scene() -> list[dict]:
    """A flat arrow crossed four times by one zigzag arrow.

    The zigzag's four legs each cut the flat arrow's y=100 line exactly
    once between x=150 and x=550 (verified by brute-force proper-
    intersection count over all 4x1 segment pairs).

    Returns:
        The two-arrow scene.
    """
    flat = el(id="a1", type="arrow", x=100, y=100, width=760, height=0,
              points=[[0, 0], [760, 0]], customData={"role": "edge"})
    zig = el(id="a2", type="arrow", x=150, y=40, width=400, height=120,
             points=[[0, 0], [100, 120], [200, 0], [300, 120], [400, 0]],
             customData={"role": "edge"})
    return [flat, zig]


def _single_crossing_scene() -> list[dict]:
    """The same flat arrow cut exactly once by a single diagonal.

    Returns:
        The two-arrow scene.
    """
    flat = el(id="a1", type="arrow", x=100, y=100, width=760, height=0,
              points=[[0, 0], [760, 0]], customData={"role": "edge"})
    diag = el(id="a2", type="arrow", x=300, y=40, width=100, height=120,
              points=[[0, 0], [100, 120]], customData={"role": "edge"})
    return [flat, diag]


def _opposed_pair(rounded: bool) -> list[dict]:
    """Two elbowed arrows whose 18px final segments meet head-on at x=250.

    Both final chords are vertical on x=250 and point at each other,
    heads 2px apart — the false-bidi signature. `rounded` decides
    whether those elbows render as curves (type-2 roundness), which
    bends the visible approach away from the stored chord.

    Args:
        rounded: True for type-2 roundness, False for sharp elbows.

    Returns:
        The two-arrow scene.
    """
    shape = {"type": 2} if rounded else None
    top = el(id="ea", type="arrow", x=150, y=282, width=100, height=18,
             points=[[0, 0], [100, 0], [100, 18]], roundness=shape,
             customData={"role": "edge"})
    bot = el(id="eb", type="arrow", x=150, y=320, width=100, height=18,
             points=[[0, 0], [100, 0], [100, -18]], roundness=shape,
             customData={"role": "edge"})
    return [top, bot]


def _collinear_pair() -> list[dict]:
    """Two horizontal arrows on y=200 overlapping along 300px of it.

    Returns:
        The two-arrow scene.
    """
    return [el(id="b1", type="arrow", x=100, y=200, width=400, height=0,
               points=[[0, 0], [400, 0]], customData={"role": "edge"}),
            el(id="b2", type="arrow", x=200, y=200, width=400, height=0,
               points=[[0, 0], [400, 0]], customData={"role": "edge"})]


def _parallel_pair() -> list[dict]:
    """The same two arrows held 40px apart — far outside the 16px tol.

    Returns:
        The two-arrow scene.
    """
    return [el(id="b1", type="arrow", x=100, y=200, width=400, height=0,
               points=[[0, 0], [400, 0]], customData={"role": "edge"}),
            el(id="b2", type="arrow", x=100, y=240, width=400, height=0,
               points=[[0, 0], [400, 0]], customData={"role": "edge"})]


# ---------------------------------------------------------------------------
# The day-one catalogue. Each entry pairs a scene the drawing gets WRONG
# today with a neighbour that must read right today; the mutant tests below
# are `expectedFailure` exactly where the defect is still live, so WP4's fix
# announces itself as an unexpected success rather than a silent pass.
# ---------------------------------------------------------------------------

CATALOGUE: dict[str, Mutant] = {}


def _register(m: Mutant) -> Mutant:
    """Add a mutant to the catalogue, keyed by its id.

    Args:
        m: The mutant to register.

    Returns:
        The same mutant, so callers can keep a reference.

    Raises:
        EngineError: If a mutant with this id is already registered.
    """
    if m.mid in CATALOGUE:
        raise EngineError("duplicate mutant id %r" % m.mid)
    CATALOGUE[m.mid] = m
    return m


# AXIS CONVENTION (binds WP4): endpoint_gap magnitude is the gap along the
# arrow's APPROACH AXIS (horizontal here), not the perpendicular distance to
# the facet — 80.0/50.0, not 35.8/22.4. A fix that reports perpendicular
# distance will not flip these mutants and is wrong by this spec. The bands
# below (±30%, ±40%) exclude the perpendicular readings deliberately: the
# approach axis is what the reader's eye follows down the arrow.

# Shape-blind endpoint lint: the endpoint sits 80px of white space clear of
# the rhombus but inside its bbox. Flips when WP4 clips to the shape.
_register(Mutant(
    "diamond_corner_silence",
    build=_diamond_stage,
    op="move_endpoint_to", args={"arrow_id": "a1", "end": "end",
                                 "x": 310, "y": 305},
    expect=FindingSpec("endpoint_gap", element="a1",
                       magnitude=(80, 0.30), direction="outside"),
    neighbour=Neighbour(_rect_stage, Silence("endpoint_gap"))))

# Same blindness, now inverted: 50px OUTSIDE the rhombus is reported as
# 15px "inside the shape". Flips when WP4 clips to the shape.
_register(Mutant(
    "diamond_wrong_direction",
    build=_diamond_stage,
    op="move_endpoint_to", args={"arrow_id": "a1", "end": "end",
                                 "x": 320, "y": 315},
    expect=FindingSpec("endpoint_gap", element="a1",
                       magnitude=(50, 0.40), direction="outside"),
    neighbour=Neighbour(_rect_stage, Silence("endpoint_gap"))))

# Over-fire: a perfect facet-midpoint attachment is called 25px inside the
# shape, as an error. Flips when WP4 clips to the shape.
_register(Mutant(
    "diamond_facet_overfire",
    build=_diamond_stage,
    op="unchanged", args={},
    expect=Silence("endpoint_gap"),
    neighbour=Neighbour(_rect_stage, Silence("endpoint_gap"))))

# Over-fire: the through-node test uses the node's bbox, so an arrow
# clipping the diamond's empty corner reads as passing through it. Flips
# when WP4 tests the rendered shape.
_register(Mutant(
    "foreign_diamond_corner_overfire",
    build=_foreign_corner_stage,
    op="unchanged", args={},
    expect=Silence("passes_through_foreign"),
    neighbour=Neighbour(_foreign_body_stage,
                        FindingSpec("passes_through_foreign",
                                    element="ax"))))

# Undercount: the pair truly crosses 4 times (reviewer-verified by brute
# force) but the double-break instrument reports 1. Flips when WP4 counts
# crossings instead of pairs.
_register(Mutant(
    "four_crossings_pairbug",
    build=_crossing_scene,
    op="unchanged", args={},
    expect=FindingSpec("crossings_count", magnitude=(4, 0.0)),
    neighbour=Neighbour(_single_crossing_scene,
                        FindingSpec("crossings_count",
                                    magnitude=(1, 0.0)))))

# Blind spot: an endpoint pinned to the diamond's exact center — the worst
# possible binding — scores gap 0 and is never reported. Flips when WP4
# stops returning r at t == 0.
_register(Mutant(
    "float_diamond_center_zero",
    build=_diamond_stage,
    op="move_endpoint_to", args={"arrow_id": "a1", "end": "end",
                                 "x": 400, "y": 350},
    expect=FindingSpec("float_diamond", element="a1",
                       magnitude=(50, 0.90)),
    neighbour=Neighbour(_diamond_stage, Silence("float_diamond"))))

# Spurious: false_bidi reads the stored chord, so a curved elbow whose
# rendered path bows away from it still reads as one bidirectional line.
# Flips when WP4 samples the rendered PATH over the final stretch — NOT
# the tangent at the arrowhead, which is (0,18) here, identical to the
# chord, and would never flip this. With Catmull-Rom controls c1=(266.7,
# 285) and c2=(250,294), the curve passes through (254.8,291.9): |dx|=4.8
# off the x=250 chord line, breaking false_bidi's 2px collinearity
# tolerance while the sharp neighbour, whose path IS its chord, keeps
# firing.
_register(Mutant(
    "curved_elbow_spurious_bidi",
    build=lambda: _opposed_pair(rounded=True),
    op="unchanged", args={},
    expect=Silence("false_bidi"),
    neighbour=Neighbour(lambda: _opposed_pair(rounded=False),
                        FindingSpec("false_bidi"))))

# No defect: the corridor instrument already reads collinear overlap
# correctly, so this one guards the behavior rather than indicting it.
_register(Mutant(
    "collinear_overlap_corridor",
    build=_collinear_pair,
    op="unchanged", args={},
    expect=FindingSpec("shared_corridor", magnitude=(300, 0.20)),
    neighbour=Neighbour(_parallel_pair, Silence("shared_corridor"))))


class TestMutantCatalogue(unittest.TestCase):
    """Verify mode: seeded defect -> asserted finding; neighbour -> pole."""

    def _run(self, mid: str) -> None:
        """Build, mutate, and assert one catalogue mutant's expectation.

        Args:
            mid: The mutant's catalogue id.
        """
        m = CATALOGUE[mid]
        scene = OPERATORS[m.op](m.build(), **m.args)
        mism = m.expect.matches(collect_findings(scene))
        self.assertIsNone(mism, "%s: %s" % (mid, mism))

    def _run_neighbour(self, mid: str) -> None:
        """Build one mutant's neighbour and assert its opposite pole.

        Args:
            mid: The mutant's catalogue id.
        """
        n = CATALOGUE[mid].neighbour
        mism = n.expect.matches(collect_findings(n.build()))
        self.assertIsNone(mism, "%s neighbour: %s" % (mid, mism))

    @unittest.expectedFailure
    def test_mutant_diamond_corner_silence(self) -> None:
        """80px clear of the rhombus, inside its bbox — lint says nothing."""
        # Shape-blind endpoint lint; flips when WP4 clips to the shape.
        self._run("diamond_corner_silence")

    def test_neighbour_diamond_corner_silence(self) -> None:
        """Fanned rectangle attachments stay endpoint-silent."""
        self._run_neighbour("diamond_corner_silence")

    @unittest.expectedFailure
    def test_mutant_diamond_wrong_direction(self) -> None:
        """50px outside the rhombus, reported as 15px inside the shape."""
        # Shape-blind endpoint lint; flips when WP4 clips to the shape.
        self._run("diamond_wrong_direction")

    def test_neighbour_diamond_wrong_direction(self) -> None:
        """Fanned rectangle attachments stay endpoint-silent."""
        self._run_neighbour("diamond_wrong_direction")

    @unittest.expectedFailure
    def test_mutant_diamond_facet_overfire(self) -> None:
        """A perfect facet-midpoint attachment is called 25px inside."""
        # Shape-blind endpoint lint; flips when WP4 clips to the shape.
        self._run("diamond_facet_overfire")

    def test_neighbour_diamond_facet_overfire(self) -> None:
        """Fanned rectangle attachments stay endpoint-silent."""
        self._run_neighbour("diamond_facet_overfire")

    @unittest.expectedFailure
    def test_mutant_foreign_diamond_corner_overfire(self) -> None:
        """An arrow clipping the empty bbox corner reads as passing through."""
        # Bbox-shaped through-node test; flips when WP4 tests the shape.
        self._run("foreign_diamond_corner_overfire")

    def test_neighbour_foreign_diamond_corner_overfire(self) -> None:
        """An arrow through the rhombus body really does pass through it."""
        self._run_neighbour("foreign_diamond_corner_overfire")

    @unittest.expectedFailure
    def test_mutant_four_crossings_pairbug(self) -> None:
        """Four true crossings, counted as one pair."""
        # Pair count masquerading as a crossing count; flips when WP4
        # drops the double-break.
        self._run("four_crossings_pairbug")

    def test_neighbour_four_crossings_pairbug(self) -> None:
        """A single crossing counts one either way."""
        self._run_neighbour("four_crossings_pairbug")

    @unittest.expectedFailure
    def test_mutant_float_diamond_center_zero(self) -> None:
        """An endpoint pinned dead-center scores gap 0 and never reports."""
        # t == 0 returns r (0); flips when WP4 measures to the boundary.
        self._run("float_diamond_center_zero")

    def test_neighbour_float_diamond_center_zero(self) -> None:
        """An endpoint exactly on the facet is not a floating endpoint."""
        self._run_neighbour("float_diamond_center_zero")

    @unittest.expectedFailure
    def test_mutant_curved_elbow_spurious_bidi(self) -> None:
        """A curved elbow bowed off its own chord still reads as bidi."""
        # Stored chord, not rendered path; flips when WP4 samples the
        # rendered path over the final stretch (see the catalogue entry —
        # the arrowhead tangent alone will not flip it).
        self._run("curved_elbow_spurious_bidi")

    def test_neighbour_curved_elbow_spurious_bidi(self) -> None:
        """Sharp opposed elbows genuinely read as one bidirectional line."""
        self._run_neighbour("curved_elbow_spurious_bidi")

    def test_mutant_collinear_overlap_corridor(self) -> None:
        """300px of collinear overlap reads as one shared corridor."""
        # No defect here — this guards correct behavior, not a bug.
        self._run("collinear_overlap_corridor")

    def test_neighbour_collinear_overlap_corridor(self) -> None:
        """Parallel arrows 40px apart are two strokes, not one."""
        self._run_neighbour("collinear_overlap_corridor")

    @unittest.skipUnless(
        (Path(__file__).parent / "fixtures" / "argus-r5").is_dir(),
        "argus-r5 fixture not present")
    def test_instruments_run_over_the_r5_fixture(self) -> None:
        """Every promoted artifact parses and every detector completes."""
        root = Path(__file__).parent / "fixtures" / "argus-r5" / "artifacts"
        for path in sorted(root.iterdir()):
            with self.subTest(artifact=path.name):
                data = json.loads(path.read_text())
                els = data["elements"] if isinstance(data, dict) else data
                finds = collect_findings(els)
                self.assertNotIn("detector-error",
                                 {f["check"] for f in finds})


# ---------------------------------------------------------------------------
# Detector-coverage gate: every detector is proven by a firing mutant or
# named in UNCOVERED with a reason — the table can never rot quietly.
# Render-tier detectors get their proving mutants from an env-gated file
# Task 8 adds, and are reported as "render-tier", never as UNCOVERED.
# ---------------------------------------------------------------------------

RENDER_TIER = {"ablation_existence", "ablation_continuity"}

UNCOVERED: dict[str, str] = {
    # The one DETECTORS entry the day-one catalogue leaves unproven: every
    # scene that runs long enough inside a bound node to trip it also trips
    # endpoint_gap first, so it needs a mutant of its own.
    "crosses_through_bound":
        "no proving mutant yet; candidate: a multi-elbow interior run "
        "— WP4 backlog",

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

    A detector is "proven" once at least one `CATALOGUE` entry carries a
    `FindingSpec` naming that detector's check — in the mutant's own
    `expect` OR in its neighbour's. The neighbour half matters because an
    over-fire mutant's `expect` is a `Silence` (the defect is that the
    check fires when it shouldn't); its live firing proof is exactly the
    neighbour that fires legitimately, and without counting that,
    `passes_through_foreign` and `false_bidi` would sit UNCOVERED forever
    despite being proven in every run. A check whose entries are Silences
    on both sides is still reported UNCOVERED for firing. Detectors named
    in `RENDER_TIER` are reported "render-tier" instead: their proving
    mutants live in a separate env-gated catalogue (Task 8) that this
    function does not read, so they are never reported UNCOVERED.

    Returns:
        One `(detector, status, evidence)` tuple per name currently in
        `DETECTORS`, sorted by name. `status` is one of "proven",
        "render-tier", "UNCOVERED". `evidence` is the proving mutant id
        for "proven" — the lexicographically first, so a check with
        several proofs reports the same one every run — a placeholder
        note for "render-tier", or the `UNCOVERED` reason (empty string
        if the detector carries none).
    """
    proven_by: dict[str, str] = {}
    for mid in sorted(CATALOGUE):
        mutant = CATALOGUE[mid]
        for expect in (mutant.expect, mutant.neighbour.expect):
            if isinstance(expect, FindingSpec):
                proven_by.setdefault(expect.check, mid)
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
        expects the check to fire cannot stand in for one that does, and
        neither can its neighbour when that is a `Silence` too.
        """
        silence = Silence("endpoint_gap")
        neighbour = Neighbour(build=lambda: [], expect=silence)
        mutant = Mutant("synthetic-silence-only", build=lambda: [],
                        op="unchanged", args={}, expect=silence,
                        neighbour=neighbour)
        row = self._row_for("endpoint_gap",
                            {"synthetic-silence-only": mutant})
        self.assertEqual(row[1], "UNCOVERED")

    def test_findingspec_mutant_proves_its_check(self) -> None:
        """A catalogue mutant whose expect is a FindingSpec proves it."""
        neighbour = Neighbour(build=lambda: [], expect=Silence("endpoint_gap"))
        mutant = Mutant("synthetic-proof", build=lambda: [], op="unchanged",
                        args={}, expect=FindingSpec("endpoint_gap"),
                        neighbour=neighbour)
        row = self._row_for("endpoint_gap", {"synthetic-proof": mutant})
        self.assertEqual(row[1], "proven")
        self.assertEqual(row[2], "synthetic-proof")

    def test_neighbour_findingspec_proves_its_check(self) -> None:
        """A FindingSpec in the NEIGHBOUR proves the check just as well.

        This is the over-fire shape: the mutant expects `Silence`
        because the defect is that the check fires when it shouldn't,
        and the neighbour is where the check's legitimate firing is
        proven. Without this rule every over-fire mutant would read as
        leaving its check unproven.
        """
        mutant = Mutant(
            "synthetic-overfire", build=lambda: [], op="unchanged", args={},
            expect=Silence("endpoint_gap"),
            neighbour=Neighbour(build=lambda: [],
                                expect=FindingSpec("endpoint_gap")))
        row = self._row_for("endpoint_gap", {"synthetic-overfire": mutant})
        self.assertEqual(row[1], "proven")
        self.assertEqual(row[2], "synthetic-overfire")

    def test_evidence_is_the_lexicographically_first_prover(self) -> None:
        """Several provers pick one deterministically, not by insertion order.

        Both mutants prove `endpoint_gap`; the reported evidence is the
        sorted-first id whichever way the catalogue was populated.
        """
        def prover(mid: str) -> Mutant:
            """Build a minimal mutant whose expect proves `endpoint_gap`.

            Args:
                mid: The mutant's catalogue id.

            Returns:
                The constructed mutant.
            """
            return Mutant(mid, build=lambda: [], op="unchanged", args={},
                          expect=FindingSpec("endpoint_gap"),
                          neighbour=Neighbour(build=lambda: [],
                                              expect=Silence("endpoint_gap")))
        forward = self._row_for("endpoint_gap",
                                {"aaa-first": prover("aaa-first"),
                                 "zzz-last": prover("zzz-last")})
        reverse = self._row_for("endpoint_gap",
                                {"zzz-last": prover("zzz-last"),
                                 "aaa-first": prover("aaa-first")})
        self.assertEqual(forward[2], "aaa-first")
        self.assertEqual(reverse[2], "aaa-first")

    def _row_for(self, detector: str,
                 catalogue: dict[str, Mutant]) -> tuple[str, str, str]:
        """Read one coverage row against a temporarily swapped catalogue.

        Args:
            detector: The detector name whose row to return.
            catalogue: The catalogue to install for the duration.

        Returns:
            That detector's `(name, status, evidence)` coverage row.
        """
        saved = dict(CATALOGUE)
        CATALOGUE.clear()
        CATALOGUE.update(catalogue)
        try:
            return next(r for r in coverage_table() if r[0] == detector)
        finally:
            CATALOGUE.clear()
            CATALOGUE.update(saved)


# ---------------------------------------------------------------------------
# Discovery mode. Verify mode asks whether a seeded defect produces the
# finding the catalogue names; discovery asks the inverse — sweep meaningful
# MODEL mutations (a dropped relation, a reversed one, a renamed node, a node
# dragged onto its neighbours' rank, two exchanged targets) through the
# detectors and demand a corresponding change in how the drawing reads. A
# cell whose correspondence predicate returns detail strings is a SURVIVOR: a
# real change nothing caught, i.e. a lint rule discovered on purpose. Every
# survivor is recorded in mutants_sweep.json and must carry a DISPOSITIONS
# verdict, so a gap can be new exactly once and never twice.
#
# Survivor detail strings are the survivor's identity (its id hashes them),
# so they name element ids and check names only — never a detector's prose,
# which would re-key every disposition the next time a message is reworded.
# ---------------------------------------------------------------------------

SWEEP_RECORD = Path(__file__).parent / "mutants_sweep.json"
# A swapped arrow may be re-routed, but it may not keep pointing at where its
# OLD target was: 200px is wider than any node in the sweep bases, so a final
# point still this far from its new target's center is stale geometry, not a
# routing style.
_SWAP_TOL = 200.0
_RENAME_TEXT = "Renamed"


def _named_elements(findings: list[dict]) -> set[str]:
    """Collect every element id a findings list names.

    Composite `element` values (the corridor and bidi checks report
    `"a+b"`) are split into their parts, and findings whose element is
    None contribute nothing — testing `"N" in str(None)` is True, so a
    substring match would let the node id `N` hide behind any
    scene-level finding.

    Args:
        findings: Normalized findings from `collect_findings`.

    Returns:
        The set of element ids named by at least one finding.
    """
    return {part for f in findings if f.get("element")
            for part in str(f["element"]).split("+")}


def _ends(arrow: dict) -> tuple[tuple[float, float], tuple[float, float]]:
    """Return an arrow's absolute start and end points.

    Args:
        arrow: An arrow element.

    Returns:
        The `((start_x, start_y), (end_x, end_y))` absolute pair.
    """
    pts = arrow["points"]
    return ((arrow["x"] + pts[0][0], arrow["y"] + pts[0][1]),
            (arrow["x"] + pts[-1][0], arrow["y"] + pts[-1][1]))


def _corr_drop_edge(before: list[dict], after: list[dict], fb: list[dict],
                    fa: list[dict]) -> list[str]:
    """The dropped relation must vanish from every detection surface.

    Args:
        before: The base scene.
        after: The mutated scene.
        fb: Findings over `before`.
        fa: Findings over `after`.

    Returns:
        One detail string per finding that still names a dropped arrow;
        empty when the correspondence held.
    """
    dropped = {e["id"] for e in before} - {e["id"] for e in after}
    return sorted({"%s finding still names dropped arrow %s" % (f["check"], d)
                   for d in dropped for f in fa
                   if d in _named_elements([f])})


def _corr_flip(before: list[dict], after: list[dict], fb: list[dict],
               fa: list[dict]) -> list[str]:
    """Reversal must be visible: a swapped arrow is drawn reversed.

    The model-side change is the binding swap; the drawing has to answer
    it by exchanging that arrow's endpoints, or the reader sees the old
    direction over the new relation. Only arrows whose binding pair
    actually swapped are judged — flipping one arrow says nothing about
    its untouched neighbours, and the operator's own binding bookkeeping
    is asserted in `TestOperators`, not here.

    Args:
        before: The base scene.
        after: The mutated scene.
        fb: Findings over `before`.
        fa: Findings over `after`.

    Returns:
        One detail string per swapped arrow whose drawn endpoints did
        not exchange; empty when the correspondence held.
    """
    out = []
    for b in before:
        if b["type"] != "arrow" or not (b.get("startBinding")
                                        and b.get("endBinding")):
            continue
        a = next((e for e in after if e["id"] == b["id"]), None)
        if a is None or not (a.get("startBinding") and a.get("endBinding")):
            continue
        if not (a["startBinding"]["elementId"] == b["endBinding"]["elementId"]
                and a["endBinding"]["elementId"]
                == b["startBinding"]["elementId"]):
            continue
        (bs, be), (as_, ae) = _ends(b), _ends(a)
        if math.hypot(as_[0] - be[0], as_[1] - be[1]) > 1 or \
                math.hypot(ae[0] - bs[0], ae[1] - bs[1]) > 1:
            out.append("flip left %s drawn unreversed" % b["id"])
    return out


def _corr_rename(before: list[dict], after: list[dict], fb: list[dict],
                 fa: list[dict]) -> list[str]:
    """A rename must not move geometry: findings must be identical.

    Args:
        before: The base scene.
        after: The mutated scene.
        fb: Findings over `before`.
        fa: Findings over `after`.

    Returns:
        A single detail string when the geometry finding set changed;
        empty when the correspondence held.
    """
    def key(fs: list[dict]) -> list[tuple[str, str]]:
        """Reduce findings to their check/element identity, ignoring prose.

        Args:
            fs: A findings list.

        Returns:
            The sorted `(check, element)` pairs.
        """
        return sorted((f["check"], str(f["element"])) for f in fs)
    return ([] if key(fb) == key(fa)
            else ["rename changed the geometry finding set"])


def _corr_rank(before: list[dict], after: list[dict], fb: list[dict],
               fa: list[dict]) -> list[str]:
    """Moving a node onto its neighbours' rank must not go unremarked.

    If two arrows now run collinear into and out of one node on
    opposite sides, the drawing asserts a straight pass-through the
    model does not have, and SOME detector must flag it.

    Args:
        before: The base scene.
        after: The mutated scene.
        fb: Findings over `before`.
        fa: Findings over `after`.

    Returns:
        One detail string per unflagged collinear pass-through; empty
        when the correspondence held.
    """
    survivors = []
    named = _named_elements(fa)
    arrows = [e for e in after if e["type"] == "arrow"
              and e.get("startBinding") and e.get("endBinding")]
    for ain in arrows:
        for aout in arrows:
            if ain is aout:
                continue
            if (ain["endBinding"]["elementId"] !=
                    aout["startBinding"]["elementId"]):
                continue
            in_y = ain["y"] + ain["points"][-1][1]
            out_y = aout["y"] + aout["points"][0][1]
            in_horiz = len({p[1] for p in ain["points"]}) == 1
            out_horiz = len({p[1] for p in aout["points"]}) == 1
            if in_horiz and out_horiz and abs(in_y - out_y) <= 2:
                node = ain["endBinding"]["elementId"]
                if node not in named and ain["id"] not in named:
                    survivors.append(
                        "phantom pass-through at node %s "
                        "(%s -> %s collinear, unflagged)"
                        % (node, ain["id"], aout["id"]))
    return survivors


def _corr_swap(before: list[dict], after: list[dict], fb: list[dict],
               fa: list[dict]) -> list[str]:
    """A swap must not leave stale geometry pointing at the old target.

    Args:
        before: The base scene.
        after: The mutated scene.
        fb: Findings over `before`.
        fa: Findings over `after`.

    Returns:
        One detail string per re-bound arrow whose final point sits
        further than `_SWAP_TOL` from its new target's center; empty
        when the correspondence held.
    """
    out = []
    centers = {e["id"]: (e["x"] + e.get("width", 0) / 2,
                         e["y"] + e.get("height", 0) / 2) for e in after}
    for b in before:
        if b["type"] != "arrow" or not b.get("endBinding"):
            continue
        a = next((e for e in after if e["id"] == b["id"]), None)
        if a is None or not a.get("endBinding"):
            continue
        target = a["endBinding"]["elementId"]
        if target == b["endBinding"]["elementId"] or target not in centers:
            continue
        end, center = _ends(a)[1], centers[target]
        gap = math.hypot(end[0] - center[0], end[1] - center[1])
        if gap > _SWAP_TOL:
            out.append("swap left stale geometry: %s ends %dpx from its "
                       "new target %s" % (a["id"], round(gap), target))
    return out


CORRESPONDENCE: dict[str, Callable[..., list[str]]] = {
    "drop_edge": _corr_drop_edge,
    "flip_direction": _corr_flip,
    "rename_node": _corr_rename,
    "move_node_onto_rank": _corr_rank,
    "swap_endpoints": _corr_swap,
}

# The sweep's base drawings, in report order.
SWEEP_BASES: tuple[tuple[str, Callable[[], list[dict]]], ...] = (
    ("chain", _chain),
    ("diamond", _diamond_stage),
    ("opposed", lambda: _opposed_pair(rounded=False)),
)


def sweep_args(op: str, scene: list[dict]) -> tuple[dict | None, str | None]:
    """Build one sweep cell's concrete operator arguments.

    Not every (base × operator) cell is buildable: `_chain` carries no
    labeled node, the opposed pair's arrows carry no bindings at all.
    Such a cell is SKIPPED EXPLICITLY, with its reason printed in the
    sweep report — never silently omitted, and never left to raise.

    Args:
        op: The operator name, a key of `CORRESPONDENCE`.
        scene: The base scene the operator would run against.

    Returns:
        `(args, None)` when the cell is buildable, else `(None, reason)`.

    Raises:
        EngineError: If `op` has no argument rule here, which would
            otherwise let a swept operator run on arguments nobody
            chose.
    """
    arrows = [e for e in scene if e.get("type") == "arrow"]
    bound = [a for a in arrows if a.get("startBinding") and a.get("endBinding")]
    if op == "drop_edge":
        return (({"arrow_id": arrows[0]["id"]}, None) if arrows
                else (None, "no arrow to drop"))
    if op == "flip_direction":
        return (({"arrow_id": bound[0]["id"]}, None) if bound
                else (None, "no arrow bound at both ends to reverse"))
    if op == "rename_node":
        node = next((e for e in scene
                     if any(b.get("type") == "text"
                            for b in (e.get("boundElements") or []))), None)
        return (({"node_id": node["id"], "text": _RENAME_TEXT}, None)
                if node else (None, "no node carries a bound text label"))
    if op == "move_node_onto_rank":
        return _rank_args(scene, bound)
    if op == "swap_endpoints":
        ends = [a for a in arrows if a.get("endBinding")]
        return (({"a_id": ends[0]["id"], "b_id": ends[1]["id"]}, None)
                if len(ends) >= 2
                else (None, "fewer than two arrows carry an end binding"))
    raise EngineError("no sweep argument rule for operator %r" % op)


def _rank_args(scene: list[dict],
               bound: list[dict]) -> tuple[dict | None, str | None]:
    """Pick a pass-through node and the rank line its source sits on.

    The target is the first node with one bound arrow in and another
    out; the destination `y` puts that node's centerline exactly on the
    inbound arrow's SOURCE node's centerline, which is what manufactures
    the straight rank line the sweep is probing for.

    Args:
        scene: The base scene.
        bound: Its arrows bound at both ends.

    Returns:
        `(args, None)` when such a node exists, else `(None, reason)`.
    """
    by_id = {e["id"]: e for e in scene}
    for e in scene:
        inbound = next((a for a in bound
                        if a["endBinding"]["elementId"] == e["id"]), None)
        outbound = next((a for a in bound
                         if a["startBinding"]["elementId"] == e["id"]), None)
        if inbound is None or outbound is None:
            continue
        src = by_id.get(inbound["startBinding"]["elementId"])
        if src is None:
            continue
        rank = src["y"] + src.get("height", 0) / 2
        return ({"node_id": e["id"],
                 "y": rank - e.get("height", 0) / 2}, None)
    return (None, "no pass-through node (one bound arrow in, another out)")


def sweep_cells() -> tuple[list[dict], list[tuple[str, str, str]]]:
    """Run every sweep cell, collecting survivors and skipped cells.

    Returns:
        `(survivors, skipped)` — survivors as `{"id", "detail"}` dicts
        sorted by id, skipped as `(base, op, reason)` triples in sweep
        order. Both are deterministic: the bases are fixed builders and
        an id hashes only its own detail string.
    """
    survivors: list[dict] = []
    skipped: list[tuple[str, str, str]] = []
    for base_name, build in SWEEP_BASES:
        for op, predicate in CORRESPONDENCE.items():
            scene = build()
            args, reason = sweep_args(op, scene)
            if args is None:
                skipped.append((base_name, op, str(reason)))
                continue
            after = OPERATORS[op](scene, **args)
            details = predicate(scene, after, collect_findings(scene),
                                collect_findings(after))
            survivors.extend(
                {"id": "%s:%s:%s"
                        % (op, base_name,
                           hashlib.sha1(detail.encode()).hexdigest()[:8]),
                 "detail": detail} for detail in details)
    return sorted(survivors, key=lambda s: s["id"]), skipped


def run_sweep(write: bool = True) -> int:
    """Sweep every cell, record the survivors, and report the verdicts.

    Args:
        write: Whether to overwrite `tests/mutants_sweep.json` with this
            run's record.

    Returns:
        The number of survivors carrying no `DISPOSITIONS` entry — 0
        when the sweep is fully accounted for, which is also this
        function's process exit code.
    """
    survivors, skipped = sweep_cells()
    if write:
        SWEEP_RECORD.write_text(json.dumps(
            {"swept": datetime.date.today().isoformat(),
             "survivors": survivors}, indent=2) + "\n")
    ran = len(SWEEP_BASES) * len(CORRESPONDENCE) - len(skipped)
    print("sweep: %d cells run, %d skipped, %d survivor(s)"
          % (ran, len(skipped), len(survivors)))
    for base, op, reason in skipped:
        print("  SKIP  %-10s %-20s %s" % (base, op, reason))
    missing = []
    for s in survivors:
        kind, why = DISPOSITIONS.get(s["id"], ("UNDISPOSITIONED", ""))
        if kind == "UNDISPOSITIONED":
            missing.append(s["id"])
        print("  %-16s %s" % (kind, s["id"]))
        print("      %s" % s["detail"])
        if why:
            print("      -> %s" % why)
    if missing:
        print("%d undispositioned survivor(s): add each id to DISPOSITIONS "
              "as promote / allow / bug, with a reason" % len(missing))
    return len(missing)


# Survivor verdicts. "promote": a real gap worth a curated mutant and a lint
# rule. "allow": correspondence the drawing legitimately need not answer,
# with the reason why. "bug": the operator or predicate is wrong — fix it and
# re-sweep rather than leaving the entry here.
DISPOSITIONS: dict[str, tuple[str, str]] = {
    "move_node_onto_rank:chain:ebb2e1f6": (
        "promote",
        "e1 phantom pass-through — the highest-hit-rate class from the "
        "Aug 2026 scan; promote to a curated mutant + WP4b candidate "
        "lint rule (V0.9-PLAN.md WP4b item 1)"),
}


class TestDiscovery(unittest.TestCase):
    """Spec §5: a survivor may be new once, never undispositioned twice."""

    def test_recorded_sweep_is_fully_dispositioned(self) -> None:
        """Every survivor in the committed sweep record carries a verdict."""
        rec = json.loads(
            (Path(__file__).parent / "mutants_sweep.json").read_text())
        missing = [s["id"] for s in rec["survivors"]
                   if s["id"] not in DISPOSITIONS]
        self.assertEqual(missing, [],
                         "undispositioned survivors from the last sweep — "
                         "run: python3 tests/test_mutants.py --sweep")

    def test_dispositions_carry_reasons(self) -> None:
        """Every disposition is a known verdict with a non-blank reason."""
        bad = [k for k, (kind, why) in DISPOSITIONS.items()
               if kind not in ("promote", "allow", "bug")
               or not why.strip()]
        self.assertEqual(bad, [])

    def test_live_sweep_reproduces_the_record(self) -> None:
        """The committed record still describes what a sweep finds today.

        Without this the record is only a file: a detector that starts
        catching a recorded survivor — or stops catching something —
        would leave a stale json passing the currency test forever.
        """
        rec = json.loads(SWEEP_RECORD.read_text())
        survivors, _skipped = sweep_cells()
        self.assertEqual([s["id"] for s in survivors],
                         [s["id"] for s in rec["survivors"]],
                         "the recorded sweep is stale — "
                         "run: python3 tests/test_mutants.py --sweep")


if __name__ == "__main__":
    if "--coverage" in sys.argv:
        for name, status, ev in coverage_table():
            print("%-28s %-12s %s" % (name, status, ev))
    elif "--sweep" in sys.argv:
        raise SystemExit(run_sweep())
    else:
        unittest.main()
