"""The focus round-trip specimen — ONE scene, read by two oracles.

The defect this pins is LATENT: as loaded, the client draws every bound
foot exactly where `canvas.py` stored it, and only re-derives it through
`updateBoundPoint` when the bound node changes (spike-focus-verify §1a,
live). So a specimen is worth having only if something re-derives it,
and the two things that can are a browser (`focus.spec.ts`, the
authoritative referee) and a transcription of the client's own algebra
(`TestFocusRoundTrip` in `test_backend.py`, the fast neighbour). They
disagree about nothing because they read the SAME scene from here.

THE SPECIMEN HAS TWO FEET AND THAT IS THE DESIGN. `determineFocusPoint`
selects its corner with strict `>` tests, so a focus value that puts the
focus point exactly collinear with the adjacent point falls through to
the WRONG corner. For a perpendicular approach to a rectangle that is
not an edge case — it is every case, because the correct magnitude
`|d| / (w/2)` is exactly the scale that lands the corner ray on the
adjacent point's own line. Which side of the cliff you come down on is
decided by the sign, so a single left-hand foot flips green under a fix
that still draws the right-hand foot 91px away (spike-anchor §6a). Both
quarter points, or the pin proves nothing.

The midpoint foot is the third row and it is the anti-regression: focus
0 through a side midpoint is exact today and must stay exact.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] /
                       "skills" / "wysiwyg-grilling" / "scripts"))
import canvas

HUB_W, HUB_H = 200.0, 64.0
# 240px of straight run out from the foot, so the approach is
# unambiguously perpendicular to the SIDE and the adjacent point is
# nowhere near a corner. A `lean` slides it along the side from there.
APPROACH = 240.0
GAP = 6
ADAPTIVE = {"type": 3}
# `(name, type, roundness, side, offset along that side, lean)`.
#
# ROWS 1-3 ARE THE ORIGINAL PIN and their arithmetic must not move: the
# quarter points of a square-cornered rectangle's bottom side, approached
# square. |focus| lands on exactly 0.5, a 3dp-exact value, so the
# collinearity that triggers `determineFocusPoint`'s selection cliff is
# exact in float and is not rounded away by luck the way a 1/3 offset
# rounds it away (spike-focus-verify §4c).
#
# ROWS 4-6 ARE THE CONIC/ROUNDED HALF, added by v0.9
# TASK-FOCUS-FOLLOWUP-A, and each is chosen to be as far as possible from
# what the square-cornered box model would say — which is what makes them
# red-provable rather than merely present. Where the browser DRAWS each
# one, against what this file said before it learned the other two
# shapes (`{"type": "rectangle"}` for the focus point AND the outline,
# which is literally what `tangential_slip` used to build), in this
# scene's own coordinates:
#
#     diamond   drawn (2042.000, 455.552) vs the box model's
#               (1966.401, 470.000) — 76.97px apart
#     ellipse   drawn (2410.401, 411.695) vs (2394.000, 403.283) — 18.43px
#     round     drawn (2968.910, 466.025) vs (2970.949, 470.000) — 4.47px
#
# All six drawn points are the BROWSER's, read at the commit that added
# these rows, and the transcription reproduces every one of them to three
# decimals.
#
# The rounded rectangle's divergence is the smallest AND the most
# instructive: it is almost entirely NORMAL. A rounded box's gap outline
# is the box grown by `offset` only at the corners, and the straight
# sides between them are displaced by `offset * cos` — 2.025px here, not
# 6 — so the client draws a rounded node's foot four pixels closer to the
# ink than a square one's. The along-side component is not zero either,
# but it is second-order: it appears only under a LEAN, because a lower
# side line met at an angle is met sooner (2.090px at this row's 120px
# lean, 0.04px square-on).
SPECIMENS = (
    ("left", "rectangle", None, "bottom", HUB_W / 2.0 - 50.0, 0.0),
    ("mid", "rectangle", None, "bottom", HUB_W / 2.0, 0.0),
    ("right", "rectangle", None, "bottom", HUB_W / 2.0 + 50.0, 0.0),
    ("diamond", "diamond", None, "bottom", HUB_W * 12.0 / 14.0, 0.0),
    ("ellipse", "ellipse", None, "left", HUB_H * 2.0 / 14.0, -120.0),
    ("round", "rectangle", ADAPTIVE, "bottom", HUB_W * 5.0 / 14.0, 120.0),
)
# outward normal and along-side tangent, per side name
SIDE_AXES = {"top": ((0.0, -1.0), (1.0, 0.0)),
             "bottom": ((0.0, 1.0), (1.0, 0.0)),
             "left": ((-1.0, 0.0), (0.0, 1.0)),
             "right": ((1.0, 0.0), (0.0, 1.0))}


def probe_elements() -> list[dict[str, Any]]:
    """The specimen scene: one hub per row, one bound foot each.

    Separate hubs rather than several feet on one side, because
    `fan_attach_points` respaces a side carrying two or more feet and
    would slide each specimen off the position it exists to test. A side
    carrying one foot is left exactly where it was put — and still has
    its focus re-stamped by the same call site, which is the code under
    test.

    The foot is placed by `_fan_point`, which pulls a box slot onto the
    node's DRAWN OUTLINE. On a square-cornered rectangle that is the box
    slot itself, bit for bit, so rows 1-3 stand exactly where they always
    did; on a rhombus or an ellipse it is the only placement that puts a
    foot on ink rather than in blank canvas, and it is how the shipped
    fan places one.

    Returns:
        The scene element list, focus values already stamped by
        `fan_attach_points` — i.e. by the shipped call site, whatever
        that call site currently computes.
    """
    els: list[dict[str, Any]] = []
    for i, (name, kind, rnd, side, off, lean) in enumerate(SPECIMENS):
        hx, hy = 400.0 + i * 500.0, 400.0
        hub = {
            "id": "hub-%s" % name, "type": kind,
            "x": hx, "y": hy, "width": HUB_W, "height": HUB_H,
            "angle": 0, "strokeColor": "#1e1e1e",
            "backgroundColor": "transparent", "fillStyle": "solid",
            "strokeWidth": 1, "strokeStyle": "solid", "roughness": 1,
            "opacity": 100, "groupIds": [], "frameId": None,
            "roundness": rnd, "seed": 1 + i, "version": 1,
            "versionNonce": 0, "isDeleted": False, "updated": 1,
            "link": None, "locked": False,
            "customData": {"role": "node", "author": "agent"},
            "boundElements": [{"id": "probe-%s" % name, "type": "arrow"}],
        }
        els.append(hub)
        (nx, ny), (tx, ty) = SIDE_AXES[side]
        foot_x, foot_y = canvas._fan_point(hub, side, off)
        adj_x = foot_x + nx * APPROACH + tx * lean
        adj_y = foot_y + ny * APPROACH + ty * lean
        arrow: dict[str, Any] = {
            "id": "probe-%s" % name, "type": "arrow",
            "x": adj_x, "y": adj_y,
            "width": abs(foot_x - adj_x), "height": abs(foot_y - adj_y),
            "points": [[0, 0], [foot_x - adj_x, foot_y - adj_y]],
            "angle": 0, "strokeColor": "#1e1e1e",
            "backgroundColor": "transparent", "fillStyle": "solid",
            "strokeWidth": 1, "strokeStyle": "solid", "roughness": 1,
            "opacity": 100, "groupIds": [], "frameId": None,
            "roundness": None, "seed": 100 + i, "version": 1,
            "versionNonce": 0, "isDeleted": False, "updated": 1,
            "link": None, "locked": False, "boundElements": [],
            "startArrowhead": None, "endArrowhead": "arrow",
            "elbowed": False, "lastCommittedPoint": None,
            "customData": {"role": "edge", "author": "agent"},
            "startBinding": None,
            "endBinding": {"elementId": "hub-%s" % name,
                           "focus": 0, "gap": GAP},
        }
        canvas._stamp_route(arrow)          # server-owned, so the fan reads it
        els.append(arrow)
    # THE CALL SITE, not a copy of it. `fan_attach_points` re-stamps the
    # focus of every server-owned bound endpoint it can see, fanned or
    # not, and it is one of the exactly two places a focus value is ever
    # written. Running it here is what makes this specimen measure the
    # shipped derivation rather than a re-implementation of it.
    canvas.fan_attach_points(els)
    return els


def hub_of(rec: dict[str, Any]) -> dict[str, Any]:
    """The bound node a manifest record describes, as the model wants it.

    One place builds this, because a record read as a `rectangle` when
    it names an ellipse is exactly the narrowing this file was widened
    to close.

    Args:
        rec: One `probe_manifest` record.

    Returns:
        The element dict `client_draws` and `solve_focus` both take.
    """
    hx, hy, w, h = rec["hub_box"]
    return {"type": rec["shape"], "x": hx, "y": hy, "width": w, "height": h,
            "roundness": rec["roundness"]}


def probe_manifest(els: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What each specimen claims, in the form both oracles check.

    Args:
        els: The scene from `probe_elements`.

    Returns:
        One record per arrow: the arrow and hub ids, the hub's shape,
        roundness and box, the stored foot, the adjacent point the client
        will aim from, the gap, the focus the server wrote, the outward
        normal and along-side tangent the slip is resolved onto, and
        `predicted` — where the transcription says the client will redraw
        the foot, which is the number the browser referee grades.
    """
    ix = {e["id"]: e for e in els}
    out = []
    for name, kind, rnd, side, _off, _lean in SPECIMENS:
        a = ix["probe-%s" % name]
        hub = ix["hub-%s" % name]
        pts = a["points"]
        normal, tangent = SIDE_AXES[side]
        rec = {
            "name": name, "arrow": a["id"], "hub": hub["id"],
            "shape": kind, "roundness": rnd, "side": side,
            "hub_box": [hub["x"], hub["y"], hub["width"], hub["height"]],
            "foot": [a["x"] + pts[-1][0], a["y"] + pts[-1][1]],
            "adjacent": [a["x"] + pts[-2][0], a["y"] + pts[-2][1]],
            "gap": a["endBinding"]["gap"],
            "focus": a["endBinding"]["focus"],
            "normal": list(normal), "tangent": list(tangent),
        }
        rec["predicted"] = list(client_draws(
            hub_of(rec), rec["focus"], rec["gap"], rec["adjacent"],
            rec["foot"]))
        out.append(rec)
    return out


