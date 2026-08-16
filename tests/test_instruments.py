"""Tests for the geometry instruments: the ports and the score vector.

Two classes of assertion live here. `TestInstrumentPorts` pins the ported
detectors' behavior; as of v0.9 WP4 none of the ported bugs is preserved
any longer, and the mutation catalogue (test_mutants.py) is what holds
each fix to its magnitude. Everything below it covers WP4's rebuild: the
rendered-path flattening, the fixed crossing counter, and one test per
metric in the score vector.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import instruments
from tests_helpers import el

sys.path.insert(0, str(Path(__file__).resolve().parents[1] /
                       "skills" / "wysiwyg-grilling" / "scripts"))
import canvas


def _node(nid: str, cx: float, cy: float, w: float = 100, h: float = 60,
          kind: str = "rectangle") -> dict:
    """Build a node shape centered on a given point.

    Args:
        nid: Element id.
        cx: Center x.
        cy: Center y.
        w: Box width.
        h: Box height.
        kind: Element type.

    Returns:
        A node element dict carrying `customData.role == "node"`.
    """
    return el(id=nid, type=kind, x=cx - w / 2.0, y=cy - h / 2.0,
              width=w, height=h, customData={"role": "node"})


def _edge(eid: str, x: float, y: float, pts: list[list[float]],
          **kw: object) -> dict:
    """Build an arrow whose box is derived from its own points.

    Args:
        eid: Element id.
        x: Arrow origin x.
        y: Arrow origin y.
        pts: Stored points, relative to the origin.
        **kw: Extra element attributes (`roundness`, bindings).

    Returns:
        An arrow element dict carrying `customData.role == "edge"`.
    """
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return el(id=eid, type="arrow", x=x, y=y, points=pts,
              width=max(xs) - min(xs), height=max(ys) - min(ys),
              customData={"role": "edge"}, **kw)


def _clean_scene() -> list[dict]:
    """Two aligned nodes joined by one straight arrow.

    Returns:
        A scene that scores 1.0 on every headline metric.
    """
    return [_node("n1", 300, 100), _node("n2", 300, 400),
            _edge("e1", 300, 130, [[0, 0], [0, 240]])]


class TestInstrumentPorts(unittest.TestCase):
    """The ported detectors run, and read what the drawing actually shows."""

    def _crossing_scene(self) -> list[dict]:
        """A flat arrow crossed four times by one zigzag arrow.

        The zigzag's four legs each cut the flat arrow's y=100 line once;
        the count is reviewer-verified by brute-force proper-intersection
        count over all 4x1 segment pairs (see
        `test_mutants._crossing_scene`, which builds the same scene).

        Returns:
            The two-arrow scene as a list of element dicts.
        """
        flat = el(id="a1", type="arrow", x=100, y=100, width=760, height=0,
                  points=[[0, 0], [760, 0]], customData={"role": "edge"})
        zig = el(id="a2", type="arrow", x=150, y=40, width=400, height=120,
                 points=[[0, 0], [100, 120], [200, 0], [300, 120], [400, 0]],
                 customData={"role": "edge"})
        return [flat, zig]

    def test_crossing_counter_counts_crossings_not_pairs(self) -> None:
        """Four crossings between one pair of arrows count four."""
        # The ported counter `break`s out of both segment loops on a
        # pair's first hit and scored 1 here, so a router change that
        # turned one crossing into four registered as no change at all.
        count, pairs = instruments.edge_crossings(self._crossing_scene())
        self.assertEqual(count, 4)
        self.assertEqual(pairs, [("a1", "a2")])

    def test_corridor_fires_on_collinear_overlap(self) -> None:
        """Two parallel, overlapping horizontal arrows read as a corridor."""
        a = el(id="b1", type="arrow", x=100, y=200, width=400, height=0,
               points=[[0, 0], [400, 0]], customData={"role": "edge"})
        b = el(id="b2", type="arrow", x=200, y=200, width=400, height=0,
               points=[[0, 0], [400, 0]], customData={"role": "edge"})
        hits = instruments.shared_corridors([a, b])
        self.assertEqual(len(hits), 1)
        self.assertGreaterEqual(hits[0]["overlap"], 290)

    def test_float_diamond_flags_a_dead_center_endpoint(self) -> None:
        """A dead-center endpoint is flagged, at its distance to the facet."""
        # The ported radial measure returned gap 0 here (the `t == 0`
        # guard returned r, which is 0 at the center), so the worst
        # possible binding never crossed the 12px threshold. WP4 (task
        # 16) measures perpendicular to the facet instead: the rhombus
        # is 200x100, so the center sits 1/sqrt(1/100^2 + 1/50^2) =
        # 44.72px from the outline, and 44.72 has no way to be 0.
        node = el(id="d1", type="diamond", x=100, y=100, width=200,
                  height=100, customData={"role": "node"})
        arrow = el(id="ar1", type="arrow", x=0, y=0, width=200, height=150,
                   points=[[0, 0], [200, 150]],
                   endBinding={"elementId": "d1", "focus": 0, "gap": 0},
                   customData={"role": "edge"})
        hits = instruments.float_diamond([node, arrow])
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["arrow"], "ar1")
        self.assertAlmostEqual(hits[0]["gap"], 44.721, places=3)

    def test_float_diamond_is_silent_on_a_facet_midpoint(self) -> None:
        """An endpoint exactly on the outline is bound, and stays unremarked.

        The other pole, and the one the fix could have broken: a measure
        that flags the center by flagging everything is not a measure.
        """
        node = el(id="d1", type="diamond", x=100, y=100, width=200,
                  height=100, customData={"role": "node"})
        # (150,125) is the top-left facet's midpoint: 50/100 + 25/50 == 1
        arrow = el(id="ar1", type="arrow", x=0, y=0, width=150, height=125,
                   points=[[0, 0], [150, 125]],
                   endBinding={"elementId": "d1", "focus": 0, "gap": 0},
                   customData={"role": "edge"})
        self.assertEqual(instruments.float_diamond([node, arrow]), [])


class TestRenderedPath(unittest.TestCase):
    """Flattening reproduces the curve the renderer actually draws."""

    def _curved_elbow(self) -> dict:
        """The curved elbow from the `curved_elbow_spurious_bidi` mutant.

        Returns:
            A rounded three-point arrow whose last span bows off its
            chord.
        """
        return _edge("ea", 150, 282, [[0, 0], [100, 0], [100, 18]],
                     roundness={"type": 2})

    def test_sharp_arrow_flattens_to_its_own_chords(self) -> None:
        """Without roundness the rendered path is the stored polyline."""
        a = _edge("s1", 10, 20, [[0, 0], [100, 0], [100, 50]])
        self.assertEqual(instruments.rendered_stretches(a),
                         [[(10, 20), (110, 20)], [(110, 20), (110, 70)]])
        self.assertEqual(instruments.rendered_path(a),
                         [(10, 20), (110, 20), (110, 70)])

    def test_curved_span_matches_the_renderers_control_points(self) -> None:
        """The sampled curve is roughjs' Catmull-Rom chain, to the pixel."""
        # Excalidraw draws a rounded arrow with roughjs `generator.curve`,
        # which pads the point list by DUPLICATING each endpoint. For this
        # elbow that puts the final span's controls at (266.67, 285) and
        # (250, 297) — verified by running the bundled roughjs — so the
        # curve stands 7.4px off the x=250 chord at its widest.
        span = instruments.rendered_stretches(self._curved_elbow())[-1]
        self.assertEqual(span[0], (250.0, 282.0))
        self.assertEqual(span[-1], (250.0, 300.0))
        x, y = span[12]                       # t = 0.6
        self.assertAlmostEqual(x, 254.8, places=2)
        self.assertAlmostEqual(y, 293.23, places=2)
        xs = [p[0] for p in span]
        self.assertAlmostEqual(max(xs) - min(xs), 7.394, places=3)

    def test_rendered_path_reports_each_joint_once(self) -> None:
        """Concatenating the spans does not duplicate their shared points."""
        arrow = self._curved_elbow()
        spans = instruments.rendered_stretches(arrow)
        path = instruments.rendered_path(arrow)
        self.assertEqual(len(path), sum(len(s) for s in spans) - 1)
        self.assertEqual(path[0], spans[0][0])
        self.assertEqual(path[-1], spans[-1][-1])

    def test_false_bidi_reads_the_rendered_path_not_the_chord(self) -> None:
        """Curved elbows that visibly separate stop reading as one line."""
        # Both pairs store the same chords; only the drawing differs.
        sharp = [_edge("ea", 150, 282, [[0, 0], [100, 0], [100, 18]]),
                 _edge("eb", 150, 320, [[0, 0], [100, 0], [100, -18]])]
        curved = [dict(e, roundness={"type": 2}) for e in sharp]
        self.assertEqual(instruments.false_bidi(sharp),
                         [{"a": "ea", "b": "eb"}])
        self.assertEqual(instruments.false_bidi(curved), [])


