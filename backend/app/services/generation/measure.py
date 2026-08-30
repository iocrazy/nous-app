"""What actually came back, measured — never inferred.

`ok: true` from a provider has already been shown to mean nothing about
shape: every one of the four production images whose ratio was wrong came
back with a success flag. So the record is built from real pixels.

Everything here degrades to None. A generation that already happened and
was already paid for must never fail because we could not measure it.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Optional

from loguru import logger

from app.services.generation.aspect import ASPECT_RATIOS, ASPECT_TOLERANCE

# ffprobe reads a header; anything slower than this is a stuck mount, not a
# big file. The wait is bounded so a hung probe cannot stall the workflow.
_FFPROBE_TIMEOUT_S = 30


@dataclass(frozen=True)
class Measured:
    width: Optional[int] = None
    height: Optional[int] = None
    duration_s: Optional[float] = None


def measure_image(path: str) -> Optional[Measured]:
    """Real pixel dimensions, or None when the file cannot be read."""
    if not path or not os.path.isfile(path):
        return None
    try:
        from PIL import Image

        with Image.open(path) as img:
            width, height = img.size
    except Exception as exc:  # corrupt, truncated, unsupported — all the same
        logger.warning("[measure] could not read image {}: {}", path[:200], exc)
        return None
    if width <= 0 or height <= 0:
        return None
    return Measured(width=width, height=height)


async def measure_video(path: str) -> Optional[Measured]:
    """Dimensions + duration via ffprobe, or None when it cannot be read."""
    if not path or not os.path.isfile(path):
        return None
    from app.agent_framework.process_lifecycle import safe_popen_kwargs

    args = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height:format=duration",
        "-of",
        "json",
        path,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **safe_popen_kwargs(),
        )
    except Exception as exc:
        logger.warning("[measure] could not spawn ffprobe for {}: {}", path[:200], exc)
        return None
    try:
        out, err = await asyncio.wait_for(
            proc.communicate(), timeout=_FFPROBE_TIMEOUT_S
        )
    except Exception as exc:
        # Reach quiescence, do not just give up on it: an abandoned ffprobe
        # holding the file open is an orphan, and the caller has no handle.
        logger.warning("[measure] ffprobe failed for {}: {}", path[:200], exc)
        await _terminate(proc)
        return None
    if proc.returncode != 0:
        logger.warning(
            "[measure] ffprobe rc={} for {}: {}",
            proc.returncode,
            path[:200],
            (err or b"").decode(errors="replace")[:200],
        )
        return None
    try:
        data = json.loads(out.decode() or "{}")
        stream = (data.get("streams") or [{}])[0]
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
        raw_duration = (data.get("format") or {}).get("duration")
        duration = float(raw_duration) if raw_duration else None
    except Exception as exc:
        logger.warning(
            "[measure] unreadable ffprobe output for {}: {}", path[:200], exc
        )
        return None
    if width <= 0 or height <= 0:
        return None
    return Measured(width=width, height=height, duration_s=duration)


async def _terminate(proc: asyncio.subprocess.Process) -> None:
    """Kill a probe we stopped waiting on, then await its actual exit."""
    try:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
    except Exception as exc:  # already reaped, or unkillable — log, never raise
        logger.warning("[measure] could not reap ffprobe: {}", exc)


def compare_aspect(
    requested_ratio: Optional[str], width: int, height: int
) -> Optional[bool]:
    """Did the product match the shape that was asked for?

    None means the question does not apply — no ratio was requested (IC
    自适应 sends an empty aspect on purpose), the ratio is not one we know,
    or the size is degenerate. A None must never be stored as False: "we
    did not ask" and "they ignored us" are different facts.
    """
    want = ASPECT_RATIOS.get((requested_ratio or "").strip())
    if not want or width <= 0 or height <= 0:
        return None
    got = width / height
    return abs(got - want) <= want * ASPECT_TOLERANCE
