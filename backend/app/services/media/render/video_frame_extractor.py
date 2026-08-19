"""Q2 — Video frame extraction → multimodal Attachment[].

Used by:
  - chat agents that receive a video URL/file in the user message and
    need to "see" key frames (vision-capable models)
  - visual_analysis agent's pre-pass (replaces ad-hoc scene detection)
  - storyboard tools that want a uniform Attachment surface

Strategy:
  - Use ffmpeg to sample ``num_frames`` evenly across the video duration
  - Output JPEG (smaller than PNG; vision models accept either)
  - Return Attachment list with ``data_url`` populated (base64)

Notes:
  - data_url payload size: ~30-100 KB per frame depending on resolution.
    For >12 frames, prefer the file-URL path (caller uploads to storage,
    passes ``url=`` instead). This module focuses on data_url for the
    common 4-8 frame case.
  - ffprobe required upfront for duration; falls back to evenly spaced
    sampling without seeking when duration unknown.
  - Subprocess uses ``safe_popen_kwargs()`` so children die with parent
    on SIGKILL/OOM.
"""

from __future__ import annotations

import asyncio
import base64
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from loguru import logger

from app.agent_framework.multimodal import Attachment, AttachmentKind
from app.agent_framework.process_lifecycle import safe_popen_kwargs

# Default config — small enough that 8 frames stay under typical 10 MB
# vision-model attachment cap.
DEFAULT_NUM_FRAMES = 6
DEFAULT_FRAME_WIDTH = 640  # px (preserves aspect, letterbox-free)
DEFAULT_JPEG_QUALITY = 4  # ffmpeg -q:v 1 best, 31 worst; 4 ≈ 80% JPEG


@dataclass(frozen=True)
class FrameExtractionResult:
    """Bundle of attachments + diagnostics for the caller."""

    attachments: List[Attachment]
    duration_seconds: Optional[float]
    sampled_at_seconds: List[float]
    """Timestamps at which each frame was sampled (parallel to attachments)."""
    error: Optional[str] = None


async def _probe_duration(video_path: str) -> Optional[float]:
    """ffprobe for duration. None on failure."""
    if not shutil.which("ffprobe"):
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            video_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **safe_popen_kwargs(),
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        if proc.returncode != 0:
            return None
        text = stdout.decode().strip()
        return float(text) if text else None
    except (asyncio.TimeoutError, ValueError, Exception) as exc:
        logger.warning(f"[VideoFrameExtractor] probe_duration failed: {exc}")
        return None


def seek_frame_cmd(
    video_path: str,
    timestamp_seconds: float,
    out_path: str,
    *,
    frame_width: int = DEFAULT_FRAME_WIDTH,
    jpeg_quality: int = DEFAULT_JPEG_QUALITY,
) -> List[str]:
    """The ffmpeg argv that turns "this file, this second" into one JPEG.

    **Single source of truth on purpose.** Two callers depend on landing on the
    *same source frame* for the same timestamp: ``extract_frames`` (which
    samples the candidate previews the user picks from) and
    ``extract_frame_at`` (which re-reads the picked one at full size, minutes
    later). If those two ever built slightly different argv — a different seek
    format, an added ``-accurate_seek``, an input-vs-output ``-ss`` — the user
    would pick frame A and get frame B, and nothing would report an error.
    Sharing the builder makes that class of drift impossible rather than
    merely unlikely, and ``tests/test_cover_frames_same_frame.py`` pins it.

    ``-ss`` before ``-i`` is input seeking, which ffmpeg performs accurately
    (it seeks to the preceding keyframe and decodes forward to the requested
    timestamp). It is also *deterministic*: measured across mp4/mkv/mov/webm,
    sparse-GOP + B-frames and VFR sources, three runs at one timestamp produce
    byte-identical JPEGs, and the widths 240 / 1080 / native all decode the
    same source frame.

    The clamps live here, not only in the callers, so both paths normalise
    identical inputs to identical argv.
    """
    frame_width = max(120, min(1920, frame_width))
    jpeg_quality = max(1, min(31, jpeg_quality))
    return [
        "ffmpeg",
        "-y",
        "-ss",
        f"{timestamp_seconds:.3f}",
        "-i",
        video_path,
        "-frames:v",
        "1",
        "-vf",
        f"scale={frame_width}:-1",
        "-q:v",
        str(jpeg_quality),
        out_path,
    ]


