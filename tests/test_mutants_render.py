"""Render tier: mutants measured in pixels, by ablation, under a browser.

Every other tier reads the model. This one reads the picture: rasterize the
scene's tier-1 SVG, rasterize it again with one element omitted, and diff.
The delta IS that element's contribution to the drawing, so two questions
the model cannot answer become arithmetic — an element whose ablation
changes no pixels is not in the picture (`ablation_existence`), and a
connector whose delta splits into two separated components is severed
somewhere along its run (`ablation_continuity`), which is the r5-14 class:
an opaque label backdrop erasing the stroke it sits on.

THERE ARE TWO RENDERERS IN HERE, and which one a finding came from is the
first thing to establish about it:

- **The SVG path** (`ablation_findings`, and everything else in this file).
  `canvas.render_svg`'s markup, rasterized in chromium. Free, cached, and
  asked of every scene here — but it can only ever answer "is this in the
  EXPORT", because the raster is a raster OF the export.
- **The CLIENT path** (`client_ablation_findings`, v0.9 task 50). The real
  Excalidraw bundle, driven headless through the screenshot protocol
  `canvas.py` ships, answering "is this in the PICTURE" — the one the user
  sees. Costs a session; see its section comment, which is where the whole
  design is written down.

The second exists because the first two tiers share a ceiling: tier 2
rasterizes tier 1's own output, so a defect where `render_svg` omits or
misstates content is inherited rather than caught, and no arrangement of
those two can escape it. `opacity` was that ceiling's longest-standing
instance — `render_svg` does not emit it, so a node invisible on the canvas
was ink in the export and `ablation_existence` could not see it. The client
path shares no code with `render_svg`, which is what makes it independent
evidence rather than a second opinion from the same witness.

Not every pixel question is an ablation: `TestSnapshotFraming` at the tail
asks a blunter one — is the whole drawing even in the frame? — and asks it
of `canvas.py snapshot` itself rather than of this file's own rasterizer.

Ablation is by OMISSION, not by styling, on both paths: the hidden element
is absent from the document entirely, so nothing about how either renderer
treats `opacity` or `display` can make a hidden element leave ghost ink
behind. What differs is only who draws what remains.

The whole module is gated behind `MUTANTS_RENDER=1` because it starts a
headless browser. Gated OFF it skips; gated ON with no browser to be found
it RAISES (spec §7) — an environmental failure must abort with a named
reason, never quietly mark the mutants green.

Renders are cached between runs, content-addressed, in the directory
`render_cache_dir` names — read that docstring before debugging a result
that makes no sense, because **`rm -rf ~/.cache/wysiwyg-grilling/render`
is the recovery procedure** and system fonts are the one input the key
cannot see. A full run asks for 138 renders of 78 distinct documents; warm,
it starts no browser at all. Client renders share that directory under a
`client-` prefix and a key of their own (`_client_cache_key`), which carries
the app bundle as well — the same purge clears both.
"""
from __future__ import annotations

import contextlib
import functools
import hashlib
import importlib
import io
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import zlib
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] /
                       "skills" / "wysiwyg-grilling" / "scripts"))
import canvas
import test_mutants as tm
from pngdiff import components, read_png_gray, tolerant_diff, tolerant_diff_mask
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

# How far in from a component's bounding box counts as that component's END,
# in pixels. Wide enough to hold a whole stroke including its anti-aliased
# skirt — the ends being compared are 2px strokes, and a band thinner than
# the stroke would read one edge of it and call that the direction it points.
# A CEILING, not the value used: `_side_band` halves it on any component too
# thin to hold two of them, or a scrap narrower than 8px reports its own
# length at both ends.
_BAND = 4

# One pixel of slack on each end before they are compared, which is the same
# budget `pngdiff._dilate` spends on the ink itself and is spent here for the
# same reason: a stroke's position either side of a gap is only ever accurate
# to a pixel. Measured on the corpus rather than chosen — two of its arrows
# are single curves broken by their own bound label whose facing ends land in
# ADJACENT columns, and without this they read as severed connectors.
_SLACK = 1

_SVG_TAG = re.compile(r"^<svg [^>]*>")


def _browser() -> str:
    """The chromium-family binary to rasterize with — best first.

    `canvas.find_browsers()` already ranks a playwright `headless_shell`
    below the full `chrome`, and this function keeps that order rather
    than preferring the faster binary. That is a deliberate refusal, and
    the measurement behind it is on this machine:

    | binary | render tier, cold | 6px word contrast |
    |---|---|---|
    | `chromium-1234/chrome` | 90.1s | **4.619:1** |
    | `chromium_headless_shell-1148` | **35.3s** | **5.510:1** |

    Every test in this tier returned the same verdict under either
    (measured 2026-08-15, 61 tests at the time — a dated result, not a
    live count), so
    switching is *tempting* and would cost nothing today. The reason not
    to is the second column. 4.62 against 5.51 is a fifth of the reading,
    on the one axis `TestLegibilityFloor` exists to measure — and it is
    the same disagreement the calibration table above that class already
    records, which at 5px reads 4.12 against 5.27 and so does straddle
    WCAG's 4.5:1 floor. (Both 6px figures clear 4.5; it is the step below
    that changes the verdict a human would give. The table is the honest
    statement of the divergence, not either single pair.)

    It already costs margin on a LIVE pin.
    `test_text_below_the_floor_degenerates_in_the_picture` requires the
    below-floor word to lose at least 30% of the at-floor contrast:

    | binary | 6px | 7px | ratio | headroom to 0.70 |
    |---|---|---|---|---|
    | `chrome` | 4.619 | 8.498 | 0.5435 | 0.157 |
    | `headless_shell` | 5.510 | 8.770 | 0.6283 | **0.072** |

    Preferring the faster binary would more than halve the margin on a
    test that is passing today — and it would pass, quietly, on a fleet
    that had silently re-calibrated itself.

    The corpus's pinned numbers were calibrated under `chrome`. Preferring
    `headless_shell` would re-calibrate the instrument on exactly those
    machines that happen to have one installed, and leave the machines
    that do not measuring something else — which is the cross-machine
    pixel movement `CHROME_FLAGS` exists to prevent, arrived at from the
    other direction.

    The speed is still available, explicitly: set `MUTANTS_RENDER_BROWSER`
    to a substring of the wanted path (`headless_shell` does it) and cold
    runs take a third of the time. Opt-in is the right shape because the
    saving is real but lands only on COLD runs — a warm cache pays zero
    browser starts either way — while the re-calibration would be
    permanent and silent.

    An explicit request that matches nothing RAISES rather than quietly
    falling back, for the same reason a missing browser does: being handed
    a different build than the one you asked to measure is how the 4.62
    number got attributed to a build that renders 5.51.

    Returns:
        The path `MUTANTS_RENDER_BROWSER` selects, or the first one
        `canvas.find_browsers()` offers.

    Raises:
        RuntimeError: If no browser was found, or if an explicit
            `MUTANTS_RENDER_BROWSER` matched none of them. The render
            tier never degrades to a skip here: `MUTANTS_RENDER=1` is a
            request to measure pixels, and not measuring them is a
            failure, not a pass (spec §7).
    """
    found = canvas.find_browsers()
    if not found:
        raise RuntimeError("render tier requested but no chromium: %r"
                           % (SEARCHED,))
    want = os.environ.get("MUTANTS_RENDER_BROWSER")
    if not want:
        return found[0]
    for path in found:
        if want in path:
            return path
    raise RuntimeError("MUTANTS_RENDER_BROWSER=%r matched none of the "
                       "browsers found: %r" % (want, found))


def _mkworkdir() -> str:
    """Make a scratch directory for a test that needs one on disk.

    Scratch, not renders: what is left here is the throwaway root a test
    needs on disk in its own right, and its whole point is being fresh
    per test. Two classes call it — `TestSnapshotFraming`, whose tests
    build a canvas PROJECT under it by way of `_rightmost_node_ink`, and
    `TestRasterizeScaleToFit`, which asks `canvas.rasterize_svg` to write
    a PNG somewhere. Renders no longer come here: they go to the shared
    cache `render_cache_dir` returns, which is the opposite kind of
    directory in every respect. Conflating the two is what made two
    `TestSnapshotFraming` tests collide on `artifact 'wide' already
    exists` when the cache was first shared, because a project root that
    is reused already holds the artifact the test creates.

    Snap-confined chromium builds run in a mount namespace where the real
    `/tmp` is invisible, so when the chosen browser is one of those the
    scratch dir has to live under `$HOME` instead (the constraint is
    documented on `canvas.find_browsers`).

    Returns:
        Path to a fresh temp directory the caller owns and must remove.
    """
    root = str(Path.home()) if "/snap/" in _browser() else None
    return tempfile.mkdtemp(prefix="mutants-render-", dir=root)


def render_cache_dir() -> Path:
    """Where cached renders live — shared, persistent, outside the repo.

    Rooted under `$HOME` unconditionally, which is not a guess about
    where caches belong: a snap-confined chromium cannot see the real
    `/tmp` at all, so a cache anywhere else would be unreadable by the
    very browser that has to write into it. `XDG_CACHE_HOME` is
    deliberately NOT honoured for the same reason — it is allowed to
    point outside `$HOME`, and a cache that relocates itself out of the
    browser's reach fails as a `RuntimeError` on every render.

    **To purge it: `rm -rf ~/.cache/wysiwyg-grilling/render`.** That is
    the whole recovery procedure, and it is the answer to the one form of
    staleness the key cannot cover. The key holds the browser binary, its
    flags, the window size and the markup, so every input this file
    controls is in it — but the SYSTEM FONTS are not, and they move the
    pixels the legibility section measures. Install or change a font and
    the cache is serving the old face forever. Purge after any font
    change, and when a render-tier failure makes no sense.

    NO MUTANT, AND THE JUDGEMENT IS RECORDED SO IT IS NOT RE-MADE
    (curator batch 23 item 11, from task-perf-report candidate 2,
    2026-08-15). That report asks whether a mutant can be built for "the
    instrument silently reports stale pixels as fresh", and calls it the
    one worth building if so. It cannot be built here, and the reason is
    the exposure itself: a pin would have to vary the font stack and
    assert the key moved, and the key does not consult the font stack, so
    there is no input to vary. The assertion degenerates to comparing a
    value with itself. Writing it anyway would produce a test that fails
    for the rest of time without ever having measured anything, which is
    worse than the gap it names.

    What closes it is a MECHANISM and not a test — the fingerprint
    manifest on the performance backlog: one line in this directory
    holding the browser identity, `CHROME_FLAGS`, the wrapper and a
    digest of `fc-list`, checked once per process and purging on
    mismatch. The reviewer measured `fc-list | md5sum` at 43ms, 0.2% of a
    warm run. When that lands the pin becomes trivial and should be
    written in the same change: patch the digest, assert the cache
    purged. Until then this docstring is the mitigation, and that is a
    weaker thing than a test, said out loud rather than implied.

    This is a NEW exposure and not an inherited one, which is the part
    most worth remembering: the old per-test cache could not outlive a
    font change, because it could not outlive the test. Persistence
    bought the speed and created the staleness in the same commit.

    Set `MUTANTS_RENDER_CACHE` to relocate it — which is how the tests
    below get an empty cache to count hits and misses against, and how a
    run can be forced cold without disturbing anyone else's cache.

    Returns:
        The cache directory, created if it did not exist.
    """
    override = os.environ.get("MUTANTS_RENDER_CACHE")
    root = (Path(override) if override else
            Path.home() / ".cache" / "wysiwyg-grilling" / "render")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _html_document(svg: str) -> str:
    """Wrap an SVG in the page chromium is actually pointed at.

    A named function and not an inline format string because the wrapper
    is a RENDER INPUT: its `margin:0` is what makes the screenshot's
    origin the drawing's origin, and anything ever added here — a
    `@font-face`, a background, a different margin — moves pixels exactly
    as a chromium flag would. `_cache_key` hashes what this returns, so
    the wrapper cannot change without the key changing.

    Args:
        svg: The complete SVG document to draw.

    Returns:
        The HTML document to write out and load.
    """
    return ("<!doctype html><html><body style='margin:0'>%s"
            "</body></html>" % svg)


def _browser_identity() -> str:
    """The chosen browser as something that CHANGES when the browser does.

    The path alone is not the binary's identity, and the difference is
    load-bearing now that renders outlive the run that made them. A
    playwright build carries its number in its path
    (`chromium-1234/...`), so a path comparison happens to work for those
    — but `canvas.find_browsers` also returns PATH-found browsers like
    `/snap/bin/chromium`, whose path is **stable across upgrades**. An
    unattended `snap refresh` or `apt upgrade` replaces the binary under
    an unchanged path, and a path-keyed cache would then serve
    pre-upgrade pixels as fresh measurement, indefinitely, with nothing
    to notice. This file's own calibration table records that a chromium
    build change moves the legibility numbers by more than the margin
    those tests have.

    Size and mtime rather than a content hash: the binary is ~288 MB and
    this is computed once per render, so hashing it would cost more than
    the render. `os.stat` is microseconds and an upgrade cannot plausibly
    leave both fields identical. A false MISS — after a copy, or a
    touch — is harmless, because a key field can only split entries,
    never merge them.

    **The snap revision is read separately, and the stat alone would not
    have covered the very case that motivates this.** `/snap/bin/chromium`
    is a symlink to `/usr/bin/snap` — the launcher, not the browser — so
    statting it fingerprints the snap TOOL, which a `snap refresh
    chromium` does not touch. What moves is `/snap/<name>/current`, a
    symlink to the revision number, and reading it costs 1.7 microseconds.

    What this still does not cover: any OTHER launcher indirection — a
    distro wrapper script, a flatpak — where a stable front path hides a
    binary that changed. Asking the browser itself (`--version`) would be
    general and is the obvious alternative; it is not used because it
    costs 5.6s on the snap build, a quarter of a warm run, for a check
    made on every render. The honest summary is that this closes the two
    known indirections and narrows rather than eliminates the class; the
    complete answer is the once-per-process fingerprint manifest tracked
    as a follow-up, which can afford a subprocess.

    Returns:
        The path, the binary's size and mtime, and — for a snap — its
        revision, NUL-joined. Propagates `_browser`'s `RuntimeError`
        when there is no browser to stat.
    """
    path = _browser()
    st = os.stat(path)
    parts = [path, str(st.st_size), str(st.st_mtime_ns)]
    if "/snap/" in path:
        try:
            parts.append(os.readlink(Path("/snap") / Path(path).name
                                     / "current"))
        except OSError:
            # an unreadable revision degrades to the stat above rather
            # than inventing a constant that looks like an answer
            parts.append("snap-revision-unreadable")
    return "\0".join(parts)


def _cache_key(document: str, w: int, h: int) -> str:
    """The content address of one render.

    Four fields, because the cache now OUTLIVES the run that filled it
    and each of these moves pixels. **This is not the same as covering
    everything that moves pixels** — the system fonts are not in here and
    cannot cheaply be (see `render_cache_dir`), and `_browser_identity`
    narrows rather than closes the question of whether a stable path
    still names the same binary. Read those two before trusting a cached
    render that surprises you; the fields below are what the key really
    promises:

    - **The browser binary, by identity and not by path** — see
      `_browser_identity`. Keyed on markup alone the cache is only sound
      while one binary is in play: the min_font calibration sweeps the
      same scenes across three chromium builds that disagree about
      anti-aliasing, and a markup-keyed cache happily served build 151's
      pixels for build 131 — reporting 4.62:1 for a build that renders
      the same word at 5.51:1, a fifth of the measurement, on the axis
      those tests exist to measure.
    - **`CHROME_FLAGS`.** They exist precisely because device scale,
      colour profile and subpixel text move pixels, so changing one and
      serving the old render would silently answer the old question.
    - **The window size**, which decides how much of the document the
      screenshot contains. Every caller happens to derive `w`/`h` from
      the same `<svg>` tag it derives the markup from, so this splits no
      entry that exists today; it is here so that a future caller that
      shoots one document at two sizes cannot get one answer twice.
    - **The whole HTML document, not the SVG fragment.** The wrapper
      `_html_document` adds is a render input like any other, and hashing
      only the fragment left a real hole: changing the wrapper's margin
      produced different pixels under an unchanged key.

    A change to `canvas.render_svg` is safe BY CONSTRUCTION and needs no
    entry here: the markup is that function's output and it is a
    SUBSTRING of what this hashes. Different drawing, different key.
    Nobody should "fix" that by adding a version stamp.

    Args:
        document: The complete HTML document, from `_html_document`.
        w: Browser window width in pixels.
        h: Browser window height in pixels.

    Returns:
        A 16-hex-character digest, used as the cache entry's filename.
    """
    return hashlib.sha1(
        ("%s\0%s\0%dx%d\0%s"
         % (_browser_identity(), "\0".join(CHROME_FLAGS), w, h, document))
        .encode("utf-8")).hexdigest()[:16]


def _rasterize(svg: str, w: int, h: int) -> bytes:
    """Screenshot one SVG document at a given window size.

    Split out of `_shot` so the parity section below can rasterize markup
    it framed itself. Renders come from the content-addressed cache
    `render_cache_dir` describes, so a document already drawn — in this
    test, in another test, or in a run last week — costs no browser start
    at all. That is the whole performance story of this tier: a full run
    asks for 138 renders of 78 distinct documents, and used to pay 100
    browser starts for them because the cache lived in a per-test
    directory that `tearDown` deleted.

    Both writes are atomic (write to a unique temp name, `os.replace`
    into place), so two runs sharing the cache cannot read a half-written
    PNG. Racing writers are otherwise harmless here by construction —
    content addressing means a concurrent writer is producing the same
    bytes for the same name.

    Args:
        svg: The complete SVG document to draw.
        w: Browser window width in pixels.
        h: Browser window height in pixels.

    Returns:
        The screenshot's PNG bytes.

    Raises:
        RuntimeError: If the browser exited without writing a PNG.
    """
    cache = render_cache_dir()
    document = _html_document(svg)
    name = _cache_key(document, w, h)
    png = cache / (name + ".png")
    if png.exists():
        return png.read_bytes()
    html = cache / (name + ".html")
    _atomic_write(html, document.encode("utf-8"))
    # the suffix stays `.png`: chromium picks the encoder off the
    # extension and refuses a screenshot path ending in anything else
    tmp = cache / ("%s.%d.tmp.png" % (name, os.getpid()))
    proc = subprocess.run(
        [_browser(), *CHROME_FLAGS, "--screenshot=%s" % tmp,
         "--window-size=%dx%d" % (w, h), html.as_uri()],
        capture_output=True, timeout=180)
    if not tmp.exists():
        raise RuntimeError("render tier: chromium wrote no PNG for %s "
                           "(rc=%s): %s" % (html, proc.returncode,
                                            proc.stderr[-400:]))
    data = tmp.read_bytes()
    os.replace(tmp, png)
    return data


def _atomic_write(path: Path, data: bytes) -> None:
    """Write `data` to `path` so no reader ever sees it half-written.

    Args:
        path: The file to create or replace.
        data: Its complete contents.
    """
    tmp = path.with_name("%s.%d.part" % (path.name, os.getpid()))
    tmp.write_bytes(data)
    os.replace(tmp, path)


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


def _shot(elements: list[dict], hide: Iterable[str] = ()) -> bytes:
    """Rasterize a scene's tier-1 SVG, omitting the `hide` elements.

    Args:
        elements: The full scene.
        hide: Ids to ablate — dropped from the element list entirely.

    Returns:
        The screenshot's PNG bytes. Propagates `_rasterize`'s
        `RuntimeError` when the browser wrote no PNG.
    """
    return _rasterize(*_framed_svg(elements, hide))


