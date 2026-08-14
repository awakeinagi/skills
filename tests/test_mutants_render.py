"""Render tier: mutants measured in pixels, by ablation, under a browser.

Every other tier reads the model. This one reads the picture: rasterize the
scene's tier-1 SVG, rasterize it again with one element omitted, and diff.
The delta IS that element's contribution to the drawing, so two questions
the model cannot answer become arithmetic — an element whose ablation
changes no pixels is not in the picture (`ablation_existence`), and a
connector whose delta splits into two separated components is severed
somewhere along its run (`ablation_continuity`), which is the r5-14 class:
an opaque label backdrop erasing the stroke it sits on.

Not every pixel question is an ablation: `TestSnapshotFraming` at the tail
asks a blunter one — is the whole drawing even in the frame? — and asks it
of `canvas.py snapshot` itself rather than of this file's own rasterizer.

Ablation is by OMISSION, not by styling: the hidden element is absent from
the SVG entirely, so nothing about how the renderer treats `opacity` or
`display` can make a hidden element leave ghost ink behind.

The whole module is gated behind `MUTANTS_RENDER=1` because it starts a
headless browser. Gated OFF it skips; gated ON with no browser to be found
it RAISES (spec §7) — an environmental failure must abort with a named
reason, never quietly mark the mutants green.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] /
                       "skills" / "wysiwyg-grilling" / "scripts"))
import canvas
import test_mutants as tm
from pngdiff import components, read_png_gray, tolerant_diff
from tests_helpers import el

RENDER = os.environ.get("MUTANTS_RENDER") == "1"

# Pinned for reproducibility, not for looks: GPU compositing, device scale,
# colour profile and subpixel text all move pixels between machines, and a
# diff that moves between machines cannot be evidence of anything.
CHROME_FLAGS = ("--headless=new", "--disable-gpu", "--no-sandbox",
                "--hide-scrollbars", "--force-device-scale-factor=1",
                "--force-color-profile=srgb", "--font-render-hinting=none",
                "--disable-lcd-text")

# Mirrors canvas.find_browsers' search so a miss can say what it looked for.
SEARCHED = ("PATH: chromium, chromium-browser, google-chrome-stable, "
            "google-chrome, chrome, brave-browser, msedge, microsoft-edge; "
            "~/.cache/ms-playwright")

# Same floor pngdiff.tolerant_diff drops speckle at: a component smaller
# than this is anti-aliasing residue, not a piece of the drawing.
MIN_BLOB = 12

_SVG_TAG = re.compile(r"^<svg [^>]*>")


def _browser() -> str:
    """The chromium-family binary to rasterize with — best first.

    Returns:
        The first path `canvas.find_browsers()` offers.

    Raises:
        RuntimeError: If no browser was found. The render tier never
            degrades to a skip here: `MUTANTS_RENDER=1` is a request to
            measure pixels, and not measuring them is a failure, not a
            pass (spec §7).
    """
    found = canvas.find_browsers()
    if not found:
        raise RuntimeError("render tier requested but no chromium: %r"
                           % (SEARCHED,))
    return found[0]


def _mkworkdir() -> str:
    """Make the scratch directory renders are written into.

    Snap-confined chromium builds run in a mount namespace where the real
    `/tmp` is invisible, so when the chosen browser is one of those the
    scratch dir has to live under `$HOME` instead (the constraint is
    documented on `canvas.find_browsers`).

    Returns:
        Path to a fresh temp directory the caller owns and must remove.
    """
    root = str(Path.home()) if "/snap/" in _browser() else None
    return tempfile.mkdtemp(prefix="mutants-render-", dir=root)


def _rasterize(svg: str, w: int, h: int, workdir: str) -> bytes:
    """Screenshot one SVG document at a given window size.

    Split out of `_shot` so the parity section below can rasterize markup
    it framed itself. Renders are cached inside `workdir`, so a document
    shot twice in one test class costs one browser start.

    The cache key is the markup AND THE BROWSER BINARY, and the second
    half is not decoration. Keyed on markup alone this cache is only
    sound while one binary is in play: the min_font calibration sweeps
    the same scenes across three chromium builds that disagree about
    anti-aliasing, and a markup-keyed cache happily served build 151's
    pixels for build 131 — returning 4.62:1 for a build that renders
    5.51:1, which is the difference between failing WCAG's floor and
    passing it. Folding the binary in makes THAT hazard unreachable
    rather than merely documented: a shared workdir can no longer return
    one build's pixels for another. It buys nothing beyond the cache —
    in particular a cross-build sweep that includes a snap-confined
    chromium still needs its workdir rooted under `$HOME`, because that
    build cannot see the real `/tmp` at all (the constraint `_mkworkdir`
    exists for). That one fails loudly with a RuntimeError rather than
    quietly with a wrong number, which is why it is a note and not a
    second key.

    Args:
        svg: The complete SVG document to draw.
        w: Browser window width in pixels.
        h: Browser window height in pixels.
        workdir: Directory to write the HTML and PNG into.

    Returns:
        The screenshot's PNG bytes.

    Raises:
        RuntimeError: If the browser exited without writing a PNG.
    """
    name = hashlib.sha1(("%s\0%s" % (_browser(), svg))
                        .encode("utf-8")).hexdigest()[:16]
    png = Path(workdir) / (name + ".png")
    if png.exists():
        return png.read_bytes()
    html = Path(workdir) / (name + ".html")
    html.write_text("<!doctype html><html><body style='margin:0'>%s"
                    "</body></html>" % svg, encoding="utf-8")
    proc = subprocess.run(
        [_browser(), *CHROME_FLAGS, "--screenshot=%s" % png,
         "--window-size=%dx%d" % (w, h), html.as_uri()],
        capture_output=True, timeout=180)
    if not png.exists():
        raise RuntimeError("render tier: chromium wrote no PNG for %s "
                           "(rc=%s): %s" % (html, proc.returncode,
                                            proc.stderr[-400:]))
    return png.read_bytes()


def _framed_svg(elements: list[dict],
                hide: Iterable[str] = ()) -> tuple[str, int, int]:
    """The tier-1 markup for a scene, pinned to the FULL scene's viewport.

    The ablated markup is framed in the FULL scene's viewport, not its
    own: `canvas.render_svg` derives width/height/viewBox from the
    elements it is given, so omitting one would otherwise resize and shift
    the whole picture and make the two rasters incomparable
    (`tolerant_diff` rejects a size mismatch outright). Swapping in the
    full scene's `<svg>` tag pins both shots to the same pixel grid. The
    ground rect underneath is left at the ablated scene's own bounds on
    purpose — it is painted in `SVG_GROUND`, which is far above the ink
    threshold, so where it does and doesn't reach is invisible to the
    diff.

    Returned as markup rather than pixels because the parity section below
    re-frames this document a second time before drawing it.

    Args:
        elements: The full scene.
        hide: Ids to ablate — dropped from the element list entirely.

    Returns:
        `(svg, width, height)` — the document to draw and the window the
        full scene asks for.
    """
    full, w, h = canvas.render_svg(elements)
    if not hide:
        return full, w, h
    hidden = set(hide)
    kept = [e for e in elements if e.get("id") not in hidden]
    svg, _kw, _kh = canvas.render_svg(kept)
    return (_SVG_TAG.match(full).group(0) + svg[_SVG_TAG.match(svg).end():],
            w, h)


def _shot(elements: list[dict], workdir: str,
          hide: Iterable[str] = ()) -> bytes:
    """Rasterize a scene's tier-1 SVG, omitting the `hide` elements.

    Args:
        elements: The full scene.
        workdir: Directory to write the HTML and PNG into.
        hide: Ids to ablate — dropped from the element list entirely.

    Returns:
        The screenshot's PNG bytes. Propagates `_rasterize`'s
        `RuntimeError` when the browser wrote no PNG.
    """
    return _rasterize(*_framed_svg(elements, hide), workdir)


def _delta_components(w: int, h: int,
                      blobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group a diff's blobs into the strokes a reader would see.

    `tolerant_diff` already returns connected components, but a single
    stroke routinely survives the dilate-XOR as several of them (the
    tolerance eats the middle of a run and leaves its ends). Filling each
    blob's bounding box and re-componenting merges the fragments of one
    stroke back together while leaving genuinely separated pieces apart,
    so the count means "how many strokes is this", not "how many scraps".

    Args:
        w: Raster width in pixels.
        h: Raster height in pixels.
        blobs: `tolerant_diff` output — dicts with an inclusive `bbox`.

    Returns:
        The merged components of at least `MIN_BLOB` pixels.
    """
    mask = bytearray(w * h)
    for blob in blobs:
        x0, y0, x1, y1 = blob["bbox"]
        for y in range(y0, y1 + 1):
            row = y * w
            for x in range(x0, x1 + 1):
                mask[row + x] = 1
    return [c for c in components(w, h, mask) if c["area"] >= MIN_BLOB]


def _completed_by_eye(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Do two pieces of one connector's ink read as a stroke with a gap?

    `ablation_continuity` fires when a connector's delta comes apart, and
    the plain component count cannot tell the two cases apart. A label
    backdrop parked mid-way along a STRAIGHT run breaks the ink too, and
    that break is the tool's own idiom — the client breaks the arrow
    behind every bound label and `render_svg` paints the ground back in
    to match (`arrow_label_anchor`). What makes it readable is that the
    two stubs are collinear across the gap: the eye continues one into
    the other. Parked on the ELBOW instead, the same backdrop leaves a
    horizontal stub and a vertical one pointing nowhere near each other,
    and that is r5-14 — a connector that reads as two disconnected
    pieces.

    So the test is collinearity, not proximity: the pieces must be
    SEPARATED along one axis and OVERLAP on the other. Overlap of one
    row is enough and is deliberate — the claim is that the two runs
    share a band, not that the band is any particular thickness, and a
    threshold in stroke-widths would move with the build's
    anti-aliasing. Measured on the scenes below, the mid-run pair
    overlaps by 2 rows and the elbow pair misses by 11.

    KNOWN TOO BROAD (curator batch 14, from the Task 19 review, F5,
    2026-08-14). Read literally this is a BBOX predicate, not a
    collinearity one: separated on one axis, overlapping by >=1 unit on
    the other, with NO bound on how far apart the pieces are and no
    look at where either piece's ENDS point. Three consequences, each
    reproduced:

    - Synthetic. An L-shaped remnant `(122,59,283,120)` merges with a
      horizontal stub `(350,100,400,101)` 67px to its right, and with a
      vertical stub `(200,200,201,300)` 80px below it; a T-corner pair
      merges on one row of shared band.
    - Reachable in PIXELS, not only on paper. Any remnant containing a
      TURN has a bbox tall AND wide, so it overlaps a distant stub on
      one axis by construction — which is what
      `test_mutant_l_shaped_remnant_hides_a_severed_back_edge` pins on
      `_back_edge_with_label("turn")`: the same erased-elbow picture the
      2-segment `corner` scene fires on, silently merged here. The
      reviewer's own sweep did NOT reach it (a corner-label sweep from
      y=90 to y=70 reconnects the scene genuinely at y=78, and a Z-path
      broken on either turn leaves the pieces separated on BOTH axes, so
      the check fires correctly); the back edge reaches it because its
      third segment turns back UNDER the remnant instead of away from it.
    - The discrimination that does work rests on a thin measurement:
      one scene, an 11-row miss against a 1-row trip point. A build
      whose anti-aliasing fattens a stub by 11 rows silences the elbow
      mutant, and nothing here would say so.

    Why this is NOT a predicate tweak, measured rather than guessed: by
    the time a component reaches here, the ink's SHAPE is already gone.
    `tolerant_diff` computes a per-pixel residual and returns only each
    blob's area and bbox; `_delta_components` then fills those bboxes
    solid, so every "component" is a rectangle and the L above reads as
    122..280 on every one of its rows. No function of these bboxes can
    tell the back edge's severed turn from its legitimate mid-leg break
    — both are two L's, y-separated, x-overlapping. VERIFIED in a
    throwaway tree: carrying the true residual through and comparing
    the ink within 4px of the two FACING edges flips the mutant green
    and leaves the whole render tier green (820 tests, sole change).
    That is the shape of the fix and it belongs to WP4; the cost is
    plumbing the residual mask out of `tolerant_diff`.

    Residual, recorded with the class rather than as an open defect: a
    foreign opaque shape covering a straight run is not lost, because
    `passes_through_foreign` owns exactly that class on the lint tier —
    but it IS lost when the foreign shape's STORED geometry shows no
    overlap, which is F1's class, and this tier was meant to be the
    backstop for precisely that. F1 itself closed at 636da5d.

    Args:
        a: One component, with an inclusive `bbox` of `(x0, y0, x1, y1)`.
        b: The other component, same shape.

    Returns:
        True if a reader completes the two into one stroke.
    """
    ax0, ay0, ax1, ay1 = a["bbox"]
    bx0, by0, bx1, by1 = b["bbox"]
    for (u0, u1, v0, v1), (w0, w1, z0, z1) in (
            ((ax0, ax1, ay0, ay1), (bx0, bx1, by0, by1)),
            ((ay0, ay1, ax0, ax1), (by0, by1, bx0, bx1))):
        if max(u0, w0) > min(u1, w1) and max(v0, z0) <= min(v1, z1):
            return True
    return False


def _reader_strokes(parts: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group a delta's components into the strokes a reader counts.

    `_delta_components` already merges the scraps one run comes apart
    into; this merges the runs a backdrop broke but did not sever, so
    the group count is "how many disconnected pieces does this connector
    read as".

    Args:
        parts: The merged components, in any order.

    Returns:
        The groups, each a non-empty list of the components in it.
    """
    groups = [[p] for p in parts]
    merged = True
    while merged:
        merged = False
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                if any(_completed_by_eye(x, y)
                       for x in groups[i] for y in groups[j]):
                    groups[i] = groups[i] + groups.pop(j)
                    merged = True
                    break
            if merged:
                break
    return groups


def ablation_findings(elements: list[dict], arrow_ids: Iterable[str],
                      workdir: str) -> list[dict]:
    """Findings from ablating each named element out of the picture.

    One shot of the full scene, then one shot per id with that id omitted;
    the diff between them is that element's ink. No ink at all means the
    element is not in the picture; ink in two or more separated pieces
    means whatever is drawn over it has cut it in half.

    The delta is *nearly* the ablated element's own ink, and the gap is
    worth knowing: `canvas.render_svg` decides whether to paint a label's
    opaque backdrop by testing the label's `containerId` against the arrow
    ids of the elements it was handed, so ablating an arrow also removes
    the backdrop of any label bound to it. When that backdrop covers only
    the ablated arrow — every scene here — the delta is exactly the ink a
    reader would lose, which is what we want. But a backdrop overlapping
    FOREIGN ink un-covers it on ablation, and the un-covered ink joins the
    delta as pieces that were never part of the connector, which can push
    the component count to 2 and fire `ablation_continuity` spuriously.
    No scene in this file reaches that; a catalogue scene with a label
    riding one arrow across another's stroke would.

    Args:
        elements: The full scene.
        arrow_ids: Ids to ablate, one at a time. Named for the connectors
            `ablation_continuity` is about, but any element id works —
            `ablation_existence` applies to all of them.
        workdir: Directory renders are written into.

    Returns:
        Findings shaped like `test_mutants.collect_findings` output:
        `{"check", "element", "magnitude", "direction", "raw"}`.
        `ablation_existence` carries magnitude 0.0 (the pixels it changed);
        `ablation_continuity` carries the count of pieces the connector
        READS as — components a reader completes across a gap are one
        piece, per `_completed_by_eye`.
    """
    full = _shot(elements, workdir)
    w, h, _pix = read_png_gray(full)
    findings: list[dict] = []
    for eid in arrow_ids:
        blobs = tolerant_diff(_shot(elements, workdir, hide=(eid,)), full)
        if not blobs:
            findings.append({
                "check": "ablation_existence", "element": eid,
                "magnitude": 0.0, "direction": None,
                "raw": "removing %s changed no pixels — it is in the "
                       "model but not in the picture" % eid})
            continue
        strokes = _reader_strokes(_delta_components(w, h, blobs))
        if len(strokes) >= 2:
            findings.append({
                "check": "ablation_continuity", "element": eid,
                "magnitude": float(len(strokes)), "direction": None,
                "raw": "%s's ink comes apart in %d separated pieces %s — "
                       "something drawn over it severs the run"
                       % (eid, len(strokes),
                          [c["bbox"] for g in strokes for c in g])})
    return findings


