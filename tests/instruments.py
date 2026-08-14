"""Geometry instruments: defect detectors and the layout score vector.

Ported from the r5 spike measurement scripts
(~/docs/optimization/r5/mermaid-spike/) per
docs/superpowers/specs/2026-08-11-mutation-harness-design.md §4. The port
kept the spike's bugs on purpose so the mutation catalogue could pin them
where CI can see them; WP4 fixes them here, in place.

Fixed by WP4, each flipping its catalogue mutant:
  - the crossing counter counted crossing PAIRS, not crossings — a
    `break` cascade left both segment loops on a pair's first hit
    (`four_crossings_pairbug`);
  - `false_bidi` read the stored chord `points[-2] -> points[-1]` rather
    than the path the renderer draws, so a curved elbow bowed well off
    its own chord still read as one bidirectional line
    (`curved_elbow_spurious_bidi`).

Still preserved, pinned by `float_diamond_center_zero` and owned by
WP4's shape-clipping item: `float_diamond` measures radially from the
node center, takes abs(), and returns 0 for an endpoint at the exact
center.

The scoring half (`score_layout`, `compare_layouts`) is the Aug 2026
literature-scan rebuild. Every headline metric is normalized to [0, 1]
with 1 = good so drawings of different sizes are comparable; compactness
is reported as a diagnostic and never scored, because it is the
literature's strongest predictor of human preference and measured
anti-correlated on our own corpus; and a winner is declared only when
the win/loss count and the weighted sum agree.
"""
from __future__ import annotations

import itertools
import math

# Excalidraw draws a non-elbow linear element with `roundness` through
# roughjs' `generator.curve()` (verified in the bundled build:
# `e.roundness ? [t.curve(s, a)] : ... t.linearPath(s, a)`). That curve is
# a Catmull-Rom chain: the point list is padded by DUPLICATING each
# endpoint, and every span becomes one cubic Bezier whose controls are the
# neighbours' sixths. Sampling it is the only way to see what a reader
# sees — the stored polyline is a chord the renderer never draws.
#
# The sampled path is the idealized curve. roughjs also jitters it by up
# to ~1.2px at default roughness, which is why the collinearity tolerances
# below are 2px rather than 0.
CURVE_SAMPLES = 20

NODE_TYPES = ("rectangle", "diamond", "ellipse")

# Kieffer et al. (TVCG 2016) describe alignment as node centres sharing a
# coordinate; 6px is the tolerance the r5 measurement used, and the corpus
# numbers in the v0.9 plan were taken with it.
GRID_TOL = 6.0

# Below this angle a "crossing" is really two strokes running together —
# Mooney et al. discount it rather than counting it as a crossing. We do
# not discount (the count stays a plain proper-intersection count, which
# is what `four_crossings_pairbug` pins); we report the shallow ones as
# their own defect class, since `shared_corridors` is the net for them.
SHALLOW_CROSSING_DEG = 2.5

# Weights for the weighted sum, written down because a single number that
# hides a 3-4 split is the practice the evaluation survey exists to
# criticise. Rationale per line; they sum to 1.0.
WEIGHTS: dict[str, float] = {
    # Kieffer et al.'s runner-up, and the only literature metric that
    # agreed with our own judgement on our own corpus (hand-laid scored
    # ~2x the seed in both head-to-head pairs). Also the metric most
    # likely to expose the dagre gap: dagre aligns within layers, not
    # across them, which is the floating-diamond root cause.
    "gridiness": 0.30,
    # Agreed with the qualitative verdict in both pairs.
    "bends_mean": 0.20,
    "bends_max": 0.10,
    # Classical, and now honest: it counts crossings, not pairs.
    "crossings": 0.15,
    # Near-constant at 1.0 on orthogonal baselines, so it is the metric
    # that will actually move if a curved-elbow regression lands.
    "crossing_angle": 0.10,
    # Direction is scored against whichever axis dominates: Figl &
    # Strembeck found no comprehension advantage for any direction, so a
    # hardcoded left-to-right preference would measure our taste.
    "flow_consistency": 0.10,
    # A hard gate more than a discriminator — it is 1.0 on anything worth
    # comparing, and `GATES` disqualifies a winner that fails it.
    "node_overlap": 0.05,
}