class TestCorridorKind(unittest.TestCase):
    """v0.9 WP4 task 24: one geometry, two defects, told apart.

    `shared_corridors` answers whether two runs are collinear and
    overlapping, and that same answer covers a chain whose node has been
    deleted from the reading and a fan whose edges have not separated
    yet. The corpus is lopsided enough that the raw count misleads: all
    6 findings across the 24 frozen artifacts are fans.
    """

    def _bind(self, eid: str, x: float, y: float, pts: list[list[float]],
              src: str | None, dst: str | None) -> dict:
        """An arrow with explicit bindings at both ends.

        Args:
            eid: Element id.
            x: The arrow's x origin.
            y: The arrow's y origin.
            pts: The arrow's points, relative to the origin.
            src: Start binding target id, or None.
            dst: End binding target id, or None.

        Returns:
            The arrow element.
        """
        arrow = _edge(eid, x, y, pts)
        if src:
            arrow["startBinding"] = {"elementId": src, "focus": 0, "gap": 1}
        if dst:
            arrow["endBinding"] = {"elementId": dst, "focus": 0, "gap": 1}
        return arrow

    def test_a_chain_shares_its_node_at_opposite_ends(self) -> None:
        """`X -> N`, `N -> Z`: the two runs continue each other."""
        els = [self._bind("e1", 80, 120, [[0, 0], [120, 0]], "A", "N"),
               self._bind("e2", 200, 120, [[0, 0], [328, 0]], "N", "Z")]
        hits = instruments.shared_corridors(els)
        self.assertEqual([h["kind"] for h in hits], ["chain"], hits)

    def test_a_fan_shares_its_node_at_the_same_end(self) -> None:
        """Two edges out of one source, still together 100px later.

        Same collinearity, same overlap, and the repair is the auto-fan
        rather than a re-route — which is why reporting the geometry
        without the relation makes the two indistinguishable.
        """
        els = [self._bind("e1", 80, 120, [[0, 0], [200, 0]], "A", "N"),
               self._bind("e2", 80, 122, [[0, 0], [200, 0]], "A", "Z")]
        hits = instruments.shared_corridors(els)
        self.assertEqual([h["kind"] for h in hits], ["fan"], hits)

    def test_unbound_arrows_on_one_line_are_neither(self) -> None:
        """No binding, no relation to name — the third answer, not a fan."""
        els = [_edge("b1", 100, 200, [[0, 0], [400, 0]]),
               _edge("b2", 200, 200, [[0, 0], [400, 0]])]
        hits = instruments.shared_corridors(els)
        self.assertEqual([h["kind"] for h in hits], ["unrelated"], hits)

    def test_no_frozen_artifact_contains_a_merged_stroke(self) -> None:
        """The census, as the claim it supports rather than as a count.

        Every corridor finding on the 24 committed artifacts is a fan.
        The exact total is deliberately not pinned — it moves whenever a
        fixture does, for reasons that say nothing about this
        instrument — but "none of them is a chain" is the whole basis
        for reading the corridor count as a fanning backlog rather than
        as merged strokes, and that must not go quiet.
        """
        root = Path(__file__).resolve().parent / "fixtures"
        kinds: list[str] = []
        for path in sorted(root.rglob("*.excalidraw")):
            doc = json.loads(path.read_text())
            for hit in instruments.shared_corridors(doc.get("elements") or []):
                kinds.append(hit["kind"])
                self.assertNotEqual(hit["kind"], "chain",
                                    "%s: %s" % (path.name, hit))
        self.assertTrue(kinds, "the fixture corpus produced no corridors "
                               "at all — this census has gone vacuous")

    def test_the_classification_survives_aggregation(self) -> None:
        """A chain is still a chain after `enumerate_defects` folds it in.

        `enumerate_defects` stamps every hit with an outer defect class
        under `"kind"`, and `shared_corridors` is the one source whose
        own dicts already use that key — so the fold overwrote
        chain/fan/unrelated with the literal `"shared_corridor"` and the
        three tests above became unreadable one function later. The loss
        was total and silent: `score_layout` embeds this list verbatim
        and `compare_layouts` builds on that, so the distinction between
        a merged stroke and an unfanned pair vanished from every score
        the harness reports. Asserting the outer label too is deliberate
        — the sub-classification must ride ALONGSIDE it, not replace it,
        or the consumers that discriminate on `"kind"` break instead.
        """
        els = [self._bind("e1", 80, 120, [[0, 0], [120, 0]], "A", "N"),
               self._bind("e2", 200, 120, [[0, 0], [328, 0]], "N", "Z")]
        found = [d for d in instruments.enumerate_defects(els)
                 if d["kind"] == "shared_corridor"]
        self.assertEqual([d.get("corridor_kind") for d in found], ["chain"],
                         found)
        scored = [d for d in instruments.score_layout(els)["defects"]
                  if d["kind"] == "shared_corridor"]
        self.assertEqual([d.get("corridor_kind") for d in scored], ["chain"],
                         scored)


