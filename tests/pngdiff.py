"""Stdlib PNG codec plus the one-pixel-tolerant blob comparator.

Why a tolerant XOR and not a perceptual metric — the render tier's design
premise, from V0.9-PLAN.md at the repo root (§ the comparator):

    Anti-aliasing, hinting and sub-pixel positioning artefacts are all
    *sub-pixel edge displacements*: at most a one-pixel band hugging a
    stroke. A real defect — a missing arrowhead, a connector erased by a
    label backdrop — is a free-standing blob. The two differ in
    **topology, not magnitude**.

So: binarise to ink/paper, dilate each mask by one pixel, XOR, and run
connected components. The dilation absorbs the entire anti-aliasing class
*by construction*, while a real defect survives with a bounding box — and a
bounding box is what makes a failing visual test fixable. A percentage
threshold cannot exploit that separation; SSIM cannot either (a diagram is
~95% flat paper, so a fully erased connector still scores ~0.997).

Never raise `min_blob` to silence a flake. A flake after the determinism
flags are set means the diagram content is non-deterministic, and that is
the bug.

Scope: 8-bit non-interlaced PNG, color type 0 (gray), 2 (RGB) or 6 (RGBA).
Anything else raises `ValueError` rather than guessing.
"""
from __future__ import annotations

import struct
import zlib
from collections import deque
from collections.abc import Iterator
from typing import Any

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# Bytes per pixel by PNG color type, for the 8-bit depths we accept.
_CHANNELS = {0: 1, 2: 3, 6: 4}

# Maps the ASCII bit string `format(bits, "b")` produces back to 0/1 bytes.
_BIT_TO_BYTE = bytes((1 if i == 0x31 else 0) for i in range(256))


def _chunk(ctype: bytes, body: bytes) -> bytes:
    """Serialise one PNG chunk: length, type, body, CRC.

    Args:
        ctype: The four-byte chunk type, e.g. `b"IHDR"`.
        body: The chunk payload.

    Returns:
        The chunk's byte stream, CRC included.
    """
    return (struct.pack(">I", len(body)) + ctype + body
            + struct.pack(">I", zlib.crc32(ctype + body) & 0xFFFFFFFF))


def _walk_chunks(data: bytes) -> Iterator[tuple[bytes, bytes]]:
    """Yield (type, body) for each chunk after the signature.

    CRCs are skipped, not verified: the decoder's inputs are files this
    process just rendered or wrote, so a CRC mismatch would mean a bug in
    the same run, and the unfilter step would fail on it anyway.

    Args:
        data: A complete PNG byte stream.

    Yields:
        The chunk type and its body, in file order.

    Raises:
        ValueError: If the 8-byte PNG signature is missing.
    """
    if data[:8] != PNG_SIGNATURE:
        raise ValueError("not a PNG: bad signature %r" % (data[:8],))
    pos = 8
    while pos + 8 <= len(data):
        length, ctype = struct.unpack(">I4s", data[pos:pos + 8])
        pos += 8
        yield ctype, data[pos:pos + length]
        pos += length + 4                      # + 4 for the CRC


def _paeth(a: int, b: int, c: int) -> int:
    """The PNG Paeth predictor: whichever neighbour p is nearest.

    Ties break a-then-b-then-c, exactly as the spec's reference code does.
    Getting that order wrong corrupts every Paeth-filtered row silently,
    which is why `test_paeth_tie_breaks_a_then_b_then_c` exists.

    Args:
        a: The byte to the left.
        b: The byte above.
        c: The byte above-left.

    Returns:
        The predicted byte value.
    """
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _unfilter_row(ftype: int, line: bytearray, prev: bytearray,
                  bpp: int) -> None:
    """Reverse one scanline's filter in place.

    `read_png_gray` reconstructs filter 2 (Up) by a faster route and only
    calls this for the other four, but the Up branch here is not dead: it
    is the reference the fast path is tested against, and the readable
    statement of what that path must compute.

    Args:
        ftype: The PNG filter type byte (0-4).
        line: The filtered scanline, mutated into the reconstructed one.
        prev: The already-reconstructed scanline above (zeros for row 0).
        bpp: Bytes per pixel, i.e. the filter's left-neighbour offset.

    Raises:
        ValueError: If `ftype` is not one of the spec's five filters.
    """
    n = len(line)
    if ftype == 0:                                            # None
        return
    if ftype == 1:                                            # Sub
        for i in range(bpp, n):
            line[i] = (line[i] + line[i - bpp]) & 0xFF
    elif ftype == 2:                                          # Up
        for i in range(n):
            line[i] = (line[i] + prev[i]) & 0xFF
    elif ftype == 3:                                          # Average
        for i in range(n):
            a = line[i - bpp] if i >= bpp else 0
            line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
    elif ftype == 4:                                          # Paeth
        for i in range(n):
            a = line[i - bpp] if i >= bpp else 0
            c = prev[i - bpp] if i >= bpp else 0
            line[i] = (line[i] + _paeth(a, prev[i], c)) & 0xFF
    else:
        raise ValueError("unknown PNG filter type %d (need 0-4)" % ftype)