# ---------------------------------------------------------------------
# THE CLIENT'S OWN ALGEBRA, TRANSCRIBED — the fast neighbour's oracle.
#
# Read straight off the PINNED bundle
# `node_modules/@excalidraw/excalidraw/dist/dev/chunk-4FTI6OG3.js`
# (Excalidraw 0.18.1), and deliberately shaped like the bundle rather
# than like `canvas.solve_focus`'s inverse of it — the four-entry
# `selected` array below is the client's, expanded, not factored. Two
# independent readings of one source is the whole point: a transcription
# that shared structure with the thing it checks would agree with it
# while both were wrong.
#
# EXACT, not approximate, for this specimen: every hub is
# `roundness: null`, so `deconstructRectanguloidElement` (:7404) takes
# its `roundness <= 0` branch and the gap outline is the box grown by
# `offset` on all four sides. The rounded branch offsets each corner
# diagonally and this model would be ~3px out; `probe_elements` avoids
# it by construction.
#
# `angle = 0` throughout, as every server-authored node is, so the four
# `pointRotateRads` calls the client keeps are identities and are
# dropped. A rotated node is untested here and in `solve_focus`.
# ---------------------------------------------------------------------

PRECISION = 1e-4                                    # bundle :680


def _cross(a: tuple[float, float], b: tuple[float, float]) -> float:
    """`vectorCross` (:725).

    Args:
        a: First vector.
        b: Second vector.

    Returns:
        The 2D cross product's scalar.
    """
    return a[0] * b[1] - b[0] * a[1]


