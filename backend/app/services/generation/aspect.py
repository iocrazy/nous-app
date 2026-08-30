"""Aspect ratio vocabulary shared by every image/video path.

Single source of truth for the eight ratios the canvas offers. Provider
modules import from here; none keeps its own copy (that is how the daemon
path ended up with a different key than the server path).
"""

from __future__ import annotations

from typing import Optional

ASPECT_RATIOS: dict[str, float] = {
    "21:9": 21 / 9,
    "16:9": 16 / 9,
    "3:2": 3 / 2,
    "4:3": 4 / 3,
    "1:1": 1.0,
    "3:4": 3 / 4,
    "2:3": 2 / 3,
    "9:16": 9 / 16,
}

# Sentence fragments appended to a prompt for providers that only honour
# shape through language (codex — measured 2026-08-23, `--size` is ignored).
ASPECT_PHRASES: dict[str, str] = {
    "21:9": "21:9 ultra-wide landscape (much wider than tall)",
    "16:9": "16:9 landscape (wider than tall)",
    "3:2": "3:2 landscape (wider than tall)",
    "4:3": "4:3 landscape (wider than tall)",
    "1:1": "1:1 square (equal width and height)",
    "3:4": "3:4 portrait (taller than wide)",
    "2:3": "2:3 portrait (taller than wide)",
    "9:16": "9:16 tall portrait (much taller than wide)",
}

# gpt-image-2-skill --size values. Kept for the daemon's `size` key (old
# daemons need it) even though the upstream does not honour it.
CODEX_SIZES: dict[str, str] = {
    "21:9": "1536x1024",
    "16:9": "1536x1024",
    "3:2": "1536x1024",
    "4:3": "1536x1024",
    "1:1": "1024x1024",
    "3:4": "1024x1536",
    "2:3": "1024x1536",
    "9:16": "1024x1536",
}
CODEX_DEFAULT_SIZE = "1024x1024"

# |got/want - 1| within this counts as honoured (P2 uses it when measuring).
ASPECT_TOLERANCE = 0.06


def aspect_instruction(aspect: str) -> str:
    """The sentence appended to a prompt to pin the output shape.

    Appended, never prepended, and only for a known aspect: the user's words
    stay first and intact. Unknown or empty adds nothing — "let the model
    choose" is a real request (IC 自适应 sends an empty aspect on purpose).
    """
    phrase = ASPECT_PHRASES.get((aspect or "").strip())
    if not phrase:
        return ""
    return (
        f"\n\nOutput image aspect ratio: {phrase}. "
        "The whole image must have this shape."
    )


def nearest_ratio(width: int, height: int) -> Optional[str]:
    """The offered ratio closest to width/height, or None for a degenerate size."""
    if width <= 0 or height <= 0:
        return None
    target = width / height
    return min(ASPECT_RATIOS, key=lambda r: abs(ASPECT_RATIOS[r] - target))