def _delta_components(w: int, h: int, blobs: list[dict[str, Any]],
                      residual: bytearray) -> list[dict[str, Any]]:
    """Group a diff's blobs into the strokes a reader would see.

    `tolerant_diff` already returns connected components, but a single
    stroke routinely survives the dilate-XOR as several of them (the
    tolerance eats the middle of a run and leaves its ends). Filling each
    blob's bounding box and re-componenting merges the fragments of one
    stroke back together while leaving genuinely separated pieces apart,
    so the count means "how many strokes is this", not "how many scraps".

    That fill is also where the ink's SHAPE would be lost — every merged
    component comes out of it a solid rectangle, and on the back-edge
    scene one of them is 33x larger than the stroke inside it. So each
    component's real ink is recovered here by intersecting its members
    with the residual mask, and reduced to four spans by `_edge_profiles`.

    THE INK ITSELF NOW TRAVELS WITH IT, in `"ink"`, which reverses a line
    this docstring used to draw ("the pixel lists never leave this
    function"). That line was drawn against reasoning from BOXES, and it
    is kept in force where it matters: `_completed_by_eye` still decides
    on spans, and the four spans are still what the predicate compares.
    What the ink buys is the ability to ask a component WHERE its ink
    faces a given lane, which four spans cannot answer for a component
    that doubles back — such a piece has two ends on one side and `ends`
    records one span per side, so the second one is not mis-ranked but
    absent (v0.9 Task 24 follow-up; `_facing_end`). The pixels are read
    only through that question.

    Coordinate pairs and not flat indices, so nothing downstream needs
    the raster width to interpret them.

    Args:
        w: Raster width in pixels.
        h: Raster height in pixels.
        blobs: `tolerant_diff` output — dicts with an inclusive `bbox`.
        residual: The `w * h` mask those blobs were componented from, from
            `tolerant_diff_mask`. Required: a component without `"ends"`
            is one `_completed_by_eye` would have to judge on its bounding
            box alone, which is the defect this argument exists to close.

    Returns:
        The merged components of at least `MIN_BLOB` pixels, each with
        `"ends"` and `"ink"` alongside its `"area"` and `"bbox"`.
    """
    mask = bytearray(w * h)
    for blob in blobs:
        x0, y0, x1, y1 = blob["bbox"]
        for y in range(y0, y1 + 1):
            row = y * w
            for x in range(x0, x1 + 1):
                mask[row + x] = 1
    out = []
    for c in components(w, h, mask, pixels=True):
        if c["area"] < MIN_BLOB:
            continue
        flat = [i for i in c.pop("pixels") if residual[i]]
        c["ends"] = _edge_profiles(w, c["bbox"], flat)
        c["ink"] = [(i % w, i // w) for i in flat]
        out.append(c)
    return out


def _side_band(extent: int) -> int:
    """How deep one end's band reaches into a component, on one axis.

    `_BAND` is the depth a stroke's end needs to be seen whole, and it is
    a CEILING here rather than the value: on a component narrower than
    two bands the leftmost columns and the rightmost columns are the same
    columns, both ends report the component's whole length, and the two
    opposite ends come back not merely wrong but IDENTICAL — a span with
    no direction in it at all (curator batch 23 item 8; the corpus
    instance is a 2x7 scrap of a sloped run). Halving the extent makes
    the two bands disjoint at every width of 2 or more, so an end is
    always a side and never the whole thing.

    The floor of 1 is the honest answer for a 1px extent and not a
    guard: a component one column wide has one column, and both of its
    horizontal ends really are it.

    THE REPAIR BELONGS HERE and not downstream, measured rather than
    inherited (the pin records the measurement): the tempting one-line
    alternative is `_ends_line_up` comparing against `min()` of the two
    spans instead of `max()`, and replaying both shape tables under that
    patch REOPENS THREE of the four over-merges `_SEVERED_SHAPES` pins.
    The wrong number is produced here, so it is corrected here.

    THREE, re-measured at 32630e9 by curator batch 26 (2026-08-16). This
    read "two" in both places it is stated, which was the count when it
    was taken; the third — "a stub 80px clear below the L" — drops from
    2 pieces to 1 under the patch as well, and no version of this
    sentence named it. The correction makes the argument STRONGER, which
    is why it is worth the edit rather than worth leaving: the constraint
    on the next person tempted by the one-line repair is three quarters
    of the table, not half.

    Args:
        extent: The component's inclusive size on this axis, in pixels.

    Returns:
        The band depth to use for both ends on that axis.
    """
    return max(1, min(_BAND, extent // 2))


def _edge_profiles(w: int, bbox: tuple[int, ...],
                   ink: list[int]) -> dict[str, tuple[int, int]]:
    """Where a component's ink sits at each of its four ends.

    Each side maps to the CROSS-AXIS span of the ink within a band of
    that side of the bounding box: `"left"` is the y-range of ink in the
    leftmost columns, `"bottom"` the x-range of ink in the bottom rows.
    An L-shaped remnant's `"bottom"` is therefore its leg's foot — two
    columns — and not the 158 columns its bbox is wide.

    The band is `_side_band` of that axis's extent and not `_BAND`
    flat, so the two opposite ends of a thin component are measured on
    disjoint ink. Above `2 * _BAND` the two are the same number and
    nothing about the ordinary case moves.

    Taken over the WHOLE merged component, deliberately, and not per
    member blob and unioned: a merged component's bbox edge band cuts
    across its members' interiors, where a member recorded nothing about
    the side it does not itself bound. Pinned as
    `test_a_merged_components_end_is_measured_on_the_union`.

    Args:
        w: Raster width in pixels, for unflattening the indices.
        bbox: The component's inclusive `(x0, y0, x1, y1)`.
        ink: The component's real ink, as flat indices.

    Returns:
        One `(lo, hi)` span per side named, inclusive. A side with no ink
        within its band is absent — which cannot happen for a component
        built from blob bounding boxes, since each of the four extremes
        of that union is a place some blob had ink.
    """
    x0, y0, x1, y1 = bbox
    xb, yb = _side_band(x1 - x0 + 1), _side_band(y1 - y0 + 1)
    out: dict[str, tuple[int, int]] = {}
    for i in ink:
        x, y = i % w, i // w
        for side, hit, q in (("left", x < x0 + xb, y),
                             ("right", x > x1 - xb, y),
                             ("top", y < y0 + yb, x),
                             ("bottom", y > y1 - yb, x)):
            if not hit:
                continue
            span = out.get(side)
            out[side] = (q, q) if span is None else (min(span[0], q),
                                                     max(span[1], q))
    return out


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

    So the test is collinearity, not proximity, and it is asked of the
    two FACING ENDS rather than of the two pieces: the bounding boxes
    must be SEPARATED along one axis and OVERLAP on the other — the
    cheap prefilter, and everything this predicate used to be — and then
    the ink in the last `_BAND` px of `a`'s trailing side must share a
    lane with the ink in the first `_BAND` px of `b`'s leading one, per
    `_ends_line_up`.

    WHY THE ENDS AND NOT THE BOXES (curator batch 14, from the Task 19
    review F5, 2026-08-14; narrowed by v0.9 task 48). Read literally the
    prefilter alone is a BBOX test, with no bound on how far apart the
    pieces are and no look at where either piece's ink points. Any
    remnant containing a TURN has a bbox tall AND wide, so it overlaps a
    distant stub on one axis by construction — which is what
    `test_mutant_l_shaped_remnant_hides_a_severed_back_edge` reached on
    `_back_edge_with_label("turn")`: the same erased-elbow picture the
    2-segment `corner` scene fires on, silently merged. The synthetic
    over-merges of the same shape are pinned in
    `TestContinuityNarrowingRegime`, and the batch's sweep is why they
    are pinned there rather than described here — a Z-path broken on
    either turn leaves the pieces separated on BOTH axes and fires
    correctly, so the class only shows up when the next segment turns
    back UNDER the remnant.

    The ends are measurable here only because the residual mask is now
    plumbed through: `tolerant_diff_mask` hands back the per-pixel
    residual and `_delta_components` reduces each component's real ink
    to four spans, so what arrives is the ink's direction and not the
    rectangle it was flattened into. The bboxes could never separate the
    back edge's severed turn from its legitimate mid-leg break — both
    are two L's, y-separated, x-overlapping, with x-overlaps of 136 and
    158 columns, the wrong way round from the verdict. On the facing
    ends the same two scenes differ by 22 rows in the same measurement:
    the leg's break is `bottom (279,280)` against `top (279,280)`, and
    the severed turn is `bottom (279,280)` against `top (122,258)`.

    KNOWN TOO NARROW, measured on the corpus at the flip (task-48 report
    §6) and left standing rather than tuned away. Replayed over all 174
    corpus arrows — old predicate against new, same rasters — three
    change verdict, and every one is this exemption's own idiom being
    reported as a severance. None is reachable from any scene in this
    file, which is why they are recorded here and queued for the curator
    rather than pinned:

    - `argus-r5/argus-domain` `r-pipeline-rerun`. The pieces INTERLEAVE:
      a back-loop's two remnants are separated in x by 10px while the
      break they actually have is 40px along the top run, so the
      prefilter picks an axis whose facing ends are not the ones facing
      the gap. The two-piece model does not describe this shape.
      CLOSED by v0.9 Task 24 follow-up — `_facing_end`, below.
    - `argus-r4-arm3/enrichment-pipeline` `e-edgar-insider`. A component
      only 2px wide has its WHOLE ink inside `_BAND`, so a sloped run's
      2x7 scrap reports a 7-row "end" and the ratio's denominator is the
      scrap's length rather than a stroke's width. CLOSED by the same
      task — `_side_band`.
    - `argus-r4-arm3/enrichment-pipeline` `e-edgar-sent`, which is not
      this predicate's doing at all: that label rides across ANOTHER
      arrow's stroke, so ablating it un-covers foreign ink 177 rows away
      — the hazard `ablation_findings`' own docstring names as reachable
      by "a catalogue scene with a label riding one arrow across
      another's". The bbox over-merge was accidentally masking it, and
      the narrowing stops masking it.

    Residual, recorded with the class rather than as an open defect: a
    foreign opaque shape covering a straight run is not lost, because
    `passes_through_foreign` owns exactly that class on the lint tier —
    but it IS lost when the foreign shape's STORED geometry shows no
    overlap, which is F1's class, and this tier was meant to be the
    backstop for precisely that. F1 itself closed at 636da5d.

    THE DOUBLED-BACK CASE, and why the bbox bands alone cannot decide it
    (v0.9 Task 24 follow-up, from batch 23 item 7 — the first residue
    above). `ends` records ONE span per side, and a component that turns
    and comes back under itself has TWO ends on the same side: the
    back run's, at the bbox edge, and the top run's, 30px inside it. The
    band sees only the nearer, so a stub continuing into the top run was
    compared against the back run 69 rows away and the pair came apart.
    No constant moves that — the right end is not mis-ranked, it is
    absent from the structure.

    So when the recorded spans do not line up, each piece's facing end is
    asked for AGAIN, this time against the other's lane (`_facing_end`),
    and the answer is compared instead. Two properties keep that from
    being a widening in disguise:

    * IT CANNOT NARROW. The recorded-span test runs FIRST and unchanged,
      so nothing that merged before stops merging. The second question is
      only ever asked of a pair already refused.
    * THE ANCHOR IS THE NARROWER END, both directions tried, and this is
      the constraint the shape was chosen for rather than a tidiness. An
      end's span is a stroke's cross-section where the end is real and
      the piece's whole width where it is not, so anchoring on the
      narrower one asks "where is the vague piece's ink, in the lane the
      definite piece points down". Reversed, the L in
      `_SEVERED_SHAPES`' fourth scene answers with its top run 116 rows
      from the gap, and two PARALLEL runs then satisfy the ratio and
      merge — which is the r5-14 over-merge this whole predicate exists
      to refuse. Measured, not feared: that scene merges under the
      unguarded version.

    Args:
        a: One component from `_delta_components`, with an inclusive
            `bbox` of `(x0, y0, x1, y1)`, the `ends` spans and its `ink`.
        b: The other component, same shape.

    Returns:
        True if a reader completes the two into one stroke.
    """
    ax0, ay0, ax1, ay1 = a["bbox"]
    bx0, by0, bx1, by1 = b["bbox"]
    # Per separation axis, the end the LOWER piece on that axis faces the
    # gap with, then the end the higher one does. Carried in the loop
    # rather than recovered from the coordinates inside it, because a
    # square-ish bbox makes the x and y projections equal and any test
    # that reads the axis back off them picks the wrong pair of sides.
    for (u0, u1, v0, v1), (w0, w1, z0, z1), lo, hi in (
            ((ax0, ax1, ay0, ay1), (bx0, bx1, by0, by1), "right", "left"),
            ((ay0, ay1, ax0, ax1), (by0, by1, bx0, bx1), "bottom", "top")):
        if max(u0, w0) <= min(u1, w1) or max(v0, z0) > min(v1, z1):
            continue
        near, far = (lo, hi) if u1 < w1 else (hi, lo)
        if _ends_line_up(a["ends"][near], b["ends"][far]):
            return True
        if _relocated_ends_line_up(a, near, b, far):
            return True
    return False


# Which axis a side name lies on, and whether it is that axis's LOW end.
# A table because the two facts are read together and deriving either from
# the other is how `_completed_by_eye`'s own comment says the square-bbox
# misreading gets in.
_SIDE_AXIS: dict[str, tuple[int, bool]] = {
    "left": (0, True), "right": (0, False),
    "top": (1, True), "bottom": (1, False)}


def _facing_end(c: dict[str, Any], side: str,
                lane: tuple[int, int]) -> tuple[int, int] | None:
    """Where `c`'s ink faces a lane, when its bbox edge is facing elsewhere.

    `_edge_profiles` anchors an end at the component's own bounding box,
    which is right for a piece with one end per side and wrong for one
    that doubles back. This asks the question the other way round: given
    the lane the facing piece points down, find `c`'s closest approach to
    that piece WITHIN the lane, and profile the band there.

    The lane is grown by `2 * _SLACK`, which is not a fudge but the exact
    reach of `_ends_line_up`: that comparator grows each of its two spans
    by `_SLACK` before intersecting, so ink further out than two could
    not have contributed an overlap anyway. Widening by exactly that much
    is what makes this incapable of answering `None` where the recorded
    spans would have merged.

    The band is BOUNDED AT THE FACING COORDINATE and not merely capped
    `depth` beyond it — `edge <= p < edge + depth`, both sides. The lane
    is what located `edge`, so a piece with ink further out on the same
    axis OUTSIDE the lane still has it, and a one-sided test hands that
    ink back: on the back-loop it put the back run's own columns into the
    top run's end and answered with the whole 71-row height. Caught by
    the pin refusing to flip, which is the pair working as designed.

    Within the band the span is then measured over ALL of `c`'s ink in
    the stroke it found and not just the lane's share, equally
    deliberately. Clipping the span to the lane would make every
    comparison self-fulfilling — a span cut to the lane overlaps the lane
    by construction — and on the back-loop's severed twin that is the
    difference between refusing and merging two parallel runs 68 rows
    apart. So the restriction is by CONTIGUITY, which is a property of
    the ink, and not by the lane, which is the question being asked.

    Args:
        c: The component to ask, with `bbox` and `ink`.
        side: Which of `c`'s sides faces the gap.
        lane: The other piece's end span, on the cross axis.

    Returns:
        The inclusive cross-axis span of `c`'s ink at that end, or None
        when `c` has no ink in the lane at all — no ink facing the lane
        is no end facing it, and there is nothing to compare.
    """
    axis, low = _SIDE_AXIS[side]
    lo, hi = lane[0] - 2 * _SLACK, lane[1] + 2 * _SLACK
    inside = [p for p in c["ink"] if lo <= p[1 - axis] <= hi]
    if not inside:
        return None
    x0, y0, x1, y1 = c["bbox"]
    depth = _side_band((x1 - x0 if axis == 0 else y1 - y0) + 1)
    if low:
        edge = min(p[axis] for p in inside)
        band = [p for p in c["ink"] if edge <= p[axis] < edge + depth]
    else:
        edge = max(p[axis] for p in inside)
        band = [p for p in c["ink"] if edge - depth < p[axis] <= edge]
    # An end is ONE stroke's cross-section, so it is CONTIGUOUS, and on a
    # doubled-back piece that is the difference between an answer and the
    # component's height: the back run crosses under the top run's own
    # columns, so the band there holds two strokes 67 rows apart and the
    # bare min/max reads the pair as one 71-row end. Take the run holding
    # the ink that located `edge` and leave the rest of the band alone.
    # Contiguous to within `_SLACK` — a 1px hole is the anti-aliasing
    # budget the comparator already spends, not a second stroke.
    qs = sorted({p[1 - axis] for p in band})
    i = qs.index(next(p[1 - axis] for p in inside if p[axis] == edge))
    lo_i = hi_i = i
    while lo_i > 0 and qs[lo_i] - qs[lo_i - 1] - 1 <= _SLACK:
        lo_i -= 1
    while hi_i < len(qs) - 1 and qs[hi_i + 1] - qs[hi_i] - 1 <= _SLACK:
        hi_i += 1
    return (qs[lo_i], qs[hi_i])


def _relocated_ends_line_up(a: dict[str, Any], near: str,
                            b: dict[str, Any], far: str) -> bool:
    """Do the two pieces line up once the vague end is looked for again?

    The doubled-back arm of `_completed_by_eye`, kept out of it so the
    cheap path reads as the two lines it is. Only the piece with the
    WIDER recorded end is relocated, and only against the narrower one's
    lane — see that function's note for the over-merge the other
    direction reopens. Equal widths qualify both ways, which is the
    ordinary case for two stroke ends and is what makes this independent
    of the order the caller happened to pair the components in.

    Args:
        a: One component, whose `near` side faces the gap.
        near: `a`'s facing side.
        b: The other component, whose `far` side faces the gap.
        far: `b`'s facing side.

    Returns:
        True if either relocation reads as one stroke.
    """
    for anchor, a_side, other, o_side in ((a, near, b, far),
                                          (b, far, a, near)):
        lane, wide = anchor["ends"][a_side], other["ends"][o_side]
        if lane[1] - lane[0] > wide[1] - wide[0]:
            continue    # the anchor is the vaguer of the two ends
        end = _facing_end(other, o_side, lane)
        if end is not None and _ends_line_up(lane, end):
            return True
    return False


def _ends_line_up(sa: tuple[int, int], sb: tuple[int, int]) -> bool:
    """Do two facing ends' ink share enough of a lane to read as one run?

    Each end is grown by `_SLACK` first — the same one pixel of tolerance
    the comparator spends on the ink — and then the overlap must be at
    least half the WIDER of the two. A ratio and not an absolute, for the
    reason the original one-row rule was written to avoid: a threshold in
    pixels moves with the build's anti-aliasing, while fattening BOTH
    ends of a genuine continuation leaves this at ~1.0 whatever the
    stroke weight. What it costs a spurious merge is proportional too —
    the elbow scene's pair missed by 11 rows, so 11 rows of fattening
    silenced its mutant under the old rule, where under this one the
    2-row end has to grow to ~53.

    Args:
        sa: One end's inclusive cross-axis span, `(lo, hi)`.
        sb: The facing end's span, on the same axis.

    Returns:
        True if a reader continues one into the other.
    """
    a0, a1 = sa[0] - _SLACK, sa[1] + _SLACK
    b0, b1 = sb[0] - _SLACK, sb[1] + _SLACK
    overlap = min(a1, b1) - max(a0, b0) + 1
    return overlap * 2 >= max(a1 - a0, b1 - b0) + 1


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


def ablation_findings(elements: list[dict],
                      arrow_ids: Iterable[str]) -> list[dict]:
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

    Returns:
        Findings shaped like `test_mutants.collect_findings` output:
        `{"check", "element", "magnitude", "direction", "raw"}`.
        `ablation_existence` carries magnitude 0.0 (the pixels it changed);
        `ablation_continuity` carries the count of pieces the connector
        READS as — components a reader completes across a gap are one
        piece, per `_completed_by_eye`.
    """
    full = _shot(elements)
    w, h, _pix = read_png_gray(full)
    findings: list[dict] = []
    for eid in arrow_ids:
        blobs, _dw, _dh, residual = tolerant_diff_mask(
            _shot(elements, hide=(eid,)), full)
        if not blobs:
            findings.append({
                "check": "ablation_existence", "element": eid,
                "magnitude": 0.0, "direction": None,
                "raw": "removing %s changed no pixels — it is in the "
                       "model but not in the picture" % eid})
            continue
        strokes = _reader_strokes(_delta_components(w, h, blobs, residual))
        if len(strokes) >= 2:
            findings.append({
                "check": "ablation_continuity", "element": eid,
                "magnitude": float(len(strokes)), "direction": None,
                "raw": "%s's ink comes apart in %d separated pieces %s — "
                       "something drawn over it severs the run"
                       % (eid, len(strokes),
                          [c["bbox"] for g in strokes for c in g])})
    return findings


# ---------------------------------------------------------------------------
# THE CLIENT TIER (v0.9 task 50): ablation against the picture the USER sees.
#
# The third rendering path in this harness, and the first one that is not
# `render_svg`'s. Tier 1 is `canvas.render_svg`'s markup. Tier 2 is chromium
# rasterizing THAT markup. This tier hands the scene to the real Excalidraw
# client — the committed bundle the server serves, driven through the
# screenshot request/complete protocol `canvas.py` already ships — and reads
# the PNG the app's own `exportToBlob` produces. It is the same picture the
# user's "Export PNG" button makes, so what it measures is the product's
# output rather than this harness's model of it.
#
# WHY IT HAD TO EXIST. Tiers 1 and 2 share a ceiling by construction: a defect
# where `render_svg` omits or misstates content is inherited by the raster of
# it, never caught. `test_mutant_opacity_ghost_is_invisible_to_tier_one` sat
# `expectedFailure` for exactly one version of that — `render_svg` never emits
# `opacity`, so a node at 0% is invisible on the canvas and still ink in the
# export, and the existence check's guarantee was "not in the EXPORT", not
# "not in the PICTURE". The client honours opacity in both of its export
# paths, so through this tier the ghost's ablation delta is not merely small
# but EMPTY, and the mutant flips. The ceiling is not lowered here, it is
# escaped: this path shares no code with `render_svg` at all.
#
# WHAT IT COSTS, measured on this machine. A cold session is ~6s wall —
# scratch project, artifacts through the real `Store.apply_batch`, server up,
# ONE chromium, teardown — and the marginal cost of the Nth ablation inside it
# is ~0: nine renders complete simultaneously at t=1.8s because the app
# services every pending request in one poll pass. Warm it costs nothing at
# all; renders join the same content-addressed cache tier 2 uses, keyed by
# `_client_cache_key`. That is cheaper per ablation than tier 2, which spawns
# a chromium per distinct document.
#
# THE THREE MECHANISMS, none of them optional:
#
# 1. ANCHORS (`_anchored`). The client frames an export at the scene's own
#    bbox plus padding, and there is no viewport argument to splice the way
#    `_framed_svg` splices tier 1's `<svg>` tag. So every variant carries two
#    tiny `role: decoration` squares placed OUTSIDE the full scene's extremes.
#    Ablation only ever removes elements, so the full scene's bbox bounds every
#    variant's, and anchors derived from it pin all of them to one pixel grid
#    — correct by construction rather than by luck. Their own ink is identical
#    in every shot (`canvas.det_seed` derives the sketchy stroke's seed from
#    the element id) and cancels in the diff.
#
# 2. A SCRATCH PROJECT STARTED `--no-browser`. Not hygiene — correctness. A
#    connected tab STEALS these requests: the spike behind this tier started a
#    server without `--no-browser`, the user's LibreWolf answered every
#    screenshot request, and it returned vertical stripe garbage at 0.033
#    bytes/px, matching the corruption signature `canvas.cmd_snapshot` already
#    documents. A browser THIS code launches renders correctly; a browser it
#    merely trusts does not. The scratch root also means no live project can
#    be reached by an ablation run.
#
# 3. A STRUCTURAL CONTROL, at the CALLER. The tempting guard is
#    `canvas.validate_png`'s bytes-per-pixel floor, and it is the wrong
#    instrument here: valid renders in this tier measure 0.028-0.058 bpp,
#    straddling `cmd_snapshot`'s `min_bpp=0.05`, because a sparse mutant scene
#    legitimately IS thin. So the tier asserts nothing about density, and every
#    test that reads an EMPTY delta as evidence must ablate a known-visible
#    element in the same call and pin that its delta is not empty. Without
#    that, a render that silently failed and a ghost that is really invisible
#    produce the same finding.
# ---------------------------------------------------------------------------

# The tier-2 flags plus the two the product's own headless launcher adds
# (`canvas._mermaid_convert`): the shm one because a container-sized /dev/shm
# crashes a renderer that has to hold a real app, and the virtual-time budget
# because chromium given `--screenshot` otherwise shoots and exits before the
# app's effect has serviced anything.
CLIENT_FLAGS = (*CHROME_FLAGS, "--disable-dev-shm-usage",
                "--virtual-time-budget=60000")

# The window the app is laid out in. It does NOT frame the export — the client
# frames that at the scene bbox — but it decides what the app lays out at all,
# so it is a render input and lives in the key like tier 2's window does.
CLIENT_WINDOW = (1200, 900)

# How long a session waits for every shot before giving up. Generous against
# the 1.8s nine renders actually took: this deadline exists to turn a hung app
# into a named failure, not to police performance.
CLIENT_DEADLINE = 120.0

# Anchor squares: side, and how far OUTSIDE the scene's extremes they sit. The
# gap keeps their sketchy stroke clear of every element's ink, so an anchor can
# never merge with the delta it exists to frame.
_ANCHOR_SIDE = 8
_ANCHOR_GAP = 24


def _web_root() -> Path:
    """The committed web bundle the server serves — the app under measurement.

    Derived from `canvas.__file__` rather than from this file's own path so
    it names the bundle the SERVER will actually serve, which is the one
    whose pixels come back.

    Returns:
        The `web/` directory beside `canvas.py`.
    """
    return Path(canvas.__file__).resolve().parent / "web"


@functools.lru_cache(maxsize=1)
def _bundle_identity() -> str:
    """The app bundle as something that CHANGES when the app does.

    New territory for the key, and the generalisation of the lesson the
    perf review filed as F2: hashing the SVG fragment while the HTML
    wrapper around it moved pixels left a hole, because the key covered
    the part this file authored rather than everything the browser drew.
    Here almost none of what the browser draws is authored in this repo's
    Python at all — the drawing is made by `exportToBlob` inside a 1.4 MB
    Vite bundle — so a key without the bundle in it would happily serve a
    render made by a DIFFERENT Excalidraw across a frontend rebuild. That
    is the same class as serving build 151's pixels for build 131, one
    layer further out.

    Path, size and mtime per file, as `_browser_identity` does and for the
    same reason: the tree is 21 MB across 369 files and hashing its
    contents would cost more per render than the render. A false MISS
    after a `git checkout` is harmless — a key field can only split
    entries, never merge them — and the field is stronger than a stat
    normally is here, because Vite content-addresses its own filenames, so
    a rebuild that changes any chunk changes a NAME in this walk.

    Memoized for the process: the walk is ~30ms, which is nothing once and
    real per render. Call `_bundle_identity.cache_clear()` after moving
    the bundle under a running interpreter — the tests below do.

    Returns:
        A 16-hex-character digest of the served tree.
    """
    root = _web_root()
    digest = hashlib.sha1()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            st = path.stat()
            digest.update(("%s\0%d\0%d\0"
                           % (path.relative_to(root), st.st_size,
                              st.st_mtime_ns)).encode("utf-8"))
    return digest.hexdigest()[:16]


def _client_cache_key(elements: list[dict]) -> str:
    """The content address of one CLIENT render.

    Six fields. Tier 2's `_cache_key` can hash the finished document
    because it MAKES that document; this path hands elements to a server
    and a bundle and gets pixels back, so the key has to name the whole
    chain that turns one into the other:

    - **The app bundle** (`_bundle_identity`) — the code that actually
      draws. The field tier 2 has no analogue of.
    - **`canvas.own_source_hash()`** — the product's own fingerprint of
      `canvas.py`, which is what normalizes these elements into the stored
      scene the client is handed. It stands in the same place tier 2's
      hashed markup does: there, a `render_svg` change is covered BY
      CONSTRUCTION because the markup is its output and is hashed; here
      the transform's output never passes through this function, so the
      transform is keyed by identity instead. Deliberately blunt — any
      edit to `canvas.py` misses the whole client cache. That is a few
      seconds on a file this tier renders a handful of scenes through,
      against the alternative of re-deriving the pipeline's output here
      and keying on a SECOND implementation of it, which is how the two
      would drift.
    - **The browser** (`_browser_identity`), **`CLIENT_FLAGS`** and **the
      window** — the same three tier 2 keys on, for the same reasons.
    - **The scene**, canonically serialized. Different drawing, different
      key. `sort_keys` because dict order is not part of the drawing, and
      NO `default=` fallback because a value JSON cannot represent must
      raise here rather than be stringified: two distinct objects with one
      `str()` would MERGE two cache entries, and the whole discipline of
      this key is that a field may only ever split them. An element that
      cannot be serialized could not have been stored either.

    What it does NOT cover is what `render_cache_dir` already says it does
    not: system fonts. This path is MORE exposed to that than tier 2,
    because the client loads its own web fonts and lays text out with
    `ctx.measureText`. `rm -rf ~/.cache/wysiwyg-grilling/render` remains
    the whole recovery procedure.

    Args:
        elements: The variant scene, anchors included, exactly as it will
            be handed to `Store.apply_batch`.

    Returns:
        A 16-hex-character digest, used as the cache entry's filename.
    """
    return hashlib.sha1(
        ("client\0%s\0%s\0%s\0%s\0%dx%d\0%s"
         % (_bundle_identity(), canvas.own_source_hash(), _browser_identity(),
            "\0".join(CLIENT_FLAGS), CLIENT_WINDOW[0], CLIENT_WINDOW[1],
            json.dumps(elements, sort_keys=True)))
        .encode("utf-8")).hexdigest()[:16]


def _drawn_corners(e: dict) -> list[tuple[float, float]]:
    """Where ONE element's ink lands — advance and rotation included.

    Three bounds unioned in the element's own upright frame, because each
    one holds ink the others let out:

    - The STORED box, which is the honest bound for every class
      Excalidraw paints inside its own rectangle — AND ONLY THOSE. It is
      not contributed for `arrow`, `line` or `freedraw`, which is a
      correction and not a tidy-up: a polyline's stored box is a summary
      of its points that holds no ink of its own, so unioning it in was
      harmless while everything was upright and became the whole defect
      once things could turn. Turning those four corners reproduces the
      AABB-of-AABB exactly, so dropping the points-rotation without
      dropping this would have fixed nothing. `canvas.ink_extent` has
      never read the box for these classes; now neither does this.
    - The `points` polyline, which overhangs the stored box on the three
      point-strung classes — `canvas.ink_extent`'s own note, and the one
      widening this function already made before v0.9 TASK-FRAMING.
    - `canvas.ink_extent` OF THIS ELEMENT ALONE, which is the product's
      bound and the only one of the three that knows what a string's real
      advance is. Read rather than restated: a text's drawn extent is a
      wrap rule plus a metrics table, and a second spelling of it here is
      exactly how the frame this harness measures in and the frame the
      export is made in would come to describe different pictures. The
      numeric fields are coerced on the way in so this keeps
      `_scene_bbox`'s tolerance for a half-built element; a DELETED
      element answers `None` there and falls back to the bounds above,
      which is what this function already did with it. `angle` is zeroed
      on the way in AND ONLY THERE: since v0.9 TASK-MICROFIX
      `ink_extent` turns its own answer, so passing the angle through
      would rotate this element twice and put the harness's frame
      somewhere the export's is not. It is asked for the UNROTATED drawn
      extent, and the turn below is applied once, here.
      NOT CONTRIBUTED FOR THE POINT-STRUNG CLASSES either, and for the
      same reason as the stored box: for those, `ink_extent` IS the
      points' span, so its corners tell this function nothing the points
      have not already said — while under rotation those four corners
      are an unrotated AABB whose turn reproduces the very
      AABB-of-AABB being removed. Both exclusions were needed; removing
      only one left the harness still disagreeing with the client, which
      is how it was caught.

    Then `angle`, about the element's STORED centre — where Excalidraw
    turns it, and deliberately not the centre of the painted box, which
    for an overhanging string is a different point and would swing the
    glyphs somewhere no renderer draws them. `canvas._turned_points`
    reads the same centre for the same reason; the two must not drift.

    EACH CANDIDATE POINT IS TURNED, not the box they span, and the
    difference is a defect this function shipped for one commit. Turning
    the union box gives an AABB of an AABB: on a 45-degree diagonal arrow
    that reads 282.8px wide where the client reads 0.0, because
    `getLinearElementRotatedBounds` turns the PATH and freedraw turns its
    POINTS. Being wrong the same way as `canvas.ink_extent` would not
    have saved it — the two would have agreed with each other and with
    nothing the client draws, which is the one failure mode a
    harness-versus-product consistency argument cannot see. Both were
    corrected together (v0.9 TASK-MICROFIX fix round 1).

    The rotation is `test_mutants._painted_corners`' arithmetic in the
    same order, which is not a stylistic choice: that function is the
    reference the angle pins measure against, and they assert the
    overhang is EXACTLY zero. Two spellings of one rotation agree to
    within a float epsilon, not to the bit, and an epsilon is not zero.

    Args:
        e: One element. Missing or `None` numeric fields read as 0, so a
            scene may carry an element that has not been positioned yet.

    Returns:
        The drawn box's corners — two when the element is upright, four
        when it is turned. Callers only ever min/max over them.
    """
    x, y = float(e.get("x") or 0), float(e.get("y") or 0)
    w, h = float(e.get("width") or 0), float(e.get("height") or 0)
    box = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    strung = e.get("type") in ("arrow", "line", "freedraw")
    cand = []
    for px, py in (e.get("points") or []):
        cand.append((x + float(px), y + float(py)))
    if not strung:
        cand += box
        drawn = canvas.ink_extent(
            [dict(e, x=x, y=y, width=w, height=h, angle=0)], pad=0)
        if drawn:
            dx1, dy1 = drawn[0] + drawn[2], drawn[1] + drawn[3]
            cand += [(drawn[0], drawn[1]), (dx1, drawn[1]),
                     (dx1, dy1), (drawn[0], dy1)]
    # nothing contributed — a DELETED element, or a polyline with no
    # points — falls back to the stored box rather than min() over []
    cand = cand or box
    ang = float(e.get("angle") or 0)
    if ang:
        cx, cy = x + w / 2.0, y + h / 2.0
        cand = [(cx + (px - cx) * math.cos(ang) - (py - cy) * math.sin(ang),
                 cy + (px - cx) * math.sin(ang) + (py - cy) * math.cos(ang))
                for px, py in cand]
    x0, y0 = min(p[0] for p in cand), min(p[1] for p in cand)
    x1, y1 = max(p[0] for p in cand), max(p[1] for p in cand)
    if not ang:
        return [(x0, y0), (x1, y1)]
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _scene_bbox(elements: list[dict]) -> tuple[float, float, float, float]:
    """The box a scene's INK occupies — the region `_anchored` frames.

    THE INK, not the stored geometry (v0.9 TASK-FRAMING). This function
    used to answer `x + width` widened by any `points`, and that box is
    not where the drawing is on two element shapes the corpus can build:
    a text whose stored width is an estimate narrower than its advance
    paints past it, and any element carrying `angle` paints somewhere the
    four unrotated numbers never mention. `_drawn_corners` is where both
    are read; this is the union over them.

    Why it matters here and not merely as tidiness: `_anchored`'s single
    promise is that every variant of one ablation run frames the SAME
    region, and it keeps that promise by placing anchors `_ANCHOR_GAP`
    outside this box. Twenty-four pixels is the whole margin. A box that
    under-reports by more than that puts real ink outside the framed
    region, differently in each variant, and every delta measured through
    the run is then taken between two differently-cropped pictures.

    Args:
        elements: The scene. An empty list, or one with no positioned
            element, yields a degenerate box at the origin.

    Returns:
        `(minx, miny, maxx, maxy)`.
    """
    xs, ys = [], []
    for e in elements:
        for cx, cy in _drawn_corners(e):
            xs.append(cx)
            ys.append(cy)
    if not xs:
        return (0.0, 0.0, 0.0, 0.0)
    return (min(xs), min(ys), max(xs), max(ys))


def _anchored(elements: list[dict],
              box: tuple[float, float, float, float]) -> list[dict]:
    """A scene with the two framing anchors added.

    `box` is passed in rather than computed here on purpose: every variant
    of one ablation run must get the FULL scene's box, not its own, or the
    export reframes the moment an extremal element is ablated and the two
    rasters stop being comparable. Taking it as an argument makes that a
    caller's decision that reads on the page instead of an invariant to
    remember.

    Args:
        elements: The variant scene.
        box: `(minx, miny, maxx, maxy)` of the FULL scene.

    Returns:
        A new list: the two anchors followed by the scene's elements.
    """
    minx, miny, maxx, maxy = box
    lo = (minx - _ANCHOR_GAP, miny - _ANCHOR_GAP)
    hi = (maxx + _ANCHOR_GAP - _ANCHOR_SIDE, maxy + _ANCHOR_GAP - _ANCHOR_SIDE)
    anchors = [el(id="abl-anchor-%s" % tag, type="rectangle", x=x, y=y,
                  width=_ANCHOR_SIDE, height=_ANCHOR_SIDE,
                  customData={"role": "decoration"})
               for tag, (x, y) in (("lo", lo), ("hi", hi))]
    return [*anchors, *elements]


def _canvas_cli(root: Path, *args: str) -> int:
    """Run one `canvas.py` subcommand against `root`, in its own process.

    A SUBPROCESS where the rest of this file reaches for `canvas.main` in
    process, and only for the two commands that touch the daemon. `start`
    opens the server's log and `Popen`s a detached child, and doing that
    inside the test runner leaves both the file handle and the unreaped
    `Popen` in the runner's own process — two `ResourceWarning`s per
    session, and a test runner slowly accumulating other people's daemons.
    A short-lived child inherits them and takes them with it when it exits.
    Everything else here still goes through `Store.apply_batch` directly,
    which starts nothing and leaks nothing.

    Args:
        root: The project root to pass as `--project`.
        *args: The subcommand and its flags.

    Returns:
        The process exit code. Output is captured and discarded; the
        caller reports failure from the code, and the server's own log
        is where a start failure explains itself.
    """
    return subprocess.run(
        [sys.executable, str(Path(canvas.__file__).resolve()),
         "--project", str(root), *args],
        capture_output=True, timeout=90).returncode


def _await_shots(project: canvas.Project, url: str, sids: dict[str, int],
                 proc: subprocess.Popen) -> dict[str, bytes]:
    """Poll until every requested screenshot has landed, or fail loudly.

    A request leaves `/api/state`'s `screenshot_requests` only once the
    server has written the PNG (the complete handler writes the file and
    THEN marks the request done), so the two conditions checked here
    cannot disagree in the dangerous direction. Both are checked anyway:
    the file is what gets read.

    THE BROWSER IS WATCHED AS WELL AS THE CLOCK, because a browser that has
    exited will service nothing more and waiting out `CLIENT_DEADLINE`
    would then be measuring only the deadline. That is not merely the crash
    case: `CLIENT_FLAGS` caps virtual time at 60s while the deadline is
    120s, so a slow session can legitimately outlive its own browser, and
    the difference is a failure in about a second against one in two
    minutes — with the browser named as the culprit either way.

    An exit gets ONE more collect pass before it is believed. The browser
    can quit in the moment between the server writing the last PNG and this
    loop noticing it, and a session that really did finish must not be
    reported as a dead browser.

    Args:
        project: The scratch project, for its `shots_dir`.
        url: The server's base URL.
        sids: Variant name to screenshot-request id.
        proc: The browser doing the rendering, polled for an early exit.

    Returns:
        Variant name to PNG bytes.

    Raises:
        RuntimeError: If the browser exited or the deadline passed with
            shots outstanding. Like `_browser`, this never degrades to a
            skip — a tier that was asked for and did not measure has
            failed, not passed.
    """
    shots: dict[str, bytes] = {}
    deadline = time.time() + CLIENT_DEADLINE
    exited = None
    while True:
        time.sleep(0.2)
        state = canvas.http_json(url + "api/state")
        waiting = {r["id"] for r in state.get("screenshot_requests") or []}
        for name, sid in sids.items():
            if name in shots or sid in waiting:
                continue
            hit = project.shots_dir / ("shot-%d.png" % sid)
            if hit.exists():
                shots[name] = hit.read_bytes()
        if len(shots) == len(sids):
            return shots
        if exited is not None:
            break  # the grace pass above found nothing more
        if proc.poll() is not None:
            exited = proc.returncode
            continue
        if time.time() >= deadline:
            break
    missing = sorted(set(sids) - set(shots))
    if exited is not None:
        raise RuntimeError(
            "client tier: the browser exited (rc=%s) with %d of %d renders "
            "outstanding (missing %s) — it will service nothing further, so "
            "this failed now rather than waiting out the %.0fs deadline"
            % (exited, len(missing), len(sids), missing, CLIENT_DEADLINE))
    raise RuntimeError(
        "client tier: %d of %d renders never came back within %.0fs "
        "(missing %s) — the app did not service the request, so nothing "
        "here measured anything"
        % (len(shots), len(sids), CLIENT_DEADLINE, missing))


def _client_session(variants: dict[str, list[dict]]) -> dict[str, bytes]:
    """Render every named scene through the real app, in ONE browser.

    The whole lifecycle, and it owns all of it: a scratch project, one
    artifact per variant through the product's own `Store.apply_batch`,
    the server up with `--no-browser`, a single chromium pointed at the
    app URL, and — in a `finally` that also covers a part-built project —
    the browser killed, the server stopped, and both the project tree and
    the runtime files it keeps OUTSIDE that tree removed.

    Elements go in through `apply_batch` rather than being written to the
    artifact file directly, so what the client draws is what the product
    would really have stored: `det_seed`'s per-id seeds, `normalize_z_order`'s
    banding, label binding, the lot. Measured on this file's own scenes,
    the batch changes no geometry.

    Args:
        variants: Name to scene. Names become artifact ids, so they must
            be distinct; nothing about a name reaches the picture, which
            is what lets the same scene appear twice under two names.

    Returns:
        Variant name to PNG bytes.

    Raises:
        RuntimeError: If the server did not start, or if `_await_shots`
            timed out. Propagates `_browser`'s when there is no chromium.
    """
    root = Path(_mkworkdir())
    project = canvas.Project(root)
    proc, serving = None, False
    try:
        project.ensure_tree()
        store = canvas.Store(project)
        revn = 0
        for name, els in variants.items():
            # revn from the RECORD rather than from a counter: `apply_batch`
            # is the authority on what the head is, and a counter that ever
            # disagreed with it would fail as a `StaleError` on the next
            # batch, naming a revision number instead of the assumption.
            record, _pin_only = store.apply_batch({
                "base_revn": revn, "artifact": name,
                "create": {"id": name, "name": name, "type": "wireframe",
                           "concept": "ablation", "concept_name": "Ablation"},
                "ops": [{"op": "add", "element": e} for e in els]})
            revn = record["revn"]
        # `--no-browser` is load-bearing, not tidy: a connected tab answers
        # these requests with corrupt readback. See mechanism 2 above.
        rc = _canvas_cli(root, "start", "--no-browser")
        if rc != 0:
            raise RuntimeError("client tier: the app server would not start "
                               "(rc=%s) — read %s" % (rc, project.log_path))
        serving = True
        url = project.read_state()["url"]
        sids = {name: canvas.http_json(url + "api/screenshot/request",
                                       payload={"artifact": name})["id"]
                for name in variants}
        proc = subprocess.Popen(
            [_browser(), *CLIENT_FLAGS,
             "--screenshot=%s" % (root / "client-headless.png"),
             "--window-size=%d,%d" % CLIENT_WINDOW, url],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return _await_shots(project, url, sids, proc)
    finally:
        if proc is not None:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
        if serving:
            # A `stop` that hangs must not become this session's exception.
            # `_canvas_cli` runs with a timeout, and letting `TimeoutExpired`
            # out of a `finally` does two bad things at once: it REPLACES
            # whatever went wrong inside the try — which is the useful
            # exception, `_await_shots`' named failure — and it skips the
            # three cleanup lines below, leaking the scratch project and the
            # runtime files it keeps outside it. That leak is the exact
            # lifecycle failure this fixture exists to prevent.
            #
            # Suppressing rather than REORDERING the cleanup ahead of the
            # stop, which is the other obvious shape and is wrong:
            # `_clear_runtime` deletes the state file `stop` reads to find
            # the server, so cleaning up first would break every stop instead
            # of the rare hanging one. The residue of this choice is that a
            # genuinely wedged daemon outlives the run; that is a worse
            # outcome than a clean stop and a better one than losing the
            # exception that explains why we are here.
            with contextlib.suppress(subprocess.SubprocessError):
                _canvas_cli(root, "stop")
        shutil.rmtree(project.shots_dir, ignore_errors=True)
        _clear_runtime(root)
        shutil.rmtree(root, ignore_errors=True)


def _client_shots(variants: dict[str, list[dict]]) -> dict[str, bytes]:
    """The cached front of `_client_session` — hits cost no session at all.

    Every variant is addressed before anything starts, and only the misses
    are built into a session. A fully warm call starts no server and no
    browser, which is the same promise `_rasterize` makes for tier 2 and
    matters more here: the session, not the render, is what costs.

    Args:
        variants: Name to scene, as `_client_session` takes them.

    Returns:
        Variant name to PNG bytes. Propagates `_client_session`'s
        `RuntimeError` when a miss could not be rendered.
    """
    cache = render_cache_dir()
    keys = {name: _client_cache_key(els) for name, els in variants.items()}
    shots, missing = {}, {}
    for name, els in variants.items():
        png = cache / ("client-%s.png" % keys[name])
        if png.exists():
            shots[name] = png.read_bytes()
        else:
            missing[name] = els
    if missing:
        for name, data in _client_session(missing).items():
            _atomic_write(cache / ("client-%s.png" % keys[name]), data)
            shots[name] = data
    return shots


def client_ablation_findings(elements: list[dict],
                             ids: Iterable[str]) -> list[dict]:
    """`ablation_findings`, asked of the client's own rendering.

    Shape-compatible with its tier-1 sibling and deliberately built out of
    the same parts below the render: `tolerant_diff_mask`, `_delta_components`
    and `_reader_strokes` are unchanged and already correct, so the ONLY
    thing that differs between the two functions is which renderer drew the
    pixels. That is what makes a disagreement between them evidence about
    the renderer rather than about the diff.

    The two functions are siblings, not replacements. Tier 1 answers "is
    this in the export"; this answers "is this in the picture". Both are
    worth having — tier 1 is free, runs on every scene in this file, and
    is the only one of the two that can be asked about markup — and the
    mutants that tell them apart now have somewhere to live.

    Args:
        elements: The full scene, WITHOUT anchors; this adds them.
        ids: Ids to ablate, one at a time.

    Returns:
        Findings shaped exactly like `ablation_findings`' output, with the
        same two checks and the same magnitudes. Propagates
        `_client_shots`' `RuntimeError` when a render did not come back.
    """
    ids = list(ids)
    box = _scene_bbox(elements)
    variants = {"full": _anchored(elements, box)}
    for eid in ids:
        kept = [e for e in elements if e.get("id") != eid]
        variants["abl-%s" % eid] = _anchored(kept, box)
    shots = _client_shots(variants)
    full = shots["full"]
    w, h, _pix = read_png_gray(full)
    findings: list[dict] = []
    for eid in ids:
        blobs, _dw, _dh, residual = tolerant_diff_mask(shots["abl-%s" % eid],
                                                       full)
        if not blobs:
            findings.append({
                "check": "ablation_existence", "element": eid,
                "magnitude": 0.0, "direction": None,
                "raw": "removing %s changed no pixels in the CLIENT's render "
                       "— it is in the model but not in the picture" % eid})
            continue
        strokes = _reader_strokes(_delta_components(w, h, blobs, residual))
        if len(strokes) >= 2:
            findings.append({
                "check": "ablation_continuity", "element": eid,
                "magnitude": float(len(strokes)), "direction": None,
                "raw": "%s's ink comes apart in %d separated pieces %s in the "
                       "CLIENT's render — something drawn over it severs the "
                       "run" % (eid, len(strokes),
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


def _label_over_foreign_stroke(foreign: bool) -> list[dict]:
    """A straight labelled run, optionally crossed by ANOTHER arrow's ink.

    Curator batch 23 item 9, from task 48 §9 candidate 3 (the corpus
    instance is `argus-r4-arm3/enrichment-pipeline`'s `e-edgar-sent`),
    2026-08-15. `render_svg` paints a bound label's opaque backdrop to
    stand the arrow's own stroke out from under its text; ablating that
    arrow removes the backdrop with it, so anything ELSE the backdrop was
    covering comes back — and that recovered ink joins the delta as
    pieces the connector never owned. `ablation_findings`' own docstring
    has named this reachable since it was written; nothing reached it,
    because the bbox over-merge used to swallow the extra pieces before
    they were counted. Task 48's narrowing stopped swallowing them.

    THE PAINT ORDER IS THE SCENE, and it is the part that took the
    longest to get right, so it is written down rather than left in the
    coordinates. The foreign arrow must be emitted BEFORE the label, or
    it is drawn on top of the backdrop and is never covered at all — a
    scene that looks identical in every stored coordinate and reproduces
    nothing. `render_svg` paints in array order, so this builder returns
    the foreign pair first.

    THE CROSSING POINT is likewise deliberate: the backdrop runs
    x 219..261, y 88..112, and the label's glyphs occupy roughly the top
    two thirds of that. The foreign run dips to y=108, in the band the
    backdrop covers and the glyphs do not, so the recovered ink is a
    clean 40x2 fragment instead of a few pixels between letters. A
    crossing through the glyphs is swallowed by the comparator's own
    tolerance and reproduces nothing either.

    Args:
        foreign: True to add the crossing pair `n1`/`n2`/`a2`. False is
            the same labelled run alone — the pole where the only thing
            the backdrop covers is the arrow it belongs to.

    Returns:
        The scene in paint order: the foreign pair (if any), then rects
        `s1`/`s2`, arrow `a1`, label `t1`.
    """
    els: list[dict] = []
    if foreign:
        els += [el(id="n1", type="rectangle", x=40, y=180, width=80,
                   height=40, customData={"role": "node"}),
                el(id="n2", type="rectangle", x=360, y=180, width=80,
                   height=40, customData={"role": "node"}),
                el(id="a2", type="arrow", x=120, y=200, width=240,
                   height=0,
                   points=[[0, 0], [60, 0], [60, -92], [180, -92],
                           [180, 0], [240, 0]],
                   startBinding={"elementId": "n1", "focus": 0, "gap": 1},
                   endBinding={"elementId": "n2", "focus": 0, "gap": 1},
                   customData={"role": "edge"})]
    arr = el(id="a1", type="arrow", x=120, y=100, width=240, height=0,
             points=[[0, 0], [240, 0]],
             startBinding={"elementId": "s1", "focus": 0, "gap": 1},
             endBinding={"elementId": "s2", "focus": 0, "gap": 1},
             customData={"role": "edge"})
    arr["boundElements"] = [{"id": "t1", "type": "text"}]
    return [*els,
            el(id="s1", type="rectangle", x=40, y=80, width=80, height=40,
               customData={"role": "node"}),
            el(id="s2", type="rectangle", x=360, y=80, width=80, height=40,
               customData={"role": "node"}),
            arr,
            el(id="t1", type="text", x=210, y=90, width=60, height=20,
               text="sent", fontSize=16, fontFamily=1, textAlign="center",
               verticalAlign="middle", containerId="a1",
               originalText="sent")]


def _with_liveness_ghost(elements: list[dict]) -> list[dict]:
    """The scene plus a zero-extent element that MUST be reported missing.

    The positive half of every silence-shaped ablation assertion in this
    file, added by curator batch 23 after a mutation sweep showed the
    problem it fixes (2026-08-15). Three neighbours here asserted a
    connector's silence and nothing else, and `ablation_findings` patched
    to return `[]` — a detector silent about everything — passed all
    three. An absent finding is exactly what a dead detector produces, so
    no assertion about what is MISSING can show the instrument spoke.
    Doctrine §3 makes this the neighbour's whole job: it is the live half
    that stops a dead detector hiding inside the expectedFailure mask,
    and it was not doing it.

    A 0x0 rectangle draws nothing, so ablating it changes no pixels and
    `ablation_existence` fires for it — the same arithmetic
    `test_ablation_existence_fires_on_invisible_element` proves. Riding it
    along in the SAME call means the liveness check runs through the very
    entry point under test.

    NOT MEASURED OFF THE RASTER, deliberately, and this is the trap worth
    naming: a control that diffs `_shot` output directly would read as a
    correct fix and prove the wrong thing, because it never calls
    `ablation_findings` and so closes a dead-SUBSTRATE hole while leaving
    the dead-DETECTOR one open. Task 50 reached for exactly that shape
    first, independently, which is why it is written down here.

    The ghost sits at the first element's origin so it cannot move the
    drawing's bounding box — verified on each scene that uses it, since a
    ghost outside the box would shift the viewBox and change the very
    pixels the caller is measuring.

    Args:
        elements: The scene. Must be non-empty; the ghost borrows its
            first element's origin.

    Returns:
        The scene with `z1` appended.
    """
    first = elements[0]
    return [*elements, el(id="z1", type="rectangle", x=first.get("x", 0),
                          y=first.get("y", 0), width=0, height=0,
                          customData={"role": "node"})]


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


class AblationLiveness:
    """The `assertDetectorSpoke` half of `_with_liveness_ghost`.

    A mixin rather than a method on one class because curator batch 24
    (2026-08-16) found the same dead-pin shape in three more classes —
    the composed content and furniture visibility pairs and the paint
    order pin — and the alternative was three copies of one assertion.
    Mixed in ahead of `unittest.TestCase` so the assertion helpers it
    calls resolve normally.

    It carries no scene knowledge on purpose: everything about WHICH
    scene is in `_with_liveness_ghost`, so a class can adopt the pair
    without inheriting anything else about how `TestRenderMutants`
    builds its drawings.
    """

    def assertDetectorSpoke(self, finds: list[dict]) -> None:
        """Fail unless `ablation_findings` reported the liveness ghost.

        The other half of every silence-shaped ablation assertion in this
        file, paired with `_with_liveness_ghost` — read that function for
        the measurement that made it necessary. A test asserting only
        that a connector drew no findings cannot tell a whole connector
        from a detector that has stopped answering, and four tests were
        in exactly that state until curator batch 23 swept for it, with
        eight more found by the mortality spike a day later.

        The whole projection is asserted rather than "z1 appears
        somewhere", so this also fails if ablation broke in the other
        direction and started reporting everything missing.

        Args:
            finds: Everything `ablation_findings` returned for a call
                whose ids included `"z1"`.
        """
        self.assertEqual(
            [(f["check"], f["element"]) for f in finds
             if f["element"] == "z1"], [("ablation_existence", "z1")],
            "the zero-extent ghost should be the one thing reported "
            "missing; got %s. Either the detector said nothing at all — "
            "in which case the silences above are about the instrument "
            "and not about the drawing — or ablation is reporting live "
            "elements as absent"
            % [(f["check"], f["element"]) for f in finds])


@unittest.skipUnless(RENDER, "render tier: set MUTANTS_RENDER=1 "
                             "(starts a headless browser)")
class TestRenderMutants(AblationLiveness, unittest.TestCase):
    """The two render-tier detectors, each proven and each held silent."""

    def test_ablation_existence_fires_on_invisible_element(self) -> None:
        """An element whose ablation changes no pixels is not in the picture.

        The ghost is zero-extent as well as fully transparent because the
        tier-1 export has no notion of opacity — see the expected failure
        below, which pins exactly that gap.
        """
        scene = tm._diamond_stage()
        ghost = el(id="g1", type="rectangle", x=300, y=300, width=0,
                   height=0, opacity=0, customData={"role": "node"})
        finds = ablation_findings([*scene, ghost], ["g1"])
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
        finds = ablation_findings(scene, ["n1"])
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

        The ghost is curator batch 24's repair (2026-08-16). This was the
        purest instance the mortality spike found — a bare
        `assertEqual(ablation_findings(...), [])` and nothing else — so
        `ablation_findings` patched to return `[]` passed it, and the
        "detector that reports every element as missing" this docstring
        claims to stand against was only half the hazard. The other half
        is a detector that reports nothing at all, and it took the same
        one-line fix three siblings in this class already carried.
        """
        scene = _with_liveness_ghost(tm._styled_scene())
        finds = ablation_findings(scene, ["n1", "z1"])
        self.assertEqual([f for f in finds if f["element"] == "n1"], [])
        self.assertDetectorSpoke(finds)

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
        finds = ablation_findings(scene, ["g0", "a1"])
        # the extremal ghost draws nothing: existence fires, for it alone
        self.assertEqual([(f["check"], f["element"]) for f in finds],
                         [("ablation_existence", "g0")])

    def test_mutant_opacity_ghost_is_invisible_to_tier_one(self) -> None:
        """A 0%-opacity node is invisible on the canvas but ink in tier 1.

        FLIPPED by v0.9 task 50, and the name is the finding rather than
        the defect: the ghost really IS invisible to tier one, permanently,
        and what changed is that the harness now has an instrument that can
        say so. `canvas.render_svg` never reads `opacity` — its `paint`
        emits fill, stroke and dash and nothing else — so an element the
        user cannot see still contributes ink to the export, ablating it
        changes pixels, and `ablation_existence` stays silent. Rasterizing
        that export in chromium inherits the blindness rather than curing
        it, which is what made this the file's longest-standing red: no
        arrangement of tiers 1 and 2 could reach it.

        The client tier can, because it shares no code with `render_svg`.
        Excalidraw honours opacity in both of its export paths, so through
        the app's own `exportToBlob` the ghost's delta is not small but
        EMPTY — full and ablated hash identically — and the check fires.

        Three claims, in the order they have to hold:

        1. Tier 1 SPOKE, and what it reported missing was the liveness
           ghost and not the opacity ghost. It is the pin that keeps the
           module docstring's account of the ceiling honest, and if
           `render_svg` ever learns opacity this is the test that says so.

           The silence half of that claim used to stand alone, and it was
           measurably worth nothing: with `ablation_findings` stubbed to
           return `[]` this test PASSED, because an absent finding is
           exactly what a dead detector produces. Curator batch 23 swept
           the class for the same shape and built the fix this now shares
           — `_with_liveness_ghost` plus `assertDetectorSpoke`, whose
           docstrings carry the measurement. Joining their mechanism
           rather than keeping a private one is deliberate: two spellings
           of one control is how the next sweep misses one of them.

           Strengthening, not relaxing, and the diff should be read that
           way: every input the old line rejected this rejects too, plus
           the dead detector. Claims 2 and 3 below are untouched — the
           client half already paired a firing with its silence, which is
           why only this half needed the repair.
        2. The client tier fires on the ghost. The original assertion,
           unchanged, against the new instrument.
        3. The STRUCTURAL CONTROL, and without it the other two are worth
           nothing. A run where the app rendered nothing at all, or
           rendered the same bytes for every variant, would report an
           empty delta for the ghost exactly as a correct run does. So a
           known-visible node is ablated in the SAME call, and its delta
           must not be empty. Density is deliberately not what is checked
           here — see mechanism 3 in the tier's section comment.
        """
        scene = tm._diamond_stage()
        ghost = el(id="g1", type="rectangle", x=300, y=300, width=10,
                   height=10, opacity=0, customData={"role": "node"})
        full = _with_liveness_ghost([*scene, ghost])
        tier1 = ablation_findings(full, ["g1", "z1"])
        self.assertDetectorSpoke(tier1)
        self.assertEqual([f for f in tier1
                          if f["check"] == "ablation_existence"
                          and f["element"] == "g1"], [],
                         "tier 1 reported the opacity ghost missing — it "
                         "has learned about opacity, and this file's account "
                         "of what tiers 1 and 2 cannot see is now wrong")
        finds = client_ablation_findings(full, ["g1", "s1"])
        missing = [f["element"] for f in finds
                   if f["check"] == "ablation_existence"]
        self.assertIn("g1", missing)
        self.assertNotIn("s1", missing,
                         "ablating a VISIBLE node changed no pixels either, "
                         "so the empty delta above is evidence about the "
                         "render pipeline, not about the ghost")

    def test_ablation_continuity_neighbour_is_silent(self) -> None:
        """A label beside the arrow leaves the connector's delta whole.

        The comment this test used to carry — "an empty delta would
        satisfy that assertion too, so pin that the arrow really did
        leave the picture" — had the right worry and the wrong remedy,
        and curator batch 23 measured it: pairing the continuity silence
        with an EXISTENCE silence proves nothing, because a dead detector
        is silent about both. Stubbing `ablation_findings` to return `[]`
        passed this test unchanged. The ghost is the remedy that works;
        see `_with_liveness_ghost`.
        """
        finds = ablation_findings(
            _with_liveness_ghost(_elbow_with_label("beside")), ["a1", "z1"])
        self.assertEqual([f for f in finds if f["element"] == "a1"], [],
                         "the connector is not whole: %s"
                         % [f["raw"] for f in finds if f["element"] == "a1"])
        self.assertDetectorSpoke(finds)

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
        indistinguishable from switching the detector off. Which is
        exactly the sentence curator batch 23 had to make true of this
        test as well as of the exemption — it asserted two silences and
        survived a stubbed-out `ablation_findings`, so the witness was
        indistinguishable from the switch-off in the other direction.
        """
        finds = ablation_findings(
            _with_liveness_ghost(_elbow_with_label("run")), ["a1", "z1"])
        self.assertEqual([f for f in finds if f["element"] == "a1"], [],
                         "the bound-label idiom now reads as a severed "
                         "run: %s"
                         % [f["raw"] for f in finds if f["element"] == "a1"])
        self.assertDetectorSpoke(finds)

    def test_mutant_a_label_riding_foreign_ink_is_not_a_severed_run(self
                                                                    ) -> None:
        """WAS RED, curator batch 23 item 9. FLIPPED by v0.9 task 51.

        ROUTE 2 WAS TAKEN, and this is half of the record of it (the
        other half is `canvas.arrow_label_break`, which is the function
        the route created). `render_svg` no longer paints an opaque
        backdrop over whatever lies beneath a bound label; it masks the
        gap out of the labelled arrow's OWN stroke, which is the same
        mechanism the client uses and therefore the same picture by the
        same means rather than by opposite ones.

        WHY NOT ROUTE 1, measured at the fix rather than argued. Route 1
        — subtract the non-owned ink inside `ablation_findings` — closes
        this test and nothing else: the occlusion stays, so the EXPORT
        still hands an agent a picture in which one arrow has a hole
        punched in it wherever another arrow's label happens to sit, and
        that export is the only picture a headless agent ever sees of its
        own drawing. It is a product defect and not only an instrument
        artifact, so the instrument was the wrong place to close it. Route
        2 also collapses the tier-1/client divergence the pin below
        asserts, and takes with it a second defect nobody had listed: the
        r5-14 struck-through label was reachable by EMISSION ORDER alone
        (`TestPaintOrder`'s bound-label pair), because a backdrop painted
        before its arrow is painted straight back over. A gap cut out of
        the ink cannot be filled back in by ordering.

        THE COROLLARY HELD: nothing was mirrored into
        `client_ablation_findings`, which needed no change in either
        route.

        An ATTRIBUTION defect, and the reason it was worth its own mutant
        rather than a line in the continuity family: `a1` is a straight
        run with a label on it, which is `..._a_label_on_a_straight_run_
        is_silent`'s scene exactly and reads as one stroke. Add a foreign
        arrow that happens to pass beneath `a1`'s label, change nothing
        about `a1`, and the check speaks about `a1` — with a magnitude of
        2 and a third bbox in its evidence that belongs to `a2`.

        So the pieces are not miscounted; they are MISATTRIBUTED. The
        ablation removes the backdrop along with the arrow, the foreign
        stroke it was covering comes back, and the delta is no longer the
        connector's own ink. `_completed_by_eye` is not at fault and no
        change to it helps — measured: the recovered fragment sits at
        raster `(220, 67, 259, 68)` against `a1`'s stubs at rows 56-63,
        y-separated and x-disjoint from both, so every reasonable
        continuity predicate keeps it apart.

        TWO FIX ROUTES, AND THIS PIN PICKED NEITHER — deliberately, and
        the choice was the fixer's, made and recorded above. An earlier
        draft of this docstring named one — "subtract the ink that is not
        the ablated element's, in `ablation_findings`" — and that was
        over-specification of exactly the kind the sibling red two tests
        down refuses to commit (it asserts a span WIDTH rather than an
        expected pair, "because the fix's shape is not this pin's to
        choose"). The same discipline applies here and was not applied.
        The routes were:

        1. Subtract in `ablation_findings` — a second ablation of the
           backdrop alone, or an intersection against the element's own
           drawn extent. Closes this.
        2. Stop `render_svg` occluding — give the label a non-painted
           gap rather than an opaque rectangle over whatever lies
           beneath. Closes this AND removes a divergence from the client,
           which the parity section would also gain from.

        WHY ROUTE 2 WAS EVEN AVAILABLE, measured rather than reasoned:
        `client_ablation_findings` does NOT inherit this defect, on this
        exact scene, and the pin below asserts it. Both renderers drew
        the same picture — an arrow broken behind its bound label — by
        opposite means. `render_svg` painted an opaque backdrop OVER what
        lay beneath; the real client simply does not draw the arrow
        through the label's box. Only a painting mechanism occludes
        third-party ink, and only an occluding one can hand it back on
        ablation. So this was narrower than "ablation misattributes
        foreign ink": it was specific to tier 1's backdrop-faking, which
        is what made stopping the backdrop a complete fix rather than a
        patch on a symptom.

        A corollary for route 1, which nobody now has to take: do NOT
        mirror the subtraction into `client_ablation_findings`. There is
        nothing there to subtract, and dead code under a live docstring
        is worse than the gap it pretends to close.

        SCOPED TO THE ATTRIBUTION AND NOT TO THE MESSAGE. The `raw`
        string names `a2`'s bbox and it would be easy to assert on that,
        but the string is not the finding — a fix that corrected the
        prose while still counting foreign pieces would satisfy such a
        test. What must become true is that this check says nothing about
        `a1`, so that is what is asserted.

        THE LIVENESS CONTROL IS A FIRING, and this mutant's first version
        got that wrong in a way worth leaving on the record. It asserted
        `ablation_existence` silent alongside, reasoning that "the arrow
        left the picture" has to stay true for the continuity silence to
        mean anything. True, and not provable that way: a dead detector
        is silent about existence too, so the pair of silences is
        satisfied by the instrument being switched off. While this mutant
        is RED that is harmless — a dead detector turns it into an
        unexpected success, which unittest reports as a hard failure — but
        the day the attribution fix lands and it flips GREEN it would
        inherit the hole with nobody looking. The ghost is in place now so
        that the flip is a one-line decorator removal and nothing else.
        """
        finds = ablation_findings(
            _with_liveness_ghost(_label_over_foreign_stroke(True)),
            ["a1", "z1"])
        self.assertEqual(
            [f for f in finds if f["element"] == "a1"], [],
            "ablating a1 un-covered another arrow's stroke and the "
            "recovered ink was counted as a1's own: %s"
            % [f["raw"] for f in finds if f["element"] == "a1"])
        self.assertDetectorSpoke(finds)

    def test_neighbour_the_same_label_covering_only_its_own_arrow(self
                                                                  ) -> None:
        """The other pole: one variable, the foreign arrow, removed.

        The identical labelled run with nothing else in the scene, so the
        only thing `a1`'s backdrop covers is `a1`. Silent, correctly.
        That is what makes the red above about the FOREIGN ink rather
        than about this builder's label placement — without it, a red
        asserting silence would be satisfied by the label being parked
        somewhere harmless, and would tell nobody which of the two
        differences mattered.

        This pair is two Silences, which is the weaker neighbour shape,
        and the check's legitimate FIRING is proven a few tests down by
        `test_mutant_label_backdrop_severs_connector` on
        `_elbow_with_label("corner")` — asserted in every gated run, so
        `ablation_continuity` cannot be dead while these two pass.
        """
        finds = ablation_findings(
            _with_liveness_ghost(_label_over_foreign_stroke(False)),
            ["a1", "z1"])
        self.assertEqual([f for f in finds if f["element"] == "a1"], [],
                         "the labelled run reads as severed with no "
                         "foreign ink in the scene at all: %s"
                         % [f["raw"] for f in finds if f["element"] == "a1"])
        self.assertDetectorSpoke(finds)

    def test_the_client_reads_the_same_scene_as_one_whole_stroke(self
                                                                 ) -> None:
        """The client did not inherit the attribution defect above.

        Curator batch 23, added on Task 50's measurement and re-measured
        here before being written (2026-08-15). One scene, two renderers,
        opposite answers, and the CLIENT was the correct one: tier 1
        reported `a1` in 2 pieces with a third bbox belonging to `a2`,
        while the app's own export read `a1` as one whole stroke.

        THE DIVERGENCE IS CLOSED, and this pin is what closed it — not by
        being satisfied, which it always was, but by being the evidence
        that named which side was right. v0.9 task 51 took route 2 on
        the red above BECAUSE of this measurement: with two renderers
        disagreeing and only one of them occluding, the one that
        occludes is the one to change. Tier 1 now draws the gap the same
        way the client does and agrees with what this asserts, which is
        the outcome the "WHEN THIS FLIPS" paragraph below predicted for
        that route. The pin stays, as the standing statement that the
        two tiers agree here — a silent parity check is exactly what
        notices the day one of them starts painting a backdrop again.

        This was the cleanest available statement of why the client tier
        exists, and it is a different statement from the opacity ghost
        above. That one is a defect tier 1 CANNOT SEE; this was one tier
        1 reported FALSELY. A tier that only caught misses would be worth
        less than one that also catches inventions — and this instance is
        the one that paid for itself, because the false report is what
        the fix went and removed from the product.

        The mechanism, which is the transferable part: the two renderers
        produced the same picture by opposite means. `render_svg` painted
        an opaque backdrop over whatever lay beneath the label; the
        client draws no backdrop at all and simply omits the arrow
        through the label's box. Ablating the label on the client
        recovers glyphs and no rectangle. Only an occluding mechanism can
        hand third-party ink back on ablation, so only tier 1 could
        misattribute it — and tier 1 stopped occluding
        (`canvas.arrow_label_break`).

        THE LIVENESS CONTROL IS POSITIVE, AND IT HAD TO BE. This pin's
        first draft asserted `ablation_existence` SILENT for `a1` and
        called that the control, reasoning that silence there means the
        ablation moved pixels. It does not, and the mutation proof said
        so: with `client_ablation_findings` patched to return `[]`
        unconditionally — a detector silent about everything — that draft
        PASSED. An absent finding is exactly what a dead detector
        produces, so no assertion about what is missing can prove the
        instrument spoke.

        The control is therefore a firing, in the SAME call over the same
        renders: ablating the label `t1` must report its glyphs coming
        apart. That is structural rather than incidental — "sent" is
        separated ink at any size, so the pieces are there whatever the
        anti-aliasing does — and a detector returning nothing fails it.
        Measured at 3 pieces and asserted at >= 2, because the exact
        component count of four glyphs is a font fact and the claim being
        made is only "this instrument is awake".

        WHEN THIS FLIPS: it did not, under the route taken, and the
        prediction is left standing because it was made in advance and
        held. Route 1 (subtract in `ablation_findings`) would have left
        the client untouched. Route 2 (stop `render_svg` occluding) was
        taken and makes tier 1 agree with what this already asserted. It
        WOULD still break if someone reflexively mirrored a subtraction
        into the client and got it wrong, which is the corollary that red
        records; nobody has, and there is now nothing to mirror.
        """
        finds = client_ablation_findings(_label_over_foreign_stroke(True),
                                         ["a1", "t1"])
        self.assertEqual(
            [f for f in finds if f["element"] == "a1"], [],
            "the CLIENT now reports something about the labelled run, "
            "where tier 1's false severance is the only complaint this "
            "scene should draw — check whether the app started painting "
            "a label backdrop: %s"
            % [f["raw"] for f in finds if f["element"] == "a1"])
        glyphs = [f for f in finds if f["element"] == "t1"
                  and f["check"] == "ablation_continuity"]
        self.assertTrue(
            glyphs and glyphs[0]["magnitude"] >= 2,
            "ablating the label reported %s, so this run's client render "
            "produced nothing to measure and the silence above is "
            "evidence about the instrument rather than about the arrow"
            % (glyphs or "no continuity finding at all"))

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
        finds = ablation_findings(scene, ["a1"])
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

        The ghost is curator batch 24's repair (2026-08-16). Both
        assertions below are absences, and the second was carrying the
        first: "no existence finding" was read as proof the arrow drew
        something, when it is equally what a detector that has stopped
        answering reports. The spike measured it — this passed with
        `ablation_findings` stubbed to `[]` — so the claim that the
        PRODUCT places this label well needed an instrument demonstrably
        awake in the same call to mean anything.
        """
        scene = _with_liveness_ghost(_elbow_with_label("routed"))
        finds = ablation_findings(scene, ["a1", "z1"])
        self.assertEqual([f for f in finds if f["element"] == "a1"], [])
        self.assertDetectorSpoke(finds)

    def test_neighbour_back_edge_label_on_the_leg_is_silent(self) -> None:
        """A backdrop mid-way down the back edge's leg is the idiom, not a cut.

        The pole of the pair below, and the half that constrained the
        task 48 fix — it stayed green across the flip and is the reason
        the narrowing had to be the one it is. Both remnants here are
        L-shaped — the top run plus the leg's upper half, the leg's lower
        half plus the bottom run — so the cheap narrowing "an L-shaped
        remnant is never completed by eye" would over-fire on this scene
        and report the tool's own bound label as a severed connector.
        What makes it readable is that the two facing ENDS are collinear
        down the leg at x~358 and 25 raster rows apart, which is the
        property `_completed_by_eye` claims to test and, since the
        residual mask reaches it, does.

        The ghost is curator batch 24's repair (2026-08-16), and this
        test is where the general lesson is cheapest to see: the comment
        below used to read "silence is only meaningful if the arrow drew
        something at all" over an assertion that `ablation_existence`
        found NOTHING. That is a second absence introduced as a control,
        which is the exact shape guide rule 8 exists to refuse — and the
        mortality sweep confirmed it, passing this test with the detector
        stubbed dead.
        """
        scene = _with_liveness_ghost(_back_edge_with_label("leg"))
        finds = ablation_findings(scene, ["a1", "z1"])
        self.assertEqual([f for f in finds if f["element"] == "a1"], [])
        self.assertDetectorSpoke(finds)

    def test_mutant_l_shaped_remnant_hides_a_severed_back_edge(self) -> None:
        """FLIPPED by v0.9 WP4 (Task 48). Kept its red-era name.

        A remnant with a turn in it used to merge with a stub it never
        touched. Curator batch 14, from the Task 19 review (F5),
        2026-08-14. Same defect class as
        `test_mutant_label_backdrop_severs_connector` and the same
        picture — a bound label's opaque backdrop parked on an elbow, the
        run arriving, stopping, and resuming somewhere the eye cannot
        follow it to — but on a path with a second turn, and
        `ablation_continuity` said nothing.

        Why the extra turn was the whole scene: broken on the lower turn,
        this connector's ink comes back as an L (122,59,280,167) and a
        bottom stub (122,176,258,183). The two are separated in y by 9
        rows, which is the severance, and OVERLAP in x for 136 columns —
        not because they share a stroke, but because the L is 158 columns
        wide and swallows the stub's range whole. `_completed_by_eye`
        read that overlap as "the eye continues one into the other" and
        `_reader_strokes` merged them into one, so the finding was never
        emitted. The 2-segment `corner` scene escaped this only because
        two straight stubs give it two thin bboxes.

        What flipped it is the one thing the red-era docstring named:
        where the facing ends POINT, not how big the pieces are. The
        residual mask `tolerant_diff` used to discard is carried through
        to `_completed_by_eye` now, so this scene's L is measured at its
        bottom end — the leg's foot, `(279,280)` — against the stub's top
        end at `(122,258)`, which miss by 20 columns. The neighbour above
        is the same measurement on the same axis with the same label and
        overlaps fully, so the two scenes are 22 rows apart in the
        property the check claims to test rather than the wrong way round
        in an x-overlap.

        Magnitude 2.0 is the count of pieces a reader sees, asserted as a
        whole projection rather than by indexing so a regression that
        emitted nothing fails here by assertion and not by IndexError.
        """
        scene = _back_edge_with_label("turn")
        finds = ablation_findings(scene, ["a1"])
        self.assertEqual([(f["check"], f["element"], f["magnitude"])
                          for f in finds],
                         [("ablation_continuity", "a1", 2.0)])


@unittest.skipUnless(RENDER, "render tier: set MUTANTS_RENDER=1 "
                             "(starts a headless browser)")
class TestClientTierMechanisms(unittest.TestCase):
    """The two properties the client tier's arithmetic rests on.

    The tier's findings are only evidence if the renders behind them are
    comparable and repeatable, and neither is visible in a finding: an
    empty delta looks the same whether the ghost was invisible, the two
    shots were framed differently, or the app rendered the same bytes
    twice by accident. These ask both questions directly, in pixels.

    Both cost a real session (~6s cold) and neither can be answered from
    the cache, which is the point — see each docstring.
    """

    def test_ablating_an_extremal_element_still_lands_on_one_grid(
            self) -> None:
        """The anchors, measured: ablate the element that sets the bbox.

        `test_ablation_of_the_bbox_defining_element_still_reads` asks this
        of tier 1, where `_framed_svg`'s `<svg>`-tag splice pins the
        viewport. There is no viewport to splice here — the client frames
        an export at the scene's own bbox — so the whole defence is the
        two anchors `_anchored` adds OUTSIDE the full scene's extremes.

        The ghost sits at x=-100, further left than anything else in the
        scene, so it DEFINES minx while drawing nothing. With anchors, its
        removal moves no frame and the empty delta reads as what it is.
        Without them the ablated export reframes 100px narrower, every
        stroke in the picture lands somewhere new, and `tolerant_diff`
        either rejects the size mismatch outright or reports the entire
        drawing as this invisible element's contribution. Delete
        `_anchored`'s call and this test fails; the flip test would not,
        because its ghost is interior.

        The visible control is ablated in the same call for the reason
        given on the flip test, and here it does double duty: `s1` is the
        LEFTMOST visible element, so a run that reframed would report it
        wrongly too.
        """
        ghost = el(id="g0", type="rectangle", x=-100, y=300, width=10,
                   height=10, opacity=0, customData={"role": "node"})
        finds = client_ablation_findings([*tm._diamond_stage(), ghost],
                                         ["g0", "s1"])
        missing = [f["element"] for f in finds
                   if f["check"] == "ablation_existence"]
        self.assertEqual(missing, ["g0"],
                         "the extremal invisible ghost and the visible node "
                         "did not read as (absent, present) — the variants "
                         "are no longer framed on one pixel grid")

    def test_one_scene_rendered_three_times_comes_back_byte_identical(
            self) -> None:
        """Repeat stability, measured where the cache cannot fake it.

        The claim the tier needs is that the app renders one scene the
        same way twice — the app's font load is genuinely asynchronous and
        `exportToBlob` has a documented fonts race, and a tier whose
        headline finding is "these two renders agree" cannot tell a real
        agreement from a shared failure to draw.

        Asserting it through `_client_shots` would be VACUOUS: the second
        call is a cache hit and returns the first call's bytes, so the
        test would prove only that a dict lookup is deterministic. So this
        goes straight to `_client_session` and asks for the same scene
        under THREE names, which the session renders as three independent
        artifacts in one browser. Nothing about a name reaches the picture,
        so all three digests must agree.

        This is the one test in the file that pays a session on every run
        by design. Measured at ~6s, against the 1.8s nine renders cost
        inside one browser — the session, not the render, is the cost, and
        buying a real answer once is the right trade.

        The fourth artifact is what keeps the other three from being
        vacuous, and it is the same doctrine `test_no_class_agrees_by_
        being_absent_from_both` states for the parity table: a comparison
        that agrees proves nothing unless it is known to be capable of
        disagreeing. `odd` holds the same scene with one node moved, so
        the digest set must come to exactly two — three identical and one
        apart. A pipeline that returned one image for every request, which
        is the way this test would otherwise fail to notice being broken,
        gives one.
        """
        scene = tm._diamond_stage()
        box = _scene_bbox(scene)
        same = _anchored(scene, box)
        odd = _anchored([dict(e, x=e["x"] + 24) if e["id"] == "s1" else e
                         for e in scene], box)
        shots = _client_session({"one": same, "two": same, "three": same,
                                 "odd": odd})
        digests = {name: hashlib.sha1(data).hexdigest()[:12]
                   for name, data in shots.items()}
        repeats = {digests[name] for name in ("one", "two", "three")}
        self.assertEqual(len(repeats), 1,
                         "one scene rendered three times gave %d distinct "
                         "images (%s) — the client's render is not "
                         "repeatable, and every delta this tier reports is "
                         "noise until it is" % (len(repeats), digests))
        self.assertNotIn(digests["odd"], repeats,
                         "a scene with a node moved 24px rendered to the "
                         "SAME bytes as the original (%s) — the session is "
                         "not rendering per artifact, so the agreement "
                         "above measured nothing" % digests)


# The L-shaped remnant curator batch 14 reproduced, in ink rather than as the
# bbox it used to flatten to: the top run, then the leg turning down its right
# end. Its bounding box is (122, 59, 283, 120) — tall AND wide, which is the
# whole property that let it swallow a stub it never touched.
_L_REMNANT = ((122, 59, 283, 60), (282, 59, 283, 120))

# Ink for the synthetic continuity scenes, as inclusive (x0, y0, x1, y1)
# rectangles of stroke. These are batch 14's own reproducers and the spike's,
# with batch 14's coordinates where it gave them, so the numbers in
# `_completed_by_eye`'s note and the numbers here are one set of numbers.
_SEVERED_SHAPES = (
    ("a stub 67px clear of the L's leg",
     (*_L_REMNANT, (350, 100, 400, 101))),
    ("a stub 80px clear below the L",
     (*_L_REMNANT, (200, 200, 201, 300))),
    ("a T-corner pair sharing one row of band",
     ((10, 50, 100, 50), (150, 50, 151, 120))),
    ("the back edge's severed turn: the L and the run beneath it",
     (*_L_REMNANT, (122, 176, 258, 177))),
)

# THE INTERLEAVED BACK-LOOP, and its severed twin (curator batch 23 item 7,
# from task 48 §9 candidate 1, 2026-08-15). The corpus instance is
# `argus-r5/argus-domain`'s `r-pipeline-rerun`; this is the smallest path
# that reproduces it, and the minimization is the work — the shape has to
# double back UNDER its own top run, which four rectangles is the fewest
# that expresses.
#
# The connector runs right along the top, turns down at its far end, and
# returns leftward beneath itself. A bound label breaks the TOP run, which
# is the tool's own idiom and must read as one stroke. It reads as two,
# and the reason is not the ends test but the PREFILTER in front of it: the
# back run stops 10px short of the left stub, so the two components are
# x-SEPARATED by 10 while the break they actually have is 40px along the
# top. `_completed_by_eye` picks the x axis, asks for the left stub's right
# end against the whole piece's left end — and the piece's left end is the
# BACK RUN, 69 rows below the gap. The ends it compares are not the ends
# facing the gap.
#
# This is a hole in the two-piece model rather than in a constant, which is
# why no threshold moves it: a component that doubles back has two ends on
# the same side, and `ends` carries one span per side. Both members below
# are the SAME loop with the SAME label; the single variable is where the
# break falls.
_BACK_LOOP_BROKEN_MID_RUN = ((10, 50, 100, 51), (140, 50, 200, 51),
                             (199, 50, 200, 120), (110, 119, 200, 120))
_BACK_LOOP_BROKEN_AT_TURN = ((10, 50, 185, 51),
                             (199, 65, 200, 120), (110, 119, 200, 120))

# A sloped run as a staircase of 1px columns, at two widths. Below `_BAND`
# the left and right bands cover the same pixels and both ends report the
# component's whole length; above it they discriminate (curator batch 23
# item 8, from task 48 §9 candidate 2). Written as steps rather than as a
# rectangle because the defect is about a SLOPE — a genuinely vertical
# 2px stroke's left edge really is its whole length, and reading this pin
# as being about thin strokes rather than about sloped ones would send the
# fix in the wrong direction.
_THIN_SLOPE = ((0, 0, 0, 3), (1, 3, 1, 6), (2, 6, 2, 9))
_WIDE_SLOPE = tuple((x, x, x, x + 3) for x in range(8))
# The same slope at SIX and at FOUR columns, the two band depths `_side_band`
# returns between its floor and its ceiling — 3 and 2. The poles above sit at
# the two ENDS of the function: `_THIN_SLOPE` at three columns reaches the
# floor of 1, `_WIDE_SLOPE` at eight reaches the `_BAND` ceiling of 4, and
# nothing sat anywhere it is genuinely halving. Curator batch 26, 2026-08-16,
# from task-24-follow-up concern 3; measured, forcing extents 4-7 to the
# ceiling in a scratch worktree left the whole 1127-test suite green.
#
# THE INTERVAL IS 4-7, not the 5-7 the concern names — the review's own NIT-2
# says so and it is right: `_side_band` returns 2 at extents 4 and 5 and 3 at
# 6 and 7. One scene per DEPTH rather than per extent, because the depth is
# what the function returns and a pin at every width would be four copies of
# two facts. Both widths are even, which keeps each scene's two bands exactly
# adjacent and makes the pins arithmetic rather than approximate.
_MID_SLOPE = tuple((x, x, x, x + 3) for x in range(6))
_SMALL_SLOPE = tuple((x, x, x, x + 3) for x in range(4))


# The other pole: breaks the eye completes, which the narrowing must not
# start reporting. The third is the back edge's LEGITIMATE mid-leg break —
# two L's, exactly the shape of the severed scene above, and the reason no
# rule of the form "an L never completes" was available.
_CONTINUED_SHAPES = (
    ("a straight run broken mid-run",
     ((10, 50, 100, 51), (140, 50, 230, 51))),
    ("a vertical leg broken mid-leg",
     ((50, 10, 51, 100), (50, 140, 51, 230))),
    ("two L's broken on the leg they share",
     (*_L_REMNANT, (282, 145, 283, 200), (122, 199, 283, 200))),
    ("two L's whose upper bbox is exactly square",
     ((50, 50, 120, 51), (119, 50, 120, 120),
      (119, 145, 120, 200), (50, 199, 120, 200))),
)


def _ink_mask(w: int, h: int,
              strokes: tuple[tuple[int, int, int, int], ...]) -> bytearray:
    """Paint inclusive rectangles of ink into a flat residual mask.

    Args:
        w: Mask width in pixels.
        h: Mask height in pixels.
        strokes: Inclusive `(x0, y0, x1, y1)` rectangles to ink.

    Returns:
        The `w * h` mask, 1 where inked.
    """
    mask = bytearray(w * h)
    for x0, y0, x1, y1 in strokes:
        for y in range(y0, y1 + 1):
            row = y * w
            for x in range(x0, x1 + 1):
                mask[row + x] = 1
    return mask


def _synthetic_strokes(strokes: tuple[tuple[int, int, int, int], ...]
                       ) -> list[list[dict[str, Any]]]:
    """Read hand-drawn ink the way `ablation_findings` reads a delta.

    Everything downstream of the browser, and nothing else: the ink is
    componented, merged and grouped by the shipped functions, so this
    answers "how many pieces would this delta report" without rendering
    anything. The raster is sized to the largest coordinate the scenes
    below use.

    Args:
        strokes: Inclusive `(x0, y0, x1, y1)` rectangles of ink.

    Returns:
        `_reader_strokes` output — one group per piece a reader sees.
    """
    w, h = 420, 320
    residual = _ink_mask(w, h, strokes)
    blobs = [c for c in components(w, h, residual) if c["area"] >= MIN_BLOB]
    return _reader_strokes(_delta_components(w, h, blobs, residual))


def _stripe_png(w: int, h: int, period: int, jitter: int = 0) -> bytes:
    """A decodable grayscale PNG of vertical stripe garbage.

    The corruption signature `canvas.cmd_snapshot` documents and the spike
    behind this tier actually observed: a connected tab answering a
    screenshot request returns banded noise rather than the drawing.
    Reproduced here as a REAL PNG — valid signature, valid IHDR, valid
    IDAT — because that is what makes it dangerous. A truncated or
    malformed file is caught by every reader that touches it; this one
    decodes perfectly and contains nothing.

    `jitter` IS THE WHOLE EXPERIMENT and the reason this takes a second
    parameter. With clean bands (`jitter` 0) the tier reports NOTHING;
    with the phase wobbling enough px per row it invents continuity
    findings. Same density, same band widths, opposite verdicts — so
    what decides whether this tier says anything at all is the TEXTURE
    of the garbage and not how dense it is. Both are things a broken
    readback produces.

    RE-MEASURED 2026-08-16 (v0.9 TASK-24-FOLLOW-UP): the wobble at which
    it starts is not a threshold. It read as one — silent to 2px,
    reporting from 4 — and after `_facing_end` the sweep is silent to
    14px and sporadic above it. Sweep before quoting a number; the
    sibling test carries the current one.

    Deterministic by a written-in seed, so a sweep is reproducible and a
    plateau in it is a real plateau rather than a lucky draw.

    Args:
        w: Raster width in px.
        h: Raster height in px.
        period: Band half-cycle in px.
        jitter: Maximum per-row phase wobble in px. Zero gives the clean
            vertical banding the red below is built on.

    Returns:
        The PNG bytes.
    """
    def chunk(tag: bytes, data: bytes) -> bytes:
        """Frame one PNG chunk.

        Args:
            tag: The four-byte chunk type.
            data: The chunk payload.

        Returns:
            Length, tag, payload and CRC, concatenated.
        """
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))

    rows, seed = [], 999
    for _y in range(h):
        seed = (seed * 1103515245 + 12345) & 0x7fffffff
        phase = (seed % (jitter + 1)) if jitter else 0
        rows.append(bytes(255 if ((x + phase) // period) % 2 else 0
                          for x in range(w)))
    raw = b"".join(b"\x00" + r for r in rows)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6))
            + chunk(b"IEND", b""))


def _flat_png(w: int, h: int, boxes: tuple[tuple[int, int, int, int], ...]
              ) -> bytes:
    """A white grayscale PNG carrying black rectangles — an honest render.

    The control substrate for the garbage above: the same encoder, the
    same dimensions, ordinary picture content. What the poles use to
    prove the instrument fires through the injection point, so a red
    about garbage cannot be satisfied by an injection that had broken
    the pipeline for some reason of its own.

    Args:
        w: Raster width in px.
        h: Raster height in px.
        boxes: Inclusive `(x0, y0, x1, y1)` rectangles to paint black.

    Returns:
        The PNG bytes.
    """
    def chunk(tag: bytes, data: bytes) -> bytes:
        """Frame one PNG chunk.

        Args:
            tag: The four-byte chunk type.
            data: The chunk payload.

        Returns:
            Length, tag, payload and CRC, concatenated.
        """
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))

    rows = [bytearray(b"\xff" * w) for _ in range(h)]
    for x0, y0, x1, y1 in boxes:
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                rows[y][x] = 0
    raw = b"".join(b"\x00" + bytes(r) for r in rows)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6))
            + chunk(b"IEND", b""))


# ---------------------------------------------------------------------------
# The client tier cannot tell a drawing from noise (curator batch 25,
# 2026-08-16; task-50-report.md §11's C4).
#
# The spike behind this tier started a server without `--no-browser`, a
# connected tab STOLE every screenshot request, and what came back was
# vertical stripe garbage at 0.033 bytes/px. Today that is prevented by
# `--no-browser` and pinned by one argv test, which is a pin on the CAUSE.
# Nothing pins the EFFECT: no instrument anywhere asks whether the bytes the
# tier just measured are a picture of the scene.
#
# MEASURED HERE, AND THE ANSWER DEPENDS ON THE TEXTURE — which is a sharper
# finding than the candidate was filed as, and it took three passes to get
# right, so the negative result is recorded beside the positive one:
#
#   CLEAN vertical bands  -> ZERO findings. A clean bill of health for a
#                            drawing the tier never saw. NOT CAUGHT.
#   bands with phase jitter -> continuity findings, garbage magnitudes.
#   dense per-pixel noise -> every delta erased by the tolerant diff, so
#                            every element reads as missing. CAUGHT.
#
# So the tier is not uniformly blind to corruption, and a pin claiming it was
# would be wrong. What it cannot see is the COHERENT case, and coherence is
# not a proxy for severity — clean banding is exactly what a stolen readback
# of a scrolled tab looks like.
#
# THE PRESCRIBED CONTROL PASSES ON THE CASE THAT MATTERS. Mechanism 3 in this
# file's section comment above rules out `validate_png`'s density floor for a
# good reason (valid renders here run 0.028-0.058 bpp and straddle it) and
# puts a STRUCTURAL control in its place: ablate a known-visible element in
# the same call and pin that its delta is not empty. Against clean bands
# every delta is enormous, so that control is satisfied on every element and
# reports nothing. It was built to catch the render that produced NOTHING,
# and it does — that is the dense-noise row above. It cannot see the render
# that produced a confident wrong picture, and nothing else looks.
#
# WHY THIS IS NOT AN ARGUMENT FOR THE DENSITY FLOOR: these rasters run 0.007
# to 0.010 bytes/px, far under `min_bpp`, so `validate_png` would have caught
# them — and would also refuse the legitimately sparse mutant scenes the
# section comment names, which is why it was rejected. The gap is real in
# both directions and the fix is not obvious, which is exactly why it is
# pinned rather than patched. The owner is this module's, not a curator's.
#
# UNGATED, AND THAT IS THE POINT: no browser is involved. `_client_shots` is
# replaced wholesale, so what is under test is everything downstream of the
# render — which is where the blindness lives.
# ---------------------------------------------------------------------------


class TestClientTierReadsWhateverItIsHandedRegime(unittest.TestCase):
    """Garbage in, clean verdict out — with the structural control green."""

    W, H = 400, 300

    def _findings(self, shots: dict[str, bytes],
                  ids: list[str]) -> list[dict[str, Any]]:
        """Run `client_ablation_findings` over supplied rasters.

        The whole tier below the browser, with the browser replaced. The
        scene passed in is `tm._diamond_stage()` in every case and is
        never what decides the answer — the rasters are — which is the
        arrangement that isolates the reading machinery from the
        rendering.

        Args:
            shots: One PNG per variant key. Must carry `full` and an
                `abl-<id>` for each of `ids`, which is the contract
                `_client_shots` itself satisfies.
            ids: The element ids to ablate.

        Returns:
            The findings the tier reports.
        """
        with mock.patch.object(sys.modules[__name__], "_client_shots",
                               side_effect=lambda variants: shots):
            return client_ablation_findings(tm._diamond_stage(), ids)

    @unittest.expectedFailure
    def test_red_clean_stripe_bands_report_a_perfectly_healthy_drawing(
            self) -> None:
        """Three band rasters in, zero findings out, nothing raised.

        C4. Each variant is clean vertical banding at a different period,
        so the readback is not merely wrong but UNSTABLE between shots —
        which is what a tab answering three separate screenshot requests
        produces. Three band widths are swept rather than one, because a
        single width would leave "this particular pattern" as a live
        explanation for the silence.

        WHAT A CORRECT INSTRUMENT WOULD DO, either of which passes this
        test, because the fix's shape is not this pin's to choose:
        REFUSE to measure — raise, the way `_client_shots` already raises
        when a render does not come back — or report every element
        missing, since none of them is in these pixels. What it must not
        do is answer that the drawing is fine. That is the assertion.

        MAGNITUDE AND DIRECTION are the count and its sign: zero findings
        over a scene containing three elements, none of which appears in
        any raster. The direction is the whole finding — the tier errs
        toward SILENCE on corrupt input, which is the one direction a
        measurement instrument must never err in, because silence is
        indistinguishable from health (doctrine §1), and this is the tier
        that exists to see what tier 1 cannot.

        WHAT THIS DOES NOT CLAIM, and the sibling test below holds it
        down: the tier is not blind to corruption in general. Dense
        per-pixel noise IS caught — the tolerant diff erases it, every
        delta comes back empty, and every element reads as missing, which
        is the loud safe answer.

        HOW WIDE THE BLINDNESS IS, re-measured 2026-08-16 by v0.9
        TASK-24-FOLLOW-UP and BROADER than this pin used to say. It read
        "specific to COHERENT garbage", on a sibling measurement that
        found a sharp boundary at 4px of phase wobble. After
        `_facing_end` that boundary is a scatter: banding is silent at
        every wobble from 0 to 14px and sporadically reporting above it.
        So the class this red pins is "banded garbage", coherent or
        moderately wobbled, and the honest statement of its edge is that
        there is no clean edge — read the sibling's re-measured sweep
        rather than inferring one. The ASSERTION here has not moved and
        neither has the defect; only the size of the class it stands for.

        WHY NOT THE DENSITY FLOOR: these rasters run under 0.010
        bytes/px and `validate_png`'s `min_bpp=0.05` would refuse them —
        and would also refuse the sparse mutant scenes this file
        legitimately measures at 0.028. Mechanism 3 in the section
        comment rejects it for that reason and the rejection stands;
        named here so it is not re-proposed as the obvious fix.

        WHO FLIPS THIS: this module's owner. It needs an instrument that
        does not exist yet — a way to ask whether a raster is a picture
        of a given scene — and that is a design question, not a patch.
        """
        for period in (4, 6, 8):
            shots = {"full": _stripe_png(self.W, self.H, period),
                     "abl-s1": _stripe_png(self.W, self.H, period + 1),
                     "abl-s2": _stripe_png(self.W, self.H, period + 2)}
            with self.subTest(period=period):
                try:
                    finds = self._findings(shots, ["s1", "s2"])
                except RuntimeError:
                    continue            # refusing to measure is a pass
                self.assertNotEqual(
                    finds, [],
                    "the client tier read three rasters of clean stripe "
                    "banding at %.4f bytes/px and reported a clean "
                    "drawing: no element missing, no run severed, nothing "
                    "raised. The structural control passes too — every "
                    "delta is enormous — so no assertion anywhere in this "
                    "file would notice"
                    % (len(shots["full"]) / float(self.W * self.H)))

    def test_wobbling_the_bands_makes_the_tier_invent_a_finding_instead(
            self) -> None:
        """The other failure mode: it speaks, and what it says is false.

        WHAT IT REPORTS IS THE POINT, and it is why this test is not
        titled "caught". On the wobbled raster the tier says `s2`'s ink
        comes apart in 2 separated pieces — a specific, confident,
        entirely false statement ABOUT THE DRAWING, derived from a raster
        that contains no drawing. It is not a complaint about the render.

        So the two textures are two failure modes of one gap rather than
        a caught case and a missed one: handed a picture of nothing, this
        tier either says nothing or invents a finding, and it has no
        vocabulary at all for "I cannot measure this". That is the
        sentence its owner needs, and neither test alone says it.

        RE-MEASURED 2026-08-16 (v0.9 TASK-24-FOLLOW-UP), and the pin
        caught the change rather than being adjusted to it — this test
        went red on the continuity widening and is what sent anyone
        looking. WHAT IT USED TO SAY, and no longer can: that the
        boundary is SHARP and the red beside it is therefore about
        COHERENCE — silent at 0, 1 and 2px of wobble, reporting from 4px,
        both elements at 8px. After `_facing_end` the same sweep is a
        SCATTER and not a threshold: silent at every wobble from 0 to 14,
        one element at 16, silent at 18-21, one element at 22 and 24-26,
        silent at 27-33, one at 34-38, silent at 40. So the constants
        move to 25 — the middle of the widest measured plateau, 24-26 —
        and the CLAIM narrows with them, to "wobbled banding CAN make
        this tier invent a finding" from "wobble is what decides".

        THE CONSEQUENCE BELONGS TO THE RED'S OWNER and is not repaired
        here: that red's own docstring cited this boundary for the
        sentence "the blindness is specific to COHERENT garbage", and
        that sentence is now false in the direction that makes the red
        BROADER — clean banding and 8px-wobbled banding are both silent
        now. The red is untouched and still red; what changed is the
        scope of the class it pins, which is corrected in its prose.

        Not a regression to mourn, on the reading this file already
        takes: a false finding about a drawing that is not there is
        exactly what the title calls it, so fewer of them is not the tier
        getting worse. The gap the red names — no vocabulary for "I
        cannot measure this" — is untouched either way.

        Its second job is rule 8, and it is the SECONDARY holder of it:
        findings coming back here prove the injection reaches the
        instrument, and `test_both_client_checks_fire_through_the_
        injected_renderer` below holds that same proof on honest rasters
        where no texture measurement can move it.
        """
        shots = {"full": _stripe_png(self.W, self.H, 31, jitter=25),
                 "abl-s1": _stripe_png(self.W, self.H, 29, jitter=25),
                 "abl-s2": _stripe_png(self.W, self.H, 37, jitter=25)}
        finds = self._findings(shots, ["s1", "s2"])
        self.assertEqual(
            sorted((f["check"], f["element"]) for f in finds),
            [("ablation_continuity", "s2")],
            "wobbled banding no longer produces the invented continuity "
            "finding this scene was measured on, so the red beside it has "
            "stopped having a firing pole at all and both need "
            "re-measuring — sweep `jitter` before choosing a new constant, "
            "because this stopped being a threshold: %r"
            % ([(f["check"], f["element"], f["magnitude"])
                for f in finds],))
        self.assertEqual(
            sorted({f["check"] for f in finds}), ["ablation_continuity"],
            "a check outside this tier's two drawing checks has appeared "
            "on a garbage raster — if that is a render-validity finding, "
            "it is the instrument the red beside this one says does not "
            "exist, and that red should be flipping: %r"
            % (sorted({f["check"] for f in finds}),))

    def test_both_client_checks_fire_through_the_injected_renderer(self
                                                                   ) -> None:
        """The red's firing pole: the same injection, honest rasters.

        Rule 8, both halves, and without it the red above is worthless:
        "zero findings came back" is exactly what a broken injection
        produces, and `mock.patch` over a module-level name is precisely
        the kind of thing that silently patches nothing. So both of this
        tier's checks are made to fire through the SAME mechanism, on
        pictures rather than noise.

        `ablation_existence` fires on an identical pair — the ablated
        raster equals the full one, so removing the element changed no
        pixels, which is the "in the model, not in the picture" finding.

        `ablation_continuity` fires on a delta that comes apart: the full
        raster carries two widely separated boxes and the ablated one is
        blank, so the recovered ink is two pieces. Asserted at >= 2
        rather than exactly 2 for the reason the tier's other continuity
        pins give — the piece count of real ink is a rendering fact and
        the claim here is only that the instrument is awake.
        """
        blank = _flat_png(self.W, self.H, ())
        one = _flat_png(self.W, self.H, ((40, 40, 90, 90),))
        two = _flat_png(self.W, self.H, ((40, 40, 90, 90),
                                         (300, 200, 350, 250)))
        with self.subTest(check="ablation_existence"):
            finds = self._findings({"full": one, "abl-s1": one}, ["s1"])
            missing = [f for f in finds
                       if f["check"] == "ablation_existence"
                       and f["element"] == "s1"]
            self.assertTrue(
                missing, "an ablation that changed no pixels at all went "
                         "unreported, so this injection is not reaching "
                         "the tier: %r" % (finds,))
        with self.subTest(check="ablation_continuity"):
            finds = self._findings({"full": two, "abl-s1": blank}, ["s1"])
            apart = [f for f in finds
                     if f["check"] == "ablation_continuity"
                     and f["element"] == "s1"]
            self.assertTrue(
                apart and apart[0]["magnitude"] >= 2,
                "a delta of two separated boxes did not read as separated "
                "ink, so the continuity half of this tier is not reached "
                "by the injection either: %r" % (finds,))

    def test_the_garbage_substrate_is_a_valid_png_of_the_right_size(self
                                                                    ) -> None:
        """The red's other control: its input decodes, and it is thin.

        Two claims the red rests on and neither is self-evident. If the
        stripe PNGs did not DECODE, the red would be measuring a reader
        that choked rather than a tier that was fooled — the whole point
        of C4 is that this file is structurally valid and semantically
        empty. And if the density were not under `min_bpp`, the paragraph
        in the red explaining why the obvious fix was rejected would be
        describing a different file than the one it runs on.

        The floor is read off the same constant the red's prose cites
        rather than being retyped as a bare number, so the day someone
        retunes it this control moves with it instead of asserting
        yesterday's figure — the calibration-literal discipline recorded
        in `tests/test_mutants.py` beside `CATALOGUE_RED_IDS`.

        Both textures are checked, because the red and its boundary test
        use different ones and a substrate control that covered only one
        would leave the other unproven.
        """
        floor = 0.05                    # `cmd_snapshot`'s `min_bpp`
        for period in (4, 6, 8, 29, 31, 37):
            for jitter in (0, 4):
                with self.subTest(period=period, jitter=jitter):
                    data = _stripe_png(self.W, self.H, period, jitter)
                    w, h, pix = read_png_gray(data)
                    self.assertEqual((w, h), (self.W, self.H))
                    self.assertEqual(len(pix), self.W * self.H)
                    self.assertLess(len(data) / float(self.W * self.H),
                                    floor)
                    self.assertFalse(
                        canvas.validate_png(data, want_w=self.W,
                                            want_h=self.H,
                                            min_bpp=floor)[0],
                        "the density floor now ADMITS this garbage, so "
                        "the red's account of why that instrument was "
                        "rejected needs re-reading")


# ---------------------------------------------------------------------------
# `_scene_bbox` bounds the INK (curator batch 25, 2026-08-16;
# task-50-report.md §11's C5 and its review MINOR-3). BOTH REDS FLIPPED by
# v0.9 TASK-FRAMING; the class keeps its name so the pins stay findable by
# the reports that route to them, and reads now as the proven pole.
#
# `_anchored` exists to make one promise: whatever a variant scene loses, the
# two anchors stay put, so every raster in an ablation run frames the same
# region and the deltas are comparable. That promise rests entirely on
# `_scene_bbox` bounding the scene, and its own docstring says as much —
# "anchors derived from an under-reported box would stop bounding the scene,
# which is the one way `_anchored`'s guarantee can fail". This WAS that
# failure, twice over, and `_ANCHOR_GAP`'s 24px is the whole margin between
# a correct run and ink outside the frame.
#
# Task 56's lesson is the general form: stored geometry is not the ink. The
# function read `x`, `y`, `width`, `height` and `points`, which is the same
# bound Task 56 spent a work package removing from the geometry checks; it
# now reads the drawn extent through `_drawn_corners`, and these three tests
# are what holds it there.
#
# BOTH WERE HARNESS DEFECTS, which made them unlike most of this file: a
# wrong answer here does not misreport a drawing, it silently weakens every
# ablation measurement taken through it. Neither was reachable by any corpus
# scene — that is why they were pins and not incidents — and the fix belonged
# to whoever owns this module's framing, not to a curator. It was taken
# there: nothing in `canvas.py` changed for either flip.
#
# WHAT DID NOT FLIP WITH THEM, and must not be read as fixed by proxy:
# `canvas.ink_extent` is still rotation-blind, and `test_mutants.
# TestInkExtentIsRotationBlind` is still red on the same `_rotated_slab(90)`
# scene. The two pins were built as sisters over one base scene precisely so
# that fixing one could not quietly appear to close the other. This module's
# framing now reads `angle`; the product's export framing does not.
#
# UNGATED, for the reason `TestContinuityNarrowingRegime` below is ungated:
# no part of this needs a browser, and the rot it catches happens in ordinary
# editing where nobody has `MUTANTS_RENDER=1` set.
# ---------------------------------------------------------------------------


class TestSceneBboxBoundsTheStoredBoxRegime(unittest.TestCase):
    """The anchors are placed around the ink, not around the stored box."""

    def _overhang(self, scene: list[dict[str, Any]],
                  ink: tuple[float, float, float, float]) -> float:
        """How far real ink reaches past `_scene_bbox`'s answer.

        Args:
            scene: The scene to bound with `_scene_bbox`.
            ink: `(minx, miny, maxx, maxy)` the ink actually occupies,
                supplied by the caller because the two reds establish it
                by different means — one asks `canvas.ink_extent`, the
                other rotates the corners itself.

        Returns:
            The largest distance from the reported box to the ink, on
            any side, in px. Zero when the box contains the ink.
        """
        bx0, by0, bx1, by1 = _scene_bbox(scene)
        ix0, iy0, ix1, iy1 = ink
        return max(bx0 - ix0, ix0 - bx1, by0 - iy0, iy0 - by1,
                   ix1 - bx1, iy1 - by1, 0.0)

    def test_red_a_wide_string_in_a_narrow_box_paints_past_the_anchors(
            self) -> None:
        """WAS RED, curator batch 25's C5. FLIPPED by v0.9 TASK-FRAMING.

        A `text` element's stored `width` is an estimate an agent may
        author badly and the client re-measures at load; the glyphs are
        drawn at their real advance either way. `_scene_bbox` took the
        stored 20px. `canvas.ink_extent` — the product's own bound, which
        Task 46 and WP4 taught to measure the DRAWN lines — answers 440px
        for the same element.

        MAGNITUDE AND DIRECTION AS FOUND, re-measured at the fix's base
        and unchanged from the pin's: 420px of ink to the RIGHT of the
        box the anchors were placed around, against `_ANCHOR_GAP`'s 24px
        of margin. So this was not a near-miss to be padded away — the
        ink left the framed region by more than seventeen times the
        entire gap, and every variant raster in such a run was framed on
        a different amount of clipped text. Corrected, `_scene_bbox`
        answers `(0, 0, 440, 25)` and the overhang is 0.0.

        THE CHEAPEST FIX WAS THE ONE THE PIN NAMED, and it was taken:
        `_drawn_corners` unions `canvas.ink_extent` of the element into
        the bound rather than restating the wrap-and-measure rule. So
        this assertion is now true BY DELEGATION — which is the point.
        It cannot pass by coincidence and then drift, because there is
        one implementation of a string's advance and both sides of the
        comparison read it. Had the fix re-derived the advance here, this
        test would have been green on the day and quietly wrong the first
        time task 46's wrap rule changed.

        MEASURED AGAINST THE PRODUCT'S BOUND, deliberately, rather than
        against a number written here: the assertion is that the
        harness's bound is at least as wide as the shipped one. A literal
        would have made this pin the calibration table it exists to argue
        against — and would now be a second place to update.
        """
        scene = [{"id": "t1", "type": "text", "x": 0, "y": 0, "width": 20,
                  "height": 25, "fontSize": 20,
                  "text": "A" * 30, "originalText": "A" * 30}]
        x, y, w, h = canvas.ink_extent(scene, pad=0)
        over = self._overhang(scene, (x, y, x + w, y + h))
        self.assertEqual(
            over, 0.0,
            "the drawn string reaches %.0fpx past the box the anchors are "
            "placed around, and _ANCHOR_GAP is only %dpx: _scene_bbox says "
            "%r, canvas.ink_extent says %r"
            % (over, _ANCHOR_GAP, _scene_bbox(scene), (x, y, x + w, y + h)))

    def test_red_a_quarter_turned_slab_paints_80px_past_the_anchors(
            self) -> None:
        """WAS RED, review MINOR-3. FLIPPED by v0.9 TASK-FRAMING.

        The sister of `test_mutants.TestInkExtentIsRotationBlind` — same
        base scene, `tm._rotated_slab`, so the two pins cannot drift onto
        different geometry. The point of keeping both is that they name
        DIFFERENT CODE with different owners: `canvas.ink_extent` frames
        exports and floors the tier-1 check, this places the ablation
        anchors. A fix to either leaves the other standing, and THAT IS
        WHAT HAPPENED: this one is green and the sister is still red. If
        both ever go green in one change, check that the change did not
        make this module call the product's bound for the angle too.

        MAGNITUDE AND DIRECTION AS FOUND, re-measured at the fix's base
        and unchanged from the pin's: the 200x40 slab quarter-turned
        paints from y=-80 to y=120; `_scene_bbox` answered y=0 to y=40.
        The ink escaped 80px ABOVE the framed region, against
        `_ANCHOR_GAP`'s 24px — so, unlike the text case, this one was
        within the same order as the gap and would have read as a
        plausible margin to widen. It was not: at 45 degrees the same
        slab's painted box is 169.7px tall against the stored 40 — 130px
        of growth, 64.9px of it above the framed region — and no constant
        gap bounds a function that never reads the angle. (The pin's own
        "reaches 130px out" was that GROWTH, not the one-sided overhang
        the `_overhang` helper measures; both re-measured here, and the
        clause is spelled out because the two numbers differ by a factor
        of two and the pin gave one name to both.)

        WHY IT UNDER-REPORTED GEOMETRY AND NOT MERELY INK, which is
        MINOR-3's actual claim and the reason it is filed apart from C5
        above: the text case is a disagreement about how wide a drawn
        string is, and reasonable bounds can differ on it. This one is
        not about ink at all. The corners are fixed by `x`, `y`,
        `width`, `height` and `angle` — five stored numbers — and the
        function read four of them.

        THE FIX ROTATES ABOUT THE STORED CENTRE, and this test does NOT
        prove that choice — measured, not assumed. Two nearby spellings
        were run against it: rotating about the PAINTED box's centre, and
        the same rotation distributed into a different arithmetic order.
        Both pass. The first passes because on this scene the painted box
        IS the stored box, so the two centres coincide and the slab
        cannot tell them apart; the second because the values here are
        exact enough that two orderings agree to the bit.

        So what this test pins is the SPAN, and the reason to rotate
        about the stored centre is argued from the renderer rather than
        held down here: Excalidraw turns an element about its own box's
        centre, so a string overhanging that box swings with it, and
        rotating about the painted box's centre would put the glyphs
        somewhere nothing draws them. The scene that would separate the
        two is a turned text whose stored width is dishonest — the two
        pins' constructions crossed — and it is not written here because
        the only available reference for where those glyphs land is this
        module's own union, which would make the assertion a restatement
        of the implementation. It wants a client render to be worth
        anything; routed as such rather than faked cheaply.
        """
        scene = tm._rotated_slab(90)
        cs = tm._painted_corners(scene[0])
        ink = (min(c[0] for c in cs), min(c[1] for c in cs),
               max(c[0] for c in cs), max(c[1] for c in cs))
        over = self._overhang(scene, ink)
        self.assertEqual(
            over, 0.0,
            "the quarter-turned slab paints %.0fpx outside the anchored "
            "region (_ANCHOR_GAP is %dpx): _scene_bbox says %r, the "
            "painted corners span %r"
            % (over, _ANCHOR_GAP, _scene_bbox(scene), ink))

    def test_the_bbox_does_bound_an_upright_scene_and_its_waypoints(
            self) -> None:
        """Both pins' live pole: unturned and untexted, it is correct.

        Load-bearing in BOTH directions, and more so now that the two
        above are green. Without it, either pin is satisfied by a
        `_scene_bbox` that had stopped bounding anything — a degenerate
        box at the origin is outside every scene's ink too, and it is
        exactly what the function returns for an empty list. And because
        the first two claims here assert EXACT boxes rather than
        containment, the opposite cheat fails too: a `_scene_bbox` that
        widened everything by a constant, or returned some enormous
        box, satisfies both overhang assertions above and fails here. An
        over-wide frame is not harmless — the anchors are what pin every
        variant of an ablation run to one grid, and a bound nobody can
        predict is a bound nobody can debug a delta against.

        Three claims, on the shapes the pins hold constant:

        The upright slab. `tm._rotated_slab(0)` is the reds' own base
        scene with the one field they turn on left flat, so the pair
        measures `angle` and nothing else about the element.

        An arrow's WAYPOINTS, which is the one place this function
        already goes beyond the stored box, and its docstring's own
        claim: a route whose points reach past `x + width` must still be
        bounded. A regression there would look identical to the reds
        beside it and have a different cause.

        A text element whose stored width is HONEST. This is what makes
        C5 a statement about the estimate rather than about text as a
        class — the same element type, bounded correctly, once the
        stored box matches the advance `canvas.ink_extent` computes.
        """
        with self.subTest(case="upright slab"):
            self.assertEqual(_scene_bbox(tm._rotated_slab(0)),
                             (0.0, 0.0, 200.0, 40.0))
        with self.subTest(case="arrow waypoints past the stored box"):
            arrow = [{"id": "a1", "type": "arrow", "x": 0, "y": 0,
                      "width": 10, "height": 10,
                      "points": [[0, 0], [100, 60]]}]
            self.assertEqual(_scene_bbox(arrow), (0.0, 0.0, 100.0, 60.0))
        with self.subTest(case="text whose stored width is honest"):
            honest = [{"id": "t1", "type": "text", "x": 0, "y": 0,
                       "width": 440, "height": 25, "fontSize": 20,
                       "text": "A" * 30, "originalText": "A" * 30}]
            x, y, w, h = canvas.ink_extent(honest, pad=0)
            self.assertEqual(self._overhang(honest, (x, y, x + w, y + h)),
                             0.0,
                             "a text whose stored width matches its advance "
                             "is still not bounded, so C5's red is about "
                             "text as a class rather than about the "
                             "estimate")

    def test_a_turned_connector_is_framed_where_the_client_frames_it(
            self) -> None:
        """The harness half of the point-strung rotation fix.

        FILED BY THE TASK-MICROFIX REVIEW (fix round 1, 2026-08-16) and
        it exists because of HOW that defect hid. `_drawn_corners` and
        `canvas.ink_extent` both turned a polyline's BOX rather than its
        points, so the harness and the product agreed with each other
        exactly — and a harness-versus-product agreement is the check
        this module is built out of. Both were wrong about the client by
        the same 282.8px on a 45-degree diagonal arrow, and nothing here
        could say so, because nothing here compared either of them to
        Excalidraw.

        SO THIS ASSERTS AGAINST THE CLIENT'S ARITHMETIC, imported from
        `tm._painted_polyline` — `getLinearElementRotatedBounds`, which
        turns the path, and the freedraw branch, which turns the points.
        It is the only assertion in this class whose reference is not
        this repo's own other spelling of the same bound.

        TWO EXCLUSIONS WERE NEEDED and that is the reason this is a test
        rather than a note. `_drawn_corners` unions three bounds, and
        for a polyline TWO of them are unrotated axis-aligned boxes: the
        stored box, and `ink_extent`'s own answer. Turning either one
        reconstructs the AABB-of-AABB by itself. The first fix dropped
        only the box, the numbers barely moved, and it took measuring
        against this reference to see that the bound was still wrong.

        The elbow is here as well as the diagonal because the diagonal
        is degenerate on one axis — a bound that had collapsed to a
        point would satisfy it — and 0 degrees is here because a
        function that had stopped bounding connectors at all agrees with
        nothing about nothing.
        """
        for pts in ([[0, 0], [200, 200]], [[0, 0], [200, 0], [200, 200]]):
            for angle in (0, 30, 45, 90):
                with self.subTest(points=pts, angle=angle):
                    scene = tm._rotated_arrow(angle, points=pts)
                    turned = tm._painted_polyline(scene[0])
                    want = (min(p[0] for p in turned),
                            min(p[1] for p in turned),
                            max(p[0] for p in turned),
                            max(p[1] for p in turned))
                    for got, expect in zip(_scene_bbox(scene), want):
                        self.assertAlmostEqual(
                            got, expect, places=6,
                            msg="the harness frames this turned connector "
                                "somewhere the client does not — harness "
                                "%r, client %r. If `canvas.ink_extent` "
                                "agrees with the harness here, they are "
                                "wrong together and that is the point of "
                                "this pin"
                                % (_scene_bbox(scene), want))


class TestContinuityNarrowingRegime(unittest.TestCase):
    """`_completed_by_eye`'s narrowing, in arithmetic rather than in ink.

    Ungated for the reason `TestRenderParityRegime` is ungated: none of
    it needs a browser, and the rot it catches happens in ordinary
    editing where nobody has `MUTANTS_RENDER=1` set. What it holds down
    is the WIDTH of the predicate — the mutant next door proves the
    narrowing on one rendered scene, but the three over-merges that
    motivated it were only ever reachable on paper (curator batch 14
    swept for them through the browser and could not reach them), so a
    later widening back toward the bbox test would leave every rendered
    test in this file green and only these would say so.
    """

    def test_the_narrowing_fires_on_the_over_merges_the_bboxes_hid(self
                                                                   ) -> None:
        """Four shapes the old predicate read as one stroke are two.

        Each is a piece whose bbox is tall AND wide against a piece it
        does not touch, so the prefilter's "separated on one axis,
        overlapping on the other" is satisfied by the box rather than by
        the stroke. Under the ends test they come apart, because the L's
        leg foot is two columns wide and the thing it is being completed
        into is nowhere near those two columns.
        """
        for name, strokes in _SEVERED_SHAPES:
            with self.subTest(shape=name):
                self.assertEqual(
                    len(_synthetic_strokes(strokes)), 2,
                    "%s reads as one stroke: the narrowing has widened "
                    "back toward the bbox test" % name)

    def test_the_narrowing_still_completes_a_break_mid_stroke(self) -> None:
        """The bound-label idiom survives it — including the two-L case.

        The constraint on the whole fix, and the reason the ends had to
        be measured rather than the shapes classified: the third scene
        here is two L's broken on their shared leg, which is the same
        pair of shapes as the severed back edge above and must read the
        opposite way. It does, because the two facing ends are the leg's
        cross-section on both sides of the gap.

        The fourth is the same scene with its upper piece's bounding box
        made exactly SQUARE, which is not a curiosity: it is the one
        input on which a `_completed_by_eye` that recovered its
        separation axis by comparing coordinates inside the loop —
        `(u0, u1) == (ax0, ax1)` — reads the y pass as the x pass and
        asks the wrong two sides. That misreading can only ever refuse a
        merge (a y-separated pair's left and right spans are disjoint by
        construction), so it shows up as this file's own idiom being
        reported as a severed connector, and only on square boxes.
        """
        for name, strokes in _CONTINUED_SHAPES:
            with self.subTest(shape=name):
                self.assertEqual(
                    len(_synthetic_strokes(strokes)), 1,
                    "%s reads as two pieces: the narrowing is reporting "
                    "the tool's own bound-label idiom as a severed run"
                    % name)

    def test_an_end_profile_is_the_ink_near_that_side_only(self) -> None:
        """A side's profile is its band's ink, not the component's.

        The distinction the whole fix rests on, on the smallest shape
        that shows it: an L whose run goes right and whose leg drops from
        the run's far end. Read as a component the ink spans the full
        height on the right and the full width on the top; read as ENDS
        the left is the run's stub and the bottom is the leg's foot. A
        profile that reported the component's extents instead would give
        `(10, 60)` and `(10, 100)` for those two, which is the bbox
        predicate again wearing a different name.
        """
        strokes = ((10, 10, 100, 11), (99, 10, 100, 60))
        mask = _ink_mask(120, 80, strokes)
        ends = _edge_profiles(120, (10, 10, 100, 60),
                              [i for i, v in enumerate(mask) if v])
        self.assertEqual(ends, {"left": (10, 11), "right": (10, 60),
                                "top": (10, 100), "bottom": (99, 100)})

    def test_a_merged_components_end_is_measured_on_the_union(self) -> None:
        """A member blob does not carry its own edges into the merge.

        Why `_edge_profiles` runs once over a merged component and not
        per blob with the results unioned, which is the cheaper shape and
        is wrong in both directions. The stub here sits inside the L's
        bounding box, so the merge swallows it: it is inside the union's
        BOTTOM band and contributes to that end, while its own left edge
        at x=200 is nowhere near the union's left edge and contributes
        nothing there. Unioning per-blob profiles would report the stub's
        y-range on the left, where there is no ink at all.
        """
        strokes = (*_L_REMNANT, (200, 117, 260, 118))
        w, h = 420, 320
        residual = _ink_mask(w, h, strokes)
        blobs = [c for c in components(w, h, residual)
                 if c["area"] >= MIN_BLOB]
        self.assertEqual(len(blobs), 2, "the stub must be a blob of its own")
        parts = _delta_components(w, h, blobs, residual)
        self.assertEqual([c["bbox"] for c in parts], [(122, 59, 283, 120)])
        self.assertEqual(parts[0]["ends"]["bottom"], (200, 283))
        self.assertEqual(parts[0]["ends"]["left"], (59, 60))

    def test_a_back_loop_broken_mid_run_reads_as_one_stroke(self) -> None:
        """FLIPPED 2026-08-16 by v0.9 TASK-24-FOLLOW-UP.

        `_facing_end` asks the doubled-back piece where its ink faces the
        stub's lane rather than taking the span at its own bbox edge, and
        the answer is the top run's left end at rows 50-51 — the same two
        rows the stub ends on. The assertion below is unchanged.

        THE NEIGHBOUR BELOW DID THE WORK, during the fix and not after
        it: two drafts of `_facing_end` satisfied this test and merged
        the severed twin or the L-and-run pair `_SEVERED_SHAPES` pins,
        and both were caught by the poles before anything was believed.
        A one-sided band handed the back run's own columns into the top
        run's end; an unguarded anchor choice let a long horizontal run
        stand in as another long horizontal run's end.

        `ablation_continuity` used to over-fire on a connector that
        doubles back under itself. The label breaks the top run — the
        tool's own idiom, which every other scene in this class reads as
        one stroke — and this one came apart, because the back run stops
        10px short of the left stub and so the boxes separate in x while
        the gap is in the top run.

        Magnitude and direction as they stood, both in the numbers the
        failure printed: the predicate reported 2 pieces where a reader
        sees 1, and the ends it compared were 69 rows apart (the stub's
        right end at rows 50-51 against the piece's left end at rows
        119-120) when the ends across the gap are the same two rows on
        both sides. Over-fire, not under-fire — the check spoke where it
        should be silent, which on a corpus run cost a false severance
        report on 1.7% of arrows rather than a missed one.

        No constant moved it, and the pin said so: the piece that doubles
        back has two ends on its left side and `ends` records one span per
        side, so the right end was not merely mis-ranked, it was absent
        from the structure. Of the two routes named here the fix took the
        second — the ends are found by where the ink faces, not by which
        projection separates — and `ends` itself is untouched, so the
        first route stays available to whoever needs it next.
        """
        groups = _synthetic_strokes(_BACK_LOOP_BROKEN_MID_RUN)
        self.assertEqual(
            len(groups), 1,
            "a back-loop broken mid-run reads as %d pieces: the axis "
            "prefilter picked the ends %s, which are not the ends across "
            "the gap"
            % (len(groups),
               [c["ends"] for g in groups for c in g]))

    def test_the_same_back_loop_severed_at_its_turn_reads_as_two(self
                                                                 ) -> None:
        """The neighbour, and the pole that keeps the red honest.

        Same loop, same label, same four rectangles of ink — the single
        variable is that the break falls on the TURN instead of on the
        top run, which is a genuine severance a reader sees as two
        pieces. It reads as two, correctly, today.

        This is the gate shape rather than a liveness control: a fix that
        widened the predicate until the red above passed would merge this
        too, and merging this is exactly the r5-14 defect the narrowing
        was built to stop. Neither pole can be satisfied by accident, and
        a fix has to move one without moving the other.
        """
        groups = _synthetic_strokes(_BACK_LOOP_BROKEN_AT_TURN)
        self.assertEqual(
            len(groups), 2,
            "the back-loop's erased turn reads as %d piece(s): a top run "
            "and a vertical leg 199px to its right have been completed "
            "into one stroke" % len(groups))

    def test_a_thin_sloped_scrap_reports_an_end_not_its_length(self) -> None:
        """FLIPPED 2026-08-16 by v0.9 TASK-24-FOLLOW-UP.

        `_side_band` caps each end's band at half the component's extent
        on that axis, so the two opposite ends are measured on disjoint
        ink at every width of 2px or more. The assertion below is
        unchanged; the scrap's right end is now its rightmost column's 4
        rows, and its left end the leftmost column's, 6 rows apart.

        `_edge_profiles` used to report a component's whole LENGTH as its
        end when the component was thinner than `_BAND`. The corpus
        instance is `argus-r4-arm3/enrichment-pipeline`'s
        `e-edgar-insider`, whose ablation leaves a 2x7 scrap of a sloped
        run; this is that shape at 3 columns, the fewest that carries
        `MIN_BLOB`'s 12 pixels.

        The scrap steps down one row per column, so its leftmost column
        holds rows 0-3 and its rightmost rows 6-9 — genuinely different
        ends, 6 rows apart. Both were reported as `(0, 9)`: at 3 columns
        against a flat 4px band, `x < x0 + _BAND` and `x > x1 - _BAND`
        selected every pixel, so the two opposite ends were not merely
        wrong but IDENTICAL, and the span carried no direction at all.
        Magnitude: 10 rows reported where 4 is the ink. Direction: over,
        on both ends, which is the direction that refuses continuations —
        `_ends_line_up` measures against `max()` of the two spans, so a
        10-row phantom end demanded ~6 rows of overlap from a real 2-row
        stroke end and never got it.

        Asserted as a span WIDTH rather than as an expected pair, because
        the fix's shape was not this pin's to choose: capping the profile
        at the component's own cross-section is the obvious route and any
        route that stops reporting the length satisfies this. That is the
        route taken — `_side_band`, half the extent — and the pin is left
        as the width bound it was written as rather than tightened to the
        pair the current implementation happens to produce, since the
        claim is still "an end is not a length".

        THE CONSTRAINT THE FIX RESPECTS, measured rather than inherited:
        the tempting one-line repair was downstream, changing
        `_ends_line_up` to measure against `min()` of the two spans
        instead of `max()`. That silences this scrap and REOPENS THREE
        of the four over-merges `_SEVERED_SHAPES` pins — "a stub 67px
        clear of the L's leg", "a stub 80px clear below the L" and "a
        T-corner pair sharing one row of band" all drop from 2 pieces to
        1, and only the back edge's severed turn survives. (Re-measured
        at 32630e9 by curator batch 26: this said "two" and named two,
        omitting the 80px stub.) Verified by patching
        `_ends_line_up` in a throwaway tree and replaying both shape
        tables. So the repair went into the PROFILE, where the wrong
        number was produced, and not into the ratio that consumes it —
        and `_ends_line_up` is untouched, which is what leaves that
        measurement standing as a live constraint on the next repair
        rather than as a spent one.
        """
        ink = [y * 400 + x for x0, y0, x1, y1 in _THIN_SLOPE
               for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]
        ends = _edge_profiles(400, (0, 0, 2, 9), ink)
        span = ends["right"][1] - ends["right"][0] + 1
        self.assertLessEqual(
            span, 4,
            "a 3-column sloped scrap reports a %d-row right end %s: its "
            "rightmost column holds 4 rows, and its LEFT end reports the "
            "identical span %s, so the profile has measured the "
            "component's length instead of either of its ends"
            % (span, ends["right"], ends["left"]))

    def test_a_slope_wider_than_the_band_reports_ends_that_differ(self
                                                                  ) -> None:
        """The neighbour: above `_BAND` the profile discriminates.

        The same staircase at 8 columns instead of 3, which is the single
        variable. Here the left band and the right band select different
        pixels, and the two ends come back `(0, 6)` and `(4, 10)` — six
        rows apart, pointing where the stroke actually enters and leaves.

        This is what makes the red above about the BAND and not about
        `_edge_profiles` being broken generally: the function does the
        job it was written for on any component wide enough to have two
        distinguishable sides, and fails exactly when the component is
        narrower than the window used to look at it. A fix that capped
        every profile regardless of width would satisfy the red and break
        this.
        """
        ink = [y * 400 + x for x0, y0, x1, y1 in _WIDE_SLOPE
               for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]
        ends = _edge_profiles(400, (0, 0, 7, 10), ink)
        self.assertNotEqual(
            ends["left"], ends["right"],
            "an 8-column sloped run reports the same span %s at both "
            "ends: the band is no longer selecting a side" % (ends["left"],))
        self.assertEqual((ends["left"], ends["right"]), ((0, 6), (4, 10)),
                         "the wide slope's ends have moved: %s" % (ends,))

    def test_the_halving_interval_keeps_the_two_ends_apart(self) -> None:
        """`_side_band`'s middle: the interval nothing sat in.

        Curator batch 26, 2026-08-16, from task-24-follow-up concern 3.
        The two pins either side of this measure `_side_band` at its
        FLOOR (three columns, band 1) and at its `_BAND` CEILING (eight
        columns, band 4). Between them the function is doing the thing
        it was written to do — halving — and no scene reached it, so the
        halving arm was unmeasured for every extent from 4 to 7.
        Measured before writing this, at 32630e9: forcing those four
        extents to the ceiling in a scratch worktree left 1127 tests OK.
        A branch that is only ever exercised at the two values where it
        agrees with its own bounds is not exercised.

        FOUR TO SEVEN, and the concern's "5-7" is one short — the
        review's NIT-2 caught that and it is right. `_side_band` returns
        2 at extents 4 and 5 and 3 at 6 and 7, so the interval holds two
        DEPTHS, and the two scenes here are one per depth rather than
        one per width. Both are even, which puts each scene's two bands
        exactly adjacent and makes the claim arithmetic.

        MAGNITUDE is the spans; DIRECTION is over, on both ends, which
        is the direction that refuses continuations — `_ends_line_up`
        measures against `max()` of the two spans, so an over-reported
        end demands more overlap than a real stroke end can give. Same
        direction the thin-slope red records, arriving by another route.

        THE FOUR-COLUMN CASE IS THE SHARPER OF THE TWO and is why it is
        worth carrying both. At six columns a flat band reads `(0, 6)`
        and `(2, 8)` — over by a row each, with the two column sets
        overlapping in the middle. At FOUR columns a flat band reads
        `(0, 6)` at BOTH ends: not merely wrong but IDENTICAL, a span
        with no direction in it at all, which is the exact failure
        `_side_band` was written to remove. The scene that produces it
        is 16 pixels, four over `MIN_BLOB`.

        Asserted as exact pairs rather than as width bounds, unlike the
        thin-slope red above. That one was written while its fix's shape
        was still open and was deliberately left loose; this measures an
        implementation that exists, and the point is that these specific
        depths are where the behaviour sits.
        """
        for width, rects, bbox, band, want in (
                (4, _SMALL_SLOPE, (0, 0, 3, 6), 2, ((0, 4), (2, 6))),
                (6, _MID_SLOPE, (0, 0, 5, 8), 3, ((0, 5), (3, 8)))):
            with self.subTest(columns=width):
                self.assertEqual(
                    _side_band(width), band,
                    "`_side_band(%d)` is %d, not %d: the interval between "
                    "the floor and the ceiling has moved and this scene no "
                    "longer sits in it" % (width, _side_band(width), band))
                ink = [y * 400 + x for x0, y0, x1, y1 in rects
                       for y in range(y0, y1 + 1)
                       for x in range(x0, x1 + 1)]
                ends = _edge_profiles(400, bbox, ink)
                self.assertNotEqual(
                    ends["left"], ends["right"],
                    "the %d-column slope reports the same span %s at both "
                    "ends: the band is no longer selecting a side"
                    % (width, (ends["left"],)))
                self.assertEqual(
                    (ends["left"], ends["right"]), want,
                    "the %d-column slope's ends are %s, not %s — at the "
                    "flat %dpx band both ends over-report"
                    % (width, (ends,), (want,), _BAND))

    def test_fattening_both_ends_leaves_a_continuation_continuous(self
                                                                  ) -> None:
        """The ratio is what makes the trip point immune to the build.

        The caveat curator batch 14 recorded against the old rule: it
        discriminated by an 11-row miss against a ONE-row trip point, so
        a build whose anti-aliasing fattened a stub by 11 rows would
        silence the elbow mutant with nothing to say so. A ratio cannot
        drift that way — thickening both ends of a genuine continuation
        thickens the overlap with them — and the second half adds the
        displacement the dilation exists to absorb on top.
        """
        for k in range(1, 13):
            self.assertTrue(
                _ends_line_up((50, 50 + k), (50, 50 + k)),
                "a %dpx stroke stopped continuing into itself" % (k + 1))
            self.assertTrue(
                _ends_line_up((50, 50 + k), (51, 51 + k)),
                "a %dpx stroke displaced one pixel stopped continuing"
                % (k + 1))

    def test_the_trip_point_scales_with_the_wider_end(self) -> None:
        """Half the WIDER end, so a thin stub cannot ride a long edge.

        The rule stated as its own boundary, `_SLACK` included. A two-row
        stub laid across a 71-row turned edge overlaps it by two rows and
        is not a continuation of it at any offset; the same pair of ends
        merges as soon as the narrow one covers half the wide one.
        Measured against the wider rather than the narrower end
        deliberately: on the min the L's own leg foot would complete into
        anything that crossed it, which is the over-merge in a smaller
        disguise, and the corpus arrow whose sloped stub still reads as
        severed is the price of refusing it (task-48 report §6).
        """
        self.assertFalse(_ends_line_up((50, 51), (50, 120)))
        self.assertFalse(_ends_line_up((50, 83), (50, 120)))   # 36 of 73
        self.assertTrue(_ends_line_up((50, 84), (50, 120)))    # 37 of 73
        self.assertTrue(_ends_line_up((50, 120), (50, 120)))

    def test_two_ends_a_pixel_apart_are_still_one_run(self) -> None:
        """`_SLACK`, and the corpus measurement that put it there.

        Everything upstream of this predicate carries one pixel of
        tolerance — `pngdiff._dilate` spends exactly that, and this
        module's premise is that a sub-pixel displacement is not a
        defect. The ends comparison was written without it and the
        corpus said so: three of its arrows are a single curve broken by
        its own bound label, and the stroke resumes in the column NEXT
        to the one it stopped in, so the spans touch without overlapping.
        The first two spans below are `enrichment-flow/f-edgar-sentiment`
        and `daily-run-flow/t-compose-clients` as measured; both used to
        read as severed connectors, which is this exemption's own idiom
        reported as the defect it exists to excuse.

        The slack is one pixel and not two on purpose: it buys the
        displacement class and nothing wider, so the severed back edge
        next door — which misses by 20 columns — is untouched by it.
        """
        self.assertTrue(_ends_line_up((898, 899), (900, 900)))
        self.assertTrue(_ends_line_up((395, 395), (393, 394)))
        self.assertFalse(_ends_line_up((279, 280), (122, 258)))


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
class TestPaintOrderInPixels(AblationLiveness, unittest.TestCase):
    """A decoration at index 0 leaves the connector visible — in the raster."""

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

        The ghost is curator batch 24's (2026-08-16). The ink magnitude
        this docstring argues for is real and stays, but it is the
        mortality spike's GRADE B: it measures the substrate and never
        calls `ablation_findings`, so it closes "a scene that drew
        nothing" and leaves "a detector that says nothing" wide open.
        Measured — this test passed with the producer stubbed to `[]`.
        Task #67 classified it PARTIAL for that reason and warned against
        an ast guard that would flag it identically with the vacuous
        ones; the distinction such a guard cannot make is that this row
        needed a control ADDED, not one replaced.
        """
        scene = _with_liveness_ghost(tm._backdrop_scene(behind=True))
        finds = ablation_findings(scene, ["e1", "z1"])
        self.assertEqual(
            [f for f in finds if f["element"] == "e1"], [],
            "the connector is declared AFTER the panel that covers it and "
            "must survive into the raster")
        self.assertDetectorSpoke(finds)
        ink = _element_ink(scene, "e1")[0]
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
             for f in ablation_findings(scene, ["e1"])],
            [("ablation_existence", "e1", 0.0)])
        self.assertEqual(_element_ink(scene, "e1")[0], 0)


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
class TestComposedContentVisibility(AblationLiveness, unittest.TestCase):
    """A tile's own value must survive its owner's fill."""

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
        ink = _element_ink(_kpi_tile("#e9e5da"), "k1-value")[0]
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
        the role. `normalize_z_order` reads the PART TAG, so `value_of`
        and `attr_of` — a composite's own content — band above the node
        that owns them. Task 44 lifted that content half only, leaving
        furniture (`box_of`, `track_of`, `body_of`, …) beneath its owner;
        task 45 lifted the rest, so the shipped rule today is that EVERY
        composed part (`COMPOSED_PART_KEYS`) bands above the owner it is
        drawn on, and only an UNTAGGED `role: decoration` — a standalone
        backdrop — bands beneath. Read the furniture half's flipped red
        below for that change; this docstring is the content half and
        stops at the tag it names. The role still means exactly one
        thing, and it is the lint exemption.

        Two things that were rejected as fixes, deliberately:
        giving the value an opaque fill of its own would have hidden the
        banding rather than fixed it, and reinstating a text-last pass in
        `render_svg` would have restored the export/canvas disagreement
        WP4 removed — the export would show a value the user cannot see.

        RE-EARNED AT THE FLIP, one batch late (curator batch 24,
        2026-08-16), and the delay is the lesson. While this was RED the
        assertion "the check is silent" was the DEFECT's signature, and a
        dead detector satisfying it tripped the unexpected-success alarm
        — the red was self-guarding. The moment task 44 dropped the
        marker the identical line began asserting HEALTH, and a dead
        detector agrees with that too. So a mutant that had genuinely
        pinned something became one that pins nothing while `mutants
        list` went on counting it as a proven pole — worse in kind than a
        weak neighbour, because it reads as coverage. The flip contract's
        second half is exactly this: a flip re-earns its control in the
        same change, and the ghost below is what that costs.
        """
        scene = _with_liveness_ghost(_kpi_tile("#e9e5da"))
        finds = ablation_findings(scene, ["k1-value", "z1"])
        self.assertEqual(
            [f for f in finds if f["element"] == "k1-value"], [],
            "the tile's own value is painted under the tile: %s"
            % [f["raw"] for f in finds])
        self.assertDetectorSpoke(finds)

    def test_neighbour_a_transparent_owner_leaves_its_value_visible(self
                                                                    ) -> None:
        """The other pole: the same tile, the same value, no fill.

        The control that keeps the red meaningful — one variable moves,
        the owner's `backgroundColor` — and the whole corpus's current
        state, which is the reason the 24-artifact replay came back
        pixel-clean and the hazard went unnoticed. Without it the red
        would be satisfied by a renderer that drew no composed content at
        all.

        TWO CONTROLS, and curator batch 24 (2026-08-16) added the second
        because the first turned out to answer a different question than
        its comment claimed. The ink measurement closes "nothing was
        drawn either way" — a real confound, worth closing, and it is
        GRADE B in the mortality spike's vocabulary: a positive fact that
        never calls the instrument under test. `ablation_findings`
        stubbed to `[]` passed this test with the ink assertion in place,
        because measuring the substrate says nothing about the detector
        reading it. The ghost is the grade-A half.
        """
        scene = _with_liveness_ghost(_kpi_tile("transparent"))
        finds = ablation_findings(scene, ["k1-value", "z1"])
        self.assertEqual([f for f in finds if f["element"] == "k1-value"], [])
        self.assertDetectorSpoke(finds)
        # Silence has to mean "the value is in the picture", never
        # "nothing was drawn either way", so pin that there was ink:
        # 309px measured 2026-08-14, the same glyphs the red loses.
        self.assertAlmostEqual(
            _element_ink(scene, "k1-value")[0], 309, delta=31)


# The composed controls whose furniture this tier can actually MEASURE, each
# with the state it needs, the part that CARRIES that state, and that part's
# ink (px, measured 2026-08-14, band ~10% and wider on the smallest where one
# antialiased row is a larger fraction). One number per row serves BOTH
# poles: it was the transparent-owner reading while the defect stood, and
# since the task 45 flip it is what the same part measures under an opaque
# owner too (38/136/108 against 36/126/108) — which is the flip's content
# stated as a magnitude rather than as prose.
#
# `body` and `image` are absent on purpose, and the reason is measurement
# rather than oversight — task-44-report §5.1 lists all five composites as
# buried on the strength of the OPAQUE pole alone, and the other pole says
# only three of them are. Body waves and X-box diagonals are stroked
# `FURNITURE_INK` at width 1: the diagonals rasterize above `tolerant_diff`'s
# ink threshold of 192 (`f1-x1`: 0px of residual even at `min_blob=1`) and
# the waves survive only as speckle under the `MIN_BLOB` floor (`f1-body1`:
# 42px at `min_blob=1`, 0px at 12). (Named by CONSTANT, not by hex: the
# 2026-08-17 contrast ruling took that ink from #b8b2a5 to #8d877a, one step
# of lightness, and the whole render tier re-measured green on it — the
# opaque-owner reading of `f1-x1` below came back at 208px, the same figure
# it was pinned at. The numbers in this paragraph date from the #b8b2a5
# measurement and were NOT individually re-derived; what was checked is that
# every assertion resting on them still holds.) Both therefore read as
# ABSENT with a
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
    than assembled by hand, because the defect was what the composition
    and the banding produced between them. The two builders stay separate
    rather than sharing a core — both halves of the family are flipped
    now (task 44 took the content, task 45 the furniture), but the kpi
    one's docstring is about content and this one's is about furniture,
    and merging them would cost the reader that distinction for one saved
    scene builder.

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
class TestComposedFurnitureVisibility(AblationLiveness, unittest.TestCase):
    """A control's own state glyph must survive its owner's fill."""

    def test_composed_furniture_survives_its_opaque_owner_by_measurement(
            self) -> None:
        """The FIX's magnitude, at both poles, across all three controls.

        Repurposed at the task 45 flip, keeping its sweep and losing the
        assertion that had become a false statement about a fixed
        product: it used to pin `ablation_existence @ 0.0` under the
        opaque owner — the DEFECT's magnitude — and its own failure
        message named this rewrite as the correct response. Task 44's
        content guard was turned the same way (that report §4).

        TWO of its original jobs are gone, and both are named because
        counting them wrong is how a guard rots into decoration.

        First, ERROR-MASKING: `@unittest.expectedFailure` swallows errors
        as well as failures (skill doctrine §6), so while the red below
        was red a `make_element` that began refusing the spec would have
        printed an identical healthy `x` with nothing measured. Dropping
        the marker is what removed that hazard, so nobody should read
        this as "every red needs a vacuity guard". (Not to be confused
        with the both-poles floor further down, which this test DOES
        still carry — different hazard, same word.)

        Second, CONTINUITY, which went with the call it rode on. The old
        opaque-pole assertion was a list equality against
        `ablation_findings`, so it also asserted that no OTHER check
        fired — and that function emits two, `ablation_existence` and
        `ablation_continuity` (ink that has come apart into two or more
        reader-visible strokes). This sweep measures ink and never calls
        it, so continuity is now asserted on the checkbox's `f1-box` and
        `f1-chk` only, by the red next door. The uncovered case, stated
        rather than left to be discovered: something drawn across a
        TOGGLE or SLIDER thumb that severed it in two would cost only a
        few pixels, sit well inside the `126 ± 13` band, and be reported
        by nothing. Deliberately out of scope — this test's charter is
        magnitude — and closing it is one `assertEqual(ablation_findings(
        buried, [part]), [])` in the loop, at three more
        rasterizes, if the exposure is ever judged worth them.

        What it still does, and why it earns its rasterizes. First,
        MAGNITUDE, which the red cannot see: the red asserts SILENCE, and
        silence is the answer for a part that is in the picture at all,
        however little of it — a clipping or sizing regression leaving a
        few pixels of the check stroke showing keeps the red green and
        fails only here. Both poles are asserted against the same number
        and the same tolerance, so each row reads "an opaque owner now
        costs this part nothing".

        Second, the BOTH-POLES FLOOR, which is not hypothetical here:
        `body` and `image` furniture reads as ABSENT at both poles (faint
        `FURNITURE_INK` at width 1, under `tolerant_diff`'s ink threshold and
        the speckle floor), which is why they are outside
        `STATE_CONTROLS` — an assertion that only said "0px opaque" would
        have been satisfied by a renderer that drew no glyphs at all.
        Measuring the lit pole too is what makes the opaque number mean
        "survived the fill" rather than "was never drawn".

        Third, and the reason this sweeps rather than measuring the
        checkbox alone: the red names one composite and the fix moved all
        three at once, so a partial regression — one that taught `band()`
        about `chk_of` and not `thumb_of` — would leave the red green and
        two controls buried with nothing to say so. Toggle and slider are
        pinned as measurements rather than as prose in a docstring,
        because prose about counts has gone stale in this repo three
        times and a measurement cannot.
        """
        for kind, state, part, lit_ink, slack in STATE_CONTROLS:
            with self.subTest(kind=kind):
                buried = _control_composite(kind, "#e9e5da", state)
                opaque = _element_ink(buried, part)[0]
                lit = _element_ink(_control_composite(kind, "transparent",
                                                      state),
                                   part)[0]
                self.assertAlmostEqual(
                    opaque, lit_ink, delta=slack,
                    msg="%s's %s measured %dpx with an OPAQUE owner "
                        "against the %dpx it carries with a transparent "
                        "one: the owner's fill is eating its own control's "
                        "state glyph again" % (kind, part, opaque, lit_ink))
                self.assertAlmostEqual(
                    lit, lit_ink, delta=slack,
                    msg="%s's %s measured %dpx with a TRANSPARENT owner; "
                        "the opaque reading above can only mean the part "
                        "survived the fill while this one is nonzero"
                        % (kind, part, lit))

    def test_mutant_composed_checkbox_state_hides_under_its_opaque_owner(self
                                                                         ) -> None:
        """FLIPPED by v0.9 WP4 (Task 45). Kept its red-era name.

        Curator batch 17, from the Task 44 report §8.1 and its review's
        curator queue, 2026-08-14. The same quadruple as the composed
        CONTENT red next door, one part tag over, and it was the half
        that Task 44 deliberately did not take: `normalize_z_order`
        banded `value_of`/`attr_of` above the node that owned them while
        composed FURNITURE — `box_of`, `chk_of`, `thumb_of`, `track_of`,
        `body_of`, `x_of` — stayed at band 1, beneath its owner at 3.
        Every composite emits its parts BEFORE the owner, so one
        documented `mod backgroundColor` on the owner painted its own
        control out.

        This was a PRODUCT defect the render tier could already see, not
        a detector miss: `ablation_existence` fired correctly and said
        so. What made it worse than the content case was WHICH part
        went: the buried glyph was the one carrying STATE. A checked
        checkbox with an opaque tile drew as a plain filled rectangle —
        not as a control that was merely hard to read, but as an
        UNCHECKED one — and an agent narrating the snapshot back to the
        user would have said so. Measured on the sibling parts, same op:
        `f1-box` 92px→0, `f1-chk` 36px→0, toggle 168/126→0, slider
        248/108→0.

        The fix was the one line the red-era docstring predicted:
        `band()` reads the whole part vocabulary (`COMPOSED_PART_KEYS`)
        rather than the content half, so every composed part bands above
        the owner it is drawn on. Two things that would NOT have flipped
        it, and still would not: giving the glyph an opaque fill of its
        own would have hidden the banding rather than fixed it, and
        lifting the whole `role: "decoration"` band would have dragged
        standalone BACKDROPS up with it — the arrangement layout.md
        prescribes for parallel edges, which `TestPaintOrder`'s backdrop
        pin holds down and which the tag list still keeps beneath. The
        model-tier half of this is that class's banding pin: its `w1`
        (`body_of`) member moved out of band 1 in this same change, and
        its docstring argues the move rather than quietly absorbing it.

        RE-EARNED AT THE FLIP, one batch late (curator batch 24,
        2026-08-16) — the composed CONTENT red next door carries the
        argument and it applies here word for word. This pin's
        `assertEqual(finds, [])` was the defect's own signature while the
        marker was on and became an assertion of health when task 45 took
        it off, and only the first of those two readings is one a dead
        `ablation_findings` fails.
        """
        scene = _with_liveness_ghost(
            _control_composite("checkbox", "#e9e5da", {"checked": True}))
        finds = ablation_findings(scene, ["f1-box", "f1-chk", "z1"])
        self.assertEqual(
            [f for f in finds if f["element"] != "z1"], [],
            "the checkbox's own box and check stroke are painted under the "
            "tile that owns them: %s" % [f["raw"] for f in finds])
        self.assertDetectorSpoke(finds)

    def test_neighbour_a_transparent_owner_leaves_the_check_visible(self
                                                                    ) -> None:
        """The other pole: the same control, the same state, no fill.

        Unchanged across the task 45 flip — same name, same scene, same
        two assertions — but its job changed, so read it as the pole
        rather than as the contrast. While the red stood, this was what
        kept it meaningful: one variable moved, the owner's
        `backgroundColor`, and only a green here made the opaque
        reading evidence of BURIAL rather than of a control that was
        never drawn. That distinction is not hypothetical — `body` and
        `image` furniture reads as absent at BOTH poles, which is why
        they sit outside `STATE_CONTROLS`.

        Now that both poles are green it is the ANCHOR: this is the
        state the whole shipped corpus is in (every owner in the 24
        fixture artifacts is `backgroundColor: transparent`, which is
        why nothing noticed the defect for so long), so a regression
        that broke composed controls outright would fail here first and
        the opaque pins would only echo it.

        The ghost is curator batch 24's (2026-08-16), on the same
        measurement as the content neighbour next door: the ink
        assertion below is grade B — a positive fact about the SUBSTRATE
        that never calls the detector — so it left this pin passing with
        `ablation_findings` stubbed dead. Both controls are kept; they
        close different confounds.
        """
        scene = _with_liveness_ghost(
            _control_composite("checkbox", "transparent", {"checked": True}))
        finds = ablation_findings(scene, ["f1-box", "f1-chk", "z1"])
        self.assertEqual([f for f in finds if f["element"] != "z1"], [])
        self.assertDetectorSpoke(finds)
        # Silence has to mean "the control is in the picture", so pin the
        # ink: 36px on the check stroke, 92px on its box, measured
        # 2026-08-14 — the state glyph the defect took, and the outline
        # that made it read as unchecked rather than as missing.
        self.assertAlmostEqual(
            _element_ink(scene, "f1-chk")[0], 36, delta=6)

    def test_faint_furniture_survives_its_opaque_owner_by_measurement(self
                                                                      ) -> None:
        """Curator batch 23 item 2 (task 45 review MINOR-2), 2026-08-15.

        The two composites `STATE_CONTROLS` had to leave out. Batch 17
        excluded `body` and `image` furniture because their strokes read
        as ABSENT at both poles — faint `FURNITURE_INK` at width 1 — so the
        both-pole "an opaque owner costs this part nothing" shape cannot
        be built for them, and task 45 §7.2 generalized that into "the
        render tier cannot demonstrate the improvement" and deferred the
        item a fourth time.

        The generalization was too broad, and its own review measured
        why. The both-pole shape is unavailable; the PRE-FIX versus
        SHIPPED shape at the opaque pole alone is wide open, and it is
        the mutant shape rather than the pole-comparison shape. Re-measured
        here 2026-08-15: an `image` composite's `f1-x1` carries 208px
        under an opaque owner with the shipped banding and 0px with the
        pre-task-45 banding — at the DEFAULT `MIN_BLOB`, not at a
        loosened floor, and 208 against 0 is not a marginal reading.

        Both poles are asserted because both are measurable and they are
        one variable apart: `normalize_z_order`'s output against the same
        elements with the owner moved last, which is where task 44 left
        furniture and what `render_svg`'s array-order paint then did with
        it. That makes this a gate — the shipped order must show the
        glyph, the buried order must hide it — so a regression that
        banded furniture back down fails the first assertion, and a
        "fix" that simply stopped the owner painting at all fails the
        second.

        WHY THE POLES RUN THE OTHER WAY HERE, since it looks wrong: with
        a TRANSPARENT owner this same part measures 0px at the default
        floor (4px at `min_blob=1`). The faint stroke has almost no
        contrast against bare paper and plenty against `#e9e5da`, so the
        opaque reading is the HIGHER one. That inversion is exactly why
        batch 17's idiom could not reach these two, and reading this
        test's numbers as a mistake is the trap it is written to avoid.

        Raises:
            RuntimeError: If `make_element` refused the image tile. Said
                out loud for the reason `_control_composite` says it: a
                rejected spec returns an empty list, and both poles of an
                empty scene measure 0px and agree with each other.
        """
        ids: set[str] = set()
        errors: list[str] = []
        parts = canvas.make_element(
            {"id": "f1", "type": "rectangle", "x": 0, "y": 0, "width": 220,
             "height": 110, "kind": "image",
             "backgroundColor": "#e9e5da"}, ids, errors)
        if errors or not parts:
            raise RuntimeError("make_element refused the image tile: %s"
                               % errors)
        shipped = canvas.normalize_z_order(parts)
        # The pre-task-45 banding, reproduced by moving the owner last
        # rather than by reverting `band()`: `render_svg` paints in array
        # order, so an owner emitted after its parts covers them, which
        # is precisely what band 3 over band 1 produced.
        buried = sorted(shipped, key=lambda e: e["id"] == "f1")
        self.assertAlmostEqual(
            _element_ink(shipped, "f1-x1")[0], 208, delta=20,
            msg="the image tile's X stroke measured %dpx under an opaque "
                "owner: composed furniture is being painted over again"
                % _element_ink(shipped, "f1-x1")[0])
        self.assertEqual(
            _element_ink(buried, "f1-x1")[0], 0,
            "with the owner painted last the X stroke still measures "
            "ink: the scene no longer reproduces the pre-fix burial, so "
            "the assertion above is not measuring what it claims")


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


# The fewest dark pixels a snapshot of either span scene can hold and still
# be a picture of it. Measured 2026-08-16 at 32630e9: the wide scene renders
# 9500 and the narrow 5100, of which the rightmost node's own band is 961 in
# both — so this floor sits an order of magnitude under a healthy render and
# an order of magnitude over the zero a blank one produces. It is NOT a
# tolerance on the assertion the mutant makes; nothing between 0 and a real
# render is a state the product can reach.
PAINTED_INK_FLOOR = 1000


def _count_ink(pix: Sequence[int], pw: int, ph: int,
               x0: int, x1: int) -> tuple[int, int]:
    """Count dark pixels in one column band and in the whole raster.

    Split out of `_rightmost_node_ink` so both numbers come from ONE walk
    and one threshold — two counters could disagree about what "ink" is,
    and the whole point of the pair is that they are comparable. Pure, so
    the poles that prove the floor need no browser.

    Args:
        pix: Greyscale samples, row-major, from `read_png_gray`.
        pw: Raster width in pixels.
        ph: Raster height in pixels.
        x0: First column of the band, inclusive.
        x1: Last column of the band, exclusive; clipped to `pw`.

    Returns:
        `(band_ink, total_ink)` — dark pixels inside the band, and dark
        pixels anywhere in the raster.
    """
    band = total = 0
    hi = min(x1, pw)
    for y in range(ph):
        row = y * pw
        for x in range(pw):
            if pix[row + x] < 192:
                total += 1
                if x0 <= x < hi:
                    band += 1
    return band, total


def _painted_or_raise(total: int, span: int, pw: int) -> None:
    """Refuse to judge a band inside a raster nothing was drawn into.

    THE FLAKE THIS EXISTS FOR, and the reason it is a `raise` and not an
    assertion. `test_mutant_snapshot_cap_drops_the_rightmost_node`
    failed intermittently under `MUTANTS_RENDER=1` and its failure read
    "the rightmost node left no ink in its own column band" — which is
    word for word what the real defect looks like. A headless chromium
    screenshot that races the paint returns rc=0 with a PNG of the right
    dimensions and nothing in it, so `validate_png` passes it, the band
    is empty, and the test reports a cropped snapshot. Nothing could
    tell the two apart, and the cost was not the red herring: the run
    that flaked was a mortality sweep, where the failure was counted as
    a witness under two UNRELATED killed detectors (recorded in
    `mutants_mortality.py`) and corrupted the measurement.

    The distinction is cheap because the two states are nothing alike. A
    cropped snapshot still contains the rest of the drawing — the wide
    scene's other two nodes and the arrow between them are 8539 of its
    9500 dark pixels, and they are what the truncated raster kept while
    the red was live. A render that never painted contains zero. So an
    empty band inside an inked raster is the finding, and an empty band
    inside an empty raster is the environment.

    Raising rather than skipping, for `_browser`'s reason: the tier was
    asked for, so not measuring is a failure. What changes is only WHICH
    failure gets reported, and therefore whether a sweep counts it as
    evidence about a detector.

    Args:
        total: Dark pixels anywhere in the raster, from `_count_ink`.
        span: The scene's span, for the message.
        pw: The raster's width, for the message.

    Raises:
        RuntimeError: If the raster holds less ink than any real render
            of this scene can, which means the browser handed back a
            blank or half-painted shot and there is nothing to measure.
    """
    if total < PAINTED_INK_FLOOR:
        raise RuntimeError(
            "the span-%d snapshot came back %dpx wide with %d dark pixels "
            "in the whole raster (floor %d): the browser produced a blank "
            "or half-painted shot, so the band count below it means "
            "nothing. This is the environment, NOT a cropped snapshot — a "
            "truncated render still holds the rest of the drawing. Re-run; "
            "if it repeats, the render path is broken rather than flaky"
            % (span, pw, total, PAINTED_INK_FLOOR))


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
        RuntimeError: If the snapshot CLI exited non-zero, wrote no PNG,
            or wrote one nothing was painted into (see
            `_painted_or_raise`). Like `_browser`, this never degrades to
            a skip: the tier was asked for, so not measuring is a
            failure.
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
    ink, total = _count_ink(pix, pw, ph, x0, x1)
    _painted_or_raise(total, span, pw)
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

    def test_a_blank_raster_is_reported_as_the_environment(self) -> None:
        """The flake discriminator fires: nothing painted, nothing judged.

        Curator batch 26, 2026-08-16, from the batch brief's item 3. The
        half of `_painted_or_raise` that has to work — a raster with no
        ink in it must come back as a `RuntimeError` about the browser,
        not as the band assertion's "the rightmost node left no ink".
        Those two sentences described the same observation until today,
        which is why an intermittent blank shot was counted as evidence
        under two unrelated killed detectors in a mortality sweep.

        Ungated and browser-free on purpose, by the argument this class
        was built on: what rots here is the numbers drifting apart in
        ordinary editing, where nobody has `MUTANTS_RENDER=1` set, and a
        gated guard would notice months late. Feeding a synthetic raster
        also makes the blank case reachable at all — it is a race, and a
        test that waited for it to happen would be the flake.
        """
        blank = [255] * (40 * 10)
        band, total = _count_ink(blank, 40, 10, 5, 15)
        self.assertEqual((band, total), (0, 0),
                         "the ink counter found ink in an all-white raster")
        with self.assertRaises(RuntimeError) as caught:
            _painted_or_raise(total, WIDE_SPAN, 40)
        self.assertIn("blank", str(caught.exception))

    def test_a_painted_raster_is_judged_on_its_band(self) -> None:
        """The other half: real ink counts, and the floor stays out of it.

        Without this the discriminator above is satisfied by a floor set
        so high that every render reads as blank — which would turn the
        snapshot mutant into a permanent environmental error and read,
        from the outside, exactly like a test that had stopped failing.

        The two magnitudes are the ones measured at 32630e9 and they are
        an order of magnitude apart in each direction: `PAINTED_INK_FLOOR`
        must sit above zero and far below the 5100 dark pixels the
        NARROWER of the two real scenes produces. A synthetic raster
        carrying ink both inside and outside the band proves the counter
        separates them rather than returning the same number twice, which
        is what would make `total` useless as a control.
        """
        pw, ph = 40, 10
        pix = [255] * (pw * ph)
        for y in range(ph):
            for x in (2, 7, 8):                     # one outside, two in
                pix[y * pw + x] = 0
        band, total = _count_ink(pix, pw, ph, 5, 15)
        self.assertEqual((band, total), (2 * ph, 3 * ph))
        self.assertGreater(PAINTED_INK_FLOOR, 0)
        self.assertLess(
            PAINTED_INK_FLOOR, 5100,
            "the ink floor (%d) has risen past the 5100 dark pixels the "
            "narrow scene measured at 32630e9: every real render now "
            "reads as a blank one and the snapshot mutant can no longer "
            "fail for its own reason" % PAINTED_INK_FLOOR)
        _painted_or_raise(5100, NARROW_SPAN, 1440)


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
        """Make a scratch directory for the PNG this test asks canvas for.

        Scratch, not cache: `canvas.rasterize_svg` is the product path
        and writes where it is told, so this is an output location rather
        than a render `_rasterize` could serve from `render_cache_dir`.
        """
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
# TO THIS PAIR and no fix to either member can lift it — but it is no longer
# the harness's ceiling, and the paragraph that used to end "and permanent"
# was describing the pair as if it were the whole file. v0.9 task 50 added a
# THIRD renderer that is not `render_svg` at all: `client_ablation_findings`
# drives the real Excalidraw bundle and reads the picture the user's own
# export produces. A defect shared by tier 1 and tier 2 is therefore now
# CATCHABLE — by leaving the pair, not by improving it. `opacity` is the
# worked example and the reason the tier was built: `render_svg` emits none,
# both tiers here are blind to a 0%-opacity ghost by construction, and
# `test_mutant_opacity_ghost_is_invisible_to_tier_one` sat `expectedFailure`
# for two versions saying so before the client path flipped it.
#
# What this section is FOR is unchanged by that, and so is its honesty
# problem: parity between tier 1 and tier 2 remains a comparison of a thing
# with a rendering of itself, and the three examples it was written around
# are not permanent either.
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
# WHAT IT FOUND NEXT, and v0.9 task 46 FIXED: the same disagreement on the
# OTHER axis. The loop sized a text by `text_dims` of the UNWRAPPED string
# while `paint` WRAPS it whenever `autoResize is False and width > 0`, so a
# bound reserving one line's height was drawn four lines deep and the tail
# left the bottom of the frame. Pinned red as `test_mutant_wrapped_text_
# overruns_the_frames_bottom` (curator batch 18), unowned for a day, and
# flipped by teaching the loop the wrap — `canvas.painted_text_lines`, which
# `paint` and the bounds loop now both READ rather than each stating their
# own version of. Both members of the family were one measurement disagreeing
# with the drawing, and the fixes are corrections to that measurement.
#
# So the section holds no live defect again, and the paragraph below — which
# was written for exactly this state — is back in force.
#
# HOW THIS SECTION STAYS HONEST WITH THE DEFECTS FIXED, since it shapes
# everything below. A frame that does not contain its own ink is the only
# thing `parity_clipped` fires on, and that is a tier-1 defect by definition —
# so with tier 1 correct no DRAWING here makes the finding fire, and every
# scene that asks the product question asserts silence. Silence is not proof:
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
# overhang any scene here produces, which since task 46 fixed the wrapped
# text's bottom is again none: no scene overhangs the frame `render_svg`
# chooses for it, and the only ink this pad has to recover is what
# `_TIGHTEN` deliberately cuts off in the short-frame probe. Kept generous
# deliberately: a pad too small to clear the next overhang would report a
# partial magnitude, which reads as a small defect rather than as an
# instrument that could not see the whole of a large one.
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


def _element_ink(elements: list[dict], eid: str, pad: int = 0,
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
    blobs = tolerant_diff(_rasterize(less, w, h),
                          _rasterize(full, w, h),
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
                    frame_pad: int = 0) -> list[dict]:
    """Findings where tier 1's frame does not contain tier 1's own ink.

    Args:
        elements: The full scene.
        ids: Ids to measure, one at a time.
        frame_pad: Resize the frame under test by this much per side,
            the same seam and the same sign convention `_element_ink`
            already has. 0 — the default, and the only value the product
            question is asked at — measures the frame `render_svg`
            actually chose. A NEGATIVE value measures a frame the caller
            has deliberately made too small, which is how this function
            can be asked to assemble a real finding while tier 1 is
            correct, and a finding proven only by its own silence is not
            proven. Between Task 22 and task 46 a live defect made it
            fire unaided (the wrapped-text bottom overrun, curator batch
            18); that was always evidence with an expiry date, and this
            seam is what the expiry left behind. The generous reference
            frame is unaffected, so `PARITY_PAD` must stay larger than
            any `frame_pad` a caller shrinks by.

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
        framed = _element_ink(elements, eid, pad=frame_pad)[0]
        whole, bbox = _element_ink(elements, eid, pad=PARITY_PAD)
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


# The body text the bottom-overrun mutant hangs on: the string, the stored
# box width `paint` wraps it inside, and the line count that wrap produces
# at fontSize 16. Written as numbers rather than computed, for the reason
# `_WIDE_LABEL_W` is — a scene that derives its own regime from the code
# under test cannot notice when that code moves it out of the regime.
# `TestRenderParityRegime` is where all three are checked.
_WRAPPED_LABEL = "alpha beta gamma delta epsilon zeta eta theta"
_WRAPPED_BOX_W = 100
_WRAPPED_LINES = 4


def _wrapped_body_text(auto_resize: bool) -> list[dict]:
    """A fixed-width text long enough to wrap, plus an ablation spare.

    `paint` wraps a text when `autoResize is False and width > 0`, and
    until task 46 `render_svg`'s bounds loop sized one by `text_dims` of
    the whole UNWRAPPED string — so the two disagreed about the same
    element by construction, and `auto_resize` was the single variable
    that turned the disagreement on. It is still the single variable, and
    the scene is still built around the four lines that made the
    disagreement visible: the loop used to bound the text at its
    unwrapped height of 20 with the pad adding 40, framing ink down to
    y=60, while `paint` puts baselines every 20px from 13.6 — the third
    line's descenders landed inside, the fourth did not. Four lines is
    the smallest count that reaches past that frame, which is why the
    string is the length it is.

    `textAlign` is left DELIBERATELY, and not for symmetry with the
    centered scene next door: it keeps this mutant on the vertical axis
    alone. A centered text would also exercise the min-side widening Task
    22 landed, and two mechanisms in one scene is one mutant reporting
    about two defects.

    `n1` is the spare and it is load-bearing for the same reason it is in
    `_short_frame_probe`: `_element_ink` ablates by dropping the element
    and re-rendering, and ablating `t1` out of a one-element scene leaves
    `render_svg` with nothing live, whose "(empty artifact)" placeholder
    text would then be counted as `t1`'s own ink. Its box also sets the
    drawing's right edge well clear of the text, so the frame is generous
    horizontally and what this measures is the bottom.

    Args:
        auto_resize: The text's `autoResize`. `False` is the mutant —
            the branch `paint` wraps on, and the branch the bound used
            not to. `True` is the neighbour: one unwrapped line, which
            is exactly the extent the bounds loop measured either way.

    Returns:
        The two-element scene: text `t1`, then spare `n1`.
    """
    return [el(id="t1", type="text", x=0, y=0, width=_WRAPPED_BOX_W,
               height=20, text=_WRAPPED_LABEL, fontSize=16,
               autoResize=auto_resize, textAlign="left",
               strokeColor="#1e1e1e"),
            el(id="n1", type="rectangle", x=400, y=0, width=120, height=20,
               strokeColor="#1e1e1e")]


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

    def assertParityInstrumentSpoke(self, scene: list[dict]) -> None:
        """Fail unless `parity_findings` reports a deliberately short frame.

        `ablation_findings`' liveness ghost has no analogue here — a
        parity finding is about a frame and not about an element, so
        there is nothing to append to the scene that must be clipped.
        The `frame_pad` seam is the equivalent, and it was built for
        precisely this: at a negative value the caller measures a frame
        it has made too small itself, so the same function on the same
        scene must assemble a real finding.

        Curator batch 24 (2026-08-16), against the mortality spike's §4b.
        The two silences this serves each carried an `_element_ink(...) >
        0` control, which closes "the label drew nothing" and closes
        nothing else — `parity_findings` stubbed to `return []` passed
        both, because ink is the substrate and not the instrument. Guide
        rule 8's second half: the firing has to go through the entry
        point under test.

        Magnitude and direction are deliberately NOT pinned here. Which
        edges a shrunken frame cuts depends on glyph metrics for these
        two text scenes, and this helper's claim is only that the
        instrument is awake; `test_the_clip_instrument_still_sees_ink_
        leave_a_short_frame` owns the magnitude and the edge naming, on
        a filled box whose geometry is fixed.

        Args:
            scene: The same scene the caller just asserted silent at the
                default frame.
        """
        finds = parity_findings(scene, ["t1"], frame_pad=-_TIGHTEN)
        self.assertEqual(
            [(f["check"], f["element"]) for f in finds],
            [("parity_clipped", "t1")],
            "a frame %dpx short on every side cut none of this label's "
            "ink: %s. The silence asserted at the honest frame is then "
            "about the instrument and not about the framing"
            % (_TIGHTEN, finds))
        self.assertGreater(finds[0]["magnitude"], 0)

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
                ink = _element_ink(scene, "x-1")[0] > 0
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
                    _element_ink(tm._one_of(etype), "x-1")[0],
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
        ever proves silences. Every scene that asks the product question
        about a CORRECT frame — both flipped mutants and both their
        neighbours — asserts NO finding, and an instrument that had gone
        blind would satisfy all four while reporting health forever
        (`test_mutants.TestCoverage.test_silence_only_mutant_does_not_
        prove_its_check` is the same rule one tier up). The bottom-
        overrun red added in curator batch 18 did make the finding fire
        on a drawing for a day, which is why it could carry a scaffold
        asserting a magnitude — but that was a live defect someone was
        expected to fix, task 46 fixed it, and this test was written to
        outlast exactly that.

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
        whole = _element_ink(scene, "r1", pad=PARITY_PAD)[0]
        framed = _element_ink(scene, "r1")[0]
        tight = _element_ink(scene, "r1", pad=-_TIGHTEN)[0]
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
        finds = parity_findings(scene, ["r1"],
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
        self.assertEqual(parity_findings(scene, ["r1"]), [],
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
        finds = parity_findings(_left_edge_label(20), ["t1"])
        self.assertEqual(
            finds, [],
            "the label's leading glyphs are painted outside the viewBox "
            "render_svg computed for its own markup: %s"
            % [f["raw"] for f in finds])

    def test_neighbour_honest_label_width_is_framed_whole(self) -> None:
        """With the stored width honest, tier 1's frame holds all its ink.

        The other pole: same label, same anchoring, same position, and
        the single variable is whether the stored width matches the
        string. It keeps the test above meaningful in both directions —
        without it that test would be satisfied by a renderer that framed
        nothing correctly, or by an instrument that reported a clip on
        every scene it was shown.

        Kept its red-era name and, until curator batch 23 (2026-08-15),
        its red-era prose: it called itself "the control that keeps the
        RED above meaningful" when that red flipped at `0b7e7ba` (Task
        22). Task 46's C3 caught the sentence and left it deliberately,
        having corrected the identical one in its own neighbour, rather
        than widening that diff into a docstring it had no other reason
        to open. Both halves of the pair are green now, and what the pair
        pins is a GATE rather than a defect — one stored width frames
        whole, the other does not — so neither member is the other's
        control any more. That is a change of kind and not just of
        tense, which is why it is written out instead of edited away.
        """
        scene = _left_edge_label(_WIDE_LABEL_W)
        self.assertEqual(parity_findings(scene, ["t1"]), [])
        # A label that drew nothing at all would satisfy that too, so pin
        # that there was ink to lose: silence has to mean "framed whole",
        # never "absent from both frames".
        self.assertGreater(_element_ink(scene, "t1")[0], 0)
        self.assertParityInstrumentSpoke(scene)

    def test_mutant_wrapped_text_overruns_the_frames_bottom(self) -> None:
        """FLIPPED by v0.9 task 46. Kept its red-era name.

        A wrapped text used to be painted below the frame drawn around
        it. Found during the Task 22 cycle (report §8 C1, review §7) and
        confirmed pre-existing there — the repro read identically on
        `d0cfcd8` and on the fixed `0b7e7ba` — then pinned red by curator
        batch 18 with the fix unowned, and scheduled here by the gate.

        The same root shape Task 22 fixed — `render_svg`'s bounds loop
        disagreeing with what `paint` actually does — on the other axis
        and through a different mechanism, which is why that fix did not
        touch it. The loop sized a text by `text_dims` of the UNWRAPPED
        string; `paint` WRAPS it whenever `autoResize is False and
        width > 0`. So the loop reserved one line's height and the
        renderer drew four, and the tail ran off the bottom of the
        viewBox the loop had just computed. Measured on this scene: the
        frame ended at drawing y=60 while the baselines are 13.6, 33.6,
        53.6 and 73.6, and `parity_clipped` reported 281 of this text's
        1421 ink pixels going off the bottom edge.

        What the reader lost was a whole line of the text, and lost it
        the way absence is always lost — with nothing left behind to be
        suspicious of. This export is the agent's own view of its own
        drawing, so a drawing that said "alpha beta gamma delta epsilon
        zeta" was what the agent read, while the model held two more
        words it would never see it dropped.

        THE FRAME WAS SIMULTANEOUSLY TOO WIDE, and the asymmetry is the
        point rather than a curiosity. The same unwrapped measurement
        gave the loop 338px of width for glyphs that occupy 100, which
        is slack: a frame larger than its ink loses nothing and the
        reader is never lied to. Only the vertical error cost picture.
        So the fix had to teach the loop to measure the WRAPPED extents
        — both of them — rather than pad the bottom, and the trap it had
        to refuse is that widening `render_svg`'s 40px pad turns this
        test green while leaving the defect, exactly as it would have
        for Task 22's: a threshold moved is a threshold the next text
        crosses, silently. What landed is `painted_text_lines`: the wrap
        stated once and READ by the bounds loop rather than restated
        there, so the frame and the ink cannot drift apart again the way
        they did here.

        AMENDMENT (Task 22 review F2, 2026-08-14) — the min side shared
        this root cause and was settled by the same change. Task 22's
        leftward widening for a centered label was computed from that
        same unwrapped width, so for a label `paint` wraps the frame was
        widened leftward by a run the glyphs never make: 119px of dead
        margin on the reviewer's synthetic scene, whose viewBox reads
        `-159 -40 719 100` before the fix and `-40 -40 600 160` after —
        the dead margin going in the same change that lowers the bottom
        clear of the text. In the corpus, 15 of the 39 under-stored
        centered labels are also wrapped, with spurious widenings up to
        30.5px; all 15 were LATENT, none of them its drawing's leftmost
        element, which is why the fixture replay was byte-identical
        before the fix and why none of the 15 moves a viewBox after it.
        No separate mutant, deliberately: it was one defect with two
        symptoms, and whoever taught the loop about wrapping fixed both
        or had not finished.
        """
        finds = parity_findings(_wrapped_body_text(False), ["t1"])
        self.assertEqual(
            finds, [],
            "the tail of a wrapped text is painted below the viewBox "
            "render_svg computed for its own markup: %s"
            % [f["raw"] for f in finds])

    def test_neighbour_unwrapped_text_is_framed_whole(self) -> None:
        """With `autoResize` on, the bound and the drawn text agree.

        The other pole: same string, same box, same position, and the
        single variable is whether `paint` wraps. `autoResize: True` is
        the branch where it does not, so the one unwrapped line it draws
        is precisely the extent the bounds loop measured. While the
        mutant was red that made its silence mean "the loop measured the
        wrong thing" rather than "this scene is too big for its frame";
        green, the pair says the fix reached the wrapping branch WITHOUT
        moving the branch that was already right, which is the half a
        renderer that framed nothing correctly would fail.

        The ungated arithmetic of both poles is in
        `TestRenderParityRegime`, which is where a revert with no browser
        gets caught. This is the same claim in ink.
        """
        scene = _wrapped_body_text(True)
        self.assertEqual(parity_findings(scene, ["t1"]), [])
        # A text that drew nothing at all would satisfy that too: silence
        # has to mean "framed whole", never "absent from both frames".
        self.assertGreater(_element_ink(scene, "t1")[0], 0)
        self.assertParityInstrumentSpoke(scene)


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

    def test_the_body_text_still_wraps_to_more_lines_than_it_is_bound_for(
            self) -> None:
        """`_WRAPPED_LABEL` still wraps to four lines past a one-line bound.

        The regime guard for the bottom-overrun scene, and it outlived
        that scene's red on purpose: all three claims are about `paint`
        and the font metrics, which task 46's fix does not touch, so it
        keeps saying the scene exercises wrapping now that the bounds
        loop has learned to measure it. Green before the flip and green
        after.

        Its sense inverted the way `test_the_wide_label_still_overhangs_
        the_frame_on_the_left`'s did. While the mutant was red this said
        "the text still reaches past a one-line bound, so there is an
        overrun left to pin"; green, the same three numbers are what
        makes the mutant's silence mean the loop measures the wrap — a
        scene whose text stopped needing more than one line's height
        would assert silence while observing nothing.

        If `text_dims` stops returning 338 the wrap is about a different
        string; if the greedy wrap stops producing four lines the scene
        may no longer reach past a one-line bound at all; and if the last
        baseline climbs back above that bound the mutant is inside its
        frame for a reason that has nothing to do with the defect it
        pins. The third is the one that would go quiet most easily — a
        `lineHeight` default change moves it without touching either
        number above.
        """
        self.assertEqual(canvas.text_dims(_WRAPPED_LABEL, 16), (338, 20),
                         "the font metrics moved: re-measure the wrapped "
                         "scene's regime, and the frame arithmetic in "
                         "test_the_bounds_loop_frames_the_wrapped_text_it_"
                         "used_to_clip with it")
        lines = canvas.wrap_label_text(_WRAPPED_LABEL, _WRAPPED_BOX_W,
                                       16).split("\n")
        self.assertEqual(len(lines), _WRAPPED_LINES,
                         "paint wraps this text to %d lines, not the %d "
                         "the scene was built around: %r"
                         % (len(lines), _WRAPPED_LINES, lines))
        # `paint` puts line i's baseline at y + fs*0.85 + i*fs*lineHeight,
        # so the fourth is at 73.6 — against the drawing y=60 a frame
        # sized on the text's UNWRAPPED height plus the pad would end at,
        # which is the frame this scene used to get.
        last_baseline = 16 * 0.85 + (_WRAPPED_LINES - 1) * 16 * 1.25
        self.assertGreater(
            last_baseline, 20 + SVG_PAD,
            "the last line's baseline is at %g, inside the y=%d bottom a "
            "one-line bound plus the %dpx pad would give this scene: the "
            "wrap is no longer asking the bounds loop for anything"
            % (last_baseline, 20 + SVG_PAD, SVG_PAD))

    def test_the_bounds_loop_frames_the_wrapped_text_it_used_to_clip(self
                                                                    ) -> None:
        """Task 46's fix, read off the markup rather than off the pixels.

        The bottom-side twin of `test_the_bounds_loop_frames_the_centered
        _label_it_used_to_clip`, and it exists for the same reason: the
        mutant next door proves this in ink and needs a browser to do it,
        so without this the whole claim would go unwatched in every
        commit made with `MUTANTS_RENDER` unset — which is how the defect
        would come back unnoticed.

        The claim is the one `parity_clipped` makes in pixels, in
        arithmetic: the viewBox's bottom edge sits at or below the lowest
        ink `paint` emits. Descenders are the reason the comparison is
        against the fourth line's whole em box rather than its baseline —
        a frame ending exactly on the baseline still cuts the tails off
        `p` and `g`, which is a loss the eye reads as a different word.

        WHY THIS STAYS AN INEQUALITY (curator batch 23 item 6, from task
        46's review MINOR-1, 2026-08-15). The review is right that this
        test does not hold the line alone: under the family's own trap —
        revert the loop to the unwrapped string AND widen the pad to 100
        — the bottom lands at 120 against an ink bottom of 80 and this
        passes. The review's suggested repair was exact equality against
        `SVG_PAD`, matching the centered pin's style, with the
        over-specification worry (another element could come to set the
        bottom edge) noted as the reason not to insist.

        Measured, and the repair does not work. Under that same cheat the
        margin is 40.0 — EXACTLY `SVG_PAD` — so equality passes too, and
        for a reason no assertion on this axis can escape: widening the
        pad by 60 compensates precisely for the 60px of line height the
        reverted loop fails to reserve, and the resulting frame is
        numerically indistinguishable from the correct one. No function
        of (bottom, ink_bottom) separates them.

        What separates them is the PAIR, exactly as the review said: the
        control pin next door asserts an honest scene's frame is
        unchanged, and the widened pad moves it. So the note rather than
        the change, and the note is worth more than the change would have
        been — it records that a plausible tightening was tried against
        the cheat it was proposed for and measured not to catch it. The
        over-specification argument is secondary and also holds: `n1`
        bottoms at y=20 against the text's 80 today, so the text does set
        this edge, but that is a property of the scene and not of the
        claim.
        """
        svg, _w, _h = canvas.render_svg(_wrapped_body_text(False))
        dims = _SVG_DIMS.search(svg)
        self.assertIsNotNone(dims, "cannot parse the <svg> tag")
        bottom = float(dims.group(4)) + float(dims.group(6))
        # the scene puts the text at y=0, so the fourth line's baseline is
        # at 73.6 and its descenders run under it to the bottom of the
        # line box: y + fs*1.25*lines = 80.
        ink_bottom = 16 * 1.25 * _WRAPPED_LINES
        self.assertGreaterEqual(
            bottom, ink_bottom,
            "the viewBox ends at %g but the wrapped text's last line runs "
            "to %g: render_svg is framing its own markup short at the "
            "bottom again" % (bottom, ink_bottom))

    def test_an_unwrapped_text_keeps_the_frame_its_stored_extents_gave_it(
            self) -> None:
        """Teaching the loop the wrap taught it nothing about `autoResize`.

        The control for the test above, in the same arithmetic and for
        the same reason `test_the_honest_label_keeps_the_frame_the_raw_
        origin_gave_it` is the control for its neighbour. `paint` wraps
        only on `autoResize is False and width > 0`, so on the other
        branch the bound is still the stored-or-estimated extent of the
        string as written — `y - SVG_PAD` to `y + height + SVG_PAD`.

        Without this a loop that wrapped EVERY text would pass the test
        above while re-sizing the frame of every drawing in the corpus
        that carries an `autoResize: True` label narrower than its own
        box, which is most of them.
        """
        svg = canvas.render_svg(_wrapped_body_text(True))[0]
        dims = _SVG_DIMS.search(svg)
        self.assertIsNotNone(dims, "cannot parse the <svg> tag")
        miny, height = float(dims.group(4)), float(dims.group(6))
        self.assertEqual(
            (miny, height), (float(-SVG_PAD), float(20 + 2 * SVG_PAD)),
            "an autoResize text's frame moved to (%g, %g): the wrap is "
            "being applied on the branch `paint` does not wrap on"
            % (miny, height))

    def test_the_edge_attribution_still_names_the_side_ink_escapes(self
                                                                   ) -> None:
        """`_clipped_edges` reports each side, and "" only when contained.

        The half of the clip instrument that needs no browser, and the
        reason it is pinned at all is the Task 22 flip. The two scenes
        that ask about a correct frame — the centered-label mutant and
        its honest-width neighbour — are both SILENCES, because the
        finding fires only on a frame short of its own ink and the
        min-side fix stopped tier 1 producing one there. An
        `_clipped_edges` that returned "" for everything would leave both
        of them green with the instrument blind, and a check proven only
        by silences is not proven
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


def rendered_text(elements: list[dict], eid: str) -> dict[str, float]:
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

    Returns:
        `{"ink", "height", "contrast"}` — ink pixels, the ink's height in
        pixels, and the darkest pixel's WCAG ratio against `SVG_GROUND`.
        An element that drew nothing reports zeros and a ratio of 1.0.
    """
    bbox = _element_ink(elements, eid, min_blob=1)[1]
    if bbox is None:
        return {"ink": 0, "height": 0, "contrast": 1.0}
    w, _h, pix = read_png_gray(_shot(elements))
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

    def test_text_at_the_floor_still_carries_its_ink(self) -> None:
        """At `MIN_FONT_FLOOR` the word survives rasterization.

        The upper pole. Without it the lower pole proves only that this
        instrument can call something illegible, which any instrument
        that called everything illegible would also manage.
        """
        got = rendered_text(tm._styled_scene(font_size=MIN_FONT_FLOOR),
                            "t1")
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
                              "t1")
        at_floor = rendered_text(tm._styled_scene(font_size=MIN_FONT_FLOOR),
                                 "t1")
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


class TestRenderCache(unittest.TestCase):
    """The render cache, and the browser choice its key is built on.

    Content-addressed, persistent, and NOT the scratch directory.

    Deliberately NOT gated, for the reason `TestRenderTierEvidence` gives:
    what these pin is the KEY and the hit/miss bookkeeping, and with
    `subprocess.run` stubbed neither needs a browser. Gating them would
    put the tests for the thing that avoids browser starts behind a flag
    that starts browsers.

    The stub is also what makes the central claim testable at all. "A hit
    costs no browser start" is not observable from the outside — a warm
    run and a cold run return the same bytes, and only the wall clock
    tells them apart. Counting calls to `subprocess.run` states it
    directly, so a future edit that reintroduces a per-test cache fails
    here instead of quietly costing two minutes a run.
    """

    def setUp(self) -> None:
        """Point the cache at an empty directory this test owns.

        The stand-in browser is a REAL file, because the key stats the
        binary for its size and mtime — a fake path would raise before it
        ever reached the behaviour under test.
        """
        self.cache = tempfile.mkdtemp(prefix="render-cache-test-")
        self.addCleanup(shutil.rmtree, self.cache, ignore_errors=True)
        env = mock.patch.dict(os.environ,
                              {"MUTANTS_RENDER_CACHE": self.cache})
        env.start()
        self.addCleanup(env.stop)
        # an ambient opt-in must not decide what these measure; patch.dict
        # restores the whole mapping on stop, so popping here is temporary
        os.environ.pop("MUTANTS_RENDER_BROWSER", None)
        self.binary = self._fake_binary("chromium-build-1", b"build one")
        found = mock.patch.object(canvas, "find_browsers",
                                  lambda: [str(self.binary)])
        found.start()
        self.addCleanup(found.stop)
        self.starts: list[list[str]] = []

    def _fake_binary(self, name: str, content: bytes) -> Path:
        """Write a stand-in browser binary somewhere `os.stat` can see it.

        Args:
            name: Filename under this test's own directory.
            content: Bytes to write — length is half of what the key
                fingerprints, so distinct content means a distinct key.

        Returns:
            The path written.
        """
        path = Path(self.cache).parent / ("%s-%s" % (name, os.getpid()))
        path.write_bytes(content)
        self.addCleanup(path.unlink, missing_ok=True)
        return path

    def _stub_browser(self, png: bytes = b"\x89PNG\r\n\x1a\nstub"
                      ) -> Callable[..., subprocess.CompletedProcess]:
        """A `subprocess.run` that records the call and writes the PNG.

        Args:
            png: Bytes to leave at the `--screenshot=` path.

        Returns:
            A callable with `subprocess.run`'s shape, appending each argv
            it is handed to `self.starts`.
        """
        def run(argv: list[str], **_kw: Any) -> subprocess.CompletedProcess:
            self.starts.append(argv)
            for arg in argv:
                if arg.startswith("--screenshot="):
                    Path(arg.split("=", 1)[1]).write_bytes(png)
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        return run

    def test_the_same_document_gets_the_same_key(self) -> None:
        """Identical inputs address the same entry — the point of a cache."""
        self.assertEqual(_cache_key("<svg/>", 240, 160),
                         _cache_key("<svg/>", 240, 160))

    def test_the_key_changes_with_the_browser_binary(self) -> None:
        """Two chromium builds disagree about anti-aliasing, so two keys.

        This is the hazard that made the binary part of the key before
        the cache was ever shared: a markup-keyed cache served build
        151's pixels for build 131 and reported a contrast ratio the
        build under test does not produce.
        """
        first = _cache_key("<svg/>", 240, 160)
        other = self._fake_binary("chromium-build-2", b"build two")
        with mock.patch.object(canvas, "find_browsers", lambda: [str(other)]):
            self.assertNotEqual(first, _cache_key("<svg/>", 240, 160))

    def test_the_key_changes_when_the_browser_is_upgraded_in_place(
            self) -> None:
        """Same path, new binary, new key — the routine upgrade case.

        A playwright build carries its number in its path, so a path
        comparison happens to distinguish those. `/snap/bin/chromium`
        does not: an unattended `snap refresh` swaps the binary and
        leaves the path alone. Keyed on the path, every cached render
        would outlive the upgrade and be served as fresh measurement
        forever — which is the failure this whole file exists to make
        impossible, arrived at through the cache instead of the code.
        """
        before = _cache_key("<svg/>", 240, 160)
        self.binary.write_bytes(b"a different build, and a longer one")
        os.utime(self.binary, ns=(0, 10 ** 9))
        self.assertNotEqual(before, _cache_key("<svg/>", 240, 160),
                            "an in-place browser upgrade left the key "
                            "unchanged: the cache now outlives the binary "
                            "it was measured with")

    def test_a_snap_browser_is_identified_by_its_revision(self) -> None:
        """A snap refresh must change the key, and the stat cannot see it.

        `/snap/bin/chromium` is a symlink to `/usr/bin/snap`, so size and
        mtime describe the launcher, not the browser — they are identical
        either side of a `snap refresh`. Only `/snap/<name>/current`
        moves. Skipped where there is no snap layout to read, because the
        alternative is asserting against a path this machine invented.
        """
        snap = Path("/snap/bin/chromium")
        current = Path("/snap/chromium/current")
        if not (snap.exists() and current.is_symlink()):
            self.skipTest("no snap chromium layout on this machine")
        with mock.patch.object(canvas, "find_browsers", lambda: [str(snap)]):
            identity = _browser_identity()
        self.assertIn(os.readlink(current), identity.split("\0"),
                      "a snap browser's identity does not carry its "
                      "revision, so a refresh would not change the key")

    def test_the_key_changes_with_the_html_wrapper(self) -> None:
        """The page the SVG is rendered INSIDE is a render input too.

        `_html_document`'s `margin:0` is what makes the screenshot's
        origin the drawing's origin; a `@font-face` or a background added
        there would move pixels exactly as a chromium flag does. Hashing
        the fragment rather than the document left that hole, and the
        miss below is what closes it: the same SVG under a changed
        wrapper must cost a fresh browser start, not serve the old
        pixels.
        """
        with mock.patch.object(subprocess, "run", self._stub_browser()):
            _rasterize("<svg>wrapped</svg>", 240, 160)
            self.assertEqual(len(self.starts), 1)
            with mock.patch.object(
                    sys.modules[__name__], "_html_document",
                    lambda svg: "<!doctype html><html><body "
                                "style='margin:24px'>%s</body></html>" % svg):
                _rasterize("<svg>wrapped</svg>", 240, 160)
        self.assertEqual(len(self.starts), 2,
                         "a changed HTML wrapper was served from cache")

    def test_the_key_changes_with_the_chrome_flags(self) -> None:
        """Flags move pixels by design, so they cannot sit outside the key.

        `CHROME_FLAGS` exists because device scale, colour profile and
        subpixel text all move pixels between machines. Serving a render
        made under different flags answers a different question with the
        old answer.
        """
        first = _cache_key("<svg/>", 240, 160)
        with mock.patch.object(sys.modules[__name__], "CHROME_FLAGS",
                               (*CHROME_FLAGS, "--force-device-scale-factor=2")):
            self.assertNotEqual(first, _cache_key("<svg/>", 240, 160))

    def test_the_key_changes_with_the_window_size(self) -> None:
        """The window decides how much of the document is in the shot.

        No caller today shoots one document at two sizes — every one
        derives `w`/`h` from the same `<svg>` tag it derives the markup
        from — so this splits no entry that exists. It is pinned so that
        the first caller that does cannot be handed the wrong crop.

        Attributed by curator batch 23 item 12 (task-perf-report
        candidate 3, 2026-08-15), because the origin is the interesting
        part and it was about to be lost. The window size was ABSENT from
        the key until that task, and the omission was latent for exactly
        the reason above: no caller exercised it, so no test could have
        failed and no run could have been wrong. Had a caller been added
        first, the cache would have served the wrong crop and nothing
        would have flagged it.

        The class this belongs to, stated so it is recognisable next
        time: CONTENT-ADDRESSING THAT OMITS A FIELD WHICH CHANGES THE
        CONTENT. A hash of some of the inputs is not a content address,
        it is a content address for a different question, and it fails
        silently and permanently — every read is a hit, every hit is
        confident, and the answer is stale. It is worth cataloguing as a
        GREEN entry rather than a red because it is now closed: the value
        here is the shape, not an open defect. Its two live siblings are
        named in `_cache_key` (the browser behind a launcher indirection,
        narrowed but not eliminated) and `render_cache_dir` (the system
        font stack, which no cheap fingerprint reaches).
        """
        self.assertNotEqual(_cache_key("<svg/>", 240, 160),
                            _cache_key("<svg/>", 240, 161))

    def test_a_miss_starts_the_browser_and_fills_the_cache(self) -> None:
        """The first render pays a browser start and leaves an entry."""
        with mock.patch.object(subprocess, "run", self._stub_browser()):
            got = _rasterize("<svg>one</svg>", 240, 160)
        self.assertEqual(got, b"\x89PNG\r\n\x1a\nstub")
        self.assertEqual(len(self.starts), 1)
        entry = Path(self.cache) / (
            _cache_key(_html_document("<svg>one</svg>"), 240, 160) + ".png")
        self.assertTrue(entry.exists(), "the miss cached nothing")

    def test_a_cached_render_starts_no_browser(self) -> None:
        """The second ask for one document costs nothing — the whole point.

        A full render tier run asks for 138 renders of 78 distinct
        documents. Every one of those 60 repeats used to be a chromium
        start, because the cache lived in a directory `tearDown` deleted.
        """
        with mock.patch.object(subprocess, "run", self._stub_browser()):
            first = _rasterize("<svg>two</svg>", 240, 160)
            second = _rasterize("<svg>two</svg>", 240, 160)
        self.assertEqual(first, second)
        self.assertEqual(len(self.starts), 1,
                         "a cached document started the browser again")

    def test_the_cache_outlives_the_scratch_directory(self) -> None:
        """Removing a test's scratch dir must not cost the cached pixels.

        The separation this pins is not cosmetic: sharing one directory
        for both is what made `TestSnapshotFraming`'s two tests collide
        on `artifact 'wide' already exists`, because those use their
        scratch dir as a canvas PROJECT root and a reused root already
        holds the artifact they create.
        """
        with mock.patch.object(subprocess, "run", self._stub_browser()):
            _rasterize("<svg>three</svg>", 240, 160)
            scratch = _mkworkdir()
            shutil.rmtree(scratch, ignore_errors=True)
            _rasterize("<svg>three</svg>", 240, 160)
        self.assertEqual(len(self.starts), 1)
        self.assertNotIn(str(render_cache_dir()), scratch)

    def test_a_render_that_wrote_no_png_caches_nothing(self) -> None:
        """A failed render must not leave an entry a later run would trust.

        The failure is raised, so the run is already lost; what matters
        is that the NEXT run retries instead of serving whatever was on
        disk when chromium died.
        """
        def dead(argv: list[str], **_kw: Any) -> subprocess.CompletedProcess:
            self.starts.append(argv)
            return subprocess.CompletedProcess(argv, 3, b"", b"boom")

        with mock.patch.object(subprocess, "run", dead), \
                self.assertRaises(RuntimeError) as caught:
            _rasterize("<svg>four</svg>", 240, 160)
        self.assertIn("wrote no PNG", str(caught.exception))
        self.assertEqual(list(Path(self.cache).glob("*.png")), [])

    def test_the_default_browser_is_the_one_find_browsers_ranks_first(
            self) -> None:
        """No env var means no re-ranking — `headless_shell` stays second.

        The corpus is calibrated under the full `chrome` build, and this
        pins that a faster binary further down the list cannot quietly
        become the instrument.
        """
        with mock.patch.object(canvas, "find_browsers",
                               lambda: ["/x/chrome", "/x/headless_shell"]):
            self.assertEqual(_browser(), "/x/chrome")

    def test_an_explicit_browser_request_is_honoured(self) -> None:
        """An explicit request wins, so a cold run can opt into the speed.

        `MUTANTS_RENDER_BROWSER` selects by substring, which is how a cold
        run gets `headless_shell`'s measured 2.6x without moving what the
        default calibrates against.
        """
        with mock.patch.object(canvas, "find_browsers",
                               lambda: ["/x/chrome", "/x/headless_shell"]), \
                mock.patch.dict(os.environ,
                                {"MUTANTS_RENDER_BROWSER": "headless_shell"}):
            self.assertEqual(_browser(), "/x/headless_shell")

    def test_an_unmatched_browser_request_raises(self) -> None:
        """Asking for a build that is not here is an environmental failure.

        Falling back silently would hand back a different build than the
        one asked for, which is precisely how a 4.62:1 contrast reading
        got attributed to a build that renders 5.51:1.
        """
        with mock.patch.object(canvas, "find_browsers",
                               lambda: ["/x/chrome"]), \
                mock.patch.dict(os.environ,
                                {"MUTANTS_RENDER_BROWSER": "firefox"}), \
                self.assertRaises(RuntimeError) as caught:
            _browser()
        self.assertIn("matched none", str(caught.exception))

    def test_the_cache_directory_is_created_and_relocatable(self) -> None:
        """`MUTANTS_RENDER_CACHE` moves it; the default sits under $HOME.

        Under `$HOME` is a requirement, not a preference — a snap-confined
        chromium cannot see the real `/tmp`, so a cache outside `$HOME`
        would be unwritable by the browser that has to fill it.
        """
        nested = Path(self.cache) / "made" / "on" / "demand"
        with mock.patch.dict(os.environ,
                             {"MUTANTS_RENDER_CACHE": str(nested)}):
            self.assertEqual(render_cache_dir(), nested)
            self.assertTrue(nested.is_dir())
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MUTANTS_RENDER_CACHE", None)
            self.assertIn(str(Path.home()), str(render_cache_dir()))


class TestClientRenderCache(unittest.TestCase):
    """The client tier's key, and the session bookkeeping built on it.

    The sibling of `TestRenderCache` and ungated for the same reason:
    what these pin is the KEY and the hit/miss accounting, and with the
    session stubbed neither needs a browser, a server or a bundle. Gating
    the tests for the thing that avoids sessions behind a flag that starts
    sessions would be the same mistake, one tier along.

    The stub matters more here than it does for tier 2. "A hit costs no
    session" is the difference between a warm run of this tier costing
    milliseconds and costing six seconds a scene, and it is invisible from
    the outside — both paths return the same PNG. Counting calls to
    `_client_session` states it directly.
    """

    def setUp(self) -> None:
        """Empty cache, a stand-in browser, and a clean bundle memo.

        The browser is a REAL file because the key stats it, as
        `TestRenderCache` explains. `_bundle_identity` is memoized for the
        process, so its cache is cleared on the way in AND on the way out
        — a test that points `_web_root` somewhere else and leaves the
        memo holding that answer would give every later render in this
        interpreter a key computed against a bundle that is not there.
        """
        self.cache = tempfile.mkdtemp(prefix="client-cache-test-")
        self.addCleanup(shutil.rmtree, self.cache, ignore_errors=True)
        env = mock.patch.dict(os.environ,
                              {"MUTANTS_RENDER_CACHE": self.cache})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("MUTANTS_RENDER_BROWSER", None)
        self.binary = Path(self.cache).parent / ("client-chrome-%d"
                                                 % os.getpid())
        self.binary.write_bytes(b"a stand-in browser")
        self.addCleanup(self.binary.unlink, missing_ok=True)
        found = mock.patch.object(canvas, "find_browsers",
                                  lambda: [str(self.binary)])
        found.start()
        self.addCleanup(found.stop)
        _bundle_identity.cache_clear()
        self.addCleanup(_bundle_identity.cache_clear)
        self.scene = [el(id="n1", type="rectangle", x=0, y=0, width=40,
                         height=20, customData={"role": "node"})]
        self.sessions: list[dict[str, list[dict]]] = []

    def _stub_session(self, png: bytes = b"\x89PNG\r\n\x1a\nstub"
                      ) -> Callable[[dict[str, list[dict]]], dict[str, bytes]]:
        """A `_client_session` that records what it was asked to render.

        Args:
            png: Bytes to return for every variant.

        Returns:
            A callable with `_client_session`'s shape, appending each
            variants mapping it is handed to `self.sessions`.
        """
        def session(variants: dict[str, list[dict]]) -> dict[str, bytes]:
            self.sessions.append(variants)
            return dict.fromkeys(variants, png)
        return session

    def _fake_bundle(self, marker: bytes) -> Path:
        """Write a stand-in web bundle and point `_web_root` at it.

        Args:
            marker: Contents of the one file in the fake tree — its
                length is half of what the identity fingerprints.

        Returns:
            The bundle root, already patched in for this test's lifetime.
        """
        root = Path(tempfile.mkdtemp(prefix="client-bundle-", dir=self.cache))
        (root / "assets").mkdir()
        (root / "assets" / "index-AAAA.js").write_bytes(marker)
        patched = mock.patch(__name__ + "._web_root", lambda: root)
        patched.start()
        self.addCleanup(patched.stop)
        _bundle_identity.cache_clear()
        return root

    def test_the_same_scene_gets_the_same_key(self) -> None:
        """Identical inputs address the same entry — the point of a cache."""
        self.assertEqual(_client_cache_key(self.scene),
                         _client_cache_key(self.scene))

    def test_the_key_changes_with_the_scene(self) -> None:
        """A different drawing is a different render, so a different key."""
        moved = [dict(self.scene[0], x=99)]
        self.assertNotEqual(_client_cache_key(self.scene),
                            _client_cache_key(moved))

    def test_the_key_changes_with_the_app_bundle(self) -> None:
        """The NEW field: a rebuilt app is a different renderer.

        Tier 2's key has no analogue of this and does not need one — it
        hashes the document it drew itself. Here the drawing is made by
        `exportToBlob` inside the committed Vite bundle, so a frontend
        rebuild changes the renderer without changing one byte of Python,
        and a key blind to it would serve the OLD app's pixels as a
        measurement of the new one. That is the wrapper-in-key hole (perf
        review F2) at the scale of the whole client.
        """
        self._fake_bundle(b"bundle one")
        before = _client_cache_key(self.scene)
        root = self._fake_bundle(b"bundle two, rebuilt and longer")
        self.assertNotEqual(before, _client_cache_key(self.scene),
                            "a rebuilt app bundle left the client cache key "
                            "unchanged: renders made by the old app are now "
                            "served as measurements of the new one")
        self.assertIn(str(root), str(_web_root()))

    def test_the_bundle_identity_sees_a_file_change_in_place(self) -> None:
        """Not just the filenames — a chunk edited in place moves too.

        Vite content-addresses its own filenames, so a normal rebuild
        renames chunks and any walk of the tree would notice. This pins
        the case that does NOT rename: a file rewritten under its existing
        name, which is what a hand-patched bundle or a partial copy looks
        like. Size and mtime carry it, the same two fields
        `_browser_identity` leans on.
        """
        root = self._fake_bundle(b"first")
        before = _bundle_identity()
        chunk = root / "assets" / "index-AAAA.js"
        chunk.write_bytes(b"second, and a different length")
        os.utime(chunk, ns=(0, 10 ** 9))
        _bundle_identity.cache_clear()
        self.assertNotEqual(before, _bundle_identity())

    def test_the_key_changes_when_canvas_py_changes(self) -> None:
        """`canvas.py` normalizes the scene the client is handed.

        Tier 2 needs no field for `render_svg` because its markup IS the
        hashed document. This path never sees the pipeline's output —
        elements go into `Store.apply_batch` and pixels come back — so the
        transform is keyed by the product's own source fingerprint
        instead. Blunt on purpose: an unrelated `canvas.py` edit costs a
        cold session, which is seconds, and the alternative is a second
        implementation of the pipeline living here to be keyed on.
        """
        before = _client_cache_key(self.scene)
        with mock.patch.object(canvas, "own_source_hash", lambda: "deadbeef"):
            self.assertNotEqual(before, _client_cache_key(self.scene))

    def test_the_key_changes_with_the_browser_and_the_window(self) -> None:
        """The three fields shared with tier 2 still split entries here.

        Asserted together because they are one borrowed decision rather
        than three: `TestRenderCache` argues each of them at length
        against tier 2's key, and what this owes is only that the client
        key really carries them too.
        """
        base = _client_cache_key(self.scene)
        other = Path(self.cache).parent / ("client-chrome2-%d" % os.getpid())
        other.write_bytes(b"a different stand-in browser, longer")
        self.addCleanup(other.unlink, missing_ok=True)
        with mock.patch.object(canvas, "find_browsers", lambda: [str(other)]):
            self.assertNotEqual(base, _client_cache_key(self.scene))
        with mock.patch(__name__ + ".CLIENT_FLAGS",
                        (*CLIENT_FLAGS, "--blink-settings=imagesEnabled=false")):
            self.assertNotEqual(base, _client_cache_key(self.scene))
        with mock.patch(__name__ + ".CLIENT_WINDOW", (800, 600)):
            self.assertNotEqual(base, _client_cache_key(self.scene))

    def test_a_miss_runs_one_session_and_fills_the_cache(self) -> None:
        """The cold path: every variant missing costs exactly one session."""
        with mock.patch(__name__ + "._client_session", self._stub_session()):
            shots = _client_shots({"full": self.scene})
        self.assertEqual(len(self.sessions), 1)
        self.assertEqual(sorted(self.sessions[0]), ["full"])
        entry = Path(self.cache) / ("client-%s.png"
                                    % _client_cache_key(self.scene))
        self.assertTrue(entry.exists())
        self.assertEqual(shots["full"], entry.read_bytes())

    def test_a_cached_variant_costs_no_session(self) -> None:
        """The whole point: warm, this tier starts no server and no browser.

        The saving being counted is a SESSION, not a render — a scratch
        project, a server, a chromium and a teardown, ~6s of it — which
        is why this is worth a test of its own rather than being taken on
        faith from tier 2's equivalent.
        """
        with mock.patch(__name__ + "._client_session", self._stub_session()):
            _client_shots({"full": self.scene})
            self.assertEqual(len(self.sessions), 1)
            _client_shots({"full": self.scene})
        self.assertEqual(len(self.sessions), 1,
                         "a fully cached call still ran a session")

    def test_a_partial_hit_renders_only_the_misses(self) -> None:
        """One new ablation must not re-render the whole run.

        The realistic warm case: a scene already measured, asked one more
        question. The session it runs must be handed the MISSING variant
        alone — a session that re-renders everything would pass the test
        above and still throw away most of the cache's value.
        """
        ablated = [dict(self.scene[0], x=17)]
        with mock.patch(__name__ + "._client_session", self._stub_session()):
            _client_shots({"full": self.scene})
            shots = _client_shots({"full": self.scene, "abl-n1": ablated})
        self.assertEqual(len(self.sessions), 2)
        self.assertEqual(sorted(self.sessions[1]), ["abl-n1"],
                         "the second call re-rendered variants it already had")
        self.assertEqual(sorted(shots), ["abl-n1", "full"])

    def test_the_session_starts_the_server_with_no_browser(self) -> None:
        """The theft guard, pinned where removing it costs a test.

        Mechanism 2 in the tier's section comment, and the one failure in
        this design that produces PLAUSIBLE-LOOKING garbage rather than an
        error: a connected tab answers the harness's screenshot requests
        with corrupt canvas readback at 0.033 bytes/px. Drop
        `--no-browser` and every ablation on a machine with a tab open
        starts measuring stripe noise.

        The stub refuses to start, so the session aborts at the CLI call
        and nothing else has to be faked — no server, no browser, no
        shots. What is left to assert is the argv, which is the thing an
        edit here would change.
        """
        calls: list[tuple[str, ...]] = []

        def refuse(root: Path, *args: str) -> int:
            calls.append(args)
            return 3

        with mock.patch(__name__ + "._canvas_cli", refuse), \
                self.assertRaises(RuntimeError) as caught:
            _client_session({"full": self.scene})
        self.assertEqual(calls, [("start", "--no-browser")],
                         "the client session no longer starts its server "
                         "with --no-browser: a connected tab will answer "
                         "its screenshot requests, and answer them wrongly")
        self.assertIn("would not start", str(caught.exception))


class TestClientSessionLifecycle(unittest.TestCase):
    """What the session does when something outside it goes wrong.

    Both tests drive a REAL `canvas.py` server — the session has to reach
    `read_state` and post screenshot requests before either failure is even
    reachable — but neither needs a real browser, so neither is gated.
    `true` stands in: it is a genuine executable that exits immediately,
    which is exactly the "browser that renders nothing" both cases are
    about, and it makes these machine-independent besides.

    Failure paths, not happy paths. The tier's own correctness is measured
    in `TestClientTierMechanisms`; what these pin is that a session which
    goes wrong takes nothing with it and says why quickly. Both were found
    by review rather than by a failing test, which is the honest note to
    leave: a `finally` that leaks and a poll loop that waits are invisible
    while everything works.
    """

    def setUp(self) -> None:
        """Stand `true` in for the browser and hold one trivial scene."""
        self.browser = shutil.which("true")
        if not self.browser:
            self.skipTest("no `true` binary to stand in for a browser")
        env = mock.patch.dict(os.environ, {})
        env.start()
        self.addCleanup(env.stop)
        # an ambient opt-in naming a real chromium would make `_browser`
        # reject the stand-in; patch.dict restores this on stop
        os.environ.pop("MUTANTS_RENDER_BROWSER", None)
        found = mock.patch.object(canvas, "find_browsers",
                                  lambda: [self.browser])
        found.start()
        self.addCleanup(found.stop)
        self.scene = [el(id="n1", type="rectangle", x=0, y=0, width=40,
                         height=20, customData={"role": "node"})]

    def test_a_hanging_stop_neither_masks_nor_skips_the_cleanup(self) -> None:
        """A `stop` that times out must not eat the real exception.

        `_canvas_cli` runs with a timeout, so `stop` CAN raise, and it is
        called from the `finally` ahead of three cleanup lines. Unhandled,
        one wedged daemon costs two things at once: the useful in-flight
        exception is replaced by a `TimeoutExpired` about the teardown, and
        the scratch project plus the runtime files it keeps OUTSIDE that
        project are both left on disk — the lifecycle leak the design of
        this fixture exists to prevent.

        The stub runs the REAL stop first and only then raises, so the
        server this test starts is genuinely stopped and the test leaks no
        daemon of its own while simulating one.

        A regression does not merely fail this test, it ERRORS it:
        `TimeoutExpired` is a `SubprocessError`, not a `RuntimeError`, so
        it escapes `assertRaises` entirely rather than being mistaken for
        the exception that should have arrived.
        """
        real_cli, stops = _canvas_cli, []

        def stop_that_hangs(root: Path, *args: str) -> int:
            rc = real_cli(root, *args)
            if args and args[0] == "stop":
                stops.append(root)
                raise subprocess.TimeoutExpired("canvas.py stop", 90)
            return rc

        def in_flight_failure(*_a: Any, **_kw: Any) -> dict[str, bytes]:
            raise RuntimeError("the in-flight failure worth keeping")

        with mock.patch(__name__ + "._canvas_cli", stop_that_hangs), \
                mock.patch(__name__ + "._await_shots", in_flight_failure), \
                self.assertRaises(RuntimeError) as caught:
            _client_session({"full": self.scene})
        self.assertIn("worth keeping", str(caught.exception),
                      "the teardown's own exception replaced the one that "
                      "explains why the session failed")
        self.assertEqual(len(stops), 1, "the session did not try to stop "
                                        "the server it started")
        root = stops[0]
        self.assertFalse(root.exists(),
                         "a hanging stop left the scratch project behind")
        project = canvas.Project(root)
        for path in (project.state_path, project.events_path,
                     project.log_path):
            self.assertFalse(path.exists(),
                             "a hanging stop left %s behind — these live "
                             "outside the project tree, so nothing else "
                             "will ever collect them" % path.name)

    def test_a_dead_browser_fails_at_once_and_names_itself(self) -> None:
        """An exited browser must not cost the whole deadline.

        Reachable in ORDINARY operation rather than only on a crash:
        `CLIENT_FLAGS` caps virtual time at 60s while `CLIENT_DEADLINE` is
        120s, so a slow session can outlive its own browser and then spend
        a further minute proving what `proc.poll()` already knew.

        The deadline is patched down to 20s so that a regression is slow
        rather than interminable, and the elapsed bound is set well below
        it: a session paced by the DEADLINE cannot come in under 10s, and
        one paced by the browser's exit takes about a second plus the
        server start. The message assertion is the primary claim though —
        `rc=0` can only come from reading the exited browser's real return
        code, so it cannot be satisfied by a timeout that happened to be
        fast.
        """
        started = time.time()
        with mock.patch(__name__ + ".CLIENT_DEADLINE", 20.0), \
                self.assertRaises(RuntimeError) as caught:
            _client_session({"full": self.scene})
        elapsed = time.time() - started
        self.assertIn("the browser exited (rc=0)", str(caught.exception),
                      "the failure does not name the dead browser, so it "
                      "was paced by the clock rather than by the culprit")
        self.assertLess(elapsed, 10.0,
                        "a dead browser took %.1fs to report against a 20s "
                        "deadline — the poll loop is waiting the deadline "
                        "out again" % elapsed)


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