def _elbow_with_label(where: str) -> list[dict]:
    """Two rects joined by an elbowed arrow carrying a bound edge label.

    The arrow runs right from `s1` to (360, 100), turns, and drops into
    `s2`'s top edge. A SHORT elbow on purpose: the 280px path puts its
    arc-length midpoint 20px from the turn, well inside a 60px label, so
    the product's own placement rule lands the backdrop on the corner.

    `where` decides what that backdrop covers, and nothing else about the
    four scenes differs — so a difference in the ablation delta is a
    difference the label's position made:

    - `"corner"` — parked over the turn by hand. r5-14's picture: the
      backdrop erases the elbow and both approaches.
    - `"run"` — parked mid-way along the horizontal run by hand. The
      backdrop breaks the ink here too, but collinearly, which is the
      legitimate bound-label idiom rather than a defect.
    - `"beside"` — clear of the stroke, above the horizontal run. The
      backdrop touches no ink at all.
    - `"routed"` — placed by `canvas.recenter_label`, i.e. by the
      product. The only variant that measures where the server actually
      puts a label rather than where this file does.

    Args:
        where: One of `"corner"`, `"run"`, `"beside"` or `"routed"`.
            Anything else is a `KeyError` from the table below rather
            than a scene with a silently defaulted label.

    Returns:
        The four-element scene: rects `s1`/`s2`, arrow `a1`, label `t1`.
    """
    src = el(id="s1", type="rectangle", x=120, y=80, width=80, height=40,
             customData={"role": "node"})
    dst = el(id="s2", type="rectangle", x=320, y=220, width=80, height=40,
             customData={"role": "node"})
    arr = el(id="a1", type="arrow", x=200, y=100, width=160, height=120,
             points=[[0, 0], [160, 0], [160, 120]],
             startBinding={"elementId": "s1", "focus": 0, "gap": 1},
             endBinding={"elementId": "s2", "focus": 0, "gap": 1},
             customData={"role": "edge"})
    lx, ly = {"corner": (330, 90), "run": (250, 90), "beside": (250, 60),
              "routed": (-999, -999)}[where]
    lbl = el(id="t1", type="text", x=lx, y=ly, width=60, height=20,
             text="then", fontSize=16, fontFamily=1, textAlign="center",
             verticalAlign="middle", containerId="a1", originalText="then")
    arr["boundElements"] = [{"id": "t1", "type": "text"}]
    scene = [src, dst, arr, lbl]
    if where == "routed":
        canvas.recenter_label(scene, arr)
    return scene


def _back_edge_with_label(where: str) -> list[dict]:
    """Two stacked rects joined by a back edge that turns TWICE.

    `_elbow_with_label` above has one turn, so a backdrop anywhere on it
    leaves two STRAIGHT stubs and `_completed_by_eye` judges them on the
    geometry it was written for. This scene adds the third segment that
    the judgement was never tested against: `s1` exits right, the run
    drops down the right margin, then turns back LEFT into `s2`'s right
    edge — the ordinary back-edge routing for a node returning to one
    stacked above it. Break it on the lower turn and one remnant is
    L-shaped (top run plus the vertical leg), and an L's bbox is tall
    AND wide, so it overlaps the far stub on an axis it never shares a
    stroke with. That is the whole point of the scene; nothing else
    about it differs from the elbow family.

    `where` moves the label and nothing else, so a difference in verdict
    is a difference the label's position made:

    - `"turn"` — over the lower turn at (360, 220). The erased-elbow
      picture: the leg stops in mid-air and a separate arrow arrives
      from the right. Identical in class to `_elbow_with_label
      ("corner")`, which fires.
    - `"leg"` — mid-way down the vertical leg. The legitimate bound-label
      idiom on this path, and where `canvas.recenter_label` puts this
      scene's label of its own accord (measured: x=330, y=150), so the
      neighbour is the product's own placement without depending on it.

    Args:
        where: `"turn"` or `"leg"`. Anything else is a `KeyError` rather
            than a scene with a silently defaulted label.

    Returns:
        The four-element scene: rects `s1`/`s2`, arrow `a1`, label `t1`.
    """
    src = el(id="s1", type="rectangle", x=120, y=80, width=80, height=40,
             customData={"role": "node"})
    dst = el(id="s2", type="rectangle", x=120, y=200, width=80, height=40,
             customData={"role": "node"})
    arr = el(id="a1", type="arrow", x=200, y=100, width=160, height=120,
             points=[[0, 0], [160, 0], [160, 120], [0, 120]],
             startBinding={"elementId": "s1", "focus": 0, "gap": 1},
             endBinding={"elementId": "s2", "focus": 0, "gap": 1},
             customData={"role": "edge"})
    lbl = el(id="t1", type="text", x=330, y={"turn": 210, "leg": 150}[where],
             width=60, height=20, text="then", fontSize=16, fontFamily=1,
             textAlign="center", verticalAlign="middle", containerId="a1",
             originalText="then")
    arr["boundElements"] = [{"id": "t1", "type": "text"}]
    return [src, dst, arr, lbl]


@unittest.skipUnless(RENDER, "render tier: set MUTANTS_RENDER=1 "
                             "(starts a headless browser)")
class TestRenderMutants(unittest.TestCase):
    """The two render-tier detectors, each proven and each held silent."""

    def setUp(self) -> None:
        """Make a scratch directory for this test's renders."""
        self.workdir = _mkworkdir()

    def tearDown(self) -> None:
        """Remove the scratch directory — renders never enter the repo."""
        shutil.rmtree(self.workdir, ignore_errors=True)

    def test_ablation_existence_fires_on_invisible_element(self) -> None:
        """An element whose ablation changes no pixels is not in the picture.

        The ghost is zero-extent as well as fully transparent because the
        tier-1 export has no notion of opacity — see the expected failure
        below, which pins exactly that gap.
        """
        scene = tm._diamond_stage()
        ghost = el(id="g1", type="rectangle", x=300, y=300, width=0,
                   height=0, opacity=0, customData={"role": "node"})
        finds = ablation_findings([*scene, ghost], ["g1"], self.workdir)
        self.assertIn("g1", [f["element"] for f in finds
                             if f["check"] == "ablation_existence"])

    def test_ablation_existence_fires_on_a_real_shipped_class(self) -> None:
        """A node stroked in the ground color is in the model, not the picture.

        Batch D item 2, 2026-08-13. `ablation_existence` had one proof
        before this — the ghost above — and that ghost is 0x0 and
        0%-opacity, which is to say an element no drawing has ever
        contained. It demonstrated the arithmetic on a scene built to
        make the arithmetic easy. This asks the same question of a real
        shipped class in a configuration a person can produce by
        accident: an ordinary 200x100 `rectangle`, full size, full
        opacity, ordinary `role: node`, whose `strokeColor` happens to be
        `SVG_GROUND` — the paper it is drawn on. Ops accept free-form
        colors, so nothing anywhere refuses it.

        The scene is `test_mutants._styled_scene`, shared on purpose with
        the three model-tier legibility mutants (`gray_text_on_ground`,
        `pale_stroke_node`, `tiny_font_text`) rather than rebuilt here:
        this is the same invisible-by-styling class seen from the other
        tier, and one base scene keeps the family readable. What differs
        is which instrument speaks. On the model tier that class is
        RED BY ABSENCE — `contrast_object` has no detector and sits in
        `ASPIRATIONAL` — so `pale_stroke_node` pins a lint that does not
        exist yet. Here it is green: the render tier can already see it,
        because a stroke the color of the paper leaves no pixels and
        pixels are all this tier reads. That contrast is the entry's
        point, and it is the argument for building the contrast lint at
        1:1 with what the render tier can already measure.
        """
        scene = tm._styled_scene(stroke=canvas.SVG_GROUND)
        finds = ablation_findings(scene, ["n1"], self.workdir)
        self.assertEqual([(f["check"], f["element"], f["magnitude"])
                          for f in finds],
                         [("ablation_existence", "n1", 0.0)])

    def test_neighbour_a_visible_node_is_in_the_picture(self) -> None:
        """The same node in ordinary ink: present, whole, and silent.

        The other pole of the proof above, and the only thing standing
        between it and a detector that reports every element as missing.
        One variable moves — `strokeColor`, from `SVG_GROUND` back to the
        default `#1e1e1e` — so the difference in verdict is the styling
        and nothing else.
        """
        scene = tm._styled_scene()
        self.assertEqual(ablation_findings(scene, ["n1"], self.workdir), [])

    def test_ablation_of_the_bbox_defining_element_still_reads(self) -> None:
        """Ablating the element that sets the viewport's own left edge.

        The scenes above all ablate an element well inside the drawing's
        bounds, so the ablated render happens to want the same viewport as
        the full one and `_shot`'s `<svg>`-tag splice is a no-op — meaning
        none of them would notice if it were removed. This ghost sits at
        x=-100, further left than anything else, so it DEFINES minx while
        drawing nothing: dropping it moves the viewport from 680x180 to
        520x180 and shifts the origin by 160px. With the splice, both
        shots land on one grid and the verdicts below hold. Reverting
        `_shot` to a naive `render_svg(kept)` makes this test fail loudly
        — and not via a size error, because the window is sized from the
        full scene either way: the ablated drawing simply renders shifted
        inside that window, every stroke lands somewhere new, and the
        ghost's delta comes back as a spurious two-component
        `ablation_continuity`. Verified by doing exactly that.
        """
        ghost = el(id="g0", type="rectangle", x=-100, y=300, width=0,
                   height=0, customData={"role": "node"})
        scene = [*tm._diamond_stage(), ghost]
        finds = ablation_findings(scene, ["g0", "a1"], self.workdir)
        # the extremal ghost draws nothing: existence fires, for it alone
        self.assertEqual([(f["check"], f["element"]) for f in finds],
                         [("ablation_existence", "g0")])

    @unittest.expectedFailure
    def test_mutant_opacity_ghost_is_invisible_to_tier_one(self) -> None:
        """A 0%-opacity node is invisible on the canvas but ink in tier 1.

        `canvas.render_svg` never reads `opacity` (its `paint` emits fill,
        stroke and dash and nothing else), so an element the user cannot
        see still contributes ink to the export and its ablation delta is
        non-empty. Flip this to a plain pass when ablation runs against
        the tier-2 render, which honours opacity.
        """
        scene = tm._diamond_stage()
        ghost = el(id="g1", type="rectangle", x=300, y=300, width=10,
                   height=10, opacity=0, customData={"role": "node"})
        finds = ablation_findings([*scene, ghost], ["g1"], self.workdir)
        self.assertIn("g1", [f["element"] for f in finds
                             if f["check"] == "ablation_existence"])

    def test_ablation_continuity_neighbour_is_silent(self) -> None:
        """A label beside the arrow leaves the connector's delta whole."""
        scene = _elbow_with_label("beside")
        finds = ablation_findings(scene, ["a1"], self.workdir)
        self.assertEqual([f for f in finds
                          if f["check"] == "ablation_continuity"], [])
        # An empty delta would satisfy that assertion too, so pin that the
        # arrow really did leave the picture: existence stays silent only
        # when ablating it changed pixels.
        self.assertEqual([f for f in finds
                          if f["check"] == "ablation_existence"], [])

    def test_neighbour_a_label_on_a_straight_run_is_silent(self) -> None:
        """The bound-label idiom: a backdrop mid-run is not a severed run.

        The third pole of the r5-14 family, and the one that keeps the
        exemption honest. `beside` above is silent because the backdrop
        touches no ink; this scene's backdrop breaks the stroke exactly
        as the elbow scene's does — same label, same size, same arrow,
        moved 80px along the horizontal run — and it is still not a
        defect, because the two stubs are collinear across the gap and
        the eye completes them. Without `_completed_by_eye` this scene
        fires, which is `ablation_continuity` reporting the tool's own
        idiom (the client breaks every arrow behind its bound label) as
        a broken drawing.

        Written in the same change as the exemption on purpose: an
        exemption whose only witness is the mutant it silences is
        indistinguishable from switching the detector off.
        """
        scene = _elbow_with_label("run")
        finds = ablation_findings(scene, ["a1"], self.workdir)
        self.assertEqual([f for f in finds
                          if f["check"] == "ablation_continuity"], [])
        self.assertEqual([f for f in finds
                          if f["check"] == "ablation_existence"], [])

    def test_mutant_label_backdrop_severs_connector(self) -> None:
        """A label parked on the elbow cuts the connector into two strokes.

        r5-14's class, measured from pixels: the label's opaque backdrop
        is painted over the corner, so the arrow's ink arrives from the
        left, stops, and resumes below — two components where the model
        holds one connector.

        Held apart from the mid-run scene above by geometry, not by a
        magnitude: both break the ink in two, and only this one leaves
        the pieces pointing nowhere near each other. Measured, the
        horizontal stub ends at raster y 60 and the vertical stub starts
        at 72.
        """
        scene = _elbow_with_label("corner")
        finds = ablation_findings(scene, ["a1"], self.workdir)
        severed = [f for f in finds if f["check"] == "ablation_continuity"]
        self.assertEqual([f["element"] for f in severed], ["a1"])
        self.assertGreaterEqual(severed[0]["magnitude"], 2.0)

    def test_the_product_keeps_its_own_label_off_the_elbow(self) -> None:
        """r5-14 itself: where `recenter_label` puts a short elbow's label.

        Every scene above places the label by hand, so all three pin the
        DETECTOR and none of them pins the product. This one asks the
        question r5-14 actually asked — six labels across three shipped
        artifacts landed on their own corner — by handing the scene to
        `canvas.recenter_label` and rendering whatever comes back.

        Before the fix the anchor is the path's arc-length midpoint, 20px
        from the turn, and the backdrop swallows the elbow: this is the
        `corner` scene reproduced by the product rather than by the test,
        and it fails here. After it, the anchor slides along the longest
        segment until the corner is clear of the label's own box, and the
        break lands mid-run where it reads as continuous.

        The assertion is on PIXELS and not on the stored anchor
        deliberately. r5-14's stored path was provably correct while the
        picture was severed, so a test that read `label["x"]` back would
        have agreed with the drawing that was wrong.
        """
        scene = _elbow_with_label("routed")
        finds = ablation_findings(scene, ["a1"], self.workdir)
        self.assertEqual([f for f in finds
                          if f["check"] == "ablation_continuity"], [])
        self.assertEqual([f for f in finds
                          if f["check"] == "ablation_existence"], [])

    def test_neighbour_back_edge_label_on_the_leg_is_silent(self) -> None:
        """A backdrop mid-way down the back edge's leg is the idiom, not a cut.

        The live pole of the pair below, and the half that constrains the
        fix. Both remnants here are L-shaped — the top run plus the leg's
        upper half, the leg's lower half plus the bottom run — so the
        cheap narrowing "an L-shaped remnant is never completed by eye"
        would over-fire on this scene and report the tool's own bound
        label as a severed connector. What makes it readable is that the
        two facing ENDS are collinear down the leg at x~358 and 25 raster
        rows apart, which is the property `_completed_by_eye` claims to
        test and does not.
        """
        scene = _back_edge_with_label("leg")
        finds = ablation_findings(scene, ["a1"], self.workdir)
        self.assertEqual([f for f in finds
                          if f["check"] == "ablation_continuity"], [])
        # Silence is only meaningful if the arrow drew something at all.
        self.assertEqual([f for f in finds
                          if f["check"] == "ablation_existence"], [])

    @unittest.expectedFailure
    def test_mutant_l_shaped_remnant_hides_a_severed_back_edge(self) -> None:
        """A remnant with a turn in it merges with a stub it never touches.

        Curator batch 14, from the Task 19 review (F5), 2026-08-14. Same
        defect class as `test_mutant_label_backdrop_severs_connector` and
        the same picture — a bound label's opaque backdrop parked on an
        elbow, the run arriving, stopping, and resuming somewhere the eye
        cannot follow it to — but on a path with a second turn, and
        `ablation_continuity` says nothing.

        Why the extra turn is the whole scene: broken on the lower turn,
        this connector's ink comes back as an L (122,59,280,167) and a
        bottom stub (122,176,258,183). The two are separated in y by 9
        rows, which is the severance, and OVERLAP in x for 136 columns —
        not because they share a stroke, but because the L is 158 columns
        wide and swallows the stub's range whole. `_completed_by_eye`
        reads that overlap as "the eye continues one into the other" and
        `_reader_strokes` merges them into one, so the finding is never
        emitted. The 2-segment `corner` scene escapes this only because
        two straight stubs give it two thin bboxes.

        Expected: `ablation_continuity` on `a1` with magnitude 2.0 — the
        count of pieces a reader sees, and 2 is the count in the raster,
        asserted as a whole projection rather than by indexing into an
        empty list so this is red BY ASSERTION and not by IndexError.

        Fix ownership is WP4's, not this file's, and the neighbour above
        is the constraint on it: whatever replaces the bbox test must
        still complete a break on the leg, so the property to reach for
        is where the facing ends POINT, not how big the pieces are.
        """
        scene = _back_edge_with_label("turn")
        finds = ablation_findings(scene, ["a1"], self.workdir)
        self.assertEqual([(f["check"], f["element"], f["magnitude"])
                          for f in finds],
                         [("ablation_continuity", "a1", 2.0)])


