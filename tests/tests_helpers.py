"""Shared builders and narrowed readers for the backend test suite."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] /
                       "skills" / "wysiwyg-grilling" / "scripts"))
import canvas


def el(**kw: Any) -> dict[str, Any]:
    """Build a full Excalidraw element dict with sane defaults.

    Args:
        **kw: Overrides merged over the defaults; `id` and `type` required.

    Returns:
        A dict shaped like a live Excalidraw element.
    """
    base: dict[str, Any] = {
        "x": 0, "y": 0, "width": 0, "height": 0, "angle": 0,
        "strokeColor": "#000", "backgroundColor": "transparent",
        "fillStyle": "solid", "strokeWidth": 1, "strokeStyle": "solid",
        "roughness": 1, "opacity": 100, "groupIds": [], "frameId": None,
        "roundness": None, "seed": 1, "version": 1, "versionNonce": 1,
        "isDeleted": False, "boundElements": [], "updated": 1,
        "link": None, "locked": False, "customData": {},
        "startArrowhead": None, "endArrowhead": "arrow",
    }
    base.update(kw)
    return base


def ink_box(els: list[dict[str, Any]],
            pad: float) -> tuple[float, float, float, float]:
    """`ink_extent`, with the empty answer refused instead of unpacked.

    SEVEN SITES ACROSS THREE MODULES wrote
    `x, y, w, h = canvas.ink_extent(scene, pad=0)` on a function that
    returns None for a scene with nothing live in it — and returns it
    deliberately, because "no extent" and "an extent of zero size" read
    differently at a call site. Every one of those unpackings meant
    "this scene has ink"; none of them said so, and an empty scene
    reached them as a `TypeError` about iterating None rather than as
    the finding it is.

    Args:
        els: The scene to measure.
        pad: Margin added on every side, in px. REQUIRED, unlike
            `ink_extent`'s own 40: a helper with a default of its own is
            a second place for the export pad to live, and the two would
            differ silently at whichever call site forgot.

    Returns:
        `(minx, miny, w, h)` in scene px.

    Raises:
        AssertionError: If nothing in `els` is live. That is a statement
            about the scene the test built, not about `ink_extent`.
    """
    box = canvas.ink_extent(els, pad=pad)
    if box is None:
        raise AssertionError(
            "ink_extent found nothing live in a scene of %d element(s), "
            "so there is no extent to unpack. The scene under test is "
            "empty (or entirely isDeleted), which is a fact about the "
            "test and not about the measurement" % len(els))
    return box


def measured(value: float | None, what: str) -> float:
    """A measurement that must exist, refusing the None that says it does not.

    THE COMPANION TO `ink_box`, for the scalar half. Several backend
    primitives answer `float | None` and mean something precise by the
    None — `side_normal_cos` for a segment with no direction,
    `shape_norm`/`shape_clearance` for an outline an inset has eaten. A
    test that then writes `assertLess(value, X)` is asserting the
    measurement EXISTS as well as its size, and only ever says the
    second half out loud.

    DELIBERATELY NOT `self.assertIsNotNone(v)` FOLLOWED BY THE
    COMPARISON, which is what this repo's first draft used and what a
    reader will reach for. Measured 2026-08-18 under this repo's
    `pyrightconfig.json`: `assertIsNotNone` leaves the type `float |
    None` — `unittest`'s assertions are not narrowing functions — so the
    pair reads as a narrowing that is not one, and the comparison below
    it stays unchecked. This raises instead, which both narrows and
    fails with a sentence about the measurement.

    Args:
        value: Whatever the primitive answered.
        what: How to name it in the failure, e.g. "the drawn arrival
            angle". Read by someone deciding whether the scene is wrong
            or the reading is.

    Returns:
        `value`, known to be a number.

    Raises:
        AssertionError: If `value` is None.
    """
    if value is None:
        raise AssertionError(
            "%s is None — the measurement does not exist, so there is "
            "nothing to compare it against. That is a fact about the "
            "scene under test, not about the size of anything" % what)
    return value
