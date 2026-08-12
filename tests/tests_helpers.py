"""Shared scene-element builder for the mutation harness tests."""
from __future__ import annotations

from typing import Any


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