async def extract_frame_at(
    video_path: str,
    *,
    timestamp_seconds: float,
    frame_width: int = DEFAULT_FRAME_WIDTH,
    jpeg_quality: int = DEFAULT_JPEG_QUALITY,
    timeout_seconds: float = 60.0,
) -> Optional[bytes]:
    """Decode exactly one frame, at ``timestamp_seconds``, as JPEG bytes.

    The single-frame counterpart to ``extract_frames``, built for "re-read the
    frame the user already picked". Same graceful-degrade contract as its
    sibling: ``None`` on any failure (missing file, no ffmpeg, timestamp past
    the end, ffmpeg error) rather than an exception, so callers decide what a
    missing frame means for them — the cover path turns it into a typed 422.

    Cleanup mirrors ``extract_frames``: the JPEG is written inside a
    ``TemporaryDirectory`` and read back before the context exits, so the
    success, failure and timeout paths all leave nothing behind.
    """
    src = Path(video_path)
    if not src.exists() or not src.is_file():
        logger.warning(f"[VideoFrameExtractor] frame_at: video not found: {video_path}")
        return None
    if not shutil.which("ffmpeg"):
        logger.warning("[VideoFrameExtractor] frame_at: ffmpeg not installed")
        return None
    if timestamp_seconds < 0:
        logger.warning(
            f"[VideoFrameExtractor] frame_at: negative timestamp {timestamp_seconds}"
        )
        return None

    with tempfile.TemporaryDirectory(prefix="vfx1_") as tmp:
        out = Path(tmp) / "frame.jpg"
        cmd = seek_frame_cmd(
            str(src),
            timestamp_seconds,
            str(out),
            frame_width=frame_width,
            jpeg_quality=jpeg_quality,
        )
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                **safe_popen_kwargs(),
            )
            _, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_seconds
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"[VideoFrameExtractor] frame_at {timestamp_seconds:.3f}s timed out"
            )
            return None
        if proc.returncode != 0 or not out.exists():
            logger.warning(
                f"[VideoFrameExtractor] frame_at {timestamp_seconds:.3f}s failed: "
                f"{stderr.decode(errors='replace')[:200]}"
            )
            return None
        raw = out.read_bytes()
        return raw or None


def _frame_to_data_url(frame_path: Path) -> Optional[str]:
    """Read JPEG, base64 encode → data URL string."""
    try:
        raw = frame_path.read_bytes()
        if not raw:
            return None
        b64 = base64.b64encode(raw).decode("ascii")
        return f"data:image/jpeg;base64,{b64}"
    except Exception as exc:
        logger.warning(f"[VideoFrameExtractor] read frame failed: {frame_path}: {exc}")
        return None