def _from(p: Any, q: Any) -> tuple[float, float]:
    """`vectorFromPoint` — the vector q -> p.

    Args:
        p: Head point.
        q: Tail point.

    Returns:
        `(px - qx, py - qy)`.
    """
    return (p[0] - q[0], p[1] - q[1])


def client_focus_point(hub: dict[str, Any], focus: float,
                       adj: Any) -> tuple[float, float]:
    """`determineFocusPoint` (:11748), transcribed.

    THE SHAPE TEST HERE IS `type === "diamond"` AND NOTHING ELSE, which
    is the one thing about this function a reader guesses wrong. An
    ELLIPSE takes the `else` branch and is aimed at with the RECTANGLE
    CORNER SET — the scaled corners of its bounding box, which are
    points no part of the ellipse is ever drawn near. The client is
    internally inconsistent about it and deliberately so: the OUTLINE it
    then intersects that aim against is a real ellipse
    (`intersectEllipseWithLineSegment`, :7845). Verified against the
    pinned bundle at :11756, not inherited: the ternary reads
    `element.type === "diamond" ? [side midpoints] : [box corners]`.

    Rounded corners do not enter here either — `roundness` is read by
    the deconstruction functions, never by the focus point.

    Args:
        hub: The bound node.
        focus: The stored focus value.
        adj: The adjacent path point, in scene coordinates.

    Returns:
        The point the client aims its interceptor ray at.
    """
    w, h = hub["width"], hub["height"]
    cx, cy = hub["x"] + w / 2.0, hub["y"] + h / 2.0
    if focus == 0:
        return (cx, cy)
    if hub["type"] == "diamond":
        raw = [(hub["x"], cy), (cx, hub["y"]), (hub["x"] + w, cy),
               (cx, hub["y"] + h)]
    else:
        raw = [(hub["x"], hub["y"]), (hub["x"] + w, hub["y"]),
               (hub["x"] + w, hub["y"] + h), (hub["x"], hub["y"] + h)]
    c = [(cx + (p[0] - cx) * abs(focus), cy + (p[1] - cy) * abs(focus))
         for p in raw]
    pos = focus > 0
    selected = [
        _cross(_from(adj, c[0]), _from(c[1], c[0])) > 0 and (   # TOP
            _cross(_from(adj, c[1]), _from(c[2], c[1])) < 0 if pos else
            _cross(_from(adj, c[3]), _from(c[0], c[3])) < 0),
        _cross(_from(adj, c[1]), _from(c[2], c[1])) > 0 and (   # RIGHT
            _cross(_from(adj, c[2]), _from(c[3], c[2])) < 0 if pos else
            _cross(_from(adj, c[0]), _from(c[1], c[0])) < 0),
        _cross(_from(adj, c[2]), _from(c[3], c[2])) > 0 and (   # BOTTOM
            _cross(_from(adj, c[3]), _from(c[0], c[3])) < 0 if pos else
            _cross(_from(adj, c[1]), _from(c[2], c[1])) < 0),
        _cross(_from(adj, c[3]), _from(c[0], c[3])) > 0 and (   # LEFT
            _cross(_from(adj, c[0]), _from(c[1], c[0])) < 0 if pos else
            _cross(_from(adj, c[2]), _from(c[3], c[2])) < 0),
    ]
    if selected[0]:
        return c[1] if pos else c[0]
    if selected[1]:
        return c[2] if pos else c[1]
    if selected[2]:
        return c[3] if pos else c[2]
    return c[0] if pos else c[3]


