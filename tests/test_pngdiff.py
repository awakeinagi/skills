"""Tests for the tolerant PNG comparator.

The contract under test is the render tier's load-bearing premise: anti-
aliasing noise and real defects differ in TOPOLOGY, not magnitude. A one-
pixel dilation absorbs a boundary-hugging band entirely; a free-standing
blob survives it. Everything here runs on tiny synthetic images built by
this module's own encoder, so the tests are hermetic — no browser, no font,
no fixtures.
"""
from __future__ import annotations

import struct
import unittest
import zlib

from pngdiff import (
    PNG_SIGNATURE,
    _chunk,
    components,
    read_png_gray,
    tolerant_diff,
    tolerant_diff_mask,
    write_png_gray,
)


def _png(ihdr: bytes, idat: bytes) -> bytes:
    """Assemble a PNG from a raw IHDR body and raw (uncompressed) pixel data.

    Args:
        ihdr: The 13-byte IHDR chunk body.
        idat: Uncompressed filtered scanlines, compressed here.

    Returns:
        The complete PNG byte stream.
    """
    return (PNG_SIGNATURE + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(idat)) + _chunk(b"IEND", b""))


def _ihdr(w: int, h: int, depth: int, ctype: int, interlace: int = 0) -> bytes:
    """Build an IHDR chunk body.

    Args:
        w: Image width in pixels.
        h: Image height in pixels.
        depth: Bit depth.
        ctype: PNG color type.
        interlace: Interlace method (0 = none, 1 = Adam7).

    Returns:
        The 13-byte IHDR body.
    """
    return struct.pack(">IIBBBBB", w, h, depth, ctype, 0, 0, interlace)