class TestFalseBidiOnTheShippedFan(unittest.TestCase):
    """The one live `false_bidi` the sharp era uncovers, and what it is.

    CURATOR DISPOSITION, batch 21, 2026-08-15 — recorded rather than
    pinned red, because after measuring it the finding STANDS: the
    geometry is exactly what the check reports, and the drawing it
    reports on is correct. `spike-stage3-flare.md` §8 flagged
    `t-agg-dashboard`/`t-agg-reports` as "a live stage-2 routing defect
    nobody has looked at". It is not a routing defect.

    What is actually drawn: `aggregator` (x 1380-1540) fans east to
    `dashboard` (y 160-224) and `reports` (y 320-384), which share an x
    range, so both edges leave aggregator's right border 22px apart, run
    260px east to x=1800 — the shared centre-line of both targets — and
    turn outward into each target's near border. That is the textbook
    way to draw one source and two vertically stacked targets, and the
    two final legs are collinear only BECAUSE the targets are aligned.
    The remedy a finding here implies — offset one line from the other —
    would break the alignment and make the picture worse, which is the
    same argument that retired the e1 broad criterion.

    Three things bound the harm, and all three were checked rather than
    assumed. `false_bidi` has NO counterpart in `canvas.py`: it lives
    only in these instruments, feeding `score_layout` and
    `compare_layouts`, so no agent and no user is ever told to repair
    this drawing.

    THE FIRST CLAUSE EXPIRED ON 2026-08-16 (v0.9 TASK-LINTPROMOTE) AND
    THE CONCLUSION SURVIVED IT, which is worth reading as one fact
    rather than two. There IS a counterpart now — `lint_layout`'s
    `false_bidirectional` — so the ground this disposition stood on is
    gone. What replaced it is the discriminator the last paragraph below
    asks for: `canvas._pair_kind` classifies the pair, and the live lint
    is SILENT on a fan. So no agent is still told to repair this
    drawing, but for a reason that has to hold rather than for a reason
    nobody could break by accident. `opposed_finals_reach_the_agent_but_
    a_fan_does_not`'s neighbour is what now carries it: same geometry,
    one binding moved, and a check that read the shape alone fails
    there. If that pole ever goes, this artifact is the first thing an
    agent gets nagged about.

    The shipped corpus is curved, and curved it does not
    fire at all (1 finding corpus-wide, the `argus-domain` pair the
    catalogue already holds); it appears only if the sharp era ships,
    and then the corpus total is 2. And the check's own docstring
    already says its thresholds are engineering choices rather than
    measured ones and that a finding is a question to put to the
    drawing, not a verdict on it.

    What the check genuinely lacks is the discriminator: it is pure
    geometry and cannot tell a swapped A->B/B->A pair — the premise it
    is written for — from a fan out of one source whose branches happen
    to align, where the symmetry a merged reading would assert is
    between two nodes neither arrow connects. `shared_corridors` draws
    exactly that distinction with `_corridor_kind`, and this check has
    no equivalent. That is a proposal for whoever owns the instrument,
    not a defect in the drawing, and the test below exists so the fact it
    rests on cannot go quiet: this finding is a FAN.

    THE PROPOSAL WAS TAKEN, on the other side of the line. v0.9
    TASK-LINTPROMOTE built `canvas._pair_kind` — the same distinction,
    split five ways — and the LIVE lint discriminates on it. The
    INSTRUMENT deliberately does not: it still reports this pair, and it
    should, because it answers "is this geometry present" for a score
    vector, while the lint answers "should the agent be told". Two
    questions, two answers, and this test is still what keeps the fact
    the disposition rests on from going quiet.
    """

    def _sharp_run_flow(self) -> list[dict]:
        """`argus-run-flow` with every arrow's roundness stripped.

        Returns:
            The fixture's elements, sharp — the geometry the era switch
            would ship, and the only one in which this pair fires.
        """
        path = (Path(__file__).resolve().parent / "fixtures" /
                "argus-r4-arm3" / "artifacts" / "argus-run-flow.excalidraw")
        els = json.loads(path.read_text())["elements"]
        for e in els:
            if e.get("type") == "arrow":
                e["roundness"] = None
        return els

    def test_the_finding_is_a_fan_and_not_a_swapped_pair(self) -> None:
        """Both arrows leave the same node; neither is the other's reverse.

        The whole disposition rests on this. A swapped pair would be the
        check's own premise holding on a real drawing and would deserve a
        fix; a fan is two edges of one relation set that the layout has
        aligned, and the merged reading it warns about would assert a
        relation between `dashboard` and `reports` that no arrow in the
        artifact draws.
        """
        els = self._sharp_run_flow()
        ix = {e["id"]: e for e in els}
        hits = instruments.false_bidi(els)
        self.assertEqual([(h["a"], h["b"]) for h in hits],
                         [("t-agg-dashboard", "t-agg-reports")], hits)
        ends = [((ix[h[k]].get("startBinding") or {}).get("elementId"),
                 (ix[h[k]].get("endBinding") or {}).get("elementId"))
                for h in hits for k in ("a", "b")]
        self.assertEqual(ends, [("aggregator", "dashboard"),
                                ("aggregator", "reports")])

    def test_the_shipped_curved_artifact_reports_nothing(self) -> None:
        """As committed, this artifact is silent — the finding is era-bound.

        Kept beside the census so nobody reads the row above as a live
        complaint about a shipped drawing. It is what the sharp switch
        would reveal, and it is why the switch is the event that should
        re-read this disposition rather than a bug filed against today.

        Reads the file RAW, deliberately: this is a statement about the
        bytes on disk, and routing it through the loader would measure
        `gate_curvature`'s verdict instead — which is the next test.
        """
        path = (Path(__file__).resolve().parent / "fixtures" /
                "argus-r4-arm3" / "artifacts" / "argus-run-flow.excalidraw")
        els = json.loads(path.read_text())["elements"]
        self.assertEqual(instruments.false_bidi(els), [])

    def test_the_gate_leaves_this_pair_sharp_so_the_finding_survives(
            self) -> None:
        """The switch arrived, and this disposition survives it intact.

        THE F1 REGRESSION PIN. The docstring above says the era switch
        is "the event that should re-read this disposition", and v0.9
        Task 57 is that event. The first cut of stage 3 curved every
        elbow unconditionally, and on this pair that took `argus-run-flow`
        from one enumerated defect to zero — not by fixing the routing,
        which is untouched, but by bowing the two strokes far enough
        apart that they stop reading as one line. No check in the repo
        could tell those apart, which is precisely what
        `spike-stage3-flare.md` predicted and why it recommended against
        the switch.

        `gate_curvature` is the answer: both arrows arrive 40.6 degrees
        off square once curved, past `NEAR_AXIS`, so the gate declines
        them and the drawing keeps the geometry the finding is about.
        This asserts the whole chain through the REAL load path — the
        verdict, and the finding it protects — because either half alone
        would pass while the other rotted. A future change that curves
        this pair for any reason fails here.
        """
        path = (Path(__file__).resolve().parent / "fixtures" /
                "argus-r4-arm3" / "artifacts" / "argus-run-flow.excalidraw")
        els = canvas.rebuild_bound_elements(
            [e for e in json.loads(path.read_text())["elements"]
             if not e.get("isDeleted")])
        ix = {e["id"]: e for e in els}
        for aid in ("t-agg-dashboard", "t-agg-reports"):
            self.assertTrue(canvas.server_owns_geometry(ix[aid]),
                            "%s stopped being server-owned, so the gate "
                            "never judged it and this pin is vacuous" % aid)
            self.assertIsNone(ix[aid].get("roundness"),
                              "%s curved: the gate accepted an arrival "
                              "40.6 degrees off square" % aid)
        self.assertEqual(
            [(h["a"], h["b"]) for h in instruments.false_bidi(els)],
            [("t-agg-dashboard", "t-agg-reports")],
            "the fan the disposition above describes went silent under "
            "the loader — curvature hid it rather than the router fixing "
            "it (review F1)")


