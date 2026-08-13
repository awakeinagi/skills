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

import argparse
import contextlib
import copy
import datetime
import hashlib
import inspect
import io
import json
import math
import re
import shutil
import sys
import tempfile
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
# were verified against canvas.py (lines 5083, 5097-5102, 5475, 5689).
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
# The one lint template that names TWO arrows and one node. `element` is the
# NODE, not either arrow: the pair is symmetric, so naming one of the two
# would be an arbitrary, iteration-order-dependent pick (and the ELK arm
# emitted three of these over one node, which would have reported the same
# arrow twice). The node is the shared attach site — the single element a
# fix has to change — and both arrow ids survive in `raw`. The trailing
# `[\w-]+` stops before the ` ('Label')` suffix `canvas.name()` appends.
_SHARED_ATTACH_RE = re.compile(
    r"arrows [\w-]+ and [\w-]+ share an attach point on (?P<element>[\w-]+)")
# The other symmetric-pair template, and the only one that names no third
# party to blame: two labels, and the reader has to be told BOTH or the
# finding says nothing actionable. So `element` is the quoted pair verbatim
# — `'settled and reconciled n' and 'queued'` — in scene order, which is the
# strongest identity this message affords. It affords no MAGNITUDE at all
# (canvas.py:5558 reports the collision and not how many pixels of it),
# which is why the mutant below asserts the pair and not a number; putting
# the overlap depth into that template is the standing proposal in
# docs/research/visualize-skill_idea_mining_2026-08-12.md O2, and the day
# it lands this spec should tighten to a magnitude.
_LABEL_OVERLAP_RE = re.compile(
    r"labels (?P<element>.+) overlap — nudge one clear")
# The clipped-text lint (canvas.py:5637), whose template is the richest one
# here: it carries a 2-D need AND a 2-D allowance AND which axis failed.
# `element` is the OWNER, not the text — the text is quoted as CONTENT and
# carries no id, so the box is the only element named, and it is also the one
# the first remedy ("widen the box") acts on. The `[\w-]+` stops before the
# ` ('Label')` suffix `canvas.name()` appends, as `_SHARED_ATTACH_RE`'s does.
#
# MAGNITUDE is the needed WIDTH — the first number, at a stable position —
# and the limitation is worth stating rather than hiding: a `FindingSpec`
# magnitude is one scalar and this finding is two, so on the too-tall arm the
# asserted number is not the axis that failed. The DIRECTION carries the axis
# instead, which is why the dirmap below distinguishes all three arms rather
# than collapsing them. Tighten this to a pair the day `FindingSpec` grows
# one; until then the band still discriminates, because on both mutants below
# it excludes every other number in the same message.
_TEXT_OVERFLOW_RE = re.compile(
    r"does not fit (?P<element>[\w-]+).*? needs ~(?P<mag>\d+)x\d+px, "
    r"the box gives \d+x\d+px "
    r"\((?P<dir>too wide and too tall|too wide|too tall)\)")


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
    "shared_attach_point": {"lint_re": _SHARED_ATTACH_RE},
    "label_label_overlap": {"lint_re": _LABEL_OVERLAP_RE},
    # Batch D follow-up, 2026-08-13: left the enumerated-no-mutant ledger
    # when the pair below proved it fires, on both arms, with magnitude and
    # direction. The dirmap keeps the three arms distinct because the
    # direction is where this check's axis information lives.
    "text_overflow": {"lint_re": _TEXT_OVERFLOW_RE,
                      "dirmap": {"too wide": "wide", "too tall": "tall",
                                 "too wide and too tall": "both"}},
    "crossings_count": {"collect": _collect_crossings},
    "shared_corridor": {"collect": _collect_corridors},
    "false_bidi": {"collect": _collect_false_bidi},
    "float_diamond": {"collect": _collect_float_diamond},
    # The render tier's two detectors are registered HERE, not by
    # test_mutants_render.py updating this dict at import: `--coverage`
    # runs through this file's standalone `__main__` (file tail), which
    # never imports the render module, so a self-registration would leave
    # `--coverage` silently missing two rows. (Discovery DOES import that
    # module — `skipUnless` defers to run time, not import time — so the
    # gap is the CLI's alone, and that is enough.) They carry neither `lint_re`
    # nor `collect`, so `collect_findings` walks straight past them — they
    # measure pixels, and the pixels only exist under MUTANTS_RENDER=1.
    "ablation_existence": {"render": True},
    "ablation_continuity": {"render": True},
    # Batch D, 2026-08-13: not an ablation at all — it compares tier 1's
    # markup against tier 1's own chosen viewport, so it is the one render
    # detector that measures a second render path rather than the picture.
    "parity_clipped": {"render": True},
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
    """The mutant's control scene: the detector's opposite pole, ungated.

    A neighbour is built and run AS-IS — `_run_neighbour` never applies
    the mutant's operator to it. Neighbours prove detector poles, not
    operator innocence: where the mutant pins the defect (and is usually
    an `expectedFailure`), the neighbour asserts the same detector's
    other answer on a healthy scene, plainly, in every commit. That is
    what keeps a crashed or dead detector from hiding inside the
    mutant's expectedFailure mask. So an over-fire mutant expecting
    `Silence` gets a neighbour whose `FindingSpec` proves the check
    still fires legitimately, and a mutant expecting a wrong finding
    gets a neighbour expecting `Silence` on the same check.

    Attributes:
        build: Zero-arg factory returning the neighbour scene's
            elements — already in its final, unmutated form.
        expect: The spec the neighbour's findings must satisfy, chosen
            as the opposite pole of the mutant's own `expect`.
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
    """Deep-copy in, validate out, and refuse to mutate nothing.

    The third guard is the root fix for a trap that surfaced twice
    downstream: an operator handed a bogus target id either returns the
    scene untouched (`drop_edge` filters on an id nothing matches) or
    dies on a bare `StopIteration` from its `next(...)` lookup, which
    names neither the operator nor the id. The first is the dangerous
    one — a pristine scene validates clean and travels on labelled
    "mutated", so `sweep_cells` had to grow an `after == scene` guard
    and the CLI's `seed` could write an unmutated artifact. Silence must
    mean the drawing answered the mutation, never that no mutation
    happened; so an unchanged output is engine misuse and raises here,
    at the source, where the operator's name and args are still in hand.
    `unchanged` is exempt BY NAME — it is the deliberate no-op behind
    the base-is-the-defect mutants.

    Args:
        fn: An operator body taking a mutable scene copy plus keyword
            args and returning the mutated scene.

    Returns:
        A wrapped operator with the same name and docstring as `fn`.
    """
    def wrapped(scene: list[dict], **args: Any) -> list[dict]:
        """Deep-copy `scene`, run the operator, then check and validate.

        Args:
            scene: The input scene; never mutated in place.
            **args: Operator-specific keyword arguments.

        Returns:
            The mutated, validated scene.

        Raises:
            EngineError: If the operator's target id names no element,
                or if a non-`unchanged` operator returned a scene equal
                to its input.
        """
        # Insurance, not a live fix: `fn` gets its own copy on the next
        # line, so today nothing can reach `scene`. Comparing against a
        # pristine copy keeps the guard honest if that ever changes —
        # an operator mutating its argument in place would otherwise
        # make every no-op look like a change, and the no-op guard would
        # go quiet exactly where it is needed. (The isolation this
        # depends on is itself pinned by
        # `test_mutation_does_not_alias_input`.)
        pristine = copy.deepcopy(scene)
        try:
            out = fn(copy.deepcopy(scene), **args)
        except StopIteration:
            # Every operator locates its target with `next(e for e in
            # scene if e["id"] == ...)`, so the only StopIteration any
            # of them can raise is that lookup missing. Some lookups are
            # on ids DERIVED from the scene rather than passed in (a
            # node's bound label id, say); those raise for themselves,
            # since this message would blame the caller's args for a
            # malformed scene. See `rename_node`.
            raise EngineError("%s: no element matches the target id in %r"
                              % (fn.__name__, args)) from None
        validate_scene(out)
        if fn.__name__ != "unchanged" and out == pristine:
            raise EngineError("%s: mutated nothing — bad target id or "
                              "no-op args? %r" % (fn.__name__, args))
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
        EngineError: If the node has no bound text label, or names one
            the scene does not contain.
    """
    node = next(e for e in scene if e["id"] == node_id)
    label_id = next((b["id"] for b in (node.get("boundElements") or [])
                     if b.get("type") == "text"), None)
    if label_id is None:
        raise EngineError("rename_node: %r has no bound label" % node_id)
    # Looked up with a default rather than bare, because `label_id` is
    # DERIVED from the node's boundElements, not passed in by the caller.
    # Letting the decorator's StopIteration handler report this would
    # blame the caller's `node_id`, which resolved perfectly well; the
    # fault is a scene whose boundElements names an absent label.
    label = next((e for e in scene if e["id"] == label_id), None)
    if label is None:
        raise EngineError("rename_node: %r is bound to label %r, which is "
                          "not in the scene — malformed scene, not a bad "
                          "target id" % (node_id, label_id))
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
def relabel(scene: list[dict], container_id: str, text: str) -> list[dict]:
    """Retext a bound label through canvas.py's own `mod` write path.

    The only operator here that does not edit the element dicts itself.
    That is the point: every other operator forges a scene, so a scene it
    produces can only ever pin what the DETECTORS make of geometry
    somebody typed. This one hands `apply_ops` the op an agent would
    really send — `{"op": "mod", "id": <arrow>, "attrs": {"label": ...}}`
    — and keeps whatever the write path decides, so the label's stored
    `width` is the production number and not a number a test author chose.
    A mutant built on it therefore pins the write path and the lint
    together: `lint_layout`'s `drawn_box` (canvas.py:5546) trusts stored
    width, so the day a write path stops recomputing it, the collision
    checks go quiet and this operator stops producing the finding its
    mutant asserts. That is the alarm; a forged scene cannot raise it.

    Args:
        scene: The scene copy to relabel.
        container_id: Id of the element carrying the bound label —
            an arrow here, since arrow labels are what the label
            collision checks measure.
        text: The label's new text.

    Returns:
        The scene as `apply_ops` returned it.

    Raises:
        EngineError: If canvas.py rejected the op batch. A rejected
            batch would otherwise return the scene untouched, and an
            untouched scene travelling on as "mutated" is the silence
            `_operator`'s no-op guard exists to kill.
    """
    errors: list[str] = []
    out = canvas.apply_ops(scene, [{"op": "mod", "id": container_id,
                                    "attrs": {"label": text}}], errors)
    if errors:
        raise EngineError("relabel(%r): canvas rejected the op batch: %s"
                          % (container_id, "; ".join(errors)))
    return out


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
    "relabel": relabel,
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
        """Unchanged returns an equal scene that is a distinct object.

        Equal output is what `_operator`'s no-op guard rejects for every
        other operator, so this doubles as the proof that `unchanged`'s
        by-name exemption holds — the base-is-the-defect mutants would
        all die at load without it.
        """
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

    def test_drop_edge_with_a_bogus_id_refuses_to_no_op(self) -> None:
        """A typo'd arrow id returns a pristine scene, so it must raise.

        `drop_edge` is the operator that made this trap concrete: it
        filters by id rather than looking one up, so a typo deletes
        nothing, validates clean, and travels on labelled "mutated".
        """
        with self.assertRaises(EngineError) as caught:
            OPERATORS["drop_edge"](_chain(), arrow_id="e1-typo")
        self.assertIn("drop_edge: mutated nothing", str(caught.exception))

    def test_move_endpoint_to_with_a_bogus_arrow_id_raises(self) -> None:
        """A missing target names the operator, not a bare StopIteration."""
        with self.assertRaises(EngineError) as caught:
            OPERATORS["move_endpoint_to"](
                _chain(), arrow_id="e1-typo", end="end", x=1, y=1)
        self.assertIn("move_endpoint_to: no element matches",
                      str(caught.exception))

    def test_rename_node_with_a_bogus_node_id_raises(self) -> None:
        """The node lookup missing is engine misuse, reported as such."""
        with self.assertRaises(EngineError) as caught:
            OPERATORS["rename_node"](
                _chain(), node_id="A-typo", text="Renamed")
        self.assertIn("rename_node: no element matches",
                      str(caught.exception))

    def test_rename_node_blames_the_scene_for_an_absent_label(self) -> None:
        """A derived id that misses indicts the scene, not the caller's args.

        `node_id` resolves perfectly here; it is the label id read OUT of
        the node's boundElements that names nothing. The decorator's
        message would have blamed `{'node_id': 'A', ...}` and sent the
        reader hunting a typo that isn't there.
        """
        node = el(id="A", type="rectangle", x=0, y=0, width=80, height=40,
                 boundElements=[{"id": "lbl-gone", "type": "text"}])
        with self.assertRaises(EngineError) as caught:
            OPERATORS["rename_node"]([node], node_id="A", text="Renamed")
        msg = str(caught.exception)
        self.assertIn("'lbl-gone'", msg)
        self.assertIn("malformed scene, not a bad target id", msg)

    def test_zero_shift_label_is_refused_as_a_no_op(self) -> None:
        """A real target with (0, 0) offsets mutates nothing, so it raises.

        The target id is fine here — this is the other half of the
        guard: arguments that make a legitimate operator do nothing are
        misuse too, and would otherwise mint a vacuous pass.
        """
        label = el(id="lbl", type="text", x=10, y=20, width=40, height=20,
                  text="A", originalText="A", containerId="A")
        node = el(id="A", type="rectangle", x=0, y=0, width=80, height=40,
                 boundElements=[{"id": "lbl", "type": "text"}])
        with self.assertRaises(EngineError) as caught:
            OPERATORS["shift_label"]([node, label], text_id="lbl", dx=0, dy=0)
        self.assertIn("shift_label: mutated nothing", str(caught.exception))


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

    def test_shared_attach_point_parses_from_real_lint_output(self) -> None:
        """Two arrows on one attach point parse, node id as the element.

        The node carries a bound label here, so the real message reads
        `... on N ('Hub')` — the regex has to stop at the id and not
        swallow the parenthesised display name.
        """
        scene = _attach_chain(shared=True)
        hub = next(e for e in scene if e["id"] == "N")
        hub["boundElements"] = [{"id": "t1", "type": "text"}]
        scene.append(el(id="t1", type="text", x=210, y=110, width=60,
                        height=20, text="Hub", fontSize=16, fontFamily=1,
                        textAlign="center", verticalAlign="middle",
                        containerId="N", originalText="Hub"))
        finds = collect_findings(scene)
        hits = [f for f in finds if f["check"] == "shared_attach_point"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["element"], "N")
        self.assertIn("('Hub')", hits[0]["raw"])
        self.assertIn("the auto-fan ran and left them together",
                      hits[0]["raw"])
        self.assertIsNone(hits[0]["magnitude"])
        self.assertIsNone(hits[0]["direction"])


# ---------------------------------------------------------------------------
# Export completeness (found via excalidraw-mcp field data, 2026-08-12 — their
# issue #22, where `export_to_excalidraw` dropped every text element, shipped,
# and no check said a word). Every detector above reads the element MODEL;
# `canvas.render_svg` is a second, hand-rolled renderer over that same model,
# independent of the web client's canvas. Two renderers, one truth — and a
# class the client paints while the exporter skips it produces a picture that
# lies BY ABSENCE. Absence is the one defect a reader cannot notice: the
# missing thing leaves no mark to be suspicious of, and the export is the
# agent's only view of its own drawing.
#
# Measured by ABLATION at the markup level: render the scene, render it again
# without one element, subtract the tag counts. The differential is what makes
# the magnitude honest. `"<rect" in svg` passes on the ground rect every scene
# carries, and a scene-wide tag count passes when one of two labels goes
# missing because the survivor answers for the casualty. What a class owes the
# export is its OWN tags, counted per element.
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<(\w+)")


def _tag_counts(svg: str) -> dict[str, int]:
    """Count an SVG document's elements by tag name.

    Args:
        svg: The rendered SVG text.

    Returns:
        Tag name -> occurrences. Closing tags never match, since the
        pattern wants a word character where `</text>` has a slash.
    """
    counts: dict[str, int] = {}
    for name in _TAG_RE.findall(svg):
        counts[name] = counts.get(name, 0) + 1
    return counts


def _export_delta(els: list[dict], eid: str) -> dict[str, int]:
    """The markup one element contributes to the export, by tag.

    Ablation rather than inspection, because `render_svg` paints a ground
    rect on every scene and a title on some: counting tags in a single
    render cannot say which element owns them. Re-rendering without `eid`
    and subtracting cancels everything that is not about `eid`.

    The subtraction is exact only because this renders at `render_svg`'s
    DEFAULTS. Ablating an element shrinks the viewport, and under
    `footnotes=True` the note block wraps to that width
    (canvas.py:4524-4529), so a narrower scene can rewrap a note onto more
    lines and move the `text` count for reasons that have nothing to do
    with `eid`. Pass footnotes through here and the counts stop being
    attributable — measure that path by counting markers against notes
    instead, the way `test_footnote_markers_match_the_footnote_list` does.

    Args:
        els: The full scene. Must hold something besides `eid` — an empty
            list takes `render_svg`'s "(empty artifact)" branch, whose
            markup is about no class at all.
        eid: The element to ablate.

    Returns:
        Tag name -> count contributed, zero deltas dropped. An empty dict
        means the element reached the export as nothing whatsoever.
    """
    full = _tag_counts(canvas.render_svg(els)[0])
    less = _tag_counts(canvas.render_svg(
        [e for e in els if e.get("id") != eid])[0])
    return {tag: n for tag, n in
            ((t, full.get(t, 0) - less.get(t, 0))
             for t in set(full) | set(less)) if n}


def _one_of(etype: str) -> list[dict]:
    """An anchor rectangle plus one element of the class under test.

    The anchor exists so the ablated render still has something to draw,
    and it sits 200px clear of `x-1` so no overlap, binding or lint rule
    can colour the measurement. Only the keys each class actually needs
    are added — the rest of `el`'s defaults are inert here.

    Args:
        etype: An element type from `canvas.ELEMENT_TYPES`.

    Returns:
        The two-element scene: `anchor`, then `x-1` of type `etype`.
    """
    extra: dict[str, Any] = {
        "arrow": {"points": [[0, 0], [120, 0]]},
        "line": {"points": [[0, 0], [120, 0]]},
        "freedraw": {"points": [[0, 0], [40, 30], [80, 10], [120, 60]]},
        "image": {"fileId": "f1"}, "frame": {"name": "Frame"},
        "text": {"text": "ink", "fontSize": 16}}.get(etype, {})
    return [el(id="anchor", type="rectangle", x=0, y=0, width=120,
               height=60),
            el(id="x-1", type=etype, x=0, y=200, width=120, height=60,
               **extra)]


# What each class that survives export owes it, per instance — MEASURED
# against live `render_svg` on 2026-08-12, not read off its source. An arrow
# owes a stroke and its arrowhead; a frame owes its box and its name; a line
# owes a stroke and no head. The two classes missing from this table are
# missing on purpose: they owe nothing today, and that is the defect pinned
# red below, so writing them here as `{}` would enshrine the bug as the spec.
_EXPORT_MARKUP: dict[str, dict[str, int]] = {
    "rectangle": {"rect": 1}, "ellipse": {"ellipse": 1},
    "diamond": {"polygon": 1}, "line": {"polyline": 1},
    "arrow": {"polyline": 1, "polygon": 1}, "text": {"text": 1},
    "frame": {"rect": 1, "text": 1}}