def read_png_gray(data: bytes) -> tuple[int, int, bytearray]:
    """Decode a PNG to one luminance byte per pixel.

    Supports bit depth 8, color type 0 (gray), 2 (RGB) or 6 (RGBA), non-
    interlaced. Luminance uses the integer Rec.601 weights,
    `(r*299 + g*587 + b*114) // 1000`.

    **Alpha composites over white**, `px = (px*a + 255*(255-a)) // 255`, so
    a transparent region reads as paper rather than as ink. That matters:
    Playwright screenshots of the canvas come back RGBA, and treating
    transparency as black would make every empty region a giant defect.

    Filter type 2 (Up) is reconstructed a whole row at a time instead of
    byte by byte, because chromium's output is overwhelmingly Up-filtered
    — 766 of 800 rows on a measured 1200x800 raster — and decoding was
    60% of a diff's cost. Reading the row as one integer, the reconstruction
    `(a + b) & 0xFF` per byte is the SWAR identity
    `((a & 0x7f..) + (b & 0x7f..)) ^ ((a ^ b) & 0x80..)`: the masked add
    cannot carry out of any byte's bit 6, so the high bits stay independent
    and the XOR restores each one. That is exact byte-wise addition mod 256,
    which is what the Up filter is defined as — not an approximation with a
    tolerance. `_unfilter_row` keeps the readable per-byte version and
    remains the oracle the SWAR path is pinned against
    (`test_the_swar_up_filter_matches_the_reference_byte_loop`).

    Args:
        data: A complete PNG byte stream.

    Returns:
        (width, height, pixels) with `len(pixels) == width * height`, row
        major, top-left origin.

    Raises:
        ValueError: If the signature, IHDR, bit depth, color type,
            interlace method or scanline count is unsupported or corrupt.
    """
    width = height = channels = -1
    idat = bytearray()
    for ctype, body in _walk_chunks(data):
        if ctype == b"IHDR":
            width, height, depth, color, _, _, interlace = struct.unpack(
                ">IIBBBBB", body)
            if depth != 8:
                raise ValueError("unsupported PNG bit depth %d (need 8)"
                                 % depth)
            if color not in _CHANNELS:
                raise ValueError(
                    "unsupported PNG color type %d (need 0, 2 or 6)" % color)
            if interlace != 0:
                raise ValueError("interlaced PNG unsupported "
                                 "(interlace method %d)" % interlace)
            channels = _CHANNELS[color]
        elif ctype == b"IDAT":
            idat += body                       # all IDATs are one zlib stream
        elif ctype == b"IEND":
            break
    if channels < 0:
        raise ValueError("PNG has no IHDR chunk")

    raw = zlib.decompress(bytes(idat))
    stride = width * channels
    if len(raw) != (stride + 1) * height:
        raise ValueError("PNG scanline data is %d bytes, expected %d"
                         % (len(raw), (stride + 1) * height))

    pix = bytearray(width * height)
    prev = bytearray(stride)
    # `prev` as one big integer, or None when the last row was not
    # reconstructed through the SWAR path and the cache is stale.
    prev_bits: int | None = 0
    low = int.from_bytes(b"\x7f" * stride, "big")
    high = int.from_bytes(b"\x80" * stride, "big")
    pos = 0
    for y in range(height):
        ftype = raw[pos]
        seg = raw[pos + 1:pos + 1 + stride]
        pos += 1 + stride
        if ftype == 2:                          # Up, a whole row at a time
            if prev_bits is None:
                prev_bits = int.from_bytes(prev, "big")
            cur = int.from_bytes(seg, "big")
            prev_bits = (((cur & low) + (prev_bits & low))
                         ^ ((cur ^ prev_bits) & high))
            line = bytearray(prev_bits.to_bytes(stride, "big"))
        else:
            line = bytearray(seg)
            _unfilter_row(ftype, line, prev, channels)
            prev_bits = None
        prev = line
        row = y * width
        if channels == 1:
            pix[row:row + width] = line
            continue
        red, green = line[0::channels], line[1::channels]
        blue = line[2::channels]
        if channels == 4:
            alpha = line[3::channels]
            for x in range(width):
                lum = (red[x] * 299 + green[x] * 587 + blue[x] * 114) // 1000
                opacity = alpha[x]
                pix[row + x] = ((lum * opacity + 255 * (255 - opacity))
                                // 255)
        else:
            for x in range(width):
                pix[row + x] = (red[x] * 299 + green[x] * 587
                                + blue[x] * 114) // 1000
    return width, height, pix


def write_png_gray(w: int, h: int, pix: bytes) -> bytes:
    """Encode one luminance byte per pixel as an 8-bit grayscale PNG.

    Rows are written with filter type 0 (None): the images this produces
    are test fixtures and human-readable diff dumps, so decode simplicity
    beats a few bytes of compression.

    Args:
        w: Image width in pixels.
        h: Image height in pixels.
        pix: Exactly `w * h` luminance bytes, row major.

    Returns:
        A complete PNG byte stream.

    Raises:
        ValueError: If `pix` is not `w * h` bytes long.
    """
    if len(pix) != w * h:
        raise ValueError("pixel buffer is %d bytes, expected %d for %dx%d"
                         % (len(pix), w * h, w, h))
    rows = b"".join(b"\x00" + pix[y * w:(y + 1) * w] for y in range(h))
    return (PNG_SIGNATURE
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(rows, 9))
            + _chunk(b"IEND", b""))