class TestTiltBand(unittest.TestCase):
    """v0.9 WP4 task 24: the straightness band scales with the span.

    `long_run_curve_hides_bidi` proves the consequence at the catalogue
    level — a 200px approach reports the bidi its 18px twin correctly
    hides. These pin the rule underneath it, which the mutant pair can
    only sample at two points.
    """

    def _bowed(self, extent: float, bow: float) -> list[tuple[float, float]]:
        """A vertical stretch of `extent` bowing `bow` px off its axis.

        Args:
            extent: On-axis (y) length of the stretch.
            bow: Off-axis (x) displacement at the midpoint.

        Returns:
            A three-point stretch in absolute coordinates.
        """
        return [(250.0, 0.0), (250.0 + bow, extent / 2.0), (250.0, extent)]

    def test_the_two_rules_agree_at_the_bands_own_crossover(self) -> None:
        """At 28px the ratio is the ported 2px band, exactly.

        28px is the shortest final span in the frozen corpus, which is
        where the constant was set so that nothing shorter changes
        behaviour at all.
        """
        self.assertAlmostEqual(instruments.TILT_RATIO * 28.0,
                               instruments.FLAT_BAND, places=9)

    def test_below_the_crossover_the_flat_band_still_rules(self) -> None:
        """A 2.5px bow on an 18px span is a curve, as it always was."""
        self.assertTrue(instruments._reads_as_line(self._bowed(18, 1.9), 1))
        self.assertFalse(instruments._reads_as_line(self._bowed(18, 2.5), 1))

    def test_above_it_the_same_bow_reads_as_a_line(self) -> None:
        """7.4px is 41% of an 18px approach and 3.7% of a 200px one."""
        self.assertFalse(instruments._reads_as_line(self._bowed(18, 7.4), 1))
        self.assertTrue(instruments._reads_as_line(self._bowed(200, 7.4), 1))

    def test_the_band_never_narrows(self) -> None:
        """One-directional by construction: `false_bidi` cannot lose a hit.

        Every stretch the flat 2px band admitted is still admitted at
        every length, which is the property that lets this land as a
        blind-spot closure with no over-fire surface to argue about.
        """
        for extent in (1, 4, 18, 28, 100, 200, 668):
            self.assertTrue(
                instruments._reads_as_line(self._bowed(extent, 2.0), 1),
                extent)