# Shipped classes `render_svg`'s paint dispatch has no branch for. Note for
# whoever writes those branches: the bounds loop reads `points` for arrows and
# lines ONLY (canvas.py:4498), so a freedraw contributes its STORED width and
# height and not the extent of its stroke — paint it from `points` and any
# part of the stroke overhanging that box lands outside the viewBox.
_DROPPED = ("freedraw", "image")


class TestExportCompleteness(unittest.TestCase):
    """`render_svg` emits every class it ships — or is caught not to."""

    def test_every_shipped_class_is_accounted_for(self) -> None:
        """A newly shipped element class cannot arrive unpinned.

        Both tables are hand-measured, so the one thing neither can do is
        notice a tenth name in `ELEMENT_TYPES`. Without this, adding a
        class the paint dispatch does not handle would repeat the exact
        defect these tests exist to catch, and repeat it silently.
        """
        self.assertEqual(set(_EXPORT_MARKUP) | set(_DROPPED),
                         set(canvas.ELEMENT_TYPES))

    def test_shipped_classes_reach_the_export(self) -> None:
        """Every surviving class contributes exactly its own markup."""
        for etype, want in sorted(_EXPORT_MARKUP.items()):
            with self.subTest(element_type=etype):
                self.assertEqual(_export_delta(_one_of(etype), "x-1"), want)

    def test_a_second_instance_exports_a_second_time(self) -> None:
        """Magnitude, not presence: two texts owe two text tags.

        Presence assertions are how an export loses one of two labels and
        still reads as healthy — `"<text" in svg` is answered by whichever
        label survived. Counted per element, the casualty has to answer
        for itself.
        """
        scene = _one_of("text")
        scene.append(el(id="x-2", type="text", x=0, y=300, width=120,
                        height=60, text="more", fontSize=16))
        self.assertEqual(_tag_counts(canvas.render_svg(scene)[0])["text"], 2)
        self.assertEqual(_export_delta(scene, "x-1"), {"text": 1})
        self.assertEqual(_export_delta(scene, "x-2"), {"text": 1})

    def test_bound_labels_reach_the_export_with_their_backdrop(self) -> None:
        """Both label bindings survive, and the arrow label keeps its ground.

        A label bound to a NODE and one bound to an ARROW take different
        paths through `paint`, and the arrow path is load-bearing: the
        client breaks the stroke behind an edge label, this renderer has
        no such notion, so it paints the ground back in under the text or
        the export shows a connector struck through its own label (r5-14).
        That backdrop is therefore part of what the edge label OWES — two
        tags, not one. Pinning only the text would let it go quietly.
        """
        node = el(id="n1", type="rectangle", x=0, y=0, width=120, height=60,
                  boundElements=[{"id": "t-node", "type": "text"}])
        dest = el(id="n2", type="rectangle", x=280, y=0, width=120,
                  height=60)
        arrow = el(id="a1", type="arrow", x=120, y=30, width=160, height=0,
                   points=[[0, 0], [160, 0]],
                   boundElements=[{"id": "t-edge", "type": "text"}])
        scene = [node, dest, arrow,
                 el(id="t-node", type="text", x=30, y=20, width=60,
                    height=20, text="Node", fontSize=16, textAlign="center",
                    verticalAlign="middle", containerId="n1",
                    originalText="Node"),
                 el(id="t-edge", type="text", x=170, y=20, width=60,
                    height=20, text="then", fontSize=16, textAlign="center",
                    verticalAlign="middle", containerId="a1",
                    originalText="then")]
        self.assertEqual(_export_delta(scene, "t-node"), {"text": 1})
        self.assertEqual(_export_delta(scene, "t-edge"),
                         {"text": 1, "rect": 1})

    def test_footnote_markers_match_the_footnote_list(self) -> None:
        """`--with-footnotes` cannot print a note nothing on the drawing marks.

        The marker circles and the numbered list are emitted by two
        separate loops over one `collect_footnotes` result, so they can
        drift apart without either half looking wrong on its own — and a
        handover export whose notes point at nothing is worse than one
        carrying no notes at all. Green today: this is the pin, not a
        report.
        """
        scene = [el(id="n-a", type="rectangle", x=0, y=0, width=120,
                    height=60, customData={"tooltip": "first"}),
                 el(id="n-b", type="rectangle", x=200, y=0, width=120,
                    height=60, customData={"tooltip": "second"})]
        svg = canvas.render_svg(scene, footnotes=True)[0]
        want = len(canvas.collect_footnotes(scene))
        self.assertEqual(want, 2)
        self.assertEqual(_tag_counts(svg).get("circle"), want)
        self.assertEqual(
            len(re.findall(r"<text[^>]*>\d+\. ", svg)), want)

    def test_dropped_classes_are_red_by_measurement_not_by_error(self) -> None:
        """The two reds below are red for the reason they claim.

        `@unittest.expectedFailure` swallows ERRORS as well as failures,
        so a `render_svg` that started RAISING on these classes would go
        on printing a healthy `x`: the pin would be gone and the run
        would look identical (skill doctrine §6). `TestMutantCatalogue`'s
        own guard cannot reach these — it walks `type(self)` and maps
        method names onto `CATALOGUE`, and export reds are outside both —
        so this is that guard's analog for this class, ungated on purpose
        because it has to speak in every commit.

        It doubles as the talkative half of unexpected-success detection:
        `unittest` reports a red that has quietly gone green as an
        anonymous "unexpected success", while this names the class and
        says which decorator to drop.
        """
        for etype in _DROPPED:
            with self.subTest(element_type=etype):
                try:
                    delta = _export_delta(_one_of(etype), "x-1")
                except Exception as exc:
                    self.fail("%s's red is red via %r, not a spec "
                              "mismatch — that is a broken pin, not a "
                              "defect pin" % (etype, exc))
                self.assertEqual(
                    delta, {},
                    "%s now reaches the export as %r — flip the "
                    "expectedFailure on "
                    "test_red_%s_never_reaches_the_export"
                    % (etype, delta, etype))

    @unittest.expectedFailure
    def test_red_freedraw_never_reaches_the_export(self) -> None:
        """A freehand stroke is in `ELEMENT_TYPES` and in no export.

        `paint` dispatches on rectangle/ellipse/diamond/arrow/line/text/
        frame and returns silently for anything else, so a stroke the user
        drew with the pencil is stored, is counted into the export's
        bounds — and paints nothing, leaving a hole the reader takes for
        empty canvas. The user's own mark is the case that stings: the
        drawing is the truth (v0.6 WP1), and the agent narrates from a
        snapshot the mark is missing from.

        Asserted as "at least one tag", not an exact count, because
        whether the fix emits `<polyline>` or `<path>` belongs to the WP
        that owns the dispatch. Flips when that branch lands.
        """
        delta = _export_delta(_one_of("freedraw"), "x-1")
        self.assertGreaterEqual(
            sum(delta.values()), 1,
            "freedraw x-1 contributes no markup to the export: it is in "
            "ELEMENT_TYPES, it is in the model, it is not in the picture")

    @unittest.expectedFailure
    def test_red_image_never_reaches_the_export(self) -> None:
        """A pasted image is in the drawing and in no export.

        Same missing branch as the stroke above, and the same silence,
        but the reachability differs and matters: `make_element` refuses
        op-made images outright (they would have no `fileId` to render),
        so every image in a scene got there because a person pasted or
        dropped it on the canvas. That makes this the purest form of the
        defect — the export drops only what the user put there by hand.

        Flips when the dispatch grows an `image` branch; whether that is
        an `<image>` href or a labelled placeholder is the owning WP's
        call, so only the count is pinned.
        """
        delta = _export_delta(_one_of("image"), "x-1")
        self.assertGreaterEqual(
            sum(delta.values()), 1,
            "image x-1 contributes no markup to the export: it is in the "
            "model, it widens the bounds, it is not in the picture")


def _annotation_at(corner_clear: bool) -> list[dict]:
    """A circle and an annotation, in its empty bbox corner or on its body.

    The circle is 400x400 at (300,250) — centre (500,450), r=200 — so the
    bounding box corner is 82.8px deep, wide enough to park a readable
    text in the void without any part of it touching the drawn outline.
    At (302,252) the 70x24 annotation's nearest point is 16.0px clear of
    the circle; at (460,420) it lies across the body, 194px inside.

    Args:
        corner_clear: True to park the annotation in the empty corner.

    Returns:
        The two-element scene: circle `n1`, then annotation `t1`.
    """
    tx, ty = (302, 252) if corner_clear else (460, 420)
    return [el(id="n1", type="ellipse", x=300, y=250, width=400, height=400,
               customData={"role": "node"}),
            el(id="t1", type="text", x=tx, y=ty, width=70, height=24,
               text="see note", fontSize=16,
               customData={"role": "annotation"})]


def _arrow_label_at(corner_clear: bool) -> list[dict]:
    """The same circle, with a BOUND ARROW LABEL in the void or on the body.

    `annotation_overlaps_node` and `label_on_foreign_node` are siblings
    with the same bbox blindness, and a scene proving one says nothing
    about the other: a fix teaching only the annotation check about
    shapes would flip that red and leave this arm exactly as blind. So
    this stage exercises the label arm on its own.

    Placing it takes one indirection. The client re-centres a bound arrow
    label on the arc-length MIDPOINT of its path and discards the stored
    x/y, and `arrow_label_anchor` mirrors that — so the label is put in
    the corner void by putting the ARROW's midpoint there, at (337,264),
    which lands the 70x24 label at (302,252): the same 16.0px of clear
    space the annotation stage uses.

    The arrow necessarily crosses the void too, so both scenes also draw
    an unbound-arrow warning and a `passes_through_foreign` hit. They
    draw the SAME two in both, and `_says_lies_on` reads neither.

    Args:
        corner_clear: True to park the label in the empty corner.

    Returns:
        The three-element scene: circle `n1`, arrow `ax`, label `t1`.
    """
    mx, my = (337, 264) if corner_clear else (500, 450)
    arrow = el(id="ax", type="arrow", x=mx - 60, y=my, width=120, height=0,
               points=[[0, 0], [120, 0]], customData={"role": "edge"},
               boundElements=[{"id": "t1", "type": "text"}])
    return [el(id="n1", type="ellipse", x=300, y=250, width=400, height=400,
               customData={"role": "node"}),
            arrow,
            el(id="t1", type="text", x=0, y=0, width=70, height=24,
               text="then", fontSize=16, containerId="ax",
               originalText="then")]


def _says_lies_on(scene: list[dict]) -> list[str]:
    """Lint warnings claiming a text sits on a node.

    Args:
        scene: The scene to lint.

    Returns:
        The matching warning lines, one per claim.
    """
    lint = canvas.lint_layout(scene, artifact_type="flow")
    return [w for w in lint["warnings"]
            if "lies on top" in w or "lands on" in w]


class TestShapeBlindAnnotationOverlap(unittest.TestCase):
    """Shape-blindness instance five: text/node overlap is raw bbox math.

    A sibling of `ellipse_corner_overfire`, and outside `CATALOGUE` for a
    reason worth stating: an over-fire mutant asserts `Silence` on its
    check, and neither `annotation_overlaps_node` (canvas.py:5593) nor
    `label_on_foreign_node` (:5576) has a `DETECTORS` entry — both sit in
    `UNCOVERED`. A `Silence` on an unregistered check passes vacuously,
    so the catalogue cannot hold these until those entries land. Read as
    lint text instead, which needs no registry.

    Both checks get their OWN red, over their own scene. They share a
    defect but not a code path — one reads a text's stored box, the other
    resolves a bound label through `arrow_label_anchor` first — so a fix
    can land on one and miss the other, and one red covering both would
    stay red and say nothing about which.
    """

    def test_an_annotation_on_the_body_is_reported(self) -> None:
        """The check fires where it should — and the red below needs it to.

        Without this, a `lint_layout` that stopped emitting the warning
        at all would turn the red green and read as a fix.
        """
        self.assertTrue(_says_lies_on(_annotation_at(corner_clear=False)))

    @unittest.expectedFailure
    def test_red_annotation_clear_of_a_circle_is_reported_as_on_it(
            self) -> None:
        """An annotation 16px clear of the circle is called an overlap.

        Both text/node checks — `label_on_foreign_node` (canvas.py:5580)
        and `annotation_overlaps_node` (:5593) — intersect raw
        `x/y/width/height` rectangles with no shape term at all, so an
        ellipse is its bounding box to both of them. Here the annotation
        sits wholly in the corner void, its nearest point 16.0px from the
        drawn outline, and the lint says it "lies on top of" the circle in
        the same words it uses for one lying across the middle.

        The `nodes` set these loops walk is not shape-filtered either, so
        a diamond would read the same way. Flips when the checks test the
        drawn shape — the geometry is already in canvas.py as
        `marker_inset` (4200).

        This method pins `annotation_overlaps_node` ONLY; its sibling has
        its own red below, because a fix could teach one and not the
        other.
        """
        self.assertEqual(
            _says_lies_on(_annotation_at(corner_clear=True)), [],
            "the annotation is 16px clear of the circle and reported as "
            "on it")

    def test_an_arrow_label_on_the_body_is_reported(self) -> None:
        """The label arm fires where it should, and its red needs it to."""
        self.assertTrue(_says_lies_on(_arrow_label_at(corner_clear=False)))

    @unittest.expectedFailure
    def test_red_arrow_label_clear_of_a_circle_is_reported_as_on_it(
            self) -> None:
        """A bound arrow label 16px clear of the circle is called an overlap.

        The sibling check, pinned separately on purpose.
        `label_on_foreign_node` (canvas.py:5576-5578) runs the same raw
        rectangle intersection as `annotation_overlaps_node`, against the
        same unfiltered `nodes` set, and until this method existed the
        `"lands on"` arm of `_says_lies_on` matched nothing in either
        scene — the helper anticipated the check, the docstring claimed
        it, and no assertion reached it. A WP4 fix teaching only the
        annotation loop about shapes would have flipped the red above and
        left this one silently as blind as before.

        One difference from its sibling is worth knowing when fixing:
        this check reads the label's DRAWN box via `arrow_label_anchor`,
        not its stored x/y, so the shape term has to be applied after
        that resolution rather than before it.
        """
        self.assertEqual(
            _says_lies_on(_arrow_label_at(corner_clear=True)), [],
            "the arrow label is 16px clear of the circle and reported as "
            "landing on it")


class TestMermaidRoundTripIdentity(unittest.TestCase):
    """`--relayout` matches nodes by id, never by label — pinned green."""

    def test_flow_to_mermaid_carries_element_identity(self) -> None:
        """Every node reaches the mermaid text as `n_<element id>`.

        No defect claimed: this is the protection itself, pinned so it
        cannot be refactored away quietly. `--relayout` round-trips a flow
        through mermaid and maps dagre's answer back with
        `sk["id"][2:] in ix` (canvas.py:10583), so identity lives in that
        `n_` prefix and nothing is ever matched by label. Were it matched
        by label instead, two nodes reading "Review" would swap positions
        on every re-layout and the drawing would rearrange itself for
        reasons no one could see.

        Asserted per element and exactly, not as a substring count: with
        three nodes, losing one and duplicating another is invisible to
        any looser check.
        """
        flow = [el(id="step-%d" % i, type=t, x=i * 200, y=0, width=120,
                   height=60, customData={"role": "node"})
                for i, t in enumerate(("rectangle", "diamond", "ellipse"))]
        flow += [el(id="step-%d-label" % i, type="text", x=i * 200 + 10,
                    y=20, width=100, height=20, text="Review",
                    containerId="step-%d" % i) for i in range(3)]
        text, count = canvas._flow_to_mermaid(flow)
        self.assertEqual(count, 3)
        # Every id present, once declared, prefix intact — and the shared
        # "Review" label proves none of it depends on the labels differing.
        for i in range(3):
            with self.subTest(node=i):
                self.assertEqual(text.count("n_step-%d" % i), 1)
        self.assertEqual(sorted(re.findall(r"n_(step-\d)", text)),
                         ["step-0", "step-1", "step-2"])


# ---------------------------------------------------------------------------
# Paint order (found during the visualize-skill idea-mine, 2026-08-12 —
# docs/research/visualize-skill_idea_mining_2026-08-12.md §(i.2), where their
# renderer's z-order bug was turned back on ours and reproduced). Excalidraw's
# element ARRAY IS the z-order: index 0 is the bottom of the stack. Two things
# we document and ship say so — `references/ops-reference.md:90`, decorations
# are "painted beneath arrows", and `:213`, the whole `reorder` op, whose one
# job is z-order by array index.
#
# `render_svg` discards it. It paints in four type-buckets (canvas.py:4642-
# 4653: frames, then arrows/lines, then other shapes, then text), so order
# holds WITHIN a bucket and is thrown away ACROSS them. A decoration at index
# 0 — declared behind everything — is painted last, over the connector it was
# meant to sit behind, and erases it.
#
# Measured as EMISSION ORDER in the SVG string, not as pixels: later markup
# paints over earlier markup, the string is cheap to assert, and no browser
# is needed to say which of two elements went down first. The render tier
# would say the same thing in pixels and is the natural second home for this
# (see the note on the red below) — but the render tier's own substrate is
# this very SVG, which is why the model-tier pin comes first.
#
# Why this is not a cosmetic export bug. `tests/test_mutants_render.py`
# rasterizes this SVG, rasterizes it again with one element omitted, and
# diffs. An element occluded ONLY by the bucketing contributes zero pixels,
# so ablating it changes nothing, so `ablation_existence` concludes a plainly
# visible element is invisible. The tier whose whole claim is that it reads
# the picture rather than the model can read an occlusion that is not on the
# canvas. `export` (the user's handover artifact) and `snapshot` tier-3 (a
# headless agent's only eyes) read the same string.
#
# canvas.py:50 already comments a "backing painted under arrow" special case
# for label backdrops: the paint-order problem was seen, solved pointwise for
# one element, and never generalized.
# ---------------------------------------------------------------------------

# A fill no other part of a render emits, so its first occurrence in the SVG
# is the decoration's own tag and nothing else's — the ground rect
# (canvas.py's SVG_GROUND) and every default style are different colours.
_DECOR_FILL = "#ffd8a8"


def _backdrop_scene(behind: bool) -> list[dict]:
    """A decoration and a connector that overlap, stacked either way round.

    The smallest drawing that can state the contract: one opaque
    `role: decoration` panel covering one straight connector, and nothing
    else — no bindings, no labels, no nodes, so the only thing that can
    decide which is visible is the array order the caller chose.

    Args:
        behind: True to declare the decoration at index 0 (the bottom of
            the stack, so the connector must stay visible); False to
            declare it after the connector (the top, so it must cover it).

    Returns:
        The two-element scene, in the requested array order.
    """
    bg = el(id="bg", type="rectangle", x=0, y=0, width=200, height=100,
            backgroundColor=_DECOR_FILL, strokeColor=_DECOR_FILL,
            customData={"role": "decoration"})
    arrow = el(id="e1", type="arrow", x=20, y=50, width=160, height=0,
               points=[[0, 0], [160, 0]], customData={"role": "edge"})
    return [bg, arrow] if behind else [arrow, bg]