class TestTolerantDiff(unittest.TestCase):
    """The comparator's contract: AA-band immune, blob-sensitive.

    `tolerant_diff_mask` is pinned here rather than in a class of its own
    because it is the same comparator — it holds the body, and the plain
    entry point is one line of it — so the two belong to one family and
    share the `_img` fixture.
    """

    def _img(self, w: int, h: int, strokes: list[tuple[int, int]]) -> bytes:
        """Gray image, white ground, black pixels at the given coords.

        Args:
            w: Image width in pixels.
            h: Image height in pixels.
            strokes: (x, y) coordinates to ink black.

        Returns:
            The encoded PNG bytes.
        """
        pix = bytearray([255] * (w * h))
        for x, y in strokes:
            pix[y * w + x] = 0
        return write_png_gray(w, h, bytes(pix))

    def test_identical_images_diff_empty(self) -> None:
        """An image compared with itself yields no surviving blobs."""
        a = self._img(32, 32, [(x, 10) for x in range(4, 28)])
        self.assertEqual(tolerant_diff(a, a), [])

    def test_one_pixel_shift_is_absorbed(self) -> None:
        """A whole stroke displaced one row is the anti-aliasing class."""
        a = self._img(32, 32, [(x, 10) for x in range(4, 28)])
        b = self._img(32, 32, [(x, 11) for x in range(4, 28)])
        self.assertEqual(tolerant_diff(a, b), [])   # AA/hinting class

    def test_missing_blob_survives(self) -> None:
        """A 6x6 mark present in one image and absent in the other survives."""
        a = self._img(64, 64, [(x, y) for x in range(20, 26)
                               for y in range(20, 26)])   # 36px blob
        b = self._img(64, 64, [])
        blobs = tolerant_diff(a, b)
        self.assertEqual(len(blobs), 1)
        self.assertGreaterEqual(blobs[0]["area"], 16)
        x0, y0, x1, y1 = blobs[0]["bbox"]
        self.assertLessEqual((x0, y0), (20, 20))
        self.assertGreaterEqual((x1, y1), (25, 25))

    def test_missing_blob_bbox_is_inclusive(self) -> None:
        """The reported bbox brackets the mark exactly, corners included."""
        a = self._img(64, 64, [(x, y) for x in range(20, 26)
                               for y in range(20, 26)])
        blobs = tolerant_diff(a, self._img(64, 64, []))
        self.assertEqual(blobs[0]["bbox"], (20, 20, 25, 25))
        self.assertEqual(blobs[0]["area"], 36)

    def test_subthreshold_speck_is_ignored(self) -> None:
        """A 2px speck is below min_blob and never reported."""
        a = self._img(32, 32, [(5, 5), (6, 5)])     # 2px speck
        b = self._img(32, 32, [])
        self.assertEqual(tolerant_diff(a, b), [])

    def test_speck_is_reported_when_min_blob_lowered(self) -> None:
        """The speck really is there — only min_blob was hiding it."""
        a = self._img(32, 32, [(5, 5), (6, 5)])
        b = self._img(32, 32, [])
        self.assertEqual(len(tolerant_diff(a, b, min_blob=1)), 1)

    def test_two_defects_report_separately(self) -> None:
        """Well-separated marks come back as two blobs, not one union."""
        a = self._img(64, 64, [(x, y) for x in range(4, 10)
                               for y in range(4, 10)]
                      + [(x, y) for x in range(50, 56)
                         for y in range(50, 56)])
        blobs = tolerant_diff(a, self._img(64, 64, []))
        self.assertEqual(len(blobs), 2)
        self.assertEqual([b["bbox"] for b in blobs],
                         [(4, 4, 9, 9), (50, 50, 55, 55)])

    def test_defect_at_image_border_is_found(self) -> None:
        """Marks touching x=0 and the last column stay separate components.

        Guards the flat-index trap: a pixel at x=0 must not neighbour
        x=w-1 of the previous row through `i-1` arithmetic.
        """
        left = [(x, y) for x in range(0, 4) for y in range(20, 28)]
        right = [(x, y) for x in range(60, 64) for y in range(20, 28)]
        blobs = tolerant_diff(self._img(64, 64, left + right),
                              self._img(64, 64, []))
        self.assertEqual(len(blobs), 2)
        self.assertEqual([b["bbox"] for b in blobs],
                         [(0, 20, 3, 27), (60, 20, 63, 27)])

    def test_dilation_does_not_wrap_across_the_right_edge(self) -> None:
        """Ink on the right edge must not excuse ink on the left edge.

        The dilation is the tolerance budget, so a wrapping one is the
        dangerous direction: it would silently forgive a real defect at
        x=0 because of unrelated ink at x=w-1 one row up.
        """
        a = self._img(64, 64, [(x, y) for x in (0, 1) for y in range(20, 30)])
        b = self._img(64, 64, [(63, y) for y in range(19, 31)])
        blobs = tolerant_diff(a, b)
        self.assertEqual([bl["bbox"] for bl in blobs],   # raster order
                         [(63, 19, 63, 30), (0, 20, 1, 29)])

    def test_ink_threshold_governs_what_counts_as_ink(self) -> None:
        """A mid-gray mark is ink at the default threshold, paper below it."""
        pix = bytearray([255] * (32 * 32))
        for x in range(10, 20):
            for y in range(10, 14):
                pix[y * 32 + x] = 200
        a = write_png_gray(32, 32, bytes(pix))
        b = self._img(32, 32, [])
        self.assertEqual(tolerant_diff(a, b), [])            # 200 >= 192
        self.assertEqual(len(tolerant_diff(a, b, ink_threshold=210)), 1)

    def test_mismatched_sizes_raise(self) -> None:
        """A size change is the caller's problem, not a diff result."""
        with self.assertRaises(ValueError) as ctx:
            tolerant_diff(self._img(32, 32, []), self._img(16, 16, []))
        self.assertIn("32x32", str(ctx.exception))
        self.assertIn("16x16", str(ctx.exception))

    def test_the_masked_entry_point_reports_the_same_blobs(self) -> None:
        """`tolerant_diff_mask` is `tolerant_diff` plus the mask, exactly.

        The whole reason it exists is that one caller needs the ink's
        shape and every other caller must be able to ignore it, so a
        difference in the blobs — an off-by-one in the `min_blob` filter,
        a different ordering — would make the two entry points two
        comparators. `w` and `h` come back too: they are the mask's
        stride, and unflattening it against the wrong one is silent.
        """
        a = self._img(64, 48, [(x, y) for x in range(20, 26)
                               for y in range(20, 26)]
                      + [(x, y) for x in range(40, 46) for y in range(8, 14)])
        b = self._img(64, 48, [])
        blobs, w, h, _mask = tolerant_diff_mask(a, b)
        self.assertEqual((w, h), (64, 48))
        self.assertEqual(blobs, tolerant_diff(a, b))

    def test_the_mask_is_what_the_blobs_were_componented_from(self) -> None:
        """The mask returned is the residual, not a fresh empty buffer.

        The pole that makes the mask worth returning: re-componenting it
        reproduces the reported blobs. A sibling that handed back a
        correctly sized bytearray of zeros would satisfy every other
        assertion here — the caller only indexes it — and would silently
        report every component as having no ink at any of its ends.
        """
        a = self._img(64, 48, [(x, y) for x in range(20, 26)
                               for y in range(20, 26)]
                      + [(x, y) for x in range(40, 46) for y in range(8, 14)])
        blobs, w, h, mask = tolerant_diff_mask(a, self._img(64, 48, []))
        self.assertEqual([c for c in components(w, h, mask)
                          if c["area"] >= 12], blobs)

    def test_the_mask_keeps_the_speckle_the_blob_list_dropped(self) -> None:
        """It is the RESIDUAL, not the reported ink, and the difference bites.

        `min_blob` drops sub-threshold specks from the blob list; the
        mask keeps them, because it is the intermediate the filter ran
        on. A caller intersecting a component's members with this mask
        therefore recovers ink that was never reported as a blob — which
        is correct (it is real residual inside that component's own box)
        and is the kind of thing a reader of the render tier's end
        profiles has to know before trusting a span by one pixel.
        """
        blobs, w, _h, mask = tolerant_diff_mask(
            self._img(32, 32, [(5, 5), (6, 5)]), self._img(32, 32, []))
        self.assertEqual(blobs, [])
        self.assertEqual([i for i, v in enumerate(mask) if v],
                         [5 * w + 5, 5 * w + 6])

    def test_agreeing_images_return_an_empty_mask(self) -> None:
        """The other pole: no difference, no ink anywhere in the residual."""
        a = self._img(32, 32, [(x, 10) for x in range(4, 28)])
        blobs, w, h, mask = tolerant_diff_mask(a, a)
        self.assertEqual(blobs, [])
        self.assertEqual(len(mask), w * h)
        self.assertEqual(max(mask), 0)

    def test_the_masked_entry_point_rejects_mismatched_sizes(self) -> None:
        """The size guard lives in the body, so both entry points keep it."""
        with self.assertRaises(ValueError) as ctx:
            tolerant_diff_mask(self._img(32, 32, []), self._img(16, 16, []))
        self.assertIn("32x32", str(ctx.exception))
        self.assertIn("16x16", str(ctx.exception))


