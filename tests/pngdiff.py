"""Stdlib PNG codec plus the one-pixel-tolerant blob comparator.

Why a tolerant XOR and not a perceptual metric — the render tier's design
premise, from docs/superpowers/specs (V0.9-PLAN.md § the comparator):

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
    pos = 0
    for y in range(height):
        ftype = raw[pos]
        line = bytearray(raw[pos + 1:pos + 1 + stride])
        pos += 1 + stride
        _unfilter_row(ftype, line, prev, channels)
        prev = line
        row = y * width
        if channels == 1:
            pix[row:row + width] = line
            continue
        for x in range(width):
            i = x * channels
            lum = (line[i] * 299 + line[i + 1] * 587 + line[i + 2] * 114)
            lum //= 1000
            if channels == 4:
                alpha = line[i + 3]
                lum = (lum * alpha + 255 * (255 - alpha)) // 255
            pix[row + x] = lum
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


def components(w: int, h: int, mask: bytearray) -> list[dict[str, Any]]:
    """Find 8-connected components of set pixels in a flat 0/1 mask.

    Flood fill is iterative (a `deque`, not recursion): a full-page mask is
    hundreds of thousands of pixels and would blow the stack. Neighbour
    offsets are computed from (x, y), never from the flat index, so a pixel
    at x=0 cannot wrap around to x=w-1 of the row above.

    Args:
        w: Mask width in pixels.
        h: Mask height in pixels.
        mask: `w * h` bytes; non-zero means set.

    Returns:
        One dict per component, in raster order of its first pixel, each
        `{"area": int, "bbox": (x0, y0, x1, y1)}` with the bounding box
        **inclusive** on all four sides — a single pixel at (5, 5) has
        bbox (5, 5, 5, 5).
    """
    seen = bytearray(w * h)
    out: list[dict[str, Any]] = []
    for start in range(w * h):
        if not mask[start] or seen[start]:
            continue
        seen[start] = 1
        queue = deque([start])
        area = 0
        x0 = x1 = start % w
        y0 = y1 = start // w
        while queue:
            i = queue.popleft()
            area += 1
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
        out.append({"area": area, "bbox": (x0, y0, x1, y1)})
    return out


def _dilate(w: int, h: int, mask: bytearray) -> bytearray:
    """Grow every set pixel into its 8-connected neighbourhood.

    This one pixel of slack is the whole tolerance budget: it is exactly
    the reach of a sub-pixel edge displacement, so an anti-aliased stroke
    lands inside the dilation of its counterpart and vanishes from the XOR.
    Bounds are clipped per row, so growth never wraps across image edges.

    Args:
        w: Mask width in pixels.
        h: Mask height in pixels.
        mask: `w * h` bytes; non-zero means set.

    Returns:
        A new mask of the same size, 1 where dilated.
    """
    out = bytearray(w * h)
    for y in range(h):
        row = y * w
        for x in range(w):
            if not mask[row + x]:
                continue
            for ny in range(max(0, y - 1), min(h, y + 2)):
                base = ny * w
                for nx in range(max(0, x - 1), min(w, x + 2)):
                    out[base + nx] = 1
    return out


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

    Raises:
        ValueError: If the two images differ in size — a size change is a
            real difference the caller must handle, not a diff result.
    """
    aw, ah, apix = read_png_gray(a)
    bw, bh, bpix = read_png_gray(b)
    if (aw, ah) != (bw, bh):
        raise ValueError("image sizes differ: %dx%d vs %dx%d"
                         % (aw, ah, bw, bh))
    ink_a = bytearray(1 if p < ink_threshold else 0 for p in apix)
    ink_b = bytearray(1 if p < ink_threshold else 0 for p in bpix)
    dil_a = _dilate(aw, ah, ink_a)
    dil_b = _dilate(aw, ah, ink_b)
    residual = bytearray(
        1 if (ink_a[i] and not dil_b[i]) or (ink_b[i] and not dil_a[i]) else 0
        for i in range(aw * ah))
    return [c for c in components(aw, ah, residual) if c["area"] >= min_blob]
