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


# ---------------------------------------------------------------------------
# Render parity (Batch D item 1, 2026-08-13; backlog item 15, out of the
# visualize-skill mine's renderer-fidelity finding). The doctrine it comes
# from: any second render path needs equivalence pins against the primary.
# Ours are `render_svg`'s markup (tier 1) and the browser's raster of it
# (tier 2) — and the first thing to say about that pair is what it cannot do.
#
# Tier 2 rasterizes tier 1's OWN OUTPUT, so every defect where tier 1 omits or
# misstates content is inherited rather than caught. `freedraw` and `image`
# reach neither path (test_mutants.TestExportCompleteness's two reds), and
# reordering an element moves neither path (TestPaintOrder's red). A parity
# sweep reports perfect agreement on all three, and that agreement is worth
# nothing as independent evidence: it ATTESTS a defect already pinned
# elsewhere. `test_dropped_classes_agree_by_being_absent_from_both` says so
# out loud, because a green row in a parity table otherwise reads as health.
#
# What the pair CAN answer is the question tier 1 settles alone and can get
# wrong: does the frame tier 1 chose actually contain the ink tier 1 emitted?
# Same markup, two viewports — the one `render_svg` computed and one
# deliberately generous. A correct frame makes the two rasters identical; a
# short frame loses ink off an edge, and the difference between them is that
# loss, counted in pixels.
#
# WHAT THIS FOUND. `render_svg`'s bounds loop (canvas.py:4497-4515) takes the
# MAX side from `max(stored, text_dims(...))` — v0.3's fix for the "real glyph
# advance regularly overhangs" stored extents, whose comment sits right there.
# The MIN side never got the same treatment: it is `xs.append(e.get("x", 0))`,
# the raw origin. For a `textAlign: center` text that is wrong by
# construction, because `paint` anchors such a text at `x + width/2` and runs
# its glyphs BOTH ways from there (canvas.py:4620): when the drawn string is
# wider than the stored width, the leading glyphs are painted to the left of
# `x`, outside the viewBox, and the SVG viewport clips them off. Half of v0.3's
# fix is missing, on the side nobody measured.
# ---------------------------------------------------------------------------

# Extra margin per side for the generous frame. It only has to exceed the
# largest overhang any scene here produces (56px); 200 leaves headroom without
# growing the raster enough to slow the diff down.
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
            `render_svg` scaled this scene (its uniform 4000x3000 clamp,
            canvas.py:4540). Under a scale a viewBox unit is no longer a
            pixel, so the difference between the two rasters stops being
            a count of lost ink — better to refuse than to report a
            number that means nothing.
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
    """Which sides of tier 1's frame the element's ink escapes.

    Args:
        bbox: The ink's bounding box, in the GENEROUS frame's pixels.
        pad: The generous frame's extra margin per side.
        w: Tier 1's own raster width.
        h: Tier 1's own raster height.

    Returns:
        The escaping edges joined by "+", or "" when the ink sits wholly
        inside the frame tier 1 chose.
    """
    x0, y0, x1, y1 = bbox
    return "+".join(name for name, escaped in
                    (("top", y0 < pad), ("left", x0 < pad),
                     ("right", x1 >= pad + w), ("bottom", y1 >= pad + h))
                    if escaped)


