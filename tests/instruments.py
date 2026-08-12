"""Verbatim ports of the r5 spike measurement scripts — BUGS PRESERVED.

Ported per docs/superpowers/specs/2026-08-11-mutation-harness-design.md §4:
the mutation catalogue proves these bugs where CI can see them, and WP4's
instrument rebuild fixes them here, in place. Known preserved bugs:
  - edge_crossing_pairs counts crossing PAIRS, not crossings.
  - false_bidi reads the stored chord points[-2]->points[-1], not the
    rendered tangent (wrong for curved elbows).
  - float_diamond measures radially from the node center, takes abs(),
    and returns 0 for an endpoint at the exact center.
Source: ~/docs/optimization/r5/mermaid-spike/ (see its README).
"""
from __future__ import annotations

import itertools


def _crossing_segments(
    el: dict,
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Absolute-coordinate polyline segments of one arrow.

    Ported from analyze.py's `segs` (the part edge_crossing_pairs needs;
    that script's version also returned the flattened point list, unused
    here).

    Args:
        el: An arrow element dict with x, y, and points.

    Returns:
        Consecutive (start, end) point pairs in absolute coordinates.
    """
    pts = el.get("points") or [[0, 0]]
    ox, oy = el["x"], el["y"]
    abs_pts = [(ox + p[0], oy + p[1]) for p in pts]
    return [(abs_pts[i], abs_pts[i + 1]) for i in range(len(abs_pts) - 1)]


def _segments_intersect(
    p1: tuple[float, float], p2: tuple[float, float],
    p3: tuple[float, float], p4: tuple[float, float],
) -> bool:
    """Proper segment intersection test (excludes shared endpoints/collinear).

    Ported from analyze.py's `inter`.

    Args:
        p1: First endpoint of the first segment.
        p2: Second endpoint of the first segment.
        p3: First endpoint of the second segment.
        p4: Second endpoint of the second segment.

    Returns:
        True if the open segments (p1, p2) and (p3, p4) cross.
    """
    def d(a: tuple[float, float], b: tuple[float, float],
          c: tuple[float, float]) -> float:
        """Twice the signed area of triangle a-b-c.

        Returns:
            The signed area value used for the orientation test.
        """
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    d1, d2 = d(p3, p4, p1), d(p3, p4, p2)
    d3, d4 = d(p1, p2, p3), d(p1, p2, p4)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def edge_crossing_pairs(
    elements: list[dict],
) -> tuple[int, list[tuple[str, str]]]:
    """Count arrow pairs whose segments cross.

    Ported from analyze.py's crossing-count block.

    BUG, preserved: once a pair's first crossing segment is found, the
    nested loop double-breaks out of the whole pair, so a pair that
    crosses three times still counts once — this is a PAIR count, not a
    crossing count, despite the source's `EDGE_CROSSINGS` label.

    Args:
        elements: Full scene element list; arrows are filtered internally.

    Returns:
        A (count, pairs) tuple: the buggy pair-count, and the (id, id)
        pairs that crossed at least once, in traversal order.
    """
    arrows = [e for e in elements if e["type"] == "arrow"]
    allsegs = [(a["id"], _crossing_segments(a)) for a in arrows]
    cross = 0
    pairs: list[tuple[str, str]] = []
    for (i1, s1), (i2, s2) in itertools.combinations(allsegs, 2):
        for a in s1:
            for b in s2:
                if _segments_intersect(a[0], a[1], b[0], b[1]):
                    cross += 1
                    pairs.append((i1, i2))
                    break
            else:
                continue
            break
    return cross, pairs


def _corridor_segments(
    el: dict,
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Absolute-coordinate polyline segments of one arrow.

    Ported from corridor.py's `segs`.

    Args:
        el: An arrow element dict with x, y, and points.

    Returns:
        Consecutive (start, end) point pairs in absolute coordinates.
    """
    pts = el.get("points") or [[0, 0]]
    ox, oy = el["x"], el["y"]
    a = [(ox + p[0], oy + p[1]) for p in pts]
    return [(a[i], a[i + 1]) for i in range(len(a) - 1)]


def _axis(
    s: tuple[tuple[float, float], tuple[float, float]],
) -> tuple[str, float, float, float] | None:
    """Classify a segment as (near-)horizontal or (near-)vertical.

    Ported from corridor.py's `axis`.

    Args:
        s: A (start, end) segment in absolute coordinates.

    Returns:
        `(orientation, fixed_coord, extent_min, extent_max)` where
        orientation is `'h'` or `'v'`, or None if the segment is
        diagonal enough that neither reading applies.
    """
    (x1, y1), (x2, y2) = s
    if abs(x2 - x1) <= 2 and abs(y2 - y1) > 2:
        return "v", x1, min(y1, y2), max(y1, y2)
    if abs(y2 - y1) <= 2 and abs(x2 - x1) > 2:
        return "h", y1, min(x1, x2), max(x1, x2)
    return None


def shared_corridors(
    elements: list[dict], tol: float = 16, minover: float = 60,
) -> list[dict]:
    """Find near-collinear arrow pairs that read as one stroke.

    Ported from corridor.py.

    Args:
        elements: Full scene element list; arrows are filtered internally.
        tol: Max lateral separation (px) that still reads as one stroke.
        minover: Min shared extent (px) needed to matter on its own; a
            small negative overlap (abutting runs) also matters if both
            segments are individually at least 60px long — that 60 is
            hardcoded in the source, not tied to this parameter.

    Returns:
        A list of `{"a": id, "b": id, "overlap": float}` dicts, one per
        corridor-sharing pair (first qualifying segment pair wins).
    """
    arrows = [e for e in elements if e["type"] == "arrow"]
    hits: list[dict] = []
    for a, b in itertools.combinations(arrows, 2):
        for s1 in _corridor_segments(a):
            A = _axis(s1)
            if not A:
                continue
            for s2 in _corridor_segments(b):
                B = _axis(s2)
                if not B or B[0] != A[0]:
                    continue
                if abs(A[1] - B[1]) > tol:
                    continue
                ov = min(A[3], B[3]) - max(A[2], B[2])
                # abutting collinear runs read as ONE continuous stroke:
                # negative overlap of a few px still joins visually
                if ov >= minover or (-10 <= ov <= 0
                                     and (A[3] - A[2]) >= 60
                                     and (B[3] - B[2]) >= 60):
                    hits.append({"a": a["id"], "b": b["id"], "overlap": ov})
                    break
            else:
                continue
            break
    return hits