class TestComponents(unittest.TestCase):
    """8-connected flood fill over a flat 0/1 mask."""

    def test_components_split_count(self) -> None:
        """Two far corners are two components."""
        m = bytearray(64)                            # 8x8
        m[0] = m[63] = 1                             # two far corners
        comps = components(8, 8, m)
        self.assertEqual(len(comps), 2)

    def test_empty_mask_has_no_components(self) -> None:
        """An all-paper mask yields nothing."""
        self.assertEqual(components(8, 8, bytearray(64)), [])

    def test_diagonal_touch_is_one_component(self) -> None:
        """8-connectivity joins pixels that meet only at a corner."""
        m = bytearray(64)
        m[0] = 1            # (0, 0)
        m[9] = 1            # (1, 1)
        self.assertEqual(len(components(8, 8, m)), 1)
        self.assertEqual(components(8, 8, m)[0]["bbox"], (0, 0, 1, 1))

    def test_row_wrap_is_not_connectivity(self) -> None:
        """(7, 0) and (0, 1) are adjacent in the flat array, not on screen."""
        m = bytearray(64)
        m[7] = 1            # (7, 0)
        m[8] = 1            # (0, 1)
        self.assertEqual(len(components(8, 8, m)), 2)

    def test_area_counts_pixels_not_bbox(self) -> None:
        """A hollow ring reports its ink count, not its bounding area."""
        m = bytearray(64)
        for x in range(2, 6):
            m[2 * 8 + x] = m[5 * 8 + x] = 1
        for y in range(3, 5):
            m[y * 8 + 2] = m[y * 8 + 5] = 1
        comps = components(8, 8, m)
        self.assertEqual(len(comps), 1)
        self.assertEqual(comps[0]["area"], 12)
        self.assertEqual(comps[0]["bbox"], (2, 2, 5, 5))

    def test_pixels_opt_in_reports_each_components_own_members(self) -> None:
        """With `pixels`, a component carries its ink's indices, not its box.

        The same hollow ring as above, and the same distinction it was
        built for: the ring's bbox is 16 cells and its ink is 12, so a
        member list taken from the bounding box rather than from the
        flood fill would come back four indices too long. `area` and the
        list are pinned against each other as well — they are produced by
        the same loop and a caller sizing one from the other must not be
        able to drift.
        """
        m = bytearray(64)
        for x in range(2, 6):
            m[2 * 8 + x] = m[5 * 8 + x] = 1
        for y in range(3, 5):
            m[y * 8 + 2] = m[y * 8 + 5] = 1
        comps = components(8, 8, m, pixels=True)
        self.assertEqual(len(comps), 1)
        self.assertEqual(len(comps[0]["pixels"]), comps[0]["area"])
        self.assertEqual(sorted(comps[0]["pixels"]),
                         [i for i, v in enumerate(m) if v])

    def test_components_report_no_pixels_by_default(self) -> None:
        """The opt-in must not rot into always-on.

        A member list is O(area) boxed ints — ~31 MB for one merged
        component on a corpus-scale raster — and `tolerant_diff` runs
        this on every diff. So the default is load-bearing, and "the
        dict grew a key nobody asked for" is exactly the change that
        would go unnoticed: no assertion in this suite compares a whole
        component dict to a literal, so nothing else here would see it.
        """
        m = bytearray(64)
        m[0] = m[63] = 1
        self.assertEqual([sorted(c) for c in components(8, 8, m)],
                         [["area", "bbox"], ["area", "bbox"]])

    def test_large_component_does_not_recurse(self) -> None:
        """A full-frame mask floods iteratively — no recursion limit."""
        comps = components(200, 200, bytearray([1] * 40000))
        self.assertEqual(len(comps), 1)
        self.assertEqual(comps[0]["area"], 40000)


