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

import re
import sys
import unittest
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

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

        Args:
            findings: The normalized findings list from `collect_findings`.

        Returns:
            None if silent; otherwise a description of the first finding
            that broke the silence.
        """
        hits = [f for f in findings if f["check"] == self.check]
        if not hits:
            return None
        return "expected silence on check=%r but it fired: %s" % (
            self.check, hits[0]["raw"])


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


# Registered mutation operators; populated by Task 3. Left empty here so
# Mutant's op guard is inert until then (see the guard's docstring).
OPERATORS: dict = {}


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
            not a registered operator (once Task 3 populates `OPERATORS`
            — until then this guard is inert).
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
        if op not in OPERATORS if OPERATORS else False:
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


if __name__ == "__main__":
    unittest.main()