def _seg_hit(seg: Any, side: Any) -> tuple[float, float] | None:
    """`lineSegmentIntersectionPoints` (:858), transcribed.

    `linesIntersectAt` (:803) plus both on-segment tests at `PRECISION`
    (:821).

    Args:
        seg: `((x, y), (x, y))` — the interceptor.
        side: `((x, y), (x, y))` — one outline side.

    Returns:
        The intersection point, or None when the lines are parallel or
        the crossing falls off either segment.
    """
    a1, b1 = seg[1][1] - seg[0][1], seg[0][0] - seg[1][0]
    a2, b2 = side[1][1] - side[0][1], side[0][0] - side[1][0]
    d = a1 * b2 - a2 * b1
    if d == 0:
        return None
    c1 = a1 * seg[0][0] + b1 * seg[0][1]
    c2 = a2 * side[0][0] + b2 * side[0][1]
    p = ((c1 * b2 - c2 * b1) / d, (a1 * c2 - a2 * c1) / d)
    for ln in (side, seg):
        cxx, cyy = ln[1][0] - ln[0][0], ln[1][1] - ln[0][1]
        sq = cxx * cxx + cyy * cyy
        t = ((p[0] - ln[0][0]) * cxx + (p[1] - ln[0][1]) * cyy) / sq \
            if sq else -1.0
        t = 0.0 if t < 0 else (1.0 if t > 1 else t)
        dx = p[0] - (ln[0][0] + t * cxx)
        dy = p[1] - (ln[0][1] + t * cyy)
        if (dx * dx + dy * dy) ** 0.5 >= PRECISION:
            return None
    return p


# ---------------------------------------------------------------------
# THE GAP-EXPANDED OUTLINE, PER SHAPE — `intersectElementWithLineSegment`
# (:7770) and the three deconstructions it dispatches to.
#
# THIS HALF IS WHY THE FIRST CUT OF THIS FILE WAS RECTANGLES ONLY, and
# why 68 of the corpus's 346 bound endpoints were asserted by nothing
# (TASK-FOCUS §6 concern 1; the reviewer's adjudication is what this
# section closes). The focus POINT above is already type-correct for
# every shape — it has exactly one `diamond` test and no others. What was
# missing is what the aim is then intersected AGAINST, and that is
# genuinely three different pieces of geometry:
#
#   rectangle        four straight sides, box grown by `gap` (:7411)
#   rectangle+round  four sides displaced DIAGONALLY per corner, plus
#                    four cubic Beziers (:7436) — the sides are pushed
#                    out by less than `gap`, which is the ~3px the
#                    original docstring named and declined to model
#   diamond          four straight facets (:7581), the vertices from
#                    `getDiamondPoints`
#   ellipse          a real ellipse, semi-axes grown by `gap` (:7845),
#                    and the only shape whose intersection is closed-form
#
# The transcription stays shaped like the bundle rather than factored,
# for the reason the section above gives: two independent readings of one
# source, so a model that drifted toward `canvas.py` would be caught.
# ---------------------------------------------------------------------

DEFAULT_PROPORTIONAL_RADIUS = 0.25                  # bundle :343
DEFAULT_ADAPTIVE_RADIUS = 32.0                      # bundle :344
# ROUNDNESS (:345): LEGACY 1, PROPORTIONAL_RADIUS 2, ADAPTIVE_RADIUS 3.
ROUNDNESS_PROPORTIONAL = (1, 2)
ROUNDNESS_ADAPTIVE = 3


def _corner_radius(x: float, hub: dict[str, Any]) -> float:
    """`getCornerRadius` (:8252).

    Args:
        x: The dimension the radius is taken against.
        hub: The element, read for its `roundness`.

    Returns:
        The corner radius in px, 0 when the element is square-cornered.
    """
    r = hub.get("roundness") or None
    t = None if r is None else r.get("type")
    if t in ROUNDNESS_PROPORTIONAL:
        return x * DEFAULT_PROPORTIONAL_RADIUS
    if t == ROUNDNESS_ADAPTIVE:
        v = r.get("value")
        fixed = DEFAULT_ADAPTIVE_RADIUS if v is None else float(v)
        if x <= fixed / DEFAULT_PROPORTIONAL_RADIUS:
            return x * DEFAULT_PROPORTIONAL_RADIUS
        return fixed
    return 0.0


def _push(v: tuple[float, float], p: Any) -> tuple[float, float]:
    """`pointFromVector(v, p)` — p displaced by v.

    Args:
        v: The displacement.
        p: The point.

    Returns:
        `(px + vx, py + vy)`.
    """
    return (p[0] + v[0], p[1] + v[1])