def _paint_offsets(scene: list[dict]) -> tuple[int, int]:
    """Where the decoration and the connector land in the emitted SVG.

    Args:
        scene: A scene built by `_backdrop_scene`.

    Returns:
        `(decoration_offset, connector_offset)` as character positions in
        the rendered SVG. The LARGER offset is painted later, and so on
        top.

    Raises:
        ValueError: If either element emitted no markup at all. Said out
            loud rather than returned as a -1, because a missing element
            is the EXPORT-COMPLETENESS defect and a -1 would quietly sort
            to the bottom of the stack and read as a paint-order answer.
    """
    svg = canvas.render_svg(scene)[0]
    decor, stroke = svg.find(_DECOR_FILL), svg.find("<polyline")
    if decor < 0 or stroke < 0:
        raise ValueError(
            "the scene emitted no %s at all — that is export "
            "completeness, not paint order"
            % ("decoration" if decor < 0 else "connector"))
    return decor, stroke


class TestPaintOrder(unittest.TestCase):
    """The element array is the z-order — or `render_svg` is caught losing it."""

    def test_a_decoration_declared_in_front_is_painted_in_front(self) -> None:
        """The contract's other pole, and it holds: index 1 paints last.

        Green today, and green after the fix — which is exactly what makes
        it the control. A "fix" that simply reversed the buckets, or one
        that dropped array order in the other direction, would satisfy the
        red below and break this. Both poles or neither.
        """
        decor, stroke = _paint_offsets(_backdrop_scene(behind=False))
        self.assertGreater(
            decor, stroke,
            "a decoration declared AFTER the connector must be painted "
            "over it")

    def test_array_order_is_honoured_within_one_type_bucket(self) -> None:
        """Order is not lost everywhere — only across bucket boundaries.

        This is what makes the red below a bucketing defect rather than a
        renderer that never reads the array: two rectangles land in one
        bucket, and swapping them swaps the emission. Without this the red
        could be "read" as `render_svg` having no notion of z-order at all,
        and the fix would be scoped wrong.
        """
        pair = [el(id="r1", type="rectangle", x=0, y=0, width=100,
                   height=100, backgroundColor="#aaaaaa"),
                el(id="r2", type="rectangle", x=20, y=20, width=100,
                   height=100, backgroundColor="#bbbbbb")]
        svg = canvas.render_svg(pair)[0]
        self.assertLess(svg.index("#aaaaaa"), svg.index("#bbbbbb"))
        swapped = canvas.render_svg(pair[::-1])[0]
        self.assertLess(swapped.index("#bbbbbb"), swapped.index("#aaaaaa"))

    def test_zorder_red_is_red_by_measurement_not_by_error(self) -> None:
        """The red below is red for the reason it claims.

        `@unittest.expectedFailure` swallows ERRORS as well as failures,
        so a `render_svg` that stopped emitting the decoration entirely —
        or stopped emitting the stroke — would go on printing a healthy
        `x` while the pin measured nothing (skill doctrine §6). It also
        gives the flip a voice: `unittest` reports a red gone green as an
        anonymous "unexpected success", while this names the decorator to
        drop and the WP that earned it.
        """
        try:
            decor, stroke = _paint_offsets(_backdrop_scene(behind=True))
        except Exception as exc:
            self.fail("the z-order red is red via %r, not a measurement "
                      "mismatch — that is a broken pin, not a defect pin"
                      % exc)
        self.assertGreater(
            decor, stroke,
            "the decoration at array index 0 is now painted BENEATH the "
            "connector (%d < %d) — WP4 honoured array order: drop the "
            "expectedFailure on "
            "test_red_zorder_bucketing_occludes_connector"
            % (decor, stroke))

    @unittest.expectedFailure
    def test_red_zorder_bucketing_occludes_connector(self) -> None:
        """A backdrop declared at the bottom of the stack erases the arrow.

        `{"op": "reorder", "id": "bg-panel", "index": 0}` is documented as
        the way to put a panel behind the drawing, and against this
        renderer it does nothing whatsoever: both orderings of the same two
        elements emit byte-identical markup, because the arrow bucket runs
        before the shape bucket either way. The picture then asserts
        something the model never said — the connector is gone, and the
        reader has no way to be suspicious of a stroke that leaves no mark.

        Asserted as emission order, not as a pixel diff, so the pin costs
        no browser; the same defect is worth a render-tier sibling once
        `ablation_existence` can be trusted not to be measuring this bug
        (§(i.2): today it is the bug's own victim). Flips when the paint
        dispatch walks `live` once in array order instead of four times by
        type — the WP that owns `render_svg`, not this file.
        """
        decor, stroke = _paint_offsets(_backdrop_scene(behind=True))
        self.assertLess(
            decor, stroke,
            "decoration 'bg' is declared at array index 0 — beneath "
            "everything — but its markup is emitted at %d, after the "
            "connector's at %d: it is painted OVER the arrow and erases "
            "it (ops-reference.md:90, :213)" % (decor, stroke))


# ---------------------------------------------------------------------------
# Store integrity — the data-loss family (Batch A, 2026-08-12: flowchartai
# mine M6 §d for the load path, excalidraw-mcp mine M4 for file references,
# the latter grounded in Excalidraw's own excalidrawDiff.ts:261 note that
# deleting an image element never deletes its asset).
#
# Everything else in this file judges a picture. These judge the LOADER, and
# the failure mode is worse in kind: a lint that misses costs a reader one
# wrong impression, while a loader that dies or goes quiet costs the user
# their work. The probe discipline is therefore the WP1 one — cause a
# failure, then diff the state — run against a throwaway project built in a
# temp directory, never against `tests/fixtures/` in place.
#
# These are plain tests rather than `Mutant` entries, and the reason is now
# structural rather than incidental: a `Mutant` is judged by
# `collect_findings` over an ELEMENT LIST, and none of what follows is in
# one. Three distinct things have now fallen outside that scene unit, two of
# them here:
#   1. rendered markup    — `render_svg`'s output is not elements
#                           (`TestExportCompleteness`);
#   2. save records       — not elements at all, and not even in the
#                           artifact file;
#   3. the `files` map    — doc-level, a SIBLING of `elements`, so the
#                           orphan direction cannot be written as a scene.
# Worth knowing before anyone tries to fold these into the catalogue: the
# blocker is the scene unit, not the will to write the quadruple.
# ---------------------------------------------------------------------------

_GOOD_ARTIFACT = json.dumps({
    "type": "excalidraw", "version": 2,
    "elements": [{"id": "n1", "type": "rectangle", "x": 0, "y": 0,
                  "width": 120, "height": 60}]})

_GOOD_SAVE = json.dumps({"revn": 1, "note": "baseline"})

# A node whose bound label is far wider than it, with an arrow bound to the
# node's BOTTOM edge — the edge the ART-011 refit moves when it grows the
# container to fit the wrapped label. `s1` exists only to give the arrow a
# start binding; the interesting end is the one on `n1`.
_OVERSIZED_LABEL_TEXT = "Escalate to the compliance review board immediately"
_OVERSIZED_LABEL_ARTIFACT = json.dumps({
    "type": "excalidraw", "version": 2, "elements": [
        {"id": "n1", "type": "rectangle", "x": 0, "y": 0, "width": 120,
         "height": 60, "customData": {"role": "node"},
         "boundElements": [{"id": "t1", "type": "text"}]},
        {"id": "t1", "type": "text", "x": 4, "y": 20, "width": 400,
         "height": 20, "text": _OVERSIZED_LABEL_TEXT, "fontSize": 16,
         "originalText": _OVERSIZED_LABEL_TEXT, "containerId": "n1",
         "textAlign": "center"},
        {"id": "s1", "type": "rectangle", "x": 0, "y": 300, "width": 120,
         "height": 60, "customData": {"role": "node"}},
        {"id": "a1", "type": "arrow", "x": 60, "y": 300, "width": 0,
         "height": 240, "points": [[0, 0], [0, -240]],
         "startBinding": {"elementId": "s1", "focus": 0, "gap": 1},
         "endBinding": {"elementId": "n1", "focus": 0, "gap": 1}}]})

# One image element pointing at a fileId the `files` map does not hold, and
# one `files` entry no element points at — the two directions of file-
# reference integrity, in one artifact so a single load probes both.
_FILEREF_ARTIFACT = json.dumps({
    "type": "excalidraw", "version": 2,
    "elements": [{"id": "n1", "type": "rectangle", "x": 0, "y": 0,
                  "width": 120, "height": 60},
                 {"id": "img", "type": "image", "x": 0, "y": 200,
                  "width": 120, "height": 90, "fileId": "missing-file-id"}],
    "files": {"orphan-file-id": {"id": "orphan-file-id",
                                 "mimeType": "image/png",
                                 "dataURL": "data:image/png;base64,AAAA"}}})


def _scratch_project(case: unittest.TestCase, artifacts: dict[str, str],
                     saves: dict[str, str]) -> Path:
    """Write a throwaway project tree and return its root.

    File CONTENTS are passed as raw strings, not objects, because every
    defect below is about malformed bytes: a helper that took dicts could
    not express a file truncated mid-JSON.

    The root is returned rather than a loaded `Store` because the two
    classes below probe different layers of the same load — one reads the
    store's own attributes, the other runs a CLI command that opens the
    project itself and is judged on what it prints.

    Args:
        case: The test owning the tree; its `addCleanup` removes it.
        artifacts: `{stem: file body}` written under
            `project_knowledge/artifacts/<stem>.excalidraw`.
        saves: `{stem: file body}` written under
            `project_knowledge/saves/<stem>.json`.

    Returns:
        The project root directory holding `project_knowledge/`.
    """
    tmp = tempfile.mkdtemp(prefix="mutants-store-")
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    pk = Path(tmp) / "project_knowledge"
    (pk / "artifacts").mkdir(parents=True)
    (pk / "saves").mkdir(parents=True)
    for stem, body in artifacts.items():
        (pk / "artifacts" / (stem + ".excalidraw")).write_text(
            body, encoding="utf-8")
    for stem, body in saves.items():
        (pk / "saves" / (stem + ".json")).write_text(body, encoding="utf-8")
    return Path(tmp)


class TestStoreIntegrity(unittest.TestCase):
    """One bad record must not cost the project — and must never go quiet."""

    def _load(self, artifacts: dict[str, str],
              saves: dict[str, str]) -> canvas.Store:
        """Build a throwaway project on disk and load it.

        Args:
            artifacts: `{stem: file body}` under `artifacts/`.
            saves: `{stem: file body}` under `saves/`.

        Returns:
            The loaded `canvas.Store` — `Store.__init__` loads eagerly, so
            a load-time crash surfaces from the constructor.
        """
        return canvas.Store(canvas.Project(
            _scratch_project(self, artifacts, saves)))

    def test_scratch_project_baseline_loads_clean(self) -> None:
        """Two good artifacts and a good save record load with no issues.

        The reds below are all deviations FROM this baseline, so this is
        their error-red guard: if the harness ever stops being able to
        build a loadable project, every `expectedFailure` in this class
        would go on passing while measuring nothing, and this is what
        says so instead.
        """
        st = self._load({"a": _GOOD_ARTIFACT, "b": _GOOD_ARTIFACT},
                        {"0001-x": _GOOD_SAVE})
        self.assertEqual(sorted(st.scenes), ["a", "b"])
        self.assertEqual(sorted(st.records), [1])
        self.assertEqual([i.get("code") for i in st.issues], [])

    def test_truncated_save_record_is_quarantined_loudly(self) -> None:
        """A save record cut mid-JSON is skipped, reported, rest still loads."""
        st = self._load({"a": _GOOD_ARTIFACT},
                        {"0001-x": _GOOD_SAVE, "0002-y": '{"revn"'})
        self.assertEqual(sorted(st.scenes), ["a"])
        self.assertEqual(sorted(st.records), [1])
        self.assertIn("SAV-001", {i.get("code") for i in st.issues})

    def test_save_record_missing_its_revision_is_quarantined_loudly(
            self) -> None:
        """Valid JSON with no `revn` is skipped loudly, not guessed at."""
        st = self._load({"a": _GOOD_ARTIFACT},
                        {"0001-x": _GOOD_SAVE, "0002-y": '{"note": "x"}'})
        self.assertEqual(sorted(st.records), [1])
        self.assertIn("SAV-001", {i.get("code") for i in st.issues})

    def test_truncated_artifact_is_quarantined_loudly(self) -> None:
        """An artifact cut mid-JSON is skipped as ART-006; siblings survive.

        This is the contrast the two reds below are measured against:
        the loader ALREADY knows how to lose one file without losing the
        project, and how to say so.
        """
        st = self._load({"a": _GOOD_ARTIFACT, "b": '{"elements":['},
                        {"0001-x": _GOOD_SAVE})
        self.assertEqual(sorted(st.scenes), ["a"])
        self.assertIn("ART-006", {i.get("code") for i in st.issues})

    def test_red_non_dict_save_record_takes_down_the_whole_store(self) -> None:
        """One save record holding `[]` is quarantined; the project opens.

        A record that is valid JSON but not an OBJECT used to slip past
        the save loop's `except (ValueError, KeyError)` and kill the
        constructor: `apply_migrations` opens by calling `migration_list`,
        whose body is `doc.setdefault("migrations", [])`, which raises
        `AttributeError` on a list. Nothing caught it, so no artifact
        loaded, no save loaded, and the project would not open at all.
        `Store.load` now rejects a non-`dict` record before migrating it,
        which lands it in the same `SAV-001` quarantine as a truncated
        file or a missing `revn` — both pinned green above.

        That call site is why the guard has to come first: `migration_list`
        is the FIRST line of `apply_migrations`, ahead of the `pending`
        computation and its `if not pending: return` early-out, so the
        crash never depended on a migration actually being due. A fully
        migrated project died exactly the same way, on every load.

        `[]` is not an exotic corruption — it is what any tool that writes
        "just the array" produces, and `null`, `"text"` and a bare number
        reached the same crash.

        The `except Exception` below pins the OUTCOME — the project opens
        — and deliberately not the mechanism: any exception escaping the
        load is the same disaster for the user, wherever it comes from.
        """
        try:
            st = self._load({"a": _GOOD_ARTIFACT},
                            {"0001-x": _GOOD_SAVE, "0002-y": "[]"})
        except Exception as exc:
            self.fail("Store.load died on ONE bad save record (%s: %s) — "
                      "the whole project is unreachable, artifacts and all"
                      % (type(exc).__name__, exc))
        self.assertEqual(sorted(st.scenes), ["a"])
        self.assertIn("SAV-001", {i.get("code") for i in st.issues})

    def test_red_non_dict_artifact_is_dropped_silently(self) -> None:
        """An artifact holding `[]` is dropped LOUDLY — the project says so.

        `validate_scene` builds exactly the right issue for this —
        ART-000, "not a JSON object", with a repair hint — and `Store.load`
        used to throw it away: the loop filing `art_issues` sat BELOW
        `if doc is None: continue`, so the unusable artifact — the case
        that most needs saying so — was the one case never reported. The
        drawing vanished from the project in silence, while the same file
        truncated one byte earlier reported ART-006.

        Losing the file is defensible; losing it quietly is not. Filing now
        happens before the skip, so this pins that an artifact leaving the
        project is always named in `issues`.
        """
        st = self._load({"a": _GOOD_ARTIFACT, "b": "[]"},
                        {"0001-x": _GOOD_SAVE})
        said = " ".join(str(i) for i in st.issues)
        self.assertIn(
            "b", said,
            "artifact 'b' left the project and no issue mentions it "
            "(issues=%r) — validate_scene made an ART-000 for exactly "
            "this and the loader dropped it" % (st.issues,))

    def test_quarantine_is_reported_but_never_filed_as_a_repair(self) -> None:
        """An unreadable artifact reaches `issues` only, not `scene_repairs`.

        Filing ART-000 above the skip — the fix that flipped the red above
        — put it in front of two consumers that read `scene_repairs` as
        work the loader DID: `catch_up` gates `repair_only` on it and
        names its codes in a "load-time repair" headline. A quarantine
        repaired nothing; the file is still unreadable on disk. So the
        loud half and the repaired half are different claims, and this
        pins that the fix bought the first without asserting the second.

        `Issue.repaired` is the discriminator, deliberately, not a list of
        codes: ART-000 is merely the only issue `validate_scene` currently
        builds with the flag False, and a code list would silently stop
        covering the second one the moment anybody adds it.
        """
        st = self._load({"a": _OVERSIZED_LABEL_ARTIFACT, "b": "[]"},
                        {"0001-x": _GOOD_SAVE})
        codes = [i["code"] for i in st.issues]
        self.assertIn("ART-000", codes, "the drop must still be reported")
        self.assertIn("ART-011", codes, "the real repair must still be "
                      "reported (issues=%r)" % (codes,))
        self.assertEqual(
            [i["code"] for i in st.scene_repairs], ["ART-011"],
            "ART-011 refit the label and belongs in scene_repairs; ART-000 "
            "touched nothing and must not be counted as repair work "
            "(scene_repairs=%r)" % (st.scene_repairs,))

    def test_quarantine_alone_makes_catch_up_claim_no_repairs(self) -> None:
        """With only a drop to report, no resume narration says "repair".

        The end of the channel the test above guards. `repair_only` is
        `bool(self.scene_repairs) and ...`, so a quarantine filed there
        would flip a load with NO repair work into the repairs-only
        branch and hand the user "load-time repair: ART-000 ×1 — no
        outside edits" about a file that is still broken on disk.

        The headline is asserted by what it may not CLAIM rather than by
        its exact text: the drift wording is incidental to this defect and
        pinning it would make an unrelated headline reword fail here.
        """
        st = self._load({"a": _GOOD_ARTIFACT, "b": "[]"},
                        {"0001-x": _GOOD_SAVE})
        self.assertEqual(st.scene_repairs, [])
        rec = st.catch_up()
        headline = ((rec or {}).get("summary") or {}).get("headline") or ""
        self.assertNotIn("repair", headline.lower(),
                         "nothing was repaired, but the resume headline "
                         "says it was: %r" % (headline,))
        self.assertNotIn("repairs", rec or {},
                         "an unrepaired quarantine was written onto the "
                         "reconciliation record as repair work")

    def test_the_fileref_artifact_loads_and_keeps_its_orphan(self) -> None:
        """The two file-reference reds' setup, asserted where nothing masks it.

        `artifact_files` and `referential` are read ONLY inside those two
        reds, and both are `expectedFailure`. Rename either attribute —
        exactly what a WP1 fix to this area would be touching — and both
        reds become swallowed `AttributeError`s printing a healthy `x`
        (doctrine §6). This is their standing guard: ungated, naming both
        attributes, and asserting the load those reds measure deviations
        from.

        `referential` is asserted to be a dict rather than to be EMPTY,
        deliberately. Its emptiness is the very silence the reds pin, so
        pinning it here as well would make WP1's fix break this test in
        the same change that flips them — a guard that fights the fix it
        is waiting for.
        """
        st = self._load({"a": _FILEREF_ARTIFACT}, {"0001-x": _GOOD_SAVE})
        self.assertEqual(sorted(st.scenes), ["a"])
        self.assertEqual(list(st.artifact_files.get("a", {})),
                         ["orphan-file-id"])
        self.assertIsInstance(st.referential, dict)

    def test_art011_repair_grows_the_container(self) -> None:
        """The load-time refit really does resize a shape.

        The red below measures what that resize leaves behind, so this
        pins the resize itself: if ART-011 ever stops firing on this
        artifact — a threshold moves, the repair is rewritten — the red
        would go quiet for a reason that has nothing to do with arrow
        re-routing, and read as a fix.
        """
        st = self._load({"a": _OVERSIZED_LABEL_ARTIFACT},
                        {"0001-x": _GOOD_SAVE})
        self.assertIn("ART-011", {i.get("code") for i in st.issues})
        n1 = next(e for e in st.scenes["a"] if e["id"] == "n1")
        self.assertEqual(n1["height"], 136)

    @unittest.expectedFailure
    def test_red_art011_repair_strands_the_bound_arrow(self) -> None:
        """A repair moves the shape and leaves its bound arrow behind.

        ART-011 refits an oversized label by calling `fit_label_in`
        (canvas.py:485), which GROWS the container to fit the wrapped text
        (canvas.py:969-971). Here `n1` goes from 60px tall to 136px. The
        arrow bound to its bottom edge is not re-routed, so an endpoint
        that was exactly on the border ends up interior by the time the
        load finishes — geometry the user never wrote.

        Two numbers describe that endpoint and both are right: it sits
        76px above the grown bottom edge (y=136) and 60px below the top
        one (y=0). The lint reports the NEARER edge, so its message says
        60px; the 76px figure is how far the border travelled out from
        under it. They are not in conflict and neither is the magnitude
        this test asserts — it asserts no finding at all.

        `endpoint_gap` DOES report the consequence, so this is not a
        silence; it is a MISATTRIBUTION. The message says "arrow a1 claims
        to bind n1 … ends 60px inside the shape — re-route it", blaming
        the user's arrow for a move the loader made. Nothing in `issues`
        says the loader resized anything with arrows on it.

        Asserted as the absence of that endpoint finding, because a repair
        that re-routes what it moves leaves nothing to report. Flips when
        the refit re-routes bound arrows — or when it declines to grow a
        container that has any.
        """
        st = self._load({"a": _OVERSIZED_LABEL_ARTIFACT},
                        {"0001-x": _GOOD_SAVE})
        gaps = [f for f in collect_findings(st.scenes["a"])
                if f["check"] == "endpoint_gap"]
        self.assertEqual(
            gaps, [],
            "the ART-011 refit grew n1 and left a1 behind: %s"
            % [f["raw"] for f in gaps])

    def test_red_dangling_file_reference_is_reported(self) -> None:
        """An image whose fileId names no file in `files` is reported.

        `referential_findings` is the pass whose whole subject is "a
        reference whose target is gone", and until v0.9 WP1 it never
        mentioned `fileId` — nor did `validate_scene`, nor `lint_layout`.
        So an image element pointing at a file the document does not
        carry loaded clean while the picture showed a hole, and nothing
        downstream surfaced it either (`render_svg` paints nothing for an
        image — see `TestExportCompleteness`).

        WP1 gave the pass a files arm, so the missing id now rides
        `st.referential` as a WARNING on that artifact. Asserted against
        BOTH channels, unchanged from when this was red: the claim is
        that SOMETHING says the id, not that a particular one does.
        """
        st = self._load({"a": _FILEREF_ARTIFACT}, {"0001-x": _GOOD_SAVE})
        said = json.dumps([st.issues, st.referential], default=str)
        self.assertIn(
            "missing-file-id", said,
            "nothing reports an image element bound to a fileId the "
            "document does not hold")

    def test_red_orphaned_file_entry_is_reported(self) -> None:
        """A `files` entry no element references is kept — and now named.

        The other direction, and Excalidraw's own documented one
        (excalidrawDiff.ts:261: deleting an image element never deletes
        its asset). Delete the image, keep the blob: the store reads
        `doc["files"]` wholesale into `artifact_files` and writes it back
        out on every save, so a project accumulates dataURL payloads
        nothing can ever draw.

        The retention itself is unchanged and still correct — WP1 reports
        the blob rather than dropping it, because a load that deleted
        user bytes to tidy a lint would be the worse defect. The finding
        is a NOTE carrying the id and what the entry costs the file in
        bytes on every save.

        The retention half — that the blob really is kept — is asserted
        by `test_the_fileref_artifact_loads_and_keeps_its_orphan` above,
        unmasked, so this one carries only its own claim: that something
        SAYS so.
        """
        st = self._load({"a": _FILEREF_ARTIFACT}, {"0001-x": _GOOD_SAVE})
        said = json.dumps([st.issues, st.referential], default=str)
        self.assertIn(
            "orphan-file-id", said,
            "the store kept an unreferenced file blob and nothing "
            "reported it")

    def test_a_resolved_file_reference_says_nothing(self) -> None:
        """The neighbour of both flips: matched ids produce no finding.

        Built from `_FILEREF_ARTIFACT` by the smallest edit that heals
        it — pointing the element and the blob at one id — so it differs
        from the mutant in exactly the property the two arms judge, and a
        files arm that fired on every image or every blob would be caught
        here rather than shipping as permanent noise on healthy projects.

        The load is asserted as well as the silence, because an artifact
        that failed to load would also produce no file findings and would
        read as a passing control.
        """
        doc = json.loads(_FILEREF_ARTIFACT)
        entry = doc["files"].pop("orphan-file-id")
        entry["id"] = "matched-file-id"
        doc["files"]["matched-file-id"] = entry
        for e in doc["elements"]:
            if e.get("fileId"):
                e["fileId"] = "matched-file-id"
        st = self._load({"a": json.dumps(doc)}, {"0001-x": _GOOD_SAVE})
        self.assertEqual(list(st.artifact_files.get("a", {})),
                         ["matched-file-id"])
        self.assertEqual(st.referential, {})
        self.assertEqual([i.get("code") for i in st.issues], [])

    def test_a_soft_deleted_image_does_not_vouch_for_its_file(self) -> None:
        """The real-world orphan: delete the image, the blob stays.

        Excalidraw soft-deletes, and `normalize_scene_doc` drops
        `isDeleted` elements at load while keeping `doc["files"]` whole —
        so the element is gone from the picture the moment the project
        opens and the blob is not. A files arm that counted the deleted
        element as a reference would call this artifact healthy for as
        long as the tombstone survived, which is the commonest way an
        orphan is made in the first place.

        Both directions are asserted: the blob is named as orphaned, and
        the deleted element does NOT earn a dangling warning of its own —
        an element the load discards is not something to send the user
        back to the canvas over.

        The note is asserted to name the id and to carry SOME byte count,
        never a particular one: the magnitude channel is the doctrinal
        half ("say what you measured") and the digits belong to the
        fixture, which two neighbouring tests are free to edit. Nothing
        here pins the remedy clause — it says today that no prune path
        exists (backlog #21), and that sentence should be free to change
        the day one does.
        """
        doc = json.loads(_FILEREF_ARTIFACT)
        for e in doc["elements"]:
            if e.get("fileId"):
                e["fileId"], e["isDeleted"] = "orphan-file-id", True
        st = self._load({"a": json.dumps(doc)}, {"0001-x": _GOOD_SAVE})
        found = st.referential.get("a") or {}
        self.assertEqual(found.get("warnings"), [])
        self.assertEqual(len(found.get("notes") or []), 1, found)
        self.assertIn("orphan-file-id", (found.get("notes") or [""])[0])
        self.assertRegex((found.get("notes") or [""])[0], r"\(\d+ bytes\)")