# ---------------------------------------------------------------------------
# Paint order, read as pixels. `test_mutants.TestPaintOrder` asserts the same
# contract as EMISSION ORDER in the SVG string, which is cheap, exact and runs
# on every commit — so why measure it again with a browser? Because emission
# order is a claim about markup and occlusion is a claim about the picture,
# and the two come apart the moment anything is transparent, clipped, or
# painted in the colour of the paper. The string can say "the connector went
# down after the panel" while the reader sees no connector.
#
# Both classes below share their base scenes with the model tier on purpose
# (skill doctrine: dedupe families onto one base scene rather than deleting
# either member) — `test_mutants._backdrop_scene` for the first, and the
# product's own `make_element` composition for the second.
#
# One thing this tier reads that the other cannot, and it decides both
# entries: `tolerant_diff` binarises at luminance 192, so INK is what is
# darker than that. The pale panel these scenes use is above the floor and is
# therefore paper, not a mark. That is exactly the right instrument for the
# question "does the panel erase the connector" — the only ink in the answer
# is the connector's own — and exactly the wrong one for "is the panel there",
# which nothing here asks.
#
# Both classes reach forward to `_element_ink`, which is defined with the
# parity section below because that is what needed a reframing ablation
# first. It is the plain one here (`pad=0`, tier 1's own frame) and it is
# left where it is rather than moved up, so the parity section's helpers stay
# together.
# ---------------------------------------------------------------------------


@unittest.skipUnless(RENDER, "render tier: set MUTANTS_RENDER=1 "
                             "(starts a headless browser)")
class TestPaintOrderInPixels(unittest.TestCase):
    """A decoration at index 0 leaves the connector visible — in the raster."""

    def setUp(self) -> None:
        """Make a scratch directory for this test's renders."""
        self.workdir = _mkworkdir()

    def tearDown(self) -> None:
        """Remove the scratch directory — renders never enter the repo."""
        shutil.rmtree(self.workdir, ignore_errors=True)

    def test_a_decoration_at_index_zero_leaves_the_connector_in_the_picture(
            self) -> None:
        """Curator batch 16 item 1 (Task 21 §8.1), 2026-08-14. GREEN.

        The pixel sibling `test_mutants.TestPaintOrder.
        test_red_zorder_bucketing_occludes_connector` asked for and could
        not have. While the bucketing defect was live, this measurement
        was the defect's own victim: a connector erased by a panel it was
        declared beneath contributes no ink, so `ablation_existence`
        reported a plainly visible element invisible, and a test asserting
        the correct answer here could not be told apart from one asserting
        the broken detector. v0.9 WP4 cleared that, so the claim is
        writable at last — and until now nothing had written it. This pins
        the fix in the tier whose whole claim is that it reads the
        picture.

        Measured 2026-08-14, and both halves matter: the connector's
        ablation ink is 348 px with the panel at index 0 and 0 px with the
        panel at index 1. The magnitude is asserted as well as the
        detector's silence, because silence alone would also be produced
        by a scene that drew nothing at all — and 0 px is precisely the
        other pole's reading.

        Sole-failure verified by reverting `render_svg`'s dispatch to the
        four type-filtered buckets in a throwaway tree: this test fails
        and the index-1 pole below does not, because the buckets painted
        shapes after arrows either way round.
        """
        scene = tm._backdrop_scene(behind=True)
        self.assertEqual(
            ablation_findings(scene, ["e1"], self.workdir), [],
            "the connector is declared AFTER the panel that covers it and "
            "must survive into the raster")
        ink = _element_ink(scene, "e1", self.workdir)[0]
        # 348px measured; the +-10% band excludes 0 (erased entirely),
        # which is the whole defect and is what the other pole reads.
        self.assertAlmostEqual(ink, 348, delta=35)

    def test_neighbour_a_decoration_at_index_one_erases_it(self) -> None:
        """The other pole: declared in front, the panel really does cover.

        The control the pin above needs, and the only thing standing
        between it and an instrument that reports every element as
        present. One variable moves — which of two elements is declared
        first — so the difference in verdict is the array order and
        nothing else. It is also this scene's proof that
        `ablation_existence` is alive here at all: a detector that had
        stopped firing would let the pin above pass forever.
        """
        scene = tm._backdrop_scene(behind=False)
        self.assertEqual(
            [(f["check"], f["element"], f["magnitude"])
             for f in ablation_findings(scene, ["e1"], self.workdir)],
            [("ablation_existence", "e1", 0.0)])
        self.assertEqual(_element_ink(scene, "e1", self.workdir)[0], 0)


def _kpi_tile(fill: str) -> list[dict]:
    """A `kind: "kpi"` tile, composed by the product, with the given fill.

    Built through `canvas.make_element` and ordered by
    `canvas.normalize_z_order` rather than assembled by hand, because the
    defect below is not a scene anyone drew — it is what the composition
    and the banding produce between them, and a hand-built stack would be
    this file asserting its own arrangement. One documented `mod
    backgroundColor` on any of the corpus's 29 composed value texts
    reaches it.

    `_deco` stamps `role: "decoration"` on the value text, the same role
    it stamps on genuine furniture (X-box strokes, wavy body lines), and
    `normalize_z_order` used to band every decoration at 1 — beneath
    nodes at 3 — so the tile's own CONTENT was declared under its own
    container. Task 44 split that band by part tag: `value_of` and
    `attr_of` are content and ride above the node band, furniture and
    backdrops stay beneath.

    Args:
        fill: The owner rectangle's `backgroundColor`. `"transparent"`
            is the whole corpus today; an opaque colour is what the
            defect needed, and `#e9e5da` is the reference example both
            `add` and `mod` document.

    Returns:
        The composed tile in paint order: `k1`, `k1-value`, `k1-label`
        (before task 44: `k1-value`, `k1`, `k1-label`).

    Raises:
        RuntimeError: If `make_element` rejected the spec. Said out loud
            because a rejected spec returns an empty list, and an empty
            scene would make every measurement below vacuously agree
            with itself — including the red's, which would then be a
            broken pin reading as a healthy one.
    """
    ids: set[str] = set()
    errors: list[str] = []
    parts = canvas.make_element(
        {"id": "k1", "type": "rectangle", "x": 0, "y": 0, "width": 160,
         "height": 80, "label": "Revenue", "kind": "kpi", "value": "42%",
         "backgroundColor": fill}, ids, errors)
    if errors or not parts:
        raise RuntimeError("make_element refused the kpi tile: %s" % errors)
    return canvas.normalize_z_order(parts)


@unittest.skipUnless(RENDER, "render tier: set MUTANTS_RENDER=1 "
                             "(starts a headless browser)")
class TestComposedContentVisibility(unittest.TestCase):
    """A tile's own value must survive its owner's fill."""

    def setUp(self) -> None:
        """Make a scratch directory for this test's renders."""
        self.workdir = _mkworkdir()

    def tearDown(self) -> None:
        """Remove the scratch directory — renders never enter the repo."""
        shutil.rmtree(self.workdir, ignore_errors=True)

    def test_composed_value_survives_its_opaque_owner_by_measurement(self
                                                                     ) -> None:
        """The flipped red's magnitude, measured rather than inferred.

        This is what the red's own red-by-measurement guard became when
        task 44 flipped it, and the job it does now is a different one.
        The guard's original job is gone: it existed because
        `@unittest.expectedFailure` swallows ERRORS as well as failures
        (skill doctrine §6), and dropping that marker is what removed the
        hazard — the red reports its own breakage now, including a
        composition that stopped emitting `k1-value` at all, which makes
        `ablation_existence` fire on the missing id and the red FAIL.

        What the red cannot see is DEGRADATION. It asserts silence, and
        silence is the answer for a value that is in the picture at all,
        however little of it: a font-size or clipping regression that
        left 100 px of the 309 showing keeps the red green and fails only
        here. Magnitude is this test's whole contribution, and the number
        moved rather than the purpose — the guard pinned the defect's
        magnitude at 0 px of ablation ink, this pins the fix's. It is
        deliberately the SAME number the transparent-owner neighbour
        reads (309 px, 2026-08-14), same string, same font, same size, so
        the pair together says an opaque fill costs the value nothing at
        all.
        """
        ink = _element_ink(_kpi_tile("#e9e5da"), "k1-value", self.workdir)[0]
        self.assertAlmostEqual(
            ink, 309, delta=31,
            msg="the tile's own value measured %d px of ablation ink under "
                "an opaque owner; 0 px is the task 44 defect returning and "
                "anything else means the composition changed" % ink)

    def test_mutant_composed_value_hides_under_its_opaque_owner(self) -> None:
        """FLIPPED by v0.9 WP4 (Task 44). Kept its red-era name.

        Curator batch 16 item 5, from the Task 21 review (F2),
        2026-08-14. `_deco` overloaded `role: "decoration"` to mean two
        unrelated things — "exempt from the lints that judge authored
        content" and "paint underneath" — and composed CONTENT was
        stamped with it alongside genuine furniture. `normalize_z_order`
        then banded the whole role at 1, under nodes at 3, so a tile's
        value text was declared beneath the tile that owned it and an
        opaque `backgroundColor` finished the job.

        This was a PRODUCT defect the render tier could already see, not
        a detector miss: `ablation_existence` fired correctly on it. It
        was live rather than theoretical. The live canvas had hidden
        these values all along — the frontend applies the server's
        element order verbatim — and it was the export's old text-last
        bucket that masked it; v0.9 WP4 stopped masking it, which is how
        it was found. All 29 composed value/attr texts in the corpus
        have transparent owners today, and `backgroundColor` is a
        documented first-class property of both `add` and `mod`, so one
        op on any of them buried its own content.

        Task 44 split the overload where the overload was: the band, not
        the role. `normalize_z_order` now reads the PART TAG, so
        `value_of` and `attr_of` — a composite's own content — band above
        the node that owns them, while furniture (`box_of`, `track_of`,
        `body_of`, …) and standalone backdrops keep banding beneath. The
        role still means exactly one thing, and it is the lint
        exemption. Two things that were rejected as fixes, deliberately:
        giving the value an opaque fill of its own would have hidden the
        banding rather than fixed it, and reinstating a text-last pass in
        `render_svg` would have restored the export/canvas disagreement
        WP4 removed — the export would show a value the user cannot see.
        """
        scene = _kpi_tile("#e9e5da")
        finds = ablation_findings(scene, ["k1-value"], self.workdir)
        self.assertEqual(
            finds, [],
            "the tile's own value is painted under the tile: %s"
            % [f["raw"] for f in finds])

    def test_neighbour_a_transparent_owner_leaves_its_value_visible(self
                                                                    ) -> None:
        """The other pole: the same tile, the same value, no fill.

        The control that keeps the red meaningful — one variable moves,
        the owner's `backgroundColor` — and the whole corpus's current
        state, which is the reason the 24-artifact replay came back
        pixel-clean and the hazard went unnoticed. Without it the red
        would be satisfied by a renderer that drew no composed content at
        all.
        """
        scene = _kpi_tile("transparent")
        self.assertEqual(
            ablation_findings(scene, ["k1-value"], self.workdir), [])
        # Silence has to mean "the value is in the picture", never
        # "nothing was drawn either way", so pin that there was ink:
        # 309px measured 2026-08-14, the same glyphs the red loses.
        self.assertAlmostEqual(
            _element_ink(scene, "k1-value", self.workdir)[0], 309, delta=31)


# The composed controls whose furniture this tier can actually MEASURE, each
# with the state it needs, the part that CARRIES that state, and that part's
# ink under a transparent owner (px, measured 2026-08-14, band ~10% and
# wider on the smallest where one antialiased row is a larger fraction).
#
# `body` and `image` are absent on purpose, and the reason is measurement
# rather than oversight — task-44-report §5.1 lists all five composites as
# buried on the strength of the OPAQUE pole alone, and the other pole says
# only three of them are. Body waves and X-box diagonals are stroked
# `#b8b2a5` at width 1: the diagonals rasterize above `tolerant_diff`'s
# ink threshold of 192 (`f1-x1`: 0px of residual even at `min_blob=1`) and
# the waves survive only as speckle under the `MIN_BLOB` floor (`f1-body1`:
# 42px at `min_blob=1`, 0px at 12). Both therefore read as ABSENT with a
# transparent owner too, so `ablation_existence` firing on them says
# nothing about burial. That is why the sweep below measures both poles for
# every row rather than trusting the opaque one — the same vacuity trap the
# neighbour exists to close, met here in the wild.
STATE_CONTROLS = (("checkbox", {"checked": True}, "f1-chk", 36, 6),
                  ("toggle", {"checked": True}, "f1-thumb", 126, 13),
                  ("slider", {"value": "50"}, "f1-thumb", 108, 11))