def _outward(p: Any, c: Any, offset: float) -> tuple[float, float]:
    """`vectorScale(vectorNormalize(vectorFromPoint(p, c)), offset)`.

    Args:
        p: The point the offset direction runs toward.
        c: The centre it runs from.
        offset: How far to go, in px.

    Returns:
        The displacement vector.
    """
    dx, dy = p[0] - c[0], p[1] - c[1]
    m = (dx * dx + dy * dy) ** 0.5
    return (0.0, 0.0) if m == 0 else (dx / m * offset, dy / m * offset)


def _bezier(c: Any, t: float) -> tuple[float, float]:
    """`bezierEquation` (:922) — a cubic Bezier at parameter t.

    Args:
        c: The four control points.
        t: The parameter.

    Returns:
        The point on the curve.
    """
    u = 1.0 - t
    return tuple(u ** 3 * c[0][i] + 3 * u * u * t * c[1][i] +
                 3 * u * t * t * c[2][i] + t ** 3 * c[3][i]
                 for i in (0, 1))                   # type: ignore[return-value]


def _newton(f: Any, t0: float, s0: float, tol: float = 1e-3,
            lim: int = 10) -> tuple[float, float] | None:
    """`solve` (:888) — the client's 2x2 Newton iteration, verbatim.

    Transcribed rather than replaced by an exact Bezier-line root solve
    ON PURPOSE. This one terminates on `error < 1e-3` after at most ten
    steps and gives up on a singular Jacobian, so it does NOT find every
    root a textbook method would; where it stops IS what the client
    draws, and a better solver here would model a client that does not
    exist.

    Args:
        f: The residual, `(t, s) -> (fx, fy)`.
        t0: Initial guess for the curve parameter.
        s0: Initial guess for the line parameter.
        tol: The convergence tolerance (`solve`'s own default).
        lim: The iteration limit (`solve`'s own default).

    Returns:
        `(t, s)` at convergence, or None when it did not converge.
    """
    d, err, it = 1e-6, float("inf"), 0
    while err >= tol:
        if it >= lim:
            return None
        y0 = f(t0, s0)
        j = [[(f(t0 + d, s0)[i] - f(t0 - d, s0)[i]) / (2 * d) for i in (0, 1)],
             [(f(t0, s0 + d)[i] - f(t0, s0 - d)[i]) / (2 * d) for i in (0, 1)]]
        # the client builds its Jacobian ROW-wise per component, so row i
        # is [d f_i / d t, d f_i / d s] — the transpose of what `j` holds
        j00, j01, j10, j11 = j[0][0], j[1][0], j[0][1], j[1][1]
        det = j00 * j11 - j01 * j10
        if det == 0:
            return None
        b0, b1 = -y0[0], -y0[1]
        t0 += (j11 / det) * b0 + (-j01 / det) * b1
        s0 += (-j10 / det) * b0 + (j00 / det) * b1
        te, se = f(t0, s0)
        err = max(abs(te), abs(se))
        it += 1
    return (t0, s0)


def _curve_hits(c: Any, seg: Any) -> list[tuple[float, float]]:
    """`curveIntersectLineSegment` (:925), transcribed.

    Bounding-box reject first, then up to three Newton starts, and it
    returns AT MOST ONE point — the first start that converges inside
    both parameter ranges wins and the rest are not tried. A ray that
    genuinely crosses one rounded corner twice therefore yields one hit
    from the client too, which matters because the caller counts hits.

    Args:
        c: The four control points.
        seg: `((x, y), (x, y))` — the interceptor.

    Returns:
        `[point]` or `[]`.
    """
    xs = [p[0] for p in c]
    ys = [p[1] for p in c]
    box = ((min(xs), min(ys)), (max(xs), max(ys)))
    walls = (((box[0][0], box[0][1]), (box[1][0], box[0][1])),
             ((box[1][0], box[0][1]), (box[1][0], box[1][1])),
             ((box[1][0], box[1][1]), (box[0][0], box[1][1])),
             ((box[0][0], box[1][1]), (box[0][0], box[0][1])))
    if not any(_seg_hit(seg, w) is not None for w in walls):
        return []

    def resid(t: float, s: float) -> tuple[float, float]:
        """The Bezier point minus the line point.

        Args:
            t: The curve parameter.
            s: The line parameter.

        Returns:
            The two residual components.
        """
        b = _bezier(c, t)
        return (b[0] - (seg[0][0] + s * (seg[1][0] - seg[0][0])),
                b[1] - (seg[0][1] + s * (seg[1][1] - seg[0][1])))

    for t0, s0 in ((0.5, 0.0), (0.2, 0.0), (0.8, 0.0)):
        sol = _newton(resid, t0, s0)
        if sol is None:
            continue
        t, s = sol
        if t < 0 or t > 1 or s < 0 or s > 1:
            continue
        return [_bezier(c, t)]
    return []


