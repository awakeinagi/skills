"""Smoke tests for the verbatim instrument ports.

These assert CURRENT behavior, including the known bugs — the mutation
catalogue (test_mutants.py) is what asserts the bugs are bugs.
"""
from __future__ import annotations

import unittest

import instruments
from tests_helpers import el


class TestInstrumentPorts(unittest.TestCase):
    """The ports run and preserve the spike scripts' observed behavior."""

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

    def test_crossing_counter_reports_pairs_not_crossings(self) -> None:
        """The counter reports one PAIR though the arrows cross four times."""
        count, pairs = instruments.edge_crossing_pairs(self._crossing_scene())
        self.assertEqual(count, 1)          # BUG, preserved: 4 crossings -> 1
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

    def test_float_diamond_center_scores_zero(self) -> None:
        """A dead-center endpoint scores gap 0 and is never flagged."""
        # BUG, preserved: an endpoint at the diamond's exact center
        # returns gap 0 (t == 0 guard returns r, which is 0). The gap
        # never exceeds the 12px threshold, so a dead-center endpoint —
        # structurally the worst possible binding — is never reported.
        node = el(id="d1", type="diamond", x=100, y=100, width=200,
                  height=100, customData={"role": "node"})
        arrow = el(id="ar1", type="arrow", x=0, y=0, width=200, height=150,
                   points=[[0, 0], [200, 150]],
                   endBinding={"elementId": "d1", "focus": 0, "gap": 0},
                   customData={"role": "edge"})
        hits = instruments.float_diamond([node, arrow])
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