class TestCodec(unittest.TestCase):
    """The decoder Task 8 consumes: gray/RGB/RGBA, 8-bit, non-interlaced."""

    def test_round_trip_preserves_pixels(self) -> None:
        """write_png_gray → read_png_gray is the identity on pixels."""
        pix = bytes((x * 7 + y * 13) % 256 for y in range(19)
                    for x in range(23))
        w, h, out = read_png_gray(write_png_gray(23, 19, pix))
        self.assertEqual((w, h), (23, 19))
        self.assertEqual(bytes(out), pix)

    def test_round_trip_single_pixel(self) -> None:
        """The degenerate 1x1 image survives the round trip."""
        self.assertEqual(read_png_gray(write_png_gray(1, 1, b"\x7f")),
                         (1, 1, bytearray(b"\x7f")))

    def test_write_rejects_wrong_pixel_count(self) -> None:
        """A pixel buffer that is not w*h bytes is a programming error."""
        with self.assertRaises(ValueError):
            write_png_gray(4, 4, b"\x00" * 15)

    def test_multiple_idat_chunks_are_concatenated(self) -> None:
        """The zlib stream may be split across IDATs at any byte."""
        pix = bytes(range(16))
        whole = zlib.compress(b"".join(b"\x00" + pix[y * 4:y * 4 + 4]
                                       for y in range(4)))
        png = (PNG_SIGNATURE + _chunk(b"IHDR", _ihdr(4, 4, 8, 0))
               + _chunk(b"IDAT", whole[:3]) + _chunk(b"IDAT", whole[3:])
               + _chunk(b"IEND", b""))
        self.assertEqual(bytes(read_png_gray(png)[2]), pix)

    def test_rgb_decodes_to_luminance(self) -> None:
        """RGB uses the integer Rec.601 weights (299/587/114)."""
        rows = b"\x00" + bytes([255, 0, 0, 0, 255, 0])
        _, _, pix = read_png_gray(_png(_ihdr(2, 1, 8, 2), rows))
        self.assertEqual(list(pix), [(255 * 299) // 1000, (255 * 587) // 1000])

    def test_rgba_composites_over_white(self) -> None:
        """Transparent regions read as paper, half-alpha black as mid-gray."""
        rows = b"\x00" + bytes([0, 0, 0, 255, 0, 0, 0, 128, 0, 0, 0, 0])
        _, _, pix = read_png_gray(_png(_ihdr(3, 1, 8, 6), rows))
        self.assertEqual(list(pix), [0, 127, 255])

    def test_all_five_filter_types_unfilter(self) -> None:
        """None/Sub/Up/Average/Paeth all reconstruct the same flat gray.

        Every row here encodes the constant value 40 under a different
        filter, so a broken predictor shows up as a non-constant row.
        """
        rows = (b"\x00" + bytes([40, 40, 40, 40])       # None
                + b"\x01" + bytes([40, 0, 0, 0])        # Sub
                + b"\x02" + bytes([0, 0, 0, 0])         # Up
                + b"\x03" + bytes([20, 0, 0, 0])        # Average
                + b"\x04" + bytes([0, 0, 0, 0]))        # Paeth
        _, _, pix = read_png_gray(_png(_ihdr(4, 5, 8, 0), rows))
        self.assertEqual(list(pix), [40] * 20)

    def test_sub_filter_uses_the_channel_stride_on_rgb(self) -> None:
        """Sub's left neighbour is the pixel before, not the byte before.

        A 2x1 RGB row Sub-filtered as [10,20,30, 30,40,50]. At bpp=3 it
        reconstructs to (10,20,30) and (40,60,80) — luminance 18 and 56.
        At bpp=1 it reconstructs to (10,30,60) and (90,130,180) instead,
        luminance 27 and 123. Nothing else in the suite walks this path:
        the other RGB/RGBA cases use filter 0, which returns before it
        ever reads bpp.
        """
        rows = b"\x01" + bytes([10, 20, 30, 30, 40, 50])
        _, _, pix = read_png_gray(_png(_ihdr(2, 1, 8, 2), rows))
        self.assertEqual(list(pix), [18, 56])

    def test_paeth_filter_uses_the_channel_stride_on_rgba(self) -> None:
        """Paeth's a and c neighbours step by 4 bytes on RGBA, not 1.

        Row 0 (filter 0) is the pixels (10,20,30,255) and (40,60,80,255);
        row 1 is Paeth-filtered so that at bpp=4 it reconstructs to
        (50,70,90,255) and (100,120,140,255) — luminance 66 and 116, with
        alpha 255 throughout so compositing is a no-op and only the
        stride is under test. This is the exact shape Chromium emits for
        Task 8's screenshots.
        """
        rows = (b"\x00" + bytes([10, 20, 30, 255, 40, 60, 80, 255])
                + b"\x04" + bytes([40, 50, 60, 0, 50, 50, 50, 0]))
        _, _, pix = read_png_gray(_png(_ihdr(2, 2, 8, 6), rows))
        self.assertEqual(list(pix), [18, 56, 66, 116])

    def test_paeth_tie_breaks_a_then_b_then_c(self) -> None:
        """A pa == pc tie must pick `a`; picking `c` corrupts the row.

        Row 0 is (c=10, b=11); row 1 starts a=8. For the last pixel
        pa=1, pb=2, pc=1 — the spec's `pa <= pb and pa <= pc` yields a=8,
        while any c-first ordering yields 10.
        """
        rows = b"\x00" + bytes([10, 11]) + b"\x04" + bytes([254, 0])
        _, _, pix = read_png_gray(_png(_ihdr(2, 2, 8, 0), rows))
        self.assertEqual(list(pix), [10, 11, 8, 8])

    def test_paeth_tie_breaks_b_before_c(self) -> None:
        """A pb == pc tie must pick `b` — the second branch's order.

        Row 0 is (c=10, b=8); row 1 starts a=11. For the last pixel
        pa=2, pb=1, pc=1, so `return b if pb <= pc else c` yields b=8,
        while a c-first spelling of that same branch yields 10. The
        pa == pc fixture above cannot reach this branch at all.
        """
        rows = b"\x00" + bytes([10, 8]) + b"\x04" + bytes([1, 0])
        _, _, pix = read_png_gray(_png(_ihdr(2, 2, 8, 0), rows))
        self.assertEqual(list(pix), [10, 8, 11, 8])


class TestDecoderRejects(unittest.TestCase):
    """Unsupported PNG variants fail loudly, naming what was found."""

    def test_bad_signature(self) -> None:
        """Not-a-PNG is rejected before any chunk walking."""
        with self.assertRaises(ValueError) as ctx:
            read_png_gray(b"GIF89a" + b"\x00" * 32)
        self.assertIn("signature", str(ctx.exception))

    def test_sixteen_bit_rejected(self) -> None:
        """Bit depth 16 is out of scope and says so."""
        with self.assertRaises(ValueError) as ctx:
            read_png_gray(_png(_ihdr(2, 2, 16, 0), b""))
        self.assertIn("16", str(ctx.exception))

    def test_palette_rejected(self) -> None:
        """Color type 3 (palette) is out of scope and says so."""
        with self.assertRaises(ValueError) as ctx:
            read_png_gray(_png(_ihdr(2, 2, 8, 3), b""))
        self.assertIn("3", str(ctx.exception))

    def test_interlaced_rejected(self) -> None:
        """Adam7 is out of scope and says so."""
        with self.assertRaises(ValueError) as ctx:
            read_png_gray(_png(_ihdr(2, 2, 8, 0, interlace=1), b""))
        self.assertIn("interlac", str(ctx.exception).lower())

    def test_missing_ihdr(self) -> None:
        """A stream with no header chunk is rejected."""
        with self.assertRaises(ValueError) as ctx:
            read_png_gray(PNG_SIGNATURE + _chunk(b"IEND", b""))
        self.assertIn("IHDR", str(ctx.exception))

    def test_truncated_scanlines(self) -> None:
        """Too few decompressed bytes for w*h is a corrupt stream."""
        with self.assertRaises(ValueError) as ctx:
            read_png_gray(_png(_ihdr(4, 4, 8, 0), b"\x00\x01\x02\x03\x04"))
        self.assertIn("scanline", str(ctx.exception))

    def test_unknown_filter_type(self) -> None:
        """Filter byte 5 does not exist in the PNG spec."""
        with self.assertRaises(ValueError) as ctx:
            read_png_gray(_png(_ihdr(2, 1, 8, 0), b"\x05\x00\x00"))
        self.assertIn("filter", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