def _deconstruct_rectanguloid(hub: dict[str, Any],
                              offset: float) -> tuple[Any, Any]:
    """`deconstructRectanguloidElement` (:7404).

    Args:
        hub: The rectangle-like element.
        offset: The binding gap.

    Returns:
        `(sides, corners)` — four segments and, when the element is
        rounded, four cubic Beziers.
    """
    x, y = hub["x"], hub["y"]
    w, h = hub.get("width", 0.0), hub.get("height", 0.0)
    rad = _corner_radius(min(w, h), hub)
    if rad <= 0:
        r = ((x - offset, y - offset), (x + w + offset, y + h + offset))
        return ((((r[0][0], r[0][1]), (r[1][0], r[0][1])),
                 ((r[1][0], r[0][1]), (r[1][0], r[1][1])),
                 ((r[0][0], r[1][1]), (r[1][0], r[1][1])),
                 ((r[0][0], r[1][1]), (r[0][0], r[0][1]))), ())
    cen = (x + w / 2.0, y + h / 2.0)
    r = ((x, y), (x + w, y + h))
    top = ((r[0][0] + rad, r[0][1]), (r[1][0] - rad, r[0][1]))
    right = ((r[1][0], r[0][1] + rad), (r[1][0], r[1][1] - rad))
    bottom = ((r[0][0] + rad, r[1][1]), (r[1][0] - rad, r[1][1]))
    left = ((r[0][0], r[1][1] - rad), (r[0][0], r[0][1] + rad))
    # each corner is pushed out along its OWN diagonal, which is why the
    # straight sides between them end up displaced by `offset * cos` and
    # not by `offset` — the gap outline of a rounded box is narrower
    # than the box grown by the gap
    o = [_outward((r[0][0] - offset, r[0][1] - offset), cen, offset),
         _outward((r[1][0] + offset, r[0][1] - offset), cen, offset),
         _outward((r[1][0] + offset, r[1][1] + offset), cen, offset),
         _outward((r[0][0] - offset, r[1][1] + offset), cen, offset)]

    def ctrl(a: Any, bx: float, by: float) -> tuple[float, float]:
        """The 2/3 control point the client builds toward a box corner.

        Args:
            a: The side end the control point runs from.
            bx: The box corner's x it runs toward.
            by: The box corner's y.

        Returns:
            The control point, before the diagonal offset.
        """
        return (a[0] + 2.0 / 3.0 * (bx - a[0]),
                a[1] + 2.0 / 3.0 * (by - a[1]))

    corners = (
        (_push(o[0], left[1]), _push(o[0], ctrl(left[1], r[0][0], r[0][1])),
         _push(o[0], ctrl(top[0], r[0][0], r[0][1])), _push(o[0], top[0])),
        (_push(o[1], top[1]), _push(o[1], ctrl(top[1], r[1][0], r[0][1])),
         _push(o[1], ctrl(right[0], r[1][0], r[0][1])), _push(o[1], right[0])),
        (_push(o[2], right[1]), _push(o[2], ctrl(right[1], r[1][0], r[1][1])),
         _push(o[2], ctrl(bottom[1], r[1][0], r[1][1])),
         _push(o[2], bottom[1])),
        (_push(o[3], bottom[0]), _push(o[3], ctrl(bottom[0], r[0][0], r[1][1])),
         _push(o[3], ctrl(left[0], r[0][0], r[1][1])), _push(o[3], left[0])),
    )
    sides = tuple((corners[i][3], corners[(i + 1) % 4][0]) for i in range(4))
    return sides, corners