class TestScoreVector(unittest.TestCase):
    """Each headline metric, and the [0, 1] contract they all share."""

    def test_every_metric_is_normalized_with_one_meaning_good(self) -> None:
        """Metrics are the weighted keys, all in [0, 1], best-case 1.0."""
        for scene in (_clean_scene(), _crowded_scene(), []):
            metrics = instruments.score_layout(scene)["metrics"]
            self.assertEqual(set(metrics), set(instruments.WEIGHTS))
            for name, value in metrics.items():
                self.assertGreaterEqual(value, 0.0, name)
                self.assertLessEqual(value, 1.0, name)
        best = instruments.score_layout(_clean_scene())["metrics"]
        self.assertEqual(sorted(set(best.values())), [1.0])

    def test_weights_are_written_down_and_sum_to_one(self) -> None:
        """The weighted sum's weights are declared, not implicit."""
        self.assertAlmostEqual(sum(instruments.WEIGHTS.values()), 1.0)

    def test_gridiness_counts_centers_within_six_px(self) -> None:
        """Alignment is center-based with a 6px tolerance, per Kieffer."""
        def scene(last_cx: float) -> list[dict]:
            """Four nodes, three of them sharing a center column.

            Args:
                last_cx: Center x of the fourth node.

            Returns:
                The four-node scene.
            """
            return [_node("n1", 100, 100), _node("n2", 100, 300),
                    _node("n3", 106, 500), _node("n4", last_cx, 700)]
        # n4 6px off n3's column still counts as aligned; 7px does not
        self.assertEqual(instruments.gridiness(scene(112)), 1.0)
        self.assertEqual(instruments.gridiness(scene(113)), 0.75)

    def test_gridiness_is_vacuous_below_two_nodes(self) -> None:
        """A drawing with one node has nothing to misalign."""
        self.assertEqual(instruments.gridiness([_node("n1", 0, 0)]), 1.0)

    def test_bends_report_mean_and_max_with_a_free_allowance(self) -> None:
        """Bends are counted per arrow and scored with slack for intent."""
        scene = [_edge("e1", 0, 0, [[0, 0], [100, 0], [100, 100],
                                    [200, 100]]),
                 _edge("e2", 0, 300, [[0, 0], [200, 0]])]
        self.assertEqual(instruments.bend_counts(scene), [2, 0])
        s = instruments.score_layout(scene)
        self.assertEqual(s["diagnostics"]["bends_mean_raw"], 1.0)
        self.assertEqual(s["diagnostics"]["bends_max_raw"], 2.0)
        self.assertAlmostEqual(s["metrics"]["bends_mean"], 0.5)
        # a couple of deliberate bends on one edge cost nothing: the best
        # hand layouts in the corpus bend on purpose
        self.assertEqual(s["metrics"]["bends_max"], 1.0)

    def test_node_overlap_scores_the_nodes_involved(self) -> None:
        """Overlapping boxes are enumerated and cost their share."""
        scene = [_node("n1", 100, 100), _node("n2", 140, 120),
                 _node("n3", 100, 400), _node("n4", 100, 600)]
        hits = instruments.node_overlaps(scene)
        self.assertEqual([(h["a"], h["b"]) for h in hits], [("n1", "n2")])
        self.assertEqual(hits[0]["area"], 60 * 40)
        s = instruments.score_layout(scene)
        self.assertEqual(s["metrics"]["node_overlap"], 0.5)

    def test_flow_consistency_scores_the_dominant_axis(self) -> None:
        """A bottom-to-top drawing is as consistent as a top-to-bottom one."""
        # Never a hardcoded left-to-right preference: Figl & Strembeck
        # found no comprehension advantage for any direction, so what is
        # scored is agreement with whichever way the drawing already goes.
        up = [_edge("e%d" % i, i * 200, 500, [[0, 0], [10, -400]])
              for i in range(3)]
        score, axis = instruments.flow_consistency(up)
        self.assertEqual((score, axis), (1.0, "y"))
        down = [dict(a, points=[[0, 0], [10, 400]]) for a in up]
        self.assertEqual(instruments.flow_consistency(down), (1.0, "y"))
        mixed = [*up[:2], dict(up[2], points=[[0, 0], [10, 400]])]
        self.assertAlmostEqual(instruments.flow_consistency(mixed)[0], 2 / 3)

    def test_flow_consistency_picks_the_axis_with_the_travel(self) -> None:
        """A left-to-right drawing is scored on x, not on the other axis."""
        across = [_edge("e%d" % i, 0, i * 200, [[0, 0], [400, 10]])
                  for i in range(3)]
        self.assertEqual(instruments.flow_consistency(across), (1.0, "x"))

    def test_crossing_angle_separates_square_from_shallow(self) -> None:
        """A perpendicular crossing scores 1.0; a 0.6 degree one does not."""
        square = [_edge("e1", 0, 100, [[0, 0], [400, 0]]),
                  _edge("e2", 200, 0, [[0, 0], [0, 300]])]
        self.assertEqual(
            instruments.crossing_angle_score(
                instruments.crossing_sites(square)), 1.0)
        shallow = [_edge("e1", 0, 100, [[0, 0], [400, 0]]),
                   _edge("e2", 0, 98, [[0, 0], [400, 4]])]
        sites = instruments.crossing_sites(shallow)
        self.assertEqual(len(sites), 1)
        self.assertLess(sites[0]["angle"], instruments.SHALLOW_CROSSING_DEG)
        self.assertLess(instruments.crossing_angle_score(sites), 0.01)

    def test_crossing_angle_is_perfect_when_nothing_crosses(self) -> None:
        """No crossings is not a bad crossing angle."""
        self.assertEqual(instruments.crossing_angle_score([]), 1.0)

    def test_shallow_crossings_are_enumerated_as_their_own_defect(self) -> None:
        """A near-parallel crossing is named, not silently averaged away."""
        shallow = [_edge("e1", 0, 100, [[0, 0], [400, 0]]),
                   _edge("e2", 0, 98, [[0, 0], [400, 4]])]
        kinds = [d["kind"] for d in instruments.enumerate_defects(shallow)]
        self.assertEqual(sorted(kinds), ["crossing", "shallow_crossing"])

    def test_defect_list_survives_the_rebuild(self) -> None:
        """Every defect class the instruments detect is enumerated by name."""
        defects = instruments.enumerate_defects(_crowded_scene())
        self.assertTrue(defects)
        self.assertLessEqual({"crossing", "node_overlap"},
                             {d["kind"] for d in defects})

    def test_compactness_is_a_diagnostic_and_never_a_headline(self) -> None:
        """Compactness is reported, excluded from the vector, and unweighted."""
        # It is the literature's strongest predictor of human preference
        # and it picked the worse drawing in both of our own pairs — so it
        # is quoted to a reviewer and kept out of every verdict.
        s = instruments.score_layout(_clean_scene())
        self.assertIn("compactness", s["diagnostics"])
        self.assertNotIn("compactness", s["metrics"])
        self.assertNotIn("compactness", instruments.WEIGHTS)
        # two 100x60 nodes inside a 100x360 bbox
        self.assertAlmostEqual(s["diagnostics"]["compactness"],
                               12000 / 36000.0)


