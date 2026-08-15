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
    (`curved_elbow_spurious_bidi`);
  - and the straightness band that fix introduced was flat, so the SAME
    bow disqualified an 18px approach and a 200px one — a genuine
    head-on pair went unreported at any run length. The band now scales
    with the span it sits on (`long_run_curve_hides_bidi`, task 24);
  - `float_diamond` measured radially from the node center, so its
    answer turned on the direction the endpoint lay in and an endpoint
    at the exact center scored 0 and was never reported — it now reads
    the perpendicular distance to the facet (`float_diamond_center_zero`).

No ported bug is preserved here any longer.

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

# Kieffer et al. (TVCG 2016) describe alignment as node centers sharing a
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
# criticize. Rationale per line; they sum to 1.0.
WEIGHTS: dict[str, float] = {
    # Kieffer et al.'s runner-up, and the only literature metric that
    # agreed with our own judgment on our own corpus (hand-laid scored
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


def _rendered_segments(
    el: dict,
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Segments of the path the renderer actually draws.

    The drop-in replacement for `_abs_segments` in every check that asks
    a question about the PICTURE rather than about the route. A sharp
    arrow's rendered segments ARE its chords, so the swap is free and
    behaviour-identical while `derived_roundness` keeps a route sharp.

    Args:
        el: An arrow element dict with x, y, points, and roundness.

    Returns:
        Consecutive (start, end) point pairs of the flattened path.
    """
    p = rendered_path(el)
    return [(p[i], p[i + 1]) for i in range(len(p) - 1)]


def _bounds(
    segs: list[tuple[tuple[float, float], tuple[float, float]]],
) -> tuple[float, float, float, float] | None:
    """The axis-aligned box a segment list occupies.

    Args:
        segs: Segments in absolute coordinates.

    Returns:
        `(minx, miny, maxx, maxy)`, or None for an empty list.
    """
    if not segs:
        return None
    xs = [c for s in segs for c in (s[0][0], s[1][0])]
    ys = [c for s in segs for c in (s[0][1], s[1][1])]
    return (min(xs), min(ys), max(xs), max(ys))


def _boxes_touch(
    a: tuple[float, float, float, float] | None,
    b: tuple[float, float, float, float] | None,
) -> bool:
    """Whether two bounding boxes overlap at all.

    Args:
        a: The first box, or None.
        b: The second box, or None.

    Returns:
        True when both exist and their extents overlap on both axes.
    """
    if a is None or b is None:
        return False
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


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

    The segments are the DRAWN ones (`_rendered_segments`), not the
    stored chords, because a crossing is a thing a reader sees. Both
    directions of that difference are real and measured: two elbows
    meeting corner to corner whose chords never touch cross TWICE once
    drawn, and a shorter pair whose chords cross once do not cross at
    all once the curve pulls them apart (v0.9 blind-spot 2 spike).

    Flattening multiplies the segment count per arrow by
    `CURVE_SAMPLES`, and this loop is quadratic in it — 330x measured
    end to end at 30 arrows x 5 points, ~3.7 SECONDS. The bbox
    prefilters below are therefore load-bearing rather than an
    optimization: real diagrams are sparse, so almost every arrow pair
    and almost every segment pair is rejected at the cost of four
    comparisons and the flattened cost is paid only where two strokes
    actually come near each other.

    Args:
        elements: Full scene element list; arrows are filtered
            internally.

    Returns:
        A list of `{"a": id, "b": id, "angle": degrees}` dicts, one per
        crossing, in traversal order.
    """
    allsegs = [(a["id"], [(s, _bounds([s])) for s in _rendered_segments(a)])
               for a in _arrows(elements)]
    hulls = [_bounds([s for s, _b in segs]) for _i, segs in allsegs]
    sites: list[dict] = []
    for (i, (i1, s1)), (j, (i2, s2)) in itertools.combinations(
            enumerate(allsegs), 2):
        if not _boxes_touch(hulls[i], hulls[j]):
            continue
        sites.extend({"a": i1, "b": i2, "angle": _acute_angle(a, b)}
                     for a, ab in s1 for b, bb in s2
                     if _boxes_touch(ab, bb)
                     and _segments_intersect(a[0], a[1], b[0], b[1]))
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


def _stretch_axis(
    stretch: list[tuple[float, float]],
) -> tuple[str, float, float, float] | None:
    """Classify a whole DRAWN stretch as one axis-aligned run.

    The naive curvature fix for `shared_corridors` — swap the segment
    source and leave the rest — goes SILENT on real corridors, and that
    is the trap this function exists to avoid. `_axis` classifies each
    segment independently within 2px, and a flattened Catmull-Rom span
    is diagonal for a good third of its length even on a gentle bow (17
    of 40 micro-segments failed classification outright on a 200px
    vertical leg), while the ones that pass carry a continuously
    DRIFTING fixed coordinate (108.9 to 110.7 across ten consecutive
    "vertical" micro-segments) instead of the single constant a straight
    run gives. The pairwise matcher below cannot find a stable pairing
    across two arrows' independently drifting sample grids, so two 200px
    curved verticals 4px apart — obviously one thick stroke — scored
    zero (v0.9 blind-spot 2 spike).

    The fix is `_reads_as_line`'s treatment, one level up: classify the
    stretch ONCE, from its own chord, and let the bow earn a tolerance
    against that chord rather than re-deriving an axis twenty times.

    Two properties make this safe to swap in everywhere:

    - a SHARP stretch is a two-point chord, its spread off its own chord
      is zero, and `_reads_as_line`'s band never goes below `FLAT_BAND`
      — so this returns exactly what `_axis` returned, bit for bit;
    - a curved stretch is judged on the same relative band `false_bidi`
      already argues for, so the two curvature-aware instruments in this
      module agree about what "reads as a line" means.

    Args:
        stretch: The sampled stretch in absolute coordinates.

    Returns:
        `(orientation, fixed_coord, extent_min, extent_max)` from the
        stretch's CHORD, or None when the chord is diagonal or the bow
        wanders too far off it to read as one run.
    """
    if len(stretch) < 2:
        return None
    A = _axis((stretch[0], stretch[-1]))
    if A is None:
        return None
    if not _reads_as_line(stretch, 0 if A[0] == "h" else 1):
        return None
    return A


def _corridor_kind(a: dict, b: dict) -> str:
    """How two corridor-sharing arrows are related through their bindings.

    The corridor instrument answers one geometric question — are these
    two runs collinear and overlapping — and two very different defects
    give the same answer. A CHAIN shares a node at opposite ends
    (`X -> N`, `N -> Z`), so the two runs continue each other and the
    merged stroke deletes N as a step. A FAN shares a node at the SAME
    end, so the two runs are the common approach of edges that diverge
    later; the picture is thick, not wrong, and the repair is the
    auto-fan rather than a re-route.

    Worth splitting because the corpus is lopsided in a way that reading
    the raw count hides: all 6 corridor findings across the 24 frozen
    artifacts are fans and none is a chain (measured 2026-08-14). Anyone
    treating this instrument as a merged-stroke detector — which is what
    `merged_stroke_caught_by_corridor` records it as, correctly, for the
    ELK scene — is reading six false positives on our own drawings.

    Args:
        a: The first arrow element.
        b: The second arrow element.

    Returns:
        `"chain"`, `"fan"`, or `"unrelated"` when no binding is shared.
        `"chain"` wins a tie, since two arrows related both ways are a
        2-cycle and the through-reading is the damaging one.
    """
    ea = ((a.get("startBinding") or {}).get("elementId"),
          (a.get("endBinding") or {}).get("elementId"))
    eb = ((b.get("startBinding") or {}).get("elementId"),
          (b.get("endBinding") or {}).get("elementId"))
    if (ea[0] and ea[0] == eb[1]) or (ea[1] and ea[1] == eb[0]):
        return "chain"
    if (ea[0] and ea[0] == eb[0]) or (ea[1] and ea[1] == eb[1]):
        return "fan"
    return "unrelated"


def shared_corridors(
    elements: list[dict], tol: float = 16, minover: float = 60,
) -> list[dict]:
    """Find near-collinear arrow pairs that read as one stroke.

    Ported from corridor.py, and rebuilt for curvature by v0.9: the unit
    of comparison is a whole DRAWN stretch (`_stretch_axis`), not a
    stored chord and not a flattened micro-segment. See `_stretch_axis`
    for why the obvious swap is worse than doing nothing.

    Cost, unlike `crossing_sites`', does not move: there is one stretch
    per stored span either way, so no prefilter is owed here — the
    flattening is linear and the pairing loop is the same size it always
    was.

    Args:
        elements: Full scene element list; arrows are filtered internally.
        tol: Max lateral separation (px) that still reads as one stroke.
        minover: Min shared extent (px) needed to matter on its own; a
            small negative overlap (abutting runs) also matters if both
            segments are individually at least 60px long — that 60 is
            hardcoded in the source, not tied to this parameter.

    Returns:
        A list of `{"a": id, "b": id, "overlap": float, "kind": str}`
        dicts, one per corridor-sharing pair (first qualifying segment
        pair wins). `kind` is `_corridor_kind`'s verdict and is
        reported rather than filtered on: the geometry is the finding,
        and which defect it is is the caller's question.
    """
    arrows = _arrows(elements)
    hits: list[dict] = []
    for a, b in itertools.combinations(arrows, 2):
        for s1 in rendered_stretches(a):
            A = _stretch_axis(s1)
            if not A:
                continue
            for s2 in rendered_stretches(b):
                B = _stretch_axis(s2)
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
                    hits.append({"a": a["id"], "b": b["id"], "overlap": ov,
                                 "kind": _corridor_kind(a, b)})
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


FLAT_BAND = 2.0
# The band the ported code applied at every length, kept as a FLOOR so
# nothing this function used to call a line stops being one.
TILT_RATIO = 1 / 14.0
# ...and the ratio that floor implies at 28px, the shortest final span
# in the 24-artifact frozen corpus (measured 2026-08-14; median 176,
# max 668). 28px is where the two rules agree, so above it the band is
# a constant 4.1 degrees of tilt instead of a constant 2px of
# displacement.
#
# THIS NUMBER IS THEORY-MOTIVATED, NOT MEASURED — the same caveat
# `false_bidi` carries about its whole premise. No perception result
# gives the angle at which a bowed stroke stops reading as a straight
# one; the curvature-detection literature would put it an order of
# magnitude tighter and call 3.7% of bow discriminable, which is the
# opposite verdict from the one the blind spot demands. What IS
# defensible is the shape of the rule rather than its constant: a fixed
# pixel band is a fixed ANGLE only at one length, and it varied 37-fold
# in strictness across one corpus (11% of tilt admitted at 28px, 0.3%
# at 668px) purely as an artifact of how long the span happened to be.


def _reads_as_line(
    stretch: list[tuple[float, float]], idx: int,
) -> bool:
    """Test whether a rendered stretch reads as one straight axis line.

    A straight chord's spread on the off-axis is exactly the endpoint
    difference the ported code compared, so at spans of 28px and under
    this is bit-for-bit the same 2px test — it only stops giving a curve
    the benefit of its chord.

    Above that the band grows with the span, and it has to: the bow on a
    curved elbow is set by the leg BEFORE it, not by the span it lands
    on, so a flat band read the same 7.4px displacement as
    disqualifying whether it sat on 18px of approach or 200px of it.
    At 200px the last quarter is dead straight on the axis, the heads
    are 2px apart, and a reader sees one bidirectional line the check
    could not report at any length (`long_run_curve_hides_bidi`). The
    discriminator is therefore relative: 7.4px is most of an 18px
    approach and a rounding error on a 200px one.

    `max` and not a replacement, so the change is one-directional —
    every stretch the flat band admitted is still admitted, and
    `false_bidi` can only gain findings from this, never lose them. That
    is what keeps `curved_elbow_spurious_bidi` silent: at 18px the band
    is still exactly 2px, and 7.4px of bow still disqualifies it.

    Args:
        stretch: The sampled stretch in absolute coordinates.
        idx: 0 to test horizontality (spread in y), 1 for verticality.

    Returns:
        True if the whole stretch stays inside the band its own length
        earns — 2px, or 1/14 of its on-axis extent, whichever is wider.
    """
    off = [p[1 - idx] for p in stretch]
    on = [p[idx] for p in stretch]
    extent = max(on) - min(on)
    return max(off) - min(off) <= max(FLAT_BAND, TILT_RATIO * extent)


def false_bidi(elements: list[dict]) -> list[dict]:
    """Find arrow pairs whose final stretches read as one bidirectional line.

    Ported from falsebidi.py: both final stretches are (near-)horizontal
    or both (near-)vertical, on the same line, running in opposite
    directions, with overlapping extent — the visual signature of two
    one-way arrows that together read as a single bidirectional
    junction. What changed in WP4 is which geometry is asked: the
    rendered path, not the stored chord.

    HONESTY NOTE (v0.9 WP4b): **no study compares double-headed arrows
    against paired opposed single arrows.** This check's premise — that
    a reader merges the two into one bidirectional relation and so reads
    a symmetry the model does not assert — is well motivated by
    continuity theory (Ware et al. 2002, continuity among the strongest
    predictors of path-tracing difficulty) and by Holten & van Wijk's
    CHI 2009 result that arrowhead OVERLAP is precisely why arrowheads
    underperform for direction tasks. It is not empirically established,
    and neither is its converse. Every tolerance below inherits that
    status: the 2px straightness floor, the 8px head alignment, the
    -24px overlap and the 1/14 tilt ratio are engineering choices with
    recorded derivations, not measured thresholds. Treat a finding here
    as a question to put to the drawing, not a verdict on it, and do not
    let the arithmetic's precision imply a confidence the premise has
    not earned.

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


HEAD_SECANT_T = 0.7
# The forward-t the client reads an arrowhead's direction from, mirrored
# from `canvas.HEAD_SECANT_T` rather than imported: `instruments.py` is
# deliberately standalone, and the value's provenance
# (`getArrowheadPoints`) is recorded at the canvas.py definition.


def arrival_squareness(elements: list[dict]) -> list[dict]:
    """How far off square each bound arrow arrives, as the reader sees it.

    The router emits nothing but orthogonal routes, so every arrival is
    square BY CONSTRUCTION in stored geometry — measured over the frozen
    corpus, all 38 curvature-eligible arrows deviate 0.00 degrees on
    their stored chords. Curvature breaks that silently: the drawn
    secant swings off the axis the route chose, the arrowhead is drawn
    along the swung secant, and the picture arrives at a slant while
    every number the router checked still says square.

    This is the measure for what review F12 named and no check owned: on
    the ungated all-curved corpus the deviation ran to a median of 9.2
    and a maximum of 40.6 degrees, on `argus-run-flow`'s `t-agg` pair.
    It exists because that is the endpoint-judgment complaint from r5-14
    — the one `derived_roundness`' docstring dropped rather than
    rebutted — and a complaint nobody can measure comes back as taste in
    a later assessment round instead of as a number in this one.

    A MEASURE, not a finding, and deliberately absent from
    `enumerate_defects` and `score_layout`. Two reasons. It has no
    defect threshold anybody has earned — no perception result gives the
    angle at which a slanted arrival reads as wrong, the same caveat
    `false_bidi` and `TILT_RATIO` carry. And folding it into the defect
    list would move the layout score of every artifact carrying a curve,
    which is the churn review F6 warns about, for a number no consumer
    has asked for yet.

    Its standing use is as the independent reading of `gate_curvature`'s
    promise. The gate refuses any candidate whose arrival leans past
    `canvas.NEAR_AXIS` (~14.04 degrees), measured through canvas.py's own
    `_arrival_lean`; this walks the rendered path from this module's
    primitives instead, so a pin over the loaded corpus checks the gate
    against code that shares none of its arithmetic.

    Args:
        elements: Full scene element list; arrows are filtered
            internally. Read as loaded — whatever `roundness` each arrow
            carries is the shape measured.

    Returns:
        One `{"arrow": id, "node": id, "end": "start"|"end",
        "deg": float}` per BOUND arrival, `deg` being the angle between
        the drawn secant and the nearer cardinal, in `[0, 45]`.
        Unbound ends are skipped: squareness is about meeting a box.
    """
    out: list[dict] = []
    for a in _arrows(elements):
        stretches = rendered_stretches(a)
        if not stretches:
            continue
        for at_end, battr in ((True, "endBinding"), (False, "startBinding")):
            node = (a.get(battr) or {}).get("elementId")
            if not node:
                continue
            span = stretches[-1][::-1] if at_end else stretches[0]
            if len(span) < 2:
                continue
            # the same sample the head is drawn along: t=0.7 of the way
            # along the span, counted from the bound end inward.
            k = min(max(round((1.0 - HEAD_SECANT_T) * (len(span) - 1)), 1),
                    len(span) - 1)
            dx = abs(span[0][0] - span[k][0])
            dy = abs(span[0][1] - span[k][1])
            if max(dx, dy) < 1e-9:
                continue
            deg = math.degrees(math.atan2(min(dx, dy), max(dx, dy)))
            out.append({"arrow": a["id"], "node": node,
                        "end": "end" if at_end else "start", "deg": deg})
    return out


def _dist_to_diamond(px: float, py: float, n: dict) -> float:
    """How far a point misses a diamond node's drawn outline by, in px.

    Ported from floatdia.py's `dist_to_diamond` and rebuilt by v0.9 WP4:
    the port measured RADIALLY from the node center, scaling the center
    ray out to the boundary and subtracting, which fails twice. The
    answer depended on the direction the endpoint happened to lie in
    rather than on how far it sat from the outline; and at the center
    there is no ray at all, so the `t == 0` guard returned `r` — zero —
    and an endpoint pinned dead-center, structurally the worst binding
    there is, scored a perfect gap and was never reported
    (`float_diamond_center_zero`).

    The perpendicular distance to the facet has neither problem. Folding
    the point into the first quadrant collapses the four facets onto the
    one plane `|dx|/a + |dy|/b = 1`, and the distance to it is exact for
    every interior point and every exterior point abreast of a facet;
    only out past a vertex does it under-read, which errs toward
    silence.

    Derived here rather than imported from `canvas.shape_clearance`,
    which computes the same number. These instruments are the measure of
    what canvas.py draws, and sharing its geometry would make a bug in
    that geometry invisible to them — `float_diamond` reading the true
    outline is exactly what caught `edge_anchor` anchoring the router on
    the bounding box (v0.9 WP4), months before the lint agreed.

    UNSIGNED, and not because the port's `abs()` survived by accident:
    an endpoint 50px inside a diamond is as unbound as one 50px outside,
    both poles are what this check exists to flag, and the magnitude is
    the miss. Which side it missed on is not returned because no caller
    has ever asked for it.

    Args:
        px: Endpoint x in absolute coordinates.
        py: Endpoint y in absolute coordinates.
        n: The diamond node element dict (x, y, width, height).

    Returns:
        Distance in px from the rhombus outline; 0 exactly on it, and 0
        for a node with no area, which has no outline to miss.
    """
    a, b = n["width"] / 2.0, n["height"] / 2.0
    if a <= 0 or b <= 0:
        return 0.0
    dx, dy = abs(px - (n["x"] + a)), abs(py - (n["y"] + b))
    return abs(dx / a + dy / b - 1) / (1 / (a * a) + 1 / (b * b)) ** 0.5


def float_diamond(elements: list[dict]) -> list[dict]:
    """Flag arrow endpoints bound to a diamond but sitting off its boundary.

    Ported from floatdia.py.

    Args:
        elements: Full scene element list; arrows and their bound
            diamond nodes are looked up internally.

    Returns:
        A list of `{"arrow": id, "node": id, "gap": float}` dicts, one
        per bound endpoint sitting more than 12px off the outline, on
        either side of it (see `_dist_to_diamond`).
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
    """Score how many node centers share a coordinate with another node.

    Kieffer et al.'s alignment metric, from their prose description
    (center-based, 6px tolerance) rather than their code. Second-best
    predictor of human preference in their study, and the one literature
    metric that agreed with our judgment on our own corpus.

    Args:
        elements: Full scene element list.

    Returns:
        The fraction of nodes aligned with at least one other node. 1.0
        for a drawing with fewer than two nodes, which has nothing to
        misalign.
    """
    centers = [(n["x"] + n["width"] / 2.0, n["y"] + n["height"] / 2.0)
               for n in _nodes(elements)]
    if len(centers) < 2:
        return 1.0
    aligned = 0
    for i, c in enumerate(centers):
        for j, o in enumerate(centers):
            if i != j and (abs(c[0] - o[0]) <= GRID_TOL
                           or abs(c[1] - o[1]) <= GRID_TOL):
                aligned += 1
                break
    return aligned / float(len(centers))


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
        floating diamond endpoint, and overlapping node pair. `"kind"`
        is the outer defect class every consumer discriminates on, so a
        corridor's own chain/fan/unrelated verdict rides alongside it
        under `"corridor_kind"` rather than in the key it would
        otherwise be overwritten by.
    """
    sites = crossing_sites(elements)
    _n, pairs = edge_crossings(elements)
    out: list[dict] = [{"kind": "crossing", "a": a, "b": b}
                       for a, b in pairs]
    out += [{"kind": "shallow_crossing", "a": s["a"], "b": s["b"],
             "angle": s["angle"]}
            for s in sites if s["angle"] < SHALLOW_CROSSING_DEG]
    # `dict(h, kind=...)` lets the kwarg win, and `shared_corridors` is the
    # ONE source here whose own dicts already carry a `kind` — so this line
    # silently ate chain/fan/unrelated for every consumer downstream
    # (`score_layout` embeds this list verbatim; `compare_layouts` builds on
    # that). The other three merges below are pure additions; their sources
    # set no `kind`. Found by the backlog-residue spike, 2026-08-15.
    out += [dict(h, kind="shared_corridor", corridor_kind=h.get("kind"))
            for h in shared_corridors(elements)]
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
    # the DRAWN length: a bow adds 1-2% over its chord at the magnitudes
    # this codebase produces, which is diagnostic-only (no WEIGHTS
    # entry) but free to get right now that the flattening is here
    edge_len = sum(math.dist(p, q) for a in arrows
                   for p, q in _rendered_segments(a))
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
    layout-evaluation survey exists to criticize, and this instrument's
    own corpus is the reason to take that seriously: its metrics are
    internally hostile, and papering over that with a sum is how a
    drawing that invents a relationship wins a scorecard.

    Args:
        a: The first drawing's element list.
        b: The second drawing's element list.
        labels: Display names for the two drawings.
        weights: Metric weights; defaults to `WEIGHTS`.

    Returns:
        `{"labels", "scores", "vector", "wins", "ties", "weighted",
        "count_winner", "sum_winner", "winner", "reason", "gates"}`.
        `vector` maps each metric to its `(a, b)` pair; `wins` counts the
        metrics each label took and `ties` the rest; `winner` is a label
        or None, and `reason` says why when it is None.

    Raises:
        ValueError: If the two labels are the same, or if `weights` names
            a metric the vector does not have.
    """
    # Every per-label reading below is keyed by label, so two drawings
    # sharing a name would silently collapse into one column and report a
    # comparison of a drawing with itself. Ties are counted separately for
    # the same reason: a label is user-supplied, so keeping "tie" in the
    # same dict handed a drawing named "tie" every metric it drew.
    if labels[0] == labels[1]:
        raise ValueError("the two layouts need distinct labels; both are %r"
                         % (labels[0],))
    wts = WEIGHTS if weights is None else weights
    sa, sb = score_layout(a), score_layout(b)
    unknown = set(wts) - set(sa["metrics"])
    if unknown:
        raise ValueError("weights name unknown metrics: %s"
                         % ", ".join(sorted(unknown)))
    vector = {k: (sa["metrics"][k], sb["metrics"][k]) for k in wts}
    wins = {labels[0]: 0, labels[1]: 0}
    ties = 0
    for va, vb in vector.values():
        if abs(va - vb) <= 1e-9:
            ties += 1
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
            "wins": wins, "ties": ties, "weighted": weighted,
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
                  result["ties"]))
    for label, s in zip((la, lb), result["scores"]):
        out.append("%s: compactness=%.4f (diagnostic, never scored), "
                   "defects=%d" % (label, s["diagnostics"]["compactness"],
                                   len(s["defects"])))
    out.append("WINNER: %s" % (result["winner"] or "none — %s"
                               % result["reason"]))
    return "\n".join(out)