def components(w: int, h: int, mask: bytearray,
               pixels: bool = False) -> list[dict[str, Any]]:
    """Find 8-connected components of set pixels in a flat 0/1 mask.

    Flood fill is iterative (a `deque`, not recursion): a full-page mask is
    hundreds of thousands of pixels and would blow the stack. Neighbour
    offsets are computed from (x, y), never from the flat index, so a pixel
    at x=0 cannot wrap around to x=w-1 of the row above.

    Args:
        w: Mask width in pixels.
        h: Mask height in pixels.
        mask: `w * h` bytes; non-zero means set.
        pixels: Also report each component's member indices. OFF by
            default, and the default is the load-bearing half: a member
            list is O(area) boxed ints, ~31 MB for one merged component
            on a corpus-scale raster, and the only caller that wants it
            (`test_mutants_render._delta_components`) reduces it to four
            numbers per side and drops it inside one function.

    Returns:
        One dict per component, in raster order of its first pixel, each
        `{"area": int, "bbox": (x0, y0, x1, y1)}` with the bounding box
        **inclusive** on all four sides — a single pixel at (5, 5) has
        bbox (5, 5, 5, 5). With `pixels`, each dict also carries
        `"pixels"`: that component's flat indices, `area` of them.
    """
    seen = bytearray(w * h)
    out: list[dict[str, Any]] = []
    for start in range(w * h):
        if not mask[start] or seen[start]:
            continue
        seen[start] = 1
        queue = deque([start])
        member: list[int] = []
        area = 0
        x0 = x1 = start % w
        y0 = y1 = start // w
        while queue:
            i = queue.popleft()
            area += 1
            if pixels:
                member.append(i)
            x, y = i % w, i // w
            if x < x0:
                x0 = x
            if x > x1:
                x1 = x
            if y > y1:
                y1 = y
            for ny in range(max(0, y - 1), min(h, y + 2)):
                base = ny * w
                for nx in range(max(0, x - 1), min(w, x + 2)):
                    j = base + nx
                    if mask[j] and not seen[j]:
                        seen[j] = 1
                        queue.append(j)
        comp: dict[str, Any] = {"area": area, "bbox": (x0, y0, x1, y1)}
        if pixels:
            comp["pixels"] = member
        out.append(comp)
    return out