class TestWinnerDeclaration(unittest.TestCase):
    """A winner needs both readings, and a clean gate, to agree."""

    def test_agreeing_readings_declare_a_winner(self) -> None:
        """When count and weighted sum point the same way, they win."""
        r = instruments.compare_layouts(_clean_scene(), _crowded_scene(),
                                        labels=("clean", "crowded"))
        self.assertEqual(r["winner"], "clean")
        self.assertEqual(r["count_winner"], r["sum_winner"])
        self.assertEqual(set(r["vector"]), set(instruments.WEIGHTS))
        self.assertEqual(sum(r["wins"].values()) + r["ties"],
                         len(instruments.WEIGHTS))
        self.assertGreater(r["weighted"]["clean"], r["weighted"]["crowded"])

    def test_a_split_verdict_declares_no_winner(self) -> None:
        """A sum that outvotes the count is a result, not a tie-break."""
        a = _crossing_free_but_ragged()
        b = _tidy_but_crossed()
        weights = {"gridiness": 0.05, "bends_mean": 0.05, "crossings": 0.90}
        r = instruments.compare_layouts(a, b, labels=("ragged", "crossed"),
                                        weights=weights)
        # the tidy drawing takes 2 of the 3 metrics; the ragged one takes
        # the sum, because these weights care about almost nothing else
        self.assertEqual(r["count_winner"], "crossed")
        self.assertEqual(r["sum_winner"], "ragged")
        self.assertIsNone(r["winner"])
        self.assertIn("split verdict", r["reason"])

    def test_a_gate_failure_disqualifies_the_higher_score(self) -> None:
        """Outscoring the other drawing does not survive overlapping nodes."""
        r = instruments.compare_layouts(_crossing_free_but_ragged(),
                                        _overlapping_but_tidy(),
                                        labels=("ragged", "overlapped"))
        self.assertEqual(r["count_winner"], "overlapped")
        self.assertEqual(r["sum_winner"], "overlapped")
        self.assertIsNone(r["winner"])
        self.assertIn("node_overlap", r["reason"])
        self.assertEqual(r["gates"]["ragged"], [])

    def test_unknown_weights_are_refused(self) -> None:
        """A weight naming no metric is a typo, and typos stay loud."""
        with self.assertRaises(ValueError):
            instruments.compare_layouts(_clean_scene(), _clean_scene(),
                                        weights={"compactness": 1.0})

    def test_two_layouts_may_not_share_a_label(self) -> None:
        """Equal labels would collapse the readings into one column."""
        # Every per-label reading is keyed by label, so one name for two
        # drawings reports a drawing compared against itself.
        with self.assertRaises(ValueError) as caught:
            instruments.compare_layouts(_clean_scene(), _crowded_scene(),
                                        labels=("same", "same"))
        self.assertIn("same", str(caught.exception))

    def test_a_layout_named_tie_keeps_its_own_wins(self) -> None:
        """Ties are counted outside the label namespace, so no label wins them."""
        # Labels are user-supplied; while the tie count shared the wins
        # dict, a drawing called "tie" collected every metric the two
        # drew and then won the comparison on them.
        r = instruments.compare_layouts(_clean_scene(), _crowded_scene(),
                                        labels=("tie", "other"))
        drawn = sum(1 for va, vb in r["vector"].values() if va == vb)
        self.assertEqual(r["ties"], drawn)
        self.assertEqual(set(r["wins"]), {"tie", "other"})
        self.assertEqual(r["wins"]["tie"],
                         sum(1 for va, vb in r["vector"].values() if va > vb))
        self.assertEqual(r["winner"], "tie")

    def test_report_shows_the_whole_vector_side_by_side(self) -> None:
        """The reader gets every metric, both sums, and the compactness."""
        r = instruments.compare_layouts(_clean_scene(), _crowded_scene(),
                                        labels=("clean", "crowded"))
        text = instruments.format_comparison(r)
        for metric in instruments.WEIGHTS:
            self.assertIn(metric, text)
        for token in ("clean", "crowded", "WEIGHTED SUM", "wins:",
                      "compactness", "WINNER"):
            self.assertIn(token, text)


