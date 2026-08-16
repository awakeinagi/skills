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
from unittest import mock

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
# Two openings, one check. v0.9 WP4 gave `crosses_through_bound` a second
# sentence for a run drawn flat ALONG the bound node's border, because the
# first one ("enters X and runs Npx inside it") is false of an arrow that
# never goes inside. Widened here rather than distorting the message to fit
# the regex, which is what the fix's first round did (task 19 review, F6).
_RUNS_INSIDE_RE = re.compile(
    r"arrow (?P<element>[\w-]+) (?:enters .+ and runs|runs) "
    r"(?P<mag>\d+)px (?:inside|along .+ own border)")
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
# WP4b's e1 (canvas.py), landed by task 24 and the second template naming
# two arrows and one node — `element` is the NODE for the same reason as
# above, and here it is also the thing the finding is ABOUT: the claim is
# that this node stopped reading as a step. MAGNITUDE is the span of it
# that has arrow drawn over it, which is the one number that separates
# this check from its neighbours. The message carries three numbers on
# purpose and the regex pins the third: the bare span (0 on the ELK
# configuration) and the node's own width (80) are both legible readings
# a wrong implementation could report, and so is the whole merged stroke
# (448, the length `shared_corridor` and the spike both quote). Only
# "how much of the node is covered" is this finding.
_PHANTOM_RE = re.compile(
    r"arrows [\w-]+ and [\w-]+ read as one stroke through "
    r"(?P<element>[\w-]+).*?so (?P<mag>\d+)px of it has arrow drawn over")
# The other symmetric-pair template, and the only one that names no third
# party to blame: two labels, and the reader has to be told BOTH or the
# finding says nothing actionable. So `element` is the quoted pair verbatim
# — `'settled and reconciled n' and 'queued'` — in scene order, which is the
# strongest identity this message affords. It affords no MAGNITUDE at all
# (canvas.py reports the collision and not how many pixels of it),
# which is why the mutant below asserts the pair and not a number; putting
# the overlap depth into that template is the standing proposal in
# docs/research/visualize-skill_idea_mining_2026-08-12.md O2, and the day
# it lands this spec should tighten to a magnitude.
_LABEL_OVERLAP_RE = re.compile(
    r"labels (?P<element>.+) overlap — nudge one clear")
# The arrow-label/foreign-node lint, registered 2026-08-15 with the
# curvature switch. `element` is the NODE the label lands on, not the
# label: the label is quoted as CONTENT and carries no id, and the node
# is what a re-route has to clear. The `[\w-]+` stops before the
# ` ('Label')` suffix `canvas.name()` appends, as `_SHARED_ATTACH_RE`'s
# does. This is the entry curator batch 19 said `label_on_foreign_node`
# was waiting on.
_LABEL_ON_FOREIGN_RE = re.compile(
    r"arrow label .+ lands on (?P<element>[\w-]+), which is neither end")
# The clipped-text lint (canvas.py), whose template is the richest one
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
# The shape-aware label check (canvas.py), landed by v0.9 WP4 as a
# SEPARATE check rather than an arm of `text_overflow` — so the entry it
# proves keeps its own key and its own element, and the re-key the
# fold-in path would have demanded does not arise. `element` is the
# LABEL here, not the owner, which is the opposite of `_TEXT_OVERFLOW_RE`
# next door and deliberate: this message names both, and the thing drawn
# in the wrong place is the label. MAGNITUDE is the total overhang at the
# label's own height, the single number this finding carries, so unlike
# the 2-D template above there is nothing lost to one scalar. The tail is
# matched as well as the head because the label's own text is quoted
# upstream of the number and could otherwise supply a `by NNpx`.
_LABEL_SHAPE_RE = re.compile(
    r"label (?P<element>[\w-]+) overhangs [\w-]+.*? by (?P<mag>\d+)px — "
    r"the (?:diamond|ellipse) is only \d+px across")
# The two arms of the text/node overlap loop (canvas.py), which v0.9 WP4
# (Task 23) made role-BLIND: every free text is measured against every node,
# and the role picks which sentence comes out. The regexes are deliberately
# near-identical — same element (the text), same magnitude (the overlap AREA,
# the reading the shape overlap loop next door already uses) — so that when
# the mutant pair below runs the SAME geometry through both, the only
# difference on either side is which check spoke. Anything else the pair
# proved would be an artefact of the templates rather than of the role gate.
_TEXT_ON_NODE_RE = re.compile(
    r"text (?P<element>[\w-]+) \(.+?\) covers [\w-]+.*? "
    r"\((?P<mag>\d+)px²\), and nothing marks it as belonging there")
_ANNOTATION_ON_NODE_RE = re.compile(
    r"annotation (?P<element>[\w-]+) \(.+?\) lies on top of [\w-]+.*? "
    r"\((?P<mag>\d+)px²\)\. Move it clear")
# Near-miss spacing (canvas.py), the crowding arm of the shape pair loop.
# MAGNITUDE is the clear gap itself and `element` is `b`, the LATER of the
# two in scene order: unlike `_SHARED_ATTACH_RE`'s symmetric pair there IS a
# non-arbitrary pick here, because scene order is paint order — `b` is drawn
# over `a`, so it is the newer placement and the one the message tells you to
# nudge. Both ids survive in `raw`. The floor is matched as a bare `\d+` and
# not pinned: this regex reads what the check MEASURED, and the day the floor
# is retuned the finding is still the same finding.
_MIN_CLEARANCE_RE = re.compile(
    r"are only (?P<mag>\d+)px apart \(spacing floor \d+px\) — "
    r"nudge (?P<element>[\w-]+) clear")


def _collect_crossings(els: list[dict]) -> list[dict]:
    """One finding whose magnitude is the scene's crossing count.

    Args:
        els: The scene's element list.

    Returns:
        A single-item findings list for the `crossings_count` check.
    """
    n, _pairs = instruments.edge_crossings(els)
    return [{"check": "crossings_count", "element": None,
             "magnitude": float(n), "direction": None,
             "raw": "crossings=%d" % n}]


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
    # v0.9 WP4b (task 24): the entry that flips
    # `phantom_passthrough_shared_attach` out of red-by-absence, added in
    # the same change as the lint it names. No dirmap — the finding is one
    # covered span with no axis to report; which axis the pair shares is
    # legible from the two arrow ids in `raw`.
    "phantom_passthrough": {"lint_re": _PHANTOM_RE},
    "label_label_overlap": {"lint_re": _LABEL_OVERLAP_RE},
    # v0.9 WP4 stage 3: landed with the curvature switch, because the
    # label anchor is the surface curvature moves and a `Silence` on an
    # unregistered check passes vacuously. No dirmap — the finding is one
    # label on one box, with no axis to report.
    "label_on_foreign_node": {"lint_re": _LABEL_ON_FOREIGN_RE},
    # Batch D follow-up, 2026-08-13: left the enumerated-no-mutant ledger
    # when the pair below proved it fires, on both arms, with magnitude and
    # direction. The dirmap keeps the three arms distinct because the
    # direction is where this check's axis information lives.
    "text_overflow": {"lint_re": _TEXT_OVERFLOW_RE,
                      "dirmap": {"too wide": "wide", "too tall": "tall",
                                 "too wide and too tall": "both"}},
    # v0.9 WP4: left ASPIRATIONAL for DETECTORS when the check it named
    # was built. No dirmap — this finding fails on one axis by
    # construction, the label's width against the body's chord.
    "label_overflows_shape": {"lint_re": _LABEL_SHAPE_RE},
    # v0.9 WP4 (Task 23), the same move for the other two: the role-blind
    # text/node arm and the near-miss spacing arm both left ASPIRATIONAL the
    # day their checks landed. `annotation_overlaps_node` joins them from the
    # other direction — it is the FIVE-ROUND-OLD arm of the same loop, and it
    # left the enumerated-no-mutant ledger in the same change, because the
    # role-blind mutant's control is exactly "this check reports the roled
    # overlap" and an unregistered name is a thing no `FindingSpec` can
    # assert: `collect_findings` never emits it, so the control could only
    # ever have been a borrow from some other detector, which is the debt
    # that flip owed.
    # No dirmaps: all three findings carry one measured scalar and no axis.
    "text_overlaps_node": {"lint_re": _TEXT_ON_NODE_RE},
    "annotation_overlaps_node": {"lint_re": _ANNOTATION_ON_NODE_RE},
    "min_clearance": {"lint_re": _MIN_CLEARANCE_RE},
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
    together: `lint_layout`'s `label_boxes` (canvas.py) trusts stored
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
    (canvas.py), so a narrower scene can rewrap a note onto more
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


# What each class owes the export, per instance — MEASURED against live
# `render_svg`, never read off its source: the seven that always survived on
# 2026-08-12, and `freedraw` and `image` on 2026-08-14, when v0.9 WP4 gave
# them paint branches and this file stopped needing a second table for the
# classes that reached no export at all. An arrow owes a stroke and its
# arrowhead; a frame owes its box and its name; a line owes a stroke and no
# head; a freedraw owes the polyline through its `points`; an image owes the
# X-box placeholder — a border and its two diagonals — because the picture's
# bytes live in the document's `files` map, a sibling of `elements` that
# `render_svg` is never handed.
_EXPORT_MARKUP: dict[str, dict[str, int]] = {
    "rectangle": {"rect": 1}, "ellipse": {"ellipse": 1},
    "diamond": {"polygon": 1}, "line": {"polyline": 1},
    "arrow": {"polyline": 1, "polygon": 1}, "text": {"text": 1},
    "frame": {"rect": 1, "text": 1}, "freedraw": {"polyline": 1},
    "image": {"rect": 1, "polyline": 2}}


class TestExportCompleteness(unittest.TestCase):
    """`render_svg` emits every class it ships — or is caught not to."""

    def test_every_shipped_class_is_accounted_for(self) -> None:
        """A newly shipped element class cannot arrive UNPINNED.

        The table is hand-measured, so the one thing it cannot do is
        notice a TENTH name in `ELEMENT_TYPES`. That is this test's whole
        job and the limit of it: it catches a class nobody wrote a row
        for. Adding one is how `freedraw` and `image` sat unpainted until
        v0.9 WP4, so the pin is not hypothetical.

        WHAT IT DOES NOT CATCH, said plainly because the temptation to
        overstate it is real. A tenth class whose row is `{}` passes here
        AND passes `test_shipped_classes_reach_the_export`, because `{}`
        is what an unpainted class measures — set equality holds and the
        exact-count sweep confirms a class owes nothing. Removing
        `_DROPPED` in v0.9 WP4 did not remove that escape hatch; it
        RESPELLED it as `{}`, and the new spelling is quieter than the
        old one because it no longer has a name to grep for.

        What closes it is one tier up: `test_mutants_render.
        TestRenderParity.test_no_class_agrees_by_being_absent_from_both`
        asserts markup != {} and ink > 0 for every name in
        `ELEMENT_TYPES`, so a `{}` row fails there. That test is gated on
        `MUTANTS_RENDER=1` and does NOT run in the every-commit suite —
        which means an unpainted tenth class is caught on the render tier
        and only there. Anyone adding a class should run it.
        """
        self.assertEqual(set(_EXPORT_MARKUP), set(canvas.ELEMENT_TYPES))

    def test_shipped_classes_reach_the_export(self) -> None:
        """Every shipped class contributes exactly its own markup."""
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

    # GONE with the flip below, on purpose and not silently:
    # `test_dropped_classes_are_red_by_measurement_not_by_error` was this
    # class's standing red-by-assertion guard, and it walked `_DROPPED`.
    # Both reds are green as of v0.9 WP4 and `_DROPPED` is empty of
    # meaning, so the guard had no subject left — it would have iterated
    # nothing and passed forever, which is the shape of a test that reads
    # as cover and gives none. Its own failure message named this removal
    # as the correct response. What replaced its job: there is no red here
    # to protect from error-masking, and `test_shipped_classes_reach_the_
    # export` measures all nine classes at exact counts in every commit.

    def test_red_freedraw_never_reaches_the_export(self) -> None:
        """FLIPPED by v0.9 WP4 (Task 21). Kept its red-era name.

        `paint` used to dispatch on rectangle/ellipse/diamond/arrow/line/
        text/frame and return silently for anything else, so a stroke the
        user drew with the pencil was stored, was counted into the
        export's bounds — and painted nothing, leaving a hole the reader
        took for empty canvas. The user's own mark was the case that
        stung: the drawing is the truth (v0.6 WP1), and the agent
        narrated from a snapshot the mark was missing from.

        Two things landed together and both were needed. The dispatch now
        walks the array once and paints a freedraw as the polyline
        through its `points`; and the bounds loop reads those same
        `points`, where before it took the STORED width and height for
        everything but arrows and lines — a stroke that overhung its own
        box had the overhang cropped by the viewBox, so a paint branch
        alone would have drawn ink outside the window.

        The assertion is unchanged from the red: "at least one tag", not
        an exact count, because which tag was the fixing WP's call. The
        exact count it chose is pinned by `_EXPORT_MARKUP` instead, so
        this staying loose costs nothing.
        """
        delta = _export_delta(_one_of("freedraw"), "x-1")
        self.assertGreaterEqual(
            sum(delta.values()), 1,
            "freedraw x-1 contributes no markup to the export: it is in "
            "ELEMENT_TYPES, it is in the model, it is not in the picture")

    def test_red_image_never_reaches_the_export(self) -> None:
        """FLIPPED by v0.9 WP4 (Task 21). Kept its red-era name.

        Same missing branch as the stroke above, and the same silence,
        but the reachability differed and mattered: `make_element` refuses
        op-made images outright (they would have no `fileId` to render),
        so every image in a scene got there because a person pasted or
        dropped it on the canvas. That made this the purest form of the
        defect — the export dropped only what the user put there by hand.

        The dispatch's `image` branch paints the X-box placeholder rather
        than an `<image>` href, and the reason is structural rather than
        expedient: the bytes live in the document's `files` map, which is
        a SIBLING of `elements`, and `render_svg` is handed the element
        array alone. A placeholder says "a picture sits here, this big",
        which is everything the geometry can honestly claim.

        Assertion unchanged from the red — only the count is pinned here,
        with the exact tags in `_EXPORT_MARKUP`.
        """
        delta = _export_delta(_one_of("image"), "x-1")
        self.assertGreaterEqual(
            sum(delta.values()), 1,
            "image x-1 contributes no markup to the export: it is in the "
            "model, it widens the bounds, it is not in the picture")


def _annotation_at(corner_clear: bool, roled: bool = True,
                   kind: str = "ellipse") -> list[dict]:
    """A circle and a text, in its empty bbox corner or on its body.

    The circle is 400x400 at (300,250) — centre (500,450), r=200 — so the
    bounding box corner is 82.8px deep, wide enough to park a readable
    text in the void without any part of it touching the drawn outline.
    At (302,252) the 70x24 annotation's nearest point is 16.0px clear of
    the circle; at (460,420) it lies across the body, 194px inside.

    `roled` is the ONE base scene both arms of the role-blind loop share
    (curator batch 19, 2026-08-14). The loop measures the same rectangle
    intersection for either, and the role only picks which sentence comes
    out, so giving each arm its own circle would have invited the two
    scenes to drift apart on a geometry that has to stay identical for
    the pair to say anything.

    `kind` is the same idea one level up (v0.9 Task 56). The three reds
    this builder was written for all used the ellipse, and the loop they
    pin does not filter by type, so the rhombus arm rode along on the fix
    with nothing watching it — a later change that special-cased
    `ellipse` would have left it blind and every red green. A 400x400
    rhombus's bbox corner is 141px deep against the circle's 82.8, so the
    SAME two coordinates work for both: at (302,252) the text is 102px
    clear of the rhombus, and at (460,420) it lies across the body.

    Args:
        corner_clear: True to park the text in the empty corner.
        roled: True for `role="annotation"` (the `annotation_overlaps_node`
            voice), False for an unroled text — which is what a pasted
            text arrives as, and what `text_overlaps_node` answers about.
        kind: The node's Excalidraw type — `"ellipse"`, or `"diamond"`
            for the same scene over the other non-box shape.

    Returns:
        The two-element scene: node `n1`, then text `t1`.
    """
    tx, ty = (302, 252) if corner_clear else (460, 420)
    return [el(id="n1", type=kind, x=300, y=250, width=400, height=400,
               customData={"role": "node"}),
            el(id="t1", type="text", x=tx, y=ty, width=70, height=24,
               text="see note", fontSize=16,
               customData={"role": "annotation"} if roled else {})]


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
    """Lint warnings claiming a text sits on a node, in any of three voices.

    One phrase per template, and all three are FIXED prose rather than
    the detector regexes: an over-firing arm on a scene where the text is
    clear computes a NEGATIVE overlap and says "-40x20px of overlap
    (-800px²)", and `_TEXT_ON_NODE_RE` reads its magnitude as a bare digit
    run, which cannot match a leading minus. Filtering through the regex
    would compare `[] == []` and pass over exactly the failure these reds
    exist to catch — the lesson the Task 23 review paid for next door in
    `TestTextOverlapAndClearanceQuietHalves`.

    Args:
        scene: The scene to lint.

    Returns:
        The matching warning lines, one per claim.
    """
    lint = canvas.lint_layout(scene, artifact_type="flow")
    return [w for w in lint["warnings"]
            if "lies on top" in w or "lands on" in w
            or "nothing marks it as belonging there" in w]


class TestShapeBlindAnnotationOverlap(unittest.TestCase):
    """Shape-blindness instance five: text/node overlap is raw bbox math.

    A sibling of `ellipse_corner_overfire`, and outside `CATALOGUE` for a
    reason worth stating: an over-fire mutant asserts `Silence` on its
    check, and `label_on_foreign_node` (canvas.py) had no `DETECTORS`
    entry when this class was written — a `Silence` on an unregistered
    check passes vacuously, so the catalogue could not hold that arm.
    Read as lint text instead, which needs no registry. (That entry
    LANDED on 2026-08-15 with the label-anchor rewrite, so this
    particular blocker is spent; the batch-19 ruling below is what keeps
    the family here now.)

    `annotation_overlaps_node` acquired an entry on 2026-08-14 (v0.9 WP4
    Task 23, as the other pole of the role gate), and `text_overlaps_node`
    with it, so two of the three reds below COULD now be `CATALOGUE`
    over-fire mutants. CURATOR RULING, batch 19, 2026-08-14: they stay
    here, and the reason is now stronger than "one paragraph". The
    family may not be split across two homes, because the third red
    pins `label_on_foreign_node`, which had no `DETECTORS` entry then and
    therefore COULD not move — a `Silence` on an unregistered check
    passes vacuously. (It has one as of 2026-08-15. The ruling stands on
    the sentence that follows, not on that absence, and the absence
    being spent is exactly the kind of thing that rots a ruling into a
    superstition if nobody writes it down.)
    Moving the two that can would leave a three-arm family
    with two homes, one of them holding the arm most likely to be
    forgotten by a fix. The cost is real and is accepted knowingly: these
    three get no dedupe fingerprint and no `Silence`-over-crash refusal.
    A crash is still caught, by the ungated liveness partner each red is
    paired with below.

    Each check gets its OWN red, over its own scene, and that per-arm
    shape IS the batch-19 constraint rather than a departure from it: a
    partial `marker_inset` fix cannot turn this family green, because
    whichever arm it teaches turns that arm's red into an UNEXPECTED
    SUCCESS — a hard failure that names the flip — while the arms it did
    not teach stay red. One red covering all three would stay red and say
    nothing about which. The three do not share a code path: the two
    overlap arms read a text's stored box (one loop, `lint_layout`), the
    label arm resolves a bound label through `arrow_label_anchor` first.
    """

    def test_an_annotation_on_the_body_is_reported(self) -> None:
        """The check fires where it should — and the red below needs it to.

        Without this, a `lint_layout` that stopped emitting the warning
        at all would turn the red green and read as a fix.
        """
        self.assertTrue(_says_lies_on(_annotation_at(corner_clear=False)))

    def test_red_annotation_clear_of_a_circle_is_reported_as_on_it(
            self) -> None:
        """FLIPPED by v0.9 Task 56. Kept its red-era name.

        Both text/node checks — `label_on_foreign_node` (canvas.py) and
        `annotation_overlaps_node` (:5593) — intersected raw
        `x/y/width/height` rectangles with no shape term at all, so an
        ellipse was its bounding box to both of them. Here the annotation
        sits wholly in the corner void, its nearest point 16.0px from the
        drawn outline, and the lint said it "lies on top of" the circle
        in the same words it uses for one lying across the middle.

        What landed: the loop measures `shape_overlap` (canvas.py)
        against the drawn outline instead of the stored box. The
        primitive reads the signed per-axis overlap of two RENDERED
        bodies through `shape_clip`, so a box pair short-circuits to the
        arithmetic this replaced and every fixture number is unmoved.

        The `nodes` set these loops walk is not shape-filtered, so a
        diamond read the same way and is now covered by the same edit —
        pinned separately by `diamond_text_void_overfire`, because
        nothing here would catch a regression that special-cased
        `ellipse`.

        This method pins `annotation_overlaps_node` ONLY; its two
        siblings have their own methods below, because a fix could have
        taught one and not the others.
        """
        self.assertEqual(
            _says_lies_on(_annotation_at(corner_clear=True)), [],
            "the annotation is 16px clear of the circle and reported as "
            "on it")

    def test_an_unroled_text_on_the_body_is_reported(self) -> None:
        """The role-blind arm fires where it should, and its red needs it to.

        Without this, a `lint_layout` that stopped measuring unroled
        texts — which is precisely the state Task 23 found it in — would
        turn the red below green and read as a fix.
        """
        self.assertTrue(
            _says_lies_on(_annotation_at(corner_clear=False, roled=False)))

    def test_red_unroled_text_clear_of_a_circle_is_reported_as_on_it(
            self) -> None:
        """FLIPPED by v0.9 Task 56. Kept its red-era name.

        Curator batch 19, from the Task 23 report §8 item 1 and the
        review's standing instruction, 2026-08-14. Task 23 made the
        text/node loop role-blind — the right fix, and it multiplied this
        bug's reach at the same time. The loop measured `ox`/`oy` as a
        raw rectangle intersection BEFORE it consults the role, so the
        blindness the method above pins for annotations applied to every
        unroled text on the canvas, which is what a pasted text is. Same
        void, same 16.0px of clear space, same fabricated area (1680px²);
        the only thing that changed was which voice fabricated it:

            annotation t1 ('see note') lies on top of n1 — 70x24px …
            text t1 ('see note') covers n1 — 70x24px …

        Pinned as its own method rather than folded into the one above
        BECAUSE the two share that code path. A fix that put the shape
        term inside the `trole == "annotation"` branch instead of ahead
        of the role test would have flipped that one, looked complete,
        and left every unroled text exactly as blind — so the family is
        only green when both arms have been taught. What landed sits
        ahead of the role test, at the `ox`/`oy` measurement itself, so
        both arms learned at once and this method is the proof.
        """
        self.assertEqual(
            _says_lies_on(_annotation_at(corner_clear=True, roled=False)), [],
            "the text is 16px clear of the circle and reported as covering "
            "it, with 1680px² of overlap that is not there")

    def test_an_arrow_label_on_the_body_is_reported(self) -> None:
        """The label arm fires where it should, and its red needs it to."""
        self.assertTrue(_says_lies_on(_arrow_label_at(corner_clear=False)))

    def test_red_arrow_label_clear_of_a_circle_is_reported_as_on_it(
            self) -> None:
        """FLIPPED by v0.9 Task 56. Kept its red-era name.

        The sibling check, pinned separately on purpose.
        `label_on_foreign_node` (canvas.py) ran the same raw rectangle
        intersection as `annotation_overlaps_node`, against the same
        unfiltered `nodes` set, and until this method existed the
        `"lands on"` arm of `_says_lies_on` matched nothing in either
        scene — the helper anticipated the check, the docstring claimed
        it, and no assertion reached it. A WP4 fix teaching only the
        annotation loop about shapes would have flipped the two above and
        left this one silently as blind as before.

        One difference from its siblings was load-bearing for the fix,
        and the fix respected it: this check reads the label's DRAWN box
        via `arrow_label_anchor`/`arrow_label_slot`, not its stored x/y,
        so the shape term went in AFTER that resolution — on the rects
        already sitting in `label_boxes`' output — rather than on a
        stored coordinate nobody paints.
        """
        self.assertEqual(
            _says_lies_on(_arrow_label_at(corner_clear=True)), [],
            "the arrow label is 16px clear of the circle and reported as "
            "landing on it")

    def test_an_annotation_on_a_rhombus_body_is_reported(self) -> None:
        """The rhombus arm fires where it should, and its pin needs it to."""
        self.assertTrue(
            _says_lies_on(_annotation_at(corner_clear=False, kind="diamond")))

    def test_an_annotation_clear_of_a_rhombus_is_left_alone(self) -> None:
        """The DIAMOND arm of the three reds above (v0.9 Task 56).

        All three used the ellipse, and the loop they pin does not filter
        on type — so one edit taught both shapes, and nothing here would
        have caught a later change that special-cased `ellipse` and left
        the rhombus reading its bounding box. The void is worse on this
        shape, which is the reason it is not left to inference: a 400x400
        rhombus's bbox corner is 141px deep against the circle's 82.8,
        and the text sits 102px clear of any drawn facet.

        Same coordinates as its ellipse sibling, `kind` the only thing
        that differs, so the pair is a single-variable experiment on the
        shape term exactly as `_clearance_pair_shaped` is one axis down.
        """
        self.assertEqual(
            _says_lies_on(_annotation_at(corner_clear=True, kind="diamond")),
            [],
            "the annotation is 102px clear of the rhombus and reported as "
            "on it")

    def test_a_text_clipping_the_shoulder_reports_the_clipped_patch(
            self) -> None:
        """The partial pole, where the reported magnitude is observable.

        Neither the void nor the wholly-inside case can see the fix's
        arithmetic: one reports nothing and the other reports the box
        number, unchanged. A 200x24 text laid across a circle's shoulder
        is the only place the shaped extent is visible in the sentence,
        and this pins it at three heights so a fix that clamped to the
        box (194x24 everywhere) or collapsed to a point fails here.

        The numbers are the DRAWN chord at the text's own band, and they
        grow as the band descends toward the circle's waist: 94px at
        y=250, 129 at y=274, 162 at y=310. `label_overflows_shape` reads
        the same geometry for a bound label through `shape_band_width`.

        RESIDUE, ruled (v0.9 Task 56, D1): the `px²` is `int(ox * oy)` —
        the intersection's BOUNDING BOX, not its area, which for a lens
        would need a conic clip for a number no reader quotes as an area.
        So `129x24` and `3119px²` do not multiply out, and the second
        reason they do not is older and applies to boxes too: each is
        independently truncated to an int.
        """
        for ty, want in ((250, "94x24px of overlap (2279px²)"),
                         (274, "129x24px of overlap (3119px²)"),
                         (310, "162x24px of overlap (3910px²)")):
            said = _says_lies_on(
                [el(id="n1", type="ellipse", x=300, y=250, width=400,
                    height=400, customData={"role": "node"}),
                 el(id="t1", type="text", x=300, y=ty, width=200, height=24,
                    text="see note", fontSize=16,
                    customData={"role": "annotation"})])
            self.assertEqual(len(said), 1, "y=%d said %r" % (ty, said))
            self.assertIn(want, said[0],
                          "y=%d reports the box's 194x24 or a clamped "
                          "patch, not the drawn chord" % ty)


def _says_overlap(scene: list[dict]) -> list[str]:
    """Lint warnings claiming two NODES overlap, in either of two voices.

    Fixed prose rather than a detector regex, and not by preference: the
    pair loop's two arms are `shape_overlap` and `grown_label_overlap` in
    the ledger, both of them `UNCOVERED`. A `Silence` on an unregistered
    check passes vacuously, so a mutant asserting one would be worth
    nothing — the same bar that keeps `TestShapeBlindAnnotationOverlap`
    out of `CATALOGUE`, one loop further up.

    Args:
        scene: The scene to lint.

    Returns:
        The matching warning lines, one per claim.
    """
    lint = canvas.lint_layout(scene, artifact_type="flow")
    return [w for w in lint["warnings"]
            if "overlap — separate them" in w or "grew to fit its label" in w]


def _void_pair(on_body: bool, grown: bool = False,
               kind: str = "diamond") -> list[dict]:
    """A big non-box node and a small one, in its bbox corner or on its body.

    `n1` is 400x400 and `n2` is a 40x40 rectangle — a plain box, so the
    only shape in the experiment is `n1`'s and there is no question about
    which one the check misread. At (0,0) the small box sits in the bbox
    corner with clear canvas between it and the drawn body: 120px of it
    on the rhombus, 40px on the circle, whose void is the shallower of
    the two. At (168,168) it sits squarely on the body with 40x40 of
    real ink shared, for either shape. Both coordinates are on the 4px
    grid, so `offgrid_elements` fires on neither and the two scenes
    differ in nothing but the position.

    SIZED, not guessed, and the first size was wrong in a way worth
    recording (v0.9 Task 56): at 60x60 the box clears the rhombus by 80px
    but CLIPS the circle by 2.8px, and the ellipse pole passed anyway
    because 2.8x2.8 falls under the arm's quarter-area bar. It asserted
    silence and got it for a reason that had nothing to do with the shape
    term — the exact shape of a test that reads as cover and gives none.
    40x40 clears both.

    `grown` marks `n1` `auto_grown`, which switches the loop to its other
    arm — the one with no area gate at all, and therefore the one that
    over-fires on any box overlap however little ink is involved.

    Args:
        on_body: True to park the small box on the drawn body.
        grown: True to mark `n1` as having grown to fit its label.
        kind: `n1`'s Excalidraw type.

    Returns:
        The two-element scene: nodes `n1` and `n2`.
    """
    cd = {"role": "node"}
    if grown:
        cd["auto_grown"] = True
    sx, sy = (168, 168) if on_body else (0, 0)
    return [el(id="n1", type=kind, x=0, y=0, width=400, height=400,
               customData=cd),
            el(id="n2", type="rectangle", x=sx, y=sy, width=40, height=40,
               customData={"role": "node"})]


class TestShapeBlindPairOverlap(unittest.TestCase):
    """The pair loop's OVERLAP arm, over-fire pole (v0.9 Task 56).

    `diamond_clearance_overfire` and `ellipse_clearance_overfire` pin the
    NEAR-MISS arm of `lint_layout`'s shape pair loop. The overlap arm
    directly above it — the one that says "overlap — separate them" and
    "grew to fit its label" — was blind in the same way and pinned by
    nothing, so a fix that taught the crowding arm and stopped would have
    turned both catalogue entries green with a 40x40 box sitting in 120px
    of empty canvas still reported as an overlap.

    Outside `CATALOGUE` for the reason its sibling class states: both
    templates sit in `UNCOVERED` with no `DETECTORS` entry, and a
    `Silence` on an unregistered check passes vacuously. Read as lint
    text instead, which needs no registry, and paired with a liveness
    partner apiece so a check that stopped speaking altogether fails
    here rather than reading as a fix.

    NAMING, so it is not mistaken for a collision: the ledger's check id
    for this arm is `shape_overlap`, and canvas.py's shape primitive
    added by the same task is also called `shape_overlap`. They are the
    same measurement seen from the two sides — the function computes what
    the check reports — and they live in different namespaces, but a
    curator grepping one will land on the other. That is the whole cost,
    measured rather than assumed (Task 56 review, F6): a
    `Silence("shape_overlap")` written by mistake does NOT slip through,
    because `TestCoverage.test_every_expected_check_has_a_detector_or_
    is_declared` refuses any catalogue entry naming a check nothing can
    answer, on both `expect` and `neighbour.expect`. Disclosure is
    therefore the right-sized response and no rename is owed.
    """

    def test_a_small_box_on_the_rhombus_body_is_reported(self) -> None:
        """The arm fires where it should, and the pin below needs it to."""
        self.assertTrue(_says_overlap(_void_pair(on_body=True)))

    def test_a_small_box_in_the_rhombus_corner_void_is_left_alone(
            self) -> None:
        """120px of clear canvas inside the bbox is not an overlap.

        The overlap arm intersected stored boxes, so a 40x40 box parked
        in a 400x400 rhombus's corner — where the nearest facet is 120px
        away on both axes and no ink is shared at all — was reported as
        "n1 and n2 overlap — separate them", in the same words used for
        two boxes lying across each other.

        This is also where decision D2 is observable. The arm fires on
        `ox * oy > 0.25 * smaller`, and `smaller` is now the DRAWN area:
        half a rhombus's box, not the box. Both halves of the ratio had
        to move together, and pinning only the numerator would have
        traded this over-fire for an under-fire on pairs that really do
        share a quarter of their ink.
        """
        self.assertEqual(
            _says_overlap(_void_pair(on_body=False)), [],
            "the small box is 120px clear of the rhombus and reported as "
            "overlapping it")

    def test_a_grown_box_overlapping_on_the_body_is_reported(self) -> None:
        """The grown arm fires where it should, and its pin needs it to."""
        self.assertTrue(_says_overlap(_void_pair(on_body=True, grown=True)))

    def test_a_grown_box_clear_in_the_corner_void_is_left_alone(self) -> None:
        """The `auto_grown` arm has no area gate, so it over-fired cleanly.

        Its own comment explains why it has none — a box that grew to fit
        its label gets no size slack, because nothing reflows siblings —
        and that reasoning is sound about ink and was applied to boxes.
        On a shape it meant ANY bounding-box intersection spoke, so this
        scene drew "n1 grew to fit its label and now overlaps n2" across
        120px of empty canvas.

        Paired with the ungrown pin above rather than folded into it:
        the two arms are separate branches of one `if`, and the fuzz
        behind this task found the ungrown arm's area gate incidentally
        covering the corner void for ellipses while this one had nothing
        covering it at all.
        """
        self.assertEqual(
            _says_overlap(_void_pair(on_body=False, grown=True)), [],
            "the grown arm reports an overlap across 120px of clear canvas")

    def test_the_quarter_bar_is_measured_against_the_DRAWN_area(
            self) -> None:
        """Decision D2, and the only thing in the suite that holds it.

        The arm fires on `ox * oy > 0.25 * smaller`, and D2 ruled
        `smaller` to be the body's area rather than its box's — "a
        quarter of the smaller shape is covered" meaning the same thing
        for a rhombus as for a box. Two 100x100 rhombi stacked 56px apart
        share 44x44 of drawn patch: 1936px², which clears the drawn bar
        of 1250 and misses the box bar of 2500. With the box denominator
        the check goes quiet over a pair sharing nearly 40% of its ink.

        Measured over the 625 offsets on the 4px grid, the drawn
        denominator recovers 62 rhombus and 35 ellipse configurations the
        box denominator silences, and silences NONE that it reports —
        so the ruling's direction is one-way, and this pins it.
        """
        self.assertTrue(
            _says_overlap(_crowded_pair_shaped(0, 56)),
            "44x44 of shared ink on a rhombus whose body is 5000px² is "
            "under a quarter only if the denominator is the box")

    def test_the_quarter_bar_numerator_is_the_DRAWN_patch(self) -> None:
        """The other half of the same ratio, and the over-fire pole.

        At 72px the two rhombi share 28x28 = 784px², under the bar by
        either reading — but their BOXES share 100x28 = 2800px², over the
        box bar of 2500, which is why this pair was reported before the
        numerator learned about shapes. A fix that moved the denominator
        and left the numerator alone passes the method above and fails
        here.
        """
        self.assertEqual(
            _says_overlap(_crowded_pair_shaped(0, 72)), [],
            "28x28 of shared ink is reported because the BOXES share "
            "100x28")

    def test_the_ellipse_arm_of_both_poles_agrees(self) -> None:
        """The same two poles over the conic, since one edit taught both.

        `kind` is the only difference from the four methods above, which
        is what makes this a shape-term experiment rather than a second
        copy of them. The circle's corner void is the shallower — 40px of
        clear canvas where the rhombus gives 120 — and it is what sized
        the small box for both, per `_void_pair`.
        """
        self.assertEqual(
            _says_overlap(_void_pair(on_body=False, kind="ellipse")), [],
            "the small box is 40px clear of the circle and reported as "
            "overlapping it")
        self.assertTrue(
            _says_overlap(_void_pair(on_body=True, kind="ellipse")))


def _dense_axis_overlap(a: dict, b: dict, axis: str,
                        samples: int = 2001) -> float | None:
    """The widest facing overlap of two bodies, by exhaustive sampling.

    An INDEPENDENT reference for `_axis_overlap` (canvas.py), which finds
    the same quantity by ternary search. It shares `shape_span` — the
    thing being checked is the search, not the geometry underneath it —
    but it makes no convexity assumption and has no tolerance, so a
    search that stops early lands measurably below it.

    Args:
        a: One element.
        b: The other.
        axis: `"x"` or `"y"`.
        samples: How many positions to probe across the facing band.

    Returns:
        The widest signed overlap found, or None if the boxes share no
        band on the other axis.
    """
    other = "y" if axis == "x" else "x"
    alo, ahi = canvas._extent(a, other)
    blo, bhi = canvas._extent(b, other)
    lo, hi = max(alo, blo), min(ahi, bhi)
    if hi < lo:
        return None
    best = None
    for i in range(samples):
        u = lo + (hi - lo) * i / (samples - 1)
        sa, sb = canvas.shape_span(a, axis, u), canvas.shape_span(b, axis, u)
        if sa is None or sb is None:
            continue
        v = min(sa[1], sb[1]) - max(sa[0], sb[0])
        if best is None or v > best:
            best = v
    return best


class TestShapeOverlapFindsTheMaximum(unittest.TestCase):
    """`_OVERLAP_TOL` cannot loosen without saying so (Task 56 review, F4).

    Every other pin over this primitive reads an INTEGER magnitude out of
    a lint sentence, so the search could lose most of a pixel and every
    one of them would stay green. Measured in the review: the tolerance
    could go from 0.01 to 5.0 — 500x — with all 933 tests passing, and
    only 25.0 turned anything red. A number nothing measures is a number
    that will drift.

    The assertion is ONE-SIDED on purpose. Both forms hunt the same
    maximum of a concave function; a dense scan is a guaranteed LOWER
    bound on it (it can only miss the peak between samples), so a healthy
    search must come in at or above the scan, and a search that stops
    early comes in below. Comparing the two directions would instead
    measure the scan's own sampling error, which is not the subject.

    Headroom, measured: the shipped 0.01 lands 0.002px under the scan
    across these five pairs; 0.5 lands 0.21 under, 1.0 lands 0.46 under,
    5.0 lands 1.50 under. The 0.05 bar below therefore has 25x of room at
    the shipped value and catches every loosening the review tried.
    """

    #: Pairs chosen to stress the search rather than the geometry. The
    #: first is the configuration that killed the candidate-set
    #: prototype the diamond spike rejected: two rhombi at half a width's
    #: offset peak where their FACETS CROSS, which is neither band end
    #: nor either centre line, and reading only those four points gave 0
    #: for a pair overlapping by 50px.
    PAIRS = (("diamond", 0, 0, 200, 200, "diamond", 100, 40, 200, 200),
             ("ellipse", 0, 0, 160, 240, "ellipse", 68, 80, 160, 240),
             ("diamond", 0, 0, 200, 120, "ellipse", 90, 30, 140, 180),
             ("ellipse", 0, 0, 240, 100, "rectangle", 70, 20, 120, 120),
             ("diamond", 0, 0, 100, 300, "diamond", 40, 150, 300, 100))

    def test_the_search_never_lands_below_a_dense_scan(self) -> None:
        """The ternary search finds the band's true widest facing row."""
        for ka, ax, ay, aw, ah, kb, bx, by, bw, bh in self.PAIRS:
            a = {"type": ka, "x": ax, "y": ay, "width": aw, "height": ah}
            b = {"type": kb, "x": bx, "y": by, "width": bw, "height": bh}
            for axis in ("x", "y"):
                ref = _dense_axis_overlap(a, b, axis)
                if ref is None:
                    continue
                got = canvas._axis_overlap(a, b, axis)
                self.assertGreaterEqual(
                    got, ref - 0.05,
                    "%s/%s on %s: the search found %.4f where a 2001-point "
                    "scan of the same band found %.4f — it is stopping "
                    "before the maximum (_OVERLAP_TOL)"
                    % (ka, kb, axis, got, ref))


def _elbow_label_stage(balanced: bool, box_at: str) -> list[dict]:
    """An L-elbow with a bound label, and a foreign box on one candidate spot.

    Three elements, no more: the arrow, its bound label, and the box the
    label may or may not be sitting in. Both elbows turn at a round
    coordinate, the label is a round 40x20, and the box is always 40x30
    offset `(+5, +2)` from the spot it is testing — so the ONLY thing
    that differs between the healthy scene and the two reds is the leg
    lengths and which candidate position the box is placed on.

    `balanced` picks 200+200 legs, where the arc-length midpoint IS the
    corner and both models name the same point (300, 300); the box then
    covers the one position everyone agrees on. Unbalanced picks 400+100,
    where the client centres on the corner (500, 100) and the superseded
    arc-length model named (350, 100) — 150px apart, one label 40px wide,
    so the two boxes they imply do not touch.

    BOTH spots are LITERALS, and `"anchor"`'s stopped being a live
    `arrow_label_anchor` call when the client rule landed (v0.9
    curves fold-in). It had to: this builder feeds a red asserting that
    nothing is drawn at the arc midpoint, and computing that midpoint by
    asking the function under test made the scene follow the fix instead
    of standing still under it. Once the anchor returned the corner the
    two `box_at` arms named the SAME box, and the over-fire red began
    failing by measuring the miss red's scene. A frozen 350 is the
    coordinate the defect actually produced — it is what the red's own
    docstring quotes — and it stays put under every future model.

    The box is always placed 2px BELOW the arrow's own y origin so it
    clears the horizontal stroke, and 5px past the corner in x so it
    clears the vertical one. That matters: a box centred on the corner
    gets caught by the unrelated "arrow passes through this box" check
    (the corner vertex is a point on the stroke), which would make the
    red below pass for a reason that has nothing to do with labels.

    Args:
        balanced: True for 200+200 legs (the models agree), False for
            400+100 (they diverge by 150px).
        box_at: `"drawn"` to put the box on the corner the client paints
            the label on, `"anchor"` to put it on the arc midpoint the
            superseded model named.

    Returns:
        The three-element scene: arrow `ax`, label `t1`, node `foreign`.
    """
    if balanced:
        ox, oy, pts = 100, 300, [[0, 0], [200, 0], [200, 200]]
        corner, arc_mid = 300, 300
    else:
        ox, oy, pts = 100, 100, [[0, 0], [400, 0], [400, 100]]
        corner, arc_mid = 500, 350
    arrow = el(id="ax", type="arrow", x=ox, y=oy, width=pts[-1][0],
               height=pts[-1][1], points=pts, customData={"role": "edge"},
               boundElements=[{"id": "t1", "type": "text"}])
    label = el(id="t1", type="text", x=0, y=0, width=40, height=20,
               text="probe", fontSize=16, containerId="ax",
               originalText="probe")
    spot = corner if box_at == "drawn" else arc_mid
    return [arrow, label,
            el(id="foreign", type="rectangle", x=spot + 5, y=oy + 2,
               width=40, height=30, customData={"role": "node"})]


# ---------------------------------------------------------------------------
# R2-8 RE-OPENED — the bound-label anchor names a point the client does not
# draw on (curated 2026-08-15 from `spike-row26-verify.md`, which built this
# configuration in a real browser, and `spike-blind1-label-anchor.md`, which
# measured the client's whole rule).
#
# `arrow_label_anchor` walks arc length over the path and centres the label
# on the halfway point. The client does not do that in any branch. It
# branches on the PARITY of `points.length`: odd -> the raw middle vertex,
# converted to global coords, with leg lengths never entering; `n == 2` ->
# the midpoint; even `n >= 4` -> the chord midpoint of the one middle
# segment, or that span's bezier arc midpoint under roundness. Whole-path
# arc length is used nowhere. On a 3-point elbow the client therefore
# centres on the CORNER, unconditionally, and the more unbalanced the legs
# the further that is from the arc midpoint we measure.
#
# Confirmed by live measurement, not by reading the bundle: the unbalanced
# elbow below was seeded through `canvas.py apply`, loaded in the real app,
# and `window.excalidrawAPI.getSceneElements()` reported the label centred
# at (500, 100) — the corner, to the pixel — where `arrow_label_anchor`
# says (350, 100). A ported parity rule reproduces the client at 0.0000px
# at `roughness=0` on every probe shape.
#
# CORPUS COST, and the reason this is not a curiosity: 107 bound arrow
# labels across the 58 artifacts on this branch. 69 sit on straight 2-point
# arrows where every model agrees, which is why the median divergence is
# 0.0px and why five assessment rounds saw nothing. Of the 38 on
# multi-segment arrows, 37 are mislocated by more than 8px — max 331px, on
# `r-object-grouping` — today, with no curvature anywhere in the build. The
# "10 of 72, 13-49px" recorded in `arrow_label_slot`'s own docstring
# compares two server-side estimates against each other; neither of them
# against the thing on screen.
#
# BOTH DIRECTIONS ARE PINNED because one model error produces two opposite
# failures and a fix that buys one by selling the other is not a fix: the
# check is silent where the label really is, and it fires where the label
# is not. The live half is the balanced elbow, where the two models happen
# to coincide — that is the ONLY configuration in which today's code and
# the client name the same point, so it is the only honest healthy scene
# available, and it is what stops a check that has simply died from reading
# as either red flipping.
#
# Owner: addendum wave (label model port). Not the curator's: the fix
# replaces `_arc_midpoint` on the label path with the client's parity rule
# in `canvas.py`, and it has a coordination cost with `_label_off_corner`
# worth knowing before anyone starts — once the base point is the corner,
# that function's `clear()` gate is False by construction on every 3-point
# elbow, taking its bias from 10 corpus labels to 36.
#
# RESOLVED by the v0.9 curves fold-in, and it landed as scoped: both reds
# above flipped on the one edit, `_arc_midpoint` is gone from the label
# path, and `_client_label_point` reproduces all four client branches —
# measured at 0.0000px against the live values this block quotes. The
# coordination cost was real and was paid in the same change:
# `_label_off_corner` was rewritten to slide by arc length along the DRAWN
# path, and the corpus population it biases moved from 10 labels to 12 at
# 18.0-29.3px (not the 36 predicted here — the predicted figure counted
# every 3-point elbow, and a minimal slide leaves the ones whose label
# already clears its own turn alone).
#
# What this block did NOT fix, stated because the two are next-door
# neighbours and the confusion is cheap to make: the corner bias still
# counts VERTICES where it means TURNS. That is the class immediately
# below, it is still red, and the parity port neither helped nor hurt it —
# see the measurement recorded there.
# ---------------------------------------------------------------------------


class TestLabelAnchorAgainstTheDrawnPosition(unittest.TestCase):
    """The lint measured a point on the path; the client paints the corner."""

    def test_a_box_on_the_agreed_position_is_reported(self) -> None:
        """The live half: balanced legs, one position, and the check fires.

        Ungated and asserted in every commit. Both reds below are claims
        about WHERE the check looks, and neither could tell "looks in the
        wrong place" apart from "does not look at all" on its own — a
        `label_on_foreign_node` that stopped emitting the warning
        entirely would turn the first red green and read as the fix. On
        200+200 legs the arc midpoint IS the corner, so this scene is the
        same three elements with the disagreement removed.
        """
        self.assertTrue(_says_lies_on(_elbow_label_stage(
            balanced=True, box_at="drawn")))

    def test_red_a_box_on_the_drawn_label_is_not_reported(self) -> None:
        """FLIPPED by the v0.9 curves fold-in. Kept its red-era name.

        The direction was a MISS, and the magnitude was the whole
        divergence: the client draws the label centred on the corner
        (500, 100), box `[480,520]x[90,110]`, and `foreign` at
        `[505,545]x[102,132]` covers 15px of it in x and 8px in y, both
        past this check's own `>8`/`>4` thresholds. Both channels
        `label_boxes` measured — anchor and slot — sat at (330, 90),
        150px away, so the check compared the box against empty canvas
        and passed. This exact scene was built in a running browser and
        observed silent while the label was visibly inside the box.

        What landed: `arrow_label_anchor` stopped walking whole-path arc
        length and now ports the client's own rule — it branches on the
        PARITY of `points`, so this 3-point elbow centres on its corner
        the way every other model in the pipeline already did not. The
        port reproduces the live client at 0.0000px on this scene: the
        anchor returns (480, 90), whose centre is the corner to the
        digit. `label_boxes`' channel `[0]` therefore hands the check the
        rect the client actually paints, and the overlap it was blind to
        is the one it now measures.

        The check reads BOTH channels, so this flip is not the slot's
        doing: `arrow_label_slot` puts the export's copy at (480, 108),
        18px down the vertical leg, and that rect overlaps `foreign`
        too. Either channel alone would fire here, which is the property
        the R2-8 comment in `lint_layout` asks for.

        The box is deliberately clear of both stroke segments. Centred on
        the corner it also trips the "arrow passes through foreign"
        routing check, which is a real but coincidental net for large
        boxes on this shape — and would make this red pass without
        anything about labels being fixed.
        """
        self.assertTrue(
            _says_lies_on(_elbow_label_stage(balanced=False,
                                             box_at="drawn")),
            "the label is drawn inside 'foreign' and nothing warns")

    def test_red_a_box_on_the_arc_midpoint_is_reported_anyway(self) -> None:
        """FLIPPED by the v0.9 curves fold-in. Kept its red-era name.

        The direction was an OVER-FIRE, and it was the same defect read
        from its other end, which is why it was pinned beside the miss
        rather than instead of it. Nothing is drawn in this box; the
        label is at the corner, 150px along the path. An agent told to
        "re-route the arrow or shorten the label" here would have been
        repairing a picture that was already right, and a fix that
        widened the check to catch the miss without moving the model
        would have produced more of exactly this.

        What landed is the same one edit the miss above names — the two
        reds were always one defect — and this pole is the evidence that
        it moved the MODEL rather than loosening the check: the anchor
        left (350, 100) instead of the check learning to forgive it.
        Both channels now sit on the corner, this box is 150px of empty
        canvas, and `_says_lies_on` returns `[]`.

        Its scene needed a repair to keep saying that, recorded on
        `_elbow_label_stage`: the 350 was being read out of
        `arrow_label_anchor` at build time, so the box tracked the fix
        onto the corner and this assertion started measuring the scene
        one method up. The coordinate is frozen now.
        """
        self.assertEqual(
            _says_lies_on(_elbow_label_stage(balanced=False,
                                             box_at="anchor")), [],
            "no label is drawn at the arc midpoint, and the box there is "
            "reported as having one land on it")


def _labelled_path(points: list[list[float]]) -> tuple[dict, dict]:
    """An arrow with those points and a 60x20 bound label, at the origin.

    Deliberately origin-anchored and unbound at both ends: the corner
    bias reads `segs` and the label's half-extents and nothing else, so
    nodes, bindings and a non-zero origin would all be scenery.

    Args:
        points: The arrow's points, in its own coordinates.

    Returns:
        `(arrow, label)`, ready for `arrow_label_anchor` /
        `arrow_label_slot`.
    """
    return (el(id="ax", type="arrow", x=0, y=0, width=400, height=400,
               points=points, customData={"role": "edge"},
               boundElements=[{"id": "t1", "type": "text"}]),
            el(id="t1", type="text", x=0, y=0, width=60, height=20,
               text="label", fontSize=16, containerId="ax",
               originalText="label"))


# ---------------------------------------------------------------------------
# The corner bias counts VERTICES where it means TURNS (curated 2026-08-15,
# found by `spike-blind4-corner-slide.md` §5 while measuring something else).
#
# `_label_off_corner` exists to keep a turn out from under the label's
# backdrop: on a straight run the two stubs either side of the break are
# collinear and the eye completes them, on a corner they are not. Its
# `corners` list is `segs[:-1]` — every interior vertex — and an interior
# vertex is not the same thing as a turn. A path may store a waypoint in
# the middle of a dead-straight run, and there the stubs are ALREADY
# collinear: the condition the bias exists to repair is satisfied before it
# does anything.
#
# The subject is the reviewer's own five-point fixture, which is the
# fixture `test_the_host_segment_is_adjacent_to_the_offending_turn`
# (test_backend.py) uses to anchor Ruling 1's exactness guarantee. Its
# "offending turn" at (400, 200) sits between (400, 0) and (400, 400) —
# the path runs straight through it. The label slides 18px for nothing.
#
# HARMLESS TODAY and pinned anyway, for two reasons. First, 18px on a
# straight run costs nothing a reader can see, but the rule it reveals is
# that label placement depends on how a path was STORED rather than on how
# it is DRAWN, and this repo's standing doctrine is that the picture is the
# fact. Two point lists that trace the same stroke get different labels.
# Second, the class grows: fillets insert vertices, and curvature makes
# every stored vertex a place the drawn path bends by some non-zero amount,
# so the population of "vertices that are not turns" is exactly what the
# next two features manufacture.
#
# The fix named by the spike is a turn-angle test on the vertex — roughly
# `> 5deg` — before it joins `corners`. It belongs to whoever owns
# `_label_off_corner`, NOT to the curator. Distinct from the label-anchor
# family above and deliberately in its own class: that one is about which
# point the label is centred on (`arrow_label_anchor`, the client's parity
# rule), this one is about whether the bias should fire at all
# (`_label_off_corner`, the trigger). The spikes call them blind spots 1
# and 4 and document the seam between them; a fix to either leaves the
# other exactly as it is.
# ---------------------------------------------------------------------------


class TestCornerBiasReadsVerticesNotTurns(unittest.TestCase):
    """A waypoint on a straight run is not a corner, and needs no clearance."""

    def _slot_offset(self, points: list[list[float]]) -> float:
        """How far the bias slides the label off the arc midpoint.

        Args:
            points: The arrow's points, in its own coordinates.

        Returns:
            The distance in px between `arrow_label_anchor` and
            `arrow_label_slot` — 0.0 when the bias did not fire.
        """
        arrow, label = _labelled_path(points)
        mx, my = canvas.arrow_label_anchor(arrow, label)
        sx, sy = canvas.arrow_label_slot(arrow, label)
        return ((sx - mx) ** 2 + (sy - my) ** 2) ** 0.5

    def test_a_real_turn_under_the_label_still_slides_it(self) -> None:
        """The live half: a 90-degree corner is what the bias is FOR.

        Ungated. The red below asserts the bias stays still, and a bias
        that had stopped firing altogether would satisfy it while
        re-opening r5-14 — six labels across three shipped artifacts with
        their connectors reading as two stubs pointing nowhere near each
        other. On a balanced 200+200 elbow the anchor IS the turn, so
        this is the trigger at its most certain.

        RE-DERIVED from 38px to 18px by the v0.9 curves fold-in, and the
        magnitude is the whole point of the rewrite rather than a
        tolerance being relaxed. 38 was the label's HALF-WIDTH plus
        `LABEL_CORNER_PAD` — the old rule picked a host segment and slid
        along it, and the segment it picked was the horizontal leg, so
        the label had to travel its own long axis to get clear. The rule
        now walks the drawn path outward from the anchor and stops at the
        first position that clears, which finds the vertical leg at 18px:
        the half-HEIGHT plus the same pad. Same turn, same clearance,
        20px less motion, and the number is still exact — a bias that
        died still reads 0.0 and still fails here.
        """
        self.assertAlmostEqual(
            self._slot_offset([[0, 0], [200, 0], [200, 200]]), 18.0, places=3)

    @unittest.expectedFailure
    def test_red_a_collinear_waypoint_is_treated_as_a_corner(self) -> None:
        """Two point lists, one stroke, two different label positions.

        RED BY ASSERTION. The direction is an OVER-FIRE and the magnitude
        is 18px — `hh + LABEL_CORNER_PAD` for this 60x20 label — bought
        against a turn of 0 degrees. Both paths trace the identical
        U-shaped polyline and both put the arc midpoint at the same
        (370, 190); dropping the redundant vertex at (400, 200) is not a
        change to the drawing, and the label moves anyway.

        Asserted as an equality between the two paths rather than as
        `offset == 0` on the five-point one, because that is the actual
        claim: the label's position is a function of the stroke, not of
        how many points were stored along it. Flips when the vertex is
        tested for an actual turn before it counts as a corner.

        Sharp geometry throughout, deliberately — under roundness the two
        point lists WOULD draw different strokes (Catmull-Rom reads every
        stored point), so this comparison is only available while the era
        is sharp, and the defect it names is the one that grows when it
        stops being.

        STILL RED after the v0.9 curves fold-in, measured rather than
        assumed. That change rewrote `_label_off_corner` end to end — the
        host-segment pick is gone and the slide walks arc length along
        the drawn path — so the obvious question was whether the new
        mechanism happens to drop collinear vertices on the way. It does
        not: the five-point path still slides 18.0000px and the four-point
        one still slides 0.0000px, both anchored at the identical
        (370, 190), which is this assertion's two operands unchanged to
        four places. Nothing moved because the rewrite replaced WHERE the
        label slides to, not WHICH vertices it slides to clear —
        `corners` is still every interior vertex. The fix named above is
        still the fix, still a turn-angle test on the vertex, and still
        belongs to whoever owns `_label_off_corner`.
        """
        self.assertAlmostEqual(
            self._slot_offset([[0, 0], [400, 0], [400, 200], [400, 400],
                               [0, 400]]),
            self._slot_offset([[0, 0], [400, 0], [400, 400], [0, 400]]),
            places=3)


class TestTextOverlapAndClearanceQuietHalves(unittest.TestCase):
    """The quiet halves of Task 23's two checks, which its mutants cannot reach.

    Both catalogue entries assert a FIRE, and `unroled_text_over_node`'s
    neighbour asserts a fire too — the roled scene answering in the
    annotation voice. That pair isolates the role gate exactly, and it is
    blind in one direction by construction: a text/node check that fired
    on EVERY text, overlap or not, satisfies the mutant AND satisfies the
    neighbour, because the neighbour only asks that the annotation arm
    speak on a scene where it should. The over-fire is what "16 warnings,
    13 false" was made of, so it is asserted here directly.

    `min_clearance`'s intent channels are the same shape of gap. Its
    neighbour proves a 60px gap is silent; nothing in the catalogue
    proves a 4px gap goes quiet when the drawing SAYS the tightness is
    deliberate, and a check whose escape hatches do not work is a check
    that gets muted wholesale in week one — the Cloud contradiction, in
    the exact words of the mutant that asked for these channels.
    """

    def _lint(self, els: list[dict], **kw: Any) -> list[str]:
        """Every lint line for a scene, across all three channels.

        Args:
            els: The scene's element list.
            **kw: Passed through to `canvas.lint_layout`.

        Returns:
            errors + warnings + notes, concatenated.
        """
        lint = canvas.lint_layout(els, artifact_type="flow", **kw)
        return lint["errors"] + lint["warnings"] + lint["notes"]

    # Both filters below match a FIXED phrase of the template and never
    # the detector regex, and that is the whole reason this class is not
    # vacuous. `_TEXT_ON_NODE_RE`/`_MIN_CLEARANCE_RE` read a magnitude as
    # `\d+`, which is right for them — `FindingSpec` should demand a
    # well-formed number — but it makes them blind to exactly the failure
    # these tests exist to catch. A check that over-fires on a text 40px
    # CLEAR of the node computes a negative overlap and says "covers n1 —
    # -40x20px of overlap (-800px²)": a real, loud, wrong warning that the
    # detector regex cannot match. Filtering through it would compare
    # [] == [] and pass. Caught in review of the first round of this task,
    # by deleting the overlap guard and watching all nine tests stay green.
    _COVERS = "and nothing marks it as belonging there"
    _CROWDS = "(spacing floor "

    def _covers(self, els: list[dict]) -> list[str]:
        """Lint lines from the role-blind text/node arm, well-formed or not.

        Args:
            els: The scene's element list.

        Returns:
            The matching lines, one per claimed overlap.
        """
        return [ln for ln in self._lint(els) if self._COVERS in ln]

    def _crowds(self, els: list[dict], **kw: Any) -> list[str]:
        """Lint lines from the near-miss arm, well-formed or not.

        Args:
            els: The scene's element list.
            **kw: Passed through to `canvas.lint_layout`.

        Returns:
            The matching lines, one per crowded pair.
        """
        return [ln for ln in self._lint(els, **kw) if self._CROWDS in ln]

    def test_a_text_beside_a_node_is_not_called_an_overlap(self) -> None:
        """The role-blind arm has a quiet half: 40px clear says nothing."""
        scene = _text_over_node(roled=False)
        scene[1]["x"] = 240        # node ends at 200
        self.assertEqual(self._covers(scene), [])

    def test_a_bound_label_inside_its_own_container_is_not_an_overlap(
            self) -> None:
        """A label rides its container; reporting it names the wrong element.

        This is the exemption the fixture replay measured as load-bearing
        — the corpus holds 363 bound texts, and without this the arm
        invents three findings about sticky notes captioning themselves.
        """
        scene = _text_over_node(roled=False)
        scene[1]["containerId"] = "n1"
        self.assertEqual(self._covers(scene), [])

    def test_a_bound_label_over_a_FOREIGN_node_is_still_exempt(self) -> None:
        """And the container exemption is wholesale, unlike the composed one.

        A bound label is placed by the RENDERER, which re-centres it in
        its container — so it cannot drift, and a label over a foreign
        node means the CONTAINER is over that node, which the shape pair
        loop reports about the container. Scoping this exemption to the
        named container is what re-introduces the three sticky-note
        findings the corpus replay measured.
        """
        scene = _text_over_node(roled=False)
        scene[1]["containerId"] = "far-away-box"
        self.assertEqual(self._covers(scene), [])

    def test_composed_content_inside_its_owner_is_not_an_overlap(self) -> None:
        """A KPI value over its own card is the design, not a defect.

        `value_of`/`attr_of` content is emitted inside its owner and
        banded above it deliberately (v0.9 z-banding), so the overlap is
        the point of it.
        """
        scene = _text_over_node(roled=False)
        scene[1]["customData"] = {"value_of": "n1"}
        self.assertEqual(self._covers(scene), [])

    def test_composed_content_that_DRIFTED_off_its_owner_is_reported(
            self) -> None:
        """The composed exemption is scoped to the owner it names, and only it.

        Composed rows are placed by us and STORED, so unlike a bound
        label they really can end up somewhere nobody meant — a value
        sitting on the card next door is the v0.5 R2-10 drift class, the
        one the decoration-drift check exists for, wearing a text. The
        first round of this task exempted the tag wholesale and made that
        unreportable by construction; caught in review.

        Asserted on all three tag kinds, because they are three separate
        `dict.get` calls and a scoping fix can land on one of them.
        """
        for tag in ("value_of", "attr_of", "parent"):
            with self.subTest(tag=tag):
                scene = _text_over_node(roled=False)
                scene[1]["customData"] = {tag: "far-away-box"}
                self.assertEqual(len(self._covers(scene)), 1,
                                 "a %s text drifted onto n1 goes unreported"
                                 % tag)

    def test_a_flush_pair_is_a_stack_and_not_a_near_miss(self) -> None:
        """Zero gap is a decision; the sliver above zero is the mistake.

        Both tearsheet fixtures stack their sections edge to edge, which
        is why exactly-flush is silent rather than the tightest possible
        finding.
        """
        self.assertEqual(self._crowds(_near_miss_pair(gap=0)), [])

    def test_the_documented_twelve_pixel_gutter_is_silent(self) -> None:
        """references/layout.md's wireframe row pitch is not a defect.

        The floor is 8 and not 12 precisely so that this gap has a grid
        unit of headroom rather than sitting on the threshold: measured
        against the 24 fixture artifacts, a floor of 16 turns fourteen of
        these into findings and every one of them is false.
        """
        self.assertEqual(self._crowds(_near_miss_pair(gap=12)), [])

    def test_a_corner_to_corner_pair_is_not_crowding(self) -> None:
        """Boxes offset on both axes are neighbours, however near.

        They share no band, so there is no sliver of ground between them
        for a reader to misread — the gap is diagonal and reads as layout.
        """
        scene = _near_miss_pair(gap=4)
        scene[1]["y"] = 120        # n1 is 60 tall: no shared band at all
        self.assertEqual(self._crowds(scene), [])

    def test_declared_nesting_exempts_a_tight_pair(self) -> None:
        """Intent channel 1: `customData.parent` inherited from the pair loop.

        A card declared inside its shelf is a composition, and the loop
        head has skipped that pairing since v0.3 — putting the crowding
        arm inside that loop is what buys it.
        """
        scene = _near_miss_pair(gap=4)
        scene[1]["customData"] = {"role": "node", "parent": "n1"}
        self.assertEqual(self._crowds(scene), [])

    def test_a_decoration_beside_a_node_exempts_a_tight_pair(self) -> None:
        """Intent channel 2: furniture roles never enter the pair loop.

        A badge pinned against a card is the mutant's own example of a
        deliberate near-touch, and `shapes` has excluded decorations from
        this loop all along.
        """
        scene = _near_miss_pair(gap=4)
        scene[1]["customData"] = {"role": "decoration"}
        self.assertEqual(self._crowds(scene), [])

    def test_a_recorded_waiver_exempts_a_tight_pair(self) -> None:
        """Intent channel 3: the escape the check shipped WITH, not after.

        The key the finding prints is the key the waive answers — asserted
        here rather than assumed, because a waiver hint naming a key
        nothing reads is the same silence wearing a helpful sentence.
        """
        scene = _near_miss_pair(gap=4)
        [line] = self._crowds(scene, aid="a1")
        key = re.search(r"key: '([^']+)'", line).group(1)
        self.assertEqual(key, "clear:a1:n1:n2")
        self.assertEqual(
            self._crowds(scene, aid="a1",
                         waives={key: {"reason": "badge sits tight"}}), [])


class TestMermaidRoundTripIdentity(unittest.TestCase):
    """`--relayout` matches nodes by id, never by label — pinned green."""

    def test_flow_to_mermaid_carries_element_identity(self) -> None:
        """Every node reaches the mermaid text as `n_<element id>`.

        No defect claimed: this is the protection itself, pinned so it
        cannot be refactored away quietly. `--relayout` round-trips a flow
        through mermaid and maps dagre's answer back with
        `sk["id"][2:] in ix` (canvas.py), so identity lives in that
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
# `render_svg` discarded it. It painted in four type-buckets (frames, then
# arrows/lines, then other shapes, then text), so order
# held WITHIN a bucket and was thrown away ACROSS them. A decoration at index
# 0 — declared behind everything — was painted last, over the connector it was
# meant to sit behind, and erased it. FIXED in v0.9 WP4 (Task 21): the
# dispatch is one walk of `live` in array order, and the pin below flipped
# green. The section is kept in its entirety because the tests are the
# regression cover for it, and because the reasoning under "Why this was not
# a cosmetic export bug" is what makes them worth keeping.
#
# Measured as EMISSION ORDER in the SVG string, not as pixels: later markup
# paints over earlier markup, the string is cheap to assert, and no browser
# is needed to say which of two elements went down first. The render tier
# would say the same thing in pixels and is the natural second home for this
# (see the note on the red below) — but the render tier's own substrate is
# this very SVG, which is why the model-tier pin comes first.
#
# Why this was not a cosmetic export bug. `tests/test_mutants_render.py`
# rasterizes this SVG, rasterizes it again with one element omitted, and
# diffs. An element occluded ONLY by the bucketing contributes zero pixels,
# so ablating it changes nothing, so `ablation_existence` concludes a plainly
# visible element is invisible. The tier whose whole claim is that it reads
# the picture rather than the model could read an occlusion that was not on
# the canvas. `export` (the user's handover artifact) and `snapshot` tier-3 (a
# headless agent's only eyes) read the same string.
#
# canvas.py already commented a "backing painted under arrow" special case
# for label backdrops: the paint-order problem was seen, solved pointwise for
# one element, and never generalized. That backdrop is still there and is
# still pointwise, and deliberately so — it is not a z-order workaround but a
# stroke-breaking effect the client performs and SVG has no notion of, so it
# has no array position to express. Generalizing the DISPATCH was the fix;
# the backdrop was never the thing to generalize.
# ---------------------------------------------------------------------------

# A fill no other part of a render emits, so its first occurrence in the SVG
# is the decoration's own tag and nothing else's — the ground rect
# (canvas.py's SVG_GROUND) and every default style are different colours.
_DECOR_FILL = "#ffd8a8"

# The same trick for a NODE, so a frame and the node it contains can be told
# apart in one string. The frame's own markup has no element-controlled
# colour to look for — `paint` hard-codes #94a3b8 for its dashed border and
# #64748b for its name — so the frame is found by that border colour and the
# node by this fill, and neither string can be the other's.
_NODE_FILL = "#a8d8ff"


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
            is the EXPORT-COMPLETENESS defect — `TestExportCompleteness`'s
            subject, green since v0.9 WP4 and a recurrence all the same if
            this ever fires — and a -1 would quietly sort to the bottom of
            the stack and read as a paint-order answer.
    """
    svg = canvas.render_svg(scene)[0]
    decor, stroke = svg.find(_DECOR_FILL), svg.find("<polyline")
    if decor < 0 or stroke < 0:
        raise ValueError(
            "the scene emitted no %s at all — that is export "
            "completeness, not paint order"
            % ("decoration" if decor < 0 else "connector"))
    return decor, stroke


def _framed_child_scene(frame_first: bool) -> list[dict]:
    """A frame and the one node inside it, stacked either way round.

    The smallest drawing that can state the frame half of the contract:
    one frame, one member node filling most of it, and nothing else — no
    labels, no siblings, so the only thing that can decide which is
    painted over which is the array order the caller chose.

    The node overlaps the frame's TOP-LEFT corner deliberately: that is
    where `paint` puts a frame's name text, so a frame painted late lands
    both its dashed border and its caption on the member's own ink.

    Args:
        frame_first: True to declare the frame at index 0, where
            `normalize_z_order` bands it (band 0) and where it must stay
            under its children; False to declare it after the node.

    Returns:
        The two-element scene, in the requested array order.
    """
    frame = el(id="f1", type="frame", x=0, y=0, width=200, height=100,
               name="Lane")
    node = el(id="n1", type="rectangle", x=0, y=0, width=160, height=60,
              backgroundColor=_NODE_FILL, frameId="f1",
              customData={"role": "node"})
    return [frame, node] if frame_first else [node, frame]


def _frame_offsets(scene: list[dict]) -> tuple[int, int]:
    """Where the frame and its member node land in the emitted SVG.

    Args:
        scene: A scene built by `_framed_child_scene`.

    Returns:
        `(frame_offset, node_offset)` as character positions in the
        rendered SVG. The LARGER offset is painted later, and so on top.

    Raises:
        ValueError: If either element emitted no markup at all — export
            completeness rather than paint order, said out loud for the
            reason `_paint_offsets` says it.
    """
    svg = canvas.render_svg(scene)[0]
    frame, node = svg.find("#94a3b8"), svg.find(_NODE_FILL)
    if frame < 0 or node < 0:
        raise ValueError(
            "the scene emitted no %s at all — that is export "
            "completeness, not paint order"
            % ("frame" if frame < 0 else "node"))
    return frame, node


def _bound_label_scene(label_last: bool) -> list[dict]:
    """A connector and its bound label, stacked either way round.

    The smallest drawing that can state the bound-label half of the
    contract. `make_element` emits a container immediately before its
    bound label, so the product's own path always produces the
    `label_last=True` order — but a client save carries client order and
    never passes `normalize_z_order`, so the other order is reachable
    without any op saying so.

    What is at stake is not only the glyphs. A label bound to an arrow is
    painted over an opaque ground rect, because the client breaks the
    stroke behind the label and this renderer has no such notion
    (canvas.py `paint`, the `arrow_ids` branch). Emitted BEFORE the
    arrow, backdrop and glyphs both go down first and the stroke is drawn
    straight across them: the r5-14 "connector struck through its own
    label" picture, arrived at by ordering instead of by placement.

    Args:
        label_last: True for the product's order, arrow then label;
            False to put the label first, where the stroke covers it.

    Returns:
        The two-element scene, in the requested array order.
    """
    arrow = el(id="a1", type="arrow", x=0, y=0, width=200, height=0,
               points=[[0, 0], [200, 0]], customData={"role": "edge"},
               boundElements=[{"id": "t1", "type": "text"}])
    label = el(id="t1", type="text", x=70, y=-10, width=60, height=20,
               text="then", originalText="then", fontSize=16,
               textAlign="center", containerId="a1")
    return [arrow, label] if label_last else [label, arrow]


def _label_offsets(scene: list[dict]) -> tuple[int, int]:
    """Where the connector and its bound label land in the emitted SVG.

    Args:
        scene: A scene built by `_bound_label_scene`.

    Returns:
        `(connector_offset, label_offset)` as character positions in the
        rendered SVG. The label's position is its BACKDROP's, not its
        glyphs': the backdrop is the piece the stroke has to stay off,
        and it is emitted first of the two, so it is the earlier of the
        label's marks and the honest one to compare against.

    Raises:
        ValueError: If either element emitted no markup at all — export
            completeness rather than paint order, said out loud for the
            reason `_paint_offsets` says it.
    """
    svg = canvas.render_svg(scene)[0]
    stroke, glyphs = svg.find("<polyline"), svg.find(">then<")
    backdrop = svg.rfind("fill='%s' stroke='none'" % canvas.SVG_GROUND,
                         0, glyphs) if glyphs >= 0 else -1
    if stroke < 0 or glyphs < 0:
        raise ValueError(
            "the scene emitted no %s at all — that is export "
            "completeness, not paint order"
            % ("connector" if stroke < 0 else "label"))
    if backdrop < 0:
        raise ValueError(
            "the label emitted no ground backdrop — the stroke-breaking "
            "effect this scene is about is not in the markup at all")
    return stroke, backdrop


class TestPaintOrder(unittest.TestCase):
    """The element array is the z-order — or `render_svg` is caught losing it."""

    def test_a_decoration_declared_in_front_is_painted_in_front(self) -> None:
        """The contract's other pole, and it holds: index 1 paints last.

        Green before the fix and green after — which is exactly what
        makes it the control, and it earned that keep. A "fix" that
        simply reversed the buckets, or one that dropped array order in
        the other direction, would have satisfied the flipped test below
        and broken this. Both poles or neither.
        """
        decor, stroke = _paint_offsets(_backdrop_scene(behind=False))
        self.assertGreater(
            decor, stroke,
            "a decoration declared AFTER the connector must be painted "
            "over it")

    def test_array_order_is_honoured_within_one_type_bucket(self) -> None:
        """Order was never lost everywhere — only across bucket edges.

        This is what made the defect below a BUCKETING defect rather than
        a renderer that never read the array: two rectangles landed in one
        bucket, and swapping them swapped the emission. Without this the
        red could have been "read" as `render_svg` having no notion of
        z-order at all, and the fix would have been scoped wrong.

        Kept, and its red-era name kept with it, though v0.9 WP4 left the
        renderer with no buckets to be within: it is now the same claim
        the flipped test below makes, asked of a pair the OLD dispatch
        also got right. That is worth a test of its own — a regression
        that reintroduced type bucketing would break the two of them for
        visibly different reasons, and only this one says the array is
        read at all.
        """
        pair = [el(id="r1", type="rectangle", x=0, y=0, width=100,
                   height=100, backgroundColor="#aaaaaa"),
                el(id="r2", type="rectangle", x=20, y=20, width=100,
                   height=100, backgroundColor="#bbbbbb")]
        svg = canvas.render_svg(pair)[0]
        self.assertLess(svg.index("#aaaaaa"), svg.index("#bbbbbb"))
        swapped = canvas.render_svg(pair[::-1])[0]
        self.assertLess(swapped.index("#bbbbbb"), swapped.index("#aaaaaa"))

    # GONE with the flip below, on purpose and not silently:
    # `test_zorder_red_is_red_by_measurement_not_by_error` guarded this
    # class's one red against `expectedFailure`'s error-masking, and its
    # assertion was the DEFECT — decoration emitted after the connector.
    # v0.9 WP4 fixed that, so the guard could only survive by asserting
    # the bug, which is the one thing a guard must never do. Its own
    # failure message named this removal as the correct response. What
    # replaced its job: nothing needs to, because there is no red left in
    # this class to mask, and `_paint_offsets` still REFUSES a scene where
    # either element emitted nothing rather than sorting a -1 to the
    # bottom of the stack — the error-vs-measurement distinction now lives
    # where it is checked on every call instead of in one guard.

    def test_red_zorder_bucketing_occludes_connector(self) -> None:
        """FLIPPED by v0.9 WP4 (Task 21). Kept its red-era name.

        `{"op": "reorder", "id": "bg-panel", "index": 0}` is documented as
        the way to put a panel behind the drawing, and against this
        renderer it used to do nothing whatsoever: both orderings of the
        same two elements emitted byte-identical markup, because the arrow
        bucket ran before the shape bucket either way. The picture then
        asserted something the model never said — the connector was gone,
        and the reader had no way to be suspicious of a stroke that leaves
        no mark.

        The dispatch now walks `live` once in array order instead of four
        times by type, so the decoration at index 0 is emitted first and
        the arrow paints over it. Asserted as emission order rather than
        as a pixel diff, which is why this pin costs no browser and why it
        is still worth a render-tier sibling: `ablation_existence` could
        not carry that sibling while it was the bug's own victim (§(i.2)),
        and now that the bug is gone, nothing has been written to check
        the same claim in pixels. Filed as a curator candidate, not
        silently assumed covered.
        """
        decor, stroke = _paint_offsets(_backdrop_scene(behind=True))
        self.assertLess(
            decor, stroke,
            "decoration 'bg' is declared at array index 0 — beneath "
            "everything — but its markup is emitted at %d, after the "
            "connector's at %d: it is painted OVER the arrow and erases "
            "it (ops-reference.md:90, :213)" % (decor, stroke))

    def test_a_frame_declared_first_is_painted_under_its_children(self
                                                                  ) -> None:
        """Curator batch 16 item 2 (Task 21 §8.2), 2026-08-14.

        Once the dispatch became a single array walk, a frame's position
        in the stack became purely an array question — and nothing asked
        it. All 11 recorded fixture frames satisfy the invariant and
        `normalize_z_order` bands frames at 0, so the op path is safe;
        this pins that the RENDERER agrees, which is the half array order
        made load-bearing.

        Low blast radius on purpose, and worth saying so rather than
        overselling the pin: a frame is `fill='none'`, so a frame painted
        late does not black out its members. What it does put over them
        is a dashed border and a caption, which is why this scene parks
        the node on the corner where the caption goes.
        """
        frame, node = _frame_offsets(_framed_child_scene(frame_first=True))
        self.assertLess(
            frame, node,
            "frame 'f1' is declared at array index 0 — beneath its own "
            "members — but its markup is emitted at %d, after the "
            "member node's at %d: its dashed border and its name are "
            "painted over the node they contain" % (frame, node))

    def test_a_frame_declared_last_is_painted_over_its_children(self) -> None:
        """The frame contract's other pole, and the control that earns it.

        Without it the pin above is satisfied by a renderer that emitted
        frames first unconditionally — a bucket by another name, and the
        exact thing v0.9 WP4 removed. Both poles or neither.
        """
        frame, node = _frame_offsets(_framed_child_scene(frame_first=False))
        self.assertGreater(
            frame, node,
            "a frame declared AFTER its member must be painted over it")

    def test_a_bound_label_declared_last_is_painted_over_its_arrow(self
                                                                   ) -> None:
        """Curator batch 16 item 3 (Task 21 §8.3), 2026-08-14.

        The same exposure as the frame pin above, on the element pair
        where the picture actually suffers. `make_element` emits a
        container immediately before its bound label, so the product's
        own path is safe — but nothing tested that it stays safe, in
        either direction, and a client save carries client order without
        passing `normalize_z_order`.

        Asserted against the label's BACKDROP rather than its glyphs
        because the backdrop is the thing the ordering defeats: it exists
        to paint the stroke back out from under the label, and a stroke
        emitted after it simply paints back in — the r5-14 struck-through
        connector reached by ordering rather than by placement.

        The render tier cannot carry this one, and that is measured, not
        assumed: at the product's own stroke widths the label's ablation
        ink is 51px in BOTH orders (2026-08-14), because a 2px stroke
        laid across 16px glyphs falls inside `tolerant_diff`'s one-pixel
        slack. The signal only clears the floor at stroke widths
        Excalidraw does not offer (measured 60px vs 12px at sw=6, 144px
        vs 0 at sw=12). Emission order says the same thing exactly, at no
        browser cost.
        """
        stroke, backdrop = _label_offsets(_bound_label_scene(label_last=True))
        self.assertLess(
            stroke, backdrop,
            "label 't1' is declared after arrow 'a1' but its ground "
            "backdrop is emitted at %d, before the stroke at %d: the "
            "stroke is painted back across the label it was broken for"
            % (backdrop, stroke))

    def test_a_bound_label_declared_first_is_painted_under_its_arrow(self
                                                                    ) -> None:
        """The bound-label contract's other pole: the defect, pinned live.

        The control for the pin above — the array is read here too, not
        special-cased for bound text — and simultaneously the record of
        what the bad order looks like, so a future reader can see that
        the renderer really will draw the stroke over the label when told
        to. This is why the invariant is worth holding upstream, in
        `normalize_z_order`, which the pin below covers.
        """
        stroke, backdrop = _label_offsets(_bound_label_scene(label_last=False))
        self.assertGreater(
            stroke, backdrop,
            "a label declared BEFORE its arrow must be painted under it")

    def test_normalize_z_order_bands_the_whole_drawing(self) -> None:
        """The z-model itself, pinned — it had no test at all until now.

        Curator batch 16, items 2 and 3 share this one: the renderer
        honours whatever order it is handed (the pins above), so the
        thing that makes a frame sit under its children and a bound label
        over its arrow is this function and nothing else. It is what
        v0.9 WP4's dispatch defers to, and every band in it was
        unasserted.

        Declared here in reverse so a sort that did nothing at all would
        fail rather than pass by luck — with the within-band TIES as the
        deliberate exception, declared in the order they must keep,
        because the docstring's "stable" is load-bearing: an explicit
        `reorder` is meant to survive inside a band, and only a tie can
        show that. There are three of them now (`p1`/`t1` at the top,
        `c1`/`w1` and `b1`/`k1` in the part band).

        **Extended by task 45, and the extension is a MOVE — read this
        before restoring the old expectation.** Task 44 split the
        decoration band and lifted composed CONTENT only, so `w1` (a
        `body_of` wave) sat in band 1 beside the standalone backdrop
        `d1`, and this test said so. That position was recorded then as
        TODAY'S RATIFIED BOUNDARY rather than as a contract, and it was
        the model-tier half of a disagreement the catalogue held on
        purpose against curator batch 17's render red. Task 45 settled
        the disagreement in the red's favour, so `w1` now bands at 4
        with the rest: a composed part is drawn ON its owner, and there
        is no part tag for which "beneath the owner's fill" is right —
        an opaque owner painted out the very glyph carrying the
        control's STATE, drawing a CHECKED checkbox as an unchecked one.
        What did NOT move, and is the whole reason the band still keys
        on a positive tag list rather than on the role: `d1`, an
        UNTAGGED `role: decoration`, is a standalone BACKDROP and stays
        in band 1 beneath the arrow it backs.

        The `d1`/`w1` tie that used to pin "furniture keeps its declared
        order against its own box" (task 21 §6's gain) is gone with the
        move — those two are no longer in one band — so it is replaced
        in kind and not dropped: `b1` (`box_of`) and `k1` (`chk_of`) are
        a checkbox's actual box and check stroke, declared in that
        order, and the sort must leave the stroke painting over the box.
        That is the same claim on the element pair it was always about.

        Residual gap, stated so nobody reads this as full cover: this
        pins what the function COMPUTES, and the function runs on the op
        and tidy paths only (canvas.py:3639, :10355). A user save carries
        client order straight to disk — the catch-up path says so in as
        many words — so a frame appended after its children by the client
        is still reachable, and no check anywhere refuses it. That is a
        product decision (nothing normalizes on the save path), not a
        defect this pin can encode.
        """
        scene = [
            el(id="p1", type="rectangle", x=0, y=0, width=10, height=10,
               customData={"role": "pin"}),
            el(id="t1", type="text", x=0, y=0, width=40, height=20,
               text="then", containerId="a1"),
            el(id="c1", type="text", x=0, y=0, width=40, height=20,
               text="42%", customData={"role": "decoration",
                                       "value_of": "n1"}),
            el(id="n1", type="rectangle", x=0, y=0, width=100, height=50,
               customData={"role": "node"}),
            el(id="a1", type="arrow", x=0, y=0, width=100, height=0,
               points=[[0, 0], [100, 0]], customData={"role": "edge"}),
            el(id="d1", type="rectangle", x=0, y=0, width=100, height=50,
               customData={"role": "decoration"}),
            el(id="w1", type="line", x=0, y=0, width=80, height=3,
               points=[[0, 0], [80, 0]],
               customData={"role": "decoration", "body_of": "n1"}),
            el(id="b1", type="rectangle", x=0, y=0, width=16, height=16,
               customData={"role": "decoration", "box_of": "n1"}),
            el(id="k1", type="line", x=0, y=0, width=12, height=12,
               points=[[0, 0], [12, 12]],
               customData={"role": "decoration", "chk_of": "n1"}),
            el(id="f1", type="frame", x=0, y=0, width=200, height=100,
               name="Lane")]
        self.assertEqual(
            [e["id"] for e in canvas.normalize_z_order(scene)],
            ["f1", "d1", "a1", "n1", "c1", "w1", "b1", "k1", "p1", "t1"],
            "layout.md's paint order is frames -> backdrops -> "
            "arrows/lines -> nodes -> composed parts -> bound labels & "
            "pins, and the sort is stable within a band")

    def test_a_backdrop_decoration_still_bands_beneath_what_it_backs(self
                                                                     ) -> None:
        """The control task 44's split owes: backdrops did NOT come up.

        The pin above reads ids out of a sort; this reads the PICTURE,
        and it is the pole that fails if the split is done by lifting the
        whole ROLE. That is the property this test holds, and it is the
        one that still matters: an untagged decoration must stay down.
        It is held here in pixels and by `d1` in the pin above.

        WHAT THIS NO LONGER COVERS, corrected at task 45 rather than left
        to be quoted stale. Task 44's review wrote this paragraph to
        record which test caught which lazy variant, and named the pin
        above — through its `w1` (`body_of`) member — as the one catching
        "lifting every `*_of`-tagged part". Task 45 made that variant,
        behaviourally, the SHIPPED rule: `w1` is in band 4 by design now,
        so the pin cannot catch it and neither can this test. Nothing in
        the suite does, because on today's eight-tag vocabulary a
        `*_of` suffix test and `COMPOSED_PART_KEYS`' positive
        enumeration sort every element identically.

        That is a real and deliberate residual, not an oversight: the two
        rules diverge only when a NINTH part tag is coined and not added
        to the list, and what defends that today is the comment on
        `COMPOSED_PART_KEYS` naming the `_deco` call sites as the
        enumeration — prose, not a test. A pin is available and cheap if
        it is wanted: give this class's banding scene a `role:
        decoration` carrying an invented tag (`{"future_of": "n1"}`) and
        expect it to stay in band 1, which fails the suffix test and
        passes the enumeration.

        The scene is `_backdrop_scene`'s covering pole — an opaque
        decoration panel declared AFTER the connector it is meant to sit
        behind, which is the arrangement layout.md prescribes for
        parallel edges — put through the banding rather than rendered
        raw. The banding is what has to move it back under the stroke,
        and if backdrops had been lifted with the content the panel would
        paint over the connector and erase it, which is the r5 finding
        that made `normalize_z_order` exist.
        """
        decor, stroke = _paint_offsets(
            canvas.normalize_z_order(_backdrop_scene(behind=False)))
        self.assertLess(
            decor, stroke,
            "an untagged `role: decoration` panel is a backdrop: the "
            "banding must put it beneath the connector it backs, but its "
            "markup is emitted at %d, after the stroke's at %d"
            % (decor, stroke))


# ---------------------------------------------------------------------------
# The bounds loop and the paint loop must read the SAME text (curator batch
# 23 item 3, from task 46 §9 C1 and that task's review, 2026-08-15).
#
# Task 46 closed one half of this: `painted_text_lines` made the WRAP one
# rule, so the frame and the ink stopped describing different shapes. The
# other half is still open and is the same defect through a different
# input. `text_dims` computes height as `lines * fontSize * 1.25` with the
# 1.25 written in; `paint` lays the same text out at
# `fontSize * (e.get("lineHeight") or 1.25)`. Give one text a lineHeight
# the client is perfectly willing to store and the two disagree by
# `(lineHeight - 1.25) * fontSize` per line — downward, off the bottom of
# the export, which is the direction that loses content rather than margin.
#
# LATENT, and worth being precise about what that does and does not mean:
# all 454 text elements under `tests/fixtures` carry exactly 1.25, so no
# corpus scene reaches it and no assessment round could have. That makes it
# unreachable TODAY, not harmless — nothing in `canvas.py` writes the field
# defensively, nothing rejects a foreign value on load, and the client's own
# line-height control writes whatever the user picks. Task 46's own regime
# guard named the hazard from the other side before this was written down:
# `test_the_body_text_still_wraps_to_more_lines_than_it_is_bound_for` says a
# `lineHeight` default change is the drift that would most easily silence
# it.
#
# Model tier, not render: this is `render_svg`'s own markup against
# `paint`'s own arithmetic, both stdlib, so it runs in every commit rather
# than behind `MUTANTS_RENDER=1` — the same argument that put
# `TestPaintOrder` above here.
# ---------------------------------------------------------------------------

_LINE_HEIGHT_VIEWBOX = re.compile(r"viewBox='(-?[\d.]+) (-?[\d.]+) "
                                  r"(-?[\d.]+) (-?[\d.]+)'")
# Three short lines at a round size, at the origin, and nothing else in the
# scene: every number below is then `fontSize * lineHeight * lines` and can
# be read without running anything. `\n`-separated rather than wrapped so
# the line COUNT is not also a variable — the wrap is task 46's subject and
# holding it fixed is what keeps this about the line HEIGHT.
_LH_TEXT = "alpha\nbeta\ngamma"
_LH_FONT = 20
_LH_LINES = 3


def _line_height_scene(line_height: float) -> list[dict]:
    """One text element at the origin, at a given line height.

    Args:
        line_height: The element's `lineHeight`. 1.25 is Excalidraw's
            default and the only value the corpus contains; anything
            else is the mutation.

    Returns:
        A one-element scene. `width`/`height` are the STORED extents a
        1.25 text would get, left alone deliberately when the mutation
        runs: the client re-measures text on load, so a scene carrying a
        taller line height with stale stored extents is not a corner
        case but the ordinary state of one edited in the app.
    """
    return [el(id="t1", type="text", x=0, y=0, width=200,
               height=_LH_FONT * _LH_LINES * 1.25, text=_LH_TEXT,
               fontSize=_LH_FONT, lineHeight=line_height, textAlign="left")]


def _frame_bottom(line_height: float) -> tuple[float, float]:
    """Where the frame ends and where the ink ends, for one line height.

    Args:
        line_height: The scene's `lineHeight`.

    Returns:
        `(frame_bottom, ink_bottom)` in scene coordinates. `ink_bottom`
        is the bottom of the last line's em box — not its baseline,
        because a frame ending on the baseline still cuts the tails off
        `p` and `g`, which the eye reads as a different word.
    """
    svg = canvas.render_svg(_line_height_scene(line_height))[0]
    box = _LINE_HEIGHT_VIEWBOX.search(svg)
    assert box is not None, "cannot parse the <svg> viewBox"
    return (float(box.group(2)) + float(box.group(4)),
            _LH_FONT * line_height * _LH_LINES)


class TestBoundsLoopReadsTheLineHeight(unittest.TestCase):
    """`text_dims` hardcodes 1.25; `paint` honours the element's."""

    def test_the_default_line_height_is_framed_whole(self) -> None:
        """The neighbour, and the pole that proves the loop works at all.

        At the corpus's own 1.25 the two rules agree by coincidence of
        the constant, the frame clears the ink by the full 40px pad, and
        that is what makes the red below mean the line height rather
        than "the bounds loop cannot frame text". Ungated and asserted in
        every commit, per the neighbour contract: without it a fix that
        simply grew every text frame by a fixed slab would satisfy the
        red and be caught by nothing.

        Asserted as a MARGIN and not as a bare "the frame contains the
        ink", because containment alone is satisfied by a frame ten times
        too big — and an over-wide frame is the failure mode a fix to the
        red would most plausibly introduce.
        """
        frame, ink = _frame_bottom(1.25)
        self.assertEqual(
            frame - ink, 40.0,
            "a default-line-height text should sit exactly the 40px pad "
            "above the frame's bottom edge; it sits %g px above (frame "
            "%g, ink %g)" % (frame - ink, frame, ink))

    @unittest.expectedFailure
    def test_a_double_spaced_text_is_framed_whole(self) -> None:
        """RED. Owner: wave/Task 24-follow-up.

        The one mutation is `lineHeight: 1.25 -> 2.0` — double spacing,
        the least exotic non-default there is. Nothing else about the
        scene moves, and that is the point: the same three lines at the
        same size in the same place, painted 0.75 * 20 = 15px lower per
        line, and the frame does not move AT ALL. `render_svg` returns a
        byte-identical viewBox for both scenes.

        Magnitude and direction, both asserted through the margin: the
        last line's em box ends at y=120 against a frame that ends at
        y=115, so the drawing's bottom line is cut 5px BELOW the export's
        edge — margin −5 where the default text gets +40, a 45px swing
        that all comes out of the bottom. Direction matters as much as
        size here: the loop errs toward too SMALL, and a bound that errs
        small is the one that removes content from a picture rather than
        adding white space to it.

        Not the largest reachable magnitude, deliberately. Three lines is
        the fewest that shows the per-line accumulation, and the overrun
        grows linearly with the line count — a twelve-line paragraph at
        the same spacing runs 140px past its frame. The small case is
        pinned because it is the one a fix is most likely to leave
        behind.
        """
        frame, ink = _frame_bottom(2.0)
        self.assertGreaterEqual(
            frame, ink,
            "the viewBox ends at %g but the double-spaced text's last "
            "line runs to %g: `text_dims` reserved height for a 1.25 "
            "line box while `paint` drew a 2.0 one, so %g px of the "
            "bottom line is outside the export"
            % (frame, ink, ink - frame))

    def test_the_three_wrap_rules_disagree_about_a_newline(self) -> None:
        r"""Curator batch 23 item 4 (task 46 §9 C2), 2026-08-15.

        The divergence measured rather than described. Three places in
        `canvas.py` wrap text and they are not quite the same rule:

        - `painted_text_lines`, the renderer's — splits on `\\n` FIRST
          and wraps each resulting line separately, so an explicit
          newline is a HARD break the drawing honours;
        - `lint_layout`'s composed-content check, which wraps
          `txt.replace("\\n", " ")` at `room_w`;
        - `shape_clip`'s label measurement, which does the same at
          `box_w`.

        The last two collapse the newline to a space. Task 46 unified the
        wrap CONDITION and the font size across the renderer's two
        readers and left these, correctly — they answer a different
        question ("does it fit its container") and are outside that
        task's scope. What was never written down is that the collapse
        makes them measure a different STRING, and that nothing pinned it
        as intentional.

        On `"yes\\nno maybe"` in a 200px box the renderer paints two
        lines and both lint rules measure one: 40px of ink against a
        20px estimate, a factor of two, in the direction that reports a
        fit where the drawing overflows. That is the bounds-versus-paint
        family again at a third site.

        GREEN AND NOT RED, deliberately. What is proven here is the
        arithmetic divergence; what is NOT proven is that any shipped
        lint goes silent on a real scene because of it, and asserting a
        miss nobody has demonstrated would put a red in the catalogue
        that no fix can be judged against. The open question for whoever
        owns those two checks: if the collapse is intentional, this pin
        is its documentation, and if it is not, this is the scene the
        red should be built on.
        """
        boxed = el(id="t1", type="text", x=0, y=0, width=200, height=20,
                   text="yes\nno maybe", fontSize=16, autoResize=False,
                   lineHeight=1.25)
        painted, fs = canvas.painted_text_lines(boxed)
        collapsed = canvas.wrap_label_text(
            boxed["text"].replace("\n", " "), boxed["width"], fs).split("\n")
        self.assertEqual(
            (len(painted), len(collapsed)), (2, 1),
            "the renderer paints %r and the lint rules measure %r: the "
            "newline divergence has moved, so re-derive the heights below"
            % (painted, collapsed))
        self.assertEqual(
            (canvas.text_dims("\n".join(painted), fs)[1],
             canvas.text_dims("\n".join(collapsed), fs)[1]), (40, 20),
            "the two readings of one string no longer differ by a factor "
            "of two; if they now agree, the collapse has been removed and "
            "this pin should be deleted rather than re-tuned")


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

# An arrow bound to `n2` and stopping 50px short of it, with nothing about
# the scene for the loader to repair. The FIRING pole for the endpoint_gap
# silence in `test_art011_repair_reroutes_the_arrow_it_displaced`: that test
# reads `collect_findings` off a Store-loaded scene and asserts no gap
# survives the refit, which a dead endpoint_gap satisfies exactly as well as
# a correct repair does. Geometry lifted from
# `TestDetectorsAgainstRealLint.test_endpoint_gap_parses_from_real_lint_output`
# on purpose — one spelling of this control, so a regex or threshold that
# stopped matching moves both poles together instead of quietly retiring one.
_GAPPED_ARROW_ARTIFACT = json.dumps({
    "type": "excalidraw", "version": 2, "elements": [
        {"id": "n1", "type": "rectangle", "x": 0, "y": 0, "width": 100,
         "height": 100},
        {"id": "n2", "type": "rectangle", "x": 300, "y": 0, "width": 100,
         "height": 100},
        {"id": "a1", "type": "arrow", "x": 100, "y": 50, "width": 150,
         "height": 0, "points": [[0, 0], [150, 0]],
         "startBinding": {"elementId": "n1", "focus": 0, "gap": 0},
         "endBinding": {"elementId": "n2", "focus": 0, "gap": 0}}]})

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


def _repair_pack(count: int) -> str:
    """Build one artifact whose load files `count` label refits.

    The `/api/state` cap is a flat `issues[-20:]`, so nothing below 21
    issues can demonstrate an eviction at all — the flood has to be a
    parameter of the fixture rather than a property of it. Each pair is
    the `_OVERSIZED_LABEL_ARTIFACT` shape reduced to its repair-producing
    half: a container with a bound label far wider than it. The arrow is
    dropped deliberately, so these carry no endpoint geometry and add no
    lint lines to the surfaces the reds read.

    Args:
        count: How many oversized label/container pairs to write.

    Returns:
        A serialized artifact document filing exactly `count` ART-011
        refits at load, each paired with the ART-012 confession its
        container resize now files (v0.9 WP4) — `2 * count` issues.
    """
    els: list[dict[str, Any]] = []
    for i in range(count):
        els += [{"id": "c%d" % i, "type": "rectangle", "x": 0, "y": 200 * i,
                 "width": 120, "height": 60, "customData": {"role": "node"},
                 "boundElements": [{"id": "t%d" % i, "type": "text"}]},
                {"id": "t%d" % i, "type": "text", "x": 4,
                 "y": 200 * i + 20, "width": 400, "height": 20,
                 "text": _OVERSIZED_LABEL_TEXT, "fontSize": 16,
                 "originalText": _OVERSIZED_LABEL_TEXT,
                 "containerId": "c%d" % i, "textAlign": "center"}]
    return json.dumps({"type": "excalidraw", "version": 2, "elements": els})


def _scratch_project(case: unittest.TestCase, artifacts: dict[str, str],
                     saves: dict[str, str],
                     pending: dict[str, str] | None = None) -> Path:
    """Write a throwaway project tree and return its root.

    File CONTENTS are passed as raw strings, not objects, because every
    defect below is about malformed bytes: a helper that took dicts could
    not express a file truncated mid-JSON.

    The root is returned rather than a loaded `Store` because the classes
    below probe different layers of the same load — one reads the store's
    own attributes, another runs a CLI command that opens the project
    itself and is judged on what it prints, and a third hands the root to
    a `ServerApp`, which is the only thing that reads the pending queue.

    Args:
        case: The test owning the tree; its `addCleanup` removes it.
        artifacts: `{stem: file body}` written under
            `project_knowledge/artifacts/<stem>.excalidraw`.
        saves: `{stem: file body}` written under
            `project_knowledge/saves/<stem>.json`.
        pending: `{filename: file body}` written under
            `project_knowledge/.pending/`. The directory is created only
            when this is given, because `Store` never makes one and a
            project that has never queued a revision does not have it.

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
    for name, body in (pending or {}).items():
        (pk / ".pending").mkdir(parents=True, exist_ok=True)
        (pk / ".pending" / name).write_text(body, encoding="utf-8")
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
            [i["code"] for i in st.scene_repairs], ["ART-011", "ART-012"],
            "ART-011 refit the label and ART-012 re-routed the arrow that "
            "refit displaced (v0.9 WP4) — both belong in scene_repairs; "
            "ART-000 touched nothing and must not be counted as repair "
            "work (scene_repairs=%r)" % (st.scene_repairs,))

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

    def test_a_real_repair_does_reach_the_resume_narration(self) -> None:
        """The firing pole for the silence above (rule 8, 2026-08-15).

        The test above asserts a quarantine-only load says "repair"
        NOWHERE — not in the resume headline, not on `rec["repairs"]`.
        Both halves of that are absences, and a `catch_up` that had lost
        the ability to narrate a repair at all would satisfy them
        perfectly: the silence-pairing census found nothing in this class
        proving either channel can still say the word. This is that
        proof, on the same two channels the silence names, through the
        same `catch_up()` entry point.

        The fixture is the reason this is a separate test rather than a
        second half of that one. `repair_only` is gated on the RAW disk
        hashes matching replayed history (canvas.py), so the headline
        branch fires only when history already holds the artifact exactly
        as it sits unrepaired on disk — which no `_scratch_project` load
        can reach, since a store that has never committed has no history
        to agree with. `commit` writes the artifact and the save record
        from one element list, so the commit here is what puts the
        UNREPAIRED scene into both, and the second store's divergence is
        then the loader's own work and nothing else.

        Measured, not assumed: without that commit the same artifact
        reconciles under an "added ..." content headline, which populates
        `rec["repairs"]` but never says "repair" — half a pole, and the
        half that would have gone unnoticed.
        """
        root = _scratch_project(self, {"a": _GOOD_ARTIFACT}, {})
        st = canvas.Store(canvas.Project(root))
        st.commit(author="user", base_revn=st.head_revn(),
                  new_scenes={"a": json.loads(
                      _OVERSIZED_LABEL_ARTIFACT)["elements"]})
        again = canvas.Store(canvas.Project(root))
        self.assertEqual(
            [i["code"] for i in again.scene_repairs],
            ["ART-011", "ART-012"],
            "the reload repaired nothing, so this proves no narration")
        rec = again.catch_up()
        headline = ((rec or {}).get("summary") or {}).get("headline") or ""
        self.assertIn("repair", headline.lower(),
                      "two repairs ran at load and the resume headline "
                      "does not say so: %r" % (headline,))
        self.assertEqual(
            [i["code"] for i in (rec or {}).get("repairs") or []],
            ["ART-011", "ART-012"],
            "the reconciliation record carries no repair list, so the "
            "silence next door is asserted against a dead channel "
            "(rec=%r)" % (rec,))

    def test_the_fileref_artifact_loads_and_keeps_its_orphan(self) -> None:
        """The two file-reference tests' setup, asserted where nothing masks it.

        `artifact_files` and `referential` are read almost nowhere else,
        and the two tests that read them were `expectedFailure` when this
        guard was written — WP1 flipped both green in Task 4, and the
        guard outlived the flip on purpose. Rename either attribute and
        those two would raise `AttributeError` rather than fail, which
        reads as a broken test rather than a broken loader; while they
        were red the mask would have swallowed it outright (doctrine §6).
        This is their standing guard either way: ungated, naming both
        attributes, and asserting the load they measure deviations from.

        `referential` is asserted to be a dict rather than to be EMPTY,
        deliberately. Its emptiness was the very silence those two
        pinned, so fixing it here as well would have made WP1's fix break
        this test in the same change that flipped them — a guard that
        fights the fix it is waiting for. It is left that way now that
        the fix has landed, because the claim it makes is about the
        attribute existing and being the right shape, not about what a
        healthy project happens to put in it.
        """
        st = self._load({"a": _FILEREF_ARTIFACT}, {"0001-x": _GOOD_SAVE})
        self.assertEqual(sorted(st.scenes), ["a"])
        self.assertEqual(list(st.artifact_files.get("a", {})),
                         ["orphan-file-id"])
        self.assertIsInstance(st.referential, dict)

    def test_art011_repair_grows_the_container(self) -> None:
        """The load-time refit really does resize a shape.

        The test below measures what that resize leaves behind, so this
        pins the resize itself: if ART-011 ever stops firing on this
        artifact — a threshold moves, the repair is rewritten — the one
        below would go quiet for a reason that has nothing to do with
        arrow re-routing, and read as health.

        That is not hypothetical. v0.9 WP4 moved the very threshold this
        guards against, from `width - 24` to `width - 16`, and this scene
        was checked against the new number deliberately rather than
        assumed past it: a 400px label in a 120px box trips any rule
        either constant could express, so the flip below stayed earned.
        """
        st = self._load({"a": _OVERSIZED_LABEL_ARTIFACT},
                        {"0001-x": _GOOD_SAVE})
        self.assertIn("ART-011", {i.get("code") for i in st.issues})
        n1 = next(e for e in st.scenes["a"] if e["id"] == "n1")
        self.assertEqual(n1["height"], 136)

    # -- Two CLASS pins from the v0.9 Task-18 cycle (2026-08-14), written
    # from third hands. The instances are fixed and covered by the tests
    # that came with the fixes; these encode the rules those instances
    # were instances OF, which is the half a same-hands test cannot carry.

    def _labelled(self, text: str, node_w: int, node_h: int,
                  label_w: int = 400) -> str:
        """Serialize one node carrying one bound label.

        Args:
            text: The label's text, which decides whether a refit is
                needed at all.
            node_w: The container's width.
            node_h: The container's height — the dimension a refit grows,
                so it is what decides whether a resize happens.
            label_w: The label box's stored width.

        Returns:
            The artifact document, as the bytes a load would read.
        """
        return json.dumps({"type": "excalidraw", "version": 2, "elements": [
            {"id": "n1", "type": "rectangle", "x": 0, "y": 0,
             "width": node_w, "height": node_h,
             "customData": {"role": "node"},
             "boundElements": [{"id": "t1", "type": "text"}]},
            {"id": "t1", "type": "text", "x": 10, "y": 40, "width": label_w,
             "height": 20, "text": text, "originalText": text,
             "fontSize": 16, "containerId": "n1", "textAlign": "center"}]})

    def test_a_repaired_flag_is_evidence_something_changed(self) -> None:
        """No repair is filed for work the loader decided not to do.

        The CLASS behind Task 18's fiction repairs. `fit_label_in` walks
        the wrap budgets and can conclude that the label as written is
        already the best it can get — the `keep` return — at which point
        it changes nothing. ART-011 used to be filed anyway, with
        `repaired=True`, so five instances across three fixtures
        re-reported a repair that had never happened on every load
        forever, and the replay test blessed it as expected output.

        The rule this pins is not about ART-011: a `repaired` flag is a
        claim that the loader CHANGED something, so a load that changed
        nothing must file nothing. Asserted over `issues` as a whole
        rather than against that one code, so a second repair filed on a
        no-op path is caught the day it is added.

        The scene is a DECLINED refit, per the Task-18 disclosure that
        the claim must be entirely fictional: an 86px label on a 90x60
        diamond is wide enough that the loader calls the fitter, and the
        fitter then finds no wrap that sits better than the label as
        written and returns having changed nothing. A repair filed there
        could point at no changed byte.

        Getting that scene took measuring rather than reasoning. My
        first attempt used a label that comfortably fits, which never
        reaches the fitter at all — the loader gates on `el.width >
        max(60, cont.width - 16)` — so no mutation of the repair guard
        could have made it fire, and the test would have been green
        forever while proving nothing. The pin below is therefore
        asserted with its own premise: the fitter was reached, and it
        declined.
        """
        body = json.dumps({"type": "excalidraw", "version": 2, "elements": [
            {"id": "n1", "type": "diamond", "x": 0, "y": 0, "width": 90,
             "height": 60, "customData": {"role": "node"},
             "boundElements": [{"id": "t1", "type": "text"}]},
            {"id": "t1", "type": "text", "x": 2, "y": 20, "width": 86,
             "height": 20, "text": "to compose",
             "originalText": "to compose", "fontSize": 16,
             "containerId": "n1", "textAlign": "center"}]})
        st = self._load({"a": body}, {"0001-x": _GOOD_SAVE})
        node = next(e for e in st.scenes["a"] if e["id"] == "n1")
        label = next(e for e in st.scenes["a"] if e["id"] == "t1")
        self.assertGreater(
            86, max(60, node["width"] - 16),
            "the loader's own guard did not admit this label, so the "
            "fitter was never reached and this scene proves nothing")
        self.assertEqual((node["width"], node["height"]), (90, 60))
        self.assertEqual((label["width"], label["height"]), (86, 20))
        self.assertEqual(
            [(i["code"], i.get("repaired")) for i in st.issues], [],
            "the fitter declined and changed nothing, and a repair was "
            "filed anyway")

    def test_every_resize_confesses_and_nothing_else_does(self) -> None:
        """A load that moves a border says so; one that does not stays quiet.

        Task 18's settled invariant, recorded here from third hands
        because it is a RULING and the fix round pinned both poles with
        the same hands that made it. The sentence it turns on: a load
        that changes geometry and stays quiet is the one thing this
        loader may never do.

        Three poles on one builder, which is what makes it a class pin
        rather than three instances. A refit that grows the container
        confesses even with NO arrow bound to it — that was the earlier
        rule's gap, since the confession was gated on having something to
        re-route when confessing is what it is FOR. A refit that resizes
        only the LABEL box files its ART-011 and no confession, because
        no border moved. And a load with nothing to refit files neither.

        The ART-012 message is asserted on the dimensions it names, not
        on its prose: "re-routed nothing; left a3 as drawn" is deliberate
        wording ruled correct in the same cycle, and pinning the sentence
        would freeze a phrasing this test has no business owning.
        """
        big = "Escalate to the compliance review board immediately now"
        for label, body, was in (
                ("grows the container",
                 self._labelled(big, 160, 40), (160, 40)),
                ("refits the label only",
                 self._labelled(big, 300, 120), (300, 120)),
                ("nothing to refit",
                 self._labelled("ok", 300, 120, label_w=80), (300, 120))):
            with self.subTest(load=label):
                st = self._load({"a": body}, {"0001-x": _GOOD_SAVE})
                node = next(e for e in st.scenes["a"] if e["id"] == "n1")
                now = (node["width"], node["height"])
                said = [i for i in st.issues if i["code"] == "ART-012"]
                self.assertEqual(
                    bool(said), now != was,
                    "%s: the border went %r -> %r and ART-012 %s"
                    % (label, was, now, "fired" if said else "did not"))
                if said:
                    self.assertIn("%gx%g" % now, said[0]["msg"])

    def test_art011_repair_reroutes_the_arrow_it_displaced(self) -> None:
        """FLIPPED by v0.9 WP4. A repair re-routes what it moves.

        ART-011 refits an oversized label by calling `fit_label_in`
        (canvas.py), which GROWS the container to fit the wrapped text.
        Here `n1` goes from 60px tall to 136px. The arrow bound to its
        bottom edge used not to be re-routed, so an endpoint that was
        exactly on the border ended up interior by the time the load
        finished — geometry the user never wrote.

        Two numbers described that endpoint and both were right: it sat
        76px above the grown bottom edge (y=136) and 60px below the top
        one (y=0). The lint reports the NEARER edge, so its message said
        60px; the 76px figure is how far the border travelled out from
        under it. They were not in conflict and neither was the
        magnitude this test asserts — it asserts no finding at all.

        `endpoint_gap` DID report the consequence, so this was never a
        silence; it was a MISATTRIBUTION. The message said "arrow a1
        claims to bind n1 … ends 60px inside the shape — re-route it",
        blaming the user's arrow for a move the loader made, and nothing
        in `issues` said the loader had resized anything with arrows on
        it. `reroute_and_confess` now does both halves, so this asserts
        both: no endpoint finding survives the load, and the loader says
        out loud which shape it resized and which arrow it re-routed.

        Still asserted as the ABSENCE of the endpoint finding rather than
        as a path, because the claim is that the repair leaves nothing to
        report — a fix that re-routed to some other wrong place would
        satisfy an equality on ART-012 alone.
        """
        st = self._load({"a": _OVERSIZED_LABEL_ARTIFACT},
                        {"0001-x": _GOOD_SAVE})
        gaps = [f for f in collect_findings(st.scenes["a"])
                if f["check"] == "endpoint_gap"]
        self.assertEqual(
            gaps, [],
            "the ART-011 refit grew n1 and left a1 behind: %s"
            % [f["raw"] for f in gaps])
        said = [i for i in st.issues if i["code"] == "ART-012"]
        self.assertEqual(len(said), 1, "the resize went unconfessed: %r"
                         % (st.issues,))
        self.assertIn("n1", said[0]["msg"])
        self.assertIn("a1", said[0]["msg"])

    def test_a_gap_the_loader_did_not_make_is_still_reported(self) -> None:
        """The firing pole for the silence above (rule 8, 2026-08-15).

        The test above asserts `endpoint_gap` finds NOTHING once the
        ART-011 refit has re-routed what it displaced. That is the right
        claim and it is an absence, so a detector that had stopped
        speaking would satisfy it exactly as well as a correct repair
        does — and the silence-pairing census found `endpoint_gap` firing
        copiously in `TestDetectorsAgainstRealLint` and nowhere inside
        this class, which pairs a different instrument path: those
        fixtures are element lists handed straight to `collect_findings`,
        never a scene that went through `Store.load` first.

        So this is the same detector, through this class's own entry
        point — the artifact is written to disk, loaded, and judged off
        `st.scenes`, which is where the silence reads too. A load that
        started rewriting user geometry, or a `scenes` dict that stopped
        carrying what the file said, would show up here as a lost finding
        rather than next door as a false clean bill.

        The load is asserted to have repaired NOTHING, which is what
        keeps the two poles about one variable. `_GAPPED_ARROW_ARTIFACT`
        carries no bound labels, so no refit is due; if a future loader
        pass ever did re-route this arrow the premise assertion fails
        here loudly, instead of the gap quietly disappearing and reading
        as a dead detector.
        """
        st = self._load({"a": _GAPPED_ARROW_ARTIFACT},
                        {"0001-x": _GOOD_SAVE})
        self.assertEqual([i["code"] for i in st.issues], [],
                         "the loader repaired this scene, so a missing "
                         "gap below would not mean the detector spoke")
        gaps = [f for f in collect_findings(st.scenes["a"])
                if f["check"] == "endpoint_gap"]
        self.assertEqual(
            [(f["element"], f["direction"]) for f in gaps],
            [("a1", "outside")],
            "a1 stops 50px short of n2 and endpoint_gap says nothing — "
            "the silence next door is asserted against a dead detector "
            "(findings=%r)" % (collect_findings(st.scenes["a"]),))
        self.assertAlmostEqual(gaps[0]["magnitude"], 50.0, delta=1.0)

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

    # -- Four more from the same load path (v0.9 Task 1-6 sweep,
    # 2026-08-13): the referential pass's tombstone blindness and its
    # integer-id double-report, the non-hashable value that kills the load
    # outright, and the one rewrite that edits a file the user owns. The
    # first two are read straight off `referential_findings`, which is a
    # pure function over raw scenes — a full `_load` would bury a
    # two-element defect under a project. The end-to-end silence was
    # confirmed through a real load first; the direct call is the
    # minimization, not a shortcut around it.

    def _refer(self, els: list[dict[str, Any]],
               registry: dict[str, Any] | None = None,
               files: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run the referential pass over one raw artifact named `flow`.

        Args:
            els: The artifact's elements, exactly as they sit on disk —
                tombstones included, since dropping them is what the load
                does later and what these tests are about.
            registry: The registry whose mappings are checked; `None`
                means no mappings.
            files: That artifact's `files` map.

        Returns:
            The pass's findings, keyed by scope (`"flow"` or
            `"registry"`); scopes with nothing to say are absent.
        """
        return canvas.referential_findings(
            {"flow": els}, registry or {}, artifact_files={"flow": files
                                                           or {}})

    def test_red_a_tombstone_hides_a_mapping_member_from_the_pass(
            self) -> None:
        """A soft-deleted element left its mapping reading as resolved.

        FLIPPED in v0.9 Task 9. `ids_by_aid` was built from every element
        carrying an id, tombstones included, and the pass then asked
        whether a mapping's member was in that set. `normalize_scene_doc`
        drops `isDeleted` elements at load, so the member was gone from
        the loaded picture while the set that vouched for it still held
        its id — the mapping pointed into a hole and the pass said
        nothing. The fix keeps tombstones out of `ids_by_aid` and skips
        them as SUBJECTS too, so the pass judges the picture the load
        will actually produce, in both directions.

        The WP1 files arm skipped tombstones and was the only arm that
        did; its comment reasoned that "the other arms judge one element
        against another, and a deleted element's target went with it".
        That is true when both go, and false here: the mapping is in the
        registry, which no tombstone touches.

        This is the mapping arm rather than the arrow arm the Task 4
        report cited, and deliberately so — I probed all three. An arrow
        bound to a tombstone is silent in THIS pass but `lint_layout`
        reports it correctly on the loaded scene, so the reader was
        warned. The mapping arm had no such backstop: probed end to end,
        `referential`, `issues` and `lint_layout` were all empty. The
        `annotates` arm was uncovered the same way and the same
        `isDeleted` filter fixed it, which is why it gets this sentence
        instead of a second entry.

        MAGNITUDE is what the finding must name — the concept and the
        `artifact#element` reference, so the user can find the mapping —
        and it is asserted against the message the hard-delete neighbour
        already produces, so the minimal fix flips this without also
        having to invent wording. DIRECTION is that it is filed under
        `registry` scope, where a mapping's owner reads.
        """
        els = [{"id": "n1", "type": "rectangle", "x": 0, "y": 0,
                "width": 100, "height": 60, "isDeleted": True}]
        registry = {"mappings": [{"concept": "checkout",
                                  "elements": ["flow#n1"]}]}
        said = self._refer(els, registry).get("registry") or {}
        self.assertEqual(
            len(said.get("warnings") or []), 1,
            "the mapping's only member is a tombstone the load is about "
            "to drop, and the pass reports nothing: %r"
            % (self._refer(els, registry),))
        self.assertIn("checkout", said["warnings"][0])
        self.assertIn("flow#n1", said["warnings"][0])

    def test_red_a_non_hashable_id_takes_the_whole_project_down(
            self) -> None:
        """One malformed value costs its own file — at both id sites.

        `ids_by_aid` builds a SET of element ids and the files arm builds
        a set of used fileIds, so a value that is not hashable raised
        `TypeError` out of a comprehension nothing caught, and
        `Store.__init__` died with it. Probed on the shipped code:
        `{"x": 1}` in either field gave `TypeError: unhashable type:
        'dict'` and no artifact loaded at all — not the malformed one,
        not its healthy siblings.

        The reviewer asked for one entry over both sites and this is it,
        as a `subTest` per field: the two are one defect under one
        operator — a container where a string belongs — and a fix that
        guarded only the newer files arm would have left the pre-existing
        id site live. `referential_findings` also advertises exactly this
        tolerance in its own docstring ("Non-dict elements are tolerated
        and skipped") while reading untrusted on-disk JSON by design, and
        `indexable` is the one guard both sites now ask: `validate_scene`
        quarantines the artifact, and the pass skips what it cannot index
        for the callers who hold a raw scene.

        Fixed for THESE TWO FIELDS ONLY, which is the sentence this
        docstring was missing and the sweep below was filed against: the
        first `indexable` covered the two set-member fields and left the
        four membership tests in the same function hashing unguarded
        values. Task 9 widened it to all six. Read nothing here as saying
        the family is closed — it is closed only for as long as nobody
        hashes a seventh field, and `indexable`'s own docstring carries
        that obligation.

        The outcome is the one `test_red_non_dict_save_record_takes_down
        _the_whole_store` established for the save loop and is asserted
        the same way: the project OPENS, and the file that could not be
        read is named. `except Exception` is deliberate and pins the
        outcome rather than the mechanism — any exception escaping the
        load is the same disaster for the user, whatever raised it.

        The artifact leaves rather than the element, which is the same
        ruling ART-000 already makes about a file that is not an object:
        dropping just the element would hand back a repaired drawing the
        user never wrote, while a quarantine leaves the bytes on disk to
        be fixed by hand and says which file went.
        """
        for field in ("id", "fileId"):
            with self.subTest(field=field):
                bad = {"id": "img", "type": "image", "x": 0, "y": 0,
                       "width": 120, "height": 90, "fileId": "f1"}
                bad[field] = {"x": 1}
                body = json.dumps({"type": "excalidraw", "version": 2,
                                   "elements": [bad]})
                try:
                    st = self._load({"a": _GOOD_ARTIFACT, "b": body},
                                    {"0001-x": _GOOD_SAVE})
                except Exception as exc:
                    self.fail(
                        "a non-hashable %s in ONE artifact killed the "
                        "load (%s: %s) — the whole project is "
                        "unreachable, healthy artifacts and all"
                        % (field, type(exc).__name__, exc))
                self.assertEqual(sorted(st.scenes), ["a"])
                self.assertIn("b", " ".join(str(i) for i in st.issues))

    def test_red_an_integer_fileid_is_reported_in_both_directions(
            self) -> None:
        """One image and its own blob draw one finding, not two.

        `used_files` and the `files` map were compared with `in`, so the
        integer `123` never matched the key `"123"` and BOTH arms fired
        about the same pair: a warning saying the image "carries no such
        file — re-import the image, or delete the element", and a note
        saying the blob "is kept ... and no element shows it".
        Excalidraw writes string fileIds, so this needs a hand-malformed
        or foreign-tool document — which is the kind this function reads
        by contract.

        Low reachability is why it is one entry and not three, but it is
        not why it would have been harmless. The two findings were
        contradictory ADVICE about one image: follow the note and the
        user deletes the only blob the element names, turning a type
        confusion into real data loss. That is the harm being pinned.

        Asserted as "not both" rather than as a coercion, deliberately.
        Two readings were defensible — the pair is matched and the
        comparison should be type-tolerant, or the pair is genuinely
        unmatched and the note should not claim nothing shows that key.
        The second is what shipped: a fileId that is not a string does
        not NAME that blob, so the dangling warning stands, while the
        orphan note now matches on the key's text and no longer calls a
        blob unshown when an element plainly points at it. The neighbour
        below is what kept the cheap fix of muting one arm from
        satisfying this.
        """
        els = [{"id": "img", "type": "image", "x": 0, "y": 0,
                "width": 120, "height": 90, "fileId": 123}]
        files = {"123": {"id": "123", "mimeType": "image/png",
                         "dataURL": "data:image/png;base64,AAAA"}}
        said = self._refer(els, files=files).get("flow") or {}
        self.assertEqual(
            (len(said.get("warnings") or []),
             len(said.get("notes") or [])), (1, 0),
            "one image and its own blob earned a dangling warning AND an "
            "orphan note — the user is told to re-import the image and "
            "to delete the file it names: %r" % (said,))

    def test_red_the_gitignore_rewrite_edits_a_users_own_pattern(
            self) -> None:
        """The one start that appends leaves what git matches alone.

        `ensure_tree` read the file as `[ln.strip() for ln in ...]` and
        rebuilt it from that list, so on the single start where
        `.pending/` or `.backups/` is missing — the legacy project
        upgrade, which every existing project takes exactly once — every
        preserved line lost its leading whitespace. Git treats leading
        whitespace as part of the pattern, so `    build/` and `build/`
        are different rules, and the file the user wrote was not the file
        that came back. The lines are kept verbatim now and only the
        membership LOOKUP is stripped, so an indented `.pending/` still
        counts as already present.

        Trailing whitespace is deliberately NOT pinned here: git ignores
        it unless it is escaped, so stripping it is defensible and
        pinning it would make this fail over a difference that changes
        no match. Leading whitespace changes what the rule matches, and
        that is the whole claim.

        MAGNITUDE is the byte-for-byte survival of the user's line.
        DIRECTION is that the append still happens — asserted in the
        same test because "leave the file alone" is trivially satisfied
        by doing nothing, and doing nothing is the bug this rewrite was
        added to fix. The outcome is pinned, not the mechanism: any way
        of appending to the raw lines instead of to a stripped list
        satisfies both.
        """
        root = _scratch_project(self, {}, {})
        gi = root / "project_knowledge" / ".gitignore"
        gi.write_text("    build/\n.backups/\n", encoding="utf-8")
        canvas.Project(root).ensure_tree()
        after = gi.read_text(encoding="utf-8").splitlines()
        self.assertIn(
            "    build/", after,
            "the user's indented pattern came back as a different rule: "
            "%r" % (after,))
        self.assertIn(".pending/", after)

    def test_a_hard_deleted_mapping_member_is_reported(self) -> None:
        """The mapping arm's live pole: the element gone, the warning fires.

        The tombstone red's neighbour, and the same scene with the one
        field changed that the red turns on — `isDeleted: True` becomes
        the element's absence. So it proves the arm is alive, that it
        reaches `registry` scope, and that the message the red asserts
        against is already shipped wording rather than something a fix
        would have to invent.
        """
        registry = {"mappings": [{"concept": "checkout",
                                  "elements": ["flow#n1"]}]}
        said = self._refer([], registry).get("registry") or {}
        self.assertEqual(len(said.get("warnings") or []), 1, said)
        self.assertIn("checkout", said["warnings"][0])
        self.assertIn("flow#n1", said["warnings"][0])

    def test_a_live_mapping_member_says_nothing(self) -> None:
        """The mapping arm's silent pole: a member that is really there.

        Without it, a fix that simply reported every mapping member
        would flip the tombstone red and hand every healthy project a
        warning per mapping. The element carries no `isDeleted` key at
        all, which is the shape a normal scene has.
        """
        els = [{"id": "n1", "type": "rectangle", "x": 0, "y": 0,
                "width": 100, "height": 60}]
        registry = {"mappings": [{"concept": "checkout",
                                  "elements": ["flow#n1"]}]}
        self.assertEqual(self._refer(els, registry), {})

    def test_an_unmatched_integer_fileid_earns_one_finding(self) -> None:
        """The integer red's neighbour: same type, genuinely no such file.

        This is what stops the double-report red from being satisfied by
        muting an arm. The fileId is still the integer `123` and the
        `files` map holds an unrelated key, so the dangling warning is
        correct and must survive any fix; the orphan note is correct too,
        because that blob really is shown by nothing. One finding per
        real problem, which is exactly what the red says the matched case
        must also produce.
        """
        els = [{"id": "img", "type": "image", "x": 0, "y": 0,
                "width": 120, "height": 90, "fileId": 123}]
        files = {"other": {"id": "other", "mimeType": "image/png",
                           "dataURL": "data:image/png;base64,AAAA"}}
        said = self._refer(els, files=files).get("flow") or {}
        self.assertEqual(len(said.get("warnings") or []), 1, said)
        self.assertIn("123", said["warnings"][0])
        self.assertEqual(len(said.get("notes") or []), 1, said)
        self.assertIn("other", said["notes"][0])

    def test_the_gitignore_rewrite_appends_once_and_keeps_the_rest(
            self) -> None:
        """The rewrite's live pole: it does its job and then stops.

        The whitespace red's neighbour, differing in the one thing that
        red turns on — no leading whitespace on the user's line. Three
        claims, because a fix to `ensure_tree` could break any of them
        while satisfying the red: the user's own lines survive in order,
        both machinery lines are appended on the start that finds them
        missing, and a second start appends nothing further. Without the
        last one, a fix that stopped comparing against the file's
        contents would grow the file on every start forever.
        """
        root = _scratch_project(self, {}, {})
        gi = root / "project_knowledge" / ".gitignore"
        gi.write_text("build/\n# mine\nnotes.local.md\n", encoding="utf-8")
        canvas.Project(root).ensure_tree()
        first = gi.read_text(encoding="utf-8")
        self.assertEqual(first.splitlines(),
                         ["build/", "# mine", "notes.local.md",
                          ".backups/", ".pending/"])
        canvas.Project(root).ensure_tree()
        self.assertEqual(gi.read_text(encoding="utf-8"), first,
                         "a second start appended the machinery again")

    def test_a_hashable_fileid_still_reaches_both_file_arms(self) -> None:
        """The crash red's neighbour: well-formed values load and report.

        Its live pole and its error-red guard in one. The red asserts
        that a project OPENS, which an artifact that quietly stopped
        being read would also satisfy, so this pins that the same
        artifact shape with string values both loads and produces the
        finding its `fileId` earns — the arm the crash happens inside is
        reached, and reached with something to say.
        """
        body = json.dumps({"type": "excalidraw", "version": 2, "elements": [
            {"id": "img", "type": "image", "x": 0, "y": 0, "width": 120,
             "height": 90, "fileId": "f1"}]})
        st = self._load({"a": _GOOD_ARTIFACT, "b": body},
                        {"0001-x": _GOOD_SAVE})
        self.assertEqual(sorted(st.scenes), ["a", "b"])
        self.assertIn("f1", json.dumps(st.referential, default=str))

    def test_red_four_reference_fields_are_still_unhashable_crash_sites(
            self) -> None:
        """The guard covered two fields; its own function indexes six.

        FLIPPED in v0.9 Task 9, by widening `indexable` to every value
        `validate_scene` hashes. It had been added with the flipped red
        above and checked `id` and `fileId` — the two fields that become
        SET MEMBERS. But `validate_scene` goes on to test four more values
        FOR MEMBERSHIP in that same `seen` set, and `x in some_set` hashes
        `x` just as hard: `containerId`, `startBinding` and `endBinding`'s
        `elementId`, and each `boundElements` entry's `id`.

        Probed on the shipped code, one element per field, `{"x": 1}` in
        each: all four raised `TypeError: unhashable type: 'dict'` and
        BRICKED `Store()` outright. That is a worse outcome than the
        defect the guard was written for, where the malformed artifact is
        quarantined as ART-000 and its healthy sibling still loads — the
        same value in a guarded field loses one file, and in an unguarded
        one lost the project.

        This entry exists because two pieces of shipped prose read as if
        the family were closed: the comment above the guard says "A
        document we cannot index is a document we cannot read", and the
        flipped red's own docstring describes the crash in the past
        tense. Neither was wrong about what it fixed; both invited the
        next reader to stop looking, and both now carry the obligation
        instead. A sweep is the honest shape here — one red per site would
        be four near-identical entries, and the subTest form matches how
        this class already pins `id` and `fileId` together.

        MAGNITUDE and DIRECTION are the outcome the guarded fields
        already have, asserted the same way: the project OPENS, and the
        artifact that could not be read is named. Widening `indexable` to
        every field `validate_scene` hashes flips all four at once, which
        is why they share one entry.
        """
        fields: dict[str, dict[str, Any]] = {
            "containerId": {"id": "t1", "type": "text",
                            "containerId": {"x": 1}},
            "startBinding": {"id": "a1", "type": "arrow",
                             "startBinding": {"elementId": {"x": 1}}},
            "endBinding": {"id": "a1", "type": "arrow",
                           "endBinding": {"elementId": {"x": 1}}},
            "boundElements": {"id": "n2", "type": "rectangle",
                              "boundElements": [{"id": {"x": 1},
                                                 "type": "text"}]},
        }
        for field, spec in fields.items():
            with self.subTest(field=field):
                body = json.dumps({"type": "excalidraw", "version": 2,
                                   "elements": [dict(spec, x=0, y=0,
                                                     width=10, height=10)]})
                try:
                    st = self._load({"a": _GOOD_ARTIFACT, "b": body},
                                    {"0001-x": _GOOD_SAVE})
                except Exception as exc:
                    self.fail(
                        "an unhashable %s in ONE element killed the load "
                        "(%s: %s) — the same value in `id` or `fileId` "
                        "only costs that one artifact"
                        % (field, type(exc).__name__, exc))
                self.assertEqual(sorted(st.scenes), ["a"])
                self.assertIn("b", " ".join(str(i) for i in st.issues))

    def test_the_four_reference_fields_are_read_when_well_formed(
            self) -> None:
        """The sweep's live pole: those same four fields resolve normally.

        The reference repairs are the code the red crashes inside, so
        this pins that all four paths are REACHED and working on ordinary
        input — a `validate_scene` that had stopped reading them would
        make the red's "the project opens" trivially true while the
        loader silently stopped repairing anything.

        Dangling values are used rather than resolvable ones because
        that is the branch the membership test selects: each field points
        at an id the scene does not hold, so every repair fires and the
        artifact still loads. Asserted by the codes, since those name
        which of the four arms spoke.
        """
        body = json.dumps({"type": "excalidraw", "version": 2, "elements": [
            {"id": "t1", "type": "text", "x": 0, "y": 0, "width": 10,
             "height": 10, "containerId": "gone"},
            {"id": "a1", "type": "arrow", "x": 0, "y": 0, "width": 10,
             "height": 10, "startBinding": {"elementId": "gone"},
             "endBinding": {"elementId": "gone"}},
            {"id": "n2", "type": "rectangle", "x": 0, "y": 0, "width": 10,
             "height": 10, "boundElements": [{"id": "gone",
                                              "type": "text"}]}]})
        st = self._load({"a": _GOOD_ARTIFACT, "b": body},
                        {"0001-x": _GOOD_SAVE})
        self.assertEqual(sorted(st.scenes), ["a", "b"])
        codes = {i["code"] for i in st.issues}
        self.assertIn("ART-004", codes, st.issues)
        self.assertIn("ART-005", codes, st.issues)
        n2 = next(e for e in st.scenes["b"] if e["id"] == "n2")
        self.assertEqual(n2["boundElements"], [])

    def test_an_unwalkable_boundelements_quarantines_its_artifact(
            self) -> None:
        """A `boundElements` that is not iterable costs one file, not all.

        `boundElements: 5` used to kill the whole load: `validate_scene`'s
        repair loop walks it, and `for b in 5` raises `TypeError` where
        nothing catches — so no artifact opened, malformed or healthy.
        The guard now answers False for an element whose `boundElements`
        cannot be walked at all, which routes it into the ART-000
        quarantine every other unreadable shape takes.

        Written from third hands deliberately. The fix was made inline by
        the implementer who found it, on the exact line they were
        hardening, and reviewed and probed at both poles by the reviewer
        — an approved routing flex, not the normal path. The pin coming
        from a third pair of hands is what keeps the discipline whole:
        the acceptance test and the fix still do not share an author.

        Both halves are asserted, because the quarantine alone is half a
        result: `a` must still load, since losing the sibling too is the
        outcome this replaced.
        """
        body = json.dumps({"type": "excalidraw", "version": 2, "elements": [
            {"id": "n2", "type": "rectangle", "x": 0, "y": 0, "width": 10,
             "height": 10, "boundElements": 5}]})
        st = self._load({"a": _GOOD_ARTIFACT, "b": body},
                        {"0001-x": _GOOD_SAVE})
        self.assertEqual(sorted(st.scenes), ["a"])
        self.assertIn("ART-000", {i["code"] for i in st.issues})
        self.assertIn("b", " ".join(str(i) for i in st.issues))

    def test_the_seventh_hash_site_is_guarded_end_to_end(self) -> None:
        """`containerId` is hashed past every quarantine, so it is guarded.

        The guard's list was widened from two fields to six, and the
        seventh site is the one that decided the guard's SHAPE.
        `rebuild_bound_elements` hashes `containerId` unconditionally —
        `e.get("containerId") in ix` — and it runs further down the load,
        past every quarantine, so a `[]` there survived a guard that had
        been narrowed to skip falsy values on the reasoning that
        `validate_scene`'s own `if el.get("containerId")` never hashes
        one. The implementer's own probe caught that narrowing before it
        shipped; the guard now asks `hash` unconditionally.

        The lesson generalises past this field, which is why the pin is
        here rather than only in prose: a guard cannot be scoped to the
        conditions of any ONE caller, because the next caller down the
        same load does not share them. `[]` is used rather than a dict
        because it is the value that made the narrowing visible.
        """
        body = json.dumps({"type": "excalidraw", "version": 2, "elements": [
            {"id": "t1", "type": "text", "x": 0, "y": 0, "width": 10,
             "height": 10, "containerId": []}]})
        st = self._load({"a": _GOOD_ARTIFACT, "b": body},
                        {"0001-x": _GOOD_SAVE})
        self.assertEqual(sorted(st.scenes), ["a"])
        self.assertIn("ART-000", {i["code"] for i in st.issues})

    def test_red_a_branch_switch_leaves_the_load_report_standing(
            self) -> None:
        """A repaired reference was still reported, head having not moved.

        FLIPPED in v0.9 Task 42. `referential` is the PRE-REPAIR report,
        true of exactly the disk state the store opened on, and
        `referential_now` merged it while `referential_revn ==
        head_revn()`. That gate read head equality as "the artifacts on
        disk are still the ones I read", and `switch_branch` breaks the
        implication: it rewrites every artifact file from
        `state_at(b["head"])` WITHOUT moving head, so the bytes under an
        unchanged revision number are new. The writers now record what
        they rewrote: `referential_spent` names the scopes whose file is
        no longer the one the report was read from, and a switch spends
        every scope because it replaces the whole picture. `lint_debt`'s
        memo, which was keyed the same way and went stale over the same
        switch, keys on `Store.state_stamp` instead.

        Probed end to end. A project whose disk copy carries a dangling
        `endBinding` loads with the finding filed; a switch to a branch
        forked at the same revision and back rewrites the file from the
        committed state, where the binding is intact. Disk and memory
        then both read `endBinding: n2` — the GHOST exists nowhere — and
        `referential_now` still reported "arrow t1 binds GHOST at its end
        point and that element no longer exists". That is r5-19's exact
        shape, an ERROR nagged for a reference already repaired, reached
        through a normal user flow rather than through a commit.

        The Task-9 review filed this as "head_revn can move BACKWARD on a
        branch switch". Backward movement is the reachable half of a
        wider fault and, on its own, is not the one that bites: revns are
        globally unique, so two branches share a head only when neither
        has committed since the fork, and their reconstructed states are
        then identical — which is why `lint_debt`'s cache, keyed the same
        way, cannot be made to serve one branch's debt for another's
        scenes. What does bite is head STAYING EQUAL while the content
        under it is rewritten, and that is what this pins.

        MAGNITUDE is what the surface reports — nothing about a
        reference that no longer exists. DIRECTION is that the report
        follows the SCENES rather than a revision number; keying the
        freshness on the scenes themselves, or dropping the load-time set
        when the files are rewritten, would each satisfy it.
        """
        tmp = Path(tempfile.mkdtemp(prefix="mutants-branch-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        project = canvas.Project(tmp)
        project.ensure_tree()
        store = canvas.Store(project)
        store.apply_batch({
            "base_revn": 0, "artifact": "f",
            "create": {"id": "f", "name": "F", "type": "flow",
                       "concept": "checkout", "concept_name": "Checkout"},
            "ops": [{"op": "add", "element": {
                "type": "rectangle", "id": nid, "label": nid.upper(),
                "x": 300 * i, "y": 0, "width": 100, "height": 60,
                "role": "node"}} for i, nid in enumerate(("n1", "n2"))]
            + [{"op": "add", "element": {"type": "arrow", "id": "t1",
                                         "from": "n1", "to": "n2"}}]})
        head = store.head_revn()
        store.registry["branches"].append(
            {"name": "side", "head": head, "archived": False,
             "forked_from": "main", "forked_at_revn": head})
        store._save_registry()
        path = tmp / "project_knowledge" / "artifacts" / "f.excalidraw"
        doc = json.loads(path.read_text(encoding="utf-8"))
        for e in doc["elements"]:
            if e["id"] == "t1":
                e["endBinding"] = {"elementId": "GHOST", "focus": 0,
                                   "gap": 1}
        path.write_text(json.dumps(doc), encoding="utf-8")
        reloaded = canvas.Store(canvas.Project(tmp))
        self.assertIn(
            "GHOST", json.dumps(reloaded.referential),
            "the load did not file the pre-repair finding, so this scene "
            "cannot measure whether it is dropped later")
        reloaded.switch_branch("side")
        reloaded.switch_branch("main")
        live = next(e for e in reloaded.scenes["f"] if e["id"] == "t1")
        self.assertEqual((live.get("endBinding") or {}).get("elementId"),
                         "n2", "the round trip did not restore the "
                         "committed binding, so nothing was repaired")
        self.assertNotIn(
            "GHOST", json.dumps(reloaded.referential_now()),
            "the switch rewrote the file from the committed state and "
            "the binding is whole on disk and in memory, but the "
            "load-time report is still merged in and still names GHOST")

    def test_red_a_negative_replay_index_lands_in_the_interior(self) -> None:
        """A corrupt record's index resolves the way every other one does.

        `replay_changes` clamped only the top: `idx = min(ch.get("index",
        len(els)), len(els))`, so a negative index went to `list.insert`
        unclamped and Python read it as an offset from the END. On a
        three-element list `-1` landed the element SECOND TO LAST, which
        is neither the front nor the back and is not a position any
        writer could have meant.

        What made it nonsense rather than merely a convention is that
        the other negative values disagreed with it: `-5` on the same
        list clamped to the front, because `insert` bottoms out at 0.
        Two corrupt records carrying two negative indices got two
        unrelated answers, while the arm thirty lines below in the SAME
        function already did the right thing — the legacy reorder branch
        is `max(0, min(ch["to_index"], len(els)))`. The missing `max(0,
        ...)` was an omission, not a decision, and the add branch now
        carries it too.

        Save records are self-generated, so there was no live trigger —
        but they are read back from DISK, which puts this in the same
        family as the malformed artifacts above. Probed end to end: with
        one add change's `index` hand-edited to `-1`, the project
        reloaded with `issues` EMPTY, `state_at` reconstructed
        `['a', 'c', 'b', ...]` where the live scene held
        `['a', 'b', 'c', ...]`, and no lint fired on the reconstruction.
        Element order is paint order, so a rollback to that revision
        handed the user a differently-stacked drawing with nothing
        anywhere saying the reconstruction disagreed with the record.

        MAGNITUDE is where the element lands. DIRECTION is that every
        negative index resolves the same way as `0` — asserted as
        agreement between `-1`, `-5` and `0` rather than as a literal
        position, so the claim is the consistency and not a chosen
        convention. `max(0, ...)`, matching the arm below, is what
        flipped it; quarantining the record at load would be a larger
        change that this test would have to be rewritten for, and the
        docstring says so rather than leaving a future fixer guessing.
        """
        base = [{"id": "a"}, {"id": "b"}, {"id": "c"}]

        def replayed(index: int) -> list[str]:
            """Replay one add change at `index` and name the result.

            Args:
                index: The change's `index` field, as read from disk.

            Returns:
                The element ids of the reconstructed list, in order.
            """
            return [e["id"] for e in canvas.replay_changes(
                base, [{"op": "add", "index": index,
                        "element": {"id": "NEW"}}])]

        self.assertEqual(
            replayed(-1), replayed(0),
            "index=-1 reconstructed %r while index=0 gives %r, and "
            "index=-5 gives %r — three answers to one corrupt record"
            % (replayed(-1), replayed(0), replayed(-5)))
        self.assertEqual(replayed(-5), replayed(0))

    def test_state_at_reconstructs_the_order_that_was_saved(self) -> None:
        """The replay red's live pole, and its end-to-end guard.

        Two jobs. It proves `replay_changes` reconstructs a real
        project's stored order faithfully when the record is intact, so
        the red measures a corrupt index rather than a replay that was
        always wrong. And it is the error-red guard: the red calls
        `replay_changes` directly on a three-item list, which would go on
        passing if the whole reconstruction path were rewritten around
        it, and this is what fails instead.

        Asserted against the LIVE scene rather than a literal order,
        because "replay reproduces what was committed" is the property
        that matters and it survives any change to how the seed batch
        happens to lay elements out.
        """
        store, _ = self._replay_project()
        rebuilt = store.state_at(store.head_revn())["flow"]["elements"]
        self.assertEqual([e["id"] for e in rebuilt],
                         [e["id"] for e in store.scenes["flow"]])

    def _replay_project(self) -> tuple[canvas.Store, Path]:
        """Build a three-node project whose save record is worth corrupting.

        Three nodes because a replay index only means something against a
        list long enough to have an interior; one batch because the reds
        edit a single record's `changes` and a second revision would give
        them two places to look. Shared by the green reconstruction pin
        and by the corrupt-index reds, so those measure a deviation from
        one known-good history rather than from two.

        Returns:
            `(the loaded store, the project root directory)`.
        """
        tmp = Path(tempfile.mkdtemp(prefix="mutants-replay-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        project = canvas.Project(tmp)
        project.ensure_tree()
        store = canvas.Store(project)
        store.apply_batch({
            "base_revn": 0, "artifact": "flow",
            "create": {"id": "flow", "name": "Flow", "type": "flow",
                       "concept": "checkout", "concept_name": "Checkout"},
            "ops": [{"op": "add", "element": {
                "type": "rectangle", "id": nid, "x": 200 * i, "y": 0,
                "width": 100, "height": 60, "role": "node"}}
                for i, nid in enumerate(("a", "b", "c"))]})
        return store, tmp

    def _corrupt_index(self, root: Path, value: Any) -> None:
        """Rewrite one add change's `index` in the newest save record.

        Args:
            root: The project root from `_replay_project`.
            value: What to write into the `index` field — the whole point
                is that it need not be a position.
        """
        saves = sorted((root / "project_knowledge" / "saves").glob("*.json"))
        doc = json.loads(saves[-1].read_text(encoding="utf-8"))
        for ch in doc["artifacts"]["flow"]["changes"]:
            if ch["op"] == "add" and ch["element"]["id"] == "c":
                ch["index"] = value
        saves[-1].write_text(json.dumps(doc), encoding="utf-8")

    def test_red_a_non_integer_replay_index_kills_reconstruction(
            self) -> None:
        """A corrupt record's index does not crash — it took history down.

        FLIPPED in v0.9 Task 9. The sibling of the negative-index red Task
        36 flipped, at the fault that fix did not cover. `index_fault`
        refuses strings, `None`, containers and bools everywhere an AGENT
        can send an index, and `replay_changes` reads its index off DISK
        instead: `min(ch.get("index", len(els)), len(els))` compared
        whatever was there against an int, so a string gave `TypeError:
        '<' not supported between instances of 'int' and 'str'`. The add
        branch now applies `index_fault`'s TYPE half — and only that half,
        since the negative case beside it is answered by the clamp.

        Pre-existing, and confirmed as such rather than assumed — it
        reproduces identically at `fce0b30`, before the shared predicate
        existed, because `min("2", 3)` raised the same way. Nobody should
        hunt this as a regression from that fix.

        What makes it worse than the negative case it sits beside is
        WHEN it is felt. Probed end to end: the project reloads with
        `issues` EMPTY — nothing at load looks at a change's index — and
        then `state_at` raises, so revert, checkout and rollback are all
        unreachable for a project that opened cleanly and looks fine. One
        hand-edited byte in one record costs the user their whole
        history, silently, until they try to use it.

        MAGNITUDE is where the element lands, and it is asserted against
        the function's OWN documented default for "no position given" —
        `ch.get("index", len(els))`, i.e. the end — rather than against a
        literal, so the fix inherits a behaviour the code already
        defines instead of inventing one. DIRECTION is that reconstruction
        RESOLVES rather than dying, asserted last and end to end, because
        that is the outcome the user feels; routing the field through
        `index_fault` and falling back to the default satisfies both, and
        quarantining the record at load would satisfy them too.

        The value list carries both booleans because `index_fault`'s
        `isinstance(value, bool)` clause is a separate branch from its
        `isinstance(value, int)` one and had no test of its own (Task-9
        review F3): `bool` subclasses `int`, so without that clause
        `True` reads as position 1 and `False` as position 0, and the
        two would land somewhere real instead of at the default. They
        are the only values here that fail SILENTLY when the guard slips
        — every other one raises — which is exactly why they are the
        easiest to leave uncovered.
        """
        base = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        default = [e["id"] for e in canvas.replay_changes(
            base, [{"op": "add", "element": {"id": "NEW"}}])]
        for value in ("2", None, [], {}, 1.5, True, False):
            with self.subTest(index=value):
                try:
                    got = canvas.replay_changes(
                        base, [{"op": "add", "index": value,
                                "element": {"id": "NEW"}}])
                except Exception as exc:
                    self.fail(
                        "replay_changes died on index=%r (%s: %s) — one "
                        "corrupt save record takes down every path that "
                        "reconstructs history"
                        % (value, type(exc).__name__, exc))
                self.assertEqual([e["id"] for e in got], default)
        store, root = self._replay_project()
        revn = store.head_revn()
        self._corrupt_index(root, "2")
        reloaded = canvas.Store(canvas.Project(root))
        try:
            rebuilt = reloaded.state_at(revn)["flow"]["elements"]
        except Exception as exc:
            self.fail(
                "the project reloaded clean (issues=%r) and then state_at "
                "died (%s: %s) — revert, checkout and rollback are all "
                "gone for a project that opens fine"
                % ([i["code"] for i in reloaded.issues],
                   type(exc).__name__, exc))
        # Counted off the live scene, not off a literal. This line held a
        # bare `6` that no run had ever reached — `state_at` raised above
        # it, `self.fail` ended the test there, and `expectedFailure` made
        # a red out of the crash — so the number was never checked against
        # a project that has three elements. Task 9's flip is what first
        # executed it. The claim is unchanged: the whole project comes
        # back, not a truncated one.
        self.assertEqual(len(rebuilt), len(store.scenes["flow"]))


# ---------------------------------------------------------------------------
# r5b-2, the phantom reorder storm (filed in assessment run 5b arm B,
# reproduced against this tree by `spike-r5b2.md`, curated 2026-08-15).
#
# `Store` keeps a scene TWICE. `self.scenes[aid]` is the cache every client
# reads — `/api/state` hands it out verbatim, and the CLI's fetch path takes
# the same bytes — and it is simply whatever the last commit posted.
# `state_at(revn)` is a full forward replay of the save-record log from the
# empty root, and it is what `commit` diffs the incoming scene AGAINST. The
# two are supposed to be the same list in the same order and NOTHING asserts
# that they are: no load-time check, no runtime check, no test. They are
# maintained by entirely separate mechanisms, so they can and do come apart.
#
# When they have, the next commit on that artifact diffs a scene against a
# differently-ordered copy of itself and mints a `reordered` fact for every
# element the two orders disagree about — 28 of `argus-domain`'s 45 in the
# reproduction, headline "renamed 'PipelineRun' -> 'Run' (+29 more)" for a
# save that changed one word. The direction is false-positive throughout:
# every one of those elements is where the user left it.
#
# WHAT MADE THIS SURVIVE TWO ASSESSMENT RUNS is the self-heal. The commit
# that exposes the drift also writes the cache order into the log, so the
# artifact agrees with itself again and the storm does not recur — it fires
# once per accumulated drift and then goes quiet. Arm B saw it twice and
# arm A not at all, and the two arms concluded opposite things about the
# same code path; the axis they were missing was not the verb (arm B
# guessed rename/note-add) but WHICH ARTIFACT had drifted. A move
# reproduces it identically on the same fixture.
#
# The drift is REAL AND SHIPPED: `tests/fixtures/argus-r4-arm3` carries it
# in `argus-domain` today, which is what the first red reads. The second red
# is the storm at minimum scale — two rectangles, one save record, the disk
# order reversed — because a 45-element artifact cannot show anyone what the
# mechanism is. Both are RED BY ASSERTION and both stay red until somebody
# owns the reconciliation; that work is unassigned, filed for the addendum
# wave, and it is emphatically not the curator's (`AGENTS.md`: the fix and
# its acceptance test do not come from the same hands).
# ---------------------------------------------------------------------------


class TestReplayOrderFidelity(unittest.TestCase):
    """The cache a client reads and the log a commit diffs are one scene."""

    def _drift_project(self, drifted: bool) -> canvas.Store:
        """A two-node project whose disk order does or does not match its log.

        Two elements is the whole mechanism: one save record adds `n1`
        then `n2`, so the replay says `[n1, n2]`, and the artifact file on
        disk is written in the other order when `drifted`. That is
        precisely the state `argus-domain` reached over 44 real saves, at
        a size where the reader can hold both orders in their head.

        Args:
            drifted: True to write the artifact file in the reverse of
                the order its own save record replays to.

        Returns:
            The loaded store, ready to commit against.
        """
        root = Path(tempfile.mkdtemp(prefix="mutants-drift-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        pk = root / "project_knowledge"
        (pk / "artifacts").mkdir(parents=True)
        (pk / "saves").mkdir(parents=True)
        n1 = el(id="n1", type="rectangle", x=0, y=0, width=80, height=40,
                customData={"role": "node"})
        n2 = el(id="n2", type="rectangle", x=200, y=0, width=80, height=40,
                customData={"role": "node"})
        (pk / "artifacts" / "d.excalidraw").write_text(json.dumps(
            {"type": "excalidraw", "version": 2,
             "elements": [n2, n1] if drifted else [n1, n2]}), "utf-8")
        (pk / "saves" / "0001-d.json").write_text(json.dumps(
            {"revn": 1, "base_revn": 0, "author": "agent", "branch": "main",
             "artifacts": {"d": {"changes": [{"op": "add", "element": n1},
                                             {"op": "add", "element": n2}]}}}),
            "utf-8")
        (pk / "model.json").write_text(json.dumps(
            {"revn": 1, "head": "main",
             "branches": [{"name": "main", "head": 1, "archived": False}]}),
            "utf-8")
        return canvas.Store(canvas.Project(root))

    def _nudge(self, store: canvas.Store) -> dict[str, Any]:
        """Move `n1` four pixels and commit, returning the save summary.

        One element, one attribute, the smallest edit the differ still
        calls a change — so every OTHER verb in the returned counts is a
        fact about an element this commit did not touch.

        Args:
            store: The store to commit against, at its own head.

        Returns:
            The record's `summary` — `verb_counts`, `headline`,
            `suppressed`.
        """
        els = copy.deepcopy(store.scenes["d"])
        for e in els:
            if e["id"] == "n1":
                e["x"] = 4
        rec = store.commit("user", {"d": els}, base_revn=store.head_revn())
        return rec["summary"]

    def _order_pairs(self, store: canvas.Store) -> list[tuple[str, bool]]:
        """Per artifact, whether the cached order equals the replayed one.

        Args:
            store: A loaded store, read at its own head.

        Returns:
            `(artifact_id, agrees)` for every artifact in the cache,
            sorted by id.
        """
        replayed = store.state_at(store.head_revn())
        out = []
        for aid in sorted(store.scenes):
            cached = [e.get("id") for e in store.scenes[aid]]
            other = [e.get("id") for e in
                     (replayed.get(aid) or {}).get("elements") or []]
            out.append((aid, cached == other))
        return out

    def test_an_undrifted_project_agrees_and_reports_only_the_edit(
            self) -> None:
        """The live half: matched orders, and a nudge that says `moved: 1`.

        Ungated and asserted in every commit, because both reds below are
        negatives — "no drift", "no phantom facts" — and a negative
        proves nothing if the probe cannot see the positive. This is the
        same builder with `drifted=False`, so it fails if `_drift_project`
        stops producing a loadable project, if `state_at` stops returning
        elements, or if the differ goes mute; each of those would
        otherwise turn both reds green while measuring nothing at all.
        """
        store = self._drift_project(drifted=False)
        self.assertEqual(self._order_pairs(store), [("d", True)])
        summary = self._nudge(store)
        self.assertEqual(summary["verb_counts"], {"moved": 1}, summary)
        self.assertEqual(summary["headline"], "n1 nudged")

    @unittest.expectedFailure
    def test_red_a_drifted_artifact_mints_phantom_reorder_facts(self) -> None:
        """One element moves; the differ reports the other one reordered.

        RED BY ASSERTION. Measured on this scene: `{"moved": 1,
        "reordered": 2}` with the headline "n1 nudged (+2 more)" — the
        magnitude is every element the two orders disagree about (both of
        them here, 28 of 45 on the real fixture) and the direction is
        false-positive, since nothing in this project has been reordered
        by anyone. `n2` was not touched by this commit and is not touched
        by the user in any commit; the reorder is an artifact of the
        differ comparing the posted cache against a replay that never
        agreed with it.

        Flips when something reconciles the two orders — at load, at
        commit, or by making `commit` diff the cache it was handed rather
        than the replay. Owner unassigned, filed for the addendum wave.
        """
        summary = self._nudge(self._drift_project(drifted=True))
        self.assertEqual(summary["verb_counts"], {"moved": 1}, summary)

    @unittest.expectedFailure
    def test_red_every_shipped_artifact_replays_in_its_cached_order(
            self) -> None:
        """The invariant nobody asserts, read over a real recorded session.

        RED BY ASSERTION on `argus-r4-arm3` as committed: `argus-domain`
        holds `pin-watchlist-real` and `r-run-rerun-label` at indices 43
        and 44 in the cache and at 44 and 43 in the replay — same 45
        elements, two of them swapped, on the richest real session on
        record. The other six artifacts agree, which is exactly why the
        storm reads as intermittent rather than as a bug: whether a save
        detonates it depends only on which artifact it lands on.
        `TestArgusR4Arm3Fixture.test_replays_full_history` walks past this
        because it compares `head_revn` and the artifact id SET, never an
        order.

        Element order is paint order, so this is not bookkeeping: the two
        halves of the store disagree about what is drawn on top of what.
        """
        src = Path(__file__).resolve().parent / "fixtures" / "argus-r4-arm3"
        root = Path(tempfile.mkdtemp(prefix="mutants-fixture-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        shutil.copytree(src, root / "project_knowledge")
        pairs = self._order_pairs(canvas.Store(canvas.Project(root)))
        self.assertEqual([aid for aid, ok in pairs if not ok], [],
                         "cache order and replay order disagree")


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

    # -- The four reach gaps Task 33 left open (v0.9 Task-33 review,
    # 2026-08-13; findings 1, 2, 3 and 5, each reproduced by the reviewer
    # against the shipped code). The red above proved the QUARANTINE= line
    # reaches lint and status. These pin the four places that line still
    # does not reach or does not survive: the summary that counts it
    # nowhere, the served-state cap that evicts it, the surface an agent
    # runs FIRST, and the one quarantine producer lint cannot see at all.
    # Same quadruple discipline as the class above, and the same base
    # project wherever the defect allows — `_quarantine_project` carries
    # the first, and only the cap and the pending queue need fixtures of
    # their own, for reasons their docstrings give.

    def _start(self, root: Path) -> list[str]:
        """Run `canvas.py start` against a project and capture its output.

        `cmd_start` reaches its print through one of two paths — spawn a
        server, or reuse a live one — and both end at the SAME `print_kv`
        call (canvas.py), so the reuse path measures the surface
        without a subprocess, a port, or a 20-second wait. `server_alive`
        is what selects between them and it is an HTTP GET, so it is
        patched rather than answered: the alternative is a real listener,
        which would buy nothing here and could hang a commit hook.

        Args:
            root: Project root, as built by `_scratch_project`.

        Returns:
            The command's stdout, split into lines.
        """
        canvas.Project(root).state_path.write_text(json.dumps({
            "url": "http://127.0.0.1:1/", "port": 1, "pid": 1,
            "protocol_version": canvas.PROTOCOL_VERSION,
            "catchup_revn": 0}), encoding="utf-8")
        buf = io.StringIO()
        with mock.patch.object(canvas, "server_alive", lambda s: True), \
                contextlib.redirect_stdout(buf):
            canvas.cmd_start(argparse.Namespace(project=str(root),
                                                no_browser=True))
        return buf.getvalue().splitlines()

    def _serve(self, root: Path) -> canvas.ServerApp:
        """Build the `ServerApp` for a project without listening on a port.

        `ServerApp.__init__` loads the store and then the pending queue
        (canvas.py), and `serve` is a separate call, so the PND-001
        producer runs here with no socket bound. The log handle is closed
        on cleanup because the constructor opens one per app and these
        tests build two over one project.

        Args:
            root: Project root, as built by `_scratch_project`.

        Returns:
            The constructed app, its load already done.
        """
        app = canvas.ServerApp(canvas.Project(root))
        self.addCleanup(app.log_file.close)
        return app

    @unittest.expectedFailure
    def test_red_the_lint_summary_counts_the_quarantines_nowhere(
            self) -> None:
        """The surface prints two dropped files, then totals them as zero.

        The half the flipped red above deliberately did not claim. `lint`
        now NAMES both quarantines and then closes with
        `ARTIFACTS=1 / FINDINGS=1` — a count of loaded artifacts that
        omits the dropped one, and a findings total that counts layout
        lines only. The summary is the part an agent skims, and it reads
        as a clean one-artifact project directly beneath the two lines
        saying it is not.

        The Task 33 implementer asked for a curator ruling on the shape
        rather than picking one, because widening `FINDINGS=` moves a
        number three neighbouring tests read. This is that ruling, and it
        is deliberately more specific than these tests usually are: a
        `QUARANTINED=` key of its own on the summary block, leaving
        `ARTIFACTS=` and `FINDINGS=` exactly as they are. It reads as
        arithmetic that closes — 1 loaded plus 1 dropped is the 2 files
        on disk — and it costs no existing reader a changed number.

        MAGNITUDE is the count, derived from `issues` rather than written
        as a literal, so a quarantine code added later is covered the day
        it is added. DIRECTION is that `FINDINGS=` must NOT absorb them,
        asserted second: that is the trap fix, and it is the one that
        breaks the three neighbours.
        """
        root = self._quarantine_project()
        out = self._lint(root)
        dropped = [i for i in canvas.Store(canvas.Project(root)).issues
                   if not i.get("repaired")]
        self.assertIn(
            "QUARANTINED=%d" % len(dropped), out,
            "%d file(s) left the project and the summary counts them "
            "nowhere: %r" % (len(dropped), out))
        self.assertIn(
            "FINDINGS=1", out,
            "the layout findings total moved — three neighbouring tests "
            "read it, and quarantines belong under a key of their own: %r"
            % (out,))

    @unittest.expectedFailure
    def test_red_a_quarantine_falls_off_the_served_state(self) -> None:
        """Twenty repairs push the dropped file off the resume surface.

        `public_state` ships `self.issues[-20:]` (canvas.py) and
        `cmd_status` prints whatever that carries, so the resume surface
        shows the twenty MOST RECENT issues and a quarantine is filed
        early — at the artifact that failed to load, before any later
        artifact's repairs. Probed on the shipped code: a project with
        one dropped artifact and twenty label refits serves twenty
        `ART-011` entries and zero quarantines, while `lint`, which reads
        `issues` directly, still names the dropped file. The cap defeats
        the Task 33 fix on exactly the projects most likely to carry load
        damage — the busy, long-lived ones.

        The fixture is the smallest the cap's own arithmetic allows:
        below 21 issues nothing is evicted at all. `a` is the dropped
        file and sorts first, which is why its quarantine is the one that
        falls off; the guard below pins that ordering as the premise, so
        a load order that changed would surface as a flip rather than as
        a silent pass.

        MAGNITUDE is which issues survive the cap, and it is asserted as
        the quarantine's PRESENCE rather than as a length or a position:
        the outcome is that a dropped file reaches the agent, and
        ordering, exemption or a raised limit would each satisfy it. That
        choice belongs to the work package that owns the endpoint.
        """
        root = _scratch_project(self, {"a": "[]", "b": _repair_pack(20)},
                                {"0001-x": _GOOD_SAVE})
        served = canvas.Store(canvas.Project(root)).public_state()["issues"]
        self.assertEqual(
            [i["code"] for i in served if not i.get("repaired")],
            ["ART-000"],
            "artifact 'a' was dropped and the served state carries only "
            "repairs (%d of them) — status prints no QUARANTINE= line at "
            "all" % (len(served),))

    @unittest.expectedFailure
    def test_red_start_names_no_load_finding(self) -> None:
        """The first surface an agent runs on resume says nothing happened.

        `cmd_start` prints its `URL=`/`CATCHUP_REVN=` block and stops
        (canvas.py). It is what an agent runs FIRST when resuming a
        session, and probed on the shipped code it is silent about a
        quarantined artifact — the drop is reachable only from a later
        `status`, which an agent that got a working URL has no reason to
        run. Symmetric, and named that way deliberately: repairs are
        equally absent, so the fix is a load-findings block on this
        surface rather than a quarantine special case.

        MAGNITUDE is what the output must CONTAIN — every unrepaired
        issue's code and its message, the same contract the lint red
        holds, so the agent learns which file left. DIRECTION is the
        claim: named as a drop, never as a repair, which is the same trap
        fix that a bare "print the issues" would walk into.

        The REPAIR= assertion at the end was added on 2026-08-15 by the
        silence-pairing census, and it is about the FLIP rather than
        about today's redness. Two silences in this class say `_start`
        prints no `REPAIR=` line — the second assertion below, and the
        `noise` check in
        `test_start_on_a_clean_load_invents_no_load_finding` — and both
        are trivially true while the surface prints no load findings at
        all. The pole that would make them readable cannot exist until
        this red flips, so it is written here, inside the red, where it
        becomes a CONDITION of flipping: a fix that prints quarantines
        and not repairs leaves this failing on its last assertion instead
        of going green and stranding two unpaired silences behind it.
        That is what "the fix is a load-findings block, not a quarantine
        special case" has to mean to be checkable.

        `_OVERSIZED_LABEL_ARTIFACT` and the `REPAIR=ART-011:` prefix are
        borrowed from `test_lint_names_a_load_time_repair` rather than
        respelled, so the two surfaces' repair poles stay one control:
        a reworded heading fails both together instead of retiring this
        one quietly.
        """
        root = self._quarantine_project()
        out = self._start(root)
        joined = "\n".join(out)
        dropped = [i for i in canvas.Store(canvas.Project(root)).issues
                   if not i.get("repaired")]
        missing = [i["code"] for i in dropped
                   if i["code"] not in joined or i["msg"] not in joined]
        self.assertEqual(
            missing, [],
            "the load quarantined %s; start names %s nowhere, so the "
            "first thing the agent reads on resume is %r"
            % ([i["code"] for i in dropped], missing, out))
        self.assertEqual(
            [ln for ln in out if ln.startswith("REPAIR=")
             and any(i["code"] in ln for i in dropped)], [],
            "a quarantine repaired nothing and start files it as repair "
            "work: %r" % (out,))
        repaired = self._start(_scratch_project(
            self, {"a": _OVERSIZED_LABEL_ARTIFACT}, {"0001-x": _GOOD_SAVE}))
        self.assertEqual(
            len([ln for ln in repaired
                 if ln.startswith("REPAIR=ART-011:")]), 1,
            "the load refit a label and start says so nowhere, so the two "
            "REPAIR= silences in this class are asserted against a "
            "channel that has never spoken: %r" % (repaired,))

    @unittest.expectedFailure
    def test_red_a_pending_quarantine_reaches_no_durable_surface(
            self) -> None:
        """The one quarantine `lint` cannot see, and it is unrecoverable.

        PND-001 is filed by `ServerApp.load_pending` (canvas.py),
        not by `Store.load`, and `cmd_lint` builds a bare `Store` — so
        the queue's own quarantine reaches the one surface that needs no
        server nowhere at all. What makes it worse than a reach gap is
        the compounding half, pinned by the guard below: the unreadable
        file is renamed `.bad` as it is quarantined, so the notice exists
        only in that one server's memory. An agent that never runs
        `status` before the server stops loses it permanently, and the
        NEXT load finds nothing to report because there is nothing left
        to fail on.

        MAGNITUDE is what the surface must contain — the code and its
        message, the same contract the lint red holds. DIRECTION is that
        the finding must be recoverable AFTER the fact, from a surface
        that does not need the server that made it, which is what the
        `.bad` rename takes away. `lint` is the surface asserted because
        it is the only server-free one; the outcome is discoverability,
        and a lint that read the queue, a producer that filed to the
        store, or a `.bad` file the loader still reports would each
        satisfy it.
        """
        root = _scratch_project(self, {"a": _GOOD_ARTIFACT},
                                {"0001-x": _GOOD_SAVE},
                                pending={"7.json": '{"id": 7, "batch"'})
        filed = [i for i in self._serve(root).store.issues
                 if i["code"] == "PND-001"]
        out = self._lint(root)
        joined = "\n".join(out)
        missing = [i["code"] for i in filed
                   if i["code"] not in joined or i["msg"] not in joined]
        self.assertEqual(
            missing, [],
            "a queued revision was quarantined and the only server-free "
            "surface names it nowhere: %r" % (out,))

    def test_the_cap_serves_a_quarantine_when_nothing_crowds_it_out(
            self) -> None:
        """The cap's other pole: under the limit, the drop is served.

        The eviction red's neighbour, and the reason it is this project —
        `_quarantine_project` is the same base the class's other reds
        use, differing from the eviction fixture in nothing but the
        twenty repairs standing between the quarantine and the tail. So a
        `public_state` that had simply stopped carrying `issues` at all,
        or a load that had stopped filing quarantines, is caught here
        rather than reading as an unexpected success over there.
        """
        root = self._quarantine_project()
        served = canvas.Store(canvas.Project(root)).public_state()["issues"]
        self.assertEqual(
            sorted(i["code"] for i in served if not i.get("repaired")),
            ["ART-000", "SAV-001"])

    def test_the_evicting_project_files_its_quarantine_first(self) -> None:
        """The eviction red's premise: 41 issues, the drop at the front.

        Its error-red guard, ungated. That red asserts over what the cap
        SERVES, so a fixture that stopped producing repairs — a moved
        ART-011 threshold, a rewritten refit — would leave the quarantine
        inside the last twenty and flip the red green while the endpoint
        stayed exactly as broken. This pins the arithmetic instead: the
        drop is issue zero, forty repairs follow it, and the total is
        twice the cap plus one.

        Forty, not twenty, since v0.9 WP4: each refit resizes its
        container, and a load-time resize now confesses (ART-012) whether
        or not an arrow rode on it. `_repair_pack` carries no arrows
        deliberately, so those confessions all read "no arrow was bound
        to it" — the pairing is what makes the count a checkable
        consequence of the pack size rather than a magic number.
        """
        root = _scratch_project(self, {"a": "[]", "b": _repair_pack(20)},
                                {"0001-x": _GOOD_SAVE})
        codes = [i["code"] for i in canvas.Store(canvas.Project(root)).issues]
        self.assertEqual(codes, ["ART-000"] + ["ART-011", "ART-012"] * 20)
        self.assertGreater(len(codes), 20, "nothing is evicted below 21")
        self.assertEqual(sorted(canvas.Store(canvas.Project(root)).scenes),
                         ["b"])

    def test_start_on_a_clean_load_invents_no_load_finding(self) -> None:
        """The start red's silent pole, and proof its surface was reached.

        Two jobs, which is why the `REUSED=true` assertion sits beside
        the silence. Without the first, a fix that printed a load finding
        unconditionally would satisfy the start red while telling every
        healthy project that a file had been dropped. Without the second,
        a patched `server_alive` that stopped selecting the reuse path —
        or a `cmd_start` signature that moved — would make the red
        measure a command that never ran, and `expectedFailure` swallows
        that as readily as a real failure (doctrine §6).

        The `noise` assertion is a silence with NO firing pole, and the
        silence-pairing census (2026-08-15) could not give it one:
        `cmd_start` prints no load-findings block at all, so no project
        makes a `REPAIR=` or `QUARANTINE=` line appear on this surface
        and the silence is true by construction rather than by health.
        `REUSED=true` proves the command RAN; it cannot prove the
        channel this test claims is quiet exists. The pole is owed by
        whoever flips `test_red_start_names_no_load_finding` above, and
        it is written into that red's last two assertions so the debt is
        executable rather than a note here — when it goes green this
        test's silence becomes readable in the same commit, and until
        then the honest reading of this line is "unpaired, and pinned as
        such next door".
        """
        root = _scratch_project(self, {"a": _GOOD_ARTIFACT,
                                       "b": _GOOD_ARTIFACT},
                                {"0001-x": _GOOD_SAVE})
        out = self._start(root)
        self.assertIn("REUSED=true", out)
        noise = [ln for ln in out if ln.startswith(("REPAIR=", "QUARANTINE="))
                 or "ART-" in ln or "SAV-" in ln]
        self.assertEqual(noise, [],
                         "a clean load was told something happened to it")

    def test_the_pending_quarantine_is_filed_once_and_never_again(
            self) -> None:
        """The PND-001 red's premise, and the half that makes it permanent.

        Its error-red guard, and the compounding claim in one test
        because they are one mechanism. First: the producer really does
        fire, so the red measures REACH rather than a fixture that
        stopped being corrupt. Second: the same project served a second
        time files nothing, because the first server renamed the file to
        `.bad` — which is what makes the missing lint surface a permanent
        loss rather than a delay. Asserted on a second `ServerApp` over
        the same root, since that is exactly the next session.
        """
        root = _scratch_project(self, {"a": _GOOD_ARTIFACT},
                                {"0001-x": _GOOD_SAVE},
                                pending={"7.json": '{"id": 7, "batch"'})
        first = self._serve(root)
        self.assertEqual([i["code"] for i in first.store.issues],
                         ["PND-001"])
        self.assertIn("PND-001", [i["code"] for i in
                                  first.store.public_state()["issues"]],
                      "status is the one surface that sees it; if even "
                      "that goes quiet the red measures nothing")
        self.assertEqual([i["code"] for i in self._serve(root).store.issues],
                         [], "the notice lived only in the first server's "
                         "memory — the queue file is renamed .bad, so no "
                         "later load can rediscover it")


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
# that and pins it GREEN. These three pinned the gaps that suite left: the
# validation that let a negative index through at all, the mapping a negative
# index silently removed, and the create that outlived its rejected batch.
#
# All three FLIPPED in v0.9 Task 34 and now stand as regressions. That task
# also had to re-vehicle one case in `TestFailurePathAtomicity`: it provoked
# its crash WITH the negative index, which its docstring already flagged as
# "a separate, still-open defect", so fixing the defect took the crash away.
# Its crash is synthetic now — a pin on a live bug dies the day the bug does.
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

    def _with_nodes(self, store: canvas.Store) -> canvas.Store:
        """Add two more plain nodes, so the scene has an order to disturb.

        The reorder red needs a list long enough that "the front" and
        "somewhere in the middle" are different answers; `_store`'s
        single node cannot tell them apart. They carry no labels, which
        keeps the stored order short enough to assert in full — the
        label elements a `label` would mint are just noise between the
        positions the red is about.

        Args:
            store: A store from `_store`.

        Returns:
            The same store, so callers can chain the call.
        """
        store.apply_batch({
            "base_revn": store.head_revn(), "artifact": "flow",
            "ops": [{"op": "add", "element": {
                "type": "rectangle", "id": nid, "x": 200 * i, "y": 200,
                "width": 100, "height": 60, "role": "node"}}
                for i, nid in enumerate(("n2", "n3"))]})
        return store

    def _ids(self, store: canvas.Store) -> list[str]:
        """List the element ids in `flow`, in stored order.

        Args:
            store: The store to read.

        Returns:
            Every element id in the artifact's scene, in the order that
            decides paint order — which is what a reorder op moves.
        """
        return [e["id"] for e in store.scenes["flow"]]

    def _send(self, store: canvas.Store,
              op: dict[str, Any]) -> Exception | None:
        """Apply one registry op and hand back whatever escaped, if any.

        The reds turn on WHICH exception reaches the caller: `BatchError`
        becomes the 422 the CLI prints as an `ERROR=` line
        (canvas.py), and nothing else is converted at all. So the
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
        return self._send_ops(store, [dict(op, op="registry")])

    def _send_ops(self, store: canvas.Store,
                  ops: list[dict[str, Any]]) -> Exception | None:
        """Apply a batch of raw ops and hand back whatever escaped.

        The registry-op wrapper above delegates here so the scene-op reds
        and the registry-op reds judge a rejection through one code path:
        the claim they share is about WHICH exception reaches the caller,
        and two helpers could drift on that answer.

        Args:
            store: The store to apply against, at its current head.
            ops: The batch's op list, each carrying its own `op` key.

        Returns:
            The exception the batch raised, or `None` if it was applied.
        """
        try:
            store.apply_batch({"base_revn": store.head_revn(),
                               "artifact": "flow", "ops": ops})
        except Exception as exc:            # broad on purpose: see above
            return exc
        return None

    def _create_batch(self, store: canvas.Store,
                      failing: bool) -> dict[str, Any]:
        """A batch that creates `ghost` and renames it in the same breath.

        `rename_artifact` is what drove this shape onto disk: it wrote
        the new name through to the artifact FILE as it validated, and
        `_seed_created_meta` publishes the create early enough for a
        registry op to name the id its own batch is making — the BUG-03
        workflow. So the create was written from INSIDE the commit
        window rather than after the ops, which is the only way a
        rejection can arrive with the file already written. A create
        plus a failing op alone does not reproduce it, and stopped
        reproducing it as of e2f3bf0.

        Kept verbatim after the fix: this is the shape that has to stay
        harmless. Both poles below are built from it, so a rename that
        crept back inside the window would fail the rejected pole while
        a rename that stopped reaching disk at all would fail the
        accepted one.

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
        """The baseline every test in this class is a deviation from.

        Each one measures what a single batch changes about this store,
        so a harness that quietly stopped being able to BUILD it — a
        `create` block whose schema moved, an `add_mapping` that started
        rejecting this element spelling — would leave the class's
        `expectedFailure`s passing while measuring nothing at all
        (doctrine §6). This is their standing guard, and it covers all
        three fixtures: the empty mapping list, the two-mapping list the
        index reds need, and the three-element scene the reorder red
        needs.

        It guards the class's GREEN tests just as much, which is why it
        outlived the three reds Task 34 flipped: those still assert what
        a rejection leaves behind, and a store that failed to seed would
        make "nothing was left behind" trivially true.
        """
        store, root = self._store()
        self.assertEqual(sorted(store.scenes), ["flow"])
        self.assertEqual(store.registry["mappings"], [])
        self.assertTrue((root / "project_knowledge" / "artifacts" /
                         "flow.excalidraw").exists())
        self.assertEqual(self._ids(self._with_nodes(self._store()[0])),
                         ["n1", "n2", "n3", "n1-label"])
        self.assertEqual(
            [m["concept"] for m in self._with_mappings(store)
             .registry["mappings"]], ["alpha", "beta"])

    def test_red_a_negative_annotate_index_escapes_as_a_bare_crash(
            self) -> None:
        """`index: -1` is refused, not waved past the bounds check.

        The check was `idx >= len(reg["mappings"])`, which is false for
        every negative number, so `-1` was waved through as a valid
        index and `reg["mappings"][-1]` was then evaluated against an
        empty list. The op did not fail the batch; it killed it. An
        `IndexError` left `apply_batch`, where `BatchError` is the only
        exception any caller knows about: `_handle_apply` turns
        `BatchError` into a 422 the CLI prints as `ERROR=` and converts
        nothing else, so the v0.8 promise that every failure prints an
        `ERROR=` line was broken here by a bare traceback the agent
        could neither parse nor act on.

        DIRECTION is the first assertion and the whole point: a batch
        must resolve one way or the other, and this one resolved
        neither. MAGNITUDE, for a rejection, is what the message
        identifies — the action and the field at fault — so the agent
        learns which of its ops was refused rather than guessing. That
        is pinned to what the already-working upper pole says, not to
        new wording, so the fix flipped this without also rewriting a
        message; the neighbour asserts the same two tokens on the same
        empty list.

        Flipped by v0.9 Task 34: the bounds check gained its sign half
        (`0 <= idx < len(...)`) in both mapping-index arms.
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

    def test_red_a_negative_remove_index_pops_the_newest_mapping(
            self) -> None:
        """`index: -1` removes nothing, rather than the newest mapping.

        The same missing sign check as the red above, at the pole where
        the list is NOT empty — so Python's own negative indexing made
        `-1` a perfectly valid subscript and there was no crash for
        anything to notice. `reg["mappings"].pop(-1)` tombstoned the
        NEWEST mapping, the batch committed, and the response reported
        the removal the agent had asked for. The model quietly lost the
        concept most recently attached, and every surface went on
        claiming the op did what was requested.

        MAGNITUDE is which mappings survive — both, `alpha` and `beta`,
        in the order they arrived — and it is asserted FIRST because it
        is the damage. DIRECTION is that the op must be REFUSED rather
        than silently redirected onto a different mapping, asserted
        second because a fix that merely stopped popping without saying
        so would leave the agent believing a mapping was removed.

        The two reds are one defect class under two magnitudes and share
        `_store` for that reason; this one earns its own entry because a
        fix that only guarded the empty list would have left this half
        live. Flipped by v0.9 Task 34 with its twin.
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

    def test_red_a_rejected_create_survives_and_blocks_the_retry(
            self) -> None:
        """A rejected batch's `create` leaves nothing, so the retry lands.

        `_write_artifact` sets `self.scenes[aid]` and writes the
        `.excalidraw` file, and `rename_artifact` used to call it from
        inside the commit window — so by the time the trailing
        `set_round` rejected the batch the artifact was already on disk.
        The e2f3bf0 restore guard puts every renamed artifact back by
        comparing against the pre-image — correctly, and `ghost` is not
        IN the pre-image, so it was skipped rather than removed. What
        was left is an EMPTY scene: the ops that would have drawn into
        it were rolled back, so the store held an artifact with no
        elements and, as the review measured, no `artifact_meta` entry
        either.

        The consequence was not a stray file. The corrected batch — the
        same batch with the typo fixed — could then never be sent,
        because `_validate_batch`'s `elif aid in self.scenes` saw the
        phantom and answered "create: artifact 'ghost' already exists".
        A fresh load took the file at face value and raised no repair,
        and neither error told the agent that dropping the `create`
        block was the way out.

        The OUTCOME is pinned and never the mechanism: unlinking on an
        error path is a decision for the work package that owns the
        write, and a batch that wrote nothing until it was accepted would
        satisfy this equally. v0.9 Task 34 took the second reading — the
        rename's write-through moved OUT of the op and into `commit`'s
        persist block, so no `_write_artifact` runs inside the commit
        window at all and there is no phantom to unlink. MAGNITUDE is
        what survives the rejection —
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

    # -- Two more from the same missing-sign-check family (v0.9 Task-34
    # review, 2026-08-13). Task 34 closed both registry index arms; these
    # are the two places the same predicate is still wrong — the SCENE
    # op's own index, and the type gate that lets a boolean through as a
    # position on either side.

    def test_red_a_negative_reorder_index_sends_the_element_to_the_front(
            self) -> None:
        """`index: -1` is refused instead of moving the element.

        The scene-op cousin of the two registry index arms Task 34
        fixed, and the last place the sign half was missing. The guard
        was `pos = max(0, min(pos, len(els)))`, which does not read `-1`
        the way Python subscripts do — it CLAMPED it — so a negative
        index was silently rewritten to zero and the element went to the
        FRONT. Probed on the shipped code: `reorder n3 index=-1` and
        `reorder n3 index=0` produced byte-identical scenes. All three
        arms now ask `index_fault` (canvas.py), which refuses the
        bottom; the top is still clamped, because an index past the end
        has always meant "to the back" and still does.

        This is the weakest member of the family and is written that way
        on purpose. Nothing crashed, nothing was deleted, and the save
        record narrated the resulting order honestly — an agent reading
        the record saw where the element really went. What it was silent
        about is that where it went is not where the op asked, and the
        clamp made every negative index mean the same thing, so an agent
        that computed `-1` for "last" got the exact opposite of what it
        meant with nothing to tell it so.

        DIRECTION is that the op is refused with a named error, matching
        what the two registry arms do — the same predicate, the same
        answer, so one convention covers all three. MAGNITUDE is that the
        stored order is untouched, asserted first because a refusal that
        still moved the element would leave the damage live.

        The `index: True` case reaches the same arm and was accepted as
        position 1; it is not asserted here because the boolean test
        below owns that shape, and the one shared index predicate that
        flipped this flipped that.
        """
        store, _ = self._store()
        self._with_nodes(store)
        escaped = self._send_ops(store, [{"op": "reorder", "id": "n3",
                                          "index": -1}])
        self.assertEqual(
            self._ids(store), ["n1", "n2", "n3", "n1-label"],
            "reorder index=-1 named no position anyone could have meant "
            "and moved n3 to the front anyway")
        self.assertIsInstance(
            escaped, canvas.BatchError,
            "reorder index=-1 was accepted (escaped=%r) — the record "
            "narrates the new order as though it were asked for"
            % (escaped,))
        said = "\n".join(escaped.errors)
        self.assertIn("reorder", said)
        self.assertIn("index", said)

    def test_red_a_boolean_mapping_index_is_taken_as_a_position(
            self) -> None:
        """`index: true` is refused instead of landing on mapping 1.

        Task 34 gave both arms `not isinstance(idx, int) or not 0 <= idx
        < len(...)`, which closed the sign half and left the type half
        open: `bool` is a subclass of `int` in Python, so `True` is an
        `int`, and `0 <= True < 3` is `0 <= 1 < 3`. Probed on the shipped
        code, `remove_mapping index=true` tombstoned `beta` and
        `annotate_mapping index=true` wrote the note onto `beta` — a
        mapping the batch never named, on a JSON value that is not a
        position at all.

        Both arms are asserted under one entry as a `subTest`, following
        `test_an_index_past_the_end_is_rejected_with_a_named_error`
        directly above: one predicate, copied to two sites, and a fix to
        either alone would have left the other live. The scene op's own
        `reorder` arm had the identical gate and took `True` as position
        1 too — recorded here rather than as a third entry, because a
        shared index predicate was the fix that flipped all of them, and
        `index_fault` (canvas.py) is now the only one.

        DIRECTION is refusal with a named error, the answer both arms
        already gave every other invalid index. MAGNITUDE is that the
        mapping list comes through untouched — same concepts, same
        order, no note — asserted first because it is the damage.
        """
        for action, extra in (("remove_mapping", {}),
                              ("annotate_mapping", {"note": "boom"})):
            with self.subTest(action=action):
                store, _ = self._store()
                self._with_mappings(store)
                escaped = self._send(store, dict(extra, action=action,
                                                 index=True))
                self.assertEqual(
                    [(m["concept"], m.get("note"))
                     for m in store.registry["mappings"]],
                    [("alpha", None), ("beta", None)],
                    "%s index=true was read as position 1 and landed on "
                    "a mapping the batch never named" % (action,))
                self.assertIsInstance(
                    escaped, canvas.BatchError,
                    "%s index=true was accepted (escaped=%r)"
                    % (action, escaped))
                self.assertIn(action, "\n".join(escaped.errors))

    def test_red_a_non_dict_op_escapes_check_batch_as_a_bare_crash(
            self) -> None:
        """A dry run answers the op that is not an op, rather than dying.

        `check_batch`'s docstring is explicit — "Never raises for a
        rejected batch — the errors come back in the payload so a caller
        can report them without a try/except" — and an ops list holding
        anything but a dict used to break that promise before validation
        started: every arm reads `o.get("op")`, so a string gave
        `AttributeError: 'str' object has no attribute 'get'`. Probed on
        all four shapes an agent could plausibly send — a bare string, an
        integer, `None`, and a nested list — and every one escaped
        uncaught, from `apply_batch` as well.

        The same class as the negative-index reds this file already
        holds: a batch must resolve one way or the other, and this one
        resolved neither. It was worse on the dry-run path, because
        `--check` is what an agent reaches for precisely so it can ask
        "would this land?" without handling exceptions, and the answer it
        got was a traceback naming a Python builtin instead of an op.

        v0.9 Task 38 checks the SHAPE of every op before any arm reads
        one, alongside the envelope's own `ops` check, so the batch is
        refused through the same `BatchError` every other malformed op
        takes. The rejection sweep in tests/test_failure_paths.py carries
        this shape too and asserted only that no write survived it, since
        a crash cannot be asked which way it resolved; it now goes down
        the refusal path with the rest.

        DIRECTION is the whole claim and is asserted as the payload
        contract the docstring already makes: `ok` is False and the
        errors come back in the return value. MAGNITUDE, for a rejection,
        is what the message identifies — the op's POSITION, since a
        non-dict has no `op` name to quote and the index is the only
        thing that tells the agent which of its ops to fix.
        """
        store, _ = self._store()
        for bad in ("just a string", 42, None, ["nested"]):
            with self.subTest(op=bad):
                try:
                    out = store.check_batch({
                        "base_revn": store.head_revn(),
                        "artifact": "flow", "ops": [bad]})
                except Exception as exc:
                    self.fail(
                        "check_batch(%r) raised %s: %s — its docstring "
                        "promises the errors come back in the payload so "
                        "a caller needs no try/except"
                        % (bad, type(exc).__name__, exc))
                self.assertFalse(out["ok"], out)
                self.assertIn("op 0", "\n".join(out["errors"]))

    def test_a_well_formed_op_list_still_dry_runs_clean(self) -> None:
        """The dry run's live pole: a legal batch answers `ok` and echoes.

        The non-dict red's neighbour, and its error-red guard. That red
        asserts `ok` is False, which a `check_batch` that had started
        rejecting everything would satisfy while telling the agent its
        good batches would not land either. This pins the other pole on
        the same store: a legal op comes back accepted, with the echo the
        surface exists to provide.
        """
        store, _ = self._store()
        out = store.check_batch({
            "base_revn": store.head_revn(), "artifact": "flow",
            "ops": [{"op": "add", "element": {
                "type": "rectangle", "id": "n9", "label": "N9", "x": 400,
                "y": 0, "width": 100, "height": 60, "role": "node"}}]})
        self.assertTrue(out["ok"], out["errors"])
        self.assertEqual(out["errors"], [])
        self.assertIn("n9", "\n".join(out["intent_echo"]))

    @unittest.expectedFailure
    def test_red_an_accepted_mod_reports_success_and_changes_nothing(
            self) -> None:
        """`mod roundness` on a routed arrow lands nowhere and says nothing.

        The B1 class `MOD_ATTRS`' own comment exists to name — "`el[attr]
        = value` catch-all was how `mod attrs.kind` no-oped with a
        success echo (live_test_2 B1)" — reached through a different
        attribute. `roundness` is in `MOD_ATTRS`, so the op validates
        with zero errors; on a server-routed arrow the routing post-pass
        re-derives the value immediately, and again at load.

        Probed on the shipped code: `roundness` is `None` before and
        `None` after, the batch is accepted, and the echo answers `op 0
        (mod roundness): arrow e1 binds a → b` — a true sentence about
        the arrow that says nothing about the attribute being discarded.
        The save record carries no entry for the artifact at all, since
        nothing changed, so the history does not record the op either.
        Rectangle roundness is unaffected and stays authored.

        On the parent this op WORKED for elbowed arrows, because the old
        routing rule happened to produce `{"type": 2}`; the WP4 switch
        made the coincidence stop holding, so it now always drops. WP4
        stage 3 brings the coincidence BACK for elbows — a routed elbow
        derives `{"type": 2}` again — so the scene is now two ALIGNED
        nodes, whose route is a straight two-point arrow that derives
        None at every roundness rule this repo has ever had. The defect
        was never about elbows; the elbow was only ever the accident
        that hid it, twice. The review filed it minor and it is:
        `references/ops-reference.md`
        documents `roundness` only for rounded rectangles, and the lines
        beside it say an arrow's geometry and bindings are computed. So
        this is an UNDOCUMENTED capability silently lost, not a promise
        broken — which is why the pin is the B1 rule rather than the
        feature.

        DIRECTION is that an accepted op changed something, or the batch
        said why not; either answer is honest and silence is not. It is
        written as a single claim rather than a branch: refused is fine,
        accepted-and-applied is fine, accepted-and-dropped is the defect.
        MAGNITUDE, for an op, is the attribute it named actually holding
        the value it named. "Derived, never authored" is a defensible
        ruling for this attribute — it just has to be SAID, and a refusal
        naming `roundness` would flip this as readily as making it stick.
        """
        store, _ = self._store()
        store.apply_batch({
            "base_revn": store.head_revn(), "artifact": "flow",
            "ops": [{"op": "add", "element": {
                "type": "rectangle", "id": "n2", "label": "N2", "x": 400,
                "y": 0, "width": 100, "height": 60, "role": "node"}},
                {"op": "add", "element": {"type": "arrow", "id": "e1",
                                          "from": "n1", "to": "n2"}}]})
        arrow = next(e for e in store.scenes["flow"] if e["id"] == "e1")
        self.assertEqual(len(arrow["points"]), 2,
                         "the scene must route STRAIGHT, or a derived "
                         "{'type': 2} masks the drop instead of the rule "
                         "being tested")
        escaped = self._send_ops(store, [
            {"op": "mod", "id": "e1", "attrs": {"roundness": {"type": 2}}}])
        arrow = next(e for e in store.scenes["flow"] if e["id"] == "e1")
        self.assertTrue(
            escaped is not None or arrow.get("roundness") == {"type": 2},
            "the mod was accepted with no error and `roundness` is still "
            "%r — the echo reports the arrow and never says the "
            "attribute was discarded" % (arrow.get("roundness"),))

    def test_a_mod_of_an_authored_attribute_takes_effect(self) -> None:
        """The live pole: an attribute the server does not derive sticks.

        The neighbour, and the reason the red above is about `roundness`
        on a ROUTED arrow rather than about `mod` in general. `mod` is
        not broken — a label change lands, is echoed, and rides the save
        record — so a fix that started refusing every `mod` would flip
        that red while deleting the op. This also fixes the contrast the
        red's message rests on: the same batch shape, a different
        attribute, and the value is there afterwards.
        """
        store, _ = self._store()
        escaped = self._send_ops(store, [
            {"op": "mod", "id": "n1", "attrs": {"label": "N1 renamed"}}])
        self.assertIsNone(escaped, "a plain label mod was refused: %r"
                          % (escaped,))
        labels = [e.get("text") for e in store.scenes["flow"]
                  if e.get("containerId") == "n1"]
        self.assertEqual(labels, ["N1 renamed"])

    def test_a_zero_reorder_index_sends_the_element_to_the_front(
            self) -> None:
        """The reorder arm's live pole: asked properly, it moves it there.

        The negative red's neighbour, differing in the SIGN of one field
        and nothing else. Without it, a fix that refused every `reorder`
        — or one that stopped moving anything — would flip that red and
        read as a success while deleting the op. It also fixes the front
        as legitimately reachable, which is what makes "index=-1 must not
        send it to the front" a statement about the ASKING rather than
        about the destination.
        """
        store, _ = self._store()
        self._with_nodes(store)
        escaped = self._send_ops(store, [{"op": "reorder", "id": "n3",
                                          "index": 0}])
        self.assertIsNone(escaped, "a valid reorder was refused: %r"
                          % (escaped,))
        self.assertEqual(self._ids(store), ["n3", "n1", "n2", "n1-label"])

    def test_a_non_integer_mapping_index_is_refused(self) -> None:
        """The type gate's live pole: a string index is turned away.

        The boolean red's neighbour, and the sharpest control available
        for it — `"1"` and `True` are both non-positions handed to the
        same `isinstance(idx, int)` check, and only one of them is
        refused today. So this proves the gate is reached and does fire,
        which means the boolean red measures a hole in a LIVE check
        rather than one that quietly stopped running.
        """
        store, _ = self._store()
        self._with_mappings(store)
        for action in ("annotate_mapping", "remove_mapping"):
            with self.subTest(action=action):
                escaped = self._send(store, {"action": action,
                                             "index": "1"})
                self.assertIsInstance(
                    escaped, canvas.BatchError,
                    "index='1' was not refused: %r" % (escaped,))
                self.assertIn(action, "\n".join(escaped.errors))
        self.assertEqual([m["concept"] for m in store.registry["mappings"]],
                         ["alpha", "beta"])

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
# The queued round (v0.9 Task-10 review, F1 and F2, 2026-08-14). Both are
# GREEN on arrival and both were found by MUTATING a shipped fix rather than
# by reading it: the review deleted each guard in turn and the whole suite
# stayed green. The logic is right; nothing measured it. That is the same
# shape as the `scene_ids` term and the minter seed in the pin family — a
# line doing silent, load-bearing work — and it earns the same treatment.
#
# The fixture is written out here rather than imported from
# `tests/test_failure_paths.py`'s `PendingHarness`. Copying ~20 lines is the
# cheaper of the two costs: importing one test module into another couples
# this file's fixtures to a refactor in a file this agent does not own, and
# the harness there carries restart machinery neither of these needs.
# ---------------------------------------------------------------------------


class TestQueuedRoundIntegrity(unittest.TestCase):
    """A revision waiting behind the banner lands in an honest round."""

    def _app(self) -> Any:
        """Stand a server app over a throwaway project holding one node.

        `ServerApp` rather than `Store` because the pending queue is the
        subject: `queue_pending` stamps the advertised round and
        `load_pending` is the only reader of a queue file. No socket is
        bound — `serve` is a separate call — so this costs a temp
        directory and nothing else.

        Returns:
            The app, its seed batch already applied.
        """
        tmp = Path(tempfile.mkdtemp(prefix="mutants-round-"))
        project = canvas.Project(tmp)
        project.ensure_tree()
        app = canvas.ServerApp(project)
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.addCleanup(self._drop_side_files, project)
        self.addCleanup(app.log_file.close)
        app.store.apply_batch({
            "base_revn": 0, "artifact": "f",
            "create": {"id": "f", "name": "F", "type": "flow",
                       "concept": "checkout", "concept_name": "Checkout"},
            "ops": [{"op": "add", "element": {
                "type": "rectangle", "id": "n1", "label": "N1", "x": 0,
                "y": 0, "width": 100, "height": 60, "role": "node"}}]})
        return app

    def _drop_side_files(self, project: canvas.Project) -> None:
        """Remove the per-process files a `ServerApp` leaves outside the tree.

        The state, events and log paths are derived from a hash of the
        project root and live in a shared runtime directory, so they
        survive the temp tree's removal and would accumulate across runs.

        Args:
            project: The project whose side files should be removed.
        """
        for path in (project.state_path, project.events_path,
                     project.log_path):
            if path.exists():
                path.unlink()

    def _queue(self, app: Any) -> dict[str, Any]:
        """Queue one drawing revision behind the banner.

        `dirty` is forced True because that is what makes a batch HOLD
        rather than apply, and holding is the only way an entry acquires
        a stamped round to go stale.

        Args:
            app: The app from `_app`.

        Returns:
            The queue entry, carrying the round the banner advertised.
        """
        app.dirty = True
        return app.queue_pending(
            {"base_revn": app.store.head_revn(), "artifact": "f",
             "note": "queued", "ops": [{"op": "mod", "id": "n1",
                                        "attrs": {"label": "N1 (edited)"}}]},
            pin_only=False)

    def _advance_round(self, app: Any) -> None:
        """Move the committed round on without going through the queue.

        A user save followed by an agent commit is what bumps `round`:
        the auto-bump fires when an agent commit follows a non-agent
        one. `dirty` is cleared first so these do not hold behind the
        banner themselves — which is the whole interleaving, an ordinary
        turn happening while a revision waits.

        TWO turns, not one, and the count is load-bearing: the banner
        advertises one round AHEAD, so a single turn only brings the
        committed round level with the stamped floor, where a floor and
        an assignment agree. The second turn is what puts the session
        past it. The caller asserts that gap as its premise rather than
        trusting this count.

        Args:
            app: The app from `_app`, with an entry already queued.
        """
        app.dirty = False
        for i in range(2):
            app.store.commit(author="user", new_scenes={},
                             user_note="a user save")
            app.store.apply_batch({
                "base_revn": app.store.head_revn(), "artifact": "f",
                "ops": [{"op": "mod", "id": "n1",
                         "attrs": {"label": "N1 (theirs %d)" % i}}]})

    def test_the_queue_stamps_the_round_it_advertised(self) -> None:
        """The premise both tests below deviate from, asserted ungated.

        Each of them measures what happens to a stamped round, so a
        fixture that stopped stamping one — a `queue_pending` that no
        longer holds under `dirty`, a `round` key that moved — would
        make both pass while measuring nothing. This pins the stamp
        itself: the entry carries a round, and it is one AHEAD of the
        committed one, because the banner advertises the round the
        session will be in once the user answers.
        """
        app = self._app()
        entry = self._queue(app)
        self.assertEqual(len(app.pending), 1)
        self.assertEqual(entry.get("round"),
                         app.store.registry.get("round", 0) + 1)

    def test_a_stale_advertised_round_never_pulls_the_commit_backward(
            self) -> None:
        """The floor is a floor, not an assignment.

        `commit` takes `rnd = max(rnd, min_round or 0)`, and the `max`
        is the whole of it. The advertised round is a FLOOR so that
        draining the queue cannot take back the +1 the banner promised
        (r5-2); but the session keeps moving while an entry waits, so by
        the time the user answers, that floor can be BELOW the committed
        round. A blind `rnd = min_round` would then drag the save
        backward into a round that has already closed — r5-2's harm from
        the other side, and the review proved both that mutation and the
        line's outright deletion leave the whole suite green.

        The interleaving is built rather than asserted about: queue under
        a dirty canvas so the entry holds, then run an ordinary
        user-then-agent turn that does NOT go through the queue, which
        bumps the committed round past the stamped one. The staleness is
        asserted as a PREMISE first — without it this scene proves
        nothing, since a floor equal to the round is satisfied by either
        implementation.

        Both directions are then pinned in one assertion: the record
        lands on the CURRENT round. Higher than the floor rules out the
        blind override; equal to the live round rules out the deletion,
        which would leave it stuck one below.
        """
        app = self._app()
        entry = self._queue(app)
        stamped = entry.get("round")
        self._advance_round(app)
        committed = app.store.registry.get("round")
        self.assertLess(
            stamped, committed,
            "the committed round did not overtake the stamped floor, so "
            "this scene cannot tell a floor from an assignment")
        record = app.commit_pending(entry)
        self.assertEqual(
            record.get("round"), committed,
            "a queued revision landed in round %r while the session is "
            "in %r — the stale floor it advertised pulled the save back "
            "into a round that had already closed"
            % (record.get("round"), committed))

    def test_a_malformed_stamped_round_is_coerced_on_restore(self) -> None:
        """A hand-edited queue file cannot poison the user's Apply.

        `load_pending` coerces a non-integer stamped round to `None`,
        and the reason is the distance between the two events: the file
        is READ at start-up and the value is USED much later, inside
        `commit`'s `max()`, at the moment the user clicks Apply. A
        string there raises `TypeError` in a place with no relation to
        the file that caused it, and the user's click is what surfaces
        it.

        The values are chosen for the three distinct ways the guard
        earns its keep, which is why they are not all "junk": `"3"`
        CRASHES the `max()`, `2.5` would stamp a FLOAT round onto a save
        record, and `True` is the quiet one — `bool` is an `int`, so it
        passes an `isinstance` check and silently means round 1. `[]`
        and `None` are harmless today (both are falsy, so `min_round or
        0` swallows them) and are kept as the controls that show the
        guard is not merely rejecting everything.

        Asserted end to end, through the restart the queue exists to
        survive: the entry comes back with `round` None, and the Apply
        that follows lands without raising and in the session's own
        round.
        """
        for value in ("3", 2.5, True, [], None):
            with self.subTest(round=value):
                app = self._app()
                entry = self._queue(app)
                path = (app.project.pk / ".pending" /
                        ("%d.json" % entry["id"]))
                doc = json.loads(path.read_text(encoding="utf-8"))
                doc["round"] = value
                path.write_text(json.dumps(doc), encoding="utf-8")
                app.log_file.close()
                restarted = canvas.ServerApp(app.project)
                self.addCleanup(restarted.log_file.close)
                self.assertEqual(len(restarted.pending), 1)
                self.assertIsNone(
                    restarted.pending[0].get("round"),
                    "a %s round survived the restore and is about to "
                    "reach commit's max()" % type(value).__name__)
                expected = restarted.store.registry.get("round", 0)
                try:
                    record = restarted.commit_pending(restarted.pending[0])
                except Exception as exc:
                    self.fail(
                        "the user's Apply died on a queue file written "
                        "with round=%r (%s: %s)"
                        % (value, type(exc).__name__, exc))
                self.assertEqual(record.get("round"), expected)


# ---------------------------------------------------------------------------
# The WP4 geometry rulings (Task 15 and 16 reviews, 2026-08-14). Four
# decisions the shape-clipping work made deliberately and left unmeasured —
# each one verified here by mutating the shipped code and watching the whole
# suite stay green first, which is how all four were found in the first
# place rather than by reading.
#
# These are RULINGS, not defects, so every entry is green on arrival and
# says which way the ruling went. That is the point: a decision nothing
# measures is indistinguishable from an accident, and the next person to
# touch the geometry cannot tell which of these numbers they are allowed to
# move. Two of the four are `test_backend.py`'s subject at a different
# layer — the router's own assertions live there — and are written here
# against the INSTRUMENTS and the gate instead, deliberately: the
# instruments are the independent measure of what canvas.py draws, and
# `float_diamond` reading the true outline is what caught `edge_anchor`
# anchoring on the bounding box months before the lint agreed.
# ---------------------------------------------------------------------------


class TestRouterGeometryRulings(unittest.TestCase):
    """Four WP4 decisions, each pinned the way it was decided."""

    def _diamond(self) -> dict[str, Any]:
        """The 200x100 rhombus every endpoint mutant in this file uses.

        Returns:
            A diamond node at (300,300), so a = 100 and b = 50 and the
            perpendicular centre-to-facet distance is 44.72136.
        """
        return {"type": "diamond", "id": "d1", "x": 300, "y": 300,
                "width": 200, "height": 100,
                "customData": {"role": "node"}}

    def test_the_router_anchors_a_diamond_on_its_outline(self) -> None:
        """Task 15's fix, measured by the instrument that caught the bug.

        `edge_anchor` used to return a point on the bounding BOX, which
        on a rhombus is out in the empty corner — the seeded mermaid flow
        shipped an arrow attaching 19.1px clear of the shape it claimed
        to bind. `float_diamond` had been reporting it the whole time.

        So the pin is written at the instrument layer rather than as a
        second clearance assertion: the detector that was right before
        the router was must read zero on what the router now produces.
        Both poles are asserted from one scene — the old box anchor still
        earns a finding, the new outline anchor earns none — so a
        `float_diamond` that had simply stopped firing cannot pass this.
        """
        node = self._diamond()
        ax, ay = canvas.edge_anchor(node, 900, 900)
        cx = node["x"] + node["width"] / 2.0
        cy = node["y"] + node["height"] / 2.0
        dx, dy = 900 - cx, 900 - cy
        scale = min((node["width"] / 2.0) / abs(dx),
                    (node["height"] / 2.0) / abs(dy))
        box = (cx + dx * scale, cy + dy * scale)
        self.assertNotAlmostEqual(
            ax, box[0], places=3,
            msg="the outline anchor and the box anchor agree here, so "
                "this scene cannot tell the two apart")
        for name, (x, y) in (("outline", (ax, ay)), ("box", box)):
            with self.subTest(anchor=name):
                arrow = el(id="a1", type="arrow", x=x, y=y, width=1,
                           height=1, points=[[0, 0], [1, 1]],
                           customData={"role": "edge"},
                           startBinding={"elementId": "d1", "focus": 0,
                                         "gap": 1})
                found = instruments.float_diamond([node, arrow])
                if name == "outline":
                    self.assertEqual(found, [], "the router anchored off "
                                     "the drawn outline: %r" % (found,))
                else:
                    self.assertEqual(len(found), 1, found)
                    self.assertGreater(found[0]["gap"], 12)

    def test_a_rectangle_never_reaches_the_shape_clip(self) -> None:
        """The type gate is asserted directly, not through its arithmetic.

        `test_router_anchor_on_a_rectangle_is_byte_identical`
        (tests/test_backend.py) pins that a rectangle keeps the closed
        form, and the Task-15 review showed its four probe points all
        land in the bit-exact set — widening the gate to admit
        rectangles passes it today. I re-measured: the two paths agree
        exactly at those four points and differ only by one ULP anywhere
        they differ at all (326.28992628992626 against 326.2899262899263),
        so no choice of probe point makes that test robust.

        The gate is therefore asserted as a gate. `shape_clip` is spied
        on, and the claim is that a rectangle never consults it while a
        diamond always does — immune to float drift, and it fails on the
        widening the arithmetic cannot see. The rectangle's ANSWER stays
        `test_backend.py`'s subject; this pins the path it takes to get
        there.
        """
        rect = dict(self._diamond(), type="rectangle")
        for node in (rect, self._diamond()):
            with self.subTest(shape=node["type"]):
                seen = self._anchor_consults_shape_clip(node)
                self.assertEqual(
                    seen, node["type"] == "diamond",
                    "%s: shape_clip consulted=%s — the type gate admits "
                    "the wrong set" % (node["type"], seen))

    def _anchor_consults_shape_clip(self, node: dict[str, Any]) -> bool:
        """Whether anchoring on `node` reaches `canvas.shape_clip`.

        A factory rather than a closure built in the caller's loop: a
        spy defined inside the loop would capture the loop variable and
        answer for whichever iteration ran last (ruff B023, which this
        repo treats as defect-shaped rather than stylistic).

        Args:
            node: The element to anchor on.

        Returns:
            True if `shape_clip` was called during the anchor.
        """
        seen: list[tuple[Any, ...]] = []
        real = canvas.shape_clip

        def spy(*args: Any, **kw: Any) -> Any:
            """Record the call, then answer as the real one does.

            Args:
                *args: Forwarded to `canvas.shape_clip`.
                **kw: Forwarded to `canvas.shape_clip`.

            Returns:
                Whatever `canvas.shape_clip` returns.
            """
            seen.append(args)
            return real(*args, **kw)

        with mock.patch.object(canvas, "shape_clip", spy):
            canvas.edge_anchor(node, 900, 900)
        return bool(seen)

    def test_the_endpoint_tolerance_scales_with_the_node(self) -> None:
        """A 20px gap on a 300px node is silent BY DESIGN, and that is new.

        Task 15 replaced a flat 14px endpoint tolerance with
        `endpoint_tol`, which never tightens below the flat floor and
        scales at 10% of the node's short side. The consequence is a
        DIRECTION change nothing guarded: a 20px-off endpoint on a
        300x300 rectangle was an ERROR before and is silence now.

        The Task-15 review named this "the one I would actually want
        encoded", and it is ruled INTENDED — the same slack reads as
        sloppy on a 60px pill and invisible on a 240px diamond, which is
        the brief's own argument. So it is pinned AS intended, with both
        poles on one scene: silent above the flat floor and below the
        scaled one, still firing above the scaled one. Without the
        second half a fix that muted `endpoint_gap` entirely would read
        as correct.

        The small node is asserted alongside because the floor is a
        FLOOR: the same 20px gap must keep firing where the node is too
        small to earn the extra slack, which is what stops the scaling
        from being read as a blanket loosening.
        """
        self.assertEqual(canvas.endpoint_tol({"width": 300,
                                              "height": 300}, 14), 30)
        self.assertEqual(canvas.endpoint_tol({"width": 120,
                                              "height": 120}, 14), 14)
        for size, gap, fires in ((300, 20, False), (300, 35, True),
                                 (120, 20, True)):
            with self.subTest(size=size, gap=gap):
                said = self._gap_finding(size, gap)
                self.assertEqual(
                    bool(said), fires,
                    "%dx%d node, %dpx gap, tolerance %g: %s"
                    % (size, size, gap,
                       canvas.endpoint_tol({"width": size,
                                            "height": size}, 14),
                       said or "silent"))

    def _gap_finding(self, size: int, gap: int) -> list[str]:
        """Lint one scene whose arrow ends `gap` px short of a node.

        Both ends are bound so the half-unbound rule stays out of the
        way — it fires on a one-sided binding and would answer for the
        endpoint check it is not.

        Args:
            size: The bound node's width and height.
            gap: How far short of the node's left facet the arrow stops.

        Returns:
            The endpoint-gap messages naming the arrow, if any.
        """
        y = 300 + size / 2.0
        src = el(id="n0", type="rectangle", x=0, y=y - 30, width=60,
                 height=60, customData={"role": "node"})
        tgt = el(id="n1", type="rectangle", x=300, y=300, width=size,
                 height=size, customData={"role": "node"})
        arrow = el(id="a1", type="arrow", x=60, y=y, width=240 - gap,
                   height=0, points=[[0, 0], [240 - gap, 0]],
                   customData={"role": "edge"},
                   startBinding={"elementId": "n0", "focus": 0, "gap": 1},
                   endBinding={"elementId": "n1", "focus": 0, "gap": 1})
        li = canvas.lint_layout([src, tgt, arrow])
        return [m for m in li["errors"] + li["warnings"]
                if "a1" in m and "point ends" in m]

    def test_a_ray_along_a_facet_touches_the_node(self) -> None:
        """The closed-interval ruling, pinned where the branch actually is.

        Task 16's review proved the exact-tangency region unguarded in
        BOTH directions: flipping `shape_clip`'s parallel-plane branch
        from `room < 0` to `room <= 0` changed nothing across all 782
        tests, while the region demonstrably re-routes a real fixture
        arrow by 72px. The ruling recorded in the fix round is that the
        interval is CLOSED — a path lying exactly along a facet TOUCHES
        the node — and this is that ruling made executable.

        Written against `shape_clip` rather than against the review's
        `_seg_hits_rect` reproducer, and the difference matters. I wrote
        this pin on `_seg_hits_rect(102, 0, 102, 400, ...)` first,
        applied the `<=` mutation, and it did not move: that reproducer
        demonstrates the OLD-versus-NEW predicate change the review was
        arguing about, and it never reaches the parallel-plane branch,
        so a pin built on it would have looked like a guard and held
        nothing. The branch is only reachable when the ray is parallel
        to a facet plane AND `room` is exactly zero, which means starting
        the ray ON the border and pointing it along.

        Both poles, on integer geometry so rounding cannot drift them:
        a ray starting on the left border going straight down returns a
        span, and one starting a pixel OUTSIDE it returns None. The
        second half is what keeps the ruling from being read as "the
        parallel branch always hits" — outside is still outside — and it
        is the pole the mutation leaves alone, so it also proves the
        scene reaches the branch at all.
        """
        node = {"type": "rectangle", "x": 100, "y": 100,
                "width": 200, "height": 100}
        for label, px, py, dx, dy, touches in (
                ("on the left border, pointing along it", 100, 0, 0, 1,
                 True),
                ("on the top border, pointing along it", 200, 100, 1, 0,
                 True),
                ("a pixel outside the left border", 99, 0, 0, 1, False),
                ("a pixel above the top border", 200, 99, 1, 0, False)):
            with self.subTest(ray=label):
                span = canvas.shape_clip(node, px, py, dx, dy)
                self.assertEqual(
                    span is not None, touches,
                    "a ray %s got %r; the closed interval says a path on "
                    "the border touches and one outside it does not"
                    % (label, span))

    def test_the_inset_outline_is_where_the_hit_test_turns_over(
            self) -> None:
        """The consequence the review reproduced, kept as its own claim.

        The reviewer's integer reproducer, and deliberately a SEPARATE
        entry from the ruling above: this one pins where the inset
        outline sits, which is what `_seg_hits_rect` answers, and the
        mutation on the parallel branch does not touch it. Keeping them
        apart is the honest split — one test cannot pin two independent
        decisions, and merging them would have let this half's green
        vouch for the other half's silence.

        The 200x100 rectangle insets to x=102 and x=298, and the two
        neighbouring integers on each side are asserted with the border
        itself so a fix cannot move the inset while keeping the
        turnover.
        """
        node = {"type": "rectangle", "x": 100, "y": 100,
                "width": 200, "height": 100}
        for x, hits in ((101, False), (102, True), (298, True),
                        (299, False)):
            with self.subTest(x=x):
                self.assertEqual(
                    canvas._seg_hits_rect(x, 0, x, 400, node), hits,
                    "a segment on x=%d %s the inset outline"
                    % (x, "must hit" if hits else "must miss"))


# ---------------------------------------------------------------------------
# Pin identity (v0.9 Task-7 review, 2026-08-13; findings M2, R1 and the
# report's own disclosure, each reproduced by the reviewer against the
# shipped code). The class above judges what a batch does to the MODEL;
# this one judges what a PIN op does to the pin lifecycle — the ❓ on the
# canvas, the registry record standing behind it, and the sentence the agent
# is handed about both. The three defects share one base store because they
# share one root: a pin id is treated as an identity without ever being
# checked for ROLE or for UNIQUENESS.
#
# The sixth subject in this file to fall outside the scene unit. The evidence
# is a store's answer plus a printed echo, so there is no element list and no
# `collect_findings` to judge one with. `mutants new` does not decline the
# subject — it writes an element-list scaffold and WARNS that the code names
# no `DETECTORS` entry — but that scaffold cannot express a batch, so the
# quadruple is kept by hand exactly as the two classes above keep it.
#
# BASE: two artifacts, one pin, ids known exactly. MUTATION: one field — an
# element reusing the id its own batch resolves, or a `pin` op reusing an id
# the registry already holds. MAGNITUDE is what SURVIVES the op (which
# elements stand, how many registry records exist) and, for the echo, what
# the sentence must CLAIM. DIRECTION is which way the batch resolved —
# refused with a named error, or accepted and acted on — and which claim the
# echo makes. NEIGHBOURS sit at the same predicates' other poles: the same
# batch with no id collision, a fresh pin id, and a resolve that really does
# take a glyph down.
#
# Deliberately NOT re-covered here: cross-artifact resolve/glyph parity.
# `TestCrossArtifactPinResolution` (tests/test_failure_paths.py) owns that
# and pins it GREEN, including the id-shadowing add that earned the role gate
# on `here`. These pin the gaps that suite leaves — the role gate that was
# never added to `apply_ops`' own resolve arm, the uniqueness check no
# validation performs, and an echo claiming a deletion the record denies.
#
# THE DOOR CENSUS (v0.9 Task-38 review, 2026-08-13). Every way found so far
# for one pin id to end up naming two questions, or for a ❓ to outlive its
# answer, and where each is pinned. Written as an enumeration rather than as
# a completeness claim: it says these doors are covered, not that no other
# door exists, and the difference is the whole reason the F-2 prose finding
# was raised against this task's own comments.
#
#   1. `pin` op spelling an id the registry has filed      green (Task 35)
#   2. `pin` op spelling an id its own batch just spelled  green (Task 35)
#   3. `pin` op spelling an id already RESOLVED            green, unpinned
#                                                          until Task-38 F-2
#   4. auto-mint colliding across two artifacts            green (Task 38)
#   5. explicit id, then an auto-mint of the same          green (Task 38)
#   6. auto-mint, THEN an explicit id of the same          green (Task 40)
#   7. `add` of a pin-role element under a filed id        green (Task 40)
#   8. resolve + same-id pin-role `add` in one batch       green (Task 40)
#   9. resolve naming an ordinary, non-pin element         green (Task 38)
#  10. `pin` op spelling an ordinary ELEMENT's scene id    green (Task 41)
#
# The tenth door arrived, which is the point of writing this as an
# enumeration. The row above it read "every door found so far is now
# shut ... it says these nine are covered, not that a tenth cannot
# exist", and a tenth existed. That sentence cost nothing to write and
# would have cost a reader real time had it said "closed" instead.
#
# Door 10 is door 7's mirror — 7 guards the `add` side against a filed
# PIN id, 10 is a `pin` op walking onto an ordinary ELEMENT's id, and
# `make_element` already refuses exactly that collision for an `add`.
# One-of-two-doors is now the shape of four separate entries here (1/7,
# 7/10), which is worth knowing before the next id check is written on
# one side only. Task 41 shut 10 by copying `make_element`'s message
# rather than writing a second one, which is the cheap half of that
# lesson: when two doors open on one wound, they should also answer in
# one voice.
#
# Doors 7 and 8 closed together on one check, as this census predicted —
# 7 is the mechanism 8 rides in on — and closing them made the shadow
# scene illegal, which retired one test and re-authored another. Both
# are recorded where they happened rather than here: see the ENACTMENT
# paragraph on the door-8 red, and the retirement comment standing where
# `test_the_shadowed_resolve_reports_the_removal_it_made` used to be.
#
# Deliberately NOT a census row: a `pin` op whose `target` is not a
# string (`test_red_a_non_string_pin_target_arrives_as_an_internal_
# error`). It reached the E-9 backstop until Task 41 named the field,
# and either way the batch is refused whole, so it produces neither of
# the two harms this census tracks — no second question under one id, no
# ❓ outliving its answer. It is filed with this family because it is the
# same op's field, and saying why it is not a door keeps the census
# meaning what it says.
# ---------------------------------------------------------------------------


class TestPinIdentityIntegrity(unittest.TestCase):
    """A pin id names one question, or the ❓ outlives the answer."""

    def _store(self) -> canvas.Store:
        """Build a two-artifact project carrying one pin on `flow`.

        Two artifacts are the fewest this family can be written with: the
        resolve arm that goes wrong is reached from a batch scoped
        somewhere ELSE, so a one-artifact store cannot express "the pin
        lives over there" at all. `n1` and `m1` exist only to give each
        artifact something legal to pin to, and to give a colliding add
        somewhere to land.

        Returns:
            The loaded store — `flow` holding node `n1` and glyph
            `pin-a`, `other` holding node `m1`, one open registry pin.
        """
        tmp = Path(tempfile.mkdtemp(prefix="mutants-pin-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        project = canvas.Project(tmp)
        project.ensure_tree()
        store = canvas.Store(project)
        for aid, nid in (("flow", "n1"), ("other", "m1")):
            store.apply_batch({
                "base_revn": store.head_revn(), "artifact": aid,
                "create": {"id": aid, "name": aid.title(), "type": "flow",
                           "concept": "checkout",
                           "concept_name": "Checkout"},
                "ops": [{"op": "add", "element": {
                    "type": "rectangle", "id": nid, "label": nid.upper(),
                    "x": 0, "y": 0, "width": 100, "height": 60,
                    "role": "node"}}]})
        store.apply_batch({
            "base_revn": store.head_revn(), "artifact": "flow",
            "ops": [{"op": "pin", "id": "pin-a", "target": "n1",
                     "question": "Which one?"}]})
        return store

    def _send(self, store: canvas.Store, artifact: str,
              ops: list[dict[str, Any]]) -> Exception | None:
        """Apply one batch and hand back whatever escaped, if any.

        Broad by the same reasoning as `TestBatchPathIntegrity._send`:
        the reds turn on WHICH way the batch resolved, so the exception
        has to be a value to assert about rather than a type an
        `assertRaises` has narrowed in advance.

        Args:
            store: The store to apply against, at its current head.
            artifact: The artifact the batch is scoped to.
            ops: The batch's op list.

        Returns:
            The exception the batch raised, or `None` if it was applied.
        """
        try:
            store.apply_batch({"base_revn": store.head_revn(),
                               "artifact": artifact, "ops": ops})
        except Exception as exc:            # broad on purpose: see above
            return exc
        return None

    def _ids(self, store: canvas.Store, artifact: str) -> list[str]:
        """List the element ids standing in one artifact.

        Args:
            store: The store to read.
            artifact: Which artifact's scene to list.

        Returns:
            Every element id in that scene, in stored order.
        """
        return [e["id"] for e in store.scenes[artifact]]

    def _glyphs(self, store: canvas.Store, artifact: str) -> list[str]:
        """List the ❓ elements standing in one artifact.

        Role rather than id or type, because that is what every arm of
        the resolve path judges by, and because the reds here turn on a
        namesake whose ROLE is the only thing distinguishing it.

        Args:
            store: The store to read.
            artifact: Which artifact's scene to search.

        Returns:
            The ids of the pin-role elements in that scene.
        """
        return [e["id"] for e in store.scenes[artifact]
                if canvas.role_of(e) == "pin"]

    def _orphan_labels(self, store: canvas.Store,
                       artifact: str) -> list[str]:
        """Name the bound labels whose container is no longer there.

        The damage a role-blind deletion leaves behind, and the reason
        the red asserts it separately from the missing shape: a label
        whose `containerId` names nothing is drawn at its last known
        position with nothing under it, so the picture keeps a caption
        for a box the reader cannot see.

        Args:
            store: The store to read.
            artifact: Which artifact's scene to check.

        Returns:
            The ids of bound labels pointing at absent containers.
        """
        live = {e["id"] for e in store.scenes[artifact]}
        return [e["id"] for e in store.scenes[artifact]
                if e.get("containerId") and e["containerId"] not in live]

    def _legacy_twin(self, store: canvas.Store) -> None:
        """Give `other` a second ❓ filed under `pin-a`, as stored data.

        The corpus the OLD auto-minter left behind: it deduped against
        the batch artifact's scene alone, so two artifacts holding a node
        of the same name got two questions under one pin id. Task 38
        stopped that being reachable through ops — the minter consults
        the registry and the explicit-id check refuses the spelling — so
        the state can only be built the way a legacy project carries it,
        as elements and records written straight into the store.

        That is the point rather than a shortcut: the fix does not
        retro-repair the projects the bug already wrote, and a resolve
        aimed at one of these has to take both glyphs or it strands one.

        Args:
            store: A store from `_store`, mutated in place to hold a
                second `pin-a` ❓ on `other` with its own open record.
        """
        twin = dict(next(e for e in store.scenes["flow"]
                         if canvas.role_of(e) == "pin"))
        twin["x"], twin["y"] = 300, 300
        store.registry["pins"].append(dict(store.registry["pins"][0],
                                           artifact="other",
                                           question="And this one?"))
        store.commit(author="user",
                     new_scenes={"other": [*store.scenes["other"], twin]})

    def _pin_records(self, store: canvas.Store,
                     pid: str) -> list[dict[str, Any]]:
        """Collect every registry pin filed under one id.

        Args:
            store: The store to read.
            pid: The pin id to count records for.

        Returns:
            Each registry record carrying that id — more than one is the
            corruption the duplicate-id red measures.
        """
        return [p for p in store.registry["pins"] if p["id"] == pid]

    def test_the_seeded_store_holds_one_pin_on_its_own_artifact(
            self) -> None:
        """The baseline every test in this class is a deviation from.

        Their standing guard, ungated. Each red measures what ONE batch
        changes about this store, so a harness that quietly stopped being
        able to build it — a `create` block whose schema moved, a `pin`
        op that started rejecting this spelling — would leave the class's
        `expectedFailure`s passing while measuring nothing at all
        (doctrine §6). The glyph's HOME is asserted, not merely its
        existence: `pin-a` living on `flow` while every mutated batch is
        scoped to `other` is the whole geometry of this family.

        It guards the green tests just as hard, which is why it outlived
        the three reds Task 35 flipped: those still assert what a batch
        leaves behind, and a store that failed to seed would satisfy them
        by having nothing to leave.
        """
        store = self._store()
        self.assertEqual(sorted(store.scenes), ["flow", "other"])
        self.assertEqual([e["id"] for e in store.scenes["flow"]
                          if canvas.role_of(e) == "pin"], ["pin-a"])
        self.assertEqual([e["id"] for e in store.scenes["other"]
                          if canvas.role_of(e) == "pin"], [])
        self.assertEqual([(p["id"], p["status"])
                          for p in store.registry["pins"]],
                         [("pin-a", "open")])

    def test_red_a_resolve_deletes_a_same_id_node_from_its_own_batch(
            self) -> None:
        """A resolve takes down the ❓ and never a namesake.

        `apply_ops`' resolve arm was `el = index.get(op.get("id"))`
        followed by an unconditional delete (canvas.py). `index` is
        the batch artifact's, keyed by id alone, so the arm never asked
        what it was deleting. Task 7 added exactly that role gate to the
        two places it thought about — the cross-artifact scan and the
        `here` set that skips it (canvas.py) — and left the
        arm those two feed into role-blind.

        So a batch that added a shape and then resolved a pin of the same
        id deleted its own new shape. Probed on the shipped code: `other`
        went in holding `m1` and came out holding `m1` and
        `pin-a-label` — the caption for a rectangle that was created and
        destroyed inside one batch — while the save record named only
        `add pin-a-label`, so nothing in the history said the shape ever
        left. The foreign ❓ on `flow` was removed correctly; that half is
        Task 7's and still works.

        v0.9 Task 35 took the role-gate reading: the arm now asks
        `role_of(el) == "pin"` before it deletes, which is the same
        question the scan and the `here` guard above it already ask. The
        add-first ordering this case sends is the one that reproduced —
        resolve-then-add was already clean via e7a5527, and stays so in
        `test_an_id_shadowing_add_cannot_hide_the_foreign_pin`.

        MAGNITUDE is what survives the batch, asserted in two parts
        because they fail independently: no label may be left pointing at
        an absent container, and an ACCEPTED batch's own add must be on
        the canvas. DIRECTION is the second assertion's other half — a
        batch refused for the id collision satisfies it, because refusing
        and applying are both honest answers and silently doing neither
        is not. The outcome is pinned and never the mechanism: a role
        gate on the arm and a validation-time collision check would both
        flip this, and choosing between them belongs to the work package
        that owns the write.
        """
        store = self._store()
        escaped = self._send(store, "other", [
            {"op": "add", "element": {
                "type": "rectangle", "id": "pin-a", "label": "New Box",
                "x": 200, "y": 0, "width": 100, "height": 60,
                "role": "node"}},
            {"op": "resolve_pin", "id": "pin-a", "answer": "yes"}])
        self.assertEqual(
            self._orphan_labels(store, "other"), [],
            "the resolve deleted the rectangle its own batch had just "
            "added and left the caption behind: other=%r"
            % (self._ids(store, "other"),))
        self.assertTrue(
            escaped is not None or "pin-a" in self._ids(store, "other"),
            "the batch was accepted and its own new rectangle is gone — "
            "no error, no record of the deletion, other=%r"
            % (self._ids(store, "other"),))

    def test_red_a_duplicate_pin_id_is_accepted_by_validation(self) -> None:
        """A second `pin` op reusing a live pin's id is refused.

        `_validate_batch` checked a `resolve_pin` against the known pins
        (canvas.py) and checked a `pin` op against nothing of the
        sort, so the id an open question already owned could be minted a
        second time. Probed on the shipped code: `check_batch` answered
        `ok=True` with no errors, the batch applied, and the registry
        held two records under one id — two different questions, on two
        different artifacts, sharing one name.

        What that cost was downstream and total. The resolve
        write-through is id-global (canvas.py), so ONE
        `resolve_pin dup` marked both records resolved, and the
        cross-artifact scan took both glyphs. The user answered one
        question and the tool closed two, with the unanswered one gone
        from the canvas and counted nowhere. The same asymmetry is what
        the Task 7 re-check filed as R1: a `pin` op reusing a
        just-resolved id was born `resolved` by that write-through, so a
        brand-new question never reached the open count while its ❓
        stood on the canvas.

        v0.9 Task 35 refuses the collision in `_validate_batch`, against
        every id the registry has ever filed — R1 is why a resolved id
        counts too — and against the ids minted earlier in the same
        batch. An id spelled on some ordinary element stays free, which
        is what the neighbour below holds the check to.

        DIRECTION is the outcome the reviewer named as the durable fix
        and it is asserted first: the duplicate op is REFUSED, with an
        error naming the id that collided, so the agent learns which of
        its ops was refused rather than guessing. MAGNITUDE is what
        survives — exactly one registry record under that id — asserted
        second because a refusal that still filed the record would leave
        the corruption live.
        """
        store = self._store()
        escaped = self._send(store, "other", [
            {"op": "pin", "id": "pin-a", "target": "m1",
             "question": "And this one?"}])
        self.assertIsInstance(
            escaped, canvas.BatchError,
            "a second pin op reusing the open pin's id was accepted "
            "(escaped=%r) — one resolve now closes two questions"
            % (escaped,))
        self.assertIn("pin-a", "\n".join(escaped.errors))
        self.assertEqual(
            len(self._pin_records(store, "pin-a")), 1,
            "the registry holds %d records under one pin id: %r"
            % (len(self._pin_records(store, "pin-a")),
               [(p["id"], p["artifact"]) for p in store.registry["pins"]]))

    def test_red_the_echo_claims_a_removal_the_record_denies(self) -> None:
        """The echo and the note tell one story about one op.

        `intent_echo`'s resolve arm read absence from the batch scene as
        success — `"❓ glyph removed from canvas" if eid not in ix`
        (canvas.py) — but `ix` is the POST-op scene, where a glyph
        the user deleted last session is equally absent. So the one case
        where nothing was removed was the case the line called a removal.

        `pin_glyph_notes` was written for exactly this tolerance and gets
        it right, deriving the answer from the `del` changes the record
        actually carries. Both go into the same server response, so the
        agent was handed "op 0 (resolve_pin): gone resolved (❓ glyph
        removed from canvas)" and "pin gone resolved; its ❓ was already
        gone" together, and had to guess which of its tool's two
        sentences about one op to believe.

        The echo is handed a post-op scene and nothing else, and the two
        cases are indistinguishable in one — this fixture and its
        real-removal neighbour reach `intent_echo` with byte-equal
        arguments. So v0.9 Task 35 settles the question in
        `_validate_batch`, where the pre-op scene and the other artifacts
        are both in reach, and marks the op; the echo reads the mark.

        MAGNITUDE for a surface is what the line must CONTAIN, and
        DIRECTION is which claim it makes; here they are the same
        assertion, so it is written as agreement with the record rather
        than as wording: the echo may not claim a removal the save record
        denies. The note's presence is asserted first as the premise —
        without it there would be no disagreement to pin, and a fixture
        that stopped reaching the tolerant branch would read as a fix.
        """
        store = self._store()
        self._send(store, "flow", [{"op": "del", "id": "pin-a"}])
        ops: list[dict[str, Any]] = [{"op": "resolve_pin", "id": "pin-a",
                                      "answer": "yes"}]
        record, _ = store.apply_batch({"base_revn": store.head_revn(),
                                       "artifact": "flow", "ops": ops})
        self.assertEqual(canvas.pin_glyph_notes(record, ops),
                         ["pin pin-a resolved; its ❓ was already gone"])
        said = canvas.intent_echo(ops, store.scenes["flow"])
        self.assertNotIn(
            "removed from canvas", "\n".join(said),
            "nothing was removed — the record carries no del and the "
            "note says so — but the echo claims a removal: %r" % (said,))

    def test_a_resolve_without_an_id_collision_leaves_the_batch_intact(
            self) -> None:
        """The resolve arm's live pole: it takes the ❓ and nothing else.

        The first red's neighbour, and the same batch with the ONE field
        changed that the red turns on — the added rectangle's id. Without
        it, a "fix" that made the resolve arm delete nothing at all would
        satisfy the red while stranding every ❓ on the canvas, which is
        r5-17 restored. So both halves are asserted here: the added shape
        stands, AND the foreign glyph is gone with its registry record
        marked resolved.
        """
        store = self._store()
        escaped = self._send(store, "other", [
            {"op": "add", "element": {
                "type": "rectangle", "id": "fresh", "label": "New Box",
                "x": 200, "y": 0, "width": 100, "height": 60,
                "role": "node"}},
            {"op": "resolve_pin", "id": "pin-a", "answer": "yes"}])
        self.assertIsNone(escaped, "a clean resolve batch was refused: %r"
                          % (escaped,))
        self.assertIn("fresh", self._ids(store, "other"))
        self.assertEqual(self._orphan_labels(store, "other"), [])
        self.assertEqual([e["id"] for e in store.scenes["flow"]
                          if canvas.role_of(e) == "pin"], [])
        self.assertEqual([(p["id"], p["status"])
                          for p in store.registry["pins"]],
                         [("pin-a", "resolved")])

    def test_a_distinct_pin_id_is_accepted_and_files_one_record(self) -> None:
        """The pin op's live pole: a fresh id is minted without argument.

        The duplicate red's neighbour, differing from it in the ONE field
        the red turns on. A uniqueness check that refused every `pin` op
        — or refused any id already spelled anywhere in the project —
        would flip that red and delete the feature, and this is what
        refuses that reading: the second question is asked, drawn, and
        filed alongside the first.
        """
        store = self._store()
        escaped = self._send(store, "other", [
            {"op": "pin", "id": "pin-b", "target": "m1",
             "question": "And this one?"}])
        self.assertIsNone(escaped, "a fresh pin id was refused: %r"
                          % (escaped,))
        self.assertEqual([e["id"] for e in store.scenes["other"]
                          if canvas.role_of(e) == "pin"], ["pin-b"])
        self.assertEqual(sorted((p["id"], p["status"])
                                for p in store.registry["pins"]),
                         [("pin-a", "open"), ("pin-b", "open")])

    def test_two_pin_ops_in_one_batch_cannot_share_an_id(self) -> None:
        """The collision the registry cannot see coming.

        The duplicate red measures an id the registry ALREADY holds, so a
        check written against `known_pins` alone flips it while leaving
        this open — and this is the ordering an agent actually stumbles
        into, drafting two questions about one screen in one batch. The
        registry is read once, before the ops run, so the first `pin` op
        is not in it when the second is checked; the batch's own minted
        ids have to be carried alongside. Both ops are refused as a
        batch, so neither question is filed under the shared name and
        nothing lands half-asked.
        """
        store = self._store()
        escaped = self._send(store, "other", [
            {"op": "pin", "id": "pin-c", "target": "m1", "question": "One?"},
            {"op": "pin", "id": "pin-c", "target": "m1", "question": "Two?"}])
        self.assertIsInstance(
            escaped, canvas.BatchError,
            "two pin ops in one batch minted the same id (escaped=%r)"
            % (escaped,))
        self.assertIn("op 1", "\n".join(escaped.errors))
        self.assertIn("pin-c", "\n".join(escaped.errors))
        self.assertEqual(self._pin_records(store, "pin-c"), [])
        self.assertEqual([e["id"] for e in store.scenes["other"]
                          if canvas.role_of(e) == "pin"], [])

    def test_a_foreign_resolve_reports_the_removal_it_reached_for(
            self) -> None:
        """The echo's cross-artifact pole: the ❓ went, and it says so.

        `apply_batch` deletes a foreign ❓ from the artifact it lives on
        (r5-17), so the removal is real and the record carries the `del`
        — but it happens somewhere the echo's scene cannot show, because
        that scene is the BATCH artifact's and the glyph was never in it.
        An echo taught to distrust absence has to keep reaching for the
        other artifacts or it would call every foreign resolve a
        no-op, which is the third red's disagreement restored with the
        sentences swapped. Asserted against the note in the same breath,
        because agreement between the two is the whole property.
        """
        store = self._store()
        ops: list[dict[str, Any]] = [{"op": "resolve_pin", "id": "pin-a",
                                      "answer": "yes"}]
        record, _ = store.apply_batch({"base_revn": store.head_revn(),
                                       "artifact": "other", "ops": ops})
        self.assertEqual([e["id"] for e in store.scenes["flow"]
                          if canvas.role_of(e) == "pin"], [])
        self.assertEqual(canvas.pin_glyph_notes(record, ops), [])
        said = canvas.intent_echo(ops, store.scenes["other"])
        self.assertEqual(len(said), 1, said)
        self.assertIn("removed from canvas", said[0])

    def test_a_dry_run_leaves_the_callers_batch_as_it_found_it(self) -> None:
        """Which of the two paths may write on the batch it is handed.

        The echo's answer travels on the `resolve_pin` op, because
        `intent_echo` is handed a post-op scene and nothing else. On the
        apply path that mark has to land on the CALLER's list — every
        echo surface builds its lines from the list it still holds after
        `apply_batch` returns — so the write there is the mechanism, and
        the flipped echo red above is what measures it.

        `check_batch` is a documented dry run, and the same write there
        is a leak: `/api/apply` checks the body and then queues that very
        dict, so the mark reached `.pending/*.json` and sat in a
        persisted file as a derived value inviting some later reader to
        trust it — while being true only of the head it was computed
        against. Both halves are asserted together, because the contract
        is the DIFFERENCE between them and either one alone would read as
        a rule about marking rather than about dry runs.
        """
        store = self._store()
        ops: list[dict[str, Any]] = [{"op": "resolve_pin", "id": "pin-a"}]
        batch = {"base_revn": store.head_revn(), "artifact": "flow",
                 "ops": ops}
        before = json.dumps(batch, sort_keys=True)
        checked = store.check_batch(batch)
        self.assertTrue(checked["ok"], checked["errors"])
        self.assertIn("removed from canvas", checked["intent_echo"][0])
        self.assertEqual(
            json.dumps(batch, sort_keys=True), before,
            "the dry run wrote into the caller's batch, which `/api/apply` "
            "then queues to disk: %r" % (batch,))
        store.apply_batch(batch)
        self.assertIs(ops[0].get("glyph_removed"), True,
                      "the apply path must mark the caller's own ops — the "
                      "echo is built from this list: %r" % (ops,))

    # -- Four more from the same root (v0.9 Task-35 review, 2026-08-13;
    # findings F-4, F-3, F-1 and F-2). Task 35 closed the three reds
    # above, and closed them at the sites they named: the resolve arm's
    # role gate and the `pin` op's uniqueness check. These are the places
    # the SAME property — one id names one question, and a resolve takes
    # only the ❓ — is still not held: the role gate's own blind spot, the
    # escape hatch beneath it, the minter that never consults the
    # registry, and the half of the new refusal nothing measures.

    def test_red_an_incoherent_pin_batch_is_swallowed_instead_of_refused(
            self) -> None:
        """One batch resolves a ❓ and re-draws it, and only one op is told.

        DIRECTION RE-RULE, and the provenance matters because this entry
        was green before it was red. As first written it pinned the
        parity half of r5-17: a resolve plus a same-id pin-role `add`
        left `flow`'s ❓ standing while the registry called `pin-a`
        resolved — two glyphs, `OPEN_PINS=0` — and its DIRECTION said the
        add "itself is legitimate and a fix that rejected it would cost
        the agent a drawing op for no reason". Task 38 closed the parity
        hole by deleting the scan's skip, so the counts now agree in
        either op order, and that interim outcome was the correct thing
        to pin while it was the only outcome the contract allowed.

        The v0.9 Task-38 review then ruled the DIRECTION wrong, and the
        controller ratified it. The precedence is right — a resolve is
        id-global, so nothing later in the batch can un-resolve it, and
        both orderings converging is the property worth having. What is
        wrong is that the losing op is never told it lost. The agent sent
        an `add` and gets back `op 1 (add): pin-a: gone`, a sentence
        about the canvas rather than about its op, indistinguishable from
        an element the user deleted; the likeliest next move is to send
        the add again, and be swallowed again.

        Three things make refusal the answer rather than better echo
        wording. The spelled form of this exact act is ALREADY refused —
        `{"op": "pin", "id": "pin-a"}` on a filed id gets a named error —
        so the precedence exists only because one of two doors is
        unguarded (F-6, pinned separately below). The one-artifact
        version of this very batch is already refused, with `op 1: id
        'pin-a' already exists in this artifact`, so only a scene-scoped
        duplicate check separates the accepted case from the refused one
        (F-7). And `ops-reference.md`, as amended by Task 38 itself, now
        says reusing an id the registry has EVER filed is refused — which
        this add does, under an id the same batch just closed.

        MAGNITUDE is that nothing lands: refused, the resolve does not
        happen either, so `pin-a` stays open with its ❓ where it was and
        `other` gains no element. That is the correct shape for an
        incoherent batch — the agent re-sends a coherent one. DIRECTION
        is the refusal itself, naming BOTH ops, since an error naming
        only one leaves the agent guessing which half to drop.

        v0.9 Task 40 refuses it, and refuses it at the ADD rather than by
        reasoning about the pair: an `add` carrying `role: "pin"` under an
        id the registry has filed is turned away wherever it appears, so
        door 7 below and this one close together, which is what the
        census predicted. The message names the resolve as well when the
        same batch carries one, since "you cannot re-ask this" and "you
        cannot re-ask what you just closed" send an agent to different
        repairs.

        ENACTMENT, recorded because this red did not flip alone. The
        curator's note named
        `test_a_pin_role_shadow_add_is_swallowed_in_either_order`, which
        asserted this same batch was ACCEPTED both ways; it is re-authored
        just below as the refusal's own order-independence, which is the
        same claim's shape with its direction re-ruled. A SECOND casualty
        the note did not name was `test_the_shadowed_resolve_reports_the
        _removal_it_made`, built on the same now-illegal scene — see the
        retirement comment below it for what was measured before it went.
        And the scan's own-artifact pass did lose its last covering scene
        exactly as the note predicted (re-measured after re-authoring:
        skipping the batch's own artifact now breaks nothing). It is KEPT
        as belt-and-braces, uncovered by design — removing it would mean
        adding a third skip to a loop whose own comment records two
        earlier skips that each stranded a ❓, i.e. writing more code to
        get less safety.
        """
        store = self._store()
        escaped = self._send(store, "other", [
            {"op": "resolve_pin", "id": "pin-a", "answer": "yes"},
            {"op": "add", "element": {
                "type": "text", "id": "pin-a", "text": "❓", "x": 500,
                "y": 0, "width": 20, "height": 25, "role": "pin"}}])
        self.assertIsInstance(
            escaped, canvas.BatchError,
            "the batch resolves pin-a and re-draws it under the same id; "
            "it was accepted (escaped=%r) and the add was swallowed with "
            "nothing said about it" % (escaped,))
        said = "\n".join(escaped.errors)
        self.assertIn("pin-a", said)
        self.assertIn("op 1", said,
                      "the refusal must name the op that lost, or the "
                      "agent cannot tell which half to drop: %r" % (said,))
        self.assertEqual(self._glyphs(store, "flow"), ["pin-a"])
        self.assertEqual(self._glyphs(store, "other"), [])
        self.assertEqual([(p["id"], p["status"])
                          for p in store.registry["pins"]],
                         [("pin-a", "open")])

    def test_red_an_explicit_id_may_collide_with_an_earlier_auto_mint(
            self) -> None:
        """The minter's other ordering: the mint happens after the check.

        Task 38 closed half of this family. `_validate_batch` reads the
        registry ONCE, before any op runs, into `known_pins`, and tracks
        spelled ids in `minted` as it walks the list — so an explicit id
        followed by an auto-mint is caught, and the second question now
        lands on `pin-n1-2`. The reverse order is not caught, because the
        set of spelled ids cannot contain one that does not exist yet:
        the auto-mint happens inside `apply_ops`, after validation has
        already finished reading.

        Probed on the shipped code — `[pin(target=n1, no id),
        pin(id="pin-n1", target=n1)]` files two questions under
        `pin-n1`, and one `resolve_pin` still closes both. The scene
        carries an extra wound the two-artifact version does not: both
        pin ops draw their ❓ into ONE scene under one id, so the canvas
        keeps a single glyph for two records. One of the two questions
        has no ❓ at all — it cannot be seen, cannot be clicked, and is
        counted open forever.

        Pre-existing and unchanged by Task 38 for this ordering, which
        the reviewer verified at `fe20fb0`; the fix needs the validator
        to track ids the batch is ABOUT to mint, not just the ones it
        spelled. MAGNITUDE is what the registry holds — one record per
        question, so two distinct ids — and DIRECTION is refusal with the
        colliding id named, which is the answer the other ordering
        already gives.

        v0.9 Task 40 does that tracking by PREDICTING the mint where the
        `minted` set is kept, running the same `mint_id` against the same
        seed and the same taken set the minter will use, so both
        orderings now meet one check. Prediction rather than a second
        refusal inside `apply_ops` because this is where the other
        ordering is already answered, and one site giving one message
        beats two sites agreeing. The prediction ignores elements the
        batch itself ADDS, which the minter counts — documented at the
        code, and it can only refuse a shade early, never admit.
        """
        store = self._store()
        escaped = self._send(store, "flow", [
            {"op": "pin", "target": "n1", "question": "First?"},
            {"op": "pin", "id": "pin-n1", "target": "n1",
             "question": "Second?"}])
        filed = [p["id"] for p in store.registry["pins"]
                 if p["id"] != "pin-a"]
        self.assertEqual(
            len(set(filed)), len(filed),
            "two questions were filed under one id %r — one resolve "
            "closes both, and only one ❓ was ever drawn for the pair "
            "(glyphs=%r)" % (filed, self._glyphs(store, "flow")))
        self.assertIsInstance(
            escaped, canvas.BatchError,
            "an explicit id colliding with the id its own batch had just "
            "minted was accepted (escaped=%r)" % (escaped,))
        self.assertIn("pin-n1", "\n".join(escaped.errors))

    def test_red_the_add_door_admits_a_filed_pin_id(self) -> None:
        """Two doors to one wound, and only the spelled one is guarded.

        `{"op": "pin", "id": "pin-a"}` on an id the registry has filed is
        refused with a named error, telling the agent to pick another or
        omit it. `{"op": "add", "element": {"id": "pin-a", "role":
        "pin"}}` — hand-drawing the same ❓ under the same filed id — is
        accepted. Probed: `flow` and `other` end up holding a ❓ apiece
        under `pin-a` against a single registry record, so the canvas
        shows two questions where the model has one, and either glyph
        will answer for both.

        This is the door every shadow scene in this class walks through,
        including the incoherent batch above; that entry is the same act
        with a resolve alongside it, and this is the act on its own. They
        share `_store` and one fix closes both — extending the existing
        pin-uniqueness check to `add` ops carrying `role: "pin"` — but
        they are separate entries because their magnitudes differ: that
        one must name two ops, this one names an element.

        DIRECTION is that the add-door answers the way the pin-door
        already does. Refusal is asserted because that is the shipped
        answer to the spelled form and reusing one message is the whole
        point of the fix; a load-time quarantine would satisfy the same
        outcome for a project that already has one, but nothing should
        accept this at write time. MAGNITUDE is that no second ❓ is
        drawn under a filed id.

        v0.9 Task 40 adds that door check beside the pin-door's, gated on
        the element's ROLE and on nothing else — the namesake neighbour
        below is what holds the gate to that, since ids are per scene and
        only a ❓ can answer for a question. It reads `role` from the
        terse spec or from `customData`, both of which
        `make_element` accepts, because a check that saw only one
        spelling would leave the door ajar in the other.
        """
        store = self._store()
        escaped = self._send(store, "other", [
            {"op": "add", "element": {
                "type": "text", "id": "pin-a", "text": "❓", "x": 500,
                "y": 0, "width": 20, "height": 25, "role": "pin"}}])
        self.assertEqual(
            self._glyphs(store, "other"), [],
            "a second ❓ was drawn under the filed id pin-a; the registry "
            "still holds one record, so either glyph answers for both")
        self.assertIsInstance(
            escaped, canvas.BatchError,
            "the `pin` op form of this id is refused and the `add` form "
            "was accepted (escaped=%r)" % (escaped,))
        self.assertIn("pin-a", "\n".join(escaped.errors))

    def test_red_a_pin_op_may_spell_an_ordinary_elements_id(self) -> None:
        """The tenth door: the question is filed and never drawn.

        Door 7 guards the `add` side — an `add` of a ❓ under a filed PIN
        id is refused. This is the mirror nobody enumerated: a `pin` op
        spelling an id that an ordinary ELEMENT already owns on the
        scene. Nothing checks it. `_validate_batch` compares a spelled
        pin id against the registry and against the batch's own mints,
        never against the artifact's element ids.

        What lands is worse than a duplicate. Probed on the shipped
        code: the registry files `('n1', 'open')`, `apply_ops` appends a
        ❓ under `n1`, and the commit path's id-dedupe keeps the
        rectangle that was already there — so ZERO ❓ are drawn for the
        new question while the record says it is open. It cannot be
        seen, cannot be clicked, and cannot be answered; it is open
        forever. The echo then says `op 0 (pin): n1 targets n1 — ...
        (❓ on canvas)`, which is false in the one direction that would
        have let the agent notice.

        One-of-two-doors again, and the other door is already right
        beside it: `make_element` refuses this exact collision for an
        `add`, with `op 0: id 'n1' already exists in this artifact`. The
        neighbour below pins that wording as shipped, so a fix here can
        reuse it rather than invent one.

        v0.9 Task 41 refuses it in `_validate_batch`'s pin arm, one `elif`
        below the check for an id the registry has filed and the one for
        an id this batch is about to mint — the same walk, asking the
        third thing an id can already be. The sentence is
        `make_element`'s, word for word rather than reworded — one
        collision read from two sides, so an agent that has met the `add`
        form has already been told what to do about this one. Only the
        prefix differs, since every error that walk raises names its op
        kind.

        MAGNITUDE is what the batch leaves — no registry record, the
        element untouched — asserted first because an accepted batch
        that filed the record is the damage. DIRECTION is refusal naming
        the id, matching the `add` door.
        """
        store = self._store()
        escaped = self._send(store, "flow", [
            {"op": "pin", "id": "n1", "target": "n1",
             "question": "Invisible?"}])
        self.assertEqual(
            [(p["id"], p["status"]) for p in store.registry["pins"]],
            [("pin-a", "open")],
            "a question was filed under an ordinary element's id and no "
            "❓ was drawn for it (glyphs=%r) — it is open forever and "
            "cannot be clicked" % (self._glyphs(store, "flow"),))
        self.assertIsInstance(
            escaped, canvas.BatchError,
            "a pin op spelling the rectangle's own id was accepted "
            "(escaped=%r)" % (escaped,))
        self.assertIn("n1", "\n".join(escaped.errors))

    def test_red_a_non_string_pin_target_arrives_as_an_internal_error(
            self) -> None:
        """A wrong field type is reported as a Python exception.

        The `pin` op type-checks `detail` and `examples` and names the
        field when either is wrong. `target` is checked by neither: it
        goes straight into `"pin-" + (target or q[:20])` and then into a
        set, so a dict, a list or a number reaches the E-9 backstop and
        comes back as `internal error applying ops — TypeError: can only
        concatenate str (not "int") to str`.

        The batch IS refused and nothing partial lands, so this is a
        message defect rather than a data one — which is why it is minor
        and why the assertion is about what the error SAYS. The E-9
        backstop exists for faults nobody predicted; a field whose type
        is wrong is not one of those, and an agent handed a `TypeError`
        about string concatenation has no way to learn that `target` was
        the problem. Pre-existing, identical at `b06812f`.

        Task 40 makes it worth pinning now rather than later: its
        prediction site type-guards the seed (`isinstance(seed, str)`)
        while the minter it must stay in step with does not, so the two
        sites disagree about this input by construction.

        v0.9 Task 41 checks the field where `question`, `detail` and
        `examples` are already checked — the same arm, immediately before
        the two lines that reach for `target` — and names it in
        `index_fault`'s voice. Which also ends the disagreement above: the
        minter can no longer be handed a non-string seed either, so the
        prediction's guard and the minter now agree by refusal rather than
        by one side coping.

        MAGNITUDE is what the message names — the op and the field.
        DIRECTION is that it is a validation error rather than the
        internal-error envelope, asserted as the absence of that
        envelope so a fix is free to word the field error itself.
        """
        for target in ({"x": 1}, 42, ["a"]):
            with self.subTest(target=target):
                store = self._store()
                escaped = self._send(store, "flow", [
                    {"op": "pin", "target": target, "question": "Q?"}])
                self.assertIsInstance(escaped, canvas.BatchError,
                                      "%r" % (escaped,))
                said = "\n".join(escaped.errors)
                self.assertNotIn(
                    "internal error", said,
                    "a wrong field type reached the unpredicted-fault "
                    "backstop: %r" % (said,))
                self.assertIn("target", said)

    def _plain_add(self) -> list[dict[str, Any]]:
        """One ordinary, legal `add` batch — the carrier AND the control.

        The three E-9 tests below share this so the fault injection is
        the only variable between the firing pole and the quiet one. A
        pole whose scene differed from its control's would prove the two
        scenes differ, not that the envelope fired.

        Returns:
            An op list adding `n2` clear of `n1`, which `_store` already
            put at the origin.
        """
        return [{"op": "add", "element": {
            "id": "n2", "type": "rectangle", "x": 300, "y": 0,
            "width": 100, "height": 60, "label": "N2"}}]

    def test_an_unpredicted_fault_surfaces_as_the_e9_envelope(self) -> None:
        """FIRING pole for the silence above (spike-e9, 2026-08-16).

        The red above asserts that a wrong field type does NOT reach the
        E-9 backstop — `assertNotIn("internal error", said)`. That is a
        silence about an envelope, and an envelope that had been deleted
        outright satisfies it on every input forever. Nothing in this
        class proved the phrase it forbids can be produced at all.

        WHAT THIS PROVES, AND WHAT IT DOES NOT — the caveat is the point,
        so it is stated before the mechanism. It proves the envelope is
        WIRED AND SPELLED CORRECTLY: a fault arising inside `apply_ops`
        comes back as a `BatchError` naming the exception type, on the
        apply surface here and the check surface below. It does NOT prove
        that any real-world fault reaches it, and it does NOT prove the
        envelope is wide enough — it is not. The spike measured seven of
        41 malformed batches escaping `apply_batch` as RAW exceptions
        from both sides of the `try`, `--check` included, which is a live
        defect in the curator's hands, not something these tests cover.

        WHY AN INJECTED FAULT rather than a natural one, recorded so it
        is not re-proposed a third time: the spike found 17 of 41
        malformed batches reach E-9 with no injection at all, so the
        route was never scarce. Every one of the 17 is the same species
        of defect Task 41 just fixed — an unvalidated field type reaching
        a raw operation — and the red above says in its own words that
        such a field "is not one of those" faults E-9 exists for. A
        firing pin on a natural route would therefore assert the exact
        opposite of the pin it is meant to pair with, and would go red
        the day someone validates that field, exactly as Task 41 turned
        the `target` route from firing to silent. Those are defects to be
        fixed, not behaviour to pin.

        The injection still travels the real entry point, which is rule
        8's second half: the batch goes `apply_batch` -> `_validate_batch`
        -> `apply_ops` and passes every real gate; only the leaf
        `make_element` is replaced, and `apply_ops` calls it for every
        `add`. The spike confirmed the boundary by contrast — injecting
        into `role_of` raises before the `try` and into
        `normalize_element` after it, and neither is enveloped. Same
        shape as the proven routing-envelope test,
        `test_backend.TestRouterTotalityAndSelfLoops`.
        """
        store = self._store()
        with mock.patch.object(canvas, "make_element",
                               side_effect=RuntimeError("injected fault")):
            escaped = self._send(store, "flow", self._plain_add())
        self.assertIsInstance(
            escaped, canvas.BatchError,
            "an unpredicted fault escaped the E-9 envelope as %r — the "
            "agent gets a raw traceback, which is the r4-11 defect"
            % (escaped,))
        said = "\n".join(escaped.errors)
        self.assertIn("internal error applying ops", said, said)
        self.assertIn("RuntimeError", said, said)
        self.assertIn("nothing partial landed", said, said)

    def test_the_same_batch_without_the_fault_stays_quiet(self) -> None:
        """The pole's own control: uninjected, the identical batch lands.

        Without this, an injection that had broken the batch for some
        reason of its own — a patched name that no longer exists, a
        scene the store would refuse anyway — would raise a `BatchError`
        that reads as a firing envelope. Asserting the element ARRIVES
        rather than merely that nothing raised, because a batch that was
        silently dropped would also raise nothing.
        """
        store = self._store()
        self.assertIsNone(self._send(store, "flow", self._plain_add()))
        self.assertIn("n2", self._ids(store, "flow"))

    def test_the_check_path_reports_the_same_envelope(self) -> None:
        """The offline `--check` surface must not leak the traceback either.

        `check_batch` is the dry-run half, and the sibling routing test
        pins the same pair for the same reason: `--check` is what an
        agent runs BEFORE applying, so an envelope present on one surface
        and absent on the other leaks a raw traceback down the path
        specifically taken to avoid one.

        Scoped to the injected fault deliberately. The spike found this
        surface really does still leak for validator-side and commit-side
        faults (`pin/id={...}`, `add/customData=[...]`, `add/width=NaN`);
        that is the open defect referenced above, and pinning it here
        would be pinning a bug rather than a contract.
        """
        store = self._store()
        with mock.patch.object(canvas, "make_element",
                               side_effect=RuntimeError("injected fault")):
            result = store.check_batch({"base_revn": store.head_revn(),
                                        "artifact": "flow",
                                        "ops": self._plain_add()})
        self.assertFalse(result["ok"], result)
        self.assertTrue(
            any("internal error applying ops" in e for e in result["errors"]),
            "the dry-run surface leaked the fault instead of enveloping "
            "it: %r" % (result["errors"],))

    def test_the_echo_says_gone_for_an_element_its_batch_removed(
            self) -> None:
        """`intent_echo`'s missing-element branch, rehomed after a retirement.

        Not a pin-identity claim, and here anyway: this branch's only
        test was the shadow-batch echo case, which THIS family retired
        when Task 40 made that scene illegal. Proven by the reviewer at
        both revisions — before the retirement, replacing `describe`'s
        missing-element return broke exactly one test; after it, nothing.
        The family that orphaned the branch owes it a home, and moving it
        somewhere tidier would lose that provenance.

        The scene is the plainest one that reaches it: add an element and
        delete it in the same batch, so the echo describes op 0 against a
        final scene the element is not in. Both lines are asserted, since
        the `del` line is what proves the element really went — an echo
        saying "gone" about an element that never existed would satisfy
        the first assertion alone.
        """
        store = self._store()
        ops: list[dict[str, Any]] = [
            {"op": "add", "element": {
                "type": "rectangle", "id": "tmp1", "label": "T", "x": 400,
                "y": 0, "width": 60, "height": 40, "role": "node"}},
            {"op": "del", "id": "tmp1"}]
        self.assertIsNone(self._send(store, "flow", ops))
        said = canvas.intent_echo(ops, store.scenes["flow"])
        self.assertEqual(said[0], "op 0 (add): tmp1: gone")
        self.assertIn("tmp1 deleted", said[1])

    def test_the_mint_prediction_counts_the_scene_it_will_land_in(
            self) -> None:
        """Door 6's guard reads scene ids, and dropping that term reopens it.

        The prediction in `_validate_batch` re-runs `mint_id` on the
        minter's seed against `scene_ids | known_pins | minted`. The
        `scene_ids` term is the one an eye skips: it is there because
        the mint lands in a SCENE, so an ordinary element already
        holding the natural id pushes the real mint to `-2`, and a
        prediction that ignored the scene would predict the bare form and
        fail to refuse a later op spelling the `-2`. Verified by
        mutation: dropping that term leaves the whole suite green and
        files two records under one id with one ❓ drawn.

        The predicted id is DERIVED here rather than written down, by
        minting it once on an identical store and then spelling it back.
        That is what makes this a cross-site agreement test rather than a
        literal: a deliberate change to the seed on BOTH sides keeps it
        green, and a change on one side alone fails it, which is exactly
        the failure the two `KEEP IN STEP` comments are guarding against
        in prose.
        """
        probe = self._store()
        self._send(probe, "flow", [{"op": "add", "element": {
            "type": "rectangle", "id": "pin-n1", "label": "Namesake",
            "x": 600, "y": 0, "width": 60, "height": 40, "role": "node"}}])
        self.assertIsNone(self._send(probe, "flow", [
            {"op": "pin", "target": "n1", "question": "First?"}]))
        minted = [p["id"] for p in probe.registry["pins"]
                  if p["id"] != "pin-a"]
        self.assertEqual(len(minted), 1, minted)
        self.assertNotEqual(minted[0], "pin-n1",
                            "the scene namesake did not push the mint "
                            "aside, so this scene cannot test the term")
        store = self._store()
        self._send(store, "flow", [{"op": "add", "element": {
            "type": "rectangle", "id": "pin-n1", "label": "Namesake",
            "x": 600, "y": 0, "width": 60, "height": 40, "role": "node"}}])
        escaped = self._send(store, "flow", [
            {"op": "pin", "target": "n1", "question": "First?"},
            {"op": "pin", "id": minted[0], "target": "n1",
             "question": "Second?"}])
        self.assertIsInstance(
            escaped, canvas.BatchError,
            "an explicit id equal to the one this batch really mints "
            "(%r) was accepted — the prediction is not counting the "
            "scene" % (minted[0],))
        self.assertEqual([p["id"] for p in store.registry["pins"]],
                         ["pin-a"])

    def test_the_minter_and_its_prediction_derive_one_seed(self) -> None:
        """The two sites agree on the seed, or door 6 reopens silently.

        `apply_ops` mints from `target or q[:20]`; `_validate_batch`
        predicts from the same expression written a second time. Nothing
        fails if they disagree — the reviewer drifted the apply site to
        `q[:12]` and the whole suite stayed green while two records
        landed under one id. The two `KEEP IN STEP` comments are the
        current guard, and prose is not a guard.

        Encoding judged rather than assumed: a direct cross-site
        assertion would be the better shape, and it is not reachable —
        both seeds are inline expressions inside long functions, not
        callables a test can hold side by side, and asserting on source
        text would pin the spelling instead of the behaviour. So the
        agreement is measured through the one place it is observable: an
        id the apply site really mints must be an id the prediction
        really refuses.

        A question with no `target` is used because the seed only
        matters when it falls back to the question, and it is long
        enough that the truncation is load-bearing — at `q[:20]` and
        `q[:12]` the slugs differ. Deriving the id keeps a coordinated
        change to both sides green, which is the point: this pins
        agreement, not a length.
        """
        question = "Which cart widget do we mean here exactly"
        probe = self._store()
        self.assertIsNone(self._send(probe, "flow", [
            {"op": "pin", "question": question}]))
        minted = [p["id"] for p in probe.registry["pins"]
                  if p["id"] != "pin-a"]
        self.assertEqual(len(minted), 1, minted)
        store = self._store()
        escaped = self._send(store, "flow", [
            {"op": "pin", "question": question},
            {"op": "pin", "id": minted[0], "question": "Second?"}])
        self.assertIsInstance(
            escaped, canvas.BatchError,
            "the apply site minted %r and the prediction did not refuse "
            "it — the two seeds have drifted apart" % (minted[0],))
        self.assertIn(minted[0], "\n".join(escaped.errors))
        self.assertEqual([p["id"] for p in store.registry["pins"]],
                         ["pin-a"])

    def test_the_add_door_reads_every_spelling_of_the_pin_role(
            self) -> None:
        """A ❓ is a ❓ however the op spells its role.

        Door 7 refuses an `add` of a pin-role element under a filed pin
        id, and `role` reaches that check by three routes: the element
        spec's own `role`, its `customData.role` — which is where the
        role actually lives once an element is on the canvas — and the
        flat op form, where the element's attributes sit on the op
        itself. The red that opened door 7 uses the first. The other two
        were unpinned, and the reviewer showed each mutation leaves the
        suite green while reopening the door; I re-ran both and confirm
        it.

        `customData` is the spelling that matters most in practice: it
        is what a round-tripped element carries, so an agent copying a ❓
        off the canvas and re-adding it comes through that route and no
        other. Both are asserted under one entry because they are one
        predicate reading one field three ways, and the shipped message
        is asserted with them so a fix cannot satisfy this by refusing
        the add for some unrelated reason.
        """
        spellings: dict[str, dict[str, Any]] = {
            "customData": {"op": "add", "element": {
                "type": "text", "id": "pin-a", "text": "❓", "x": 500,
                "y": 0, "width": 20, "height": 25,
                "customData": {"role": "pin"}}},
            "flat op form": {"op": "add", "type": "text", "id": "pin-a",
                             "text": "❓", "x": 500, "y": 0, "width": 20,
                             "height": 25, "role": "pin"},
        }
        for spelling, op in spellings.items():
            with self.subTest(spelling=spelling):
                store = self._store()
                escaped = self._send(store, "other", [op])
                self.assertIsInstance(
                    escaped, canvas.BatchError,
                    "a ❓ spelled through %s slipped past the add door "
                    "(escaped=%r)" % (spelling, escaped))
                self.assertIn("filed pin id", "\n".join(escaped.errors))
                self.assertEqual(self._glyphs(store, "other"), [])

    def test_an_ordinary_namesake_of_a_filed_pin_id_stays_legal(
            self) -> None:
        """The add-door's live pole: same id, no pin role, still allowed.

        The neighbour that keeps the add-door red from being satisfied by
        refusing every `add` whose id matches a pin record. Ids are
        minted per scene, so an ordinary node called `pin-a` on another
        artifact is not the same thing as the question — the whole role
        gate on the resolve scan exists to say exactly that, and
        `test_a_same_id_element_elsewhere_is_not_collateral`
        (tests/test_failure_paths.py) pins the resolve half of it. This
        pins the write half: the element lands, keeps its label, and the
        registry is untouched.
        """
        store = self._store()
        escaped = self._send(store, "other", [
            {"op": "add", "element": {
                "type": "rectangle", "id": "pin-a", "label": "Namesake",
                "x": 700, "y": 0, "width": 60, "height": 40,
                "role": "node"}}])
        self.assertIsNone(escaped, "an ordinary namesake was refused: %r"
                          % (escaped,))
        self.assertIn("pin-a", self._ids(store, "other"))
        self.assertEqual(self._glyphs(store, "other"), [])
        self.assertEqual([(p["id"], p["status"])
                          for p in store.registry["pins"]],
                         [("pin-a", "open")])

    def test_red_a_resolve_naming_an_ordinary_element_is_accepted(
            self) -> None:
        """The escape hatch under the role gate asks the role now too.

        `_validate_batch` accepted a `resolve_pin` whose id named any
        element on the scene, pin or not (`o.get("id") not in
        scene_ids`). Task 35's role gate stopped that from DESTROYING the
        element — at `HEAD~1` the node and its label were deleted — and
        the improvement is real. What it left was a resolve that did
        nothing while two surfaces described it differently: probed, the
        echo said "op 0 (resolve_pin): m1 resolved (❓ glyph STILL on
        canvas)" and the note beside it said "pin m1 resolved; its ❓ was
        already gone".

        That is a fresh instance of the disagreement
        `test_red_the_echo_claims_a_removal_the_record_denies` banned —
        in a scene that red cannot reach, because it resolves a real pin.
        The sentence pair was new with Task 35 even though the hatch was
        not, which is why it is filed here rather than as a regression.

        v0.9 Task 38 gates the hatch on the same role every other arm of
        the resolve path asks about: the id must name a ❓ on the scene,
        or be one the registry knows. The hatch is narrowed and not
        removed, because a ❓ drawn straight onto the canvas has no
        record to be known by and must stay resolvable — which is what
        `test_an_unregistered_glyph_on_the_scene_stays_resolvable` holds
        it to. The error says which of the two it is, since "that is not
        a ❓" and "no such pin" send an agent to different fixes.

        DIRECTION is the outcome asserted, and it is refusal: a resolve
        naming something that was never a question has nothing to
        resolve, and the same validator already refuses an unknown id
        with a named error. Both the implementer and the reviewer leant
        the same way — role-gate the hatch — and the alternative reading
        (accept it, but say ONE thing) still leaves the agent an op that
        reports success for work nobody could do. MAGNITUDE is that the
        element survives, asserted first, because the fix must not walk
        back the destruction Task 35 stopped.
        """
        store = self._store()
        escaped = self._send(store, "other", [
            {"op": "resolve_pin", "id": "m1", "answer": "yes"}])
        self.assertIn(
            "m1", self._ids(store, "other"),
            "the ordinary element the resolve named was deleted — Task "
            "35 stopped exactly that and it must not come back")
        self.assertIsInstance(
            escaped, canvas.BatchError,
            "resolve_pin named an ordinary node and was accepted "
            "(escaped=%r) — the echo then says the ❓ is STILL on canvas "
            "while the note says it was already gone" % (escaped,))
        self.assertIn("m1", "\n".join(escaped.errors))

    def test_red_the_auto_minter_reissues_a_live_pin_id(self) -> None:
        """Omitting `id` no longer walks around the refusal.

        `mint_id("pin-" + target, "pin", existing)` deduped against the
        batch artifact's SCENE ids and never against the registry, and
        Task 35's uniqueness check fires only when `o.get("id")` is set.
        So the default path — the one an agent takes when it does not
        care about the id — recreated the exact corruption the check was
        written to prevent.

        Probed on the shipped code, and this scene is the probe: two
        artifacts each holding a node called `n1`, one ordinary `pin` op
        each with no id at all. Two open records landed under `pin-n1`,
        a ❓ on each canvas, `OPEN_PINS=2` — and one `resolve_pin pin-n1`
        then marked BOTH resolved and took BOTH glyphs. The user answered
        one question and the tool closed two, which is word for word what
        the duplicate-id red above describes. A single-artifact variant
        reproduced too: pin `n1`, resolve it, pin `n1` again.

        The refusal's own error message routed agents here — "Pick
        another id, or omit `id` and one will be minted" — so the
        documented way out of the collision was the way into it.

        The fix belongs at the MINTING site, which the implementer and
        the reviewer reached independently: the minter runs inside
        `apply_ops`, after validation has already read the ops, so a
        validation-time refusal cannot see an id that does not exist yet.
        v0.9 Task 38 hands `apply_ops` every id the registry has filed
        and mints against those as well as the scene's, which is the same
        set the explicit-id refusal already consults — element ids are
        per scene, a pin id names one question project-wide.

        MAGNITUDE is what the two batches file — two ids, two questions,
        two glyphs — and DIRECTION is that both stay open until each is
        answered, asserted as the r5-17 parity so a fix that minted two
        ids but still closed both would not pass.
        """
        store = self._store()
        self._send(store, "other", [{"op": "add", "element": {
            "type": "rectangle", "id": "n1", "label": "N1", "x": 300,
            "y": 0, "width": 100, "height": 60, "role": "node"}}])
        for aid in ("flow", "other"):
            self.assertIsNone(
                self._send(store, aid, [{"op": "pin", "target": "n1",
                                         "question": "About %s?" % aid}]),
                "an ordinary auto-minted pin was refused on %r" % (aid,))
        minted = [p["id"] for p in store.registry["pins"]
                  if p["id"] != "pin-a"]
        self.assertEqual(
            len(set(minted)), 2,
            "two questions about two different nodes were filed under %r "
            "— one resolve now closes both and takes both ❓"
            % (sorted(minted),))

    def test_a_pin_id_the_registry_has_resolved_is_not_reissued(
            self) -> None:
        """The resolved half of the refusal, which nothing measured.

        Green on arrival, and added because the reviewer's mutation M5b —
        narrow the check to `open`/`answered` ids — survives the whole
        suite. The behaviour is right and deliberate: the resolve
        write-through is id-global, so a question minted under a resolved
        id is marked resolved AT BIRTH and never reaches the open count
        while its ❓ stands on the canvas. That is the R1 wound from the
        other side, and it was documented, decided, and unpinned.

        The damage is asserted rather than only the refusal — one record
        under that id, still resolved — because a check that refused the
        op and filed the record anyway would leave the corruption live
        while looking correct from the outside.
        """
        store = self._store()
        self.assertIsNone(self._send(store, "flow", [
            {"op": "resolve_pin", "id": "pin-a", "answer": "yes"}]))
        escaped = self._send(store, "flow", [
            {"op": "pin", "id": "pin-a", "target": "n1",
             "question": "Asking again?"}])
        self.assertIsInstance(
            escaped, canvas.BatchError,
            "a resolved pin's id was reissued (escaped=%r) — the "
            "id-global write-through marks the new question resolved "
            "before anyone can answer it" % (escaped,))
        self.assertIn("pin-a", "\n".join(escaped.errors))
        self.assertEqual([(p["id"], p["status"])
                          for p in store.registry["pins"]],
                         [("pin-a", "resolved")])

    def test_an_unregistered_glyph_on_the_scene_stays_resolvable(
            self) -> None:
        """The hatch's live pole: a ❓ with no record still comes down.

        The escape-hatch red's neighbour, and the reason that red demands
        a ROLE gate rather than the hatch's removal. A pin-role element
        the registry never filed — a ❓ drawn straight onto the canvas —
        is exactly what the hatch is for, and a fix that closed it
        outright would strand every such glyph with no way to take it
        down. Asserted with its surfaces: the element goes, and echo and
        note agree about it, which is the pair the red says must never
        disagree.
        """
        store = self._store()
        self._send(store, "other", [{"op": "add", "element": {
            "type": "text", "id": "q1", "text": "❓", "x": 200, "y": 0,
            "width": 20, "height": 25, "role": "pin"}}])
        self.assertEqual(self._glyphs(store, "other"), ["q1"])
        ops: list[dict[str, Any]] = [{"op": "resolve_pin", "id": "q1",
                                      "answer": "yes"}]
        record, _ = store.apply_batch({"base_revn": store.head_revn(),
                                       "artifact": "other", "ops": ops})
        self.assertEqual(self._glyphs(store, "other"), [])
        self.assertEqual(canvas.pin_glyph_notes(record, ops), [])
        self.assertIn("removed from canvas",
                      canvas.intent_echo(ops, store.scenes["other"])[0])

    def test_one_auto_minted_pin_lands_under_a_predictable_id(
            self) -> None:
        """The minter's live pole: asked once, it names the node it asks about.

        The minter red's neighbour, and its error-red guard. That red
        counts DISTINCT ids across two batches, which a minter that had
        stopped working at all — or a `pin` op that started rejecting the
        no-id form — would satisfy by filing nothing. This fixes the
        single-batch behaviour it deviates from: one op, one record, one
        ❓, under the id the shipped minter derives from the target.

        The id is asserted literally because the red's whole claim is
        that a SECOND op must not land on this same string; a neighbour
        that accepted any id would leave that claim untethered.
        """
        store = self._store()
        self.assertIsNone(self._send(store, "other", [
            {"op": "pin", "target": "m1", "question": "About m1?"}]))
        self.assertEqual(self._glyphs(store, "other"), ["pin-m1"])
        self.assertEqual(
            [(p["id"], p["artifact"], p["status"])
             for p in store.registry["pins"]],
            [("pin-a", "flow", "open"), ("pin-m1", "other", "open")])

    def test_the_echo_reports_a_removal_that_really_happened(self) -> None:
        """The echo's other pole: a glyph that WAS taken down is claimed.

        The third red's neighbour, and the reason that red is written as
        agreement with the record rather than as a banned phrase: "❓
        glyph removed from canvas" is the correct sentence here, so a
        fix that simply deleted the clause would flip the red while
        making the echo useless. The note list is asserted empty in the
        same breath — it is the record-derived half the red compares
        against, and this is where it is pinned as working.
        """
        store = self._store()
        ops: list[dict[str, Any]] = [{"op": "resolve_pin", "id": "pin-a",
                                      "answer": "yes"}]
        record, _ = store.apply_batch({"base_revn": store.head_revn(),
                                       "artifact": "flow", "ops": ops})
        self.assertEqual(canvas.pin_glyph_notes(record, ops), [])
        said = canvas.intent_echo(ops, store.scenes["flow"])
        self.assertEqual(len(said), 1, said)
        self.assertIn("removed from canvas", said[0])

    # RETIRED by v0.9 Task 40: `test_the_shadowed_resolve_reports_the
    # _removal_it_made`, the reviewer's F-9 — the echo's answer comes from
    # the arriving state rather than from absence. It read that off the
    # one batch that could tell the two apart: a resolve whose ❓ lives
    # elsewhere, followed by an add re-drawing a ❓ under the answered id,
    # so the foreign glyph was never in the post-op scene and the re-drawn
    # one was gone from it for a different reason. Task 40 refuses that
    # batch, so the scene no longer exists to be measured, and no legal
    # one replaces it: the add needs an id the registry has filed to
    # shadow anything, and that is exactly what is now refused.
    #
    # Retired rather than re-authored because the property outlives the
    # scene, which was measured and not assumed. Dropping either limb of
    # the `glyph_removed` stamp still fails tests that stay legal — the
    # cross-artifact limb fails `test_a_foreign_resolve_reports_the
    # _removal_it_reached_for`, the pre-op-scene limb fails
    # `test_the_echo_reports_a_removal_that_really_happened`,
    # `test_an_unregistered_glyph_on_the_scene_stays_resolvable` and
    # `test_echo_covers_every_op_kind` (tests/test_backend.py) — and the
    # tolerant third state is `test_red_the_echo_claims_a_removal_the
    # _record_denies`. What the retirement really costs is the assertion
    # about the ADD's own echo line, and the refusal replaces that
    # outright: the agent is now told by a named error which of its two
    # ops lost, instead of having to read it out of an honest echo.

    def test_a_legacy_duplicate_pin_id_gives_up_both_its_glyphs(
            self) -> None:
        """The corpus the old minter wrote, resolved from either side.

        Task 38's first attempt at the shadow hole moved the scan's skip
        from the post-op scene to the PRE-op one, which closed the hole
        and opened its mirror: with the ❓ standing HERE the skip fired,
        and a same-id ❓ on another artifact was never scanned for. That
        is r5-17 restored on exactly the state the minter bug produced —
        registry resolved, foreign glyph drawn, the counts disagreeing —
        and reproducing it needed no exotic scene, just the two-artifact
        corpus any project that ran the old minter already carries.

        The skip is gone entirely. The scan's role gate was always the
        thing protecting an ordinary namesake, and every skip tried on
        top of it stranded a ❓ under the very id being resolved.

        Both artifacts issue the resolve, because the two are not
        symmetric in the code: one path finds the ❓ in `apply_ops`' own
        index and the other reaches it only through the cross-artifact
        scan, and a skip reintroduced on either reading would show up in
        exactly one of these. The registry is asserted alongside the
        canvas — parity is a claim about the two agreeing, so a fix that
        cleared the glyphs while leaving a record open would fail here.
        """
        for aid in ("flow", "other"):
            with self.subTest(resolved_from=aid):
                store = self._store()
                self._legacy_twin(store)
                self.assertEqual(
                    [self._glyphs(store, a) for a in sorted(store.scenes)],
                    [["pin-a"], ["pin-a"]],
                    "the legacy corpus did not build: two ❓ under one id "
                    "is the whole premise")
                ops: list[dict[str, Any]] = [{"op": "resolve_pin",
                                              "id": "pin-a",
                                              "answer": "yes"}]
                record, _ = store.apply_batch(
                    {"base_revn": store.head_revn(), "artifact": aid,
                     "ops": ops})
                drawn = [eid for a in store.scenes
                         for eid in self._glyphs(store, a)]
                open_pins = [p["id"] for p in store.registry["pins"]
                             if p["status"] in ("open", "answered")]
                self.assertEqual(
                    drawn, [],
                    "a resolve from %r left a ❓ under the answered id "
                    "standing on the other artifact: %r" % (aid, drawn))
                self.assertEqual(len(drawn), len(open_pins),
                                 "canvas and registry disagree about how "
                                 "many questions are open (drawn=%r, "
                                 "open=%r)" % (drawn, open_pins))
                self.assertEqual(
                    [p["status"] for p in self._pin_records(store, "pin-a")],
                    ["resolved", "resolved"])
                self.assertEqual(canvas.pin_glyph_notes(record, ops), [])
                self.assertIn("removed from canvas",
                              canvas.intent_echo(ops, store.scenes[aid])[0])

    def test_a_pin_role_shadow_add_is_refused_in_either_order(
            self) -> None:
        """The incoherent batch gets the same answer from either position.

        RE-AUTHORED by v0.9 Task 40, in the commit that landed the
        refusal, per the ENACTMENT NOTE on
        `test_red_an_incoherent_pin_batch_is_swallowed_instead_of_refused`.
        Until then this asserted the same two batches were ACCEPTED both
        ways and converged on an empty canvas — the correct thing to pin
        while acceptance was the only outcome the contract allowed. The
        re-rule made the scene illegal, so what survives is the shape of
        the claim rather than its direction: whatever the answer is, the
        two orders must give the SAME one.

        That is worth a test of its own and not a duplicate of the two
        reds, because the refusal is the first thing here that reads the
        op list twice. The message names the resolve alongside the add,
        and a check written as one forward walk could only find the
        resolve when it came FIRST — add-first would fall back to the
        generic wording and the agent would lose the pointer to the op it
        has to drop. Only this test sends the add first.

        The durable half of the retired version — a resolve takes the
        local ❓ and the foreign one in one act — lives on in
        `test_a_legacy_duplicate_pin_id_gives_up_both_its_glyphs`, whose
        corpus stays legal because the old auto-minter, not an add, made
        its duplicate.
        """
        shadow = {"op": "add", "element": {
            "type": "text", "id": "pin-a", "text": "❓", "x": 500, "y": 0,
            "width": 20, "height": 25, "role": "pin"}}
        resolve = {"op": "resolve_pin", "id": "pin-a", "answer": "yes"}
        orders: tuple[tuple[str, list[dict[str, Any]], str, str], ...] = (
            ("resolve first", [resolve, shadow], "op 1", "op 0"),
            ("add first", [shadow, resolve], "op 0", "op 1"))
        for name, ops, loser, closer in orders:
            with self.subTest(order=name):
                store = self._store()
                escaped = self._send(store, "other", ops)
                self.assertIsInstance(
                    escaped, canvas.BatchError,
                    "%s: the batch closes pin-a and re-draws it under the "
                    "same id, and was accepted: %r" % (name, escaped))
                said = "\n".join(escaped.errors)
                self.assertIn("pin-a", said)
                self.assertIn(loser, said,
                              "%s: the refusal must name the add that lost: "
                              "%r" % (name, said))
                self.assertIn(
                    closer, said,
                    "%s: the refusal must name the resolve too, or the "
                    "agent cannot see which half to drop — and finding it "
                    "cannot depend on it coming first: %r" % (name, said))
                self.assertEqual(self._glyphs(store, "flow"), ["pin-a"])
                self.assertEqual(self._glyphs(store, "other"), [])
                self.assertEqual([(p["id"], p["status"])
                                  for p in store.registry["pins"]],
                                 [("pin-a", "open")])


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


def _rect_stage_gapped() -> list[dict]:
    """`_rect_stage` with its straight arrow stopping 40px short of `r1`.

    The FIRING pole for the endpoint family's rectangle control, added by
    curator batch 24 (2026-08-16) against the mortality spike's §4a
    finding: `diamond_facet_overfire` asserted `Silence("endpoint_gap")`
    on BOTH of its slots, so an `endpoint_gap` that had stopped answering
    at all passed the mutant AND its neighbour. That is guide rule 8's
    archetype sitting in the catalogue since WP4 — a second absence
    proves nothing — and the repair has to be a scene where the same
    check SPEAKS.

    A rectangle is the right shape to prove it on, and that is the whole
    reason this builder is `_rect_stage`'s twin rather than a diamond:
    for a rectangle the bbox IS the outline, so the shape-blindness the
    diamond mutants pin cannot reach this reading. Whatever WP4 does to
    the shape clip, 40px of white space between `a1`'s head and `r1`'s
    left border stays 40px, and the finding stays live evidence that the
    check runs.

    One variable moves against `_rect_stage`: `a1`'s length, 160px to
    120px. The fanned siblings `a2`/`a3` are untouched and still land on
    the border, so a check that had started over-firing on any bound
    arrow would fail the r4-1 guard here rather than pass this pole by
    accident. Measured 2026-08-16: `endpoint_gap` on `a1`, 40.0px,
    direction `outside`, and nothing on `a2`/`a3`.

    Returns:
        The eight-element `_rect_stage` scene with `a1` shortened.
    """
    scene = _rect_stage()
    for element in scene:
        if element["id"] == "a1":
            element["points"] = [[0, 0], [120, 0]]
            element["width"] = 120
    return scene


def _labelled_shape(shape: str, width: int = 200) -> list[dict]:
    """One node carrying a bound label right at the old fitter's budget.

    `fit_label_in` used to allot a label `width - 24` whatever the
    container's shape (canvas.py), so at 200px wide the budget was 176px
    and this label's measured 171px cleared it — the fitter returned
    early and never wrapped, resized or grew anything. On a RECTANGLE
    that is right: the bbox is the shape, and the label has 29px to
    spare. On a 200-wide DIAMOND the same box overhangs the rhombus,
    because at the label's own height the rhombus is 160px across, not
    200px.

    `width` is the OTHER POLE, and it is the room and not the shape that
    it moves: at 220 the same label on the same diamond has a 176px
    chord at the same band and clears by 5px. Holding the label and the
    type fixed and varying only the width is what forces the check to be
    about the body's room — a check that fired on diamonds as such, or
    above some coarse size, would pass a rectangle control and fail this
    one.

    Coordinates are frozen. The label is sized to
    `text_dims("Send for second review", 16)` exactly and centred on the
    node (x = 14 at 200 wide — half a pixel left of centre, since exact
    centring would want 14.5), so drift in the advance table moves the
    finding this stage asserts. The mutant's 11px margin fails its ±30%
    band before the 5px clearance here fails, so the tight pole costs no
    robustness the mutant does not already spend.

    Args:
        shape: The container's element type — `"diamond"` for the mutant,
            `"rectangle"` for the shape control.
        width: The container's width. 200 overhangs; 220 fits.

    Returns:
        The two-element scene: the node `d1`, then its bound label `t1`.
    """
    text = "Send for second review"
    node = el(id="d1", type=shape, x=0, y=0, width=width, height=100,
              customData={"role": "node"},
              boundElements=[{"id": "t1", "type": "text"}])
    lbl = el(id="t1", type="text", x=int((width - 171) / 2), y=40,
             width=171, height=20,
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
    (canvas.py), and the annotation/node overlap check gates on
    `role_of(e) == "annotation"` (canvas.py). So the SAME text over
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


def _clearance_pair_shaped(kind: str) -> list[dict]:
    """Two 100x100 shapes whose BOXES clear by 4px, offset down by 80.

    The offset is the whole scene. Side by side at the same `y`, a
    rhombus pair's facing vertices are 4px apart too and the check would
    be right; stagger the second shape by 80 and the two drawn outlines
    part company while the bounding boxes do not. Across the entire
    shared band (y 80..100, 20px of it, comfortably over `CLEARANCE_BAND`)
    the facing facets run parallel and exactly 84px apart — verified with
    `shape_norm`, which reads 1.000 on both boundary points at y=80, 90
    and 100. The two boxes still clear by 4.

    A separate builder from `_near_miss_pair` on purpose: that scene is
    aligned, box-filling and minimized against a different confound
    (`offgrid_elements`), and its geometry is load-bearing for
    `near_miss_clearance`'s magnitude. This one needs a shape and a
    stagger, which are the two things that scene must not have.

    Args:
        kind: The Excalidraw type for both shapes — `"diamond"` for the
            defect, `"rectangle"` for the control, and nothing else is
            varied between them.

    Returns:
        The two-element scene: nodes `n1` and `n2`.
    """
    return [el(id="n1", type=kind, x=0, y=0, width=100, height=100,
               customData={"role": "node"}),
            el(id="n2", type=kind, x=104, y=80, width=100, height=100,
               customData={"role": "node"})]


def _crowded_pair_shaped(dx: int, dy: int,
                         kind: str = "diamond") -> list[dict]:
    """Two 100x100 shapes, the second offset by `(dx, dy)`.

    A rhombus is the L1 ball of radius 50 about its centre, so the clear
    space between two of them along the facing axis is exactly
    `dx + dy - 100` — which makes the offset the whole experiment and
    every gap in this family arithmetic a reader can check. Every offset
    used is a multiple of 4, because a coordinate off the grid draws an
    `offgrid_elements` note and a mutant whose base and control differ in
    something other than the defect is a confound.

    `kind` exists for one configuration the rhombus CANNOT express: the
    diagonal stagger where the outlines clear on both axes at once. Under
    the L1 metric the gap is the same number along either axis, so a
    rhombus reads identically however the arm picks between them — and
    that is exactly why the arm's axis choice went unwatched. On the
    conic the two axes disagree, and the difference is a whole class of
    near-miss (v0.9 Task 56 fix round 1, F1).

    The offsets in play, and what each is for:

        (56, 48)   4px apart, and their BOXES overlap by 44x52
        (0, 104)   4px apart, stacked
        (104, 0)   4px apart, side by side
        (160, 160) 220px apart — a layout, and the silent pole
        (0, 56)    overlapping: 44x44 of drawn ink, 1936px²
        (0, 72)    overlapping: 28x28 of drawn ink, 784px²
        (68, 80)   ELLIPSE: boxes overlap 32x20, outlines 6.68px apart
        (68, 88)   ELLIPSE: boxes overlap 32x12, outlines 14.68px apart

    (0,56) and (0,72) are the overlap arm's quarter-area bar read from
    both sides, and they land on opposite sides of it for opposite
    reasons. The bar is 1250px² against the drawn rhombus and 2500
    against its box. At (0,56) the drawn patch clears the drawn bar but
    not the box one, so it is the DENOMINATOR that decides; at (0,72) the
    drawn patch misses both bars while the boxes' 100x28 clears the box
    one, so it is the NUMERATOR that decides. Nothing else in the suite
    separates the two halves of that ratio.

    The last two are the diagonal pair, and they share `dx` so the
    stagger is the only variable between them.

    Args:
        dx: The second shape's x offset from the first.
        dy: Its y offset.
        kind: Both shapes' Excalidraw type.

    Returns:
        The two-element scene: nodes `n1` and `n2`.
    """
    return [el(id="n1", type=kind, x=0, y=0, width=100, height=100,
               customData={"role": "node"}),
            el(id="n2", type=kind, x=dx, y=dy, width=100, height=100,
               customData={"role": "node"})]


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

    The SINGLE-LINE arm of `text_overflow` (canvas.py): a text
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


def _opposed_pair(rounded: bool, run: int = 18) -> list[dict]:
    """Two elbowed arrows whose final segments meet head-on at x=250.

    Both final chords are vertical on x=250 and point at each other,
    heads 2px apart — the false-bidi signature. `rounded` decides
    whether those elbows render as curves (type-2 roundness), which
    bends the visible approach away from the stored chord.

    `run` lengthens those final segments without moving the heads or
    the horizontal legs, so the BOW stays 7.4px — it is set by the
    100px horizontal leg through `c1 = P1 + (P2-P0)/6`, not by the
    vertical run — while the segment it sits on grows. That is the one
    variable separating the two curve mutants: the same absolute
    deviation is most of a short leg and a rounding error on a long one.

    Args:
        rounded: True for type-2 roundness, False for sharp elbows.
        run: Length of each final segment. The default 18 is the
            original scene; the heads stay 2px apart at any value.

    Returns:
        The two-arrow scene.
    """
    shape = {"type": 2} if rounded else None
    top = el(id="ea", type="arrow", x=150, y=300 - run, width=100,
             height=run, points=[[0, 0], [100, 0], [100, run]],
             roundness=shape, customData={"role": "edge"})
    bot = el(id="eb", type="arrow", x=150, y=302 + run, width=100,
             height=run, points=[[0, 0], [100, 0], [100, -run]],
             roundness=shape, customData={"role": "edge"})
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


def _attach_chain(shared: bool, headless: bool = False) -> list[dict]:
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

    `headless` strips both arrowheads, which is the ONE cue the ink
    half's whole justification rests on — see the third mutant below. It
    is not a hypothetical shape: `canvas._er_seed_ops` writes
    `"arrow" if "*" in rel["lc"] else None` for each end, so every
    one-to-one erDiagram relation (`A ||--|| N`) mints a line with no
    terminator at either end, verified through the shipped seeder.

    Args:
        shared: True to attach both arrows at N's left-edge midpoint;
            False to move e2's foot to N's right edge.
        headless: True to strip both arrows' arrowheads, as a one-to-one
            ER relation does.

    Returns:
        The five-element scene: nodes A, N, Z and arrows e1, e2.
    """
    head = None if headless else "arrow"
    a = el(id="A", type="rectangle", x=0, y=100, width=80, height=40,
           customData={"role": "node"})
    n = el(id="N", type="rectangle", x=200, y=100, width=80, height=40,
           customData={"role": "node"})
    z = el(id="Z", type="rectangle", x=528, y=100, width=80, height=40,
           customData={"role": "node"})
    e1 = el(id="e1", type="arrow", x=80, y=120, width=120, height=0,
            points=[[0, 0], [120, 0]], endArrowhead=head,
            startBinding={"elementId": "A", "focus": 0, "gap": 1},
            endBinding={"elementId": "N", "focus": 0, "gap": 1},
            customData={"role": "edge"})
    foot = 200 if shared else 280
    e2 = el(id="e2", type="arrow", x=foot, y=120, width=528 - foot,
            height=0, points=[[0, 0], [528 - foot, 0]], endArrowhead=head,
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


def _crossing_tail() -> list[dict]:
    """An arrow that starts on `N`'s near border and crosses the whole node.

    The scene for `lint_layout`'s interior-run walk and the `if not
    outside:` gate in front of it (canvas.py). AS BUILT this is the
    walk's ungated pole and the lint is right to shout: the tail sits
    exactly on N's left border, so `outside` is 0, the walk runs, and
    the arrow is reported as crossing 98px of N before it ever reaches
    `B`. 98 and not 100 because the walk clips 1px in from the outline,
    which is what keeps a fanned attach point at zero (the r4-1
    over-fire).

    Everything here exists to make the gate the only variable. `N` is a
    RECTANGLE, not a diamond: the diamond/ellipse branch zeroes both
    readings whenever `shape_clearance` is within `tol`, so it reaches
    the walk on a tolerable gap and the rectangle branch — a raw bbox
    distance with no such floor — does not. `B` is a second bound node
    rather than a bare endpoint so the arrow is a legal two-ended edge
    and nothing else in `lint_layout` has an opinion; N's 100x80 keeps
    `endpoint_tol` at its 14px floor (`max(14, 0.10 * 80)`), which is
    the number that makes the 3px mutation "tolerable" rather than a
    gap the endpoint check would catch on its own.

    Returns:
        The three-element scene: nodes `N` and `B`, then arrow `e1`.
    """
    return [el(id="N", type="rectangle", x=200, y=100, width=100,
               height=80),
            el(id="B", type="rectangle", x=400, y=100, width=100,
               height=80),
            el(id="e1", type="arrow", x=200, y=140, width=200, height=0,
               points=[[0, 0], [200, 0]],
               startBinding={"elementId": "N", "focus": 0, "gap": 0},
               endBinding={"elementId": "B", "focus": 0, "gap": 0})]


def _labelled_elbow(foreign_x: int, foreign_y: int) -> list[dict]:
    """An unbalanced 400+100 L whose label the client draws on the corner.

    Blind-spot 1's Pin A. The arrow is a 3-point elbow, so the client
    centres its label on `points[1]` — the corner, at (500, 100) — while
    the arc-length walk this repo used until v0.9 put it 150px away at
    (350, 100). Both positions are on the stroke and neither is
    obviously wrong from the store, which is why the two models were
    indistinguishable for three versions.

    The foreign box sits at `y = 85`, deliberately NOT centred on the
    corner: a box centred there is caught by `passes_through_foreign`
    instead, and the pin would pass for a reason that has nothing to do
    with the label (the trap `spike-row26-verify` documented). At
    `x = 505` it is also clear of the stroke's own `x <= 500`, so it
    touches neither leg.

    Args:
        foreign_x: The foreign node's x. 505 puts it over the DRAWN
            label; 290 puts it over the arc-length model's answer.
        foreign_y: The foreign node's y.

    Returns:
        The five-element scene: `src`, `dst`, `foreign`, arrow `e1`,
        and `e1`'s bound label.
    """
    arrow = el(id="e1", type="arrow", x=100, y=100, width=400, height=100,
               points=[[0, 0], [400, 0], [400, 100]],
               startBinding={"elementId": "src", "focus": 0, "gap": 0},
               endBinding={"elementId": "dst", "focus": 0, "gap": 0},
               customData={"role": "edge"})
    label = el(id="e1-label", type="text", x=0, y=0, width=60, height=20,
               text="ships", originalText="ships", containerId="e1",
               fontSize=16)
    label["x"], label["y"] = canvas.arrow_label_slot(arrow, label)
    return [el(id="src", type="rectangle", x=-60, y=70, width=160,
               height=60, customData={"role": "node"}),
            el(id="dst", type="rectangle", x=440, y=200, width=160,
               height=60, customData={"role": "node"}),
            el(id="foreign", type="rectangle", x=foreign_x, y=foreign_y,
               width=160, height=60, customData={"role": "node"}),
            arrow, label]


def _labelled_skew_z(rounded: bool) -> list[dict]:
    """A 4-point skew Z whose label curvature moves 23.8px.

    Blind-spot 1's Pin B, and the ONLY scene that separates the client's
    third branch from its fourth. An even point count sends the client
    to the middle SPAN — here `points[1] -> points[2]` — whose chord
    midpoint is (1300, 130) sharp and whose Bezier arc midpoint is
    (1324.0, 126.0) rounded, both measured against the running app. An
    odd-point elbow cannot discriminate them: branch 1 ignores
    `roundness` entirely.

    The 80x40 foreign box straddles that gap, clear of the chord
    midpoint's label box and squarely over the Bezier one's.

    Args:
        rounded: True for `{"type": 2}`, False for a sharp Z.

    Returns:
        The five-element scene.
    """
    arrow = el(id="e1", type="arrow", x=900, y=100, width=460, height=60,
               points=[[0, 0], [400, 0], [400, 60], [460, 60]],
               roundness={"type": 2} if rounded else None,
               startBinding={"elementId": "src", "focus": 0, "gap": 0},
               endBinding={"elementId": "dst", "focus": 0, "gap": 0},
               customData={"role": "edge"})
    label = el(id="e1-label", type="text", x=0, y=0, width=40, height=20,
               text="on", originalText="on", containerId="e1", fontSize=16)
    label["x"], label["y"] = canvas.arrow_label_slot(arrow, label)
    return [el(id="src", type="rectangle", x=740, y=70, width=160,
               height=60, customData={"role": "node"}),
            el(id="dst", type="rectangle", x=1360, y=130, width=160,
               height=60, customData={"role": "node"}),
            el(id="foreign", type="rectangle", x=1320, y=108, width=80,
               height=40, customData={"role": "node"}),
            arrow, label]


def _two_leg_approach(pts: list[tuple[int, int]],
                      rounded: bool) -> list[dict]:
    """A two-leg approach ending exactly ON a bound rectangle's border.

    Blind-spot 3's walk scenes. The endpoint is on the border so
    `outside` is 0 and the interior walk is actually REACHED — the gate
    in front of it is what makes most curved-final scenes never get
    this far, and a pin that does not clear it measures nothing.

    Args:
        pts: The path in absolute coordinates; `pts[-1]` must sit on
            `N`'s outline.
        rounded: True for `{"type": 2}`, False for sharp chords.

    Returns:
        The three-element scene: `N` (the bound target), `S` (a source
        so the arrow is a legal two-ended edge), and arrow `e1`.
    """
    ox, oy = pts[0]
    local = [[p[0] - ox, p[1] - oy] for p in pts]
    return [el(id="N", type="rectangle", x=100, y=50, width=120,
               height=100),
            el(id="S", type="rectangle", x=ox - 40, y=oy - 20, width=40,
               height=40),
            el(id="e1", type="arrow", x=ox, y=oy,
               width=max(abs(p[0]) for p in local),
               height=max(abs(p[1]) for p in local), points=local,
               roundness={"type": 2} if rounded else None,
               startBinding={"elementId": "S", "focus": 0, "gap": 0},
               endBinding={"elementId": "N", "focus": 0, "gap": 0})]


# The two scenes, named so the poles read the same way in both mutants.
_RUN_HIDDEN = [(0, 40), (160, 40), (100, 60)]
_RUN_OVERSTATED = [(0, 160), (160, 160), (220, 135)]


def _corner_elbow_pair(rounded: bool) -> list[dict]:
    """Two elbows meeting corner to corner, crossing only once drawn.

    Blind-spot 2's crossing pin. The stored chords never touch — the two
    L's meet at their corners and turn away — so the chord-based
    instrument reports a clean diagram. The drawn curves bow INTO each
    other and cross twice.

    Args:
        rounded: True for `{"type": 2}`, False for sharp elbows.

    Returns:
        The two-arrow scene.
    """
    shape = {"type": 2} if rounded else None
    return [el(id="ca", type="arrow", x=0, y=0, width=100, height=100,
               points=[[0, 0], [100, 0], [100, 100]], roundness=shape,
               customData={"role": "edge"}),
            el(id="cb", type="arrow", x=100, y=0, width=100, height=100,
               points=[[0, 0], [-100, 0], [-100, 100]], roundness=shape,
               customData={"role": "edge"})]


def _fan_finals(sep: int) -> list[dict]:
    """Two curved arrows leaving one hub and running 200px near-parallel.

    Blind-spot 2's corridor pin, and the one that guards the FIX rather
    than the defect: the obvious curvature fix for `shared_corridors` —
    swap `_abs_segments` for flattened segments — scores ZERO here,
    because twenty micro-segments per span each fail `_axis`'s 2px
    tolerance independently and the ones that pass carry a drifting
    fixed coordinate. Two 200px verticals `sep` px apart are one thick
    stroke to a reader; anything that cannot say so is not a corridor
    instrument.

    The two targets track `sep`, so each arrow lands on its own node's
    border at either pole and the scene has no endpoint defect for the
    corridor finding to be confused with. At `sep = 6` the targets are
    6px apart and overlap, which is not an accident to be tidied away:
    two edges whose whole approach is 6px apart ARE going to two nodes
    6px apart, and that is the drawing this instrument exists to name.

    Args:
        sep: Lateral separation in px. 6 is inside the 16px tolerance
            and obviously one stroke; 40 is outside it.

    Returns:
        The five-element scene: `hub`, `t1`, `t2`, and arrows `fa`/`fb`.
    """
    return [el(id="hub", type="rectangle", x=60, y=60, width=120,
               height=40, customData={"role": "node"}),
            el(id="t1", type="rectangle", x=110, y=300, width=10,
               height=40, customData={"role": "node"}),
            el(id="t2", type="rectangle", x=110 + sep, y=300, width=10,
               height=40, customData={"role": "node"}),
            el(id="fa", type="arrow", x=100, y=100, width=15, height=200,
               points=[[0, 0], [15, 0], [15, 200]], roundness={"type": 2},
               startBinding={"elementId": "hub", "focus": 0, "gap": 0},
               endBinding={"elementId": "t1", "focus": 0, "gap": 0},
               customData={"role": "edge"}),
            el(id="fb", type="arrow", x=100 + sep, y=100, width=15,
               height=200, points=[[0, 0], [15, 0], [15, 200]],
               roundness={"type": 2},
               startBinding={"elementId": "hub", "focus": 0, "gap": 0},
               endBinding={"elementId": "t2", "focus": 0, "gap": 0},
               customData={"role": "edge"})]


def _stub_chain(rounded: bool, shared: bool) -> list[dict]:
    """A -> N -> Z whose two feet meet N through an 8px vertical jog.

    The migrated-reader scene for `phantom_passthrough`'s feet block
    (curves-fold review F3). Both arrows turn 8px perpendicular in the
    last 8px of a 300px run, which is what a router draws when source
    and target sit one nudge off the same rank — and it is the shape
    that makes the STORED chord and the DRAWN arrival name different
    axes. Stored, each foot's last leg is a vertical stub, so the block
    calls both feet vertical, gives them the same sign and never pairs
    them. Drawn, `_arrival_path`'s secant reads the 300px body: e1
    arrives horizontally and e2 leaves horizontally, opposed, and the
    pair is found.

    WHY THE SHARP TWIN OF THE MUTANT IS NOT THE NEIGHBOUR, measured
    rather than assumed. At `shared` the sharp drawing is a merged
    stroke too — one line at y=112 running A through N to Z with a
    hairpin at x=200 — and the stored read is SILENT on it. Pairing the
    mutant against that pole would assert a miss as the healthy answer.
    The neighbour is therefore the sharp FANNED chain, whose feet sit on
    opposite borders with the node between them and whose silence is the
    correct reading of a correct picture. The residual — an 8px jog
    blinding this block in the sharp world as well — is recorded at the
    registration and left to the check's owner.

    Args:
        rounded: True to stamp `{"type": 2}` on both arrows.
        shared: True to put e2's foot on N's LEFT border, where e1's
            already is, so the pair covers all 80px of N; False to fan
            it onto the RIGHT border, one node width clear.

    Returns:
        The five-element scene: nodes A, N, Z and arrows e1, e2.
    """
    r = {"type": 2} if rounded else None
    foot = 200 if shared else 280
    return [el(id="A", type="rectangle", x=-180, y=100, width=80,
               height=40, customData={"role": "node"}),
            el(id="N", type="rectangle", x=200, y=100, width=80, height=40,
               customData={"role": "node"}),
            el(id="Z", type="rectangle", x=528, y=100, width=80, height=40,
               customData={"role": "node"}),
            el(id="e1", type="arrow", x=-100, y=112, width=300, height=8,
               points=[[0, 0], [300, 0], [300, 8]], roundness=r,
               endArrowhead="arrow",
               startBinding={"elementId": "A", "focus": 0, "gap": 1},
               endBinding={"elementId": "N", "focus": 0, "gap": 1},
               customData={"role": "edge"}),
            el(id="e2", type="arrow", x=foot, y=120, width=528 - foot,
               height=8, points=[[0, 0], [0, -8], [528 - foot, -8]],
               roundness=r, endArrowhead="arrow",
               startBinding={"elementId": "N", "focus": 0, "gap": 1},
               endBinding={"elementId": "Z", "focus": 0, "gap": 1},
               customData={"role": "edge"})]


def _bow_past_box(rounded: bool, under: bool) -> list[dict]:
    """One 300+300 elbow and one small box in the gap its bow sweeps.

    The through-node arm's scene — the FIFTH reader of arrow shape, the
    one the curves fold-in found while building the gate's second arm
    and migrated onto `_rendered_path`. Two elements and nothing else:
    the arrow binds nothing, so `ends` is empty and every node in the
    scene is foreign to it.

    A 300+300 elbow's drawn corner leaves its stored L by 22.2px at the
    apex — the curve rides ABOVE the horizontal chord and RIGHT of the
    vertical one — so an 80x16 box straddling either side of a chord
    separates the two readings cleanly and in opposite directions.

    Args:
        rounded: True to stamp `{"type": 2}`, False for the sharp L.
        under: True to sit the box at y=96..112, straddling the stored
            chord at y=100 where the drawn curve has already climbed
            to y=81..87; False to sit it at y=76..92, in the gap the
            bow crosses and the chord clears by 8px.

    Returns:
        The two-element scene: foreign box `F`, then arrow `e1`.
    """
    return [el(id="F", type="rectangle", x=200, y=96 if under else 76,
               width=80, height=16, customData={"role": "node"}),
            el(id="e1", type="arrow", x=100, y=100, width=300, height=300,
               points=[[0, 0], [300, 0], [300, 300]],
               roundness={"type": 2} if rounded else None,
               customData={"role": "edge"})]


def _short_finals(rounded: bool) -> list[dict]:
    """Two 80px finals 15px apart, each after a long vertical approach.

    F8's regime, and the population the shipped corridor pin misses.
    `_fan_finals` puts a 200px final after a 15px stub, whose bow is
    2.5px against a 14.3px band and clears stage 2 comfortably; this is
    the opposite ratio, and the review measured it as 33% of the
    all-curved corpus's axis-aligned stretches. The two finals bow 22.2
    and 13.7px off their own chords against a band of 5.7px, so
    `_reads_as_line` refuses to classify either and `shared_corridors`
    never sees them.

    THE APPROACHES ARE DELIBERATELY UNPAIRABLE, which is what makes the
    loss visible. `shared_corridors` breaks on the first qualifying
    stretch pair, so an approach that corridors would supply the finding
    the finals lost and hide the defect. These two run on one line at
    x=100 but their extents abut at -15px — past the -10px abutting
    window — and neither reaches the 60px overlap, so the finals are the
    only pair that can speak.

    The drawn separation is the point of the pin: sampled at the finals'
    midpoint the two strokes sit 15.4px apart curved against 15.0px
    sharp. The picture a reader sees is the same thick stroke in both;
    only the check's willingness to look at it moved.

    Args:
        rounded: True to stamp `{"type": 2}` on both arrows.

    Returns:
        The six-element scene: hubs A and B, targets t1 and t2, and
        arrows `fa` (down then right) and `fb` (up then right).
    """
    r = {"type": 2} if rounded else None
    return [el(id="hubA", type="rectangle", x=60, y=-40, width=80,
               height=40, customData={"role": "node"}),
            el(id="hubB", type="rectangle", x=60, y=500, width=80,
               height=40, customData={"role": "node"}),
            el(id="t1", type="rectangle", x=180, y=280, width=40,
               height=40, customData={"role": "node"}),
            el(id="t2", type="rectangle", x=180, y=320, width=40,
               height=40, customData={"role": "node"}),
            el(id="fa", type="arrow", x=100, y=0, width=80, height=300,
               points=[[0, 0], [0, 300], [80, 300]], roundness=r,
               startBinding={"elementId": "hubA", "focus": 0, "gap": 0},
               endBinding={"elementId": "t1", "focus": 0, "gap": 0},
               customData={"role": "edge"}),
            el(id="fb", type="arrow", x=100, y=500, width=80, height=185,
               points=[[0, 0], [0, -185], [80, -185]], roundness=r,
               startBinding={"elementId": "hubB", "focus": 0, "gap": 0},
               endBinding={"elementId": "t2", "focus": 0, "gap": 0},
               customData={"role": "edge"})]


def _fanned_void_foot(shape: str) -> list[dict]:
    """One arrow stopped where the auto-fan puts a foot on a 300x300 node.

    `_fan_point` (canvas.py) spreads attach points along the BOUNDING
    BOX side, so on anything but a rectangle the fanned feet land in the
    corner void. (200, 175) is not a coordinate anybody chose: it is
    exactly what `fan_attach_points` writes for the first of three
    arrows converging on this node's left side, `length * 1/(3+1)`.

    300x300 AND NOT SOMETHING SMALLER, because a pin has to be able to
    flip. `float_diamond` reports nothing under 12px whatever shape it
    learns to read, and the fan's miss on a circle is 0.118r — so at
    200x200 it is 11.80px and would still be silent after the ellipse
    arm landed, which is a red nothing could ever turn green. At r=150
    the miss is 17.71px, clear of that floor and still far under the
    30px `endpoint_tol` this node earns.

    The two poles are the SAME void foot on two shapes, so the only
    thing that differs is which outline the checks are willing to
    measure it against. On the ellipse the foot floats 17.71px outside
    the drawn curve in empty canvas; on the rhombus the identical point
    is 53.03px out.

    Args:
        shape: `"ellipse"` for the silent pole, `"diamond"` for the
            pole where the instrument speaks.

    Returns:
        The three-element scene: node `N`, source `S`, arrow `a1`.
    """
    return [el(id="N", type=shape, x=200, y=100, width=300, height=300,
               customData={"role": "node"}),
            el(id="S", type="rectangle", x=0, y=155, width=60, height=40,
               customData={"role": "node"}),
            el(id="a1", type="arrow", x=60, y=175, width=140, height=0,
               points=[[0, 0], [140, 0]],
               startBinding={"elementId": "S", "focus": 0, "gap": 1},
               endBinding={"elementId": "N", "focus": 0, "gap": 1},
               customData={"role": "edge", "routed": True})]


# ---------------------------------------------------------------------------
# The day-one catalogue. Each entry pairs a scene the drawing gets WRONG
# today with a neighbour that must read right today; the mutant tests below
# are `expectedFailure` exactly where the defect is still live, so WP4's fix
# announces itself as an unexpected success rather than a silent pass.
# ---------------------------------------------------------------------------

# Not every red in this file is a catalogue entry, and the gap is not small:
# re-measured 2026-08-15 after curator batch 23, `mutants list --red` reports
# 8 (of 41 entries) while this file carries 17 expectedFailure methods —
# the catalogue has grown well past the "6 of 30" this paragraph carried
# until today, which is the drift it warns about happening to itself. Task
# 56 moved BOTH halves down at once, which no earlier change had done: it
# flipped one catalogue red (`diamond_clearance_overfire`) and all three of
# `TestShapeBlindAnnotationOverlap`'s, emptying that class, and it added
# four catalogue entries that landed GREEN (the fourth,
# `diagonal_ellipses_near_miss`, in its review's fix round) — so 7/20 became
# 6/16 while the catalogue grew. Batch 21 just before it had gone the other way (one
# catalogue red and five hand-authored across three new classes, 6/14 to
# 7/20, the largest single-batch addition this census has recorded), which
# together are the reminder that the two halves do not move together and
# that a total says nothing. COUNT THE METHODS WITH CARE: a bare
# `grep -c @unittest.expectedFailure` says 21 against 17 real methods,
# because four mentions are PROSE — this very paragraph,
# `test_red_mutants_are_red_by_mismatch_not_by_error`'s docstring, and two
# in `coverage_table` and its guard. Measure the METHODS, which is what
# `grep -cE '^\s*@unittest\.expectedFailure\s*$'` does, or read the runtime
# line. That miscount is what once put "15" here when the true figure was
# 13, and the figure quoted in this sentence has ITSELF gone stale once
# (it read 24 when the bare grep returned 21, caught in the task 49
# re-review) — which is the joke this paragraph keeps telling on itself and
# the reason the command is now written out instead of its answer.
# The nine outside live
# in the five classes `HAND_AUTHORED_RED_CLASSES` names, which since
# curator batch 16 is a CHECKED structure rather than a sentence — read the
# counts there, and see
# `TestCoverage.test_the_hand_authored_red_classes_are_the_ones_that_exist`
# for why this paragraph no longer states them itself.
# They are outside deliberately, because a Mutant is
# judged by `collect_findings` over an ELEMENT LIST and none of what they
# measure is in one. Each class carries its own standing guard for its reds;
# the one below covers CATALOGUE alone.
# From curator batch 15 until v0.9 Task 49 one red lived outside this file
# entirely — `TestSnapshotTierOne` in `tests/test_backend.py`, where the
# connected tab's export was never measured against the drawing — so the
# suite's default line ran exactly one ahead of the count here. Task 49
# flipped it, the two numbers agreed at 16 for part of one day, and curator
# batch 23 put a red back in that same class the same afternoon (the tier-1
# export has no ceiling, and the reason given for not having one was
# measured false). So the default line runs one ahead again, at 18 against
# the 17 here. Read the brevity of that agreement as the warning: it says
# only that every red in the suite happens to live in one file, which the
# next red authored anywhere else undoes, and which lasted hours.
# Task 50 removed the LAST expectedFailure from
# `tests/test_mutants_render.py`, which is why the gated line no longer runs
# ahead of the default one — those reds were never part of the count here,
# they were the reason `MUTANTS_RENDER=1` used to read higher. State that as
# the render file's own count (a grep for the DECORATOR, which is 0) and not
# as a suite total: this paragraph was drafted claiming ef=16 on three
# different lines at once, and a concurrent curator batch adding one red in
# `test_backend.py` falsified it before the commit landed. Which is the
# paragraph's own lesson arriving on schedule. Equally, do not reconcile
# them by hand if they part again — go find the outside red.
# (The pair has read 16/15, 21/20, 17/16 and 15/14
# before, and never the same 16 and 15 twice: Task 23 flipped two, curator
# batch 19 added two once on each side of the CATALOGUE boundary, Task 24
# flipped two more, batch 20 added one, batch 21 added six, Task 56 flipped
# four, the curves fold-in flipped two and Task 49 flipped the outside one —
# a fair warning that totals prove nothing here, matching or not, and only
# the split is worth reading.)
# These counts are a hand enumeration and drift silently, so re-measure them
# rather than trusting them. Twice caught stale now, and the second time is
# the instructive one: on 2026-08-12 this read "(5)" for `TestStoreIntegrity`
# when WP1 had already flipped four of the five, and it still read "32 / six
# classes / `TestPinIdentityIntegrity` (3)" after Task 40 flipped that class's
# last three reds — a class does not merely lose a number here, it leaves the
# list, and nothing in the suite notices either way. Task 41 flipped that
# class's final two, so it has now left; that is the shape to expect, and
# Task 21 is the third instance: `TestExportCompleteness` and `TestPaintOrder`
# both left in one commit.
#
# AUTHORING RULE for the reds in this file, learned the expensive way in
# v0.9 Task 9: nothing load-bearing may sit AFTER the line a red is expected
# to fail on. `expectedFailure` stops the method at its first failure, so
# every later assertion is dead code for the whole life of the red — it is
# first executed on the day the defect is fixed, which is the worst possible
# day to discover it was wrong. One such assertion carried a literal `6`
# against a live value of `3` from the day it was written and nobody could
# have known: the suite was green throughout, and the mutant was red for the
# right reason at the line above it. Task 9 rewrote it to derive from the
# live store instead.
#
# So: put the whole claim in the assertion that fails, or split the test in
# two. A red whose tail is dead is a red that will hand its flipper a
# failure unrelated to the fix they just landed, and they will reasonably
# read that as their own bug.
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
#
# NEIGHBOUR REPOINTED by curator batch 24 (2026-08-16), from
# `Neighbour(_rect_stage, Silence("endpoint_gap"))`. The mortality spike
# (§4a) computed the catalogue's silence pairings and found this entry the
# one place where BOTH slots assert the same Silence: an `endpoint_gap`
# patched to answer nothing passed the mutant and its control alike, so the
# pin proved nothing about its own instrument. Guide rule 8's archetype,
# and it had been here since WP4 — the neighbour's old comment said its
# expectation was "the same as the other two diamond mutants, deliberately",
# which was true and was the defect: those two pair their Silence with a
# FindingSpec and this one inherited the scene without the firing.
#
# The rectangle SILENCE pole is not lost, which is what makes the repoint
# cheap: `diamond_corner_silence` and `diamond_wrong_direction` both still
# carry `Neighbour(_rect_stage, Silence("endpoint_gap"))`, so the r4-1
# fanned-sibling over-fire guard is asserted twice over in green runs. What
# was missing everywhere was a GREEN firing, and `_rect_stage_gapped` is it.
_register(Mutant(
    "diamond_facet_overfire",
    build=_diamond_stage,
    op="unchanged", args={},
    expect=Silence("endpoint_gap"),
    neighbour=Neighbour(_rect_stage_gapped,
                        FindingSpec("endpoint_gap", element="a1",
                                    magnitude=(40, 0.30),
                                    direction="outside"))))

# Over-fire: the through-node test used the node's bbox, so an arrow
# clipping the diamond's empty corner read as passing through it. FIXED by
# WP4 (task 16), which clips `_seg_hits_rect` to the rendered outline.
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
# possible binding — scored gap 0 and was never reported. FIXED by WP4
# (task 16): the radial measure is gone, and the perpendicular distance to
# the facet reads 44.7px at the center of this 200x100 rhombus. The ±90%
# band admits [5, 95] and so still excludes both numbers that would mean the
# fix went wrong: 0, the old answer, and 100, the axial penetration depth
# `endpoint_gap` reports on this same scene.
#
# CURATOR RULING on the magnitude's sign (Task-16 report concern 2, batch 12):
# unsigned is right, and no direction mutant is owed. The remedy for an
# endpoint 50px inside a diamond and one 50px outside is the same — put it on
# the outline — so the side adds nothing a fix would act on, and
# `endpoint_gap` already carries direction for the user-facing lint. The
# contradiction the concern was raised about is already gone: `instruments`'
# header no longer calls `abs()` a preserved bug, and `_dist_to_diamond`
# documents the unsigned choice with its reasoning. Nothing to fix, and this
# note exists so the question is not re-opened from the old header text.
_register(Mutant(
    "float_diamond_center_zero",
    build=_diamond_stage,
    op="move_endpoint_to", args={"arrow_id": "a1", "end": "end",
                                 "x": 400, "y": 350},
    expect=FindingSpec("float_diamond", element="a1",
                       magnitude=(50, 0.90)),
    neighbour=Neighbour(_diamond_stage, Silence("float_diamond"))))

# Spurious: false_bidi read the stored chord, so a curved elbow whose
# rendered path bows away from it still read as one bidirectional line.
# FLIPPED by WP4 (task 13), which samples the rendered PATH over the
# final stretch — NOT the tangent at the arrowhead, which points
# straight down the chord here and would never have flipped it.
# Derivation corrected in task 13 against the bundled renderer (roughjs
# `generator.curve`, which pads by DUPLICATING the endpoints; the
# original comment's c2 came from reflecting them): the final span's
# Catmull-Rom controls are c1=(266.7,285) and c2=(250,297), and the
# curve passes through (254.8,293.2) at t=0.6 and reaches x=257.4 at
# t=1/3, so |dx| up to 7.4 off the x=250 chord line breaks false_bidi's
# 2px collinearity tolerance while the sharp neighbour, whose path IS
# its chord, keeps firing.
#
# RATIFIED by the curator (batch 11), re-derived rather than read: the
# pad choice reaches ONLY c2 — c1 is `P1 + (P2-P0)/6`, which the trailing
# pad cannot touch — and c2's x is 250 under both pads because P1, P2 and
# both candidate P3s share x=250. So the x profile is
# `250 + 50*(1-t)^2*t` either way, peaking at 200/27 = 7.407 at t=1/3,
# and the encoded expectation never depended on which pad was assumed.
# The y profile DOES differ (293.23 duplicated against 291.94 reflected),
# which is why the old numbers were worth correcting rather than
# tolerating: they were harmless to this entry's assertion and would
# have misled the next reader who needed a y.
_register(Mutant(
    "curved_elbow_spurious_bidi",
    build=lambda: _opposed_pair(rounded=True),
    op="unchanged", args={},
    expect=Silence("false_bidi"),
    neighbour=Neighbour(lambda: _opposed_pair(rounded=False),
                        FindingSpec("false_bidi"))))

# Blind spot, and the cost of the fix above (task 13 review F2).
# FLIPPED by v0.9 WP4 (task 24). `_reads_as_line` measured the off-axis
# spread of the WHOLE final span against a flat 2px band, so the 7.4px
# bow disqualified the span at any length — and the bow is set by the
# 100px HORIZONTAL leg, not by the vertical run, so it did not shrink as
# the run grew. At run=200 the two arrows are straight on x=250 for
# their last quarter (dx +0.45 at t=0.9), collinear, opposed, heads 2px
# apart: a reader saw one bidirectional line and the check could not
# report one at any run length.
#
# The discriminator this entry existed to force is RELATIVE, not
# absolute: 7.4px is most of an 18px approach and a rounding error on a
# 200px one. Its neighbour is the entry ABOVE — the same scene at
# run=18, which must stay silent — so widening the band flips this and
# breaks that. The fix that satisfies both takes the WIDER of the old
# 2px and 1/14 of the stretch's own on-axis extent: at 18px the band is
# still exactly 2px (neighbour unchanged, bit for bit), at 200px it is
# 14.3px and admits the bow. `max` rather than a replacement makes the
# change one-directional — `false_bidi` can gain findings from it and
# never lose one — and the corpus confirms it: 1 finding before, the
# SAME 1 after (`argus-r4-arm3`'s `r-run-signal`/`r-run-rerun` pair,
# measured 2026-08-14). This was a standing blind spot rather than a
# live miss, and closing it cost no over-fire.
_register(Mutant(
    "long_run_curve_hides_bidi",
    build=lambda: _opposed_pair(rounded=True, run=200),
    op="unchanged", args={},
    expect=FindingSpec("false_bidi"),
    neighbour=Neighbour(lambda: _opposed_pair(rounded=True),
                        Silence("false_bidi"))))

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

# Phantom pass-through — was RED BY ABSENCE, FLIPPED by v0.9 WP4 (task 24).
# e1's end and e2's start are one point on N, so the pair draws as a single
# unbroken stroke THROUGH the box: a reader sees A -> Z and a decoration in
# the middle. e1 is the highest-hit-rate class from the Aug 2026 scan, and
# the ELK spike produced it in production. There was deliberately NO
# `phantom_passthrough` entry in DETECTORS — the table lists detectors that
# exist, not ones we want — so this failed with "no finding of
# check='phantom_passthrough'" until the lint landed. It fulfils the
# "promote" disposition on sweep survivor
# `move_node_onto_rank:chain:ebb2e1f6`, which found this configuration by
# accident and could only record it.
#
# THE SURVIVOR DID NOT DIE WITH THE PROMOTION, and the plan said it would
# (V0.9-PLAN.md WP4b item 1 predicted `test_live_sweep_reproduces_the_record`
# failing loudly as the discovery-flip signal). The sweep cell and this
# mutant turn out to be the two halves of e1 and only one of them ships:
# the sweep's rank line leaves the feet on OPPOSITE borders with the whole
# node between them, and reporting that is the broad criterion measured at
# 70 findings over 24 shipped artifacts. The survivor is re-dispositioned
# `promote` -> `allow` in this change with that number; the record is
# unchanged and no sweep was re-run, because nothing about it went stale.
#
# MAGNITUDE added at the flip, and it is the assertion that makes this
# entry discriminate rather than merely detect. 80px is how much of N has
# arrow drawn over it — the whole node. The three other readings a wrong
# implementation would report are all present in the same message or in the
# neighbouring checks and are all excluded by the +-10% band: 0 (the gap
# between the two feet), 80 is also the node's own width so that one
# coincides here by construction and is disambiguated by the shared=False
# control below, and 448 (the merged stroke's length, which is what
# `shared_corridor` and the spike both quote about this same picture).
#
# THE OTHER POLE, paid for at the flip. The neighbour was
# `Silence("shared_corridor")` over `_attach_chain(shared=False)` — a
# contingent negative that proved something about the picture but nothing
# about THIS check, because a Silence on a check with no detector passes
# vacuously. It is now `Silence("phantom_passthrough")` over the same
# builder, and that Silence bites: one foot moves 80px onto N's far border,
# the two arrows stay collinear and stay opposed, and the ONLY thing that
# changes is whether ink crosses the box. A check that fired on every
# collinear in/out pair — which is the literature scan's own broad
# criterion, and fires 70 times on 24 shipped artifacts — passes the old
# neighbour and fails this one. The corridor pole is not lost: the two
# mutants below still assert it over the same pair of scenes.
_register(Mutant(
    "phantom_passthrough_shared_attach",
    build=lambda: _attach_chain(shared=True),
    op="unchanged", args={},
    expect=FindingSpec("phantom_passthrough", element="N",
                       magnitude=(80, 0.10)),
    neighbour=Neighbour(lambda: _attach_chain(shared=False),
                        Silence("phantom_passthrough"))))

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
# firing conditions (canvas.py): two arrows bound to the same node
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

# RED BY ABSENCE — the fourth member of the e1 family, and the one that
# takes the ink half's own argument at its word (curated 2026-08-15 from
# `spike-e1-perceptual.md` §5).
#
# The broad criterion was measured, rejected and closed on a mechanism
# claim, not on a count: every one of the 70 collinear in/out pairs has an
# ARROWHEAD terminating each stroke at the node, so the reader has a cue
# saying the path ends here and no amodal completion across the box is
# invited. 174 of 174 arrows in the frozen corpus carry one, which is what
# made the argument safe to close on. The corollary nobody had checked is
# what happens where the cue is absent — and there the argument does not
# weaken, it evaporates: two plain lines meeting a box on opposite borders,
# nothing terminating either, is the configuration the completion
# literature is actually about.
#
# IT IS REACHABLE THROUGH SHIPPED CODE, which is what moves this off the
# "interesting hypothetical" pile. `canvas._er_seed_ops` gives an end an
# arrowhead only when its cardinality token contains `*`, so an erDiagram
# one-to-one relation mints a headless line; run through the real seeder,
# `A ||--|| N : owns` / `N ||--|| Z : holds` produces `r-a-owns` and
# `r-n-holds` with `startArrowhead=None, endArrowhead=None` at both ends.
# Chain two of them through a node on one rank and this scene is what the
# user gets, out of the shipped mermaid path, with no hand-authoring.
#
# THE PAIR IS THE ASSERTION, because the discriminator here is the cue and
# not a number. Mutant and neighbour are the same five elements, the same
# feet, the same collinearity, the same zero ink over N — `headless` is the
# only thing that differs. An implementation that reports collinear in/out
# pairs regardless of terminators fires on BOTH and fails the neighbour,
# which is precisely the broad criterion the 70 already rejected; an
# implementation that reads terminators fires on this and stays quiet
# there. No `magnitude`: the current message template (and `_PHANTOM_RE`
# with it) reports how much of the node has arrow drawn over it, and that
# is 0 here BY CONSTRUCTION — the whole point is the no-ink case — so
# demanding a number today would specify a message that lies. Whoever
# lands the fix owes a second template naming the span the reader
# completes across (80px, N's full width between the feet) and a magnitude
# on this entry in the same change.
#
# Honest caveat, kept because it is the reason this is a pin and not a
# work order: no fixture exhibits it (0 headless arrows in 174) and no
# cold observer has reported the misreading. It is the configuration where
# the closing argument fails, encoded so that the day someone draws one
# the harness has an opinion. Owner unassigned — Task 24 follow-up /
# addendum wave.
_register(Mutant(
    "headless_chain_reads_through_node",
    build=lambda: _attach_chain(shared=False, headless=True),
    op="unchanged", args={},
    expect=FindingSpec("phantom_passthrough", element="N"),
    neighbour=Neighbour(lambda: _attach_chain(shared=False),
                        Silence("phantom_passthrough"))))

# ---------------------------------------------------------------------------
# Shape-blindness, instance THREE — RED BY ABSENCE (found via the flowchartai
# idea-mine, 2026-08-12: docs/research/flowchartai_idea_mining_2026-08-12.md
# OP1). The endpoint lint measured to the bbox; the through-node test used the
# bbox; and `fit_label_in` budgets every container `width - 24` alike
# (canvas.py). canvas.py already knows better IN THE SAME FILE:
# `marker_inset` (canvas.py) returns 0.5 for a diamond and 1-1/sqrt(2)
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
# the chord at the label box's own height does. FIXED by WP4 (task 17) —
# `shape_band_width` reproduces all four of those numbers from
# `shape_clip`, so the ellipse is right here without an ellipse mutant to
# claim, and `_labelled_shape("ellipse")` is silent as the derivation says
# it should be.
#
# CONVERGENCE DECIDED (task 17): a SEPARATE `label_overflows_shape` check,
# NOT an arm of `text_overflow` — so no re-key, and this entry keeps both
# its check name and its `element="t1"`. Three reasons, the first
# dispositive. (1) The fold-in cannot flip this mutant. Give
# `text_overflow` a shape-aware `room_w` of 160 and its wrapped arm is
# STILL silent here, correctly: it fires on `longest > room_w` (the widest
# single word is 54px) or on the wrapped height (2 lines, 40px, against
# 96px of room), and neither is true. Flipping it from inside would mean
# rewriting the wrapped arm's semantics, which is what
# `wrapped_label_overflows_its_box` is green to prevent. (2) The magnitude
# would not survive: `_TEXT_OVERFLOW_RE` reports the needed WIDTH (171
# here), and the band this entry exists to hold is the OVERHANG (11, ±30%,
# excluding 0 and 5.5 on purpose). (3) They are different questions.
# `text_overflow` asks whether the renderer's wrapping fits the text in the
# owner's bounds, and on this scene it is right to be quiet — the text does
# wrap and the wrapped block does fit. This asks whether the label's DRAWN
# box lies inside the outline a reader can see.
_register(Mutant(
    "diamond_label_overflows_shape",
    build=lambda: _labelled_shape("diamond"),
    op="unchanged", args={},
    expect=FindingSpec("label_overflows_shape", element="t1",
                       magnitude=(11, 0.30)),
    # THE OTHER POLE, paid for at the flip (task 17). This was a
    # `Silence("endpoint_gap")` over a rectangle — liveness only, because
    # a Silence on a check with no detector passes vacuously (see
    # `phantom_passthrough_shared_attach`). Now the check exists, so the
    # Silence bites, and the control moved from the rectangle to a WIDER
    # DIAMOND on purpose: same label, same shape, 220px wide, where the
    # chord at the same band is 176px and the label clears by 5px. The
    # rectangle could not have caught a check that fired on every diamond
    # regardless of room; this does. The shape control is not lost —
    # `marker_inset` gates the check, so a rectangle is silent by
    # construction and `text_overflow` still owns it.
    neighbour=Neighbour(lambda: _labelled_shape("diamond", width=220),
                        Silence("label_overflows_shape"))))


def _wrapped_ink_in_a_wide_frame() -> list[dict]:
    """Narrow centred ink inside a wrapping frame wider than the body.

    The CLASS scene behind Task 17's F1 (v0.9 WP4, 2026-08-14): the
    check read `max(tw, box_w)` on the `autoResize is False` branch,
    where `box_w` is the frame the FITTER chose and not a measurement of
    any ink. On argus-r4-arm4 that reported an 11px overhang about a
    label with roughly 20px of real clearance on each side.

    The three numbers are picked so the two readings disagree, which is
    the only thing that makes this a test: the ink is 86px, the band at
    the label's own height is 186.3px, and the stored box is 200px. Ink
    clears the body comfortably; the frame does not fit inside it. So
    reading ink is silent and reading the frame reports about 14px of
    overhang that no reader could see.

    The label sits at the ellipse's shoulder rather than its middle
    deliberately — at the widest band the frame fits too, and the scene
    would be silent under either reading.

    Returns:
        The two-element scene: ellipse `e1` and its bound label `t1`.
    """
    owner = el(id="e1", type="ellipse", x=1600, y=400, width=250,
               height=120, customData={"role": "node"},
               boundElements=[{"id": "t1", "type": "text"}])
    label = el(id="t1", type="text", x=1625, y=420, width=200, height=20,
               text="to compose", originalText="to compose", fontSize=16,
               containerId="e1", textAlign="center", autoResize=False)
    return [owner, label]


# Frame membership asserted against a picture that contradicts it — RED BY
# ABSENCE (flowchartai mine M2, 2026-08-12). `frameId` is a claim of
# containment, and nothing in `lint_layout` ever tests it. Three of the
# frameId sites there serve help-slot lookup, same-frame pairing and the
# unconnected-node note; a fourth, the dot-row/progress tell
# (canvas.py), IS geometric — it groups a frame's small members and
# compares their y against `fr.y + height * 0.25` — but it is hunting for a
# row of dots, never asking whether a member lies inside the frame at all. No
# site tests containment. Verified live — this scene and its control
# produce IDENTICAL lint and findings, so the tooling cannot tell a lane
# with its members in it from a lane whose member is 80px below it.
#
# The producer is confirmed too: `--relayout`'s move set is
# rect/diamond/ellipse (canvas.py) and never frames, so re-laying a
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
# (canvas.py) and the text/node overlap check gates on
# role_of(e) == "annotation" (canvas.py), so the same text over the
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
# FLIPPED by v0.9 WP4 (Task 23) — the loop is role-blind now: it walks every
# free text, and the role selects the SENTENCE rather than whether anything
# is measured. The flip paid its debt in the same change: the old neighbour
# was `Silence("endpoint_gap")` over an arrowless scene, which proved
# liveness and nothing else, and `annotation_overlaps_node` — the check that
# control was actually exercising — had no DETECTORS entry to assert against.
# It has one now, drained from UNCOVERED here, and the neighbour asserts the
# roled overlap FIRING, with the same element and the same 2400px² the mutant
# asserts of the unroled one.
#
# That is the whole point of this pair: two scenes, identical geometry,
# identical magnitude, identical named element, and the role is the single
# bit that differs. A check that fired on every text/node pair would satisfy
# the mutant and CONTRADICT the neighbour (it would speak in the wrong voice
# on the roled scene); one that still gated on role would satisfy the
# neighbour and fail the mutant. Neither pole can be passed by accident.
_register(Mutant(
    "unroled_text_over_node",
    build=lambda: _text_over_node(roled=False),
    op="unchanged", args={},
    expect=FindingSpec("text_overlaps_node", element="t1",
                       magnitude=(2400, 0.10)),
    neighbour=Neighbour(lambda: _text_over_node(roled=True),
                        FindingSpec("annotation_overlaps_node", element="t1",
                                    magnitude=(2400, 0.10)))))

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
#
# FLIPPED by v0.9 WP4 (Task 23). The check shipped INSIDE the shape pair
# loop, so it inherits that loop's nesting exemptions and `shapes`' role
# filter for free, and the waiver arm shipped with it rather than after it —
# all three channels the paragraph above demanded, on day one. The neighbour
# is no longer the arrowless `Silence("endpoint_gap")` liveness borrow: it is
# `Silence("min_clearance")` over the SAME builder at 60px, the pole that
# discriminates. A check that simply fired on every non-overlapping pair
# would satisfy this mutant and fail that control.
_register(Mutant(
    "near_miss_clearance",
    build=lambda: _near_miss_pair(gap=4),
    op="unchanged", args={},
    expect=FindingSpec("min_clearance", element="n2", magnitude=(4, 0.33)),
    neighbour=Neighbour(lambda: _near_miss_pair(gap=60),
                        Silence("min_clearance"))))

# Shape-blindness, instance SIX — the newest check inherits the oldest bug
# (curator batch 19, from the Task 23 report §8 item 3, 2026-08-14). The
# check landed on 2026-08-14 and was shape-blind the same day: it reads
# `ox`/`oy` off stored bounding boxes, so it says "only 4px apart" about a
# rhombus pair whose drawn facets never come within 84px of each other,
# and it says it in the same words it uses for two boxes that really are
# touching. This is the OVER-FIRE pole of `near_miss_clearance` directly
# above — that entry proved the check speaks, this one asks whether it
# speaks about the picture — and it is the crowding half of the family
# whose overlap half `TestShapeBlindAnnotationOverlap` holds.
#
# It gets its own entry rather than a rider on the diamond endpoint
# mutants for the reason those got one from the ellipse: WP4's shape
# clipping went into `_seg_hits_rect` and `marker_inset`'s callers, and
# `lint_layout`'s pair loop calls neither. A fix that taught every ARROW
# check about rhombus edges and stopped could go green with node-to-node
# spacing exactly as blind as it is today.
#
# No magnitude to assert, because the correct reading is nothing at all:
# 84px of clear ground is a layout, not a slip, and the pole is silence.
# The neighbour carries the number. It is the SAME builder with `kind`
# switched to rectangle — boxes that fill their boxes, where 4px stored
# really is 4px drawn — so the pair is a single-variable experiment on
# the shape term, and a fix that suppressed the check wholesale satisfies
# this mutant and fails that control.
_register(Mutant(
    "diamond_clearance_overfire",
    build=lambda: _clearance_pair_shaped("diamond"),
    op="unchanged", args={},
    expect=Silence("min_clearance"),
    neighbour=Neighbour(lambda: _clearance_pair_shaped("rectangle"),
                        FindingSpec("min_clearance", element="n2",
                                    magnitude=(4, 0.33)))))

# The ELLIPSE arm of the entry directly above (v0.9 Task 56, owed by both
# shape spikes). One argument of the same builder, which is the whole
# point: the rhombus and the conic are different geometry reached through
# the same `shape_clip`, and a fix that special-cased `diamond` — or a
# regression that later did — turns exactly one of the two red while the
# other stays green and looks like cover. Staggered by the same 80, the
# two circles' boxes still clear by 4px while their outlines clear by
# 44.0px along the facing axis.
#
# Its neighbour is the SAME rectangle control the diamond entry uses, and
# that reuse is deliberate rather than lazy: the control's job is to prove
# the check still speaks about boxes that really are 4px apart, and there
# is one such fact, not two. The dedupe guard separates the two entries on
# their base scenes, which differ.
_register(Mutant(
    "ellipse_clearance_overfire",
    build=lambda: _clearance_pair_shaped("ellipse"),
    op="unchanged", args={},
    expect=Silence("min_clearance"),
    neighbour=Neighbour(lambda: _clearance_pair_shaped("rectangle"),
                        FindingSpec("min_clearance", element="n2",
                                    magnitude=(4, 0.33)))))

# The OTHER direction of the same fix, and the one nothing watched before
# (v0.9 Task 56, decision D4). Two rhombi offset (56,48): their BOXES
# overlap by 44x52, so the pair never reached the crowding arm at all —
# the overlap arm owns any pair whose boxes intersect, and 44*52 falls
# under its quarter-area bar, so the lint said nothing. Their drawn
# facets are 4px apart, which is the exact configuration that arm exists
# to report.
#
# This is the only place the shape fix makes the lint SPEAK where it was
# silent, which is why it is pinned on its own rather than folded into a
# neighbour: everything else in Task 56 narrows, and a narrowing fix that
# quietly lost this arm would leave every other pin green.
_register(Mutant(
    "boxed_overlap_hides_a_near_miss",
    build=lambda: _crowded_pair_shaped(56, 48),
    op="unchanged", args={},
    expect=FindingSpec("min_clearance", element="n2", magnitude=(4, 0.33)),
    neighbour=Neighbour(lambda: _crowded_pair_shaped(160, 160),
                        Silence("min_clearance"))))

# Both ORIENTATIONS of the crowding arm, pinned as a GATE rather than as
# two poles (v0.9 Task 56, named by the ellipse spike as the trap worth
# the entry). The arm picks the axis it reports across, and picking the
# wrong one is SILENT — it does not fire wrongly, it simply fails to
# correct anything, so a reversal hides behind every other pin here. The
# mutant is two rhombi stacked 4px apart and the neighbour is the same
# builder side by side, and BOTH assert 4px: neither pole can be
# satisfied by suppressing the check, and neither can be satisfied by an
# implementation that only understands one axis.
_register(Mutant(
    "stacked_diamonds_near_miss",
    build=lambda: _crowded_pair_shaped(0, 104),
    op="unchanged", args={},
    expect=FindingSpec("min_clearance", element="n2", magnitude=(4, 0.33)),
    neighbour=Neighbour(lambda: _crowded_pair_shaped(104, 0),
                        FindingSpec("min_clearance", element="n2",
                                    magnitude=(4, 0.33)))))

# The DIAGONAL stagger, which the two entries above cannot reach and
# which nothing in this file could see until the Task 56 review measured
# it (fix round 1, F1). When two outlines clear on BOTH axes at once, the
# arm has to pick which separation to report, and picking the farther one
# is silent — it does not speak wrongly, it just says nothing.
#
# It takes an ELLIPSE to observe, and that is the finding rather than a
# detail of it. A rhombus is an L1 ball, so its two axis gaps are the
# same number and every rhombus pin reads identically whichever axis the
# arm picks; all three entries above are rhombus pairs, and all three
# stayed green through a form that missed 96 of 294 conic near-misses on
# a 2px sweep — every one of the 96 a true gap inside the floor, measured
# against brute-force outline distance.
#
# Here the boxes overlap 32x20 while the drawn outlines clear by 6.68px
# on the axis the pair actually faces across, and by 8.00px on the axis
# it does not. Reporting the second is the whole defect.
_register(Mutant(
    "diagonal_ellipses_near_miss",
    build=lambda: _crowded_pair_shaped(68, 80, kind="ellipse"),
    op="unchanged", args={},
    expect=FindingSpec("min_clearance", element="n2", magnitude=(6, 0.2)),
    neighbour=Neighbour(lambda: _crowded_pair_shaped(68, 88, kind="ellipse"),
                        Silence("min_clearance"))))

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
# helper as the diamond cases: `marker_inset` (canvas.py) already returns
# 1-1/sqrt(2) for an ellipse, on the stated grounds that "the box's corner is
# empty canvas for them", and `_seg_hits_rect` (canvas.py) never asks it
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
# number (the through-node crossing walk in `lint_layout`), so what this
# pins is the POLE
# — silence — and the neighbour carries the firing proof. The corner arrow's
# 27.3px of clear
# canvas is derived in `_ellipse_stage`. FIXED by WP4 (task 16) in the same
# edit as its rhombus sibling — the scope this entry was written to force
# held, and cost nothing extra: `shape_clip` knows all three shapes.
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
# `lint_layout`'s `label_boxes` (canvas.py) measures a label by its stored
# `width`. That is safe only while every write path recomputes it, and three
# things say the
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
# coverage on `text_overflow` (canvas.py) was zero: the check has shipped
# since v0.4, has been enumerated as unproven since 2026-08-12, and nothing
# had ever asserted that it fires, on either arm, with any number.
#
# Two mutants rather than one because the check has TWO code paths, not two
# messages. `single_line` (canvas.py) is true only for composed rows
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
# owner's bbox on every container type (canvas.py), so a diamond or
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
# the producer side (`fit_label_in`, canvas.py), and what it adds is the
# NAME OF THE SHIPPED CHECK that should host the fix.
#
# RE-KEY OBLIGATION: DISCHARGED, and it did not arise. WP4 (task 17) put the
# shape term in a SEPARATE `label_overflows_shape` check rather than inside
# this one, so `diamond_label_overflows_shape` keeps its own key and its own
# `element="t1"`, and the silent-rot path this paragraph was written to guard
# is closed rather than merely watched. The reasoning is recorded at that
# entry; the dispositive half is that a shape-aware `room_w` would NOT have
# flipped it — feed this check a 160px `room_w` on that scene and the wrapped
# arm is still silent, because the widest single word is 54px and the wrapped
# block is 40px tall against 96px of room. Reaching the mutant from in here
# would have meant rewriting the wrapped arm, which is what
# `wrapped_label_overflows_its_box` is green to prevent.
#
# WHAT STILL BINDS ANYONE WHO REVISITS THAT CALL: the two checks report
# different elements on purpose. This template names the OWNER, because the
# text is quoted as content and carries no id; the shape check names the
# LABEL, because the label is the thing drawn in the wrong place and its
# message names both. A later fold-in that moved the check and left the
# element pointing at the label would assert a finding this template cannot
# emit, and would go red for a reason unrelated to the shape term.
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

# RED BY ABSENCE, and the direction is what indicts it: the mutation makes
# the drawing WORSE and the lint goes from a loud ERROR to nothing at all.
# Found during curator batch 20, 2026-08-14, from task 24's curator
# candidate 1 (the reviewer's queue endorsed it).
#
# `lint_layout` measures the interior run only `if not outside:`, and the
# rectangle branch computes `outside` as a raw bbox distance. So ANY
# positive gap — including one well inside `tol` — switches the walk off
# entirely. Move this arrow's tail 3px from N's border to 3px PAST it and
# it now crosses the whole node and pokes out the far side, while `outside`
# becomes 3.0, the walk never runs, and `max(outside, inside) > tol` is
# false at 3 < 14. Verified live across all three lint channels: the base
# scene emits one error, the mutated scene emits nothing, in errors,
# warnings AND notes. The silent band is 3px..14px wide; at 15px the only
# voice that returns is `endpoint_gap` saying "ends 15px away" about an
# arrow that traverses 100px of the node — the anti-correlated severity
# the endpoint comment at canvas.py already names.
#
# WHAT e15 DOES AND DOES NOT CLOSE. Task 24's degenerate-arrow overshoot
# arm reads `seq[-2:]` against the END binding's target only. The same
# geometry at the END pole IS caught ("its head sits 3px past N's far
# outline, having crossed the whole node"); at the START pole, which is
# this mutant, e15 is structurally unable to look. That is the general
# silence the candidate named, minimized to its one reachable instance.
#
# ONE CORRECTION TO THE ORIGINAL REPORT, measured rather than assumed: it
# predicted a diamond would be silent too. It is not. The diamond/ellipse
# branch takes `(outside, inside) = (0, 0)` unless `abs(clear) > tol`, so a
# 3px gap on a diamond reaches the walk and fires. The tolerance floor the
# rectangle branch lacks is the whole defect, and pinning it on a diamond
# would have pinned a scene that already works.
#
# MAGNITUDE: 98px, the interior run — confirmed by simulating the fix
# (zeroing `outside` at or below `tol` in the rectangle branch), which
# yields the identical finding the base scene already emits. The +/-10%
# band excludes the readings that would mean the fix measured the wrong
# thing: 3 (the gap, i.e. `endpoint_gap`'s answer), 80 (N's height) and
# 203 (the arrow's own length). It deliberately ADMITS 100 (un-inset) and
# 103 (tail to far edge): all three are honest readings of "how much of N
# this arrow crosses", and the 1px inset is a fan-attach detail no fix
# should be failed over.
#
# THE NEIGHBOUR IS THE UNGATED POLE, not a Silence, and it is what makes
# the `crosses_through_bound` drain honest. The poles of a GATE are gated
# and ungated (the `unroled_text_over_node` precedent above), so the pair
# is: same builder, same node, same 98px of interior run, same named
# element, and 3px of tail position is the single bit that differs. The
# red proves the check goes quiet; the NEIGHBOUR — ungated, asserted in
# every commit — proves it fires with the right magnitude. A check that
# stayed silent on both would fail the neighbour, and one that fired on
# every bound arrow would satisfy neither pole's magnitude.
_register(Mutant(
    "tolerable_gap_hides_interior_run",
    build=_crossing_tail,
    op="move_endpoint_to",
    args={"arrow_id": "e1", "end": "start", "x": 197, "y": 140},
    expect=FindingSpec("crosses_through_bound", element="e1",
                       magnitude=(98, 0.10), direction=None),
    neighbour=Neighbour(_crossing_tail,
                        FindingSpec("crosses_through_bound", element="e1",
                                    magnitude=(98, 0.10), direction=None))))


# ---------------------------------------------------------------------------
# The curvature guard pins (v0.9 WP4 stage 3, 2026-08-15). Every entry
# below has the SAME single bit between its two poles — `roundness`, or
# where a foreign box sits — so none of them can pass because some
# unrelated tolerance moved. They exist because `roundness` is invisible
# to `content_fingerprint` AND to `DEFAULT_SIGNIFICANT_ATTRS` by design
# (it is derived, and making it significant re-opens the permanent
# disk/history divergence v0.8 WP2 closed), so a curvature regression
# mints no diff and no reconciliation. These are the instruments that
# would notice, and there is nothing else.
# ---------------------------------------------------------------------------

# Blind spot 1, Pin A. Needs no curvature at all: this is the model bug
# that was live in the SHARP build, on 37 of 107 corpus bound labels,
# worst 331px. The poles are the two models' answers for the same label.
_register(Mutant(
    "label_anchor_reads_the_arc_not_the_client",
    build=lambda: _labelled_elbow(505, 85),
    op="unchanged", args={},
    expect=FindingSpec("label_on_foreign_node", element="foreign"),
    neighbour=Neighbour(lambda: _labelled_elbow(290, 70),
                        Silence("label_on_foreign_node"))))

# Blind spot 1, Pin B: the only pin that separates the client's chord
# branch from its Bezier one, and it exists only in the curved world.
_register(Mutant(
    "curved_even_path_moves_the_label",
    build=lambda: _labelled_skew_z(True),
    op="unchanged", args={},
    expect=FindingSpec("label_on_foreign_node", element="foreign"),
    neighbour=Neighbour(lambda: _labelled_skew_z(False),
                        Silence("label_on_foreign_node"))))

# Blind spot 3, pole 1 — FALSE SILENCE. The stored chord measures 27.4px
# of interior run, one hair under the 28px gate, and says nothing; the
# curve the browser draws measures 36.2px and is over it. The sharp twin
# at the same points has no curve to hide behind and is a genuine
# negative, which is what makes it the pole rather than a coincidence.
_register(Mutant(
    "curved_final_run_hides_interior_crossing",
    build=lambda: _two_leg_approach(_RUN_HIDDEN, True),
    op="unchanged", args={},
    expect=FindingSpec("crosses_through_bound", element="e1",
                       magnitude=(36, 0.10)),
    neighbour=Neighbour(lambda: _two_leg_approach(_RUN_HIDDEN, False),
                        Silence("crosses_through_bound"))))

# Blind spot 3, pole 2 — FALSE ERROR, the other direction. The chord
# measures 35.3px and raises an error against a drawing that reads
# clean; the drawn curve only dips 25.6px in. The neighbour is the SHARP
# scene FIRING, so this pair pins a correction rather than a mere
# sensitivity drop: a check that went quiet on everything would fail it.
_register(Mutant(
    "curved_final_run_overstates_interior_crossing",
    build=lambda: _two_leg_approach(_RUN_OVERSTATED, True),
    op="unchanged", args={},
    expect=Silence("crosses_through_bound"),
    neighbour=Neighbour(lambda: _two_leg_approach(_RUN_OVERSTATED, False),
                        FindingSpec("crosses_through_bound", element="e1",
                                    magnitude=(35, 0.10)))))

# Blind spot 2, crossings. Zero chord crossings, two drawn ones.
_register(Mutant(
    "curved_corner_crossing_miss",
    build=lambda: _corner_elbow_pair(True),
    op="unchanged", args={},
    expect=FindingSpec("crossings_count", magnitude=(2, 0.0)),
    neighbour=Neighbour(lambda: _corner_elbow_pair(False),
                        FindingSpec("crossings_count", magnitude=(0, 0.0)))))

# Blind spot 2, corridors — the pin on the FIX, not on the defect. See
# `_fan_finals`: registering this against the naive segment swap would
# have failed, which is the whole reason it was designed before the fix.
_register(Mutant(
    "curved_parallel_finals_corridor_miss",
    build=lambda: _fan_finals(6),
    op="unchanged", args={},
    expect=FindingSpec("shared_corridor", element="fa+fb",
                       magnitude=(200, 0.05)),
    neighbour=Neighbour(lambda: _fan_finals(40),
                        Silence("shared_corridor"))))

# ---------------------------------------------------------------------------
# The migrated-reader pins (curator batch 22, 2026-08-15). The curves
# fold-in moved four readers of "what shape is this arrow" onto the drawn
# path and its own fix round found a fifth; three of the four were pinned
# as they landed and two were not, which is the gap these close. All three
# are GREEN — they guard a fix that shipped, and a regression to the stored
# chord is what turns them red. Origin: fold-curves-review.md F3 (the feet
# block) and fold-curves-report.md's F3 section (the through-node arm).
# ---------------------------------------------------------------------------

# Reader four: `phantom_passthrough`'s feet. The stored chord calls both
# feet VERTICAL — each arrow's last stored leg is an 8px jog — gives them
# the same sign and never pairs them; `_arrival_path`'s secant reads the
# 300px body each jog hangs off, and the pair is found. Measured on this
# scene: the two readings of e1's foot differ by 61.4 degrees, against a
# gate of 14.
#
# MAGNITUDE: 80px, all of N, and the ±10% band is what makes this
# discriminate. The three other numbers reachable here are excluded by it —
# 0 (the bare span between the feet, which the same sentence prints), 40
# (N's HEIGHT, which is what the block reports if it pairs these feet on
# the VERTICAL axis the stored chord names, and the reading a half-migration
# produces), and 448 (the merged stroke's own length, the corridor's answer
# about the same picture). A regression to the stored chord scores none of
# them: it scores silence.
#
# THE RESIDUAL, recorded because the scene shows it and nothing else says
# it. The SHARP twin of this mutant is silent too, and that silence is a
# miss rather than a correction: sharp, these points draw one line at y=112
# from A through N to Z with a hairpin at x=200, and it is as merged as the
# curved version. An 8px perpendicular jog at the attach point defeats this
# block's axis classification in BOTH worlds, because the classification
# reads the last leg and the jog IS the last leg. That is not curvature's
# fault and is not this pin's to fix — it belongs to the feet block's owner,
# alongside the older question of whether the sign test should read the
# stroke's body rather than its final secant at all. The neighbour is the
# sharp FANNED chain precisely so this pin never asserts that miss is
# health.
_register(Mutant(
    "curved_foot_axis_misreads_phantom_passthrough",
    build=lambda: _stub_chain(rounded=True, shared=True),
    op="unchanged", args={},
    expect=FindingSpec("phantom_passthrough", element="N",
                       magnitude=(80, 0.10)),
    neighbour=Neighbour(lambda: _stub_chain(rounded=False, shared=False),
                        Silence("phantom_passthrough"))))

# Reader five, pole 1 — FALSE SILENCE. The stored L clears F by 8px and
# says nothing; the drawn corner has already climbed to y=81..87 across F's
# span and crosses it plainly. Four sampled path points sit inside the box.
# The sharp twin is a genuine negative — the chords really do miss — which
# is what makes it the pole rather than a coincidence.
_register(Mutant(
    "curved_bow_hides_through_node_crossing",
    build=lambda: _bow_past_box(rounded=True, under=False),
    op="unchanged", args={},
    expect=FindingSpec("passes_through_foreign", element="e1"),
    neighbour=Neighbour(lambda: _bow_past_box(rounded=False, under=False),
                        Silence("passes_through_foreign"))))

# Reader five, pole 2 — FALSE ERROR, the other direction, and the reason
# this arm needed two pins rather than one. The stored chord runs straight
# through F at y=100 and reports a crossing; the drawn curve has bowed
# 14px clear of it by the time it reaches F's span and touches nothing.
# The neighbour is the SHARP scene FIRING, so the pair pins a CORRECTION:
# a check that had simply gone quiet on curved arrows would satisfy the
# mutant and fail the neighbour. No magnitude — this message names a box,
# not a number.
_register(Mutant(
    "curved_bow_overstates_through_node_crossing",
    build=lambda: _bow_past_box(rounded=True, under=True),
    op="unchanged", args={},
    expect=Silence("passes_through_foreign"),
    neighbour=Neighbour(lambda: _bow_past_box(rounded=False, under=True),
                        FindingSpec("passes_through_foreign",
                                    element="e1"))))

# WAS RED BY ABSENCE — F8, the curved corridor population the shipped pin
# did not reach (curves-fold review §7, curated 2026-08-15). FLIPPED GREEN
# by v0.9 task 51. `_reads_as_line` scaled its band with the span it landed
# on while the bow that span carries is set by the leg BEFORE it, so a short
# final after a long approach was rejected by stage 2 and vanished from
# `shared_corridors` entirely. The review measured 75 of 224
# axis-aligned-chord stretches rejected in the all-curved corpus, 10% under
# the shipped gate; this is one of them, minimized.
#
# THE FIX WAS NOT A WIDER BAND, which is what this entry's own flip
# verification had already ruled out. Stage 2 left `shared_corridors`
# altogether: a straightness gate can only suppress a chord reading it
# shares, and over the 24 frozen artifacts its whole measured effect was to
# make the corridor count depend on whether the corners were rounded (5 as
# stored, 5 all-curved without it, 1 all-curved with it — roundness moves no
# endpoint). `_reads_as_line` itself is untouched and `false_bidi` still
# gates on it, which is why none of the six things the naive widen-the-
# constant fix broke moved this time. See `_stretch_axis`.
#
# WHAT MAKES IT A DEFECT AND NOT A SENSITIVITY DROP: the two finals are
# 15.0px apart sharp and 15.4px apart drawn, sampled at their midpoint. The
# drawing does not change. A reader sees one thick stroke either way and
# the check reports it once and then stops.
#
# MAGNITUDE 80px, the finals' shared extent, and the band excludes the two
# readings a wrong fix gives: 300 and 185 (the APPROACH extents, which is
# what a fix that relaxed the band far enough to admit everything would
# pair instead) and 15 (the lateral separation, which is the tolerance and
# not the overlap).
#
# THE NEIGHBOUR IS THE SHARP SCENE FIRING, not a Silence, for the reason
# `tolerable_gap_hides_interior_run` states about gates: the red says the
# check goes quiet, the green says what it must say when it speaks, and a
# check that had gone quiet on both poles satisfies neither. It is what
# makes the flip mean something too: both poles now report the same 80px
# corridor, which is the entry's claim that the drawing did not change.
_register(Mutant(
    "curved_short_finals_escape_the_corridor",
    build=lambda: _short_finals(rounded=True),
    op="unchanged", args={},
    expect=FindingSpec("shared_corridor", element="fa+fb",
                       magnitude=(80, 0.10)),
    neighbour=Neighbour(lambda: _short_finals(rounded=False),
                        FindingSpec("shared_corridor", element="fa+fb",
                                    magnitude=(80, 0.10)))))

# WAS RED BY ABSENCE — shape-blindness in the WRITER, and the oldest open
# one in the repo when it was written (curator batch 22, 2026-08-15, from
# Task 56 §8.4 and §8.5). FLIPPED GREEN by v0.9 task 51, which took both
# halves the entry names: `_fan_point` now pulls each slot onto the drawn
# outline along the ray from the node's centre, and `float_diamond` reads
# ellipses through a `_dist_to_ellipse` derived here rather than borrowed
# from canvas.py. THE MUTANT SCENE IS UNCHANGED and still hand-places the
# foot at (200, 175), which is what keeps it a pin on the READER after the
# writer stopped producing that point — the geometry it asserts about is
# now unreachable from the fan, and that is the writer's own regression
# test's job (`TestFanAttachPoints`), not this one's.
#
# `_fan_point` spread attach points along the BOUNDING BOX side, so on an
# ellipse the fanned feet stopped in the corner void. Nothing reported it:
# `endpoint_gap` measures the miss but `endpoint_tol` allows
# `0.10 * short side` = 30px here, and `float_diamond` — the independent
# instrument whose whole reason for existing is that it does NOT share
# canvas.py's geometry, and which caught `edge_anchor` on the bounding box
# months before the lint agreed — filters `n["type"] != "diamond"` and
# never looks at an ellipse.
#
# THE SILENCE IS STRUCTURAL, NOT A COINCIDENCE OF THIS SIZE. For a circle
# of radius r the 1/4 fan slot misses the outline by `r(sqrt(1.25) - 1)` =
# 0.118r, and the tolerance is `max(14, 0.2r)`. 0.118r is under both terms
# at every r, so no circle anywhere is loud enough to trip the endpoint
# lint from its own fan. Measured across the sweep that established it,
# as the true perpendicular distance: 160x80 -> 7.68px against a 14px
# tolerance, 200x200 -> 11.80 against 20, 240x160 -> 12.77 against 16,
# 300x300 -> 17.71 against 30.
#
# THE DIAMOND IS NOT THE BUG, which corrects Task 56 §8.5's own guess. The
# same fan on a rhombus lands 17.9-53.0px out and fires BOTH the endpoint
# lint and `float_diamond` at every size tested — which is exactly why the
# neighbour is the diamond: it proves the instrument is alive, measuring,
# and stopped only by its type filter. The ellipse is the shape with no
# reader at all.
#
# MAGNITUDE 17.71px, the TRUE perpendicular distance from the drawn
# ellipse — verified against a 400k-sample scan of the outline, because
# this is the one number in the entry that no shipped function computes.
# The ±10% band excludes 53.03 (the rhombus's answer for the identical
# point, i.e. a fix that added ellipses by pointing them at
# `_dist_to_diamond` unchanged), 75.0 (the raw bbox reading, which is
# what a fix that never left the bounding box would report) and 0 (the
# rectangle's). It deliberately ADMITS 16.77, which is what
# `canvas.shape_clearance` returns here: that function is first-order in
# the gradient and its own docstring calls the ellipse arm a slight
# under-estimate that errs toward silence, so a fix that borrows it is
# honest and must not fail over 0.94px.
#
# THE TWO CHANGES WERE SEPARABLE and both landed together, which is what
# the entry demanded: `_fan_point` on the drawn outline removes the
# geometry, the ellipse arm gives it a reader, and either alone would have
# left the other half open. The reader is the half this pin holds.
_register(Mutant(
    "fanned_ellipse_foot_floats_in_the_void",
    build=lambda: _fanned_void_foot("ellipse"),
    op="unchanged", args={},
    expect=FindingSpec("float_diamond", element="a1",
                       magnitude=(17.71, 0.10)),
    neighbour=Neighbour(lambda: _fanned_void_foot("diamond"),
                        FindingSpec("float_diamond", element="a1",
                                    magnitude=(53.03, 0.10)))))

# ---------------------------------------------------------------------------
# MEASUREMENTS THE CURVATURE PINS DEPEND ON, recorded here because they were
# made in a review that is not code and would otherwise have to be re-run by
# whoever next moves one of these numbers (curator batch 22, 2026-08-15, from
# fold-curves-review.md's re-review).
#
# `NEAR_AXIS` = 0.25 (~14 degrees) SITS ON A PLATEAU, which is a better
# defence of it than "it already existed" — the value reads as one nobody
# chose, and the measurement says the corpus is genuinely insensitive across
# it. Arrows of the 38 eligible that the gate lets curve, by bar:
#
#     0.10 (5.7 deg)  ->  0        0.35 (19.3) -> 24
#     0.18 (10.2)     -> 14        0.50 (26.6) -> 27
#     0.25 (14.0)     -> 23  <- shipped
#
# 0.25 -> 0.35 buys ONE arrow. Anyone retuning it should also know the
# constant has two readers and the coupling is measured, not hypothetical:
# `lint_layout`'s feet block classifies with the same number, so widening it
# to 0.50 adds six "read as one stroke" warnings to `tearsheet-pipeline` —
# six that appear in an ALL-SHARP control too, so they are the
# classification widening and not a curve the gate let through. That is the
# coupling `curved_foot_axis_misreads_phantom_passthrough` above sits on
# top of: its 61.4-degree divergence is against THIS bar.
#
# TWO PROPERTIES OF THE GATE'S SECOND ARM that its docstring understates,
# and that a pin over gated output would otherwise look wrong against:
#
#   - It compares finding STRINGS, magnitudes included. A curve that only
#     changes a pixel count inside an existing finding ("runs 35px inside")
#     counts as the set having moved and the candidate is declined. That is
#     stricter than set membership and is defensible — the finding DID
#     change — but "the finding set must not move" describes something
#     weaker than what runs.
#   - Its attribution is order-arbitrary. Two arrows that individually keep
#     the set clean but jointly move it see the second in sorted-`id` order
#     declined. Deterministic and correct in effect, but not minimal: a
#     different order would curve a different one of the pair. The gate's
#     determinism paragraph should not be read as saying the verdict set is
#     canonical, only that it is reproducible.
# ---------------------------------------------------------------------------


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

    def test_mutant_label_anchor_reads_the_arc_not_the_client(self) -> None:
        """The label is drawn on the corner, inside a box 150px away."""
        self._run("label_anchor_reads_the_arc_not_the_client")

    def test_neighbour_label_anchor_reads_the_arc_not_the_client(
            self) -> None:
        """A box over the arc-length model's answer is not where it is."""
        self._run_neighbour("label_anchor_reads_the_arc_not_the_client")

    def test_mutant_curved_even_path_moves_the_label(self) -> None:
        """Curvature moves an even-point label 23.8px, into a box."""
        self._run("curved_even_path_moves_the_label")

    def test_neighbour_curved_even_path_moves_the_label(self) -> None:
        """The same Z sharp centres on the chord and clears that box."""
        self._run_neighbour("curved_even_path_moves_the_label")

    def test_mutant_curved_final_run_hides_interior_crossing(self) -> None:
        """The drawn curve runs 36px inside where the chord read 27.4."""
        self._run("curved_final_run_hides_interior_crossing")

    def test_neighbour_curved_final_run_hides_interior_crossing(
            self) -> None:
        """The sharp twin at the same points is a genuine negative."""
        self._run_neighbour("curved_final_run_hides_interior_crossing")

    def test_mutant_curved_final_run_overstates_interior_crossing(
            self) -> None:
        """The drawn curve dips 25.6px in, under the gate — no error."""
        self._run("curved_final_run_overstates_interior_crossing")

    def test_neighbour_curved_final_run_overstates_interior_crossing(
            self) -> None:
        """The sharp scene fires at 35px, so the check is alive."""
        self._run_neighbour("curved_final_run_overstates_interior_crossing")

    def test_mutant_curved_corner_crossing_miss(self) -> None:
        """Two elbows whose chords miss cross twice once drawn."""
        self._run("curved_corner_crossing_miss")

    def test_neighbour_curved_corner_crossing_miss(self) -> None:
        """The sharp pair genuinely does not cross."""
        self._run_neighbour("curved_corner_crossing_miss")

    def test_mutant_curved_parallel_finals_corridor_miss(self) -> None:
        """Two curved verticals 6px apart still read as one stroke."""
        self._run("curved_parallel_finals_corridor_miss")

    def test_neighbour_curved_parallel_finals_corridor_miss(self) -> None:
        """At 40px apart they are two strokes and stay silent."""
        self._run_neighbour("curved_parallel_finals_corridor_miss")

    def test_mutant_curved_foot_axis_misreads_phantom_passthrough(
            self) -> None:
        """Two 8px jogs hide a merged stroke from the stored chord."""
        self._run("curved_foot_axis_misreads_phantom_passthrough")

    def test_neighbour_curved_foot_axis_misreads_phantom_passthrough(
            self) -> None:
        """Fanned onto opposite borders, the sharp chain says nothing."""
        self._run_neighbour("curved_foot_axis_misreads_phantom_passthrough")

    def test_mutant_curved_bow_hides_through_node_crossing(self) -> None:
        """The bow crosses a box its own stored L clears by 8px."""
        self._run("curved_bow_hides_through_node_crossing")

    def test_neighbour_curved_bow_hides_through_node_crossing(self) -> None:
        """The sharp L genuinely misses that box."""
        self._run_neighbour("curved_bow_hides_through_node_crossing")

    def test_mutant_curved_bow_overstates_through_node_crossing(
            self) -> None:
        """The drawn curve clears a box the stored chord runs through."""
        self._run("curved_bow_overstates_through_node_crossing")

    def test_neighbour_curved_bow_overstates_through_node_crossing(
            self) -> None:
        """Sharp, that chord IS the drawing, and the crossing is real."""
        self._run_neighbour("curved_bow_overstates_through_node_crossing")

    def test_mutant_curved_short_finals_escape_the_corridor(self) -> None:
        """Two strokes 15px apart stay a corridor once curved (task 51)."""
        self._run("curved_short_finals_escape_the_corridor")

    def test_neighbour_curved_short_finals_escape_the_corridor(self) -> None:
        """Sharp, the same two finals are reported at 80px of overlap."""
        self._run_neighbour("curved_short_finals_escape_the_corridor")

    def test_mutant_fanned_ellipse_foot_floats_in_the_void(self) -> None:
        """A foot 17.7px off a circle is reported now (task 51)."""
        self._run("fanned_ellipse_foot_floats_in_the_void")

    def test_neighbour_fanned_ellipse_foot_floats_in_the_void(self) -> None:
        """The identical point on a rhombus is reported at 53.0px."""
        self._run_neighbour("fanned_ellipse_foot_floats_in_the_void")

    def test_mutant_diamond_corner_silence(self) -> None:
        """80px clear of the rhombus, inside its bbox, is now reported."""
        # Was measured against the bbox. FLIPPED by WP4 (task 15):
        # `endpoint_gap` clips the approach axis to the drawn outline.
        self._run("diamond_corner_silence")

    def test_neighbour_diamond_corner_silence(self) -> None:
        """Fanned rectangle attachments stay endpoint-silent."""
        # Same control and same expectation as the other two diamond
        # mutants, deliberately — see `_run_neighbour`.
        self._run_neighbour("diamond_corner_silence")

    def test_mutant_diamond_wrong_direction(self) -> None:
        """50px outside the rhombus now reads outside, not 15px inside."""
        # Was measured against the bbox. FLIPPED by WP4 (task 15): the
        # sign comes off the clip, so the direction cannot invert.
        self._run("diamond_wrong_direction")

    def test_neighbour_diamond_wrong_direction(self) -> None:
        """Fanned rectangle attachments stay endpoint-silent."""
        # Same control and same expectation as the other two diamond
        # mutants, deliberately — see `_run_neighbour`.
        self._run_neighbour("diamond_wrong_direction")

    def test_mutant_diamond_label_overflows_shape(self) -> None:
        """A label inside its w-24 budget overhangs the rhombus by 11px."""
        # Was invisible to every check we owned. FLIPPED by WP4 (task
        # 17): `label_overflows_shape` measures the label's drawn box
        # against `shape_band_width` at the label's own y-band, and
        # `fit_label_in` budgets against the same number.
        self._run("diamond_label_overflows_shape")

    def test_the_overflow_check_measures_ink_and_not_the_frame(
            self) -> None:
        """A check's number has to be about the picture, not about a box.

        The CLASS pin for Task 17's F1, from third hands. The instance —
        the fixture label wrongly reported at 11px — is fixed and
        covered by the test that came with the fix; what is encoded here
        is the rule it broke: a check may only report a number a reader
        could verify by looking. `box_w` on the `autoResize is False`
        branch is the wrapping frame the fitter chose, so a check reading
        it measures the loader's own bookkeeping and calls it ink.

        Written against the finding rather than against the arithmetic:
        `_wrapped_ink_in_a_wide_frame` is built so ink (86px) clears the
        band (186.3px) while the frame (200px) does not, so the current
        reading is silent and a frame-reading regression reports about
        14px. I verified the discrimination by measuring both numbers on
        the scene before writing the assertion — a scene where the two
        readings agree would pass here forever while proving nothing,
        which is exactly how the defect survived its own test.

        The silence is asserted over every channel rather than as a
        `Silence` spec, because the neighbour half of this claim already
        lives in `diamond_label_overflows_shape` — that entry proves the
        check still fires, at a magnitude of 11px, so a check that had
        simply died could not make this pass.
        """
        scene = _wrapped_ink_in_a_wide_frame()
        said = [f for f in collect_findings(scene)
                if f["check"] == "label_overflows_shape"]
        self.assertEqual(
            said, [],
            "the label's ink is 86px inside a 186px band and nothing "
            "overhangs; the check reported %s — it is reading the "
            "wrapping frame, not the drawn text"
            % [f["raw"] for f in said])

    def test_neighbour_diamond_label_overflows_shape(self) -> None:
        """The same label on a 220-wide diamond has 5px to spare, and is quiet."""
        # A real other pole since task 17, not the liveness borrow it
        # replaced — see the catalogue entry.
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

    def test_mutant_unroled_text_over_node(self) -> None:
        """FLIPPED: a text with no role covering a node is now reported.

        The overlap loop gated on role == "annotation" and `role_of`
        defaults everything unroled to "node", so the same 2400px² was
        reported when roled and silent when not. v0.9 WP4 made the loop
        role-blind: the role picks the sentence, never whether we look.
        """
        self._run("unroled_text_over_node")

    def test_neighbour_unroled_text_over_node(self) -> None:
        """The same overlap with a role attached: the annotation arm speaks.

        Same geometry, same 2400px², same named element — only the
        check's name differs, which is the role gate and nothing else.
        """
        self._run_neighbour("unroled_text_over_node")

    @unittest.expectedFailure
    def test_mutant_tolerable_gap_hides_interior_run(self) -> None:
        """An arrow crossing a whole node reads clean once its tail clears it.

        Pushing the tail 3px PAST N's border adds an overshoot to a
        crossing and takes the lint from one error to silence, because
        the interior-run walk is gated on `outside` being zero and the
        rectangle branch has no tolerance floor under it.
        """
        self._run("tolerable_gap_hides_interior_run")

    def test_neighbour_tolerable_gap_hides_interior_run(self) -> None:
        """The same crossing with its tail ON the border: the walk speaks.

        Same node, same 98px of interior run, same named element — the
        gate is the only thing that differs, so this pole is what proves
        the check alive and numerate while the mutant stays red.
        """
        self._run_neighbour("tolerable_gap_hides_interior_run")

    def test_mutant_near_miss_clearance(self) -> None:
        """FLIPPED: two nodes 4px apart no longer read like two 60px apart.

        The overlap arm needed a real intersection, so every positive gap
        was silent however small. v0.9 WP4 added the crowding arm inside
        the same pair loop, under an 8px floor measured against the
        fixture corpus.
        """
        self._run("near_miss_clearance")

    def test_neighbour_near_miss_clearance(self) -> None:
        """A generous gap is silent, and now on the check that could speak."""
        self._run_neighbour("near_miss_clearance")

    def test_mutant_diamond_clearance_overfire(self) -> None:
        """FLIPPED by v0.9 Task 56. Two rhombi 84px apart are left alone.

        The crowding arm measured stored boxes, so a shape that does not
        fill its box was reported on geometry nobody drew. The pair loop
        now refines the box reading with `shape_overlap` (canvas.py) and
        reads this pair's gap as the 84px the builder's docstring
        derives, against the box's 4.
        """
        self._run("diamond_clearance_overfire")

    def test_neighbour_diamond_clearance_overfire(self) -> None:
        """The same two boxes as rectangles really are 4px apart, and are told.

        Single-variable against the mutant: identical ids, coordinates
        and extents, `kind` the only difference. A fix that silenced the
        check rather than teaching it about shapes passes the mutant and
        fails here.
        """
        self._run_neighbour("diamond_clearance_overfire")

    def test_mutant_ellipse_clearance_overfire(self) -> None:
        """Two circles 44px of clear ground apart are left alone.

        The conic arm of the entry above. `shape_clip` reaches both
        shapes, so one edit taught both — and a later change that reached
        only the polygon would fail here alone.
        """
        self._run("ellipse_clearance_overfire")

    def test_neighbour_ellipse_clearance_overfire(self) -> None:
        """The same two boxes as rectangles really are 4px apart, and are told."""
        self._run_neighbour("ellipse_clearance_overfire")

    def test_mutant_boxed_overlap_hides_a_near_miss(self) -> None:
        """Two rhombi whose boxes overlap are still 4px apart, and told so.

        The one arm of the shape fix that ADDS a voice: the overlap arm
        owns any pair whose boxes intersect, so this pair fell between
        the two arms and drew silence from both.
        """
        self._run("boxed_overlap_hides_a_near_miss")

    def test_neighbour_boxed_overlap_hides_a_near_miss(self) -> None:
        """The same two rhombi 220px apart are a layout, and draw nothing."""
        self._run_neighbour("boxed_overlap_hides_a_near_miss")

    def test_mutant_stacked_diamonds_near_miss(self) -> None:
        """Two rhombi stacked 4px apart are reported on the vertical axis."""
        self._run("stacked_diamonds_near_miss")

    def test_neighbour_stacked_diamonds_near_miss(self) -> None:
        """And the same pair side by side, on the horizontal.

        Not a silent pole: both halves of this entry assert 4px, because
        what it pins is that the arm reads BOTH axes. An implementation
        that understood one and reversed the other would satisfy either
        half alone.
        """
        self._run_neighbour("stacked_diamonds_near_miss")

    def test_mutant_diagonal_ellipses_near_miss(self) -> None:
        """Two circles clear on both axes are reported on the nearer one.

        The three rhombus entries above cannot see this: L1 geometry
        gives a rhombus the same gap along either axis, so they read
        identically whichever the arm picks. These circles clear by
        6.68px on the axis they face across and 8.00px on the axis they
        do not, and only one of those is what a reader sees.
        """
        self._run("diagonal_ellipses_near_miss")

    def test_neighbour_diagonal_ellipses_near_miss(self) -> None:
        """The same pair staggered 8px further is clear, and stays quiet.

        Same `dx`, so the stagger is the only variable, and the boxes
        still overlap — the pair takes the identical path through the
        loop and differs only in whether the outlines are inside the
        floor. A fix that reported the nearer axis unconditionally, or
        that dropped the floor, fails here.
        """
        self._run_neighbour("diagonal_ellipses_near_miss")

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

    def test_mutant_diamond_facet_overfire(self) -> None:
        """A perfect facet-midpoint attachment draws no complaint."""
        # Was measured against the bbox. FLIPPED by WP4 (task 15): on the
        # outline the clip reads zero, for the gap AND the interior run.
        self._run("diamond_facet_overfire")

    def test_neighbour_diamond_facet_overfire(self) -> None:
        """A rectangle attachment 40px short of its border is named.

        REPOINTED by curator batch 24 — this used to read "fanned
        rectangle attachments stay endpoint-silent", which was a second
        absence beside the mutant's own, and the two of them together
        said nothing a dead `endpoint_gap` would not also say. The
        catalogue entry carries the reasoning; the silence it gave up is
        still asserted by the two sibling diamond mutants.
        """
        self._run_neighbour("diamond_facet_overfire")

    def test_mutant_foreign_diamond_corner_overfire(self) -> None:
        """An arrow clipping the empty bbox corner draws no complaint."""
        # Was a bbox-shaped through-node test. FLIPPED by WP4 (task 16):
        # `_seg_hits_rect` clips to the outline via `shape_clip`, so the
        # 36px of white canvas in the corner is white canvas again.
        self._run("foreign_diamond_corner_overfire")

    def test_neighbour_foreign_diamond_corner_overfire(self) -> None:
        """An arrow through the rhombus body really does pass through it."""
        self._run_neighbour("foreign_diamond_corner_overfire")

    def test_mutant_four_crossings_pairbug(self) -> None:
        """Four true crossings, counted as four."""
        # Was a pair count masquerading as a crossing count. FLIPPED by
        # WP4 (task 13): `crossing_sites` enumerates every intersecting
        # segment pair, so the double-break is gone.
        self._run("four_crossings_pairbug")

    def test_neighbour_four_crossings_pairbug(self) -> None:
        """A single crossing counts one either way."""
        self._run_neighbour("four_crossings_pairbug")

    def test_mutant_float_diamond_center_zero(self) -> None:
        """An endpoint pinned dead-center is reported, 44.7px off the facet."""
        # The radial measure's `t == 0` guard returned r (0). FLIPPED by
        # WP4 (task 16): `_dist_to_diamond` reads the perpendicular
        # distance to the facet, which the center has no way to zero.
        # The incidental `endpoint_gap` on this scene reads 100px inside
        # — the axial penetration depth task 15 made it measure — and is
        # a different check answering a different question.
        self._run("float_diamond_center_zero")

    def test_neighbour_float_diamond_center_zero(self) -> None:
        """An endpoint exactly on the facet is not a floating endpoint."""
        self._run_neighbour("float_diamond_center_zero")

    def test_mutant_curved_elbow_spurious_bidi(self) -> None:
        """A curved elbow bowed off its own chord no longer reads as bidi."""
        # Was read off the stored chord. FLIPPED by WP4 (task 13):
        # `_final_stretch` samples the rendered path over the final span
        # (the arrowhead tangent alone would not have flipped it).
        self._run("curved_elbow_spurious_bidi")

    def test_neighbour_curved_elbow_spurious_bidi(self) -> None:
        """Sharp opposed elbows genuinely read as one bidirectional line."""
        self._run_neighbour("curved_elbow_spurious_bidi")

    def test_mutant_long_run_curve_hides_bidi(self) -> None:
        """FLIPPED (task 24): the band grew with the span and this fired.

        The 2px band was absolute and the span was not, so the same
        7.4px bow disqualified an 18px approach and a 200px one alike.
        `_reads_as_line` now takes the wider of 2px and 1/14 of the
        stretch's own on-axis extent, which admits 7.4px on 200px of run
        and still refuses it on 18px — the neighbour below.
        """
        self._run("long_run_curve_hides_bidi")

    def test_neighbour_long_run_curve_hides_bidi(self) -> None:
        """The short approach really is mostly bow — silence is right there."""
        self._run_neighbour("long_run_curve_hides_bidi")

    def test_mutant_collinear_overlap_corridor(self) -> None:
        """300px of collinear overlap reads as one shared corridor."""
        # No defect here — this guards correct behavior, not a bug.
        self._run("collinear_overlap_corridor")

    def test_neighbour_collinear_overlap_corridor(self) -> None:
        """Parallel arrows 40px apart are two strokes, not one."""
        self._run_neighbour("collinear_overlap_corridor")

    def test_mutant_phantom_passthrough_shared_attach(self) -> None:
        """FLIPPED (task 24): the pass-through is named, and measured.

        No `phantom_passthrough` detector existed, so this was red by
        absence. The lint landed with a DETECTORS entry in the same
        change and reports the span of N that has arrow drawn over it —
        80px, the whole node.
        """
        self._run("phantom_passthrough_shared_attach")

    def test_neighbour_phantom_passthrough_shared_attach(self) -> None:
        """Feet on opposite borders leave the box between them, and quiet."""
        # UPGRADED at the flip from `Silence("shared_corridor")`, which
        # was a borrow: it proved the picture was two strokes and nothing
        # about this check. The two mutants below still assert that pole.
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
    def test_mutant_headless_chain_reads_through_node(self) -> None:
        """RED BY ABSENCE: no terminator, and no finding either.

        The ink half ships on the argument that an arrowhead ends the
        reading before the eye completes a stroke across the box. Strip
        the arrowheads — which a one-to-one erDiagram relation does
        through the shipped seeder — and the lint is still silent,
        because its gate is `covered >= 1` and nothing about the cue.
        """
        self._run("headless_chain_reads_through_node")

    def test_neighbour_headless_chain_reads_through_node(self) -> None:
        """The identical scene with arrowheads must stay quiet.

        This is the half that keeps the fix honest: the 70 findings the
        broad criterion produced over 24 shipped artifacts all look like
        this pole, so an implementation that earns the red above by
        dropping the terminator test buys it back here.

        THE THIRD SCENE below is curator batch 24's repair of the
        mortality spike's §4a second finding (2026-08-16). Both this
        neighbour and the mutant's own `expect` name
        `phantom_passthrough`, and while the mutant is RED its expect is
        not evidence — so in a green run nothing asserted the check fires
        on this pin's geometry at all, and a dead `phantom_passthrough`
        passed the neighbour. The spike offered a cross-reference to
        `curved_foot_axis_misreads_phantom_passthrough` as the cheap
        alternative and rejected it in the same breath, correctly:
        comments do not fail.

        Why a third scene rather than repointing the neighbour, which
        would have been one catalogue line. The neighbour's Silence is
        this mutant's whole discriminator — `headless` is the ONLY thing
        that differs between the two scenes, so an implementation that
        reports collinear in/out pairs regardless of terminators fires on
        both and fails here. Swapping it for a FindingSpec would buy the
        firing pole by spending the pair, which is the assertion. The
        firing therefore rides beside it, on the same builder, through
        the same entry point `_run_neighbour` uses: one foot moved onto
        N's far border is the shared-attach chain, and 80px of N really
        does have arrow drawn across it.
        """
        self._run_neighbour("headless_chain_reads_through_node")
        # The live half. Same builder, same check, same `collect_findings`
        # call the neighbour above is judged on, so a detector that has
        # stopped answering fails HERE rather than passing both poles.
        spoke = [f for f in collect_findings(_attach_chain(shared=True))
                 if f["check"] == "phantom_passthrough"]
        self.assertEqual(
            [(f["element"], f["magnitude"]) for f in spoke], [("N", 80.0)],
            "the shared-attach chain draws 80px of arrow across N and "
            "`phantom_passthrough` should say so; got %s. Until it does, "
            "the silence asserted just above is about the instrument and "
            "not about the picture" % spoke)

    def test_mutant_ellipse_corner_overfire(self) -> None:
        """An arrow 27px clear of the circle draws no complaint."""
        # Bbox-shaped through-node test, ellipse instance. FLIPPED by WP4
        # (task 16) with its rhombus sibling and by the same edit — the
        # scope rule that made this mutant exist, discharged: `shape_clip`
        # knows all three shapes, so one predicate fixed both.
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
        """Every promoted artifact parses and every detector completes.

        The sweep is a SILENCE — "no detector crashed" — and it carries
        its own firing pole since 2026-08-15 (rule 8), because two
        different failures produce the same green here. The corpus loop
        below asserts `detector-error` is absent; an instrument path that
        had stopped REPORTING crashes, or a `collect_findings` whose
        error arm had been refactored out from under this name, is also
        absent, on every artifact, forever.

        `TestEngineRules.test_crashing_detector_reports_detector_error`
        proves the arm over an EMPTY scene and a synthetic registry,
        which is a different question from the one asked here: this sweep
        runs the shipped `DETECTORS` over real promoted drawings, and it
        is that path a corpus-wide silence is a claim about. So the last
        subTest injects a crash into a copy of the live registry and
        re-runs the same call over the same corpus element list. It has
        to speak there, or the six silences above it are unreadable.

        The corpus is also asserted NON-EMPTY. `skipUnless` only checks
        that the directory exists, so an emptied fixture would leave the
        loop iterating nothing and passing — a vacuous green in a test
        whose whole subject is whether the instruments ran at all.
        """
        root = Path(__file__).parent / "fixtures" / "argus-r5" / "artifacts"
        corpus = sorted(root.iterdir())
        self.assertTrue(corpus, "the r5 fixture directory is empty, so "
                                "this swept no artifact at all: %s" % root)
        for path in corpus:
            with self.subTest(artifact=path.name):
                data = json.loads(path.read_text())
                els = data["elements"] if isinstance(data, dict) else data
                finds = collect_findings(els)
                self.assertNotIn("detector-error",
                                 {f["check"] for f in finds})
        with self.subTest(artifact="synthetic crash"):
            data = json.loads(corpus[0].read_text())
            els = data["elements"] if isinstance(data, dict) else data
            bad = dict(DETECTORS)
            bad["boom"] = {"collect": lambda els: 1 / 0}
            finds = collect_findings(els, registry=bad)
            self.assertIn(
                "detector-error", {f["check"] for f in finds},
                "a detector raised over %s and the sweep's own instrument "
                "path reported nothing — the silences above are asserted "
                "against a channel that cannot speak" % corpus[0].name)
            self.assertEqual(
                [f["element"] for f in finds
                 if f["check"] == "detector-error"], ["boom"],
                "the crash was reported without naming which detector "
                "raised, which is what makes the sweep actionable")


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
    # stays (it is the control for the opacity mutant beside it); the record
    # names the proof over a real shipped class in a plausible configuration.
    # NOT moved again by v0.9 task 50, which flipped that opacity mutant by
    # giving the check a THIRD renderer to run against — the real Excalidraw
    # client, `test_mutants_render.client_ablation_findings`. That proof is
    # stronger about the RENDERER and weaker about the DETECTOR: its scene is
    # a hand-built ghost, where this one is a configuration a person reaches
    # by accident. The string here is the detector's evidence, so it stays;
    # read the flipped mutant for what the client path can see that the SVG
    # path cannot.
    "ablation_existence":
        "test_mutants_render.TestRenderMutants."
        "test_ablation_existence_fires_on_a_real_shipped_class",
    "ablation_continuity":
        "test_mutants_render.TestRenderMutants."
        "test_mutant_label_backdrop_severs_connector",
    # Named at the FIRING proof, not at the red mutant beside it: the mutant
    # asserts the post-fix silence, and a silence proves nothing (see
    # `test_silence_only_mutant_does_not_prove_its_check`). v0.9 WP4 (Task 22)
    # moved this off `test_parity_clip_is_red_by_measurement_not_by_error`,
    # which fixing the bounds loop made false — that test asserted the check
    # FIRES on a centered label the export used to clip, and it no longer
    # does. What is named instead is still a FIRING proof, and that is the
    # point of naming it: `parity_clipped` fires only on a frame that does
    # not contain its own ink, which is a tier-1 defect by definition, so
    # with tier 1 correct no DRAWING produces the finding. The replacement
    # gets one anyway by asking `parity_findings` for a frame the caller
    # deliberately shrank (its `frame_pad` seam), so the real function
    # assembles a real finding — check, magnitude and direction — over a
    # correct product. The attribution half is pinned ungated besides, in
    # `TestRenderParityRegime.
    # test_the_edge_attribution_still_names_the_side_ink_escapes`.
    # Curator batch 18 then found tier 1 was NOT correct on the vertical
    # axis — a wrapped text overran its frame's bottom — so for a day a
    # DRAWING fired this check again, pinned with its magnitude in a
    # scaffold beside that red. The pointer deliberately did NOT move to
    # it, on the grounds that a record which goes dark the day the
    # product gets better is not a record of the check; v0.9 task 46
    # then fixed the bounds loop, the red flipped and the scaffold died
    # with it, and this line needed no edit. That is the argument for
    # the rule, not a coincidence: name the proof that survives a fix.
    "parity_clipped":
        "test_mutants_render.TestRenderParity."
        "test_the_clip_instrument_still_sees_ink_leave_a_short_frame",
}

# Check names a catalogue entry may name with no `DETECTORS` detector
# behind them, mapped to why that is legitimate. Red-by-absence is a real
# tactic — `phantom_passthrough_shared_attach` pinned a defect the lint
# could not see, stayed red for two days, and flipped when the lint landed
# (task 24) — but it is
# indistinguishable from a TYPO'd check name, which is red forever for no
# reason. Worse, a typo'd check inside a `Silence` matches nothing and so
# passes VACUOUSLY forever. This table is the difference: aspiration is
# declared here with its reason, and anything else is a mistake.
ASPIRATIONAL: dict[str, str] = {
    # (`phantom_passthrough` left this table on 2026-08-14, v0.9 WP4 task
    # 24: WP4b's e1 lint landed, took a `DETECTORS` entry, and the mutant
    # flipped in the same change — with its borrowed neighbour replaced by
    # this check's own quiet pole, which is the debt the block below says
    # a flip owes.)
    "frame_containment":
        "WP5 — no check compares a member's geometry against the frame its "
        "`frameId` names; lint_layout reads frameId for help slots and "
        "same-frame pairing only, never for containment",
    # (`text_overlaps_node` and `min_clearance` left this table on
    # 2026-08-14, v0.9 WP4 Task 23: both checks landed, both took a
    # `DETECTORS` entry, and both mutants flipped in the same change.)
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
#   frame_containment — neighbours `Silence("endpoint_gap")` over a scene
#       with NO ARROWS, which proves liveness only: that check cannot
#       fire there whatever the frames do. It earns its keep by refusing
#       to match over any run where a detector crashed, and nothing more.
#       (`unroled_text_over_node` stood here too until Task 23 flipped it;
#       see the second worked example at the foot of this block.)
#
# So when WP4b/WP5's lints land, dropping the `expectedFailure` is not the
# whole change: give each mutant a real other-pole neighbour on the new
# check at the same time, or the flip trades a red that meant something for
# a green that does not.
#
# `label_overflows_shape` is the worked example, flipped by v0.9 WP4: its
# neighbour was one of the `Silence("endpoint_gap")` borrows above and is
# now `Silence("label_overflows_shape")` over a 220-wide diamond, where
# the same label on the same shape has 5px to spare. The pole that
# borrow could not assert is the one that discriminates — a check that
# simply fired on every diamond would have satisfied the old rectangle
# control and fails this one.
#
# `phantom_passthrough_shared_attach` is the fourth (task 24) and the only
# one so far to pay the debt by DESIGNING the check around the control
# rather than finding a control for the check. Its borrow was the
# contingent `Silence("shared_corridor")` over `_attach_chain(shared=False)`
# — quiet that meant something about the picture and nothing about this
# check. The upgrade to `Silence("phantom_passthrough")` over the same
# builder is only worth having because the lint was narrowed until that
# Silence bites: the literature scan's broad criterion (any collinear
# in/out pair on opposite borders) satisfies the OLD neighbour and fails
# the new one, and measured 70 findings across 24 shipped artifacts. The
# order matters and is the transferable part — write the other-pole
# neighbour first and it tells you what the check may not be.
#
# `near_miss_clearance` and `unroled_text_over_node` are the second and
# third, flipped by Task 23, and they paid the debt in the two shapes it
# comes in. `near_miss_clearance` took the same route as the example above:
# `Silence("min_clearance")` over its own builder at 60px, the pole its
# arrowless borrow could not reach. `unroled_text_over_node` took the OTHER
# route, the one the flip debt is easy to read as forbidding — its neighbour
# is not a Silence at all but a `FindingSpec` on a DIFFERENT check,
# `annotation_overlaps_node`, which had to be drained from `UNCOVERED` in the
# same change to exist as an assertion. That is legitimate here and worth
# saying why: this mutant's defect is not "the check is missing", it is "the
# check consults the role", and the poles of a GATE are gated and ungated,
# not fired and silent. Both scenes fire; the question the pair settles is
# which voice answers. A Silence-on-a-clear-text control would have been the
# easy shape and would have proved something else — that the check has a
# quiet half — which is not what five rounds of this bug were about.

UNCOVERED: dict[str, str] = {
    # `crosses_through_bound` stood here from day one — the last DETECTORS
    # row with no proving mutant — and was drained by curator batch 20
    # (2026-08-14) with `tolerable_gap_hides_interior_run`. Its own note
    # said "every scene that runs long enough inside a bound node to trip
    # it also trips endpoint_gap first", and that turned out to be the
    # reason the drain was available rather than an obstacle to it: the
    # gap and the run are read on the same scene by two checks with
    # different floors, so a tail 3px OUTSIDE the border is beneath
    # `endpoint_gap`'s tolerance and above the interior walk's gate. The
    # proof of firing is that mutant's NEIGHBOUR, not its red — ungated,
    # asserted at 98px in every commit — which is what `coverage_table`
    # counts and what keeps this drain honest.

    # lint_layout message templates with no DETECTORS entry (enumerated
    # 2026-08-12 by grepping errors.append/warnings.append/notes.append
    # over `lint_layout`'s body. The three templates
    # DETECTORS already covers via lint_re — endpoint_gap,
    # crosses_through_bound, passes_through_foreign — are excluded here;
    # project_lint (canvas.py) delegates to lint_layout,
    # lint_glossary and lint_registry and has no direct appends of its
    # own, so it contributes no rows).
    "budget_override_note":
        "enumerated 2026-08-12; no proving mutant yet — in lint_layout",
    "decoration_overhang":
        "enumerated 2026-08-12; no proving mutant yet — in lint_layout",
    # Added 2026-08-14 (v0.9 WP4, task 24) with the template itself, which
    # is what this ledger is for: WP4b's e15 arrived with no CATALOGUE
    # mutant because no defect indicts it — it is input hygiene, and the
    # catalogue's unit is a defect the checks got WRONG. Both poles of all
    # four arms are proven ungated in
    # tests/test_backend.TestDegenerateArrowGeometry, including the
    # 24-fixture corpus staying silent, so the gap this row names is
    # narrow and specific: nothing asserts a MAGNITUDE for these findings
    # through `collect_findings`, so a rewrite that kept the wording and
    # lost the arithmetic would pass the catalogue. Draining it needs a
    # DETECTORS entry with a `lint_re` over the fault clauses.
    "degenerate_arrow":
        "landed 2026-08-14 (WP4b e15) with unit tests on both poles but "
        "no CATALOGUE mutant — hygiene, not a missed defect; drain it "
        "with a DETECTORS lint_re over the fault clauses",
    "half_unbound_endpoint":
        "enumerated 2026-08-12; no proving mutant yet — in lint_layout",
    "unbound_arrow":
        "enumerated 2026-08-12; no proving mutant yet — in lint_layout",
    "dangling_binding":
        "enumerated 2026-08-12; no proving mutant yet — in lint_layout",
    "source_to_sink":
        "enumerated 2026-08-12; no proving mutant yet — in lint_layout",
    "flow_black_hole":
        "enumerated 2026-08-12; no proving mutant yet — in lint_layout",
    "flow_miracle":
        "enumerated 2026-08-12; no proving mutant yet — in lint_layout",
    "message_travels_up":
        "enumerated 2026-08-12; no proving mutant yet — in lint_layout",
    "message_budget":
        "enumerated 2026-08-12; no proving mutant yet — in lint_layout",
    "duplicate_screen_title":
        "enumerated 2026-08-12; no proving mutant yet — in lint_layout",
    "variant_label_mismatch":
        "enumerated 2026-08-12; no proving mutant yet — in lint_layout",
    "submit_precedes_inputs":
        "enumerated 2026-08-12; no proving mutant yet — in lint_layout",
    "input_missing_label":
        "enumerated 2026-08-12; no proving mutant yet — in lint_layout",
    "input_asterisk_required":
        "enumerated 2026-08-12; no proving mutant yet — in lint_layout",
    "uniform_input_widths":
        "enumerated 2026-08-12; no proving mutant yet — in lint_layout",
    "sticky_bar_over_inputs":
        "enumerated 2026-08-12; no proving mutant yet — in lint_layout",
    "help_presence_missing":
        "enumerated 2026-08-12; no proving mutant yet — in lint_layout",
    "help_slot_drift":
        "enumerated 2026-08-12; no proving mutant yet — in lint_layout",
    "target_size_too_close":
        "enumerated 2026-08-12; no proving mutant yet — in lint_layout",
    "progress_indicator_present":
        "enumerated 2026-08-12; no proving mutant yet — in lint_layout",
    "label_wider_than_run":
        "enumerated 2026-08-12; no proving mutant yet — in lint_layout",
    "arrow_points_both_ways":
        "enumerated 2026-08-12; no proving mutant yet — in lint_layout",
    "diagonal_arrow":
        "enumerated 2026-08-12; no proving mutant yet — in lint_layout",
    "grown_label_overlap":
        "enumerated 2026-08-12; no proving mutant yet — in lint_layout. "
        "v0.9 Task 56: `TestShapeBlindPairOverlap` now pins the arm from "
        "both poles, but hand-authored, so this row stands. Draining it "
        "needs a DETECTORS entry — the arm reports no magnitude at all "
        "today, which is what a `lint_re` would have to read.",
    "shape_overlap":
        "enumerated 2026-08-12; no proving mutant yet — in lint_layout. "
        "v0.9 Task 56 pins it the same way and for the same reason as "
        "`grown_label_overlap` above; the two are the two arms of one "
        "`if`. NAME COLLISION, stated so it is not read as one: "
        "canvas.py grew a `shape_overlap` FUNCTION in that task, and it "
        "is the geometry this check reports on rather than a second "
        "meaning. The confusion is real; the CORRUPTION is not, and the "
        "Task 56 review measured the difference — registering a "
        "`Silence(\"shape_overlap\")` here fails "
        "`TestCoverage.test_every_expected_check_has_a_detector_or_is_"
        "declared` twice, once for `expect` and once for "
        "`neighbour.expect`, whose own message names the vacuously-green "
        "case. So the hazard is a reader landing on the wrong symbol, "
        "not a pin that silently proves nothing.",
    "annotation_budget":
        "enumerated 2026-08-12; no proving mutant yet — in lint_layout",
    # (`label_label_overlap`, canvas.py, left this table on
    # 2026-08-12: the visualize-skill mine's §(i.4) exposed the stored-width
    # dependency underneath it and `stale_label_width_hides_collision` now
    # proves it from DETECTORS, both poles.)
    # (DRAINED 2026-08-15, v0.9 WP4 stage 3: the check took a `DETECTORS`
    # entry and two proving mutants — `label_anchor_reads_the_arc_not_
    # the_client` and `curved_even_path_moves_the_label` — in the same
    # change as the label-anchor rewrite, which is the rule this table
    # states: the entry lands with the work that needed it. The row is
    # kept, commented, because the batch-19 ruling below cites it: the
    # three-arm shape-blindness family stays hand-authored, and the
    # reason is no longer "no entry exists" but the ruling's own one —
    # a family may not be split across two homes. Whoever revisits that
    # ruling should know its stated blocker is gone.)
    # (`annotation_overlaps_node`, canvas.py, left this table on 2026-08-14:
    # `unroled_text_over_node`'s neighbour now proves it from DETECTORS with
    # a magnitude, over the same geometry the mutant itself uses. It is the
    # only row here drained by a NEIGHBOUR rather than by a mutant's own
    # expectation, which `coverage_table` counts on purpose — the roled
    # overlap firing IS the other pole of the role gate, not a control
    # borrowed from somewhere else.)
    # (`text_overflow`, canvas.py, left this table on 2026-08-13:
    # `composed_row_overflows_its_box` and `wrapped_label_overflows_its_box`
    # now prove it from DETECTORS on BOTH code paths, each with a magnitude
    # and an axis. The bbox-naive `room_w` underneath it is recorded at those
    # entries as an arm of `label_overflows_shape`, not as a defect of its
    # own.)
    # (`shared_attach_point`, canvas.py, left this table on
    # 2026-08-12: the ELK spike fired it in production and
    # `shared_attach_point_fan_failed` now proves it from DETECTORS.)
    "stranded_element":
        "enumerated 2026-08-12; no proving mutant yet — in lint_layout",
    "offgrid_elements":
        "enumerated 2026-08-12; no proving mutant yet — in lint_layout",
    "opacity_not_style":
        "enumerated 2026-08-12; no proving mutant yet — in lint_layout",
    "unlabeled_decision_branch":
        "enumerated 2026-08-12; no proving mutant yet — in lint_layout",
    "unconnected_nodes":
        "enumerated 2026-08-12; no proving mutant yet — in lint_layout",
    "screen_node_budget":
        "enumerated 2026-08-12; no proving mutant yet — in lint_layout",
    "node_budget_whole_artifact":
        "enumerated 2026-08-12; no proving mutant yet — in lint_layout",
    "arrow_budget":
        "enumerated 2026-08-12; no proving mutant yet — in lint_layout",

    # ART-### repair codes: validate_scene (canvas.py) and the
    # project-load JSON guard (canvas.py).
    # (`not_a_json_object`, ART-000, canvas.py, left this table on
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
                           "ART-001, validate_scene",
    "malformed_element_dropped":
        "enumerated 2026-08-12; no proving mutant yet — "
        "ART-002, validate_scene",
    "duplicate_element_id_dropped":
        "enumerated 2026-08-12; no proving mutant yet — "
        "ART-003, validate_scene",
    "dangling_container_detached":
        "enumerated 2026-08-12; no proving mutant yet — "
        "ART-004, validate_scene",
    "dangling_binding_cleared":
        "enumerated 2026-08-12; no proving mutant yet — "
        "ART-005, validate_scene",
    "invalid_json_ignored": "enumerated 2026-08-12; no proving mutant yet — "
                            "ART-006, the project-load JSON guard",
    "detached_label_recentered":
        "enumerated 2026-08-12; no proving mutant yet — "
        "ART-007, validate_scene",
    "label_in_text_element_merged":
        "enumerated 2026-08-12; no proving mutant yet — "
        "ART-010, validate_scene",
    "label_wider_than_container_refit":
        "enumerated 2026-08-12; no proving mutant yet — "
        "ART-011, validate_scene",
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

    A RED mutant's own `expect` is not evidence, and this is the one rule
    here that is not obvious. A `FindingSpec` on a mutant marked
    `@unittest.expectedFailure` records a finding the check does NOT
    emit — that is what red means — so counting it would let the table
    cite, as proof that a detector speaks, the very entry asserting it is
    silent. It went unnoticed while no red happened to sort first for its
    check; `headless_chain_reads_through_node` (curator batch 21) is the
    first that does, and it displaced the green
    `phantom_passthrough_shared_attach` from the evidence column while
    the row went on saying "proven". A NEIGHBOUR's `FindingSpec` is
    always evidence by contrast, red mutant or not: neighbours are
    ungated and asserted in every commit, which is the whole reason
    doctrine calls them the live half.

    Returns:
        One `(detector, status, evidence)` tuple per name currently in
        `DETECTORS`, sorted by name. `status` is one of "proven",
        "render-tier", "UNCOVERED". `evidence` is the proving mutant id
        for "proven" — the lexicographically first, so a check with
        several proofs reports the same one every run — the gated test
        `RENDER_TIER` names for "render-tier", or the `UNCOVERED` reason
        (empty string if the detector carries none).
    """
    def is_red(mid: str) -> bool:
        """Whether this mutant's own test is declared red-by-intent.

        Read off the test method, not the catalogue entry, because
        `@unittest.expectedFailure` is where the declaration lives and
        the runner is what turns a landed fix into an unexpected
        success — the same source `scripts/mutants` reads.

        Args:
            mid: The mutant's id.

        Returns:
            True if `test_mutant_<mid>` carries the decorator.
        """
        method = getattr(TestMutantCatalogue, "test_mutant_%s" % mid, None)
        return bool(getattr(method, "__unittest_expecting_failure__", False))

    proven_by: dict[str, str] = {}
    for mid in sorted(CATALOGUE):
        mutant = CATALOGUE[mid]
        expects = [mutant.neighbour.expect]
        if not is_red(mid):
            expects.append(mutant.expect)
        for expect in expects:
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


# The classes in THIS file whose reds are hand-authored plain tests rather
# than CATALOGUE entries, with how many each carries. Declared once, here,
# because it is the list the dedupe guard's residual-gap paragraph and the
# `CATALOGUE` section comment both state in prose — and both have now been
# caught stating it WRONGLY three times (2026-08-12 `TestStoreIntegrity` at
# five when WP1 had flipped four; after Task 40 `TestPinIdentityIntegrity`
# still listed with three when it had none; Task 21 leaving
# `TestExportCompleteness` and `TestPaintOrder` behind in the list after
# emptying them). A class does not merely lose a number when its last red
# flips — it LEAVES this list, and nothing in the suite noticed either way
# until `test_the_hand_authored_red_classes_are_the_ones_that_exist` below.
#
# Counts and not just names, deliberately: a new red inside one of these
# classes is precisely the event that also needs those two comments updated,
# so it should cost one line here and be loud about it.
#
# What this does NOT do, so it is not mistaken for the fix to backlog row 27:
# it makes the DISCLOSURE self-checking, not the dedupe. The reds in these
# classes still never reach `_register`, still have no expectation objects to
# fingerprint, and two agents could still write the same plain red test under
# different method names with nothing to notice. That exposure is unchanged;
# what changed is that the sentence admitting it can no longer go stale.
HAND_AUTHORED_RED_CLASSES = {"TestBatchPathIntegrity": 1,
                             "TestBoundsLoopReadsTheLineHeight": 1,
                             "TestCornerBiasReadsVerticesNotTurns": 1,
                             "TestLoadFindingsReachTheAgent": 4,
                             "TestReplayOrderFidelity": 2}
# `TestBoundsLoopReadsTheLineHeight` JOINED this list on 2026-08-15 (curator
# batch 23 item 3) — the first arrival since batch 16 built the guard, and
# the motion the counts exist for: one new class, one new red, and the two
# prose statements of this fact below forced through the same edit.
# `TestShapeBlindAnnotationOverlap` LEFT this list on 2026-08-15 (v0.9
# Task 56) when its three reds flipped together — the whole class drained
# in one change, which is what the fix was scoped to do. It still holds
# tests, and green ones at that; it is absent because it holds no REDS,
# which is the only thing this dict counts.
# `TestLabelAnchorAgainstTheDrawnPosition` LEFT it the same way on the
# v0.9 curves fold-in, and for the reason its own block states: its two
# reds were one defect pinned from both ends, so the label-model port
# drained them together or it would have drained neither.
# `TestCornerBiasReadsVerticesNotTurns` survives that same change with its
# single red intact, which is worth reading as the pair it is — two label
# classes curated in one batch, one flipped by the fold-in and one not,
# because they name different functions.

# The one class whose reds ARE catalogue entries, excluded from the
# comparison above. Named rather than inlined so a rename of the class shows
# up as a failing enumeration instead of as a phantom hand-authored class.
CATALOGUE_RED_CLASS = "TestMutantCatalogue"

# The CATALOGUE half of the same disclosure, and the half that had no guard
# until curator batch 22 (2026-08-15). `HAND_AUTHORED_RED_CLASSES` above
# made the non-catalogue count self-checking three batches ago; the catalogue
# count stayed a hand-copied list in SESSION-HANDOVER.md and went stale FOUR
# recorded times — most recently omitting batch 21's
# `headless_chain_reads_through_node` while the paragraph beside it said
# "four of the six", a sentence that only adds up at seven.
#
# IDS AND NOT A NUMBER, deliberately, and the difference is the whole point.
# A count is satisfied by any six reds, so a batch that flips one red and
# adds another leaves the total right and the census wrong — which is the
# exact motion Task 56 and the curves fold-in both made. Naming them means
# a flip costs one deletion here and an addition costs one line.
#
# THAT SENTENCE USED TO END "and either way the handover's prose is forced
# through the same edit", and it was FALSE when it was written (v0.9 task 45
# §7.1, confirmed by that task's review info-1). This constant is compared
# against the live decorators and nothing else; `SESSION-HANDOVER.md` was
# never read, so the guard sat green on 2026-08-15 while the table it
# claimed to force listed six of these eight ids — the FIFTH recorded
# staleness of one hand-copied table, and the first one to happen while a
# guard was standing over it promising it could not.
#
# The lesson, which is why this is written here at length rather than
# fixed and forgotten: a guard's own comment is not evidence about the
# guard's reach. Batch 22 built the constant, described the reach it
# INTENDED, and the description outlived the intention by one commit. What
# closes it is `test_the_handover_transcribes_the_reds_it_declares` below,
# which parses the table out of the file and compares it to the same
# derivation — so the claim is now made by an assertion instead of by a
# sentence, and the two can no longer disagree.
#
# WHY THIS IS SAFE TO PIN when the flip contract already fails on an
# unexpected success: the two guards catch opposite mistakes. The runner
# notices a red that started PASSING. Nothing noticed a red that was ADDED,
# and an addition is what leaves a census stating six where seven is true.
# Neither guard subsumes the other, and this one is the cheap half.
CATALOGUE_RED_IDS = {"framed_node_escapes_its_lane", "gray_text_on_ground",
                     "headless_chain_reads_through_node", "pale_stroke_node",
                     "tiny_font_text", "tolerable_gap_hides_interior_run"}


def catalogue_red_ids() -> set[str]:
    """Every CATALOGUE id whose mutant method carries `expectedFailure`.

    Derived from the decorator's own attribute rather than from any
    written-down list, so this is the measurement the census is checked
    against. `mutants list --red` reads the same thing through a
    different path and must agree.

    Returns:
        The set of red-by-intent catalogue ids. Empty is a legitimate
        answer — it is what draining v0.9's geometry work to zero looks
        like — and is the one state that would let the guard below pass
        while meaning something new, so read the handover with it.
    """
    return {mid for mid in CATALOGUE
            if getattr(getattr(TestMutantCatalogue, "test_mutant_%s" % mid,
                               None),
                       "__unittest_expecting_failure__", False)}


HANDOVER = Path(__file__).resolve().parents[1] / "SESSION-HANDOVER.md"
# The table cell this reads, by its row label. Matched on the label rather
# than on a line number because the file is edited by every task in the
# wave; a moved table should re-find itself, and a RENAMED row should fail
# loudly rather than silently match nothing (which is the failure mode that
# would turn this guard back into the sentence it replaced).
_HANDOVER_MODEL_ROW = "| model (default suite) |"


def handover_catalogue_reds() -> set[str]:
    """The catalog reds SESSION-HANDOVER.md transcribes, as ids.

    Curator batch 23 item 1 (task 45 §7.1), 2026-08-15. The half of the
    census that batch 22's guard said it covered and did not: this reads
    the hand-copied table out of the file so the transcription can be
    compared to the derivation instead of trusted.

    Returns:
        Every backticked id in the model row of the "Catalog reds" table.

    Raises:
        AssertionError: If the file or the row is absent. A census guard
            that quietly returns an empty set when its subject moves is
            worth less than no guard, because it reports agreement while
            reading nothing — the exact shape of the defect this closes.
            Both cases are ASSERTIONS and not the underlying OSError:
            this file's own doctrine is that a red must be red by
            assertion, and an isolated tree — `tests/` copied beside a
            symlinked `skills/`, which is how every mutation proof in
            this repo is run — hits the missing-file case as a matter of
            course. It should read as one sentence about the tree, not
            as a traceback about the census.
    """
    if not HANDOVER.exists():
        raise AssertionError(
            "%s is not in this tree, so the census transcription cannot "
            "be checked. If this is an isolated mutation-proof tree, copy "
            "the file in beside tests/ — the guard is not what you are "
            "measuring and this failure is not a finding" % HANDOVER)
    for line in HANDOVER.read_text(encoding="utf-8").splitlines():
        if line.startswith(_HANDOVER_MODEL_ROW):
            return set(re.findall(r"`([a-z0-9_]+)`",
                                  line[len(_HANDOVER_MODEL_ROW):]))
    raise AssertionError(
        "no %r row in %s: the catalog-reds table has been renamed or "
        "removed. If it is gone on purpose, delete this function and its "
        "test in the same change — do not leave them matching nothing"
        % (_HANDOVER_MODEL_ROW, HANDOVER.name))


# The handover's transcription of `coverage_table`'s totals. Matched on the
# four numbers in order rather than on surrounding prose, so the sentence
# can be rewritten without breaking the guard and cannot be rewritten to say
# something else about the same numbers.
_HANDOVER_COVERAGE = re.compile(
    r"\*\*(\d+) detectors, (\d+) proven, (\d+) render-tier, "
    r"(\d+)\s*\n?\s*UNCOVERED\*\*")


def handover_coverage_totals() -> tuple[int, int, int, int]:
    """The coverage totals SESSION-HANDOVER.md states, as numbers.

    Curator batch 23's reconciliation pass, 2026-08-15. The sibling of
    `handover_catalogue_reds` and added for the same reason one batch
    later: this sentence read "18 detectors ... 15 proven" from a
    batch-21 measurement while the live table said 19 and 16, because
    `label_on_foreign_node` was registered by the curves fold-in and the
    hand copy did not move with it.

    Worth being exact about what was and was not broken, because it
    changes what this guards. `coverage_table`'s gate was never wrong —
    a `DETECTORS` entry with no mutant and no reason has always failed
    it — so no detector was ever silently unproven. What drifted was the
    handover's TRANSCRIPTION of the totals, which is the only part of
    the coverage story a reader gets without running anything.

    Returns:
        `(detectors, proven, render_tier, uncovered)`.

    Raises:
        AssertionError: If the file or the sentence is absent, for the
            reason `handover_catalogue_reds` gives at length — a census
            guard that reads nothing and reports agreement is worse than
            no guard.
    """
    if not HANDOVER.exists():
        raise AssertionError(
            "%s is not in this tree, so the coverage transcription cannot "
            "be checked. If this is an isolated mutation-proof tree, copy "
            "the file in beside tests/" % HANDOVER)
    found = _HANDOVER_COVERAGE.search(HANDOVER.read_text(encoding="utf-8"))
    if found is None:
        raise AssertionError(
            "no '**N detectors, N proven, N render-tier, N UNCOVERED**' "
            "sentence in %s: the coverage bullet has been reworded past "
            "what this reads. Re-anchor it or delete this function and "
            "its test together" % HANDOVER.name)
    return (int(found.group(1)), int(found.group(2)),
            int(found.group(3)), int(found.group(4)))


def red_bearing_classes() -> dict[str, int]:
    """Every TestCase in this module carrying an `expectedFailure`, counted.

    Reads `__unittest_expecting_failure__`, the attribute the decorator
    sets, off each class's OWN dict — inherited methods would otherwise
    be counted once per subclass and attribute a red to a class that
    never wrote one.

    Returns:
        Class name -> number of red methods defined on it. Classes with
        no reds are absent, which is the state a drained class must
        reach.
    """
    found: dict[str, int] = {}
    for name, obj in globals().items():
        if not (isinstance(obj, type) and issubclass(obj, unittest.TestCase)):
            continue
        reds = sum(1 for attr in vars(obj).values()
                   if getattr(attr, "__unittest_expecting_failure__", False))
        if reds:
            found[name] = reds
    return found


# ---------------------------------------------------------------------------
# TWO WAYS A VERIFICATION METHOD LIES, both banked from the v0.9 wave and
# both recorded here rather than beside the code they happened to, because
# what generalizes is the METHOD and not the function (curator batch 23
# items 10 and 14, from task-perf-report §candidates 1 and 3 and task 49 §7
# item 3, 2026-08-15). Doctrine §1 says silence is indistinguishable from
# health; these are the two silences that fool careful people.
#
# 1. BOUNDARY BLINDNESS — "identical on every real raster" is not a proof.
#    `pngdiff._dilate`'s two wrap guards were transposed, and the defect was
#    BIT-IDENTICAL to correct on 100% of the corpus. Not nearly, exactly:
#    the corpus cannot express the input that separates them, because every
#    diagram has a paper margin and no real drawing inks an edge column. The
#    check was right everywhere anyone looked and wrong at the image
#    boundary, and the verification method that misses it — replay over
#    every real raster, compare byte for byte — is the FIRST one a careful
#    person reaches for and the most convincing when it comes back green.
#    The existing right-edge pin caught this one, so the class is proven
#    rather than open; what is worth carrying forward is that a corpus
#    replay measures the corpus's imagination, not the code's domain. When
#    a check has a boundary case the corpus structurally cannot produce,
#    the pin has to be synthetic or there is no pin.
#
# 2. SURVIVING GUARDS — a green test is not evidence it still discriminates.
#    Task 49 removed tier 1's oversize band, which was the entire mechanism
#    `..._keeps_the_export_of_a_downscaled_drawing` had been written to
#    exercise; the guard stayed green throughout, because a clamped 4000
#    read as a FLOOR accepts 8080 just as happily as the old upper branch
#    refused it. It went from pinning a decision to pinning nothing, and
#    nothing said so — the naive fix it existed to keep out would have
#    passed the whole suite. Curator batch 23 reproduced the shape a second
#    time while pinning the slack band: widening `floor_slack` from 5% to
#    10% leaves `..._admits_every_measured_real_export` green, because
#    widening a floor only ever admits more.
#
#    The discipline: after a design change, mutation-check the guards that
#    SURVIVED it, not only the tests it added. A test that was green before
#    and green after has told you nothing about whether it is still
#    watching, and the cheapest way to ask is to break the thing it names
#    and see whether it notices. Retire by mutation rather than by taste
#    when the answer is no — task 49 §6(a) is the precedent.
# ---------------------------------------------------------------------------


# Guide rule 8, as data: the catalogue Silences allowed to stand without a
# paired firing on the same check in the same mutant's other slot, keyed by
# `<mutant id>:<slot>` and carrying the reason the gate below prints.
#
# Built by curator batch 24 (2026-08-16) out of the mortality spike's §4a,
# which computed the pairing over all 35 Silences by walking these very
# objects and found seven unpaired. Two were defects and are repaired
# (`diamond_facet_overfire`'s neighbour now fires, and
# `headless_chain_reads_through_node`'s neighbour test asserts a third
# scene's firing beside the pole). The five below are the legitimate ones,
# and putting them here rather than in a paragraph is the point of the
# table: an ASPIRATIONAL borrow stops being prose the next agent may not
# read and becomes a row a flip has to DELETE, in the same change that
# gives the check a pole of its own.
#
# Adding a row is a judgement, not a convenience. The only reason this
# table accepts is "no firing pole can be constructed", and the four
# borrows qualify for the strongest possible version of it — their checks
# do not exist, so `Silence` on them passes vacuously and the neighbour is
# a placeholder holding the mutant's shape until one does.
RULE8_EXEMPT: dict[str, str] = {
    "framed_node_escapes_its_lane:neighbour":
        "ASPIRATIONAL borrow — its own check `frame_containment` has no "
        "detector, so the partner FindingSpec is RED and no green firing "
        "exists to pair with. Delete this row when the check lands",
    "gray_text_on_ground:neighbour":
        "ASPIRATIONAL borrow — partner `contrast_text` is RED and has no "
        "detector. Delete this row when the check lands",
    "pale_stroke_node:neighbour":
        "ASPIRATIONAL borrow — partner `contrast_object` is RED and has no "
        "detector. Delete this row when the check lands",
    "tiny_font_text:neighbour":
        "ASPIRATIONAL borrow — partner `min_font` is RED and has no "
        "detector. Delete this row when the check lands",
    "headless_chain_reads_through_node:neighbour":
        "the Silence IS this mutant's discriminator — `headless` is the "
        "only variable between the two scenes — so a FindingSpec here "
        "would buy the pole by spending the assertion. The firing rides "
        "beside it instead: `test_neighbour_headless_chain_reads_through_"
        "node` asserts `phantom_passthrough` fires at 80px on "
        "`_attach_chain(shared=True)` through the same `collect_findings` "
        "call, and dies with the detector (stub-checked 2026-08-16)",
}


class TestCoverage(unittest.TestCase):
    """Spec §3: every detector is proven or named, never silently unproven."""

    def test_every_catalogue_silence_has_a_firing_beside_it(self) -> None:
        """Guide rule 8, executed over the catalogue rather than remembered.

        For every `Silence` in the catalogue — in a mutant's `expect` or
        in its neighbour's — require a `FindingSpec` on the SAME check in
        the same mutant's OTHER slot, or a declared reason in
        `RULE8_EXEMPT`. A pin that asserts a check is quiet and has no
        firing beside it passes when that check answers nothing at all,
        and cannot tell the two apart; the mortality spike measured
        exactly that against `diamond_facet_overfire`, which had carried
        `Silence("endpoint_gap")` on BOTH slots since WP4 with a comment
        explaining that the repetition was deliberate.

        A RED mutant's own `expect` is not evidence, the same rule
        `coverage_table` applies one level up and for the same reason: a
        FindingSpec under `@unittest.expectedFailure` records a finding
        the check does NOT emit today, so pairing a green Silence against
        it means nothing in a green run asserts the check fires.

        SCOPE, stated because the gate's silence about the rest is not
        evidence about them. This walks `CATALOGUE` objects only. The
        hand-authored silences in this file, `test_backend.py` and the
        render tier are NOT covered — a static classifier over
        `assertEqual(<expr>, [])` was prototyped and rejected in the same
        spike, because deciding whether a paired firing exists is not a
        decidable-by-ast question and a gate that cries wolf on a third of
        its hits gets suppressed. The instrument for those is the
        empirical stub sweep, run at each phase gate.
        """
        for mid in sorted(CATALOGUE):
            mutant = CATALOGUE[mid]
            method = getattr(TestMutantCatalogue, "test_mutant_%s" % mid,
                             None)
            red = bool(getattr(method, "__unittest_expecting_failure__",
                               False))
            slots = (("expect", mutant.expect, mutant.neighbour.expect,
                      True),
                     ("neighbour", mutant.neighbour.expect, mutant.expect,
                      not red))
            for slot, here, other, other_counts in slots:
                if not isinstance(here, Silence):
                    continue
                key = "%s:%s" % (mid, slot)
                paired = (other_counts and isinstance(other, FindingSpec)
                          and other.check == here.check)
                with self.subTest(mutant=mid, slot=slot):
                    self.assertTrue(
                        paired or key in RULE8_EXEMPT,
                        "%s asserts %s is SILENT and nothing in this "
                        "mutant makes it FIRE — a detector answering "
                        "nothing passes both poles. Pair it, or declare "
                        "the reason in RULE8_EXEMPT"
                        % (key, here.check))

    def test_the_rule_eight_exemptions_cannot_rot(self) -> None:
        """`RULE8_EXEMPT` may not outlive the pairings it excuses.

        The same discipline `ASPIRATIONAL` carries, for the same reason:
        an exception table nobody prunes becomes a list of pins that
        stopped being read. Every row must name a real catalogue slot,
        must carry a reason, and must still be UNPAIRED — the moment a
        flip gives one of these Silences a firing partner the row is
        obsolete and has to go, or the next reader cannot tell which of
        the entries are live judgements.
        """
        for key, reason in sorted(RULE8_EXEMPT.items()):
            mid, _, slot = key.partition(":")
            with self.subTest(exemption=key):
                self.assertIn(mid, CATALOGUE,
                              "RULE8_EXEMPT names %r, which is not a "
                              "mutant" % mid)
                self.assertIn(slot, ("expect", "neighbour"),
                              "RULE8_EXEMPT key %r names no slot" % key)
                self.assertTrue(str(reason).strip(),
                                "RULE8_EXEMPT %s has no reason" % key)
                mutant = CATALOGUE[mid]
                here = (mutant.expect if slot == "expect"
                        else mutant.neighbour.expect)
                self.assertIsInstance(
                    here, Silence,
                    "RULE8_EXEMPT %s excuses a slot that is not a "
                    "Silence — the row has gone stale" % key)

    def test_every_detector_is_proven_or_named(self) -> None:
        """Every DETECTORS entry is either proven or carries an UNCOVERED reason."""
        gaps = [name for name, status, _ in coverage_table()
                if status == "UNCOVERED" and name not in UNCOVERED]
        self.assertEqual(gaps, [],
                         "detectors with no firing mutant and no "
                         "UNCOVERED reason: %s" % gaps)

    def test_no_row_is_proven_by_a_red_mutants_own_expectation(self) -> None:
        """The evidence column may never cite a mutant that is still red.

        `proven` means a mutant has watched this detector speak. A red
        mutant has watched it stay silent, so citing one turns the
        headline metric into its own opposite — and it happens by
        accident, through the lexicographic tie-break, the moment
        somebody adds a red whose id sorts before the green proof
        (curator batch 21, `headless_chain_reads_through_node` before
        `phantom_passthrough_shared_attach`). A neighbour's
        `FindingSpec` is legitimate evidence and is deliberately not
        caught here: neighbours never carry the decorator and run in
        every commit.
        """
        for name, status, evidence in coverage_table():
            if status != "proven" or evidence not in CATALOGUE:
                continue
            mutant = CATALOGUE[evidence]
            method = getattr(TestMutantCatalogue,
                             "test_mutant_%s" % evidence, None)
            red = getattr(method, "__unittest_expecting_failure__", False)

            def fires(spec: FindingSpec | Silence, check: str = name) -> bool:
                """Whether this expectation asserts `check` firing.

                Args:
                    spec: A mutant's or neighbour's expectation.
                    check: The detector name the row is about.

                Returns:
                    True for a `FindingSpec` naming that check.
                """
                return isinstance(spec, FindingSpec) and spec.check == check

            live = fires(mutant.neighbour.expect) or (
                not red and fires(mutant.expect))
            with self.subTest(detector=name):
                self.assertTrue(
                    live,
                    "%s is reported proven by %r, but that entry's only "
                    "claim about it is a RED expectation — which asserts "
                    "the check is SILENT" % (name, evidence))

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
        non-CATALOGUE red classes — `HAND_AUTHORED_RED_CLASSES`, and it
        is that constant rather than a list retyped here, because this
        very sentence has gone stale three times — never reach
        `_register` and have no expectation objects to compare, so
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

    def test_the_hand_authored_red_classes_are_the_ones_that_exist(self
                                                                   ) -> None:
        """Curator batch 16 item 4 (Task 21 §8.4), 2026-08-14.

        The guard above discloses a residual gap by naming the classes it
        cannot cover, and the `CATALOGUE` section comment enumerates the
        same classes with per-class counts. Both were hand transcriptions
        of a fact the interpreter can be asked for directly, and both had
        drifted three times — the file's own note says nothing in the
        suite notices, which is what this is.

        The enumeration is now derived and compared, so a red that flips
        (or a new one that lands) fails HERE with the corrected list in
        the message, instead of leaving two paragraphs quietly describing
        a suite that no longer exists. That is the whole claim: it makes
        the disclosure honest, it does not extend the dedupe fingerprint
        to these classes — see `HAND_AUTHORED_RED_CLASSES` on why those
        are different jobs, and feature-backlog row 27 for the second.
        """
        found = red_bearing_classes()
        self.assertEqual(
            {k: v for k, v in found.items() if k != CATALOGUE_RED_CLASS},
            HAND_AUTHORED_RED_CLASSES,
            "the hand-authored reds in this file have moved: measured %s "
            "against a declared %s. Update HAND_AUTHORED_RED_CLASSES, the "
            "residual-gap paragraph in "
            "test_no_two_mutants_encode_the_same_defect, and the counts in "
            "the CATALOGUE section comment — they are one fact stated in "
            "three places, which is why this drifts"
            % ({k: v for k, v in sorted(found.items())
                if k != CATALOGUE_RED_CLASS}, HAND_AUTHORED_RED_CLASSES))

    def test_the_catalogue_reds_are_the_ones_declared(self) -> None:
        """Curator batch 22 item 3 (Task 56 §8.1), 2026-08-15.

        The other half of the guard above, and the half the census kept
        getting wrong. `HAND_AUTHORED_RED_CLASSES` has covered the
        non-catalogue reds since batch 16; the CATALOGUE reds stayed a
        list hand-copied into SESSION-HANDOVER.md, which went stale four
        recorded times — the last of them omitting an entry while the
        sentence above it counted a different total.

        Deriving them here means the handover can cite ONE checked name
        instead of transcribing eight ids, and a batch that both flips a
        red and adds one — which is the motion that keeps the total right
        and the list wrong — fails here with both sets in the message.

        This does not duplicate the flip contract. unittest fails on a
        red that started PASSING; nothing failed on a red that was ADDED,
        and an addition is what left the census stating six where seven
        was true.
        """
        found = catalogue_red_ids()
        self.assertEqual(
            found, CATALOGUE_RED_IDS,
            "the catalogue's red-by-intent entries have moved: measured "
            "%s against a declared %s. Update CATALOGUE_RED_IDS and the "
            "'What is red' section of SESSION-HANDOVER.md, which cites it "
            "by name rather than restating it"
            % (sorted(found), sorted(CATALOGUE_RED_IDS)))

    def test_the_handover_transcribes_the_reds_it_declares(self) -> None:
        """Curator batch 23 item 1 (task 45 §7.1), 2026-08-15.

        The reach the guard above was described as having. Its comment
        promised that naming ids "forces the handover's prose through the
        same edit"; it compares a constant to the decorators and never
        opens the file, so on 2026-08-15 it stood green over a table
        listing six of eight — the FIFTH staleness of that transcription
        and the first with a guard watching it.

        The general lesson, and the reason this is a separate test rather
        than a line added to its sibling: those are two different facts.
        One says the declared set matches the code; this says the
        transcribed set matches the declaration. A guard proves the
        property it evaluates and no part of the property its author had
        in mind, and the distance between those two is invisible from
        inside the guard — it can only be closed by writing the second
        assertion down.

        The cheaper alternative was real and was rejected: the table
        could simply be deleted, since §1a already says both halves are
        derived and names where to read them. It stays because a reader
        opening the handover to learn what is red should not have to run
        the suite to find out, and a transcription that is CHECKED costs
        one line per flip — the same line the flip already costs.
        """
        transcribed = handover_catalogue_reds()
        self.assertEqual(
            transcribed, catalogue_red_ids(),
            "SESSION-HANDOVER.md's catalog-reds table lists %s while the "
            "live decorators say %s. The table is a hand copy and this is "
            "the fifth time it has drifted — edit the row, or delete the "
            "row and this test together"
            % (sorted(transcribed), sorted(catalogue_red_ids())))

    def test_the_handover_transcribes_the_coverage_totals(self) -> None:
        """Curator batch 23's reconciliation pass, 2026-08-15.

        The third census hand copy to be given a guard, after the
        non-catalogue red classes (batch 16) and the catalogue red ids
        (batch 22, reach fixed by this batch's item 1). Found by
        re-measuring rather than by being told: the handover claimed 18
        detectors and 15 proven where `coverage_table` reports 19 and 16,
        having missed `label_on_foreign_node`'s arrival with the curves
        fold-in.

        WHAT THIS DOES NOT MEAN, since a stale coverage figure sounds
        worse than it is: no detector was ever unproven without saying
        so. `test_every_detector_is_proven_or_named` is the gate and it
        held throughout. The failure was confined to the transcription —
        which matters because the transcription is what a reader who has
        not run the suite actually reads, and it had been wrong for the
        whole life of the curves fold-in.

        Three of these guards now exist and they were all written the
        same way, which is the pattern worth naming: a derived fact gets
        copied into prose for readability, the copy is correct on the day
        it is written, and nothing connects the two again. The fix is
        never to delete the prose — people need it — but to make the copy
        an assertion.
        """
        stated = handover_coverage_totals()
        rows = coverage_table()
        live = (len(rows),
                sum(1 for _n, status, _e in rows if status == "proven"),
                sum(1 for _n, status, _e in rows if status == "render-tier"),
                sum(1 for _n, status, _e in rows if status == "UNCOVERED"))
        self.assertEqual(
            stated, live,
            "SESSION-HANDOVER.md states (detectors, proven, render-tier, "
            "UNCOVERED) = %s while coverage_table() reports %s. Update the "
            "sentence; this is a hand copy of a derived fact and it has "
            "drifted before" % (stated, live))

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

        45 -> 46 on 2026-08-14 (v0.9 WP4, task 17): the
        `label_overflows_shape` warning. It needs no `UNCOVERED` row —
        that ledger is for templates with nothing proving them, and this
        one arrived with a `DETECTORS` entry and a proving mutant
        (`diamond_label_overflows_shape`) in the same change, which is
        the outcome this pin exists to insist on rather than the drift
        it exists to catch.

        46 -> 47 on 2026-08-14 (v0.9 WP4, task 19): a THIRD tier on the
        crosses-through template, for a run drawn flat along the bound
        node's own border (`r5-5`). No new row here either, and for a
        different reason than above — this is not a new template but a
        new channel for one `DETECTORS` already covers by `lint_re`, and
        the regex matches both wordings, which
        `TestBorderCollinearExit` in tests/test_backend.py proves from
        both poles.

        47 -> 49 on 2026-08-14 (v0.9 WP4, task 23): the near-miss
        clearance note, and a SECOND arm on the text/node overlap loop —
        the same measurement, worded for a text with no role. Both are
        new templates and neither needs an `UNCOVERED` row: each arrived
        with a `DETECTORS` entry and a proving mutant in this change,
        which is what drained `text_overlaps_node` and `min_clearance`
        out of `ASPIRATIONAL`. The same change also drained the
        `annotation_overlaps_node` ROW from `UNCOVERED` without adding a
        site — that template is five rounds old and only now has
        something asserting it.

        49 -> 50 on 2026-08-14 (v0.9 WP4, task 24): WP4b's e15
        degenerate-arrow warning. ONE site for four arms, deliberately —
        the faults co-occur on a broken path and the repair is one edit,
        so the message lists them and the ledger gets one row. That row
        IS added (`degenerate_arrow`), and it is the first here added by
        the change that created the template rather than by the
        2026-08-12 enumeration, which is the outcome this pin exists to
        produce.

        50 -> 51 on 2026-08-14 (v0.9 WP4, task 24): WP4b's e1 phantom
        pass-through warning. No `UNCOVERED` row for this one — it
        arrived with a `DETECTORS` entry and flipped
        `phantom_passthrough_shared_attach` in the same change, which
        also drained `phantom_passthrough` from `ASPIRATIONAL`.
        """
        src = inspect.getsource(canvas.lint_layout)
        sites = sum(src.count("%s.append" % chan)
                    for chan in ("errors", "warnings", "notes"))
        self.assertEqual(sites, 51,
                         "canvas.py lint_layout append-site count changed "
                         "(51 -> %d): re-enumerate the UNCOVERED ledger "
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
    # RE-DISPOSITIONED 2026-08-14 (v0.9 WP4, task 24), promote -> allow.
    # The promotion was DELIVERED: the curated mutant exists
    # (`phantom_passthrough_shared_attach`) and the WP4b e1 lint landed in
    # canvas.py. The plan expected this survivor to die with it and
    # `test_live_sweep_reproduces_the_record` to fail loudly as the flip
    # signal for a discovery finding. It did not, and that is the result
    # rather than a miss: the survivor and the mutant are the two HALVES of
    # e1, and only one of them ships.
    #
    # `move_node_onto_rank` puts N's centreline on the rank and leaves e1's
    # foot on N's left border and e2's on its right — collinear, opposed,
    # 80px apart across an 80px node, so no ink touches the box. Reporting
    # that is the literature scan's broad criterion, and it was implemented
    # and measured before being cut: 70 findings across the 24 frozen
    # artifacts, i.e. every chained node of every correct left-to-right
    # flow, with a remedy ("offset one line from the other") that would
    # make each of those drawings worse. The half that ships is the one
    # with ink behind it, which is why the mutant's scene fires and this
    # cell stays quiet.
    #
    # RE-WORDED 2026-08-15 (curator batch 21 item 7, user ruling) — the
    # re-open condition only. The verdict, its reason and the 70 stand.
    # The old condition was "a perceptual result that separates the two",
    # and `spike-e1-perceptual.md` showed it is not a decision procedure:
    # the survivor and the 70 are the SAME configuration on every cue
    # measured (ink 0, terminators present, collinear, abutting, and the
    # survivor sits at the 27th percentile of narrowness), so a study
    # could only condemn all 71 or exonerate all 71 — neither of which
    # moves this cell. A condition no result can meet is a placeholder,
    # and a placeholder in a disposition is the undispositioned state
    # wearing a verdict's clothes. The replacement keys on an observation
    # instead: it costs nothing (the cold-look rounds run anyway), it has
    # already been run with a negative result (29 instance-views carrying
    # 21 of the 70 across two independent cold observers, zero reports,
    # on a channel that reported the neighbouring ink case as r5-5), and
    # unlike a study it can actually come back positive.
    "move_node_onto_rank:chain:ebb2e1f6": (
        "allow",
        "e1 phantom pass-through, BROAD half — collinear in/out on "
        "opposite borders with the node's full width between them. "
        "Deliberately unflagged: measured at 70 findings over 24 shipped "
        "artifacts, so it describes the normal drawing rather than a "
        "defect. The INK half is caught and curated "
        "(`phantom_passthrough_shared_attach`, canvas.py's e1 lint). "
        "Re-open if a cold observer reports a chained node on a shipped "
        "drawing reading as a pass-through — the node skipped, the "
        "relation read straight from its predecessor to its successor"),
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