def _ink_bits(pix: bytearray, ink_threshold: int) -> int:
    """Binarise a luminance buffer to ink, packed as one integer.

    `bytes.translate` does the per-pixel comparison in C by turning each
    luminance byte straight into the ASCII `'0'` or `'1'` that `int(s, 2)`
    then reads — one pass, no Python-level loop over pixels.

    Args:
        pix: Luminance bytes, as `read_png_gray` returns.
        ink_threshold: Luminance below which a pixel counts as ink.

    Returns:
        The ink mask, pixel 0 the most significant bit; 0 for empty input.
    """
    if not pix:
        return 0
    table = bytes((0x31 if i < ink_threshold else 0x30) for i in range(256))
    return int(bytes(pix).translate(table), 2)


def _mask_bytes(bits: int, n: int) -> bytearray:
    """Unpack an `n`-pixel bitset back into one 0/1 byte per pixel.

    Args:
        bits: The mask as an integer, pixel 0 the most significant bit.
        n: Pixel count, i.e. the width to zero-pad the bit string to.

    Returns:
        `n` bytes, 1 where set. Empty for `n == 0` — `format` would
        render a bare `"0"` there and invent a pixel.
    """
    if n == 0:
        return bytearray()
    return bytearray(format(bits, "0%db" % n).encode("ascii")
                     .translate(_BIT_TO_BYTE))


def _edge_masks(w: int, h: int) -> tuple[int, int, int]:
    """The three constants `_dilate` needs for a `w` x `h` bitset.

    Built once per diff and shared by both dilations, because each is a
    `w * h`-bit integer and materialising them is not free.

    Args:
        w: Mask width in pixels.
        h: Mask height in pixels.

    Returns:
        `(notleft, notright, full)`. `notleft` is set everywhere except
        column 0, `notright` everywhere except column `w - 1`, and `full`
        is all `w * h` bits — the clip that stops a vertical shift from
        running off the top of the image. All three are 0 for an empty
        image, which is the only size where the bitset has no columns to
        guard.
    """
    n = w * h
    if n == 0:
        return 0, 0, 0
    return (int(("0" + "1" * (w - 1)) * h, 2),
            int(("1" * (w - 1) + "0") * h, 2),
            (1 << n) - 1)


def _dilate(w: int, bits: int, edges: tuple[int, int, int]) -> int:
    """Grow every set pixel into its 8-connected neighbourhood.

    This one pixel of slack is the whole tolerance budget: it is exactly
    the reach of a sub-pixel edge displacement, so an anti-aliased stroke
    lands inside the dilation of its counterpart and vanishes from the XOR.

    The mask is one integer with pixel 0 as the MOST significant bit, so
    `<< 1` moves ink to x-1 and `>> 1` moves it to x+1. Growth must not
    wrap across image edges, and in this representation a row's edges are
    not a boundary at all — the rows are one continuous bit string, so
    ink at x=0 shifted left would land at x=w-1 of the row ABOVE and
    silently forgive a defect there. The guards are therefore applied to
    the SOURCE column that would wrap: mask off column 0 before shifting
    left, column w-1 before shifting right. Getting that pair the wrong
    way round still passes on any real raster — a diagram has a paper
    margin, so no ink ever sits in an edge column — which is why
    `test_dilation_does_not_wrap_across_the_right_edge` pins it on
    synthetic ink instead.

    The vertical shifts need no such guard: `<< w` runs off the top into
    bits above the image, which `full` clears, and `>> w` runs off the
    bottom into nothing.

    Args:
        w: Mask width in pixels.
        bits: The mask as an integer, pixel 0 the most significant bit.
        edges: `_edge_masks(w, h)` for this mask's dimensions.

    Returns:
        The dilated mask, same representation.
    """
    notleft, notright, full = edges
    hz = bits | ((bits & notleft) << 1) | ((bits & notright) >> 1)
    return (hz | (hz << w) | (hz >> w)) & full


