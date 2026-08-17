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
# 240px of straight run below the foot, so the approach is unambiguously
# perpendicular and the adjacent point is nowhere near a corner.
APPROACH = 240.0
GAP = 6
# The quarter points: |focus| lands on exactly 0.5, a 3dp-exact value, so
# the collinearity is exact in float and the cliff is not rounded away by
# luck the way a 1/3 offset rounds it away (spike-focus-verify §4c).
SPECIMENS = (
    ("left", -50.0),
    ("mid", 0.0),
    ("right", 50.0),
)


def probe_elements() -> list[dict[str, Any]]:
    """The specimen scene: three hubs, one bound foot each.

    Three separate hubs rather than three feet on one side, because
    `fan_attach_points` respaces a side carrying two or more feet and
    would slide the specimen off the quarter points it exists to test.
    A side carrying one foot is left exactly where it was put — and
    still has its focus re-stamped by the same call site, which is the
    code under test.

    Every hub is `roundness: null`, deliberately: the client offsets a
    ROUNDED rectangle's gap outline diagonally per corner
    (`deconstructRectanguloidElement`, chunk-4FTI6OG3.js :7404) and a
    square-cornered one straight out, so only the square-cornered case
    is exactly modelled by a transcription. The browser referee would
    not care; the fast neighbour would be ~3px vague, and a pin at 0.5px
    cannot afford that.

    Returns:
        The scene element list, focus values already stamped by
        `fan_attach_points` — i.e. by the shipped call site, whatever
        that call site currently computes.
    """
    els: list[dict[str, Any]] = []
    for i, (name, off) in enumerate(SPECIMENS):
        hx, hy = 400.0 + i * 500.0, 400.0
        cx = hx + HUB_W / 2.0
        foot_x, foot_y = cx + off, hy + HUB_H
        els.append({
            "id": "hub-%s" % name, "type": "rectangle",
            "x": hx, "y": hy, "width": HUB_W, "height": HUB_H,
            "angle": 0, "strokeColor": "#1e1e1e",
            "backgroundColor": "transparent", "fillStyle": "solid",
            "strokeWidth": 1, "strokeStyle": "solid", "roughness": 1,
            "opacity": 100, "groupIds": [], "frameId": None,
            "roundness": None, "seed": 1 + i, "version": 1,
            "versionNonce": 0, "isDeleted": False, "updated": 1,
            "link": None, "locked": False,
            "customData": {"role": "node", "author": "agent"},
            "boundElements": [{"id": "probe-%s" % name, "type": "arrow"}],
        })
        arrow: dict[str, Any] = {
            "id": "probe-%s" % name, "type": "arrow",
            "x": foot_x, "y": foot_y + APPROACH,
            "width": 0, "height": APPROACH,
            "points": [[0, 0], [0, -APPROACH]],
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


def probe_manifest(els: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What each specimen claims, in the form both oracles check.

    Args:
        els: The scene from `probe_elements`.

    Returns:
        One record per arrow: the arrow and hub ids, the hub's box, the
        stored foot, the adjacent point the client will aim from, the
        gap, and the focus the server wrote.
    """
    ix = {e["id"]: e for e in els}
    out = []
    for name, _off in SPECIMENS:
        a = ix["probe-%s" % name]
        hub = ix["hub-%s" % name]
        pts = a["points"]
        out.append({
            "name": name, "arrow": a["id"], "hub": hub["id"],
            "hub_box": [hub["x"], hub["y"], hub["width"], hub["height"]],
            "foot": [a["x"] + pts[-1][0], a["y"] + pts[-1][1]],
            "adjacent": [a["x"] + pts[-2][0], a["y"] + pts[-2][1]],
            "gap": a["endBinding"]["gap"],
            "focus": a["endBinding"]["focus"],
        })
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


def client_draws(hub: dict[str, Any], focus: float, gap: float, adj: Any,
                 stored: Any) -> tuple[float, float]:
    """`updateBoundPoint` (:11380) for a square-cornered rectangle.

    The client shoots a ray from the adjacent point through the focus
    point, long enough to cross the whole shape, and takes the NEAREST
    of its intersections with the gap-expanded outline. One intersection
    means the ray only grazed, and the client then draws the focus point
    itself; none means it keeps what was stored.

    Args:
        hub: The bound node (`roundness: null`, unrotated).
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
    x1, y1 = hub["x"] - gap, hub["y"] - gap
    x2, y2 = hub["x"] + w + gap, hub["y"] + h + gap
    sides = (((x1, y1), (x2, y1)), ((x2, y1), (x2, y2)),
             ((x1, y2), (x2, y2)), ((x1, y2), (x1, y1)))
    hits: list[tuple[float, float]] = []
    for side in sides:
        p = _seg_hit(seg, side)
        if p is None:
            continue
        if any(abs(p[0] - q[0]) < PRECISION and abs(p[1] - q[1]) < PRECISION
               for q in hits):
            continue                            # the client's own dedup
        hits.append(p)
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
    it. Every specimen here stands on a horizontal side, so that is x.

    Args:
        rec: One `probe_manifest` record.

    Returns:
        The absolute tangential slip in px.
    """
    hx, hy, w, h = rec["hub_box"]
    hub = {"type": "rectangle", "x": hx, "y": hy, "width": w, "height": h}
    drawn = client_draws(hub, rec["focus"], rec["gap"], rec["adjacent"],
                         rec["foot"])
    return abs(drawn[0] - rec["foot"][0])


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