# Metrics that disqualify a drawing outright rather than costing it
# points: the "hard gate, then score" pattern the survey recommends.
GATES = ("node_overlap",)


def _nodes(elements: list[dict]) -> list[dict]:
    """Select the diagram's node shapes.

    Args:
        elements: Full scene element list.

    Returns:
        Elements of a node shape type carrying `customData.role ==
        "node"`, in scene order.
    """
    return [e for e in elements if e["type"] in NODE_TYPES
            and (e.get("customData") or {}).get("role") == "node"]


def _arrows(elements: list[dict]) -> list[dict]:
    """Select the diagram's arrows.

    Args:
        elements: Full scene element list.

    Returns:
        Every arrow element, in scene order.
    """
    return [e for e in elements if e["type"] == "arrow"]


def _abs_points(el: dict) -> list[tuple[float, float]]:
    """Resolve an arrow's stored points into absolute coordinates.

    Args:
        el: An arrow element dict with x, y, and points.

    Returns:
        The stored polyline in scene coordinates.
    """
    pts = el.get("points") or [[0, 0]]
    ox, oy = el["x"], el["y"]
    return [(ox + p[0], oy + p[1]) for p in pts]


def _abs_segments(
    el: dict,
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Absolute-coordinate polyline segments of one arrow.

    Ported from analyze.py's and corridor.py's identical `segs`; the two
    ports were kept apart while this module was verbatim and are one
    function now that it is not.

    Args:
        el: An arrow element dict with x, y, and points.

    Returns:
        Consecutive (start, end) point pairs in absolute coordinates.
    """
    a = _abs_points(el)
    return [(a[i], a[i + 1]) for i in range(len(a) - 1)]


def _bezier_point(
    b0: tuple[float, float], b1: tuple[float, float],
    b2: tuple[float, float], b3: tuple[float, float], t: float,
) -> tuple[float, float]:
    """Evaluate a cubic Bezier at parameter t.

    Args:
        b0: Start point.
        b1: First control point.
        b2: Second control point.
        b3: End point.
        t: Curve parameter in [0, 1].

    Returns:
        The point on the curve at t.
    """
    u = 1.0 - t
    c0, c1 = u * u * u, 3 * u * u * t
    c2, c3 = 3 * u * t * t, t * t * t
    return (c0 * b0[0] + c1 * b1[0] + c2 * b2[0] + c3 * b3[0],
            c0 * b0[1] + c1 * b1[1] + c2 * b2[1] + c3 * b3[1])


def rendered_stretches(
    el: dict, samples: int = CURVE_SAMPLES,
) -> list[list[tuple[float, float]]]:
    """Flatten an arrow into the path the renderer actually draws.

    One sampled polyline per STORED span, so callers that care about a
    particular leg of a route — `false_bidi` wants the last one — can
    take it without re-deriving the correspondence. A sharp arrow's
    stretches are its chords, unchanged; a rounded one's are samples of
    the Catmull-Rom Bezier the renderer builds for that span.

    Args:
        el: An arrow element dict with x, y, points, and roundness.
        samples: Sub-segments per curved span. Straight spans ignore it.

    Returns:
        A list of sampled polylines, one per span, each starting at that
        span's first stored point and ending at its last. Empty for an
        arrow with fewer than two points.
    """
    p = _abs_points(el)
    if len(p) < 2:
        return []
    if not el.get("roundness") or len(p) < 3:
        return [[p[i], p[i + 1]] for i in range(len(p) - 1)]
    pad = [p[0], p[0], *p[1:], p[-1]]
    out: list[list[tuple[float, float]]] = []
    for i in range(1, len(p)):
        b0, b3 = pad[i], pad[i + 1]
        b1 = (b0[0] + (pad[i + 1][0] - pad[i - 1][0]) / 6.0,
              b0[1] + (pad[i + 1][1] - pad[i - 1][1]) / 6.0)
        b2 = (b3[0] + (pad[i][0] - pad[i + 2][0]) / 6.0,
              b3[1] + (pad[i][1] - pad[i + 2][1]) / 6.0)
        out.append([_bezier_point(b0, b1, b2, b3, k / float(samples))
                    for k in range(samples + 1)])
    return out


def rendered_path(
    el: dict, samples: int = CURVE_SAMPLES,
) -> list[tuple[float, float]]:
    """Flatten an arrow into one continuous rendered polyline.

    Args:
        el: An arrow element dict with x, y, points, and roundness.
        samples: Sub-segments per curved span.

    Returns:
        The whole rendered path, span joints appearing once.
    """
    out: list[tuple[float, float]] = []
    for stretch in rendered_stretches(el, samples):
        out.extend(stretch[1:] if out else stretch)
    return out


def _segments_intersect(
    p1: tuple[float, float], p2: tuple[float, float],
    p3: tuple[float, float], p4: tuple[float, float],
) -> bool:
    """Test whether two open segments properly cross.

    Ported from analyze.py's `inter`. Shared endpoints and collinear
    overlap both read False — collinear overlap is `shared_corridors`'
    business, and the two instruments are deliberately not merged.

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


def _acute_angle(
    s1: tuple[tuple[float, float], tuple[float, float]],
    s2: tuple[tuple[float, float], tuple[float, float]],
) -> float:
    """Measure the acute angle between two segments.

    Args:
        s1: First (start, end) segment.
        s2: Second (start, end) segment.

    Returns:
        The angle in degrees, in [0, 90]: 90 is a perpendicular crossing,
        0 two strokes running together.
    """
    ux, uy = s1[1][0] - s1[0][0], s1[1][1] - s1[0][1]
    vx, vy = s2[1][0] - s2[0][0], s2[1][1] - s2[0][1]
    return math.degrees(math.atan2(abs(ux * vy - uy * vx),
                                   abs(ux * vx + uy * vy)))


def crossing_sites(elements: list[dict]) -> list[dict]:
    """Enumerate every crossing between segments of two different arrows.

    This is the whole crossing algorithm; `edge_crossings` is a count
    over it. Every intersecting segment pair is one site, which is the
    fix for the ported `break` cascade: a pair that crosses three times
    used to leave both loops on the first hit and score 1, so a router
    change that turned one crossing into three registered as no change.

    Args:
        elements: Full scene element list; arrows are filtered
            internally.

    Returns:
        A list of `{"a": id, "b": id, "angle": degrees}` dicts, one per
        crossing, in traversal order.
    """
    allsegs = [(a["id"], _abs_segments(a)) for a in _arrows(elements)]
    sites: list[dict] = []
    for (i1, s1), (i2, s2) in itertools.combinations(allsegs, 2):
        sites.extend({"a": i1, "b": i2, "angle": _acute_angle(a, b)}
                     for a in s1 for b in s2
                     if _segments_intersect(a[0], a[1], b[0], b[1]))
    return sites


def edge_crossings(
    elements: list[dict],
) -> tuple[int, list[tuple[str, str]]]:
    """Count crossings between arrows, and name the pairs that cross.

    Args:
        elements: Full scene element list; arrows are filtered
            internally.

    Returns:
        A `(crossings, pairs)` tuple: the total number of crossings, and
        the distinct (id, id) arrow pairs that crossed at least once, in
        traversal order. The two numbers differ whenever a pair crosses
        more than once — that difference is the whole point.
    """
    sites = crossing_sites(elements)
    pairs: list[tuple[str, str]] = []
    for s in sites:
        if (s["a"], s["b"]) not in pairs:
            pairs.append((s["a"], s["b"]))
    return len(sites), pairs


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
    arrows = _arrows(elements)
    hits: list[dict] = []
    for a, b in itertools.combinations(arrows, 2):
        for s1 in _abs_segments(a):
            A = _axis(s1)
            if not A:
                continue
            for s2 in _abs_segments(b):
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


def _final_stretch(
    el: dict,
) -> list[tuple[float, float]] | None:
    """The rendered path of an arrow's last span, as the reader sees it.

    Ported from falsebidi.py's `final`, which returned the STORED chord
    `points[-2] -> points[-1]`. That chord is not on the page: a curved
    elbow bows several px off it, and reading the chord made two curved
    arrows that visibly separate still report as one bidirectional line.
    The tangent at the arrowhead is not the answer either — on a
    symmetric elbow it points exactly along the chord — so what comes
    back is the whole final span, sampled.

    Args:
        el: An arrow element dict with x, y, points, and roundness.

    Returns:
        The sampled final span in absolute coordinates, or None if the
        arrow has fewer than two points.
    """
    stretches = rendered_stretches(el)
    return stretches[-1] if stretches else None


def _reads_as_line(
    stretch: list[tuple[float, float]], idx: int,
) -> bool:
    """Test whether a rendered stretch reads as one straight axis line.

    A straight chord's spread on the off-axis is exactly the endpoint
    difference the ported code compared, so this is the same 2px test
    for every arrow the old one judged correctly — it only stops giving
    a curve the benefit of its chord.

    Args:
        stretch: The sampled stretch in absolute coordinates.
        idx: 0 to test horizontality (spread in y), 1 for verticality.

    Returns:
        True if the whole stretch stays inside a 2px band.
    """
    off = [p[1 - idx] for p in stretch]
    return max(off) - min(off) <= 2


def false_bidi(elements: list[dict]) -> list[dict]:
    """Find arrow pairs whose final stretches read as one bidirectional line.

    Ported from falsebidi.py: both final stretches are (near-)horizontal
    or both (near-)vertical, on the same line, running in opposite
    directions, with overlapping extent — the visual signature of two
    one-way arrows that together read as a single bidirectional
    junction. What changed in WP4 is which geometry is asked: the
    rendered path, not the stored chord.

    Args:
        elements: Full scene element list; arrows are filtered internally.

    Returns:
        A list of `{"a": id, "b": id}` dicts, one per qualifying pair
        (a pair can appear twice if it qualifies on both axes).
    """
    arrows = _arrows(elements)
    hits: list[dict] = []
    for a, b in itertools.combinations(arrows, 2):
        fa, fb = _final_stretch(a), _final_stretch(b)
        if not fa or not fb:
            continue
        for idx in (0, 1):
            if not (_reads_as_line(fa, idx) and _reads_as_line(fb, idx)):
                continue
            # heads on the same line, and pointing at each other
            if abs(fa[-1][1 - idx] - fb[-1][1 - idx]) > 8:
                continue
            da = fa[-1][idx] - fa[0][idx]
            db = fb[-1][idx] - fb[0][idx]
            if da * db >= 0:
                continue
            av = [p[idx] for p in fa]
            bv = [p[idx] for p in fb]
            if min(max(av), max(bv)) - max(min(av), min(bv)) > -24:
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
        pts = _abs_points(e) if e.get("points") else []
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


# ---------------------------------------------------------------------------
# The score vector. Every metric below returns [0, 1] with 1 = good, so a
# 12-node seed and a 30-node hand layout are comparable — which is the whole
# reason the campaign needs an instrument at all.
# ---------------------------------------------------------------------------


def _decay(value: float, free: float, scale: float) -> float:
    """Map a lower-is-better quantity onto [0, 1] with 1 = good.

    Full marks up to `free`, then a hyperbola. The free allowance is
    what keeps a metric from being zero-targeted where zero is not the
    goal; the hyperbola is what keeps it from saturating at some
    arbitrary ceiling the way a `1 - x/max` ramp does.

    Args:
        value: The measured quantity, >= 0.
        free: Allowance scoring a flat 1.0.
        scale: Excess over `free` that costs half the remaining score.

    Returns:
        A score in (0, 1].
    """
    excess = max(0.0, value - free)
    return 1.0 if excess == 0 else scale / (scale + excess)


def gridiness(elements: list[dict]) -> float:
    """Score how many node centres share a coordinate with another node.

    Kieffer et al.'s alignment metric, from their prose description
    (centre-based, 6px tolerance) rather than their code. Second-best
    predictor of human preference in their study, and the one literature
    metric that agreed with our judgement on our own corpus.

    Args:
        elements: Full scene element list.

    Returns:
        The fraction of nodes aligned with at least one other node. 1.0
        for a drawing with fewer than two nodes, which has nothing to
        misalign.
    """
    centres = [(n["x"] + n["width"] / 2.0, n["y"] + n["height"] / 2.0)
               for n in _nodes(elements)]
    if len(centres) < 2:
        return 1.0
    aligned = 0
    for i, c in enumerate(centres):
        for j, o in enumerate(centres):
            if i != j and (abs(c[0] - o[0]) <= GRID_TOL
                           or abs(c[1] - o[1]) <= GRID_TOL):
                aligned += 1
                break
    return aligned / float(len(centres))


def bend_counts(elements: list[dict]) -> list[int]:
    """Count the direction changes along each arrow's route.

    Measured on the stored polyline, not the rendered path: rounding an
    elbow softens how a bend looks but does not remove it, and a route
    with three corners is a three-bend route however it is drawn.

    Args:
        elements: Full scene element list.

    Returns:
        One bend count per arrow, in scene order.
    """
    counts: list[int] = []
    for a in _arrows(elements):
        segs = _abs_segments(a)
        n = 0
        for s1, s2 in zip(segs, segs[1:]):
            if _acute_angle(s1, s2) > 1e-9:
                n += 1
        counts.append(n)
    return counts


def node_overlaps(elements: list[dict]) -> list[dict]:
    """Find node pairs whose boxes intersect.

    Args:
        elements: Full scene element list.

    Returns:
        A list of `{"a": id, "b": id, "area": float}` dicts, one per
        overlapping pair, with the intersected area in px².
    """
    hits: list[dict] = []
    for a, b in itertools.combinations(_nodes(elements), 2):
        ow = (min(a["x"] + a["width"], b["x"] + b["width"])
              - max(a["x"], b["x"]))
        oh = (min(a["y"] + a["height"], b["y"] + b["height"])
              - max(a["y"], b["y"]))
        if ow > 0 and oh > 0:
            hits.append({"a": a["id"], "b": b["id"], "area": ow * oh})
    return hits


def flow_consistency(elements: list[dict]) -> tuple[float, str]:
    """Score how many arrows advance the same way along the dominant axis.

    The axis is whichever one the drawing actually spends its travel on,
    and the good direction is whichever way the majority of arrows go —
    never a hardcoded left-to-right, since Figl & Strembeck found no
    comprehension advantage for any direction. Hardcoding one would
    measure our taste and call it a defect.

    Args:
        elements: Full scene element list.

    Returns:
        A `(score, axis)` pair. The score is the fraction of arrows
        travelling in the majority direction — floored at 0.5 by
        construction on that axis, and lower only because arrows with no
        travel at all still count in the denominator. `axis` is `'x'` or
        `'y'`, ties going to `'x'`.
    """
    deltas = [(p[-1][0] - p[0][0], p[-1][1] - p[0][1])
              for p in (_abs_points(a) for a in _arrows(elements))
              if len(p) >= 2]
    if not deltas:
        return 1.0, "x"
    best: tuple[float, float, str] | None = None
    for name, idx in (("x", 0), ("y", 1)):
        vals = [d[idx] for d in deltas]
        extent = sum(abs(v) for v in vals)
        pos = sum(1 for v in vals if v > 0)
        neg = sum(1 for v in vals if v < 0)
        cand = (extent, max(pos, neg) / float(len(deltas)), name)
        if best is None or cand > best:
            best = cand
    return best[1], best[2]


def crossing_angle_score(sites: list[dict]) -> float:
    """Score how squarely the crossings that exist are made.

    A crossing a reader can follow is one made near a right angle; a
    shallow one reads as two strokes merging. Near-constant at 1.0 on
    orthogonal baselines, which is exactly why it is the metric that
    moves when a curved-elbow regression lands.

    Args:
        sites: Crossings from `crossing_sites`.

    Returns:
        The mean crossing angle as a fraction of a right angle. 1.0 when
        nothing crosses.
    """
    if not sites:
        return 1.0
    return sum(s["angle"] / 90.0 for s in sites) / float(len(sites))


def enumerate_defects(elements: list[dict]) -> list[dict]:
    """List the drawing's individual reading failures, by class.

    The one thing the spike script already got right was naming the
    defects instead of only scoring them, so the list survives the
    rebuild: a score says a drawing is worse, the list says where.

    Args:
        elements: Full scene element list.

    Returns:
        A list of `{"kind": str, ...}` dicts — one per crossing pair,
        shallow crossing, shared corridor, false bidirectional pair,
        floating diamond endpoint, and overlapping node pair.
    """
    sites = crossing_sites(elements)
    _n, pairs = edge_crossings(elements)
    out: list[dict] = [{"kind": "crossing", "a": a, "b": b}
                       for a, b in pairs]
    out += [{"kind": "shallow_crossing", "a": s["a"], "b": s["b"],
             "angle": s["angle"]}
            for s in sites if s["angle"] < SHALLOW_CROSSING_DEG]
    out += [dict(h, kind="shared_corridor") for h in shared_corridors(elements)]
    out += [dict(h, kind="false_bidi") for h in false_bidi(elements)]
    out += [dict(h, kind="float_diamond") for h in float_diamond(elements)]
    out += [dict(h, kind="node_overlap") for h in node_overlaps(elements)]
    return out


def score_layout(elements: list[dict]) -> dict:
    """Measure one drawing: normalized headline vector plus diagnostics.

    Compactness is deliberately NOT in the headline vector. It is the
    literature's strongest single predictor of human preference for
    orthogonal drawings (Kieffer et al., 8 of 8 graphs, p<0.01) and it
    picked the worse drawing in both of our own head-to-head pairs by
    ~2.5x, including the seed that drew a relationship the source does
    not contain. The mechanism is not mysterious — compactness rewards
    density, and our worst defect classes are all caused by density — so
    it is reported, and never scored.

    Args:
        elements: Full scene element list.

    Returns:
        `{"metrics": {...}, "diagnostics": {...}, "defects": [...]}`.
        Every value in `metrics` is in [0, 1] with 1 = good and its key
        is in `WEIGHTS`; `diagnostics` carries the raw readings, which
        are what a reviewer quotes.
    """
    nodes, arrows = _nodes(elements), _arrows(elements)
    sites = crossing_sites(elements)
    bends = bend_counts(elements)
    overlaps = node_overlaps(elements)
    flow, axis = flow_consistency(elements)
    bmean = sum(bends) / float(len(bends)) if bends else 0.0
    bmax = float(max(bends)) if bends else 0.0
    per_edge = len(sites) / float(len(arrows)) if arrows else 0.0
    hurt = {i for h in overlaps for i in (h["a"], h["b"])}
    xs = ([e["x"] for e in elements]
          + [e["x"] + e.get("width", 0) for e in elements])
    ys = ([e["y"] for e in elements]
          + [e["y"] + e.get("height", 0) for e in elements])
    w = (max(xs) - min(xs)) if xs else 0.0
    h = (max(ys) - min(ys)) if ys else 0.0
    ink = sum(n["width"] * n["height"] for n in nodes)
    edge_len = sum(math.dist(p, q) for a in arrows
                   for p, q in _abs_segments(a))
    metrics = {
        "gridiness": gridiness(elements),
        # a quarter of the edges may carry one deliberate bend for free:
        # the best hand layouts in the corpus bend on purpose, so a
        # zero-targeted score would rank them below a worse drawing
        "bends_mean": _decay(bmean, 0.25, 0.75),
        "bends_max": _decay(bmax, 2.0, 3.0),
        "crossings": _decay(per_edge, 0.0, 0.5),
        "crossing_angle": crossing_angle_score(sites),
        "flow_consistency": flow,
        "node_overlap": (1.0 - len(hurt) / float(len(nodes))
                         if nodes else 1.0),
    }
    return {
        "metrics": metrics,
        "diagnostics": {
            "nodes": len(nodes), "arrows": len(arrows),
            "bends_mean_raw": bmean, "bends_max_raw": bmax,
            "crossings_raw": len(sites), "flow_axis": axis,
            "bbox": (w, h), "area": w * h, "node_ink": ink,
            # reported, never scored — see this function's docstring
            "compactness": (ink / (w * h)) if w and h else 0.0,
            "edge_len_total": edge_len,
            "edge_len_mean": (edge_len / len(arrows)) if arrows else 0.0,
        },
        "defects": enumerate_defects(elements),
    }


def compare_layouts(
    a: list[dict], b: list[dict], labels: tuple[str, str] = ("a", "b"),
    weights: dict[str, float] | None = None,
) -> dict:
    """Compare two drawings and declare a winner only if the readings agree.

    Two verdicts are computed over the same vector — a win/loss/tie count
    and a weighted sum — and a winner is named only when both point the
    same way. A single number hiding a 3-4 split is the practice the
    layout-evaluation survey exists to criticise, and this instrument's
    own corpus is the reason to take that seriously: its metrics are
    internally hostile, and papering over that with a sum is how a
    drawing that invents a relationship wins a scorecard.

    Args:
        a: The first drawing's element list.
        b: The second drawing's element list.
        labels: Display names for the two drawings.
        weights: Metric weights; defaults to `WEIGHTS`.

    Returns:
        `{"labels", "scores", "vector", "wins", "weighted", "count_winner",
        "sum_winner", "winner", "reason", "gates"}`. `vector` maps each
        metric to its `(a, b)` pair; `wins` counts them; `winner` is a
        label or None, and `reason` says why when it is None.

    Raises:
        ValueError: If `weights` names a metric the vector does not have.
    """
    wts = WEIGHTS if weights is None else weights
    sa, sb = score_layout(a), score_layout(b)
    unknown = set(wts) - set(sa["metrics"])
    if unknown:
        raise ValueError("weights name unknown metrics: %s"
                         % ", ".join(sorted(unknown)))
    vector = {k: (sa["metrics"][k], sb["metrics"][k]) for k in wts}
    wins = {labels[0]: 0, labels[1]: 0, "tie": 0}
    for va, vb in vector.values():
        if abs(va - vb) <= 1e-9:
            wins["tie"] += 1
        else:
            wins[labels[0 if va > vb else 1]] += 1
    weighted = {labels[i]: sum(wts[k] * v[i] for k, v in vector.items())
                for i in (0, 1)}
    count_winner = (None if wins[labels[0]] == wins[labels[1]]
                    else labels[0 if wins[labels[0]] > wins[labels[1]]
                                else 1])
    sum_winner = (None if abs(weighted[labels[0]] - weighted[labels[1]])
                  <= 1e-9 else
                  labels[0 if weighted[labels[0]] > weighted[labels[1]]
                         else 1])
    gates = {labels[i]: [g for g in GATES if s["metrics"][g] < 1.0]
             for i, s in ((0, sa), (1, sb))}
    winner, reason = count_winner, None
    if count_winner != sum_winner:
        winner = None
        reason = ("count says %s, weighted sum says %s — a split verdict "
                  "is a result, not a tie to be broken"
                  % (count_winner, sum_winner))
    elif winner is None:
        reason = "both readings are level"
    elif gates[winner] and not gates[labels[1] if winner == labels[0]
                                    else labels[0]]:
        reason = ("%s outscores the other but fails the hard gate(s) %s, "
                  "which the other passes" % (winner, ", ".join(gates[winner])))
        winner = None
    return {"labels": labels, "scores": (sa, sb), "vector": vector,
            "wins": wins, "weighted": weighted,
            "count_winner": count_winner, "sum_winner": sum_winner,
            "winner": winner, "reason": reason, "gates": gates}


def format_comparison(result: dict) -> str:
    """Render a comparison as the side-by-side vector a reviewer reads.

    Args:
        result: The dict from `compare_layouts`.

    Returns:
        A multi-line report: every metric with both readings and its
        weight, then the two verdicts, the diagnostics compactness sits
        in, and the defect counts.
    """
    la, lb = result["labels"]
    out = ["%-18s %8s %8s %6s" % ("metric", la, lb, "wt")]
    for k, (va, vb) in result["vector"].items():
        out.append("%-18s %8.3f %8.3f %6.2f"
                   % (k, va, vb, WEIGHTS.get(k, 0.0)))
    out.append("%-18s %8.3f %8.3f" % ("WEIGHTED SUM",
                                      result["weighted"][la],
                                      result["weighted"][lb]))
    out.append("wins: %s=%d %s=%d tie=%d"
               % (la, result["wins"][la], lb, result["wins"][lb],
                  result["wins"]["tie"]))
    for label, s in zip((la, lb), result["scores"]):
        out.append("%s: compactness=%.4f (diagnostic, never scored), "
                   "defects=%d" % (label, s["diagnostics"]["compactness"],
                                   len(s["defects"])))
    out.append("WINNER: %s" % (result["winner"] or "none — %s"
                               % result["reason"]))
    return "\n".join(out)