async def extract_frames(
    video_path: str,
    *,
    num_frames: int = DEFAULT_NUM_FRAMES,
    frame_width: int = DEFAULT_FRAME_WIDTH,
    jpeg_quality: int = DEFAULT_JPEG_QUALITY,
    timeout_seconds: float = 60.0,
) -> FrameExtractionResult:
    """Extract ``num_frames`` evenly-spaced frames from a video file.

    Returns FrameExtractionResult with attachments populated. On failure
    returns a result with empty attachments and an error message — callers
    should treat this as graceful degrade (chat continues without vision).

    Validation:
      - num_frames clamped to [1, 32]
      - frame_width clamped to [120, 1920]
      - jpeg_quality clamped to [1, 31]
      - video_path must exist + be readable
    """
    num_frames = max(1, min(32, num_frames))
    frame_width = max(120, min(1920, frame_width))
    jpeg_quality = max(1, min(31, jpeg_quality))

    src = Path(video_path)
    if not src.exists() or not src.is_file():
        return FrameExtractionResult(
            attachments=[],
            duration_seconds=None,
            sampled_at_seconds=[],
            error=f"video not found: {video_path}",
        )

    if not shutil.which("ffmpeg"):
        return FrameExtractionResult(
            attachments=[],
            duration_seconds=None,
            sampled_at_seconds=[],
            error="ffmpeg not installed",
        )

    duration = await _probe_duration(str(src))

    # Compute sampling timestamps. If duration unknown, fall back to fps=N
    # mode (lets ffmpeg pick spacing).
    if duration is not None and duration > 0:
        # Skip the first/last 5% to avoid black title cards / fade-outs
        margin = duration * 0.05
        usable = duration - 2 * margin
        if usable <= 0 or num_frames == 1:
            timestamps = [duration / 2.0]
        else:
            step = usable / max(1, num_frames - 1) if num_frames > 1 else 0
            timestamps = [margin + step * i for i in range(num_frames)]
    else:
        timestamps = []

    with tempfile.TemporaryDirectory(prefix="vfx_") as tmp:
        tmp_path = Path(tmp)
        attachments: List[Attachment] = []
        sampled_at: List[float] = []

        if timestamps:
            # Seek + 1-frame extract per timestamp. More accurate than
            # filter_complex for shorter videos.
            for idx, ts in enumerate(timestamps):
                out = tmp_path / f"frame_{idx:03d}.jpg"
                cmd = seek_frame_cmd(
                    str(src),
                    ts,
                    str(out),
                    frame_width=frame_width,
                    jpeg_quality=jpeg_quality,
                )
                try:
                    proc = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.PIPE,
                        **safe_popen_kwargs(),
                    )
                    _, stderr = await asyncio.wait_for(
                        proc.communicate(),
                        timeout=timeout_seconds / max(1, len(timestamps)),
                    )
                    if proc.returncode != 0 or not out.exists():
                        logger.warning(
                            f"[VideoFrameExtractor] frame {idx} extraction failed: "
                            f"{stderr.decode(errors='replace')[:200]}"
                        )
                        continue
                    data_url = _frame_to_data_url(out)
                    if data_url:
                        attachments.append(
                            Attachment(
                                kind=AttachmentKind.VIDEO_THUMBNAIL,
                                data_url=data_url,
                                mime="image/jpeg",
                                alt_text=f"frame at {ts:.1f}s",
                            )
                        )
                        sampled_at.append(ts)
                except asyncio.TimeoutError:
                    logger.warning(
                        f"[VideoFrameExtractor] frame {idx} extraction timed out"
                    )
                    continue
        else:
            # Duration unknown — let ffmpeg pick frames at a rate that
            # gives ~num_frames over the unknown total. Use fps filter
            # with very low rate; cap total via -frames:v.
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(src),
                "-vf",
                f"fps=1/2,scale={frame_width}:-1",
                "-frames:v",
                str(num_frames),
                "-q:v",
                str(jpeg_quality),
                str(tmp_path / "frame_%03d.jpg"),
            ]
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                    **safe_popen_kwargs(),
                )
                _, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout_seconds
                )
                if proc.returncode != 0:
                    return FrameExtractionResult(
                        attachments=[],
                        duration_seconds=None,
                        sampled_at_seconds=[],
                        error=f"ffmpeg fallback failed: "
                        f"{stderr.decode(errors='replace')[:200]}",
                    )
                for frame_path in sorted(tmp_path.glob("frame_*.jpg")):
                    data_url = _frame_to_data_url(frame_path)
                    if data_url:
                        attachments.append(
                            Attachment(
                                kind=AttachmentKind.VIDEO_THUMBNAIL,
                                data_url=data_url,
                                mime="image/jpeg",
                                alt_text=f"frame {frame_path.stem}",
                            )
                        )
            except asyncio.TimeoutError:
                return FrameExtractionResult(
                    attachments=[],
                    duration_seconds=None,
                    sampled_at_seconds=[],
                    error=f"ffmpeg timed out after {timeout_seconds}s",
                )

        return FrameExtractionResult(
            attachments=attachments,
            duration_seconds=duration,
            sampled_at_seconds=sampled_at,
        )


__all__ = [
    "FrameExtractionResult",
    "extract_frame_at",
    "extract_frames",
    "seek_frame_cmd",
    "DEFAULT_NUM_FRAMES",
    "DEFAULT_FRAME_WIDTH",
    "DEFAULT_JPEG_QUALITY",
]