def _deconstruct_diamond(hub: dict[str, Any],
                         offset: float) -> tuple[Any, Any]:
    """`deconstructDiamondElement` (:7576).

    THE VERTICES ARE NOT THE SIDE MIDPOINTS and this is the surprise the
    shape holds. `getDiamondPoints` (:10173) returns
    `Math.floor(width / 2) + 1` for the top vertex's x and
    `Math.floor(height / 2) + 1` for the right vertex's y — so the drawn
    rhombus is up to a pixel wider and lower than the symmetric one, and
    on an even-sized node it is exactly 1px off centre on both axes.
    `determineFocusPoint` above uses the SYMMETRIC midpoints for the same
    element (`element.width / 2`, :11757), so the client disagrees with
    itself about where a diamond's corners are. Both readings are
    transcribed where they belong; neither is corrected.

    Args:
        hub: The diamond element.
        offset: The binding gap.

    Returns:
        `(sides, curves)` — four facets and, when rounded, four Beziers.
    """
    x, y = hub["x"], hub["y"]
    w, h = hub.get("width", 0.0), hub.get("height", 0.0)
    top_x = float(int(w // 2)) + 1.0
    right_y = float(int(h // 2)) + 1.0
    vr = _corner_radius(abs(top_x - 0.0), hub)
    hr = _corner_radius(abs(right_y - 0.0), hub)
    r = hub.get("roundness") or None
    if r is None or r.get("type") is None:
        top = (x + top_x, y - offset)
        right = (x + w + offset, y + right_y)
        bottom = (x + top_x, y + h + offset)
        left = (x - offset, y + right_y)
        return ((((top[0] + vr, top[1] + hr), (right[0] - vr, right[1] - hr)),
                 ((right[0] - vr, right[1] + hr),
                  (bottom[0] + vr, bottom[1] - hr)),
                 ((bottom[0] - vr, bottom[1] - hr),
                  (left[0] + vr, left[1] + hr)),
                 ((left[0] + vr, left[1] - hr), (top[0] - vr, top[1] + hr))),
                ())
    cen = (x + w / 2.0, y + h / 2.0)
    top = (x + top_x, y + 0.0)
    right = (x + w, y + right_y)
    bottom = (x + top_x, y + h)
    left = (x + 0.0, y + right_y)
    o = [_outward(right, cen, offset), _outward(bottom, cen, offset),
         _outward(left, cen, offset), _outward(top, cen, offset)]
    corners = (
        (_push(o[0], (right[0] - vr, right[1] - hr)), _push(o[0], right),
         _push(o[0], right), _push(o[0], (right[0] - vr, right[1] + hr))),
        (_push(o[1], (bottom[0] + vr, bottom[1] - hr)), _push(o[1], bottom),
         _push(o[1], bottom), _push(o[1], (bottom[0] - vr, bottom[1] - hr))),
        (_push(o[2], (left[0] + vr, left[1] + hr)), _push(o[2], left),
         _push(o[2], left), _push(o[2], (left[0] + vr, left[1] - hr))),
        (_push(o[3], (top[0] - vr, top[1] + hr)), _push(o[3], top),
         _push(o[3], top), _push(o[3], (top[0] + vr, top[1] + hr))),
    )
    sides = tuple((corners[i][3], corners[(i + 1) % 4][0]) for i in range(4))
    return sides, corners


def _ellipse_hits(hub: dict[str, Any], offset: float,
                  seg: Any) -> list[tuple[float, float]]:
    """`intersectEllipseWithLineSegment` (:7845) + `ellipseLine…` (:7382).

    THE SEGMENT IS TREATED AS AN INFINITE LINE and that is the client's
    code, not a simplification here: :7853 wraps the interceptor in
    `line(...)`, not `lineSegment(...)`, and the quadratic's roots are
    never range-checked against it. So an ellipse can hand back a hit
    BEHIND the adjacent point. The caller sorts by distance and takes
    the nearest, which is what keeps that mostly harmless — mostly.

    Args:
        hub: The ellipse element.
        offset: The binding gap, added to BOTH semi-axes.
        seg: `((x, y), (x, y))` — the interceptor.

    Returns:
        The zero, one or two intersection points.
    """
    w, h = hub.get("width", 0.0), hub.get("height", 0.0)
    cx, cy = hub["x"] + w / 2.0, hub["y"] + h / 2.0
    a, b = w / 2.0 + offset, h / 2.0 + offset
    x1, y1 = seg[0][0] - cx, seg[0][1] - cy
    x2, y2 = seg[1][0] - cx, seg[1][1] - cy
    qa = (x2 - x1) ** 2 / a ** 2 + (y2 - y1) ** 2 / b ** 2
    qb = 2 * (x1 * (x2 - x1) / a ** 2 + y1 * (y2 - y1) / b ** 2)
    qc = x1 ** 2 / a ** 2 + y1 ** 2 / b ** 2 - 1
    disc = qb * qb - 4 * qa * qc
    if disc < 0 or qa == 0:
        return []                                   # the client's NaN filter
    root = disc ** 0.5
    out = [(x1 + t * (x2 - x1) + cx, y1 + t * (y2 - y1) + cy)
           for t in ((-qb + root) / (2 * qa), (-qb - root) / (2 * qa))]
    if len(out) == 2 and abs(out[0][0] - out[1][0]) < PRECISION and \
            abs(out[0][1] - out[1][1]) < PRECISION:
        return [out[0]]
    return out


def client_outline_hits(hub: dict[str, Any], gap: float,
                        seg: Any) -> list[tuple[float, float]]:
    """`intersectElementWithLineSegment` (:7770) — every shape.

    Args:
        hub: The bound node, unrotated.
        gap: The binding gap the outline is expanded by.
        seg: `((x, y), (x, y))` — the interceptor.

    Returns:
        The intersection points, deduplicated the way the client
        deduplicates them (`pointsEqual` at `PRECISION`, first wins) and
        in the client's own emission order — sides before corners.
    """
    kind = hub.get("type")
    if kind == "ellipse":
        raw = _ellipse_hits(hub, gap, seg)
        sides: Any = ()
        curves: Any = ()
    else:
        sides, curves = (_deconstruct_diamond(hub, gap) if kind == "diamond"
                         else _deconstruct_rectanguloid(hub, gap))
        raw = [p for p in (_seg_hit(seg, s) for s in sides) if p is not None]
        for c in curves:
            raw.extend(_curve_hits(c, seg))
    out: list[tuple[float, float]] = []
    for p in raw:
        if any(abs(p[0] - q[0]) < PRECISION and abs(p[1] - q[1]) < PRECISION
               for q in out):
            continue
        out.append(p)
    return out


def client_draws(hub: dict[str, Any], focus: float, gap: float, adj: Any,
                 stored: Any) -> tuple[float, float]:
    """`updateBoundPoint` (:11380) for any of the three node shapes.

    The client shoots a ray from the adjacent point through the focus
    point, long enough to cross the whole shape, and takes the NEAREST
    of its intersections with the gap-expanded outline. One intersection
    means the ray only grazed, and the client then draws the focus point
    itself; none means it keeps what was stored.

    Rectangle, rounded rectangle, diamond and ellipse all arrive here —
    the shape lives entirely in `client_outline_hits`. Rotation does
    not: `angle = 0` throughout, as every server-authored node is, so the
    `pointRotateRads` pairs the client wraps each intersection in are
    identities and are dropped.

    Args:
        hub: The bound node, unrotated.
        focus: The stored focus value.
        gap: The stored binding gap.
        adj: The adjacent path point, in scene coordinates.
        stored: The endpoint as stored, in scene coordinates.

    Returns:
        Where the client draws the endpoint after re-deriving it.
    """
    fp = client_focus_point(hub, focus, adj)
    if gap == 0:
        return fp
    w, h = hub["width"], hub["height"]
    cx, cy = hub["x"] + w / 2.0, hub["y"] + h / 2.0
    reach = (_dist(adj, stored) + _dist(adj, (cx, cy)) + max(w, h) * 2.0)
    vx, vy = fp[0] - adj[0], fp[1] - adj[1]
    m = (vx * vx + vy * vy) ** 0.5
    vx, vy = (0.0, 0.0) if m == 0 else (vx / m, vy / m)
    seg = ((adj[0], adj[1]), (adj[0] + vx * reach, adj[1] + vy * reach))
    hits = client_outline_hits(hub, gap, seg)
    hits.sort(key=lambda p: _dist(adj, p))
    if len(hits) > 1:
        return hits[0]
    if len(hits) == 1:
        return fp
    return (stored[0], stored[1])


def _dist(a: Any, b: Any) -> float:
    """Euclidean distance between two points.

    Args:
        a: First point.
        b: Second point.

    Returns:
        The distance.
    """
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def tangential_slip(rec: dict[str, Any]) -> float:
    """How far ALONG its side the client redraws one specimen's foot.

    Displacement across the side is the `gap` and is by design; the slip
    that moves a fan lane or a port assignment is the component along
    it. Resolved onto the record's own tangent, which for a foot on a
    horizontal side is x — so rows 1-3 read exactly what they always read.

    Args:
        rec: One `probe_manifest` record.

    Returns:
        The absolute tangential slip in px.
    """
    drawn = client_draws(hub_of(rec), rec["focus"], rec["gap"],
                         rec["adjacent"], rec["foot"])
    tx, ty = rec["tangent"]
    return abs((drawn[0] - rec["foot"][0]) * tx +
               (drawn[1] - rec["foot"][1]) * ty)


def normal_slip(rec: dict[str, Any]) -> float:
    """How far ACROSS its side the client redraws one specimen's foot.

    Signed, outward positive. This is the `gap` by design on a
    square-cornered rectangle and is NOT on anything else — a rounded
    box's straight sides move out by `gap * cos`, and a conic's expanded
    outline is a scaled shape rather than an offset curve. Reported
    rather than asserted at 0, for that reason.

    Args:
        rec: One `probe_manifest` record.

    Returns:
        The signed normal displacement in px.
    """
    drawn = client_draws(hub_of(rec), rec["focus"], rec["gap"],
                         rec["adjacent"], rec["foot"])
    nx, ny = rec["normal"]
    return ((drawn[0] - rec["foot"][0]) * nx +
            (drawn[1] - rec["foot"][1]) * ny)


def build_project(root: str) -> list[dict[str, Any]]:
    """Write the specimen out as a project the real server can serve.

    Args:
        root: Directory to make the project in. Created if absent.

    Returns:
        The manifest, also written to `<root>/manifest.json` for the
        browser referee to read back.
    """
    project = canvas.Project(root)
    project.ensure_tree()
    els = probe_elements()
    manifest = probe_manifest(els)
    canvas.write_json(project.artifacts_dir / "focus-probe.excalidraw", {
        "type": "excalidraw", "version": 2, "source": "wysiwyg-grilling",
        "appState": {"gridSize": 20, "viewBackgroundColor": "#faf8f2"},
        "files": {}, "elements": els,
        "wysiwyg": {"artifact": "focus-probe", "artifact_type": "flow",
                    "name": "Focus probe", "migrations": ["0001-baseline"]},
    })
    canvas.write_json(Path(root) / "manifest.json", manifest)
    return manifest


if __name__ == "__main__":
    print(json.dumps(build_project(sys.argv[1])))