def _control_composite(kind: str, fill: str,
                       state: dict[str, Any]) -> list[dict]:
    """A composed control of `kind`, with the given fill and state.

    `_kpi_tile`'s sibling, and deliberately the same shape: built through
    `canvas.make_element` and ordered by `canvas.normalize_z_order` rather
    than assembled by hand, because the defect is what the composition and
    the banding produce between them. The two builders stay separate
    rather than sharing a core — the kpi one is the FLIPPED half of this
    family and its docstring is about content, this one is the live half
    and its docstring is about furniture.

    Minimized to three elements: no label. A composite's bound label is
    band-5 text that no fill can reach, so it is incidental to the
    question and its absence removes a band from the picture.

    Args:
        kind: `checkbox`, `toggle` or `slider` — the composites whose
            furniture carries STATE. `_compose_control_glyph` and
            `_compose_slider_glyph` stamp `box_of`/`chk_of`/`thumb_of`/
            `track_of` on the parts, all of them `role: "decoration"`.
        fill: The owner rectangle's `backgroundColor`. `"transparent"` is
            the whole corpus today; `#e9e5da` is the opaque reference
            colour both `add` and `mod` document.
        state: The control's own state — `{"checked": True}`, or the
            slider's `value`. Passed as a dict rather than kwargs so the
            sweep below can carry it in a table with the magnitudes.

    Returns:
        The composed control in paint order: the parts, then the owner.

    Raises:
        RuntimeError: If `make_element` rejected the spec. Said out loud
            because a rejected spec returns an empty list, and an empty
            scene would make every measurement below vacuously agree with
            itself — including the red's.
    """
    ids: set[str] = set()
    errors: list[str] = []
    spec: dict[str, Any] = {"id": "f1", "type": "rectangle", "x": 0, "y": 0,
                            "width": 160, "height": 80, "kind": kind,
                            "backgroundColor": fill}
    spec.update(state)
    parts = canvas.make_element(spec, ids, errors)
    if errors or not parts:
        raise RuntimeError("make_element refused the %s: %s" % (kind, errors))
    return canvas.normalize_z_order(parts)


@unittest.skipUnless(RENDER, "render tier: set MUTANTS_RENDER=1 "
                             "(starts a headless browser)")
class TestComposedFurnitureVisibility(unittest.TestCase):
    """A control's own state glyph must survive its owner's fill."""

    def setUp(self) -> None:
        """Make a scratch directory for this test's renders."""
        self.workdir = _mkworkdir()

    def tearDown(self) -> None:
        """Remove the scratch directory — renders never enter the repo."""
        shutil.rmtree(self.workdir, ignore_errors=True)

    def test_composed_furniture_red_is_red_by_measurement_not_by_error(self
                                                                       ) -> None:
        """The red below is red for the reason it claims, across all three.

        Three jobs. First, `@unittest.expectedFailure` swallows ERRORS as
        well as failures (skill doctrine §6), so a `make_element` that
        began refusing the spec would print an identical healthy `x` with
        nothing measured. Second, this is where the defect's magnitude
        lives: 0px of ablation ink under an opaque owner against the ink
        the SAME part carries under a transparent one, so silence here
        can never mean "nothing was drawn either way" — the failure mode
        that put `body` and `image` outside `STATE_CONTROLS`.

        Third, and the reason this sweeps rather than measuring the
        checkbox alone: the red names one composite and the fix is
        expected to move all three at once, so a partial fix — one that
        taught `band()` about `chk_of` and not `thumb_of` — would flip
        the red and leave two controls buried with nothing to say so.
        Toggle and slider are pinned as measurements here rather than as
        prose in a docstring, because prose about counts has gone stale
        in this repo three times and a measurement cannot.
        """
        for kind, state, part, lit_ink, slack in STATE_CONTROLS:
            with self.subTest(kind=kind):
                try:
                    buried = _control_composite(kind, "#e9e5da", state)
                    finds = ablation_findings(buried, [part], self.workdir)
                    ink = _element_ink(_control_composite(kind, "transparent",
                                                          state),
                                       part, self.workdir)[0]
                except Exception as exc:
                    self.fail("the composed-furniture red is red via %r, "
                              "not a measurement — that is a broken pin, "
                              "not a defect pin" % exc)
                self.assertEqual(
                    [(f["check"], f["element"], f["magnitude"])
                     for f in finds],
                    [("ablation_existence", part, 0.0)],
                    "ablation_existence no longer fires on %s's buried %s — "
                    "if the decoration band was split for furniture too, "
                    "drop the expectedFailure on this class's red and "
                    "re-pin these three as the fix's magnitudes"
                    % (kind, part))
                self.assertAlmostEqual(
                    ink, lit_ink, delta=slack,
                    msg="%s's %s measured %dpx with a TRANSPARENT owner; "
                        "the opaque reading above is only evidence of "
                        "burial while this one is nonzero" % (kind, part, ink))

    @unittest.expectedFailure
    def test_mutant_composed_checkbox_state_hides_under_its_opaque_owner(self
                                                                        ) -> None:
        """A checked checkbox draws as an empty box. Owner: unassigned.

        Curator batch 17, from the Task 44 report §8.1 and its review's
        curator queue, 2026-08-14. The same quadruple as the composed
        CONTENT red next door, one part tag over, and it is the half that
        Task 44 deliberately did not take: `normalize_z_order` now bands
        `value_of`/`attr_of` above the node that owns them
        (`CONTENT_PART_KEYS`), while composed FURNITURE — `box_of`,
        `chk_of`, `thumb_of`, `track_of`, `body_of`, `x_of` — stays at
        band 1, beneath its owner at 3. Every composite emits its parts
        BEFORE the owner, so one documented `mod backgroundColor` on the
        owner paints its own control out.

        This is a PRODUCT defect the render tier can already see, not a
        detector miss: `ablation_existence` fires correctly and says so
        in the guard above. What makes it worse than the content case is
        WHICH part goes: the buried glyph is the one carrying STATE. A
        checked checkbox with an opaque tile draws as a plain filled
        rectangle — not as a control that is merely hard to read, but as
        an UNCHECKED one — and an agent narrating the snapshot back to
        the user will say so. Measured on the sibling parts, same op:
        `f1-box` 92px→0, `f1-chk` 36px→0, toggle 168/126→0, slider
        248/108→0.

        Owner: UNASSIGNED — no work package holds it, and the Phase 3
        gate (Task 24 surface) schedules or defers it. The fix is one
        line in `band()`, widening the content-tag test to the whole part
        vocabulary, but it is not a free line: it moves furniture markup
        across the corpus (5+ artifacts carry `x_of`/`track_of`/
        `thumb_of`), so it needs its own fixture-replay budget, which is
        exactly why Task 44 left it rather than smuggling it in under
        acceptance tests that did not cover it. Two things that will NOT
        flip it, deliberately: giving the glyph an opaque fill of its own
        would hide the banding rather than fix it, and lifting the whole
        `role: "decoration"` band would drag standalone BACKDROPS up with
        it — the arrangement layout.md prescribes for parallel edges,
        which `TestPaintOrder`'s backdrop pin holds down. The model-tier
        half of this defect is that class's banding pin, whose `w1`
        (`body_of`) member sits under its owner today and moves in the
        same change as this flip; it says so in its own docstring.
        """
        scene = _control_composite("checkbox", "#e9e5da", {"checked": True})
        finds = ablation_findings(scene, ["f1-box", "f1-chk"], self.workdir)
        self.assertEqual(
            finds, [],
            "the checkbox's own box and check stroke are painted under the "
            "tile that owns them: %s" % [f["raw"] for f in finds])

    def test_neighbour_a_transparent_owner_leaves_the_check_visible(self
                                                                    ) -> None:
        """The other pole: the same control, the same state, no fill.

        The control that keeps the red meaningful — one variable moves,
        the owner's `backgroundColor` — and the whole corpus's current
        state, which is why nothing has noticed. Without it the red would
        be satisfied by a renderer that drew no control glyphs at all,
        which is not a hypothetical here: `body` and `image` furniture
        reads as absent at BOTH poles, and only this pole can tell the
        two apart.
        """
        scene = _control_composite("checkbox", "transparent",
                                   {"checked": True})
        self.assertEqual(
            ablation_findings(scene, ["f1-box", "f1-chk"], self.workdir), [])
        # Silence has to mean "the control is in the picture", so pin the
        # ink: 36px on the check stroke, 92px on its box, measured
        # 2026-08-14 — the state glyph the red loses, and the outline
        # that would make it read as unchecked rather than as missing.
        self.assertAlmostEqual(
            _element_ink(scene, "f1-chk", self.workdir)[0], 36, delta=6)


# ---------------------------------------------------------------------------
# Snapshot framing. Nothing above leaves this file's own `_shot`; the tests
# below drive `canvas.py snapshot` end to end — a real Project, a real Store,
# the real argv — because the defect they pin lives in that CLI's tier 2 and
# nowhere else. `rasterize_svg` clamped the browser window to 3000px wide
# (`win_w = max(min(want_w, 3000), 320)`) while `render_svg` only scaled a
# drawing down past 4000px wide, so anything between those two numbers was
# rendered at full size into a window too narrow to hold it and the overflow
# was simply not in the PNG. `validate_png` then compared the file against the
# WINDOW, not against the drawing, so the snapshot reported VALID=true and
# TIER=2 with pieces of the artifact missing. That is what bit the ELK spike:
# the 12-node dagre arm lost `Hand to carrier` and `Delivered` off the right
# edge (ELK-RESULTS.md, "What the eyes caught" item 4).
#
# FIXED in v0.9 WP4 (task 20): one shared ceiling for both, and a
# `validate_png` that measures the file against the drawing. These tests stay
# as the regression — the two numbers below are the OLD clamps, held here as
# literals so a re-narrowing of the window is caught by a scene that still
# straddles them.
# ---------------------------------------------------------------------------

# The window clamp `rasterize_svg` used to carry. Kept as a number here on
# purpose: importing the clamp would make the test agree with the bug by
# construction, and it no longer exists in canvas.py to import.
SNAP_WIN_CAP = 3000
# Node spans (outer node x to outer node x) chosen either side of the old cap
# and both under render_svg's 4000px scale-down, so the uniform-scale path
# never runs and PNG x is svg x minus minx exactly. Asserted, not assumed.
WIDE_SPAN = 3400
NARROW_SPAN = 1200
SVG_PAD = 40   # canvas.render_svg's fixed margin