def _final_segment(
    el: dict,
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """The last (arrowheaded) chord of an arrow: points[-2] -> points[-1].

    Ported from falsebidi.py's `final`.

    BUG, preserved: this reads the STORED chord, not the rendered
    tangent — wrong for a curved elbow, whose visual direction at the
    head can differ from the straight line between its last two points.

    Args:
        el: An arrow element dict with x, y, and points.

    Returns:
        The (start, end) absolute-coordinate chord, or None if the arrow
        has fewer than two points.
    """
    pts = el.get("points") or [[0, 0]]
    if len(pts) < 2:
        return None
    ox, oy = el["x"], el["y"]
    p, q = pts[-2], pts[-1]
    return (ox + p[0], oy + p[1]), (ox + q[0], oy + q[1])


def false_bidi(elements: list[dict]) -> list[dict]:
    """Find arrow pairs whose final chords read as one bidirectional line.

    Ported from falsebidi.py: both chords are (near-)horizontal or both
    (near-)vertical, on the same line, running in opposite directions,
    with overlapping extent — the visual signature of two one-way
    arrows that together read as a single bidirectional junction.

    Args:
        elements: Full scene element list; arrows are filtered internally.

    Returns:
        A list of `{"a": id, "b": id}` dicts, one per qualifying pair
        (a pair can appear twice if it qualifies on both axes).
    """
    arrows = [e for e in elements if e["type"] == "arrow"]
    hits: list[dict] = []
    for a, b in itertools.combinations(arrows, 2):
        fa, fb = _final_segment(a), _final_segment(b)
        if not fa or not fb:
            continue
        (ax1, ay1), (ax2, ay2) = fa
        (bx1, by1), (bx2, by2) = fb
        # both horizontal, same y, opposite direction, x ranges overlap
        if (abs(ay1 - ay2) <= 2 and abs(by1 - by2) <= 2
                and abs(ay2 - by2) <= 8):
            da, db = ax2 - ax1, bx2 - bx1
            if (da * db < 0
                    and min(max(ax1, ax2), max(bx1, bx2))
                    - max(min(ax1, ax2), min(bx1, bx2)) > -24):
                hits.append({"a": a["id"], "b": b["id"]})
        # both vertical, same x, opposite direction, y ranges overlap
        if (abs(ax1 - ax2) <= 2 and abs(bx1 - bx2) <= 2
                and abs(ax2 - bx2) <= 8):
            da, db = ay2 - ay1, by2 - by1
            if (da * db < 0
                    and min(max(ay1, ay2), max(by1, by2))
                    - max(min(ay1, ay2), min(by1, by2)) > -24):
                hits.append({"a": a["id"], "b": b["id"]})
    return hits


def _dist_to_diamond(px: float, py: float, n: dict) -> float:
    """Radial gap between a point and a diamond node's boundary.

    Ported from floatdia.py's `dist_to_diamond`.

    BUG, preserved: takes abs() of the radial excess, so a point just
    inside the diamond and a point just as far outside score the same
    gap; and when the point sits exactly on the node's center (t == 0),
    returns r (which is 0) instead of a meaningful gap, silently
    hiding an endpoint pinned dead-center.

    Args:
        px: Endpoint x in absolute coordinates.
        py: Endpoint y in absolute coordinates.
        n: The diamond node element dict (x, y, width, height).

    Returns:
        The (buggy) radial distance from the diamond's boundary.
    """
    cx, cy = n["x"] + n["width"] / 2.0, n["y"] + n["height"] / 2.0
    a, b = n["width"] / 2.0, n["height"] / 2.0
    # |dx|/a + |dy|/b = 1 is the boundary; scale the ray to hit it
    dx, dy = abs(px - cx), abs(py - cy)
    t = dx / a + dy / b
    r = (dx * dx + dy * dy) ** 0.5
    if t == 0:
        return r
    return abs(r - r / t)


def float_diamond(elements: list[dict]) -> list[dict]:
    """Flag arrow endpoints bound to a diamond but sitting off its boundary.

    Ported from floatdia.py.

    Args:
        elements: Full scene element list; arrows and their bound
            diamond nodes are looked up internally.

    Returns:
        A list of `{"arrow": id, "node": id, "gap": float}` dicts, one
        per bound endpoint whose (buggy) radial gap exceeds 12px.
    """
    by_id = {e["id"]: e for e in elements}
    bad: list[dict] = []
    for e in elements:
        if e["type"] != "arrow":
            continue
        pts = [(e["x"] + p[0], e["y"] + p[1])
               for p in (e.get("points") or [])]
        if len(pts) < 2:
            continue
        for pt, bind in ((pts[0], e.get("startBinding")),
                         (pts[-1], e.get("endBinding"))):
            if not bind:
                continue
            n = by_id.get(bind.get("elementId"))
            if not n or n["type"] != "diamond":
                continue
            g = _dist_to_diamond(pt[0], pt[1], n)
            if g > 12:
                bad.append({"arrow": e["id"], "node": n["id"], "gap": g})
    return bad