# ---------------------------------------------------------------------------
# The reporting surface (v0.9 re-review O-1, 2026-08-13; fixed in Task 33).
# The class above pins what `Store.issues` CARRIES; this one pins what the
# agent is TOLD, and the two had come apart. `issues` gained ART-000 in the
# fix round above, but every printer of load findings filtered on
# `if i.get("repaired")` — `cmd_status` (resume) and `cmd_lint` — so the
# QUARANTINE half reached no agent-facing surface at all: neither ART-000 nor
# the long-shipped SAV-001. A project whose second artifact was unreadable
# linted as "a project with one artifact", and nothing anywhere said a file
# had been dropped. Both printers now split on `repaired` instead of
# filtering by it, and this class holds that split open.
#
# This is the fourth thing to fall outside the scene unit named above, and
# the furthest out: the evidence is a CLI command's STDOUT, so there is no
# element list to judge and no `collect_findings` to judge it with. The
# quadruple is therefore kept by hand — a base project, one mutation (an
# artifact body of `[]`), the expectation, and neighbours at the filtering
# predicate's other pole. The magnitude analogue for a surface is WHAT it
# must contain: every quarantined issue's code and its message, so the agent
# learns which file left. The direction analogue is WHICH CLAIM it makes:
# named as a drop, never as a repair — see the flip note on the red.
# ---------------------------------------------------------------------------


class TestLoadFindingsReachTheAgent(unittest.TestCase):
    """A finding filed at load and never printed is a finding nobody has."""

    def _lint(self, root: Path) -> list[str]:
        """Run `canvas.py lint` over a project and capture what it printed.

        `cmd_lint` is called in-process rather than through a subprocess
        because it opens the project itself and needs no running server,
        so these lines are the same bytes an agent's terminal receives.

        Args:
            root: Project root, as built by `_scratch_project`.

        Returns:
            The command's stdout, split into lines.
        """
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            canvas.cmd_lint(argparse.Namespace(project=str(root),
                                               artifact=None))
        return buf.getvalue().splitlines()

    def _quarantine_project(self) -> Path:
        """One good artifact and one dropped artifact, plus a dropped save.

        The smallest project carrying BOTH quarantine producers, which is
        the point: `b`'s body is `[]`, which `validate_scene` rejects as
        ART-000, and `0002-y`'s is `[]`, which the save loop rejects as
        SAV-001. Two unrelated code paths in `Store.load`, one shared
        silent consumer downstream — pinning them together is what says
        the defect is the print filter and not either producer.

        `a` and `0001-x` load cleanly so the project still has something
        to talk about; without them the surface's silence would be honest.

        Returns:
            The project root.
        """
        return _scratch_project(self, {"a": _GOOD_ARTIFACT, "b": "[]"},
                                {"0001-x": _GOOD_SAVE, "0002-y": "[]"})

    def test_both_quarantines_are_filed_unrepaired(self) -> None:
        """The red's scene really does produce two unrepaired issues.

        Its error-red guard, ungated. The red below asserts over whatever
        `issues` holds, so a scene that stopped quarantining anything
        would make it iterate an empty list and pass — reading as a fix
        for a surface nobody changed. This pins the premise instead: two
        issues, both flagged unrepaired, which is what makes them
        invisible downstream.
        """
        st = canvas.Store(canvas.Project(self._quarantine_project()))
        self.assertEqual(sorted(st.scenes), ["a"])
        self.assertEqual({i["code"] for i in st.issues},
                         {"ART-000", "SAV-001"})
        self.assertEqual([i["code"] for i in st.issues if i.get("repaired")],
                         [], "neither quarantine repaired anything")

    def test_lint_names_a_load_time_repair(self) -> None:
        """The repaired half of the same filter does reach stdout.

        The neighbour, and the reason it is this scene: ART-011 rides the
        exact `if i.get("repaired")` branch the red dies on, at its other
        pole. So this proves the print loop is reached, that it formats a
        code and a message, and that the surface is alive — which is what
        stops a broken helper, a renamed `cmd_lint` or a deleted loop from
        hiding inside the red's `expectedFailure` mask and reading as
        health.

        The message is matched on the element it names rather than in
        full: the repair's exact wording is incidental here, and pinning
        it would make an unrelated reword fail in a test about reach.
        """
        root = _scratch_project(self, {"a": _OVERSIZED_LABEL_ARTIFACT},
                                {"0001-x": _GOOD_SAVE})
        out = self._lint(root)
        said = [ln for ln in out if ln.startswith("REPAIR=ART-011:")]
        self.assertEqual(len(said), 1,
                         "the one load-time repair reached lint %d times: %r"
                         % (len(said), out))
        self.assertIn("t1", said[0],
                      "the repair line does not name what it refitted")

    def test_lint_on_a_clean_load_invents_no_load_finding(self) -> None:
        """A load with nothing to report reports nothing — the silent pole.

        The other neighbour. Without it, a "fix" that printed a load
        finding unconditionally would satisfy the red while telling every
        healthy project that something had gone wrong. `ARTIFACTS=2` is
        asserted alongside because it is the same count the red watches
        collapse to 1, so this fixes the surface's honest reading of a
        two-artifact project as the baseline that reading deviates from.
        """
        root = _scratch_project(self, {"a": _GOOD_ARTIFACT,
                                       "b": _GOOD_ARTIFACT},
                                {"0001-x": _GOOD_SAVE})
        out = self._lint(root)
        self.assertIn("ARTIFACTS=2", out)
        noise = [ln for ln in out
                 if ln.startswith("REPAIR=") or "ART-" in ln or "SAV-" in ln]
        self.assertEqual(noise, [],
                         "a clean load was told something happened to it")

    def test_red_lint_never_names_what_the_load_quarantined(self) -> None:
        """Two files left the project and the only surface names both.

        Was red through v0.9 Task 33. `canvas.py lint` is the sole
        server-free surface that prints load findings, and its loop kept
        only issues with `repaired` true. Quarantines are exactly the
        issues that repaired nothing, so the filter dropped the whole
        class: this project printed `LAYOUT_NOTE=a: ...`, `ARTIFACTS=1`,
        `FINDINGS=1` and stopped, naming artifact `b` nowhere — a
        one-artifact project and no reason to doubt it. `status` shared
        the predicate, so the resume surface was silent the same way. Both
        loops now print every finding, under `REPAIR=` or `QUARANTINE=`
        as its own `repaired` flag decides.

        Magnitude is what the output must CONTAIN: every unrepaired
        issue's code AND its message, since the code alone would not say
        which file went. Asserted over `issues` rather than against the
        two literal codes, following the model-layer test above:
        `repaired` is the discriminator, so a third quarantine added later
        is covered the day it is added, not the day someone remembers.

        Direction is the CLAIM the line makes, and the second assertion
        is what the fix had to satisfy. The one-line "fix" of deleting the
        filter would print `REPAIR=ART-000: b: not a JSON object` — a
        repair headline over a file still unreadable on disk, the same
        falsehood `test_quarantine_is_reported_but_never_filed_as_a_repair`
        keeps out of the model layer. It stays here to hold the two
        headings apart if either loop is ever collapsed back into one.
        """
        root = self._quarantine_project()
        out = self._lint(root)
        joined = "\n".join(out)
        dropped = [i for i in canvas.Store(canvas.Project(root)).issues
                   if not i.get("repaired")]
        missing = [i["code"] for i in dropped
                   if i["code"] not in joined or i["msg"] not in joined]
        self.assertEqual(
            missing, [],
            "the load quarantined %s; lint names %s nowhere, so all the "
            "agent is shown is %r"
            % ([i["code"] for i in dropped], missing, out))
        mislabelled = [ln for ln in out if ln.startswith("REPAIR=")
                       and any(i["code"] in ln for i in dropped)]
        self.assertEqual(
            mislabelled, [],
            "a quarantine repaired nothing, but the surface files it as "
            "repair work: %r" % (mislabelled,))


# ---------------------------------------------------------------------------
# The WRITE path (v0.9 Task-5 review, 2026-08-13). The two classes above judge
# what a LOAD produces; this one judges what an op BATCH does — the same store
# driven the other way. Three shipped defects the reviewer reproduced against
# three revisions, and the fifth subject in this file to fall outside the
# scene unit: the evidence is a store's ANSWER to a batch, so there is no
# element list and no `collect_findings` to judge one with. `mutants new`
# declines the subject outright — it wants a `DETECTORS` code and emits an
# element-list scaffold — so the quadruple is kept by hand, exactly as
# `TestLoadFindingsReachTheAgent` keeps it.
#
# BASE: a one-node artifact whose mapping list is known exactly. MUTATION: one
# field — an `index` of `-1` where a valid index belongs, or a
# `rename_artifact` naming the id its own batch is creating. The MAGNITUDE
# analogue for a write path is what SURVIVES the op: which mappings remain,
# which scenes and files exist. The DIRECTION analogue is which way the batch
# resolved — refused with a named error, or accepted and acted on. The two are
# not interchangeable, and a batch that does neither is the first red below.
# NEIGHBOURS sit at the same predicates' other poles: an index past the TOP of
# the list, a valid index, and the same batch with its failing op removed.
#
# Deliberately NOT re-covered here: whether the rejection guard restores state
# afterwards. `TestFailurePathAtomicity` (tests/test_failure_paths.py) owns
# that and pins it GREEN — including the very IndexError below, whose own
# docstring there records that "the negative index itself is a separate,
# still-open defect". These three pin the gaps that suite leaves: the
# validation that lets a negative index through at all, the mapping a negative
# index silently removes, and the create that outlives its rejected batch.
# ---------------------------------------------------------------------------