def _crowded_scene() -> list[dict]:
    """A drawing that fails on several axes at once.

    Returns:
        A scene with overlapping nodes, ragged columns, bends, and a
        crossing.
    """
    return [_node("n1", 100, 100), _node("n2", 137, 118),
            _node("n3", 411, 260), _node("n4", 233, 640),
            _edge("e1", 100, 130, [[0, 0], [0, 80], [311, 80], [311, 100]]),
            _edge("e2", 411, 290, [[0, 0], [-178, 350]]),
            _edge("e3", 137, 148, [[0, 0], [300, 400]])]


def _crossing_free_but_ragged() -> list[dict]:
    """Nothing crosses, but the columns are ragged and the routes bend.

    Returns:
        The scene.
    """
    return [_node("n1", 100, 100), _node("n2", 260, 400),
            _node("n3", 520, 700),
            _edge("e1", 100, 130, [[0, 0], [0, 140], [160, 140], [160, 240]]),
            _edge("e2", 260, 430, [[0, 0], [0, 140], [260, 140], [260, 240]])]


def _tidy_but_crossed() -> list[dict]:
    """Aligned columns and straight routes, but the routes cross.

    Returns:
        The scene.
    """
    return [_node("n1", 100, 100), _node("n2", 100, 400),
            _node("n3", 100, 700),
            _edge("e1", 60, 130, [[0, 0], [80, 540]]),
            _edge("e2", 140, 130, [[0, 0], [-80, 540]])]


def _overlapping_but_tidy() -> list[dict]:
    """Perfect on every metric except that two nodes sit on each other.

    Returns:
        The scene.
    """
    return [_node("n1", 100, 100), _node("n2", 100, 130),
            _node("n3", 100, 700),
            _edge("e1", 100, 160, [[0, 0], [0, 510]])]


