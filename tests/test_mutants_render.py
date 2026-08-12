"""Render tier: mutants measured in pixels, by ablation, under a browser.

Every other tier reads the model. This one reads the picture: rasterize the
scene's tier-1 SVG, rasterize it again with one element omitted, and diff.
The delta IS that element's contribution to the drawing, so two questions
the model cannot answer become arithmetic — an element whose ablation
changes no pixels is not in the picture (`ablation_existence`), and a
connector whose delta splits into two separated components is severed
somewhere along its run (`ablation_continuity`), which is the r5-14 class:
an opaque label backdrop erasing the stroke it sits on.

Ablation is by OMISSION, not by styling: the hidden element is absent from
the SVG entirely, so nothing about how the renderer treats `opacity` or
`display` can make a hidden element leave ghost ink behind.

The whole module is gated behind `MUTANTS_RENDER=1` because it starts a
headless browser. Gated OFF it skips; gated ON with no browser to be found
it RAISES (spec §7) — an environmental failure must abort with a named
reason, never quietly mark the mutants green.
"""
from __future__ import annotations

import hashlib
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


def _shot(elements: list[dict], workdir: str,
          hide: Iterable[str] = ()) -> bytes:
    """Rasterize a scene's tier-1 SVG, omitting the `hide` elements.

    The ablated shot is framed in the FULL scene's viewport, not its own:
    `canvas.render_svg` derives width/height/viewBox from the elements it
    is given, so omitting one would otherwise resize and shift the whole
    picture and make the two rasters incomparable (`tolerant_diff` rejects
    a size mismatch outright). Swapping in the full scene's `<svg>` tag
    pins both shots to the same pixel grid. The ground rect underneath is
    left at the ablated scene's own bounds on purpose — it is painted in
    `SVG_GROUND`, which is far above the ink threshold, so where it does
    and doesn't reach is invisible to the diff.

    Renders are cached by SVG digest inside `workdir`, so a scene shot
    twice in one test class costs one browser start.

    Args:
        elements: The full scene.
        workdir: Directory to write the HTML and PNG into.
        hide: Ids to ablate — dropped from the element list entirely.

    Returns:
        The screenshot's PNG bytes.

    Raises:
        RuntimeError: If the browser exited without writing a PNG.
    """
    full, w, h = canvas.render_svg(elements)
    if hide:
        hidden = set(hide)
        kept = [e for e in elements if e.get("id") not in hidden]
        svg, _kw, _kh = canvas.render_svg(kept)
        svg = _SVG_TAG.match(full).group(0) + svg[_SVG_TAG.match(svg).end():]
    else:
        svg = full
    name = hashlib.sha1(svg.encode("utf-8")).hexdigest()[:16]
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


def ablation_findings(elements: list[dict], arrow_ids: Iterable[str],
                      workdir: str) -> list[dict]:
    """Findings from ablating each named element out of the picture.

    One shot of the full scene, then one shot per id with that id omitted;
    the diff between them is that element's ink. No ink at all means the
    element is not in the picture; ink in two or more separated pieces
    means whatever is drawn over it has cut it in half.

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
        `ablation_continuity` carries the component count.
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
        parts = _delta_components(w, h, blobs)
        if len(parts) >= 2:
            findings.append({
                "check": "ablation_continuity", "element": eid,
                "magnitude": float(len(parts)), "direction": None,
                "raw": "%s's ink comes apart in %d separated pieces %s — "
                       "something drawn over it severs the run"
                       % (eid, len(parts), [c["bbox"] for c in parts])})
    return findings


def _elbow_with_label(label_on_corner: bool) -> list[dict]:
    """Two rects joined by an elbowed arrow carrying a bound edge label.

    The arrow runs right from `s1` to (360, 100), turns, and drops into
    `s2`'s top edge. `label_on_corner` decides where the label's opaque
    backdrop lands: centred on that turn, or clear of the stroke above the
    horizontal run. Everything else about the two scenes is identical, so
    a difference in the ablation delta is a difference the label made.

    Args:
        label_on_corner: True to park the label box over the (160, 0)
            corner; False to park it beside the run instead.

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
    lx, ly = (330, 90) if label_on_corner else (250, 60)
    lbl = el(id="t1", type="text", x=lx, y=ly, width=60, height=20,
             text="then", fontSize=16, fontFamily=1, textAlign="center",
             verticalAlign="middle", containerId="a1", originalText="then")
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
        finds = ablation_findings(scene + [ghost], ["g1"], self.workdir)
        self.assertIn("g1", [f["element"] for f in finds
                             if f["check"] == "ablation_existence"])

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
        finds = ablation_findings(scene + [ghost], ["g1"], self.workdir)
        self.assertIn("g1", [f["element"] for f in finds
                             if f["check"] == "ablation_existence"])

    def test_ablation_continuity_neighbour_is_silent(self) -> None:
        """A label beside the arrow leaves the connector's delta whole."""
        scene = _elbow_with_label(label_on_corner=False)
        finds = ablation_findings(scene, ["a1"], self.workdir)
        self.assertEqual([f for f in finds
                          if f["check"] == "ablation_continuity"], [])
        # An empty delta would satisfy that assertion too, so pin that the
        # arrow really did leave the picture: existence stays silent only
        # when ablating it changed pixels.
        self.assertEqual([f for f in finds
                          if f["check"] == "ablation_existence"], [])

    def test_mutant_label_backdrop_severs_connector(self) -> None:
        """A label parked on the elbow cuts the connector into two strokes.

        r5-14's class, measured from pixels: the label's opaque backdrop
        is painted over the corner, so the arrow's ink arrives from the
        left, stops, and resumes below — two components where the model
        holds one connector.
        """
        scene = _elbow_with_label(label_on_corner=True)
        finds = ablation_findings(scene, ["a1"], self.workdir)
        severed = [f for f in finds if f["check"] == "ablation_continuity"]
        self.assertEqual([f["element"] for f in severed], ["a1"])
        self.assertGreaterEqual(severed[0]["magnitude"], 2.0)


@unittest.skipUnless(RENDER, "render tier: set MUTANTS_RENDER=1 "
                             "(starts a headless browser)")
class TestRenderTierAborts(unittest.TestCase):
    """Spec §7: a render that was asked for and cannot happen is a failure."""

    def test_missing_browser_raises_instead_of_skipping(self) -> None:
        """No browser with the tier requested raises, naming the search."""
        with mock.patch.object(canvas, "find_browsers", lambda: []):
            with self.assertRaises(RuntimeError) as caught:
                _browser()
        self.assertIn("no chromium", str(caught.exception))
        self.assertIn("ms-playwright", str(caught.exception))
