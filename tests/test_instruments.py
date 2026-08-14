"""Tests for the geometry instruments: the ports and the score vector.

Two classes of assertion live here. `TestInstrumentPorts` pins the ported
detectors' behavior; as of v0.9 WP4 none of the ported bugs is preserved
any longer, and the mutation catalogue (test_mutants.py) is what holds
each fix to its magnitude. Everything below it covers WP4's rebuild: the
rendered-path flattening, the fixed crossing counter, and one test per
metric in the score vector.
"""
from __future__ import annotations

import unittest

import instruments
from tests_helpers import el


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


if __name__ == "__main__":
    unittest.main()
