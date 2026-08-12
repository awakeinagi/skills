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
        finds = ablation_findings([*scene, ghost], ["g1"], self.workdir)
        self.assertIn("g1", [f["element"] for f in finds
                             if f["check"] == "ablation_existence"])

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


# ---------------------------------------------------------------------------
# Snapshot framing. Nothing above leaves this file's own `_shot`; the tests
# below drive `canvas.py snapshot` end to end — a real Project, a real Store,
# the real argv — because the defect they pin lives in that CLI's tier 2 and
# nowhere else. `rasterize_svg` clamps the browser window to 3000px wide
# (canvas.py:10003, `win_w = max(min(want_w, 3000), 320)`) while `render_svg`
# only scales a drawing down past 4000px wide (canvas.py:4542), so anything
# between those two numbers is rendered at full size into a window too narrow
# to hold it and the overflow is simply not in the PNG. `validate_png` then
# compares the file against the WINDOW, not against the drawing, so the
# snapshot reports VALID=true and TIER=2 with pieces of the artifact missing.
# That is what bit the ELK spike: the 12-node dagre arm lost `Hand to carrier`
# and `Delivered` off the right edge (ELK-RESULTS.md, "What the eyes caught"
# item 4).
# ---------------------------------------------------------------------------

# Mirrors canvas.py:10003. Kept as a number here on purpose: importing the
# clamp would make the test agree with the bug by construction.
SNAP_WIN_CAP = 3000
# Node spans (outer node x to outer node x) chosen either side of the cap and
# both under render_svg's own 4000px scale-down, so the uniform-scale path
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


def _span_scene(span: int, root: Path) -> tuple[Any, list[dict], int]:
    """Build the `span`-wide flow in a project and ask what it wants to be.

    The browser-free half of `_rightmost_node_ink`, split out so the
    ungated regime test below computes `want_w` by exactly the same route
    the mutant does — a regime invariant checked against a differently
    derived number would not be checking the mutant's regime.

    Args:
        span: Passed to `_span_flow_batch`.
        root: Project root to create. The caller owns it, and owns
            clearing the runtime files the `Project` writes OUTSIDE it
            (see `_clear_runtime`).

    Returns:
        `(project, elements, want_w)` — the live `canvas.Project`, the
        committed scene, and the width `render_svg` asks for.
    """
    project = canvas.Project(root)
    project.ensure_tree()
    store = canvas.Store(project)
    store.apply_batch(_span_flow_batch(span))
    els = store.scenes["wide"]
    _svg, want_w, _want_h = canvas.render_svg(els, title="Wide")
    return project, els, want_w


def _clear_runtime(project: Any) -> None:
    """Delete the runtime files a Project keeps outside its own root.

    `Project` hashes its root path into the system temp dir for state,
    events and log, so removing the project tree alone leaves three
    files behind per test run.

    Args:
        project: The `canvas.Project` to clean up after.
    """
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
    project, _els, want_w = _span_scene(span, root)
    try:
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
        _clear_runtime(project)
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

    @unittest.expectedFailure
    def test_mutant_snapshot_cap_drops_the_rightmost_node(self) -> None:
        """A drawing wider than the window cap loses its right-hand end.

        The artifact wants 3640px; the window is clamped to 3000; the
        rightmost node's whole column band is past the clamp, so its ink
        count is 0 and the agent's only view of its own drawing is
        missing a node it just placed — reported VALID=true.

        WHAT FLIPS THIS, precisely. The band is mapped 1:1 (PNG x == svg x
        minus minx), so the only repair that turns this green untouched is
        one where **the window follows the drawing** — raise or drop the
        3000px clamp so a 3640px drawing is rasterized into a 3640px
        window. Every other candidate needs work here too, and two of them
        are traps:

        - **Scale-to-fit inside `rasterize_svg`** — the fix V0.9-PLAN
          recommends first, and what the spike's own `fullshot2.py` did —
          leaves this RED on a FIXED product. The whole drawing lands in a
          3000px PNG at 0.824x, so `n2`'s ink sits at PNG x 2835..2967
          while this band still looks at 3440..3600, off the right edge.
          Such a fix MUST rewrite `_rightmost_node_ink`'s band mapping in
          the same change, or it will look like it failed.
        - **Naive proportional band scaling is NOT that rewrite.** Multiply
          the band by `png_w / want_w` and it lands at 2835..2967 — which
          on the UNFIXED 1:1 raster is the middle of the `n1 -> n2` arrow's
          horizontal run (svg-relative x 1900..3440). Measured against the
          product as it stands today: 264 ink pixels, so the test would go
          GREEN with two nodes still missing from the picture. Any band
          rewrite has to derive its mapping from the PNG the product
          actually produced AND be re-run against the unfixed product
          first, to watch it fail.
        - **Raising `render_svg`'s own 4000px threshold** takes the scene
          out of the regime this mutant measures. That does not flip it; it
          fails `TestSnapshotFramingRegime` loudly instead, which is the
          intended signal.
        - **A truncation warning alone** (V0.9-PLAN WP5's fallback
          suggestion) changes stdout, not pixels, and leaves this red —
          correctly, because the drawing is still not in the file. If the
          project decides warn-only is the ship, this mutant does not
          become a lie: re-point it at the warning on stdout, or retire it
          with that decision as the reason. Do not delete it to get green.

        The regime guards this test used to carry inline moved OUT to
        `TestSnapshotFramingRegime`: inside an `expectedFailure` every
        assertion reports identically as "expected failure", so a guard
        here could never signal and would have let the test silently stop
        measuring anything.
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
    """The wide scene must stay in the band of widths the red mutant means.

    Deliberately NOT gated and deliberately NOT an `expectedFailure`, for
    the same reason `TestRenderTierEvidence` is neither: this needs no
    browser, and the rot it catches — the scene drifting out of the
    regime, so the mutant measures nothing and still reports "expected
    failure" — happens in ordinary editing, where nobody has
    `MUTANTS_RENDER=1` set. Gated, it would notice months late; inside
    the mutant, it could not notice at all.
    """

    def test_wide_scene_sits_between_the_two_caps(self) -> None:
        """`WIDE_SPAN` wants a width past the window clamp but under 4000.

        Both bounds are load-bearing. Under `SNAP_WIN_CAP` there is no
        truncation to pin. Over 4000, `render_svg`'s own uniform
        scale-down (canvas.py:4542) kicks in, PNG x stops being svg x
        minus minx, and `_rightmost_node_ink`'s 1:1 band mapping silently
        measures the wrong column.
        """
        root = Path(tempfile.mkdtemp(prefix="mutants-regime-"))
        try:
            project, _els, want_w = _span_scene(WIDE_SPAN, root)
            _clear_runtime(project)
        finally:
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