def parity_findings(elements: list[dict], ids: Iterable[str],
                    workdir: str) -> list[dict]:
    """Findings where tier 1's frame does not contain tier 1's own ink.

    Args:
        elements: The full scene.
        ids: Ids to measure, one at a time.
        workdir: Directory renders are written into.

    Returns:
        Findings shaped like `ablation_findings` output. `parity_clipped`
        carries as its magnitude the count of pixels tier 1 emitted and
        tier 1's own viewport then cut away, and as its direction the
        edges they went off.
    """
    framed_w, framed_h = _framed_svg(elements)[1:]
    findings: list[dict] = []
    for eid in ids:
        framed = _element_ink(elements, eid, workdir)[0]
        whole, bbox = _element_ink(elements, eid, workdir, pad=PARITY_PAD)
        if bbox is None or whole <= framed:
            continue
        edges = _clipped_edges(bbox, PARITY_PAD, framed_w, framed_h)
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

        Two of the nine rows are VACUOUS, and should be read as such:
        `freedraw` and `image` reach neither path, so both sides of the
        assertion are False and those rows pass without observing
        anything. They are swept all the same — see
        `test_dropped_classes_agree_by_being_absent_from_both`, which
        exists to say why that agreement attests a defect pinned
        elsewhere rather than evidencing fidelity, and which names the
        red to flip when the paint branches land.
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

    def test_dropped_classes_agree_by_being_absent_from_both(self) -> None:
        """Both dropped classes are absent from tier 1 AND tier 2 — agreed.

        Read this row correctly, because it is the one that could be
        misquoted. It is NOT evidence that the two paths are faithful to
        each other in any useful sense: tier 2 rasterizes tier 1's
        output, so a class tier 1 never paints cannot appear in tier 2
        either, and the agreement is arithmetic rather than observation.
        It ATTESTS the defect `test_mutants.TestExportCompleteness`
        already pins red and adds nothing to it. It is written down so
        the sweep above cannot be summarized as "all nine classes agree"
        without this caveat attached, and so that whoever lands the
        missing paint branches is sent to both files at once.
        """
        for etype in tm._DROPPED:
            with self.subTest(element_type=etype):
                self.assertEqual(tm._export_delta(tm._one_of(etype), "x-1"),
                                 {})
                self.assertEqual(
                    _element_ink(tm._one_of(etype), "x-1", self.workdir)[0],
                    0,
                    "%s now leaves ink, so the tier-1 paint branch has "
                    "landed: flip test_mutants.TestExportCompleteness."
                    "test_red_%s_never_reaches_the_export and re-read "
                    "this attestation" % (etype, etype))

    def test_parity_clip_is_red_by_measurement_not_by_error(self) -> None:
        """The red below is red for the reason it claims, and says so.

        Two jobs the `expectedFailure` next door cannot do for itself.
        First, `@unittest.expectedFailure` swallows ERRORS as well as
        failures (skill doctrine §6), so a `_reframe` that began raising
        — on a scaled scene, on a changed `<svg>` tag — would print an
        identical healthy `x` with nothing measured at all. Second, this
        is the only place `parity_clipped` is asserted to FIRE, with its
        magnitude and its direction: the mutant asserts the post-fix
        silence, and a check proven only by a silence is not proven
        (`test_mutants.TestCoverage.
        test_silence_only_mutant_does_not_prove_its_check`).
        """
        try:
            finds = parity_findings(_left_edge_label(20), ["t1"],
                                    self.workdir)
        except Exception as exc:
            self.fail("the parity red is red via %r, not a measurement "
                      "mismatch — that is a broken pin, not a defect pin"
                      % exc)
        self.assertEqual(
            [(f["check"], f["element"], f["direction"]) for f in finds],
            [("parity_clipped", "t1", "left")],
            "parity_clipped no longer fires on the clipped label — if the "
            "bounds loop learned about center anchoring, drop the "
            "expectedFailure on "
            "test_mutant_center_anchored_label_is_clipped_off_the_frame")
        # 170px of 820 (measured 2026-08-13). The +-10% band excludes 0
        # (no loss), 650 (the part that survives) and 820 (the whole
        # label), so a check that reported any of those instead fails.
        self.assertAlmostEqual(finds[0]["magnitude"], 170, delta=17)

    @unittest.expectedFailure
    def test_mutant_center_anchored_label_is_clipped_off_the_frame(self
                                                                  ) -> None:
        """A label wider than its stored width loses its head off the frame.

        The picture then asserts something the model never said: the
        drawing reads "considerably wider label" where the model holds
        "a considerably wider label", and the reader has no way to be
        suspicious of glyphs that leave no mark. Absence is the one
        defect a reader cannot notice, and this export is the agent's own
        view of its own drawing.

        Flips when `render_svg`'s bounds loop gives its MIN side the
        treatment v0.3 gave the max side — accounting for a centered
        text's leftward run, `x + width/2 - text_dims(...)/2`, instead of
        the raw `x`. That is the WP that owns `render_svg`, not this
        file. Two things that will NOT flip it, deliberately: widening
        the fixed 40px pad only moves the threshold this scene has
        already cleared by 46px, and clamping the anchor to `x` would
        flip it while changing where every centered label in every
        drawing sits — which `test_shipped_classes_agree_on_what_they
        _render` and the model tier's label checks would both notice.
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
    change quietly taking the scene out of its regime, so the red
    measures nothing and still reports "expected failure" — happens in
    ordinary editing, where nobody has `MUTANTS_RENDER=1` set.
    """

    def test_the_wide_label_still_overhangs_the_frame_on_the_left(self
                                                                 ) -> None:
        """`_WIDE_LABEL` drawn at 16px still runs left of the viewBox.

        Both halves are load-bearing. If `text_dims` stops returning 192
        the arithmetic below is about a different string; and if the
        leftward run stops clearing the renderer's fixed 40px pad there
        is no clip left to pin, whatever the mutant goes on reporting.
        """
        self.assertEqual(canvas.text_dims(_WIDE_LABEL, 16),
                         (_WIDE_LABEL_W, 20),
                         "the font metrics moved: re-measure _WIDE_LABEL_W "
                         "and the 170px magnitude in "
                         "test_parity_clip_is_red_by_measurement_not_by_"
                         "error")
        # `paint` centers the text on x + width/2 = 10 and runs the glyphs
        # 96px each way, so the drawn left edge is at -86 while the frame
        # starts at minx = x - SVG_PAD = -40.
        drawn_left = 20 / 2 - _WIDE_LABEL_W / 2
        self.assertLess(drawn_left, -SVG_PAD,
                        "the centered label no longer reaches past the "
                        "%dpx pad (left edge %g): the clip mutant has "
                        "nothing to measure" % (SVG_PAD, drawn_left))


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