class TestArrivalSquareness(unittest.TestCase):
    """The measure review F12 named, and the gate's promise read back.

    Two jobs. It states what curvature costs the endpoints, which is the
    half of r5-14's complaint that can be counted; and it is the
    INDEPENDENT reading of `gate_curvature`'s squareness arm — the gate
    decides with `canvas._arrival_lean` on slopes, this walks
    `instruments.rendered_stretches` and reports degrees, and the two
    share no arithmetic. A pin that used the gate's own function to
    check the gate would prove only that the function equals itself.
    """

    def _elbow(self, roundness: object,
               pts: list[list[float]] | None = None) -> list[dict]:
        """A bound elbow, sharp or curved.

        Defaults to `argus-run-flow`'s `t-agg` geometry — a 260px run
        east and a 37px turn — because that is the corpus's worst case
        and the one review F12 quotes.

        Args:
            roundness: The `roundness` value to give the arrow.
            pts: Stored points; the t-agg shape when omitted.

        Returns:
            A two-node scene with one elbow bound at both ends.
        """
        pts = pts or [[0, 0], [260, 0], [260, 37]]
        return [_node("a", 60, 100), _node("b", 420, 200),
                _edge("e", 100, 100, pts, roundness=roundness,
                      startBinding={"elementId": "a", "focus": 0, "gap": 1},
                      endBinding={"elementId": "b", "focus": 0, "gap": 1})]

    def test_a_sharp_orthogonal_route_arrives_dead_square(self) -> None:
        """0.00 degrees on both ends, the zero everything else is read against.

        Every route this router emits is orthogonal, so the sharp world
        is the zero this measure is calibrated against — measured over
        the frozen corpus, all 38 curvature-eligible arrows read 0.00 on
        their stored chords. Without this, a curved reading of 9 degrees
        could be the router's doing rather than the rendering's.
        """
        got = instruments.arrival_squareness(self._elbow(None))
        self.assertEqual(len(got), 2, got)
        for r in got:
            self.assertAlmostEqual(r["deg"], 0.0, places=6)

    def test_curving_the_t_agg_shape_reproduces_the_40_6_degree_worst_case(
            self) -> None:
        """The corpus's worst arrival, to the hundredth, from this module.

        THE CALIBRATION. 40.63 degrees is the figure review F12 measured
        on `argus-run-flow`'s `t-agg` pair, and this reaches it from
        `rendered_stretches` rather than from the canvas-side
        `_arrival_lean` the gate uses — so agreement here is two
        implementations meeting, not one function checked against
        itself. An equality (not a floor) because a change in
        `CURVE_SAMPLES` or in the Catmull-Rom rule SHOULD land here: the
        number is a property of the drawn curve and this is the only
        place that says what it is.
        """
        got = {r["end"]: r["deg"]
               for r in instruments.arrival_squareness(
                   self._elbow({"type": 2}))}
        self.assertAlmostEqual(got["end"], 40.63, places=2)

    def test_the_swing_lands_on_the_short_leg_not_the_long_one(self) -> None:
        """1.00 degrees in, 40.63 out, on the same arrow.

        The asymmetry IS the mechanism, and it is the same one
        `_reads_as_line`'s docstring names: a span's bow is set by the
        leg BEFORE it, not by its own length. A 260px approach barely
        leans; the 37px final it feeds swings 40 degrees. That is why
        the gate's arm rejects short finals after long runs and leaves
        balanced elbows alone, and why a reader sees the difference at
        the arrowheads rather than in the middle of the runs.
        """
        got = {r["end"]: r["deg"]
               for r in instruments.arrival_squareness(
                   self._elbow({"type": 2}))}
        self.assertLess(got["start"], 2.0)
        self.assertGreater(got["end"] - got["start"], 35.0)
        balanced = {r["end"]: r["deg"]
                    for r in instruments.arrival_squareness(
                        self._elbow({"type": 2},
                                    [[0, 0], [200, 0], [200, 200]]))}
        self.assertAlmostEqual(balanced["end"], balanced["start"], places=6)
        self.assertLess(balanced["end"], canvas.NEAR_AXIS_DEG,
                        "a balanced elbow should clear the gate's bar")

    def test_only_bound_ends_are_measured(self) -> None:
        """A free end has no box to be square to, and is not reported."""
        scene = self._elbow({"type": 2})
        scene[-1].pop("startBinding")
        got = instruments.arrival_squareness(scene)
        self.assertEqual([r["end"] for r in got], ["end"], got)

    def test_no_curved_arrival_in_the_corpus_passes_the_gates_bar(
            self) -> None:
        """`gate_curvature`'s promise, measured from outside the gate.

        THE CALIBRATION PIN. Ungated, this corpus ran to a median of 9.2
        and a maximum of 40.6 degrees over 28 arrivals past `NEAR_AXIS`
        — the numbers review F12 recorded. Loaded through the gate the
        maximum is 12.89 degrees and the count past `NEAR_AXIS` is ZERO,
        which is the gate's entire claim about endpoints.

        Asserted over every shipped artifact rather than a probe scene,
        because the claim is about the corpus and because a per-arrow
        pin would not notice the gate being bypassed on the load path.
        Only arrivals curvature actually MOVED are judged: a stored
        route that is diagonal on purpose (a 2-point arrow between two
        boxes that do not line up) is off-square in both worlds and is
        none of this measure's business.
        """
        root = Path(__file__).resolve().parent / "fixtures"
        worst, checked = 0.0, 0
        for path in sorted(root.glob("*/artifacts/*.excalidraw")):
            raw = [e for e in json.loads(path.read_text())["elements"]
                   if not e.get("isDeleted")]
            sharp = json.loads(json.dumps(raw))
            for e in sharp:
                if e.get("type") in ("arrow", "line"):
                    e["roundness"] = None
            was = {(r["arrow"], r["end"]): r["deg"]
                   for r in instruments.arrival_squareness(sharp)}
            for r in instruments.arrival_squareness(
                    canvas.rebuild_bound_elements(raw)):
                before = was.get((r["arrow"], r["end"]))
                if before is None or abs(r["deg"] - before) < 1e-9:
                    continue        # curvature did not move this arrival
                checked += 1
                worst = max(worst, r["deg"])
                self.assertLessEqual(
                    r["deg"], canvas.NEAR_AXIS_DEG,
                    "%s %s/%s arrives %.2f degrees off square — the gate "
                    "accepted a candidate past NEAR_AXIS"
                    % (path.name, r["arrow"], r["end"], r["deg"]))
        self.assertTrue(checked, "no arrival in the corpus was moved by "
                                 "curvature — the gate declined everything "
                                 "and this pin proves nothing")
        self.assertGreater(worst, 5.0,
                           "every accepted curve arrives near-dead-square, "
                           "so this bar is not the thing constraining them")


if __name__ == "__main__":
    unittest.main()