class TestBatchPathIntegrity(unittest.TestCase):
    """An op does what was asked, or says why — never quietly neither."""

    def _store(self) -> tuple[canvas.Store, Path]:
        """Build a throwaway project holding one artifact and no mappings.

        The smallest store an `index` op can be aimed at. `mappings` is
        empty deliberately: that is the pole where a negative index has
        nothing at all to land on, so the miss surfaces as a crash rather
        than as a wrong write. The single node exists only so the
        artifact is legal and a mapping has something to name.

        Returns:
            `(the loaded store, the project root directory)`.
        """
        tmp = Path(tempfile.mkdtemp(prefix="mutants-batch-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        project = canvas.Project(tmp)
        project.ensure_tree()
        store = canvas.Store(project)
        store.apply_batch({
            "base_revn": 0, "artifact": "flow",
            "create": {"id": "flow", "name": "Flow", "type": "flow",
                       "concept": "checkout", "concept_name": "Checkout"},
            "ops": [{"op": "add", "element": {
                "type": "rectangle", "id": "n1", "label": "N1", "x": 0,
                "y": 0, "width": 100, "height": 60, "role": "node"}}]})
        return store, tmp

    def _with_mappings(self, store: canvas.Store) -> canvas.Store:
        """Attach two mappings, `alpha` then `beta`, in that order.

        Two is the fewest that can tell "the op removed the one it was
        asked for" apart from "the op removed the last one", which is the
        entire distinction the remove red measures. They arrive in
        separate batches so the stored order is the order they were
        added, and the red can name which one went.

        Args:
            store: A store from `_store`, still holding no mappings.

        Returns:
            The same store, so callers can chain the call.
        """
        for concept in ("alpha", "beta"):
            store.apply_batch({
                "base_revn": store.head_revn(), "artifact": "flow",
                "ops": [{"op": "registry", "action": "add_mapping",
                         "concept": concept, "elements": ["flow#n1"]}]})
        return store

    def _send(self, store: canvas.Store,
              op: dict[str, Any]) -> Exception | None:
        """Apply one registry op and hand back whatever escaped, if any.

        The reds turn on WHICH exception reaches the caller: `BatchError`
        becomes the 422 the CLI prints as an `ERROR=` line
        (canvas.py:8944), and nothing else is converted at all. So the
        exception has to be a value to assert about, not something an
        `assertRaises` narrows to one type in advance — catching only
        `BatchError` would let the crash these pin propagate and turn an
        honest red into an error-red (doctrine §6).

        Args:
            store: The store to apply against, at its current head.
            op: The registry op's body, e.g. `{"action": …, "index": …}`.

        Returns:
            The exception the batch raised, or `None` if it was applied.
        """
        payload = dict(op, op="registry")
        try:
            store.apply_batch({"base_revn": store.head_revn(),
                               "artifact": "flow", "ops": [payload]})
        except Exception as exc:            # broad on purpose: see above
            return exc
        return None

    def _create_batch(self, store: canvas.Store,
                      failing: bool) -> dict[str, Any]:
        """A batch that creates `ghost` and renames it in the same breath.

        `rename_artifact` is what drives this shape onto disk: it writes
        the new name through to the artifact FILE as it validates
        (canvas.py:6885), and `_seed_created_meta` publishes the create
        early enough for a registry op to name the id its own batch is
        making — the BUG-03 workflow. So the create is written from
        INSIDE the commit window rather than after the ops, which is the
        only way a rejection can arrive with the file already written. A
        create plus a failing op alone does not reproduce it, and stops
        reproducing it as of e2f3bf0.

        Args:
            store: The store the batch will be applied to, at its head.
            failing: Append the `set_round` carrying a string that the
                review used to reject the batch after that write.

        Returns:
            An op-batch envelope creating and renaming `ghost`.
        """
        ops: list[dict[str, Any]] = [
            {"op": "add", "element": {
                "type": "rectangle", "id": "g1", "label": "G1", "x": 0,
                "y": 0, "width": 100, "height": 60, "role": "node"}},
            {"op": "registry", "action": "rename_artifact",
             "artifact": "ghost", "name": "Renamed Ghost"}]
        if failing:
            ops.append({"op": "registry", "action": "set_round",
                        "round": "two"})
        return {"base_revn": store.head_revn(), "artifact": "ghost",
                "create": {"id": "ghost", "name": "Ghost", "type": "flow",
                           "concept": "checkout",
                           "concept_name": "Checkout"},
                "ops": ops}

    def _ghost_file(self, root: Path) -> Path:
        """Where a `ghost` artifact lands on disk.

        Args:
            root: The project root returned by `_store`.

        Returns:
            The path `_write_artifact` writes `ghost` to, whether or not
            it currently exists.
        """
        return root / "project_knowledge" / "artifacts" / "ghost.excalidraw"

    def test_the_seeded_store_holds_one_artifact_and_no_mappings(
            self) -> None:
        """The baseline all three reds below are deviations from.

        Each red measures what one batch changes about this store, so a
        harness that quietly stopped being able to BUILD it — a `create`
        block whose schema moved, an `add_mapping` that started rejecting
        this element spelling — would leave all three `expectedFailure`s
        passing while measuring nothing at all (doctrine §6). This is
        their standing guard, and it covers both fixtures: the empty
        mapping list the annotate red needs, and the two-mapping list the
        remove red needs.
        """
        store, root = self._store()
        self.assertEqual(sorted(store.scenes), ["flow"])
        self.assertEqual(store.registry["mappings"], [])
        self.assertTrue((root / "project_knowledge" / "artifacts" /
                         "flow.excalidraw").exists())
        self.assertEqual(
            [m["concept"] for m in self._with_mappings(store)
             .registry["mappings"]], ["alpha", "beta"])

    @unittest.expectedFailure
    def test_red_a_negative_annotate_index_escapes_as_a_bare_crash(
            self) -> None:
        """`index: -1` walks past the bounds check and dies uncaught.

        The check is `idx >= len(reg["mappings"])` (canvas.py:7480),
        which is false for every negative number, so `-1` is waved
        through as a valid index and `reg["mappings"][-1]` is then
        evaluated against an empty list. The op does not fail the batch;
        it kills it. An `IndexError` leaves `apply_batch`, where
        `BatchError` is the only exception any caller knows about:
        `_handle_apply` turns `BatchError` into a 422 the CLI prints as
        `ERROR=` and converts nothing else, so the v0.8 promise that
        every failure prints an `ERROR=` line is broken here by a bare
        traceback the agent cannot parse or act on.

        DIRECTION is the first assertion and the whole point: a batch
        must resolve one way or the other, and this one resolves neither.
        MAGNITUDE, for a rejection, is what the message identifies — the
        action and the field at fault — so the agent learns which of its
        ops was refused rather than guessing. That is pinned to what the
        already-working upper pole says, not to new wording, so the
        minimal fix flips this without also rewriting a message; the
        neighbour asserts the same two tokens on the same empty list.

        Flips when the bounds check gains its sign half.
        """
        store, _ = self._store()
        escaped = self._send(store, {"action": "annotate_mapping",
                                     "index": -1, "note": "boom"})
        self.assertIsInstance(
            escaped, canvas.BatchError,
            "annotate_mapping index=-1 left apply_batch as %s(%s) — no "
            "surface converts that, so the agent is handed a traceback "
            "where the error envelope promises an ERROR= line"
            % (type(escaped).__name__, escaped))
        said = "\n".join(escaped.errors)
        self.assertIn("annotate_mapping", said)
        self.assertIn("index", said)

    @unittest.expectedFailure
    def test_red_a_negative_remove_index_pops_the_newest_mapping(
            self) -> None:
        """`index: -1` removes a mapping nobody asked to remove.

        The same missing sign check as the red above (canvas.py:7491), at
        the pole where the list is NOT empty — so Python's own negative
        indexing makes `-1` a perfectly valid subscript and there is no
        crash for anything to notice. `reg["mappings"].pop(-1)`
        tombstones the NEWEST mapping, the batch commits, and the
        response reports the removal the agent asked for. The model has
        quietly lost the concept most recently attached, and every
        surface goes on claiming the op did what was requested.

        MAGNITUDE is which mappings survive — both, `alpha` and `beta`,
        in the order they arrived — and it is asserted FIRST because it
        is the damage. DIRECTION is that the op must be REFUSED rather
        than silently redirected onto a different mapping, asserted
        second because a fix that merely stopped popping without saying
        so would leave the agent believing a mapping was removed.

        The two reds are one defect class under two magnitudes and share
        `_store` for that reason; this one earns its own entry because a
        fix that only guards the empty list leaves this half live.
        """
        store, _ = self._store()
        self._with_mappings(store)
        escaped = self._send(store, {"action": "remove_mapping",
                                     "index": -1})
        self.assertEqual(
            [m["concept"] for m in store.registry["mappings"]],
            ["alpha", "beta"],
            "remove_mapping index=-1 named no mapping in particular and "
            "tombstoned the newest one")
        self.assertIsInstance(
            escaped, canvas.BatchError,
            "remove_mapping index=-1 was accepted (escaped=%r) — the "
            "response tells the agent the removal it asked for happened"
            % (escaped,))
        said = "\n".join(escaped.errors)
        self.assertIn("remove_mapping", said)
        self.assertIn("index", said)

    @unittest.expectedFailure
    def test_red_a_rejected_create_survives_and_blocks_the_retry(
            self) -> None:
        """A rejected batch's `create` stays, and nothing can dislodge it.

        `_write_artifact` sets `self.scenes[aid]` and writes the
        `.excalidraw` file (canvas.py:7106) from inside the commit
        window, so by the time the trailing `set_round` rejects the batch
        the artifact is already on disk. The e2f3bf0 restore guard puts
        every renamed artifact back by comparing against the pre-image —
        correctly, and `ghost` is not IN the pre-image, so it is skipped
        rather than removed. What is left is an EMPTY scene: the ops that
        would have drawn into it were rolled back, so the store holds an
        artifact with no elements and, as the review measured, no
        `artifact_meta` entry either.

        The consequence is not a stray file. The corrected batch — the
        same batch with the typo fixed — can now never be sent, because
        `_validate_batch`'s `elif aid in self.scenes` (canvas.py:7721)
        sees the phantom and answers "create: artifact 'ghost' already
        exists". A fresh load takes the file at face value and raises no
        repair, and neither error tells the agent that dropping the
        `create` block is the way out.

        The OUTCOME is pinned and never the mechanism: unlinking on an
        error path is a decision for the work package that owns the
        write, and a batch that wrote nothing until it was accepted would
        satisfy this equally. MAGNITUDE is what survives the rejection —
        no scene, no file. DIRECTION is which way the retry resolves —
        accepted, where today it is refused for a reason the agent cannot
        act on. The retry is measured with `check_batch` rather than a
        second `apply_batch`: `--check` is the surface an agent reaches
        for after a rejection, and it answers whether the batch WOULD
        land without committing a second artifact into the assertion.
        """
        store, root = self._store()
        with self.assertRaises(canvas.BatchError):
            store.apply_batch(self._create_batch(store, failing=True))
        retry = store.check_batch(self._create_batch(store, failing=False))
        self.assertEqual(
            sorted(store.scenes), ["flow"],
            "the rejected batch's create is still in the live store")
        self.assertFalse(
            self._ghost_file(root).exists(),
            "the rejected batch's create is still on disk at %s"
            % self._ghost_file(root))
        self.assertTrue(
            retry["ok"],
            "the corrected batch can now never be sent: %r"
            % (retry["errors"],))

    def test_an_index_past_the_end_is_rejected_with_a_named_error(
            self) -> None:
        """The live half of the bounds check both reds slip under.

        `idx >= len(...)` is the predicate at fault, and this is the
        direction where it works — so this is what says the reds measure
        a gap in a LIVE check rather than one that quietly stopped
        running. Asserted on the same empty mapping list the annotate red
        uses, where every index is out of range, so mutant and neighbour
        differ in the SIGN of one field and nothing else.

        It also fixes the message tokens both reds demand: they assert
        the rejection names its action and its field, and this is where
        that wording is pinned as already-shipped behaviour rather than
        as something a fix would have to invent.
        """
        store, _ = self._store()
        for action in ("annotate_mapping", "remove_mapping"):
            with self.subTest(action=action):
                escaped = self._send(store, {"action": action, "index": 0})
                self.assertIsInstance(escaped, canvas.BatchError,
                                      "index=0 on an empty mapping list "
                                      "was not refused: %r" % (escaped,))
                said = "\n".join(escaped.errors)
                self.assertIn(action, said)
                self.assertIn("index", said)

    def test_a_valid_index_annotates_exactly_that_mapping(self) -> None:
        """The annotate op's other pole: asked properly, it writes there.

        Without this, a fix that simply refused every `annotate_mapping`
        would flip the first red and read as a success while deleting the
        feature. Mapping 0 takes the note and mapping 1 must be left
        alone — the same "exactly one" claim the remove neighbour makes,
        which is what a fix that annotated the whole list would fail.
        """
        store, _ = self._store()
        self._with_mappings(store)
        escaped = self._send(store, {"action": "annotate_mapping",
                                     "index": 0, "note": "deliberate"})
        self.assertIsNone(escaped, "a valid annotate was refused: %r"
                          % (escaped,))
        self.assertEqual([m.get("note") for m in
                          store.registry["mappings"]],
                         ["deliberate", None])

    def test_a_valid_index_removes_exactly_that_mapping(self) -> None:
        """The remove op's live pole: index 0 takes `alpha`, `beta` stays.

        The neighbour the remove red is measured against, and the reason
        that red's magnitude is the SURVIVING list rather than the list
        LENGTH: a fix that removed the right count but the wrong element
        would satisfy a length check and fail this one.
        """
        store, _ = self._store()
        self._with_mappings(store)
        escaped = self._send(store, {"action": "remove_mapping",
                                     "index": 0})
        self.assertIsNone(escaped, "a valid remove was refused: %r"
                          % (escaped,))
        self.assertEqual([m["concept"] for m in store.registry["mappings"]],
                         ["beta"])

    def test_an_accepted_create_and_rename_persists_normally(self) -> None:
        """The create red's neighbour: the same batch, minus the typo.

        The pole that makes the red's demand safe to satisfy. "A rejected
        batch's create leaves nothing behind" is trivially met by never
        writing a create at all, or by unlinking one on every path, and
        this refuses both readings: accepted, the artifact is in the
        store, on disk, and carrying the name its rename gave it — the
        BUG-03 workflow of naming the id your own batch creates.
        """
        store, root = self._store()
        store.apply_batch(self._create_batch(store, failing=False))
        self.assertIn("ghost", store.scenes)
        self.assertTrue(self._ghost_file(root).exists())
        self.assertEqual(store.artifact_meta["ghost"]["name"],
                         "Renamed Ghost")


# ---------------------------------------------------------------------------
# Scene builders. Every coordinate here was measured against live canvas.py
# and instruments.py output, so the numbers are frozen: move a point and you
# move the finding the catalogue asserts. The diamond is 200x100 at
# (300,300), i.e. center (400,350) with half-axes a=100, b=50, so the
# rhombus boundary is |x-400|/100 + |y-350|/50 == 1 and the rhombus's
# HALF-WIDTH at a given y is 100*(1 - |y-350|/50). The horizontal gap from
# a point to the boundary at its own y is (400 - half_width) - x on the
# left side, i.e. derived from the half-width, not equal to it.
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


def _labelled_shape(shape: str) -> list[dict]:
    """One 200x100 node carrying a bound label right at the fitter's budget.

    `fit_label_in` allots a label `width - 24` whatever the container's
    shape (canvas.py:962), so at 200px wide the budget is 176px and this
    label's measured 171px clears it — the fitter returns early and never
    wraps, resizes or grows anything. On a RECTANGLE that is right: the
    bbox is the shape, and the label has 29px to spare. On a DIAMOND the
    same box overhangs the rhombus, because at the label's own height the
    rhombus is 160px across, not 200px.

    The two scenes differ in exactly one field — `type` — which is the
    whole argument: the fitter never reads it.

    Coordinates are frozen. The label is centred (14, 40) and sized to
    `text_dims("Send for second review", 16)` exactly, so drift in the
    advance table moves the finding this stage asserts.

    Args:
        shape: The container's element type — `"diamond"` for the mutant,
            `"rectangle"` for its control.

    Returns:
        The two-element scene: the node `d1`, then its bound label `t1`.
    """
    text = "Send for second review"
    node = el(id="d1", type=shape, x=0, y=0, width=200, height=100,
              customData={"role": "node"},
              boundElements=[{"id": "t1", "type": "text"}])
    lbl = el(id="t1", type="text", x=14, y=40, width=171, height=20,
             text=text, fontSize=16, fontFamily=1, textAlign="center",
             verticalAlign="middle", containerId="d1", originalText=text)
    return [node, lbl]


def _framed_flow(escaped: bool) -> list[dict]:
    """A lane frame and two members, one of them outside the lane.

    Minimal on purpose: an earlier draft kept the arrow joining the two
    members, and moving `s2` out of the lane dragged its bound endpoint
    with it, so the scene fired `endpoint_gap` and the containment
    question arrived wrapped in someone else's finding. Frame membership
    is a `frameId` claim about geometry; it needs no edges to be wrong.

    `s2` sits 80px below the lane's bottom edge when escaped — the frame
    spans y 0..200 and the node spans y 280..340 — and inside it at the
    same y as `s1`. Both scenes carry the identical `frameId` claim.

    Args:
        escaped: True to place `s2` clear of the lane it claims to be in.

    Returns:
        The three-element scene: frame `lane`, members `s1` and `s2`.
    """
    return [el(id="lane", type="frame", x=0, y=0, width=400, height=200,
               name="Lane A"),
            el(id="s1", type="rectangle", x=40, y=60, width=120, height=60,
               frameId="lane", customData={"role": "node"}),
            el(id="s2", type="rectangle", x=240, y=280 if escaped else 60,
               width=120, height=60, frameId="lane",
               customData={"role": "node"})]


def _text_over_node(roled: bool) -> list[dict]:
    """A free text lying across a node, with and without a role.

    `role_of` defaults to `"node"` for anything carrying no explicit role
    (canvas.py:3197), and the annotation/node overlap check gates on
    `role_of(e) == "annotation"` (canvas.py:5523). So the SAME text over
    the SAME node is reported when it is roled and invisible when it is
    not — and unroled is what arrives when a user pastes text onto the
    canvas, the least instrumented direction there is.

    The text lies wholly INSIDE the node — 120x20 of text on a 200x100
    box, so the overlap is the text's own area, 2400px², and it covers
    about 12% of the node. That orientation is what makes the rejected
    "fraction" reading 1.0 rather than 0.12: every pixel of the TEXT is
    over the node. A check reporting the node's covered fraction instead
    would be a different number and a different claim.

    Args:
        roled: True to mark the text `role="annotation"`, which is the
            control — the existing lint fires on it.

    Returns:
        The two-element scene: node `n1`, then the text `t1`.
    """
    return [el(id="n1", type="rectangle", x=0, y=0, width=200, height=100,
               customData={"role": "node"}),
            el(id="t1", type="text", x=40, y=40, width=120, height=20,
               text="pasted note", fontSize=16,
               customData={"role": "annotation"} if roled else {})]


def _near_miss_pair(gap: float) -> list[dict]:
    """Two nodes separated by `gap` px of clear space, never overlapping.

    The overlap loop needs a real intersection (`ox * oy > …`), so a
    positive gap is silent no matter how small — 4px reads exactly like
    60px to every check we own, while to a reader 4px is a mistake and
    60px is a layout.

    The gap is 4px rather than a tighter 3px because 3 puts `n2` at
    x=123 and trips `offgrid_elements`, which fires on the mutant scene
    and not the control. That note says nothing about clearance, but it
    IS a difference in the lint between the two scenes, and a mutant
    whose base and control differ in something other than the defect is
    the confound `_framed_flow` was minimized to avoid. 4px is the
    smallest gap the 4px grid can express at all, which sharpens the
    claim rather than weakening it: the least clearance the coordinate
    system can even represent is invisible to every check.

    Args:
        gap: Horizontal clear space between the two boxes.

    Returns:
        The two-element scene: nodes `n1` and `n2`.
    """
    return [el(id="n1", type="rectangle", x=0, y=0, width=120, height=60,
               customData={"role": "node"}),
            el(id="n2", type="rectangle", x=120 + gap, y=0, width=120,
               height=60, customData={"role": "node"})]


def _styled_scene(text_color: str = "#1e1e1e", stroke: str = "#1e1e1e",
                  font_size: int = 16) -> list[dict]:
    """A node and a free text on the ground, styled to order.

    One base for all three legibility mutants, since they differ only in
    which declared style is wrong: the text's color, the node's stroke,
    or the font size. Everything sits on `SVG_GROUND` (#fdfcf8) with no
    fills, so the effective background is unambiguous and the WCAG ratio
    is a pure function of the two declared colors.

    Args:
        text_color: The free text's `strokeColor` (its ink).
        stroke: The node's `strokeColor`.
        font_size: The free text's `fontSize`.

    Returns:
        The two-element scene: node `n1`, then free text `t1`.
    """
    return [el(id="n1", type="rectangle", x=0, y=0, width=200, height=100,
               strokeColor=stroke, backgroundColor="transparent",
               customData={"role": "node"}),
            el(id="t1", type="text", x=0, y=160, width=120, height=20,
               text="status", fontSize=font_size, strokeColor=text_color)]


def _composed_row(text: str) -> list[dict]:
    """An entity node carrying one composed attribute row.

    The SINGLE-LINE arm of `text_overflow` (canvas.py:5623): a text
    claimed by an owner through `customData.value_of` is a composed row —
    a KPI value, an entity attribute — which the renderer emits on one
    line and never wraps, so width alone decides whether it fits. The
    box is 120px wide and the check reserves 16px of padding for this
    arm, leaving `room_w = 104`.

    Owner and text are separate elements rather than a bound label
    precisely because that is what a composed row is; binding it would
    take the wrapped path and measure something else.

    Args:
        text: The row's content. Anything measuring over 104px at
            fontSize 16 overflows; `text_dims` is the arbiter.

    Returns:
        The two-element scene: node `n1`, then row `t1`.
    """
    return [el(id="n1", type="rectangle", x=0, y=0, width=120, height=60,
               customData={"role": "node"}),
            el(id="t1", type="text", x=8, y=20, width=100, height=20,
               text=text, fontSize=16, customData={"value_of": "n1"})]


def _boxed_label(height: int) -> list[dict]:
    """A 200px-wide node whose bound label wraps to three lines.

    The WRAPPED arm of `text_overflow`, which is the common one: a bound
    label is laid out by the renderer, so the check wraps it to
    `room_w = 192` and judges the resulting HEIGHT, calling it too wide
    only when a single word cannot fit. This label wraps to 172x60px
    whatever the box does, and its longest word is 67px, so the width
    arm stays quiet and `height` alone decides the verdict.

    Only the container's height moves between the two poles — the label,
    its text and the box's width are identical — so the difference in
    verdict is the room and nothing else.

    Args:
        height: The container's height. `room_h` is this minus 4, so
            anything under 64 cannot hold the wrapped 60px label.

    Returns:
        The two-element scene: node `n1`, then its bound label `t1`.
    """
    text = "reconcile every settled position nightly against custody"
    return [el(id="n1", type="rectangle", x=0, y=0, width=200,
               height=height, customData={"role": "node"},
               boundElements=[{"id": "t1", "type": "text"}]),
            el(id="t1", type="text", x=4, y=4, width=192, height=20,
               text=text, fontSize=16, textAlign="center",
               verticalAlign="middle", containerId="n1",
               originalText=text)]


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


def _attach_chain(shared: bool) -> list[dict]:
    """A -> N -> Z on one rank line, N's two edges sharing a foot or not.

    The production configuration from the 2026-08-12 ELK spike, rebuilt at
    the same scale: with `shared`, e1's absolute END and e2's absolute
    START are the SAME point — N's left-edge midpoint (200, 120) — so the
    two arrows draw as one unbroken 448px horizontal stroke from x=80 to
    x=528, straight through the box, and N reads as something the line
    passes rather than something it arrives at. That is what ELK drew for
    `authorise-payment -> auth-succeeded -> pick-and-pack`: same line, 0px
    apart, 448px, with one edge's label sitting on the other's stroke
    (ELK-RESULTS.md, "What the eyes caught" item 1).

    Without `shared`, e2 starts at N's RIGHT edge instead — one node width
    (80px) clear of e1's end, past both the lint's 12px coincidence window
    and the corridor instrument's 10px abut window. Two strokes with a box
    between them, nothing merged.

    Both arrows are straight, 2-point and unmarked, so
    `canvas.server_owns_geometry` calls them server-routed and the lint's
    "why" list stays empty — which is what makes the shared variant emit
    the production wording, "the auto-fan ran and left them together",
    rather than one of the disqualified-arrow phrasings.

    Args:
        shared: True to attach both arrows at N's left-edge midpoint;
            False to move e2's foot to N's right edge.

    Returns:
        The five-element scene: nodes A, N, Z and arrows e1, e2.
    """
    a = el(id="A", type="rectangle", x=0, y=100, width=80, height=40,
           customData={"role": "node"})
    n = el(id="N", type="rectangle", x=200, y=100, width=80, height=40,
           customData={"role": "node"})
    z = el(id="Z", type="rectangle", x=528, y=100, width=80, height=40,
           customData={"role": "node"})
    e1 = el(id="e1", type="arrow", x=80, y=120, width=120, height=0,
            points=[[0, 0], [120, 0]],
            startBinding={"elementId": "A", "focus": 0, "gap": 1},
            endBinding={"elementId": "N", "focus": 0, "gap": 1},
            customData={"role": "edge"})
    foot = 200 if shared else 280
    e2 = el(id="e2", type="arrow", x=foot, y=120, width=528 - foot,
            height=0, points=[[0, 0], [528 - foot, 0]],
            startBinding={"elementId": "N", "focus": 0, "gap": 1},
            endBinding={"elementId": "Z", "focus": 0, "gap": 1},
            customData={"role": "edge"})
    return [a, n, z, e1, e2]


def _ellipse_stage(through_body: bool) -> list[dict]:
    """One circular node and one unbound arrow, at the corner or through it.

    Deliberately two elements and no more. The diamond family's stage
    carries a source box, a label and a bound arrow because the endpoint
    mutants need them; nothing here does, and the smaller the scene the
    less there is to argue about when this fires.

    The node is a 200x200 ellipse at (300,250), i.e. the circle centred
    (400,350) with r=100 — a circle rather than an oval so every number
    below is a plain distance from one point. Its bounding box corners
    are 141.4px from that centre, so the corner void is 41.4px deep.

    With `through_body` False the arrow runs the line x+y=570, every point
    of which is 127.28px from the centre: **27.3px of clear white canvas**
    from the drawn outline along its whole length, verified by dense
    sampling. It clips the top-left corner of the (2px-inset) bounding box
    and touches nothing a reader can see. With `through_body` True the
    same arrow runs the horizontal diameter and genuinely crosses the
    circle — the check's legitimate firing.

    Args:
        through_body: True for the control, driving the arrow along
            y=350 through the circle; False for the mutant, clipping the
            empty bbox corner.

    Returns:
        The two-element scene: the ellipse `n1`, then the arrow `ax`.
    """
    node = el(id="n1", type="ellipse", x=300, y=250, width=200, height=200,
              customData={"role": "node"})
    if through_body:
        arrow = el(id="ax", type="arrow", x=280, y=350, width=240,
                   height=0, points=[[0, 0], [240, 0]],
                   customData={"role": "edge"})
    else:
        arrow = el(id="ax", type="arrow", x=280, y=290, width=60,
                   height=60, points=[[0, 0], [60, -60]],
                   customData={"role": "edge"})
    return [node, arrow]


def _label_pair_stage() -> list[dict]:
    """Two arrows whose labels sit 62px apart — and one `mod` from colliding.

    Built for the write-path invariant, so every number is chosen to make
    the collision hinge on the STORED width and nothing else. Both arrows
    are horizontal, so `arrow_label_anchor` centres each label on its
    own midpoint: `e1`'s is (200,200) and `e2`'s is (300,212). The 12px
    vertical offset leaves the two 20px-tall labels overlapping by 8px on
    y — past the check's 4px floor — so the x axis alone decides.

    On x, as built: `'ok'` is 20px wide and `'queued'` 56px, half-widths
    10 and 28 against a 100px centre distance, so the boxes miss by 62px
    and the check is right to stay quiet. Retext `e1` to "settled and
    reconciled nightly" (216px) and the half-widths become 108 and 28, so
    they overlap by 36px — past the 6px floor — and it must speak.

    `e1` spans 400px on purpose: its run has to stay wider than the grown
    label, or `label_wider_than_run` fires and the scene starts asserting
    two things at once.

    Returns:
        The four-element scene: arrows `e1`, `e2` and their bound labels
        `t1`, `t2`. Stored widths are the true `text_dims` of the short
        texts, which is the state a healthy write path leaves behind.
    """
    e1 = el(id="e1", type="arrow", x=0, y=200, width=400, height=0,
            points=[[0, 0], [400, 0]], customData={"role": "edge"},
            boundElements=[{"id": "t1", "type": "text"}])
    e2 = el(id="e2", type="arrow", x=250, y=212, width=100, height=0,
            points=[[0, 0], [100, 0]], customData={"role": "edge"},
            boundElements=[{"id": "t2", "type": "text"}])
    t1 = el(id="t1", type="text", x=190, y=190, width=20, height=20,
            text="ok", originalText="ok", fontSize=16, fontFamily=1,
            textAlign="center", verticalAlign="middle", containerId="e1")
    t2 = el(id="t2", type="text", x=272, y=202, width=56, height=20,
            text="queued", originalText="queued", fontSize=16,
            fontFamily=1, textAlign="center", verticalAlign="middle",
            containerId="e2")
    return [e1, e2, t1, t2]


# ---------------------------------------------------------------------------
# The day-one catalogue. Each entry pairs a scene the drawing gets WRONG
# today with a neighbour that must read right today; the mutant tests below
# are `expectedFailure` exactly where the defect is still live, so WP4's fix
# announces itself as an unexpected success rather than a silent pass.
# ---------------------------------------------------------------------------

# Not every red in this file is a catalogue entry, and the gap is not small:
# as of 2026-08-13 `mutants list --red` reports 16 while the suite reports 26
# expected failures. The ten outside live in six classes —
# `TestBatchPathIntegrity` (3), `TestExportCompleteness` (2),
# `TestShapeBlindAnnotationOverlap` (2), `TestStoreIntegrity` (1),
# `TestPaintOrder` (1) and `TestLoadFindingsReachTheAgent` (1) — and are
# outside
# deliberately, because a Mutant is judged by `collect_findings` over an
# ELEMENT LIST and none of what they measure is in one. Each class carries
# its own standing guard for its reds; the one below covers CATALOGUE alone.
# These counts are a hand enumeration and drift silently, so re-measure them
# rather than trusting them: on 2026-08-12 this read "(5)" for
# `TestStoreIntegrity`, and four of those five had since been flipped green by
# WP1 fixes with the comment left behind.
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

# ---------------------------------------------------------------------------
# The ELK spike's three (2026-08-12). All three read the SAME base scene,
# `_attach_chain(shared=True)`, because all three conditions genuinely hold
# on it at once — that is the point: one production configuration, one
# picture, three different answers from the tooling.
# ---------------------------------------------------------------------------

# Phantom pass-through — RED BY ABSENCE. e1's end and e2's start are one
# point on N, so the pair draws as a single unbroken stroke THROUGH the box:
# a reader sees A -> Z and a decoration in the middle. e1 is the
# highest-hit-rate class from the Aug 2026 scan, and the ELK spike produced
# it in production. There is deliberately NO `phantom_passthrough` entry in
# DETECTORS — the table lists detectors that exist, not ones we want — so
# this mutant fails with "no finding of check='phantom_passthrough'" until
# WP4b item 1's lint lands; flip it by adding that DETECTORS entry and
# dropping the expectedFailure. It fulfils the "promote" disposition on
# sweep survivor `move_node_onto_rank:chain:ebb2e1f6`, which found this same
# configuration by accident and could only record it.
# The neighbour cannot be the usual opposite pole (a Silence on a check with
# no detector passes vacuously and proves nothing), so it asserts instead
# what makes the control a control: with the feet 80px apart the picture is
# genuinely two strokes, and `shared_corridor` — the one net that does exist
# — says so by staying quiet.
_register(Mutant(
    "phantom_passthrough_shared_attach",
    build=lambda: _attach_chain(shared=True),
    op="unchanged", args={},
    expect=FindingSpec("phantom_passthrough", element="N"),
    neighbour=Neighbour(lambda: _attach_chain(shared=False),
                        Silence("shared_corridor"))))

# The same merged stroke, caught by the net that exists today. No defect
# here: `instruments.shared_corridors`' abutting-run case (overlap 0, both
# runs >= 60px) reads two contiguous collinear strokes as one, and the spike
# confirmed it on the real ELK output. The division of labour is worth
# recording — corridor is the current net for FULL-run merges, while
# `falsebidi` scored the production 448px case 0 because its test only ever
# looks at each arrow's final segment (ELK-RESULTS.md: "structurally
# blind"). So this guards the half we have while the mutant above waits for
# the half we do not.
_register(Mutant(
    "merged_stroke_caught_by_corridor",
    build=lambda: _attach_chain(shared=True),
    op="unchanged", args={},
    expect=FindingSpec("shared_corridor", element="e1+e2"),
    neighbour=Neighbour(lambda: _attach_chain(shared=False),
                        Silence("shared_corridor"))))

# The shared-attach lint, proven — the first organic UNCOVERED drain. Its
# firing conditions (canvas.py:5646-5694): two arrows bound to the same node
# at either end, neither a self-loop, whose attach points on that node sit
# within 12px on both axes. Here they are the same point, and both arrows
# are server-routed 2-pointers, so nothing disqualifies them and the message
# is the ELK arm's own — "the auto-fan ran and left them together" (3 hits
# there, 0 on the dagre arm). No defect: this drains `shared_attach_point`
# from UNCOVERED.
_register(Mutant(
    "shared_attach_point_fan_failed",
    build=lambda: _attach_chain(shared=True),
    op="unchanged", args={},
    expect=FindingSpec("shared_attach_point", element="N"),
    neighbour=Neighbour(lambda: _attach_chain(shared=False),
                        Silence("shared_attach_point"))))

# ---------------------------------------------------------------------------
# Shape-blindness, instance THREE — RED BY ABSENCE (found via the flowchartai
# idea-mine, 2026-08-12: docs/research/flowchartai_idea_mining_2026-08-12.md
# OP1). The endpoint lint measured to the bbox; the through-node test used the
# bbox; and `fit_label_in` budgets every container `width - 24` alike
# (canvas.py:962). canvas.py already knows better IN THE SAME FILE:
# `marker_inset` (canvas.py:4200) returns 0.5 for a diamond and 1-1/sqrt(2)
# for an ellipse precisely because "the box's corner is empty canvas for
# them" — and nothing on the label path ever asks it. Prior art for the fix:
# flowchartai's diamond safeAreaRatio.
#
# MAGNITUDE CONVENTION (binds the future lint, as the approach axis binds
# WP4's endpoint work): the overflow is measured at the LABEL BOX's own
# height, not at the shape's widest point. A 200x100 rhombus is 200px across
# at its centre line but 160px across at the top and bottom edges of a 20px
# label, so the 171px label overhangs by 11px. The ±30% band admits that and
# excludes the two plausible wrong readings deliberately: 0px (measuring at
# the centre line, where everything fits) and 5.5px (half the total, the
# shape a per-side report would take). A lint reporting either is wrong by
# this spec and will not flip this mutant. The true split is 6px left and
# 5px right, not 5.5 each: at x=14 the label sits half a pixel left of the
# node's centre line, since exact centring would want 14.5.
#
# The ellipse is deliberately NOT a second mutant, and the thresholds differ
# by which box you mean — state both, because quoting one at the other is
# how this gets miscited. For THIS label (171x20) a 200-wide ellipse gives a
# chord of 195.96px at 200x100 and 188.56px at 200x60, so it clears by 25px
# and 17.6px respectively; overflow begins only below 200x38.6. For the
# WRAPPED 176x40 box the fitter produces from a longer label, the same
# ellipse overflows below 200x84.2. Either way the ellipse is the weaker
# case. The area ratio usually quoted for ellipses governs none of this —
# the chord at the label box's own height does.
_register(Mutant(
    "diamond_label_overflows_shape",
    build=lambda: _labelled_shape("diamond"),
    op="unchanged", args={},
    expect=FindingSpec("label_overflows_shape", element="t1",
                       magnitude=(11, 0.30)),
    # Same control shape as the other shape-blindness mutants: the box
    # whose bbox IS its shape. It cannot assert this check's other pole
    # (a Silence on a check with no detector passes vacuously — see
    # `phantom_passthrough_shared_attach`), so it asserts instead that the
    # pipeline runs clean over the control, which `Silence` alone can say:
    # it refuses to match over any run where a detector crashed.
    neighbour=Neighbour(lambda: _labelled_shape("rectangle"),
                        Silence("endpoint_gap"))))

# Frame membership asserted against a picture that contradicts it — RED BY
# ABSENCE (flowchartai mine M2, 2026-08-12). `frameId` is a claim of
# containment, and nothing in `lint_layout` ever tests it. Three of the
# frameId sites there serve help-slot lookup, same-frame pairing and the
# unconnected-node note; a fourth, the dot-row/progress tell
# (canvas.py:5389-5405), IS geometric — it groups a frame's small members and
# compares their y against `fr.y + height * 0.25` — but it is hunting for a
# row of dots, never asking whether a member lies inside the frame at all. No
# site tests containment. Verified live — this scene and its control
# produce IDENTICAL lint and findings, so the tooling cannot tell a lane
# with its members in it from a lane whose member is 80px below it.
#
# The producer is confirmed too: `--relayout`'s move set is
# rect/diamond/ellipse (canvas.py:10581) and never frames, so re-laying a
# framed flow walks members out of a lane that stays put. That half is a
# WP5 defect and is NOT pinned here — pinning it means stubbing
# `_mermaid_convert` and `cmd_apply` around a CLI command, which binds the
# test to three internal names to prove a state this scene already holds.
#
# MAGNITUDE: the escape is measured from the frame's near edge to the
# member's near edge — 280 - 200 = 80px past the bottom. The ±25% band
# excludes 140px (measuring to the member's FAR edge) and 0px (a bbox
# overlap test, which would call this "not overlapping" and report nothing).
_register(Mutant(
    "framed_node_escapes_its_lane",
    build=lambda: _framed_flow(escaped=True),
    op="unchanged", args={},
    expect=FindingSpec("frame_containment", element="s2",
                       magnitude=(80, 0.25)),
    neighbour=Neighbour(lambda: _framed_flow(escaped=False),
                        Silence("endpoint_gap"))))

# The role gate on text checks — RED BY ABSENCE (visualize-skill mine M2,
# 2026-08-12). `role_of` defaults everything unroled to "node"
# (canvas.py:3197) and the text/node overlap check gates on
# role_of(e) == "annotation" (canvas.py:5523), so the same text over the
# same node is reported when roled and silent when not. Verified live: with
# `role="annotation"` the lint says "annotation 'pasted note' lies on top of
# n1"; with no role at all it says nothing. Unroled is what a user's pasted
# text looks like, which makes the blind spot point at the one direction we
# instrument least.
#
# MAGNITUDE: the overlap AREA, 120 x 20 = 2400px², matching how the shape
# overlap loop next door already scores overlaps (`ox * oy`). The ±10% band
# excludes both single-axis readings (120 and 20) and any fraction-of-text
# reading (1.0).
#
# FLIP CONSTRAINT: giving this mutant a real other-pole neighbour is blocked
# on something outside it — the check its control exercises,
# `annotation_overlaps_node` (canvas.py:5592), has no DETECTORS entry and
# sits in UNCOVERED. Until that lands there is no registered check to assert
# the roled overlap firing, which is why the neighbour asserts liveness
# instead of the pole it is actually demonstrating.
_register(Mutant(
    "unroled_text_over_node",
    build=lambda: _text_over_node(roled=False),
    op="unchanged", args={},
    expect=FindingSpec("text_overlaps_node", element="t1",
                       magnitude=(2400, 0.10)),
    neighbour=Neighbour(lambda: _text_over_node(roled=True),
                        Silence("endpoint_gap"))))

# Near-miss spacing — RED BY ABSENCE (vskill mine M1, 2026-08-12). The
# overlap loop needs a real intersection, so 4px of clear space reads
# exactly like 60px to every check we own — verified across all three lint
# channels, errors, warnings AND notes. To a reader they are opposites: 4px
# is a mistake, 60px is a layout.
#
# DESIGN CONSTRAINT for whoever builds the check, learned the hard way from
# the Cloud contradiction: a bare distance threshold WILL eventually gate a
# correct drawing — deliberate near-touching is a real composition (a badge
# on a card, a shelf and the card inside it). The lint needs an intent
# channel first (customData.parent already carries nesting; a decoration
# role and an explicit waiver are the other two), or it will be muted
# wholesale the first week and never heard from again.
#
# MAGNITUDE: the clear gap itself, 4px. The ±33% band excludes 0 (an
# overlap-area reading, which is what today's loop would report) and 124
# (edge to far edge).
_register(Mutant(
    "near_miss_clearance",
    build=lambda: _near_miss_pair(gap=4),
    op="unchanged", args={},
    expect=FindingSpec("min_clearance", element="n2", magnitude=(4, 0.33)),
    neighbour=Neighbour(lambda: _near_miss_pair(gap=60),
                        Silence("endpoint_gap"))))

# Legibility — three RED BY ABSENCE mutants over one base scene
# (docs/todo/contrast-and-min-font-lints.md, user-directed 2026-08-12;
# independently corroborated by the excalidraw-mcp mine O4/M5). Ops allow
# free-form colors, so nothing stops an agent writing near-invisible ink on
# the near-white ground. Legibility is enforced NOWHERE today: not in lint,
# not on the render tier.
#
# MAGNITUDE CONVENTION: the finding carries the MEASURED WCAG ratio, not a
# pass/fail. Ratios below were computed from the todo's own formula —
# relative luminance over linearized sRGB, `(L1 + 0.05) / (L2 + 0.05)` —
# against SVG_GROUND #fdfcf8. A check that reports a boolean, or the
# inverse ratio, will not flip these.
_register(Mutant(
    "gray_text_on_ground",
    build=lambda: _styled_scene(text_color="#d0d0d0"),
    op="unchanged", args={},
    expect=FindingSpec("contrast_text", element="t1",
                       magnitude=(1.50, 0.05)),
    neighbour=Neighbour(_styled_scene, Silence("endpoint_gap"))))

# WCAG 1.4.11, the criterion people forget: non-text objects need 3:1, and
# a pale stroke on cream paper is the case it exists for. #b0b0b0 scores
# 2.11:1 — present in the model, practically invisible in the picture.
_register(Mutant(
    "pale_stroke_node",
    build=lambda: _styled_scene(stroke="#b0b0b0"),
    op="unchanged", args={},
    expect=FindingSpec("contrast_object", element="n1",
                       magnitude=(2.11, 0.05)),
    neighbour=Neighbour(_styled_scene, Silence("endpoint_gap"))))

# The font floor. 6px is legible in a zoomed editor and gone in a
# fit-to-window snapshot — which is the only view the agent ever gets.
# MAGNITUDE is the offending fontSize itself, and it stays that way: the
# convention across this family is that the finding reports what it MEASURED
# and names the threshold in its message, exactly as `contrast_text` carries
# 1.50 and names 4.5. A floor is a threshold, so it does not belong here.
#
# FLOOR, MEASURED 2026-08-13 (Batch D item 3): 7px, against the render tier at
# deviceScaleFactor 1 — `test_mutants_render.TestLegibilityFloor` and the
# sweep in that section's header. This scene is unchanged by the calibration,
# and that is the result: 6px sits one below the measured floor, so what was a
# plausible-looking choice is now the boundary-honest case the design doc asks
# every legibility lint to carry. The evidence is that between 7px and 6px the
# rendered word loses nearly half its stroke contrast (8.50:1 to 4.62:1, the
# sharpest step in the sweep) and drops from 5px to 3px of ink height, while
# its DECLARED contrast stays 16.24:1 throughout — so a lint reading declared
# colors alone waves through text the picture cannot deliver.
_register(Mutant(
    "tiny_font_text",
    build=lambda: _styled_scene(font_size=6),
    op="unchanged", args={},
    expect=FindingSpec("min_font", element="t1", magnitude=(6, 0.01)),
    neighbour=Neighbour(_styled_scene, Silence("endpoint_gap"))))

# ---------------------------------------------------------------------------
# Shape-blindness, the ELLIPSE (found during the visualize-skill idea-mine,
# 2026-08-12 — docs/research/visualize-skill_idea_mining_2026-08-12.md §(i.3),
# where their renderer's checks were turned back on ours). Same uncalled
# helper as the diamond cases: `marker_inset` (canvas.py:4200) already returns
# 1-1/sqrt(2) for an ellipse, on the stated grounds that "the box's corner is
# empty canvas for them", and `_seg_hits_rect` (canvas.py:4839) never asks it
# — it rejects against the bounding box and nothing else. 4 of 4 corner probes
# false-positived.
#
# This is a SEPARATE entry rather than a rider on the diamond ones because
# WP4's shape-blindness scope IS its mutants, and every one of them was a
# diamond. A WP4 that taught the through-node test about rhombus edges and
# stopped could go green with ellipses exactly as blind as they are today.
# (Not to be confused with the note on `diamond_label_overflows_shape`, which
# says the ellipse is the WEAKER case — that is about label chords inside a
# shape, a different check with different thresholds. On through-node testing
# the ellipse is not weaker at all.)
#
# No magnitude to assert: `passes_through_foreign` reports the fact and no
# number (canvas.py:5474), so what this pins is the POLE — silence — and the
# neighbour carries the firing proof. The corner arrow's 27.3px of clear
# canvas is derived in `_ellipse_stage`. Flips when WP4 tests the drawn shape.
_register(Mutant(
    "ellipse_corner_overfire",
    build=lambda: _ellipse_stage(through_body=False),
    op="unchanged", args={},
    expect=Silence("passes_through_foreign"),
    neighbour=Neighbour(lambda: _ellipse_stage(through_body=True),
                        FindingSpec("passes_through_foreign",
                                    element="ax"))))

# ---------------------------------------------------------------------------
# The label-collision checks trust STORED width — latent, so this is a GREEN
# regression pin and not an indictment (found during the visualize-skill
# idea-mine, 2026-08-12 — §(i.4)). It is the odd one out here: every other
# entry says "the drawing is wrong today". This one says "the drawing is
# right today, for a reason nothing was guarding".
#
# `drawn_box` (canvas.py:5546) measures a label by its stored `width`. That
# is safe only while every write path recomputes it, and three things say the
# dependency is undefended: the load-time repairs that refit labels (ART-011)
# and re-glue detached ones (ART-007) both explicitly skip arrow-type
# containers, and arrow labels are exactly what these checks measure;
# `render_svg` DISTRUSTS the same number, bounding text by "the larger of
# stored and estimated extents" because "stored text extents are estimates"
# (v0.3) — the renderer and the lint disagree about whether it can be
# believed; and no test asserted it either way.
#
# Hence `relabel`, the one operator that sends a real `mod` through
# `apply_ops` rather than forging the widened label. Green today because the
# write path recomputes 20px -> 216px and the boxes overlap by 36px. The day
# a write path stops recomputing, the stored width stays 20px, the drawn
# boxes miss by 62px, the warning vanishes and this goes RED — which is
# exactly why it is worth writing while it passes.
#
# It also drains `label_label_overlap` from UNCOVERED. The element is the
# quoted pair because that message names no third party and carries no
# magnitude (see `_LABEL_OVERLAP_RE`); tighten this spec to a magnitude the
# day O2 puts the overlap depth into the template.
# ---------------------------------------------------------------------------
# The clipped-text lint, PROVEN — both arms, the second organic drain off the
# enumerated ledger (Batch D follow-up, 2026-08-13; review item 1). Detector
# coverage on `text_overflow` (canvas.py:5637) was zero: the check has shipped
# since v0.4, has been enumerated as unproven since 2026-08-12, and nothing
# had ever asserted that it fires, on either arm, with any number.
#
# Two mutants rather than one because the check has TWO code paths, not two
# messages. `single_line` (canvas.py:5623) is true only for composed rows
# (`value_of` / `attr_of`), which measure raw `text_dims` against a 16px pad;
# everything else — bound labels, sticky notes, fixed-width text, the common
# case — is wrapped to `room_w` first and judged on the resulting height with
# an 8px pad. A pair that exercised only the first would leave the branch that
# governs most of the drawings we make unproven, which is the exact difference
# between line coverage and detector coverage this harness exists to keep.
#
# Both are GREEN. Nothing is broken here: the check fires, and it fires
# correctly. What was missing was the proof, and a check nobody has ever
# watched fire is indistinguishable from one that cannot.
#
# THE SHAPE ARM IS ABSENT, AND IT IS NOT A NEW DEFECT — determination for the
# review's second half, evidence first. `room_w`/`room_h` come from the
# owner's bbox on every container type (canvas.py:5626-5627), so a diamond or
# ellipse is credited with room its drawn body does not have. Measured on the
# scene the catalogue ALREADY pins for this: `_labelled_shape("diamond")` is a
# 200x100 rhombus carrying a 171px label at y=40..60, where the rhombus is
# 160px across — an 11px overhang — and `text_overflow` is SILENT on it, as it
# is on the rectangle control. A shape-aware `room_w` would therefore report
# exactly 11px there, which is `diamond_label_overflows_shape`'s pinned
# magnitude (11, ±30%) to the pixel.
#
# So this is not shape-blindness instance SIX. It is the same defect
# `label_overflows_shape` already pins, seen from the checker side rather than
# the producer side (`fit_label_in`, canvas.py:962), and what it adds is the
# NAME OF THE SHIPPED CHECK that should host the fix. Consequence, flagged in
# V0.9-PLAN WP4 and repeated here because nothing automated enforces it: if
# WP4 puts the shape term inside `text_overflow`, then `label_overflows_shape`
# stops being a check that needs building and becomes an arm of this one — and
# the `diamond_label_overflows_shape` entry must be RE-KEYED to `text_overflow`
# deliberately, not left pointing at a check that will never exist. Two halves
# of that re-key move in opposite directions, so neither is automatic. Its
# magnitude convention (measured at the LABEL BOX's own height, not the
# shape's widest point) is already the right one for that arm and should
# survive the move unchanged — but its `element` must NOT: that spec names
# the label (`t1`), while `_TEXT_OVERFLOW_RE` names the OWNER, because the
# text is quoted as content and carries no id. A re-key that moves the check
# and leaves the element pointing at the label asserts a finding this
# template cannot emit, and would go red for a reason that has nothing to do
# with the shape term it was meant to record.
_register(Mutant(
    "composed_row_overflows_its_box",
    build=lambda: _composed_row("net revenue total"),
    op="unchanged", args={},
    # MAGNITUDE: the needed width, 126px. The ±10% band excludes every
    # other number this message prints — 104 (room_w), 56 (room_h) and 20
    # (the needed height) — so a check reporting the allowance instead of
    # the need, or transposing the axes, fails this spec.
    expect=FindingSpec("text_overflow", element="n1",
                       magnitude=(126, 0.10), direction="wide"),
    neighbour=Neighbour(lambda: _composed_row("revenue"),
                        Silence("text_overflow"))))

# The wrapped arm, and the one that reaches most drawings. Same check, other
# code path, other axis: the label wraps to 172x60 and the box gives 192x56,
# so the height fails while the width passes. The neighbour moves ONLY the
# container's height, 60 -> 120, which is the cleanest control this check
# affords — same label, same text, same width, opposite verdict.
#
# Honest note on both neighbours: they are `Silence("text_overflow")`, which
# is this check's real other pole, but neither scene is silent on EVERY
# check — both emit an `unconnected node(s)` note, since a lone node is by
# construction unconnected. That note belongs to a different check and cannot
# color a `text_overflow` spec, but the C-wave review earned this sentence
# the hard way (a scene claimed "identical to every check" was firing
# `offgrid_elements`), so it is written down rather than assumed.
_register(Mutant(
    "wrapped_label_overflows_its_box",
    build=lambda: _boxed_label(height=60),
    op="unchanged", args={},
    # MAGNITUDE: 172px, the wrapped text's width. On this arm that is NOT
    # the failing axis — see the note on `_TEXT_OVERFLOW_RE` — and the
    # direction carries the axis instead. The ±10% band still excludes
    # 192 (room_w), 60 (the needed height) and 56 (room_h).
    expect=FindingSpec("text_overflow", element="n1",
                       magnitude=(172, 0.10), direction="tall"),
    neighbour=Neighbour(lambda: _boxed_label(height=120),
                        Silence("text_overflow"))))

_register(Mutant(
    "stale_label_width_hides_collision",
    build=_label_pair_stage,
    op="relabel", args={"container_id": "e1",
                        "text": "settled and reconciled nightly"},
    expect=FindingSpec("label_label_overlap",
                       element="'settled and reconciled n' and 'queued'"),
    neighbour=Neighbour(_label_pair_stage,
                        Silence("label_label_overlap"))))


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

        Every mutant gets its own neighbour method even where several
        mutants share one control scene and one expectation, so some of
        these read as duplicates. That is the convention, not an
        oversight: a neighbour is part of a single mutant's RECORD, and
        collapsing the repeats would mean a mutant whose control quietly
        stopped being the right pole — or one whose control was deleted
        with the mutant it was written for — could take its neighbours
        with it, or keep passing on another mutant's evidence. One
        mutant, one control, one line in the run.

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
        # Same control and same expectation as the other two diamond
        # mutants, deliberately — see `_run_neighbour`.
        self._run_neighbour("diamond_corner_silence")

    @unittest.expectedFailure
    def test_mutant_diamond_wrong_direction(self) -> None:
        """50px outside the rhombus, reported as 15px inside the shape."""
        # Shape-blind endpoint lint; flips when WP4 clips to the shape.
        self._run("diamond_wrong_direction")

    def test_neighbour_diamond_wrong_direction(self) -> None:
        """Fanned rectangle attachments stay endpoint-silent."""
        # Same control and same expectation as the other two diamond
        # mutants, deliberately — see `_run_neighbour`.
        self._run_neighbour("diamond_wrong_direction")

    @unittest.expectedFailure
    def test_mutant_diamond_label_overflows_shape(self) -> None:
        """A label inside its w-24 budget overhangs the rhombus by 11px."""
        # Shape-blind label fitter; flips when WP4's shape-aware label
        # check lands and takes a DETECTORS entry.
        self._run("diamond_label_overflows_shape")

    def test_neighbour_diamond_label_overflows_shape(self) -> None:
        """The same label on a rectangle: today's nets are right to be quiet."""
        self._run_neighbour("diamond_label_overflows_shape")

    @unittest.expectedFailure
    def test_mutant_framed_node_escapes_its_lane(self) -> None:
        """A member 80px below the lane its frameId claims goes unreported."""
        # Nothing tests frameId against geometry; flips when WP5's
        # containment check lands and takes a DETECTORS entry.
        self._run("framed_node_escapes_its_lane")

    def test_neighbour_framed_node_escapes_its_lane(self) -> None:
        """The same members inside the lane: nothing to report, nothing said."""
        self._run_neighbour("framed_node_escapes_its_lane")

    @unittest.expectedFailure
    def test_mutant_unroled_text_over_node(self) -> None:
        """A text with no role covering a node is invisible to every check."""
        # The overlap check gates on role == "annotation"; flips when a
        # role-blind text/node check lands and takes a DETECTORS entry.
        self._run("unroled_text_over_node")

    def test_neighbour_unroled_text_over_node(self) -> None:
        """The same overlap with a role attached is reported by today's lint."""
        self._run_neighbour("unroled_text_over_node")

    @unittest.expectedFailure
    def test_mutant_near_miss_clearance(self) -> None:
        """Two nodes 4px apart read exactly like two nodes 60px apart."""
        # No near-miss check exists; flips when WP4's lands with the
        # intent channel the catalogue entry describes.
        self._run("near_miss_clearance")

    def test_neighbour_near_miss_clearance(self) -> None:
        """A generous gap is silent, and correctly so."""
        self._run_neighbour("near_miss_clearance")

    @unittest.expectedFailure
    def test_mutant_gray_text_on_ground(self) -> None:
        """#d0d0d0 on #fdfcf8 is 1.50:1 where WCAG 1.4.3 wants 4.5:1."""
        # Nothing checks contrast; flips when the todo's lint lands.
        self._run("gray_text_on_ground")

    def test_neighbour_gray_text_on_ground(self) -> None:
        """The default ink scores 16.24:1 and has nothing to answer for."""
        self._run_neighbour("gray_text_on_ground")

    @unittest.expectedFailure
    def test_mutant_pale_stroke_node(self) -> None:
        """#b0b0b0 stroke is 2.11:1 where WCAG 1.4.11 wants 3:1."""
        # Nothing checks non-text contrast; flips when the todo's lint lands.
        self._run("pale_stroke_node")

    def test_neighbour_pale_stroke_node(self) -> None:
        """A default stroke on the ground is legible and stays unremarked."""
        self._run_neighbour("pale_stroke_node")

    @unittest.expectedFailure
    def test_mutant_tiny_font_text(self) -> None:
        """A 6px label survives the model and not the snapshot."""
        # No font floor exists; flips when the todo's lint lands.
        self._run("tiny_font_text")

    def test_neighbour_tiny_font_text(self) -> None:
        """16px is the ordinary size and draws no finding."""
        self._run_neighbour("tiny_font_text")

    @unittest.expectedFailure
    def test_mutant_diamond_facet_overfire(self) -> None:
        """A perfect facet-midpoint attachment is called 25px inside."""
        # Shape-blind endpoint lint; flips when WP4 clips to the shape.
        self._run("diamond_facet_overfire")

    def test_neighbour_diamond_facet_overfire(self) -> None:
        """Fanned rectangle attachments stay endpoint-silent."""
        # Same control and same expectation as the other two diamond
        # mutants, deliberately — see `_run_neighbour`.
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

    @unittest.expectedFailure
    def test_mutant_phantom_passthrough_shared_attach(self) -> None:
        """One 448px stroke through N, and no check names the pass-through."""
        # No `phantom_passthrough` detector exists; flips when WP4b item 1's
        # lint lands and earns its DETECTORS entry.
        self._run("phantom_passthrough_shared_attach")

    def test_neighbour_phantom_passthrough_shared_attach(self) -> None:
        """Feet a node width apart draw two strokes, not one corridor."""
        # Same control and same expectation as the other _attach_chain
        # corridor mutant, deliberately — see `_run_neighbour`.
        self._run_neighbour("phantom_passthrough_shared_attach")

    def test_mutant_merged_stroke_caught_by_corridor(self) -> None:
        """The abutting collinear runs read as one shared corridor."""
        # No defect here — the corridor instrument is the net that does
        # catch a full-run merge, and this holds it to that.
        self._run("merged_stroke_caught_by_corridor")

    def test_neighbour_merged_stroke_caught_by_corridor(self) -> None:
        """Feet a node width apart leave no corridor to share."""
        # Same control and same expectation as the other _attach_chain
        # corridor mutant, deliberately — see `_run_neighbour`.
        self._run_neighbour("merged_stroke_caught_by_corridor")

    def test_mutant_shared_attach_point_fan_failed(self) -> None:
        """Two edges on one attach point: the lint names N and says why."""
        # No defect here — this proves the detector the ELK arm fired.
        self._run("shared_attach_point_fan_failed")

    def test_neighbour_shared_attach_point_fan_failed(self) -> None:
        """Attach points 80px apart are past the lint's 12px window."""
        self._run_neighbour("shared_attach_point_fan_failed")

    @unittest.expectedFailure
    def test_mutant_ellipse_corner_overfire(self) -> None:
        """An arrow 27px clear of the circle reads as passing through it."""
        # Bbox-shaped through-node test, ellipse instance; flips when WP4
        # tests the drawn shape — for every shape, not only the rhombus.
        self._run("ellipse_corner_overfire")

    def test_neighbour_ellipse_corner_overfire(self) -> None:
        """An arrow along the diameter really does pass through the circle."""
        self._run_neighbour("ellipse_corner_overfire")

    def test_mutant_stale_label_width_hides_collision(self) -> None:
        """A real `mod` grows the label, and the collision check sees it."""
        # GREEN, and a pin rather than a report: the collision checks read
        # stored width, so this stays green only while the write paths keep
        # recomputing it. If it ever goes red, the defect is upstream in
        # whatever wrote the label — not in this mutant.
        self._run("stale_label_width_hides_collision")

    def test_neighbour_stale_label_width_hides_collision(self) -> None:
        """Labels 62px apart are two captions, and the check says nothing."""
        self._run_neighbour("stale_label_width_hides_collision")

    def test_mutant_composed_row_overflows_its_box(self) -> None:
        """A 126px attribute row in a box that affords 104px, called wide."""
        # Green: the check fires and is right to. This is the proof it
        # fires, which nothing had ever asserted.
        self._run("composed_row_overflows_its_box")

    def test_neighbour_composed_row_overflows_its_box(self) -> None:
        """A 60px row in the same box fits, and the check says nothing."""
        self._run_neighbour("composed_row_overflows_its_box")

    def test_mutant_wrapped_label_overflows_its_box(self) -> None:
        """A label wrapping to 60px tall in a box that affords 56px."""
        # The other code path: wrapped, judged on height, width quiet.
        self._run("wrapped_label_overflows_its_box")

    def test_neighbour_wrapped_label_overflows_its_box(self) -> None:
        """The same label in a 120px box has room, and the check is quiet."""
        self._run_neighbour("wrapped_label_overflows_its_box")

    def test_red_mutants_are_red_by_mismatch_not_by_error(self) -> None:
        """Every expectedFailure above is red for the reason it claims.

        `@unittest.expectedFailure` swallows ERRORS as well as failures,
        so a red mutant whose scene builder crashes, or whose operator
        starts raising, reads as a healthy `x` in the dots — the defect
        it was pinning could have been fixed, or broken further, and the
        run would look identical. This re-runs each red mutant at engine
        level and insists the redness is a spec MISMATCH: a crash is a
        broken mutant, not a defect pin.

        It also gives unexpected-success detection a second and far more
        talkative voice: `unittest` reports a mutant that has quietly
        gone green only as an anonymous "unexpected success", while this
        names the mutant and says to flip its decorator.

        Scope is the model tier deliberately. The render tier's two
        expectedFailures need a live browser, so their analog belongs in
        `tests/test_mutants_render.py` behind the same `MUTANTS_RENDER=1`
        gate its own catalogue uses — not here, where it would either
        skip silently or import a module `--coverage` never loads.
        """
        cls = type(self)
        reds = [n for n in sorted(dir(cls))
                if getattr(getattr(cls, n, None),
                          "__unittest_expecting_failure__", False)]
        self.assertTrue(reds, "no expectedFailure methods found — the "
                              "introspection hook moved")
        for name in reds:
            with self.subTest(method=name):
                mid = name[len("test_mutant_"):]
                self.assertTrue(
                    name.startswith("test_mutant_") and mid in CATALOGUE,
                    "expectedFailure method %r maps to no catalogue mutant "
                    "(convention: test_mutant_<mid>) — rename it, or this "
                    "guard walks straight past it" % name)
                m = CATALOGUE[mid]
                try:
                    scene = OPERATORS[m.op](m.build(), **m.args)
                    mism = m.expect.matches(collect_findings(scene))
                except Exception as exc:
                    self.fail("red mutant %r is red via %r, not a spec "
                              "mismatch — that is a broken mutant, not a "
                              "defect pin" % (mid, exc))
                self.assertIsNotNone(
                    mism, "red mutant %r is secretly GREEN at engine level "
                          "— its fix may have landed; flip the "
                          "expectedFailure" % mid)

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

# Detector -> the gated test that proves it, in tests/test_mutants_render.py.
# Where a detector has several proofs this names the STRONGEST one, since the
# string is what a reader takes for the state of the evidence.
RENDER_TIER = {
    # Batch D, 2026-08-13: moved off
    # `test_ablation_existence_fires_on_invisible_element`, which proves the
    # check against a 0x0, 0%-opacity ghost — an element no drawing contains,
    # so it demonstrated the arithmetic and not the defect class. That test
    # stays (it is the opacity red's control); the record now names the proof
    # over a real shipped class in a plausible configuration.
    "ablation_existence":
        "test_mutants_render.TestRenderMutants."
        "test_ablation_existence_fires_on_a_real_shipped_class",
    "ablation_continuity":
        "test_mutants_render.TestRenderMutants."
        "test_mutant_label_backdrop_severs_connector",
    # Named at the FIRING proof, not at the red mutant beside it: the mutant
    # asserts the post-fix silence, and a silence proves nothing (see
    # `test_silence_only_mutant_does_not_prove_its_check`).
    "parity_clipped":
        "test_mutants_render.TestRenderParity."
        "test_parity_clip_is_red_by_measurement_not_by_error",
}

# Check names a catalogue entry may name with no `DETECTORS` detector
# behind them, mapped to why that is legitimate. Red-by-absence is a real
# tactic — `phantom_passthrough_shared_attach` pins a defect the lint
# cannot see yet, and stays red until the lint lands — but it is
# indistinguishable from a TYPO'd check name, which is red forever for no
# reason. Worse, a typo'd check inside a `Silence` matches nothing and so
# passes VACUOUSLY forever. This table is the difference: aspiration is
# declared here with its reason, and anything else is a mistake.
ASPIRATIONAL: dict[str, str] = {
    "phantom_passthrough":
        "WP4b item 1 — e1 phantom pass-through lint, not yet built",
    "label_overflows_shape":
        "WP4 — shape-aware label fitting/lint, not yet built; the geometry "
        "it needs is already in canvas.py as `marker_inset` (4200)",
    "frame_containment":
        "WP5 — no check compares a member's geometry against the frame its "
        "`frameId` names; lint_layout reads frameId for help slots and "
        "same-frame pairing only, never for containment",
    "text_overlaps_node":
        "WP4 — a role-BLIND text/node overlap check, not yet built; the "
        "existing one gates on role_of(e) == 'annotation' (canvas.py:5523) "
        "and role_of defaults everything unroled to 'node' (3197)",
    "min_clearance":
        "WP4 — a near-miss check, not yet built; today's overlap loop needs "
        "a real intersection, so any positive gap is silent however small. "
        "Needs an intent channel before it can ship — see the mutant",
    "contrast_text":
        "docs/todo/contrast-and-min-font-lints.md — WCAG 1.4.3 text "
        "contrast (4.5:1), not yet built. Opacity is SETTLED: fold it into "
        "the effective color rather than ignoring it",
    "contrast_object":
        "docs/todo/contrast-and-min-font-lints.md — WCAG 1.4.11 non-text "
        "contrast (3:1), not yet built; the criterion people forget, and "
        "the one that catches a pale connector on cream paper",
    "min_font":
        "docs/todo/contrast-and-min-font-lints.md — fontSize floor, not "
        "yet built. The floor itself is no longer open: MEASURED at 7px "
        "on 2026-08-13 against the render tier at deviceScaleFactor 1, "
        "evidence in test_mutants_render.TestLegibilityFloor",
}

# FOR WHOEVER FLIPS THESE. No aspirational mutant has a neighbour asserting
# ITS check's other pole, because none of those checks exists to have a pole
# yet — every one borrows a detector that does exist. The borrowings are not
# equally strong, and the flip work differs accordingly:
#
#   phantom_passthrough  — neighbour `Silence("shared_corridor")` over
#       `_attach_chain(shared=False)` is a CONTINGENT negative: the same
#       builder with `shared=True` fires that check (see
#       `merged_stroke_caught_by_corridor`), so the quiet means something
#       about the picture.
#   label_overflows_shape, frame_containment, unroled_text_over_node —
#       each neighbours `Silence("endpoint_gap")` over a scene with NO
#       ARROWS, which proves liveness only: that check cannot fire there
#       whatever the labels, frames or texts do. They earn their keep by
#       refusing to match over any run where a detector crashed, and
#       nothing more. `unroled_text_over_node` has the strongest control
#       of the three even so — its neighbour is the SAME overlap with a
#       role attached, which the existing lint does report, so the pair
#       isolates the role gate as the only variable.
#
# So when WP4/WP4b's lints land, dropping the `expectedFailure` is not the
# whole change: give each mutant a real other-pole neighbour on the new
# check at the same time, or the flip trades a red that meant something for
# a green that does not.

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
    # (`label_label_overlap`, canvas.py:5558, left this table on
    # 2026-08-12: the visualize-skill mine's §(i.4) exposed the stored-width
    # dependency underneath it and `stale_label_width_hides_collision` now
    # proves it from DETECTORS, both poles.)
    "label_on_foreign_node":
        "enumerated 2026-08-12; no proving mutant yet — canvas.py:5579",
    "annotation_overlaps_node":
        "enumerated 2026-08-12; no proving mutant yet — canvas.py:5592",
    # (`text_overflow`, canvas.py:5637, left this table on 2026-08-13:
    # `composed_row_overflows_its_box` and `wrapped_label_overflows_its_box`
    # now prove it from DETECTORS on BOTH code paths, each with a magnitude
    # and an axis. The bbox-naive `room_w` underneath it is recorded at those
    # entries as an arm of `label_overflows_shape`, not as a defect of its
    # own.)
    # (`shared_attach_point`, canvas.py:5688, left this table on
    # 2026-08-12: the ELK spike fired it in production and
    # `shared_attach_point_fan_failed` now proves it from DETECTORS.)
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
    # (`not_a_json_object`, ART-000, canvas.py:378, left this table on
    # 2026-08-13. It has no CATALOGUE mutant and will not get one — the
    # subject is a LOAD, so there is no element list to mutate and
    # `mutants new` declines it — but the hand-kept quadruples prove it
    # ungated on both poles and at both tiers: what the model files
    # (`test_red_non_dict_artifact_is_dropped_silently`,
    # `test_quarantine_is_reported_but_never_filed_as_a_repair`,
    # `test_quarantine_alone_makes_catch_up_claim_no_repairs` in
    # `TestStoreIntegrity`, green since v0.9 Task 3) and what the agent is
    # told (`TestLoadFindingsReachTheAgent`, green since v0.9 Task 33).
    # "No proving mutant" was still literally true and would have stayed
    # true forever, which is how the row read as a gap it is not.)
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
        several proofs reports the same one every run — the gated test
        `RENDER_TIER` names for "render-tier", or the `UNCOVERED` reason
        (empty string if the detector carries none).
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
            rows.append((name, "render-tier", RENDER_TIER[name]))
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

    def test_every_expected_check_has_a_detector_or_is_declared(self) -> None:
        """No catalogue entry names a check that nothing can ever answer.

        Walks every mutant's `expect` and its neighbour's, both spec
        types: a `FindingSpec` on a check with no detector is red
        forever, and a `Silence` on one is worse — it matches nothing,
        so it passes vacuously forever and reads as coverage. Either
        way the likeliest cause is a typo, and the only legitimate
        cause is deliberate aspiration, which belongs in `ASPIRATIONAL`
        with its reason.
        """
        for mid in sorted(CATALOGUE):
            mutant = CATALOGUE[mid]
            for where, spec in (("expect", mutant.expect),
                                ("neighbour.expect",
                                 mutant.neighbour.expect)):
                with self.subTest(mutant=mid, where=where):
                    self.assertTrue(
                        spec.check in DETECTORS or spec.check in ASPIRATIONAL,
                        "mutant %r: %s names check %r, which is in neither "
                        "DETECTORS %s nor ASPIRATIONAL %s — a typo is red "
                        "forever (or, in a Silence, vacuously green "
                        "forever); declare it in ASPIRATIONAL if the "
                        "detector is genuinely still to be built"
                        % (mid, where, spec.check, sorted(DETECTORS),
                           sorted(ASPIRATIONAL)))

    def test_aspirational_entries_are_live_reasoned_and_still_needed(
            self) -> None:
        """ASPIRATIONAL cannot rot into a permanent excuse list.

        It is the one table that makes a check name legal with nothing
        behind it, so it needs its own anti-rot in three directions: a
        blank reason makes the exemption unreviewable; a key that has
        since gained a detector is stale, and leaving it means the
        mutant it covers stays red after its fix landed; and a key no
        catalogue entry names is dead weight nobody will dare delete
        later.
        """
        blank = sorted(k for k, v in ASPIRATIONAL.items() if not str(v).strip())
        self.assertEqual(blank, [],
                         "ASPIRATIONAL entries with no reason: %s" % blank)
        landed = sorted(set(ASPIRATIONAL) & set(DETECTORS))
        self.assertEqual(landed, [],
                         "ASPIRATIONAL names %s, which now HAS a detector — "
                         "the lint landed: drop the entry and flip the "
                         "mutant it was covering" % landed)
        named = {spec.check for m in CATALOGUE.values()
                for spec in (m.expect, m.neighbour.expect)}
        unused = sorted(set(ASPIRATIONAL) - named)
        self.assertEqual(unused, [],
                         "ASPIRATIONAL names %s, which no catalogue entry "
                         "expects — the aspiration it excused is gone; "
                         "delete the entry" % unused)

    def test_no_two_mutants_encode_the_same_defect(self) -> None:
        """Two ids for one defect cost every future run and teach nothing.

        `_register` already refuses a duplicate ID, and on 2026-08-12 that
        guard was the only thing that caught two agents pinning the same
        ellipse over-fire in parallel. It catches that collision solely
        because both picked the same name; had either chosen differently,
        both would have committed and the catalogue would carry two
        mutants for one bug forever.

        This compares what a mutant MEANS instead: the expectation
        (check, element, magnitude, direction), the operator and its
        arguments, and a content fingerprint of the base scene. Two
        entries agreeing on all of that are the same experiment under two
        names. Deliberate families stay apart on their own merits — the
        four diamond mutants share a scene and separate on expectation
        and operator args, which is exactly the distinction that earns
        them separate entries.

        Residual gap, stated so nobody reads this as full cover: the
        non-CATALOGUE red classes (`TestExportCompleteness`,
        `TestStoreIntegrity`, `TestPaintOrder`,
        `TestShapeBlindAnnotationOverlap`,
        `TestLoadFindingsReachTheAgent`, `TestBatchPathIntegrity`) never
        reach `_register` and have no expectation objects to compare, so
        nothing here would notice two agents writing the same plain red
        test under different method names. What defends those classes is
        a per-agent FILE-SECTION convention plus reviewer vigilance — no
        automated layer covers them, and saying so is the point.

        And one convention is REJECTED rather than merely absent, so it
        does not get re-proposed: per-agent mutant-id PREFIXES. They look
        like collision avoidance and are the opposite — guaranteeing ids
        differ permanently disarms `_register`'s duplicate-id refusal,
        which is the only reason the 2026-08-12 collision was caught at
        all. That trades a loud, detectable collision for a silent one.
        A per-agent FILE-SECTION convention is fine and carries none of
        that cost: it reduces textual merge conflict without touching
        either detection layer.
        """
        by_defect: dict[tuple, list[str]] = {}
        for mid in sorted(CATALOGUE):
            m = CATALOGUE[mid]
            spec = m.expect
            key = (spec.check, getattr(spec, "element", None),
                   getattr(spec, "magnitude", None),
                   getattr(spec, "direction", None), m.op,
                   tuple(sorted(m.args.items())),
                   canvas.content_fingerprint(m.build()))
            by_defect.setdefault(key, []).append(mid)
        dups = {k[0]: v for k, v in by_defect.items() if len(v) > 1}
        self.assertEqual(
            dups, {},
            "these mutants encode the same defect under different ids "
            "(same expectation, operator, args and base scene): %s — "
            "merge them, or give the second one the distinct magnitude "
            "or direction that is its reason to exist" % dups)

    def test_uncovered_entries_all_carry_reasons(self) -> None:
        """No UNCOVERED entry has a blank or whitespace-only reason."""
        empty = [k for k, v in UNCOVERED.items() if not str(v).strip()]
        self.assertEqual(empty, [])

    def test_lint_layout_append_count_is_pinned(self) -> None:
        """Spec §3 anti-rot, canvas.py half: the enumeration cannot drift.

        `coverage_table` gates the `DETECTORS` half of the ledger — a new
        registry entry with no mutant and no reason fails. The canvas.py
        half (the lint-template and ART-code rows in `UNCOVERED`) is a
        hand enumeration frozen on 2026-08-12 instead, so a new
        `errors.append` in `lint_layout` would otherwise land with no
        ledger row, no coverage row and nothing red — exactly the silence
        §3 says cannot happen. This pins the append-site count the
        enumeration was made from, so the ledger's basis moving is loud
        and says what to do about it.
        """
        src = inspect.getsource(canvas.lint_layout)
        sites = sum(src.count("%s.append" % chan)
                    for chan in ("errors", "warnings", "notes"))
        self.assertEqual(sites, 45,
                         "canvas.py lint_layout append-site count changed "
                         "(45 -> %d): re-enumerate the UNCOVERED ledger "
                         "(see plan Task 4 Step 1) and update this pin."
                         % sites)

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

    A cell that mutated nothing is an ENGINE defect — a broken operator
    or arguments that missed their target — not a fact about a drawing,
    so it raises rather than minting a survivor: every predicate here
    returns `[]` on an unchanged scene (nothing dropped, `fb == fa`, no
    re-bound target, no manufactured collinearity), which would turn the
    cell into a vacuous pass. Silence has to mean "the drawing answered
    the mutation", never "no mutation happened".

    Returns:
        `(survivors, skipped)` — survivors as `{"id", "detail"}` dicts
        sorted by id, skipped as `(base, op, reason)` triples in sweep
        order. Both are deterministic: the bases are fixed builders and
        an id hashes only its own detail string.

    Raises:
        EngineError: If a cell's operator left the scene unchanged, or
            raised one itself — either way named by the `base/op` cell
            it happened in, which is what makes it actionable.
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
            try:
                after = OPERATORS[op](scene, **args)
            except EngineError as exc:
                # The decorator's guards fire first for a decorated
                # operator, and they know the operator but not the CELL
                # — and a sweep failure is only actionable with the base
                # it ran on. Re-raise with the cell name prefixed so both
                # paths out of here read the same way.
                raise EngineError("sweep cell %s/%s: %s"
                                  % (base_name, op, exc)) from None
            # `_operator` now refuses a no-op at the source, so a
            # decorated operator can never reach this line unchanged.
            # The check stays as the net for entries that bypass the
            # decorator — a monkeypatched or foreign `OPERATORS` value
            # (see `test_no_op_operator_fails_the_sweep_cell`, which
            # patches in a bare lambda and is caught HERE, by cell name).
            if after == scene:
                raise EngineError("sweep cell %s/%s mutated nothing"
                                  % (base_name, op))
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

    def test_no_op_operator_fails_the_sweep_cell(self) -> None:
        """An operator that mutates nothing raises, naming its cell.

        The guard exists because every correspondence predicate is
        silent on an unchanged scene, so without it a broken operator
        would read as a clean sweep — silence standing in for health,
        which is the failure mode this harness exists to kill.
        """
        saved = OPERATORS["drop_edge"]
        OPERATORS["drop_edge"] = lambda scene, **kw: copy.deepcopy(scene)
        try:
            with self.assertRaises(EngineError) as caught:
                sweep_cells()
        finally:
            OPERATORS["drop_edge"] = saved
        self.assertIn("chain/drop_edge", str(caught.exception))

    def test_a_decorated_no_op_still_names_its_sweep_cell(self) -> None:
        """The decorator's guard reaches the caller with the cell attached.

        The test above patches in a BARE lambda, which bypasses
        `_operator` and so exercises the sweep's own check. A decorated
        no-op takes the other path: the decorator raises first, before
        `sweep_cells` can compare anything. Without the re-raise that
        report would name the operator and not the base it ran on, and
        "drop_edge mutated nothing" does not tell you which of the sweep
        bases to go and look at.
        """
        saved = OPERATORS["drop_edge"]
        OPERATORS["drop_edge"] = _operator(
            lambda scene, **kw: copy.deepcopy(scene))
        try:
            with self.assertRaises(EngineError) as caught:
                sweep_cells()
        finally:
            OPERATORS["drop_edge"] = saved
        msg = str(caught.exception)
        self.assertIn("sweep cell chain/drop_edge", msg)
        self.assertIn("mutated nothing", msg)

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