def _span_flow_batch(span: int) -> dict:
    """A three-node left-to-right flow whose outer nodes are `span` apart.

    Args:
        span: X distance from the leftmost node's origin to the
            rightmost node's origin.

    Returns:
        An apply batch that creates the `wide` flow artifact.
    """
    ops: list[dict] = []
    prev = None
    for i, x in enumerate((0, span // 2, span)):
        nid = "n%d" % i
        ops.append({"op": "add", "element": {
            "type": "rectangle", "id": nid, "label": "Step %d" % i, "x": x,
            "y": 100, "width": 160, "height": 60, "role": "node"}})
        if prev is not None:
            ops.append({"op": "add",
                        "element": {"type": "arrow", "id": "t%d" % i},
                        "from": prev, "to": nid})
        prev = nid
    return {"base_revn": 0, "artifact": "wide",
            "create": {"id": "wide", "name": "Wide", "type": "flow",
                       "concept": "w", "concept_name": "Wide"},
            "ops": ops}


def _span_scene(span: int, root: Path) -> tuple[list[dict], int]:
    """Build the `span`-wide flow in a project and ask what it wants to be.

    The browser-free half of `_rightmost_node_ink`, split out so the
    ungated regime test below computes `want_w` by exactly the same route
    the mutant does — a regime invariant checked against a differently
    derived number would not be checking the mutant's regime.

    Args:
        span: Passed to `_span_flow_batch`.
        root: Project root to create. The caller owns it, and owns
            calling `_clear_runtime(root)` afterwards — including when
            this function raises part-way through.

    Returns:
        `(elements, want_w)` — the committed scene and the width
        `render_svg` asks for.
    """
    project = canvas.Project(root)
    project.ensure_tree()
    store = canvas.Store(project)
    store.apply_batch(_span_flow_batch(span))
    els = store.scenes["wide"]
    _svg, want_w, _want_h = canvas.render_svg(els, title="Wide")
    return els, want_w


def _clear_runtime(root: Path) -> None:
    """Delete the runtime files a project rooted at `root` keeps outside it.

    `Project` hashes its root path into the system temp dir for state,
    events and log, so removing the project tree alone would leave three
    files behind per test run — and `tearDown`'s `rmtree` cannot reach
    them either.

    Takes the ROOT, not a live `Project`, so that a caller can put it in
    a `finally` that also covers CONSTRUCTION. Those paths are a pure
    function of the root, so a project that raised half-built — before
    any `Project` object came back to hand to this — is still cleaned up.
    Reconstructing the `Project` here is free: `__init__` only hashes the
    path and ensures the runtime dir.

    Args:
        root: The project root that was (or was being) built.
    """
    project = canvas.Project(root)
    # system tempdir, not `workdir` — tearDown's rmtree never reaches these
    for path in (project.state_path, project.events_path,
                 project.log_path):
        if path.exists():
            path.unlink()


def _rightmost_node_ink(span: int, workdir: str) -> tuple[int, int, int]:
    """Snapshot a `span`-wide flow and count ink where its last node lands.

    A region-scoped ink count rather than an ablation: ablation answers
    "does this element contribute to the picture", and the snapshot cap
    does not change what an element contributes — it changes whether the
    part of the canvas holding it is in the file at all. Running the
    product path twice (with and without `n2`) would diff two pictures
    that are byte-identical in the region we can see and differ only in a
    region neither PNG contains, which is exactly no evidence. Counting
    ink in the column band `n2` must occupy asks the question directly,
    and it is the same question a reader asks of the snapshot: is the last
    box there?

    Args:
        span: Passed to `_span_flow_batch`.
        workdir: Directory to build the project and write the PNG into.

    Returns:
        `(ink_pixels, png_width, wanted_width)` — ink counted over the
        full raster height within `n2`'s own column band, the PNG's real
        width, and the width `render_svg` asked for.

    Raises:
        RuntimeError: If the snapshot CLI exited non-zero or wrote no
            PNG. Like `_browser`, this never degrades to a skip: the
            tier was asked for, so not measuring is a failure.
    """
    root = Path(workdir) / ("proj-%d" % span)
    # construction INSIDE the try: `_span_scene` writes a project tree and
    # can raise part-way, and the runtime files it would leave behind sit
    # outside `workdir` where tearDown's rmtree will never find them.
    try:
        _els, want_w = _span_scene(span, root)
        out = root / "shot.png"
        # the CLI prints KEY=VALUE lines; swallow them so a passing run is
        # quiet and a failure's message is the assertion, not the banner
        with contextlib.redirect_stdout(io.StringIO()):
            rc = canvas.main(["--project", str(root), "snapshot",
                              "--artifact", "wide", "--out", str(out),
                              "--no-tab"])
        if rc != 0 or not out.exists():
            raise RuntimeError("snapshot CLI failed (rc=%s) for span %d"
                               % (rc, span))
        pw, ph, pix = read_png_gray(out.read_bytes())
    finally:
        _clear_runtime(root)
    # n0 sits at x=0 and is the leftmost thing drawn, so render_svg's minx
    # is -SVG_PAD; with no uniform scale in play (the regime
    # `TestSnapshotFramingRegime` pins ungated) PNG x is svg x minus that.
    # This mapping is 1:1 BY CONSTRUCTION and the mutant's flip contract
    # depends on it — read that docstring before changing either.
    x0, x1 = span + SVG_PAD, span + 160 + SVG_PAD
    ink = sum(1 for y in range(ph) for x in range(x0, min(x1, pw))
              if pix[y * pw + x] < 192)
    return ink, pw, want_w


@unittest.skipUnless(RENDER, "render tier: set MUTANTS_RENDER=1 "
                             "(starts a headless browser)")
class TestSnapshotFraming(unittest.TestCase):
    """Does `canvas.py snapshot` put the whole drawing in the file?"""

    def setUp(self) -> None:
        """Make a scratch directory for this test's project and renders."""
        self.workdir = _mkworkdir()

    def tearDown(self) -> None:
        """Remove the scratch directory — renders never enter the repo."""
        shutil.rmtree(self.workdir, ignore_errors=True)

    def test_mutant_snapshot_cap_drops_the_rightmost_node(self) -> None:
        """A drawing wider than the window cap loses its right-hand end.

        FLIPPED by v0.9 WP4 (task 20), and flipped with this file's band
        mapping UNTOUCHED — which was the whole design of the fix, for the
        reasons the red version spelled out below. `rasterize_svg`'s window
        clamp and `render_svg`'s own scale-down are now one shared ceiling
        (`canvas.RASTER_MAX_W`/`_H`), so the window follows the drawing and
        a 3640px drawing is rasterized into a 3640px window. `validate_png`
        also stopped measuring the file against the WINDOW: it takes the
        drawing's extent, and a raster short of it fails at 2px instead of
        passing inside a 728px symmetric tolerance. Either half alone would
        have left the product half-honest — the first without the second
        puts the picture in the file but keeps the check that would not
        have noticed, and the second without the first turns tier 2 into a
        refusal for every wide drawing.

        What it pinned while red: the artifact wants 3640px; the window was
        clamped to 3000; the rightmost node's whole column band was past
        the clamp, so its ink count was 0 and the agent's only view of its
        own drawing was missing a node it had just placed — reported
        VALID=true.

        WHAT FLIPPED THIS, precisely, and what would have been a lie. The
        band is mapped 1:1 (PNG x == svg x minus minx), so **the window
        follows the drawing** is the only repair that turns this green
        untouched. Every other candidate needed work here too, and two of
        them were traps:

        - **Scale-to-fit inside `rasterize_svg`** — the fix V0.9-PLAN
          recommends first, and what the spike's own `fullshot.py` did —
          leaves this RED on a FIXED product. The whole drawing lands in a
          3000px PNG at 0.824x, so `n2`'s ink sits at PNG x 2835..2967
          while this band still looks at 3440..3600, off the right edge.
          Such a fix MUST rewrite `_rightmost_node_ink`'s band mapping in
          the same change, or it will look like it failed. (WP4 kept
          scale-to-fit as the arm past the shared ceiling — see
          `rasterize_svg` — precisely so that arm never touches this
          scene, which sits under it.)
        - **Naive proportional band scaling is NOT that rewrite.** Multiply
          the band by `png_w / want_w` and it lands at 2835..2967 — which
          on the UNFIXED 1:1 raster is the middle of the `n1 -> n2` arrow's
          horizontal run (svg-relative x 1900..3440). Measured against the
          product as it stood: 264 ink pixels, so the test would have gone
          GREEN with two nodes still missing from the picture. Any band
          rewrite has to derive its mapping from the PNG the product
          actually produced AND be re-run against the unfixed product
          first, to watch it fail.
        - **Raising `render_svg`'s own 4000px threshold** takes the scene
          out of the regime this mutant measures. That does not flip it; it
          fails `TestSnapshotFramingRegime` loudly instead, which is the
          intended signal — and it is still the signal now that the same
          number bounds the window.
        - **A truncation warning alone** (V0.9-PLAN WP5's fallback
          suggestion) changes stdout, not pixels, and would have left this
          red — correctly, because the drawing would still not be in the
          file.

        The regime guards this test carried inline while red live in
        `TestSnapshotFramingRegime`: inside an `expectedFailure` every
        assertion reported identically as "expected failure", so a guard
        here could never signal and would have let the test silently stop
        measuring anything. Green, that argument no longer applies, but the
        split is kept — the regime is checkable without a browser and the
        rot it catches happens in ordinary editing, where nobody has
        `MUTANTS_RENDER=1` set.
        """
        ink, pw, want_w = _rightmost_node_ink(WIDE_SPAN, self.workdir)
        self.assertGreater(ink, 0, "the rightmost node left no ink in its "
                                   "own column band: PNG is %dpx wide for "
                                   "a %dpx drawing" % (pw, want_w))

    def test_neighbour_narrow_snapshot_keeps_the_rightmost_node(self) -> None:
        """The same path on a drawing that fits: the last node is there.

        Without this the red above would prove nothing — an ink check
        that can never find ink fails on a truncated snapshot and on a
        perfect one alike.
        """
        ink, pw, want_w = _rightmost_node_ink(NARROW_SPAN, self.workdir)
        self.assertLess(want_w, SNAP_WIN_CAP, "span not under the clamp")
        self.assertGreaterEqual(pw, want_w, "narrow drawing was clamped")
        self.assertGreater(ink, 0, "the rightmost node left no ink in its "
                                   "own column band even unclamped — the "
                                   "check itself is broken")


class TestSnapshotFramingRegime(unittest.TestCase):
    """The wide scene must stay in the band of widths the mutant means.

    Deliberately NOT gated, for the same reason `TestRenderTierEvidence`
    is not: this needs no browser, and the rot it catches — the scene
    drifting out of the regime, so the mutant measures nothing and still
    reports green — happens in ordinary editing, where nobody has
    `MUTANTS_RENDER=1` set. Gated, it would notice months late. While the
    mutant was red it could not notice at all, since inside an
    `expectedFailure` every assertion reports identically.
    """

    def test_wide_scene_sits_between_the_two_caps(self) -> None:
        """`WIDE_SPAN` wants a width past the old clamp but under 4000.

        Both bounds are load-bearing. Under `SNAP_WIN_CAP` the scene
        never straddles the window clamp that used to truncate it, so a
        re-narrowed window would go unnoticed. Over 4000, `render_svg`'s
        uniform scale-down kicks in, PNG x stops being svg x minus minx,
        and `_rightmost_node_ink`'s 1:1 band mapping silently measures
        the wrong column.
        """
        root = Path(tempfile.mkdtemp(prefix="mutants-regime-"))
        try:
            _els, want_w = _span_scene(WIDE_SPAN, root)
        finally:
            _clear_runtime(root)
            shutil.rmtree(root, ignore_errors=True)
        self.assertGreater(want_w, SNAP_WIN_CAP,
                           "WIDE_SPAN no longer overflows the %dpx window "
                           "clamp (wants %dpx): the snapshot mutant has "
                           "nothing to measure" % (SNAP_WIN_CAP, want_w))
        self.assertLess(want_w, 4000,
                        "WIDE_SPAN (wants %dpx) reached render_svg's own "
                        "4000px scale-down: PNG x is no longer svg x minus "
                        "minx, so _rightmost_node_ink's band mapping is "
                        "wrong and the mutant is measuring the wrong "
                        "column" % want_w)


@unittest.skipUnless(RENDER, "render tier: set MUTANTS_RENDER=1 "
                             "(starts a headless browser)")
class TestRasterizeScaleToFit(unittest.TestCase):
    """`rasterize_svg`'s other arm: past the ceiling, shrink don't crop.

    NOT A MUTANT — nothing here is expected to fail and nothing pins a
    defect. What it pins is an arm no other test can reach. Task 20 gave
    `rasterize_svg` a window that follows the drawing up to
    `RASTER_MAX_W`/`_H` and a scale-to-fit branch beyond it, and both
    callers — `cmd_snapshot` and `apply --check --render` — size their
    markup with `render_svg`, which holds every drawing to that same
    ceiling. So no route through the product can make `fit < 1.0`, and
    the branch, the chromium 0.5 floor it clamps to, and the scale it
    reports in its detail line all shipped with zero coverage (the Task
    20 review's deferred item F4, discharged by Task 22).

    Unreachable is not dead: the helper takes a width from its CALLER and
    the docstring's promise is that it will not truncate one it did not
    choose. The next caller to hand it a number `render_svg` did not
    choose is the one that finds out, and this is what it will find out
    against. Called directly for exactly that reason — driving it through
    a CLI is impossible by construction here.
    """

    def setUp(self) -> None:
        """Make a scratch directory for this test's renders."""
        self.workdir = _mkworkdir()

    def tearDown(self) -> None:
        """Remove the scratch directory — renders never enter the repo."""
        shutil.rmtree(self.workdir, ignore_errors=True)

    def test_a_drawing_past_the_ceiling_comes_back_whole_and_smaller(self
                                                                    ) -> None:
        """9000x400 rasterizes to 4500x200 with its far edge still in it.

        9000 wide is past `RASTER_MAX_W` by a ratio of 0.444, under
        chromium's 0.5 floor on the device scale factor — so this also
        pins the CLAMP rather than only the scale: asking for 0.444 and
        getting 0.5 silently substituted is the one bound the arm cannot
        beat, and the file must come back at the scale that was really
        used or `validate_png` would reject a picture that is fine.

        The far-edge marker is what makes this about wholeness instead of
        arithmetic. A crop to 4500 CSS px would produce a PNG of exactly
        these dimensions with the right-hand half of the drawing missing,
        and the dimension assertion alone cannot tell those apart — which
        is precisely the failure Task 20 was about.
        """
        svg = ("<svg xmlns='http://www.w3.org/2000/svg' width='9000' "
               "height='400' viewBox='0 0 9000 400'>"
               "<rect x='0' y='0' width='9000' height='400' fill='%s'/>"
               "<rect x='8600' y='100' width='300' height='200' "
               "fill='#1e1e1e'/></svg>" % canvas.SVG_GROUND)
        out = Path(self.workdir) / "past-the-ceiling.png"
        ok, detail = canvas.rasterize_svg(svg, out, 9000, 400, "scalefit")
        self.assertTrue(ok, "the scale-to-fit arm produced no valid "
                            "raster: %s" % detail)
        w, h, pix = read_png_gray(out.read_bytes())
        self.assertEqual(
            (w, h), (4500, 200),
            "a 9000x400 drawing came back %dx%d: the arm is meant to "
            "clamp to chromium's 0.5 floor, so the file is the drawing "
            "halved — anything else is a crop or an unhonoured scale"
            % (w, h))
        self.assertIn(
            "scaled to 0.50x", detail,
            "the detail line does not report the scale that was used "
            "(%r): a reader told VALID=true about a picture silently "
            "shrunk has no way to know why it is small" % detail)
        # The marker occupies svg x 8600..8900 by y 100..300, so at 0.5x it
        # is PNG x 4300..4450 by y 50..150 — 15000 px, and the only ink in
        # the image (SVG_GROUND is luminance ~252, far above the 192
        # threshold). Pinned as a BAND rather than as "more than nothing":
        # a wrong-but-nonzero scale puts a marker of the wrong size at the
        # wrong offset and would leave some ink in this column while
        # failing the count.
        ink = sum(1 for y in range(h) for x in range(4300, 4450)
                  if pix[y * w + x] < 192)
        self.assertAlmostEqual(
            ink, 15000, delta=1500,
            msg="the far-edge marker covers %d px of this column where a "
                "300x200 rect at 0.50x covers 15000: the drawing was "
                "cropped to %dpx, or scaled by something other than the "
                "0.50x the detail line claims" % (ink, w))


# ---------------------------------------------------------------------------
# Render parity (Batch D item 1, 2026-08-13; backlog item 15, out of the
# visualize-skill mine's renderer-fidelity finding). The doctrine it comes
# from: any second render path needs equivalence pins against the primary.
# Ours are `render_svg`'s markup (tier 1) and the browser's raster of it
# (tier 2) — and the first thing to say about that pair is what it cannot do.
#
# Tier 2 rasterizes tier 1's OWN OUTPUT, so every defect where tier 1 omits or
# misstates content is inherited rather than caught. That limit is structural
# and permanent; the three examples this section was written around are not.
# `freedraw` and `image` reached neither path (test_mutants.
# TestExportCompleteness's two reds) and reordering an element moved neither
# path (TestPaintOrder's red) until v0.9 WP4 fixed all three in the dispatch.
# The lesson outlived them: a parity sweep reported perfect agreement on all
# three, and that agreement was worth nothing as independent evidence, because
# it ATTESTED a defect already pinned elsewhere. Which is why the successor
# test asserts PRESENCE in both paths rather than agreement between them —
# `test_no_class_agrees_by_being_absent_from_both`. A green row in a parity
# table reads as health whether or not it observed anything, so the way to
# keep this section honest is to make every row observe something.
#
# What the pair CAN answer is the question tier 1 settles alone and can get
# wrong: does the frame tier 1 chose actually contain the ink tier 1 emitted?
# Same markup, two viewports — the one `render_svg` computed and one
# deliberately generous. A correct frame makes the two rasters identical; a
# short frame loses ink off an edge, and the difference between them is that
# loss, counted in pixels.
#
# WHAT THIS FOUND, and v0.9 WP4 (Task 22) FIXED. `render_svg`'s bounds loop
# took the MAX side from `max(stored, text_dims(...))` — v0.3's fix for the
# "real glyph advance regularly overhangs" stored extents, whose comment sits
# right there. The MIN side never got the same treatment: it was the raw
# origin, `e.get("x", 0)`. For a `textAlign: center` text that is wrong by
# construction, because `paint` anchors such a text at `x + width/2` and runs
# its glyphs BOTH ways from there: when the drawn string is wider than the
# stored width, the leading glyphs were painted to the left of `x`, outside
# the viewBox, and the SVG viewport clipped them off. Half of v0.3's fix was
# missing, on the side nobody measured. The min side now takes the leftward
# run, floored at `x` so the change can only widen a frame.
#
# HOW THIS SECTION STAYS HONEST ONCE THE DEFECT IS FIXED, since it shapes
# everything below. A frame that does not contain its own ink is the only
# thing `parity_clipped` fires on, and that is a tier-1 defect by definition —
# so with tier 1 correct no DRAWING here makes the finding fire, and both
# scenes that ask the product question assert silence. Silence is not proof:
# a `parity_findings` that inverted its gate or returned `[]` unconditionally
# would leave this whole section green with the instrument blind, which is the
# same vacuity `test_no_class_agrees_by_being_absent_from_both` exists to
# prevent, and no automated rule catches it here (`parity_clipped` is
# `render: True` in the catalogue, so the `Silence` rule exempts it, and
# `TestRenderTierEvidence` only checks that the pointer resolves).
#
# So the instrument is asked for a frame the CALLER made too small, via
# `parity_findings`' `frame_pad` — the seam `_element_ink` already had, one
# level up. `test_the_clip_instrument_still_sees_ink_leave_a_short_frame`
# drives the real function to a real finding that way, and
# `TestRenderParityRegime` pins `_clipped_edges` ungated besides. Read those
# two before concluding a silent parity table means anything.
# ---------------------------------------------------------------------------

# Extra margin per side for the generous frame. It has to exceed the largest
# overhang any scene here produces, which since Task 22 fixed the bounds loop
# is none — so what it really guards is the day a scene overhangs again, and
# 200 leaves headroom for one without growing the raster enough to slow the
# diff down. Kept generous deliberately: a pad too small to clear the next
# overhang would report a partial magnitude, which reads as a small defect
# rather than as an instrument that could not see the whole of a large one.
PARITY_PAD = 200

_SVG_DIMS = re.compile(r"width='(\d+)' height='(\d+)' viewBox='"
                       r"(-?[\d.]+) (-?[\d.]+) (-?[\d.]+) (-?[\d.]+)'")

# The label the clip mutant hangs on, and the width `render_svg`'s own
# estimator gives it at fontSize 16. Written as a number rather than computed,
# for the reason SNAP_WIN_CAP is: a scene that derives its own regime from the
# code under test cannot notice when that code moves it out of the regime.
# `TestRenderParityRegime` is where the number is checked.
_WIDE_LABEL = "a considerably wider label"
_WIDE_LABEL_W = 192


def _reframe(svg: str, pad: int) -> tuple[str, int, int]:
    """Re-open the same markup in a viewport `pad` px larger on every side.

    Nothing inside the document moves: the viewBox origin shifts out by
    `pad` and the window grows by `2 * pad`, so every drawn coordinate
    lands where it did plus a constant offset, and ink the original frame
    cut off is simply inside this one. That is what makes the two rasters
    comparable as "what tier 1 framed" against "what tier 1 drew".

    Args:
        svg: A document from `_framed_svg`.
        pad: Extra margin per side, in viewBox units.

    Returns:
        `(svg, width, height)` for the widened frame.

    Raises:
        RuntimeError: If the `<svg>` tag cannot be parsed, or if
            `render_svg` scaled this scene (its uniform
            `RASTER_MAX_W`/`RASTER_MAX_H` clamp). Under a scale a viewBox
            unit is no longer a pixel, so the difference between the two
            rasters stops being a count of lost ink — better to refuse
            than to report a number that means nothing.
    """
    dims = _SVG_DIMS.search(_SVG_TAG.match(svg).group(0))
    if dims is None:
        raise RuntimeError("parity: cannot parse the <svg> tag %r"
                           % svg[:120])
    w, h = int(dims.group(1)), int(dims.group(2))
    vx, vy, vw, vh = (float(dims.group(i)) for i in range(3, 7))
    if (w, h) != (int(vw), int(vh)):
        raise RuntimeError("parity: render_svg scaled this scene to %dx%d "
                           "for a %gx%g viewBox — a viewBox unit is no "
                           "longer a pixel, so lost ink cannot be counted"
                           % (w, h, vw, vh))
    tag = ("<svg xmlns='http://www.w3.org/2000/svg' width='%d' height='%d' "
           "viewBox='%f %f %f %f'>"
           % (w + 2 * pad, h + 2 * pad, vx - pad, vy - pad, vw + 2 * pad,
              vh + 2 * pad))
    return tag + svg[_SVG_TAG.match(svg).end():], w + 2 * pad, h + 2 * pad


def _element_ink(elements: list[dict], eid: str, workdir: str, pad: int = 0,
                 min_blob: int = MIN_BLOB
                 ) -> tuple[int, tuple[int, ...] | None]:
    """How much ink one element contributes, in a frame of the given size.

    Ablation exactly as `ablation_findings` does it — the diff between the
    scene and the scene without `eid` is that element's own ink — but
    optionally measured in a viewport `pad` px bigger than the one tier 1
    chose, so ink outside tier 1's frame is counted rather than lost.

    Args:
        elements: The full scene.
        eid: The element to ablate.
        workdir: Directory renders are written into.
        pad: Extra margin per side; 0 means tier 1's own frame.
        min_blob: Smallest component worth counting. The default drops
            anti-aliasing speckle, which is right when the question is
            "how much of this drawing is here". It is WRONG when the
            element is itself only a few dozen pixels — a word set at 5px
            is speckle-sized, and filtering would eat the thing being
            measured — so the legibility section passes 1.

    Returns:
        `(area, bbox)` — the element's ink in pixels and the union
        bounding box of its blobs, or `(0, None)` if it drew nothing.
    """
    full, w, h = _framed_svg(elements)
    less = _framed_svg(elements, hide=(eid,))[0]
    if pad:
        full, w, h = _reframe(full, pad)
        less = _reframe(less, pad)[0]
    blobs = tolerant_diff(_rasterize(less, w, h, workdir),
                          _rasterize(full, w, h, workdir),
                          min_blob=min_blob)
    if not blobs:
        return 0, None
    return (sum(b["area"] for b in blobs),
            (min(b["bbox"][0] for b in blobs),
             min(b["bbox"][1] for b in blobs),
             max(b["bbox"][2] for b in blobs),
             max(b["bbox"][3] for b in blobs)))


def _clipped_edges(bbox: tuple[int, ...], pad: int, w: int, h: int) -> str:
    """Which sides of the frame under test the element's ink escapes.

    Pure arithmetic over two rectangles, which is why it is pinned
    ungated in `TestRenderParityRegime` while everything else in this
    section needs a browser.

    Args:
        bbox: The ink's bounding box, in the GENEROUS frame's pixels.
        pad: How far the tested frame's top-left sits inside the generous
            frame, in the same pixels. That is `PARITY_PAD` when the
            frame under test is the one tier 1 chose, and `PARITY_PAD -
            frame_pad` when the caller resized it — see
            `parity_findings`, which is where getting this wrong would
            misname the edges while leaving the magnitude right.
        w: The tested frame's raster width.
        h: The tested frame's raster height.

    Returns:
        The escaping edges joined by "+", in the fixed order top, left,
        right, bottom; or "" when the ink sits wholly inside the frame.
    """
    x0, y0, x1, y1 = bbox
    return "+".join(name for name, escaped in
                    (("top", y0 < pad), ("left", x0 < pad),
                     ("right", x1 >= pad + w), ("bottom", y1 >= pad + h))
                    if escaped)


def parity_findings(elements: list[dict], ids: Iterable[str],
                    workdir: str, frame_pad: int = 0) -> list[dict]:
    """Findings where tier 1's frame does not contain tier 1's own ink.

    Args:
        elements: The full scene.
        ids: Ids to measure, one at a time.
        workdir: Directory renders are written into.
        frame_pad: Resize the frame under test by this much per side,
            the same seam and the same sign convention `_element_ink`
            already has. 0 — the default, and the only value the product
            question is asked at — measures the frame `render_svg`
            actually chose. A NEGATIVE value measures a frame the caller
            has deliberately made too small, which is how this function
            can be asked to assemble a real finding while tier 1 is
            correct: after Task 22 no scene makes it fire on its own, and
            a finding proven only by its own silence is not proven. The
            generous reference frame is unaffected, so `PARITY_PAD` must
            stay larger than any `frame_pad` a caller shrinks by.

    Returns:
        Findings shaped like `ablation_findings` output. `parity_clipped`
        carries as its magnitude the count of pixels tier 1 emitted and
        the frame under test then cut away, and as its direction the
        edges they went off. At a non-zero `frame_pad` the `raw` sentence
        still says "the viewport render_svg chose" — true at the default
        and the only way this text is ever reported; a caller that
        shrank the frame itself knows it did.
    """
    framed_w, framed_h = _framed_svg(elements)[1:]
    # The frame under test, in the GENEROUS raster's pixels. `_reframe`
    # puts a frame's origin at `minx - pad`, so the tested frame's left
    # edge sits `PARITY_PAD - frame_pad` in from the generous origin —
    # NOT `PARITY_PAD`, which is only correct at the default. Getting
    # this wrong costs the direction and nothing else, which is exactly
    # the kind of error a silence-only proof cannot see: a shrunken
    # frame still yields the right magnitude while naming the wrong
    # edges.
    edge_pad = PARITY_PAD - frame_pad
    test_w, test_h = framed_w + 2 * frame_pad, framed_h + 2 * frame_pad
    findings: list[dict] = []
    for eid in ids:
        framed = _element_ink(elements, eid, workdir, pad=frame_pad)[0]
        whole, bbox = _element_ink(elements, eid, workdir, pad=PARITY_PAD)
        if bbox is None or whole <= framed:
            continue
        edges = _clipped_edges(bbox, edge_pad, test_w, test_h)
        findings.append({
            "check": "parity_clipped", "element": eid,
            "magnitude": float(whole - framed), "direction": edges,
            "raw": "%s draws %d ink pixels and only %d of them are inside "
                   "the viewport render_svg chose — %d px go off the %s "
                   "edge" % (eid, whole, framed, whole - framed,
                             edges or "?")})
    return findings


def _left_edge_label(stored_width: int) -> list[dict]:
    """A center-anchored label at the drawing's left edge, plus a node.

    The label sits at x=0 and the node at x=400, so the LABEL sets the
    drawing's minx and a clip, if there is one, lands on the left edge of
    the picture where it can be attributed to nothing else.
    `textAlign: center` is what makes the stored width load-bearing:
    `render_svg` anchors such a text at `x + width/2` and runs the glyphs
    both ways from there, so a stored width narrower than the drawn string
    pushes the leading glyphs left of `x` — and the bounds loop's min side
    only ever saw `x`.

    A stored width narrower than the drawn string is not a contrivance.
    `render_svg`'s own bounds comment calls stored text extents estimates
    that "regularly overhang", which is why the MAX side was patched in
    v0.3; and the two load-time repairs that would refit a label (ART-011)
    or re-center a detached one (ART-007) both skip arrow-type containers
    by design, so arrow labels in particular keep whatever width they were
    last written with — the same undefended dependency
    `stale_label_width_hides_collision` pins from the model tier.

    Args:
        stored_width: The label's `width`. `_WIDE_LABEL_W` is the honest
            value; anything less is an underestimate.

    Returns:
        The two-element scene: node `n1`, then label `t1`.
    """
    return [el(id="n1", type="rectangle", x=400, y=0, width=120, height=60,
               strokeColor="#1e1e1e", customData={"role": "node"}),
            el(id="t1", type="text", x=0, y=200, width=stored_width,
               height=20, text=_WIDE_LABEL, fontSize=16,
               textAlign="center", strokeColor="#1e1e1e")]


# How much smaller than tier 1's own frame the instrument probe is measured
# in. Big enough that the cut is far outside anti-aliasing and MIN_BLOB, small
# enough that the probe keeps about half its ink (121372 px whole, 61380 px
# tightened, measured 2026-08-14) — a cut that took all of it would read the
# same as an instrument that had gone blind.
_TIGHTEN = 100


def _short_frame_probe() -> list[dict]:
    """A big filled box and a spare, for measuring a frame against ink.

    Nothing here is a defect scene: `render_svg` frames this drawing
    correctly and the test tightens the frame itself. The box is filled
    rather than merely stroked so that a frame cutting into it removes
    SOME of its ink instead of all of it — an outline lies entirely on
    its own boundary, so a viewport pulled inside it loses every side but
    one and the measurement becomes about which strokes survived rather
    than about how much picture did.

    The fill is dark ON PURPOSE and is not a style choice. `tolerant_diff`
    counts a pixel as ink below luminance 192, so an ordinary pale
    Excalidraw fill is invisible to it and the probe would silently go
    back to measuring its own outline — which is how the first draft of
    this scene behaved, at 2772 px where the area is 120000.

    `n1` is the spare, and it is not decoration. `_element_ink` ablates
    by dropping the element and re-rendering; ablating `r1` out of a
    one-element scene leaves NO live elements, and `render_svg` answers
    that with its "(empty artifact)" placeholder — whose text would then
    show up in the diff as ink belonging to `r1`. Its position also sets
    the frame's right edge well clear of the box, so the tightening cuts
    the box on three sides rather than four and what survives is a window
    onto the middle of it rather than a ring of clipped strokes.

    Returns:
        The two-element scene: box `r1`, then spare `n1`.
    """
    return [el(id="r1", type="rectangle", x=0, y=0, width=400, height=300,
               strokeColor="#1e1e1e", backgroundColor="#6b7280"),
            el(id="n1", type="rectangle", x=600, y=0, width=120, height=60,
               strokeColor="#1e1e1e", customData={"role": "node"})]


@unittest.skipUnless(RENDER, "render tier: set MUTANTS_RENDER=1 "
                             "(starts a headless browser)")
class TestRenderParity(unittest.TestCase):
    """Do the two render paths agree about what they both claim to draw?"""

    def setUp(self) -> None:
        """Make a scratch directory for this test's renders."""
        self.workdir = _mkworkdir()

    def tearDown(self) -> None:
        """Remove the scratch directory — renders never enter the repo."""
        shutil.rmtree(self.workdir, ignore_errors=True)

    def test_shipped_classes_agree_on_what_they_render(self) -> None:
        """Markup in tier 1 and ink in tier 2 are the same answer, per class.

        The equivalence pin proper, asked of every class `ELEMENT_TYPES`
        ships and asked over the SAME scenes the model tier measures
        markup on (`test_mutants._one_of`), so a disagreement is about
        the two paths and not about two different drawings. What it
        catches is a paint branch that emits markup drawing nothing — a
        shape stroked in the ground color, a zero-extent box, an
        attribute the rasterizer ignores. Neither tier asks this alone:
        tier 1 counts tags and cannot tell whether they are visible, and
        tier 2's ablation checks run on hand-built scenes rather than
        across the class list.

        No row is vacuous, and that is a claim rather than an accident.
        Until v0.9 WP4 two of the nine were: `freedraw` and `image`
        reached neither path, so both sides of the assertion were False
        and those rows passed without observing anything. `assertEqual`
        cannot tell the difference between "both paths drew it" and
        "neither path drew it", so the sweep alone could never have
        noticed them going quiet. `test_no_class_agrees_by_being_absent_
        from_both` is what makes the difference checkable, and it is the
        reason this docstring can now say "no row is vacuous" instead of
        listing the exceptions.
        """
        for etype in sorted(canvas.ELEMENT_TYPES):
            with self.subTest(element_type=etype):
                scene = tm._one_of(etype)
                markup = bool(tm._export_delta(scene, "x-1"))
                ink = _element_ink(scene, "x-1", self.workdir)[0] > 0
                self.assertEqual(
                    markup, ink,
                    "%s emits markup=%s but leaves ink=%s: one render "
                    "path claims it and the other does not"
                    % (etype, markup, ink))

    def test_no_class_agrees_by_being_absent_from_both(self) -> None:
        """No class agrees with itself by being missing from both tiers.

        FLIPPED IN MEANING by v0.9 WP4, and the direction of the flip is
        the point. This was `test_dropped_classes_agree_by_being_absent_
        from_both`, which pinned `freedraw` and `image` as absent from
        tier 1 AND tier 2 and existed to say that the agreement was worth
        nothing: tier 2 rasterizes tier 1's output, so a class tier 1
        never paints cannot appear in tier 2 either, and the agreement
        was arithmetic rather than observation. It ATTESTED a defect
        pinned red elsewhere and added nothing to it.

        Both paint branches landed, so the same scenes now measure the
        opposite claim: every class `ELEMENT_TYPES` ships is PRESENT in
        both paths. Kept rather than deleted with the reds it attested,
        because the sweep above still cannot make this claim for itself —
        `assertEqual(markup, ink)` is satisfied by False == False, so a
        paint branch regressing to silence would turn a row vacuous and
        the sweep would go on printing agreement. This is the assertion
        that stays loud when that happens, and the tier-2 half is what
        makes it more than a restatement of `test_mutants._EXPORT_MARKUP`:
        markup that draws nothing would satisfy tier 1 alone.
        """
        for etype in sorted(canvas.ELEMENT_TYPES):
            with self.subTest(element_type=etype):
                self.assertNotEqual(
                    tm._export_delta(tm._one_of(etype), "x-1"), {},
                    "%s emits no markup at all: tier 1 has stopped "
                    "painting a class it ships, and the equivalence "
                    "sweep's %s row has gone vacuous" % (etype, etype))
                self.assertGreater(
                    _element_ink(tm._one_of(etype), "x-1", self.workdir)[0],
                    0,
                    "%s emits markup that rasterizes to nothing: tier 1 "
                    "claims the class and tier 2 cannot see it" % etype)

    def test_the_clip_instrument_still_sees_ink_leave_a_short_frame(self
                                                                    ) -> None:
        """Ink outside the frame is still measured as ink outside the frame.

        REPLACES `test_parity_clip_is_red_by_measurement_not_by_error`,
        which Task 22 could not keep. That test held the flipped mutant's
        scene against the OPPOSITE claim — `parity_clipped` fires on
        `_left_edge_label(20)`, direction `left`, magnitude 170 — and the
        fix makes every word of it false. It had two jobs and only one of
        them survives the product being correct. The first was that
        `@unittest.expectedFailure` swallows ERRORS as well as failures
        (skill doctrine §6), so a `_reframe` that began raising would have
        printed a healthy `x` with nothing measured; the flip discharges
        that outright, because the mutant is an ordinary test now and an
        exception in it is a loud error. The second is this one.

        The second job: `parity_clipped` must not be a check that only
        ever proves silences. Both remaining scenes — the flipped mutant
        and its honest-width neighbour — now assert NO finding, and an
        instrument that had gone blind would satisfy both while reporting
        health forever (`test_mutants.TestCoverage.test_silence_only_
        mutant_does_not_prove_its_check` is the same rule one tier up).

        What it cost to keep that job honestly. `parity_clipped` fires
        only on a frame that does not contain its own ink, which is the
        definition of a tier-1 defect — so once tier 1 is right, no
        DRAWING makes the finding fire, and a fire-proof that waits for
        one is only available while the product is broken. Two ways of
        getting a fire anyway were refused: borrowing an unfixed defect
        from elsewhere in `render_svg` would tie this test to a bug
        someone else is expected to fix, which is the attestation trap
        this section's header warns about; and hand-rolling the frame
        arithmetic in a helper would prove the copy rather than the
        instrument.

        The third way is the one taken, and it is the technique
        `_element_ink` already ships: ask the real function for a frame
        the CALLER made too small. `parity_findings` takes a `frame_pad`
        with the same sign convention, so at `-_TIGHTEN` it assembles a
        real finding — real gate, real magnitude, real attribution, real
        `check` name — against a correct product and without borrowing
        anyone's bug. The drawing is right; only the frame it is measured
        against is not. So the assembly IS proven to fire, and what
        follows asserts it rather than asserting a silence.

        Its parts are pinned either side of that: the two-frame ink
        measurement below, and `_clipped_edges` in
        `TestRenderParityRegime`, which is ungated and so guards the
        attribution in every commit rather than only under
        `MUTANTS_RENDER=1`.
        """
        scene = _short_frame_probe()
        whole = _element_ink(scene, "r1", self.workdir, pad=PARITY_PAD)[0]
        framed = _element_ink(scene, "r1", self.workdir)[0]
        tight = _element_ink(scene, "r1", self.workdir, pad=-_TIGHTEN)[0]
        self.assertEqual(framed, whole,
                         "the honest frame already loses ink off this "
                         "probe: %d of %d px — the scene has drifted out "
                         "of its regime and the tightened measurement "
                         "below no longer isolates the frame"
                         % (whole - framed, whole))
        # The probe is a 400x300 filled box; tightening by _TIGHTEN leaves
        # a 340x180 window onto it. Both bounds are load-bearing: an
        # instrument that lost the whole element would satisfy `less than
        # whole`, and one that ignored the frame entirely would satisfy
        # `more than nothing`.
        self.assertLess(tight, whole,
                        "a frame %dpx short on every side lost no ink at "
                        "all (%d px both ways): the two-frame comparison "
                        "parity_clipped is built on has stopped seeing "
                        "the frame" % (_TIGHTEN, whole))
        self.assertGreater(tight, 0,
                           "the tightened frame lost the element entirely "
                           "rather than part of it: %d px became 0, so "
                           "this measures a blind instrument and a clipped "
                           "one identically" % whole)
        # The whole finding, assembled by the real function. The direction
        # is read off the DRAWING rather than off `_clipped_edges`, so it
        # is an independent claim: the probe's box spans 0..400 x 0..300,
        # and tightening tier 1's -40..760 x -40..340 frame by 100 leaves
        # 60..660 x 60..240 — which cuts the box's left (0 < 60), top
        # (0 < 60) and bottom (300 > 240) while its right edge at 400
        # stays well inside 660. Three sides, named in the order
        # `_clipped_edges` reports them.
        finds = parity_findings(scene, ["r1"], self.workdir,
                                frame_pad=-_TIGHTEN)
        self.assertEqual(
            [(f["check"], f["element"], f["direction"]) for f in finds],
            [("parity_clipped", "r1", "top+left+bottom")],
            "parity_findings did not assemble the finding its own parts "
            "measure: %s" % finds)
        self.assertEqual(
            finds[0]["magnitude"], float(whole - tight),
            "the magnitude is not the ink the tightened frame lost "
            "(%d - %d = %d): the gate and the subtraction disagree with "
            "the two measurements above"
            % (whole, tight, whole - tight))
        # And the same call at the DEFAULT pad stays silent, so the fire
        # above is the manufactured frame talking and not a probe that
        # was mis-framed all along.
        self.assertEqual(parity_findings(scene, ["r1"], self.workdir), [],
                         "the probe is clipped by the frame render_svg "
                         "chose for it: it is a defect scene, and the "
                         "finding above proves nothing about the seam")

    def test_mutant_center_anchored_label_is_clipped_off_the_frame(self
                                                                   ) -> None:
        """FLIPPED by v0.9 WP4 (Task 22). Kept its red-era name.

        A label wider than its stored width used to lose its head off the
        frame. The picture then asserted something the model never said:
        the drawing read "considerably wider label" where the model held
        "a considerably wider label", and the reader had no way to be
        suspicious of glyphs that leave no mark. Absence is the one
        defect a reader cannot notice, and this export is the agent's own
        view of its own drawing.

        The bug was half of v0.3's fix. That assessment established that
        stored text extents are estimates the real glyph advance
        regularly overhangs, and patched the bounds loop's MAX side to
        take `max(stored, text_dims(...))`; the MIN side stayed the raw
        `x`, so the identical overhang going LEFT was never bounded. It
        only becomes visible under `textAlign: center`, because that is
        the anchoring where an underestimated width moves ink to the left
        of `x` instead of merely leaving slack to its right. Task 22 gave
        the min side the same treatment, as `min(x, x + width/2 -
        text_dims(...)/2)` — the leftward run of the centered anchor,
        floored at `x` so an honest or generous stored width leaves the
        bound where it was and no correctly-framed drawing has its frame
        pulled in.

        Two things that were rejected as fixes, and the red carried both
        as traps because each would have turned this test green while
        leaving the defect. Widening the fixed 40px pad only moves a
        threshold — this scene clears it by 46px — and a threshold moved
        is a threshold the next label crosses; the frame would go on
        losing whatever exceeded the new number, silently, which is the
        failure mode rather than the magnitude. Clamping the paint anchor
        to `x` would have flipped this by MOVING every centered label in
        every drawing: the bounds loop was what was wrong about the
        geometry, not the geometry.

        The corpus says how live this was and how quietly. 39 of the 388
        centered labels in `tests/fixtures` store a width narrower than
        their own string, overhanging by up to 30.5px per side — yet the
        fix moves no fixture's markup at all, because in every one of the
        39 some other element sets the drawing's minimum x and the 40px
        pad swallows the run. The closest is 9.5px from having mattered.
        That is why this needed a scene built to put the label at the
        left edge: the defect was never rare, only never load-bearing
        until the wrong label was the leftmost thing drawn.
        """
        finds = parity_findings(_left_edge_label(20), ["t1"], self.workdir)
        self.assertEqual(
            finds, [],
            "the label's leading glyphs are painted outside the viewBox "
            "render_svg computed for its own markup: %s"
            % [f["raw"] for f in finds])

    def test_neighbour_honest_label_width_is_framed_whole(self) -> None:
        """With the stored width honest, tier 1's frame holds all its ink.

        The other pole, and the control that keeps the red above
        meaningful: same label, same anchoring, same position, and the
        single variable is whether the stored width matches the string.
        Without it the red would be satisfied by a renderer that framed
        nothing correctly, or by an instrument that reported a clip on
        every scene it was shown.
        """
        scene = _left_edge_label(_WIDE_LABEL_W)
        self.assertEqual(parity_findings(scene, ["t1"], self.workdir), [])
        # A label that drew nothing at all would satisfy that too, so pin
        # that there was ink to lose: silence has to mean "framed whole",
        # never "absent from both frames".
        self.assertGreater(_element_ink(scene, "t1", self.workdir)[0], 0)


class TestRenderParityRegime(unittest.TestCase):
    """The parity scene must keep measuring what it was built to measure.

    Ungated for the reason `TestSnapshotFramingRegime` is ungated: it
    needs no browser, and the rot it catches — a font-metric or bounds
    change quietly taking the scene out of its regime, so the mutant
    measures nothing and still reports green — happens in ordinary
    editing, where nobody has `MUTANTS_RENDER=1` set. Since Task 22
    flipped that mutant this class carries a second job as well, and the
    reason it lands here is the same one: `_clipped_edges` is the only
    part of the clip instrument that can be checked without a browser,
    and it is now the only place the instrument is asked to report a
    clip rather than the absence of one.
    """

    def test_the_wide_label_still_overhangs_the_frame_on_the_left(self
                                                                 ) -> None:
        """`_WIDE_LABEL` drawn at 16px still runs left of `x` minus the pad.

        Both halves are load-bearing, and what they guard SURVIVED the
        Task 22 flip with its sense inverted. While the mutant was red
        this said "the label still reaches past the pad, so there is a
        clip left to pin". Green, the leftward run is still exactly the
        regime — the mutant's claim is that a label overhanging this far
        is framed ANYWAY, and a scene whose label stopped overhanging
        would satisfy that claim while observing nothing. If `text_dims`
        stops returning 192 the arithmetic below is about a different
        string; if the drawn left edge climbs back inside `x - SVG_PAD`
        the bounds loop's min side is no longer being asked for anything.
        """
        self.assertEqual(canvas.text_dims(_WIDE_LABEL, 16),
                         (_WIDE_LABEL_W, 20),
                         "the font metrics moved: re-measure _WIDE_LABEL_W "
                         "— the drawn-left arithmetic below and in "
                         "test_the_bounds_loop_frames_the_centered_label_"
                         "it_used_to_clip both derive from it")
        # `paint` centers the text on x + width/2 = 10 and runs the glyphs
        # 96px each way, so the drawn left edge is at -86, where a raw-`x`
        # frame would have started at minx = x - SVG_PAD = -40.
        drawn_left = 20 / 2 - _WIDE_LABEL_W / 2
        self.assertLess(drawn_left, -SVG_PAD,
                        "the centered label no longer reaches past the "
                        "%dpx pad (left edge %g): the clip mutant is inside "
                        "the frame for a reason that has nothing to do with "
                        "the fix it pins" % (SVG_PAD, drawn_left))

    def test_the_bounds_loop_frames_the_centered_label_it_used_to_clip(self
                                                                      ) -> None:
        """The fix, read off the markup rather than off the pixels.

        The mutant next door proves this in ink and needs a browser to do
        it; this proves it in arithmetic and runs in every commit. It is
        the same claim from the other side — the viewBox origin sits at
        or left of the label's drawn left edge — and it is the assertion
        that stays loud if the min-side widening is reverted while
        `MUTANTS_RENDER` is unset, which is how the defect would come
        back unnoticed.
        """
        svg = canvas.render_svg(_left_edge_label(20))[0]
        dims = _SVG_DIMS.search(svg)
        self.assertIsNotNone(dims, "cannot parse the <svg> tag")
        minx = float(dims.group(3))
        drawn_left = 20 / 2 - _WIDE_LABEL_W / 2
        self.assertLessEqual(
            minx, drawn_left,
            "the viewBox starts at %g but the centered label's glyphs "
            "start at %g: render_svg is framing its own markup short on "
            "the left again" % (minx, drawn_left))

    def test_the_honest_label_keeps_the_frame_the_raw_origin_gave_it(self
                                                                    ) -> None:
        """The widening is a widening: an honest width moves no frame.

        The control for the test above, in the same arithmetic. The min
        side is floored at `x`, so a stored width that matches or exceeds
        the drawn string leaves the origin exactly where the raw-origin
        loop put it — `x - SVG_PAD`. Without this a fix that framed every
        centered label by its glyph run would pass the test above while
        shifting the viewBox of every drawing in the corpus.
        """
        for width, label in ((_WIDE_LABEL_W, "honest"), (400, "generous")):
            with self.subTest(stored_width=label):
                svg = canvas.render_svg(_left_edge_label(width))[0]
                minx = float(_SVG_DIMS.search(svg).group(3))
                self.assertEqual(minx, float(-SVG_PAD),
                                 "a %s stored width moved the viewBox "
                                 "origin to %g: the min side is no longer "
                                 "floored at x" % (label, minx))

    def test_the_edge_attribution_still_names_the_side_ink_escapes(self
                                                                   ) -> None:
        """`_clipped_edges` reports each side, and "" only when contained.

        The half of the clip instrument that needs no browser, and the
        reason it is pinned at all is the Task 22 flip. `parity_clipped`
        now has no scene in this file that makes it emit a finding — the
        mutant and its honest-width neighbour are both SILENCES, because
        the finding fires only on a frame short of its own ink and tier 1
        no longer produces one. An `_clipped_edges` that returned "" for
        everything would leave both of them green with the instrument
        blind, and a check proven only by silences is not proven
        (`test_mutants.TestCoverage.test_silence_only_mutant_does_not_
        prove_its_check` is the same rule one tier up).

        The frame here is 100x50 inset 10px into a generous raster, so
        the contained bbox and each escaping one differ by a single pixel
        on a single side — the boundary itself, which is where an
        off-by-one in the comparison lives.
        """
        pad, w, h = 10, 100, 50
        inside = (pad, pad, pad + w - 1, pad + h - 1)
        self.assertEqual(_clipped_edges(inside, pad, w, h), "")
        for name, bbox in (("top", (pad, pad - 1, pad + w - 1, pad + h - 1)),
                           ("left", (pad - 1, pad, pad + w - 1, pad + h - 1)),
                           ("right", (pad, pad, pad + w, pad + h - 1)),
                           ("bottom", (pad, pad, pad + w - 1, pad + h))):
            with self.subTest(edge=name):
                self.assertEqual(_clipped_edges(bbox, pad, w, h), name)
        self.assertEqual(
            _clipped_edges((pad - 1, pad - 1, pad + w, pad + h), pad, w, h),
            "top+left+right+bottom",
            "ink escaping on every side must name every side: a direction "
            "that reports one of them is a finding the reader will chase "
            "to the wrong edge")


# ---------------------------------------------------------------------------
# The min_font floor, CALIBRATED (Batch D item 3, 2026-08-13; backlog item 17,
# design doc docs/todo/contrast-and-min-font-lints.md). That doc asks for a
# fontSize floor "calibrated against the render tier's rasterization ... at
# deviceScaleFactor 1 — measure, don't guess", and the C5 wave deliberately
# left it unmeasured: `tiny_font_text` pins fontSize 6 with a comment saying
# the floor is not encoded because guessing it would fix the wrong number.
# This is that measurement.
#
# NOT MUTANTS. Nothing below pins a defect in our code and nothing below is
# expected to fail — small type renders badly because of how rasterizers work,
# not because `render_svg` is wrong about it. What these are is the evidence
# behind a number, held in the place the number can be re-measured: the day
# someone writes the `min_font` lint, its threshold has to answer to these two
# poles rather than to taste. They are the same kind of thing as
# `TestSnapshotFramingRegime` — a guard on a constant, not a judgement on a
# drawing.
#
# The analogy stops at one place, and it is worth naming because it invites an
# expectation this section cannot meet. `TestSnapshotFramingRegime` is
# UNGATED: its regime is arithmetic over stored geometry, so it guards its
# mutant in every commit. Here that is impossible. What these poles measure is
# what a rasterizer does to a glyph, and only pixels can see that — so the
# guard runs under `MUTANTS_RENDER=1` or not at all, and on an ordinary commit
# nothing checks that the floor still holds. `TestLegibilityFloorRegime` below
# IS ungated, but it guards only the ARITHMETIC (our WCAG implementation
# against the catalogue's pinned ratios); it cannot notice the rasterizer
# moving under the constant. That gap does not close at this tier: treat
# `MIN_FONT_FLOOR` as a number to re-measure when the render stack changes,
# not as one the suite defends for you.
#
# THE MEASUREMENT (2026-08-13, headless chromium, the pinned CHROME_FLAGS,
# deviceScaleFactor 1, text #1e1e1e on SVG_GROUND, declared contrast 16.24:1).
# Sweeping `_styled_scene`'s free text "status" from 20px down and reading the
# darkest pixel the word achieves anywhere, its ink height, and its ink count:
#
#     fontSize  20     16     14     13     12     11     10
#     contrast  16.24  16.24  16.24  16.24  16.24  15.50  14.74
#     height    14     11     10      9      9      8      8
#     ink      308    190    162    130    115    107     96
#
#     fontSize   9      8      7   |   6      5      4
#     contrast  11.25   8.91   8.50 |  4.62   4.12   3.36
#     height     6      5      5   |   3      3      2
#     ink       72     58     52   |  40     34     18
#
# Contrast holds at the declared 16.24:1 down to 12px and decays gently to
# 8.50:1 at 7px. Between 7 and 6 it nearly halves — 8.50 to 4.62, the largest
# single step anywhere in the sweep — while the ink height drops 5px to 3px
# and the letters of "status" stop separating at all (5 inter-glyph gaps at
# 12px, 2 at 7px, 0 at 6px). A second, longer, mixed-case word ("Reconcile
# nightly") puts its cliff in the same place: 7.73:1 at 7px, 5.04:1 at 6px.
# Below 6 it degrades monotonically to a 2px-tall 3.36:1 smear at 4px.
#
# So the floor is 7: the smallest size whose rendered ink keeps BOTH a stroke
# that clears WCAG 1.4.3's 4.5:1 with real margin and enough height to hold a
# letterform. This says nothing about whether 7px is a good size to write at
# — it is the size below which the picture stops carrying the text at all,
# which is the only thing pixels can settle.
#
# STABILITY, and the part of it that bites. Across three scanline phases (the
# text moved to y=160, 163 and 167) every column above is identical, so these
# are properties of the rasterizer under the pinned flags rather than of where
# the text happened to land.
#
# Across BROWSER BUILDS the picture is more interesting, and it decided how
# the tests below are written. Swept on all three chromium builds this machine
# offers — Chrome for Testing 151.0.7922.34 (what `find_browsers()` returns
# first, and so the source of the table above), snap Chromium 150.0.7871.128,
# and playwright's headless_shell 131.0.6778.33, a 20-major-version spread:
#
#   - INK HEIGHT is identical on all three at every size. 11/9/8/6/5/5/3/3/2.
#   - CONTRAST is not, and it diverges exactly where it matters. 151 and 150
#     agree to the decimal; 131 reads HIGHER at the small end — 5.51:1 at 6px
#     where the others read 4.62:1, and 5.27 against 4.12 at 5px.
#
# That 6px divergence straddles WCAG's 4.5 floor: the same word, the same
# markup, the same flags, failing on one build and passing on another. So an
# absolute contrast bound below the floor would be a pin on which chromium the
# tier happened to find, and would have gone red on a machine where
# headless_shell sorted first. The floor itself is unmoved — it computes to 7
# on all three builds — because it rests on the STEP between 7px and 6px, and
# the step survives the divergence (6px is 0.54 of 7px on 151/150 and 0.63 on
# 131, against 1.0 for no step at all). Hence: the tests assert ink height and
# the step strictly, and keep the absolute WCAG anchor on the passing pole,
# where all three builds read 8.5:1 or better. Put the strict assertion where
# the measurement is stable.
#
# (The parity and ablation pins elsewhere in this file were swept the same way
# and are build-robust as written: the clip magnitude reads 170/172/170 px
# against a +-17 band, direction `left` on all three.)
#
# One instrument note worth keeping: the ablation that locates the word must
# run with `min_blob=1`. At these sizes the whole word is speckle-sized, and
# the default filter silently ate a third of the 7px reading — reporting
# 6.62:1 where the word really reaches 8.50:1, which would have moved the
# floor by measuring the instrument instead of the picture.
# ---------------------------------------------------------------------------

# The measured floor. `tiny_font_text` (model tier) pins fontSize 6, one below
# this, which the measurement above now makes a boundary-honest choice rather
# than a plausible-looking one.
MIN_FONT_FLOOR = 7

# Matches `pngdiff.tolerant_diff`'s default: below this, a pixel is ink.
INK_THRESHOLD = 192

# WCAG 1.4.3's floor for body text, and the paper every scene here is drawn
# on. The model tier already works to both — the three legibility mutants'
# ratios (1.50, 2.11, control 16.24) were computed against this same ground.
WCAG_TEXT_FLOOR = 4.5
_GROUND_RGB = (0xFD, 0xFC, 0xF8)


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    """WCAG relative luminance of an 8-bit sRGB triple.

    Implemented here rather than imported from `canvas.py` on purpose,
    and it must stay that way when the contrast lint lands: the harness
    checks the product, so sharing the product's arithmetic would make
    every ratio agree with itself by construction. The check that this
    implementation is right is that it reproduces the model tier's
    already-pinned ratios exactly — see
    `TestLegibilityFloorRegime.test_the_contrast_arithmetic_matches_the
    _catalogue`.

    Args:
        rgb: Channel values 0-255.

    Returns:
        Luminance in 0.0-1.0.
    """
    chans = []
    for ch in rgb:
        c = ch / 255.0
        chans.append(c / 12.92 if c <= 0.03928
                     else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * chans[0] + 0.7152 * chans[1] + 0.0722 * chans[2]


def contrast_ratio(a: tuple[int, int, int],
                   b: tuple[int, int, int]) -> float:
    """WCAG contrast ratio between two 8-bit sRGB colors.

    Args:
        a: One color's channels, 0-255.
        b: The other's.

    Returns:
        The ratio, 1.0 (identical) to 21.0 (black on white). Order does
        not matter.
    """
    la, lb = _relative_luminance(a), _relative_luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def rendered_text(elements: list[dict], eid: str,
                  workdir: str) -> dict[str, float]:
    """What one text element actually looks like once it is rasterized.

    Located by ablation — the diff between the scene and the scene
    without `eid` is that element's own ink — and then MEASURED in the
    full raster inside that region, so anti-aliased grays are read at
    their real values rather than at whatever the diff made of them.

    The contrast reported is of the DARKEST pixel in the word, which is
    the most generous reading available: if even the best pixel fails a
    threshold, every pixel does. A floor argued from this number is
    therefore conservative on the picture's side.

    Args:
        elements: The full scene.
        eid: The text element to measure.
        workdir: Directory renders are written into.

    Returns:
        `{"ink", "height", "contrast"}` — ink pixels, the ink's height in
        pixels, and the darkest pixel's WCAG ratio against `SVG_GROUND`.
        An element that drew nothing reports zeros and a ratio of 1.0.
    """
    bbox = _element_ink(elements, eid, workdir, min_blob=1)[1]
    if bbox is None:
        return {"ink": 0, "height": 0, "contrast": 1.0}
    w, _h, pix = read_png_gray(_shot(elements, workdir))
    x0, y0, x1, y1 = bbox
    marks = [(y, pix[y * w + x]) for y in range(y0, y1 + 1)
             for x in range(x0, x1 + 1) if pix[y * w + x] < INK_THRESHOLD]
    if not marks:
        return {"ink": 0, "height": 0, "contrast": 1.0}
    rows = [m[0] for m in marks]
    darkest = min(m[1] for m in marks)
    return {"ink": len(marks), "height": max(rows) - min(rows) + 1,
            "contrast": contrast_ratio((darkest,) * 3, _GROUND_RGB)}


@unittest.skipUnless(RENDER, "render tier: set MUTANTS_RENDER=1 "
                             "(starts a headless browser)")
class TestLegibilityFloor(unittest.TestCase):
    """The two poles the measured `min_font` floor sits between."""

    def setUp(self) -> None:
        """Make a scratch directory for this test's renders."""
        self.workdir = _mkworkdir()

    def tearDown(self) -> None:
        """Remove the scratch directory — renders never enter the repo."""
        shutil.rmtree(self.workdir, ignore_errors=True)

    def test_text_at_the_floor_still_carries_its_ink(self) -> None:
        """At `MIN_FONT_FLOOR` the word survives rasterization.

        The upper pole. Without it the lower pole proves only that this
        instrument can call something illegible, which any instrument
        that called everything illegible would also manage.
        """
        got = rendered_text(tm._styled_scene(font_size=MIN_FONT_FLOOR),
                            "t1", self.workdir)
        self.assertGreater(
            got["contrast"], WCAG_TEXT_FLOOR,
            "text at the floor no longer clears WCAG 1.4.3 (%.2f:1): "
            "re-run the sweep in this section's header and move "
            "MIN_FONT_FLOOR" % got["contrast"])
        self.assertGreaterEqual(got["height"], 5)

    def test_text_below_the_floor_degenerates_in_the_picture(self) -> None:
        """One px under the floor, the same word is a gray smear.

        The scene is exactly the one `test_mutants.tiny_font_text` pins
        from the model tier — `_styled_scene(font_size=6)` — so this is
        the evidence for that mutant's choice of 6 rather than a second
        opinion about a different drawing. Its declared color is
        #1e1e1e, which the model tier scores at 16.24:1; rasterized at
        6px, no pixel in the word gets within a whisker of that, and the
        text a contrast lint reading DECLARED colors would wave through
        is one the picture cannot deliver.

        Green, and not a mutant: nothing here is our bug to fix. It is
        the floor's evidence, kept where it can be re-measured.

        ASSERTED AS A STEP, not as an absolute ratio, and the
        cross-build sweep in this section's header is why. Ink height is
        identical on every build tested; the small-size contrast reading
        is NOT — Chromium 131 renders this same 6px word at 5.51:1 where
        151 renders it at 4.62:1, which is the difference between
        failing WCAG's 4.5 and passing it. An absolute bound here would
        therefore pass or fail on which chromium the tier happened to
        find, and a calibration that moves with the binary calibrates
        nothing. The cliff between the two poles survives that: 6px
        measures 0.54 of 7px's contrast on one build and 0.63 on
        another, nowhere near the 1.0 that would mean no step at all.
        The absolute WCAG anchor is kept on the pole where it is stable
        — the passing one, where every build reads 8.5:1 or better.
        """
        below = rendered_text(tm._styled_scene(font_size=MIN_FONT_FLOOR - 1),
                              "t1", self.workdir)
        at_floor = rendered_text(tm._styled_scene(font_size=MIN_FONT_FLOOR),
                                 "t1", self.workdir)
        self.assertLessEqual(
            below["height"], 3,
            "text below the floor now stands %d px tall — this reading "
            "was identical on every browser build swept, so a change "
            "here is the font or the renderer, not the binary; re-run "
            "the sweep in this section's header before trusting "
            "MIN_FONT_FLOOR" % below["height"])
        step = below["contrast"] / at_floor["contrast"]
        self.assertLess(
            step, 0.70,
            "the contrast cliff under the floor has gone: %.2f:1 at %dpx "
            "against %.2f:1 at %dpx is %.0f%% of it, where every build "
            "swept read 63%% or less. The floor rests on that step — "
            "re-measure before trusting it"
            % (below["contrast"], MIN_FONT_FLOOR - 1, at_floor["contrast"],
               MIN_FONT_FLOOR, step * 100))


class TestLegibilityFloorRegime(unittest.TestCase):
    """The calibration's arithmetic, checked without a browser.

    Ungated, like the other two regime classes: an implementation of the
    WCAG formulas that quietly went wrong would move the measured floor
    while every gated test went on passing, and nobody runs the gated
    tier during ordinary editing.
    """

    def test_the_contrast_arithmetic_matches_the_catalogue(self) -> None:
        """Our ratios reproduce the ones the model tier already pins.

        `gray_text_on_ground` and `pale_stroke_node` carry MEASURED WCAG
        ratios computed independently when they were written. Landing on
        the same three numbers from this implementation is what makes it
        trustworthy enough to argue a font floor from; disagreeing would
        mean one of the two is wrong and the floor rests on nothing.
        """
        for hexcolor, want in (("#1e1e1e", 16.24), ("#d0d0d0", 1.50),
                               ("#b0b0b0", 2.11)):
            with self.subTest(color=hexcolor):
                rgb = tuple(int(hexcolor[i:i + 2], 16)
                            for i in (1, 3, 5))
                self.assertAlmostEqual(contrast_ratio(rgb, _GROUND_RGB),
                                       want, places=2)


class TestRenderTierEvidence(unittest.TestCase):
    """The coverage table's render-tier evidence must name real tests.

    Deliberately NOT gated: resolving a dotted name needs no browser, and
    the rot this catches — renaming a test here and leaving `--coverage`
    pointing at a ghost — happens in ordinary editing, where nobody has
    `MUTANTS_RENDER=1` set. A gated check would notice months late.
    """

    def test_render_tier_evidence_resolves_to_real_tests(self) -> None:
        """Every RENDER_TIER evidence string walks to a callable test."""
        for check, dotted in tm.RENDER_TIER.items():
            modname, *path = dotted.split(".")
            obj: Any = importlib.import_module(modname)
            for part in path:
                obj = getattr(obj, part, None)
                self.assertIsNotNone(
                    obj, "RENDER_TIER[%r] names %s, but %r does not exist"
                         % (check, dotted, part))
            self.assertTrue(callable(obj), "%s is not callable" % dotted)


@unittest.skipUnless(RENDER, "render tier: set MUTANTS_RENDER=1 "
                             "(starts a headless browser)")
class TestRenderTierAborts(unittest.TestCase):
    """Spec §7: a render that was asked for and cannot happen is a failure."""

    def test_missing_browser_raises_instead_of_skipping(self) -> None:
        """No browser with the tier requested raises, naming the search."""
        with mock.patch.object(canvas, "find_browsers", lambda: []), \
                self.assertRaises(RuntimeError) as caught:
            _browser()
        self.assertIn("no chromium", str(caught.exception))
        self.assertIn("ms-playwright", str(caught.exception))