def tolerant_diff(a: bytes, b: bytes, ink_threshold: int = 192,
                  min_blob: int = 12) -> list[dict[str, Any]]:
    """Compare two PNGs, tolerating one pixel of edge displacement.

    Binarise both to ink (`pix < ink_threshold`), dilate each mask by one
    pixel, and keep `(A & ~dilate(B)) | (B & ~dilate(A))` — ink that has no
    counterpart even after slack. Surviving connected components smaller
    than `min_blob` are dropped as residual speckle. What is left is,
    by the topology argument in this module's docstring, real.

    Args:
        a: The first PNG's bytes.
        b: The second PNG's bytes.
        ink_threshold: Luminance below which a pixel counts as ink.
        min_blob: Smallest surviving component worth reporting, in pixels.

    Returns:
        One dict per surviving blob, `{"area": int, "bbox": (x0, y0, x1,
        y1)}` with an inclusive bbox. An empty list means the images agree.
        A caller that needs the ink's SHAPE rather than its bounding boxes
        wants `tolerant_diff_mask` instead — same computation, same blobs.

    Raises:
        ValueError: If the two images differ in size — a size change is a
            real difference the caller must handle, not a diff result.
    """  # noqa: DOC502 — raised by the delegate, and still this contract
    return tolerant_diff_mask(a, b, ink_threshold, min_blob)[0]


def tolerant_diff_mask(a: bytes, b: bytes, ink_threshold: int = 192,
                       min_blob: int = 12
                       ) -> tuple[list[dict[str, Any]], int, int, bytearray]:
    """`tolerant_diff`, handing back the residual mask it computed anyway.

    A bounding box is what makes a failing visual test fixable, which is
    why `tolerant_diff` reports boxes — but a box cannot say which way the
    ink inside it POINTS, and one caller needs exactly that (the render
    tier's `ablation_continuity`, deciding whether two pieces of a severed
    connector read as one stroke with a gap). The mask is not a second
    computation for them: it is the intermediate this function already
    builds on its last-but-one line and used to drop on the `return`.

    So this holds the body and `tolerant_diff` delegates, rather than the
    other way round. A sibling that re-derived the mask would pay a second
    `read_png_gray` and two more `_dilate` passes, against zero extra
    allocation for keeping a bytearray the peak already contained. Since
    the bitset rewrite the dilations are effectively free and the second
    DECODE is the whole of that cost: on a 0.56 MP raster whose complete
    diff takes 0.342s, re-deriving adds 0.157s — 46% more work to recover
    something this function already had in hand.

    Args:
        a: The first PNG's bytes.
        b: The second PNG's bytes.
        ink_threshold: Luminance below which a pixel counts as ink.
        min_blob: Smallest surviving component worth reporting, in pixels.

    Returns:
        `(blobs, width, height, residual)`. `blobs` is exactly what
        `tolerant_diff` returns for the same arguments. `residual` is the
        `width * height` mask those blobs were componented from, so it
        also retains the sub-`min_blob` speckle the blob list dropped —
        it is the residual, not the reported ink.

    Raises:
        ValueError: If the two images differ in size, as `tolerant_diff`.
    """
    aw, ah, apix = read_png_gray(a)
    bw, bh, bpix = read_png_gray(b)
    if (aw, ah) != (bw, bh):
        raise ValueError("image sizes differ: %dx%d vs %dx%d"
                         % (aw, ah, bw, bh))
    ink_a = _ink_bits(apix, ink_threshold)
    ink_b = _ink_bits(bpix, ink_threshold)
    edges = _edge_masks(aw, ah)
    residual = _mask_bytes(
        ((ink_a & ~_dilate(aw, ink_b, edges))
         | (ink_b & ~_dilate(aw, ink_a, edges))) & edges[2], aw * ah)
    return ([c for c in components(aw, ah, residual) if c["area"] >= min_blob],
            aw, ah, residual)
