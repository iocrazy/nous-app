"""Direct stream downloader for yt-dlp-discovered formats.

Background — 2026-05-13: bilibili downloads were intermittently
stalling for 60s in the legacy `_do_douyin_download` → yt-dlp fallback
path because the download stage re-invoked yt-dlp from scratch
(re-fetching share-page HTML → wbi-sign → playurl API → finally the
m4s stream). The metadata stage had already pulled the same info_dict
during parse and got the actual stream URLs in `formats[]`, but
`_map_metadata_to_media` discarded them. The download-stage yt-dlp
then redid the entire scrape and was the one that hit the upstream
silence.

After migration 216 introduces `parsed_media.ytdlp_formats`, this
module pulls the m4s streams directly via httpx using the saved
`http_headers` (Referer is the critical bit for bilibili), then runs
ffmpeg with `-c copy` to remux into a single mp4. yt-dlp is not
invoked.

Live spike (2026-05-13 NAS container):
  * video.m4s 12MB → 0.72s (16.9 MB/s)
  * audio.m4s 12MB → 2.4s
  * ffmpeg merge → ~instant, 24.7MB mp4 with h264+aac

If anything in this path fails (token expired, ffmpeg missing,
network blip), the caller in `download_strategies.py` falls back to
the legacy yt-dlp subprocess so we never lose the previous behavior.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

import aiofiles
import httpx
from loguru import logger

from app.agent_framework.process_lifecycle import safe_popen_kwargs
from app.boundary import safe_async_client


# httpx GET timeout for a single m4s segment. m4s parts are typically
# 5-50 MB on bilibili — generous enough to not false-fail on slow CDN
# pops, tight enough to surface persistent silence quickly so we can
# fall back to yt-dlp.
_M4S_TIMEOUT_SEC = 90.0

# Read chunk size for streaming the m4s to disk. 256 KB matches the
# downloader.py convention.
_CHUNK_BYTES = 256 * 1024


@dataclass(frozen=True)
class DirectStreamResult:
    """Outcome of a direct-stream download attempt."""

    file_path: str
    file_size: int
    video_format_id: Optional[str]
    audio_format_id: Optional[str]


class DirectStreamUnavailable(Exception):
    """Raised when ytdlp_formats can't drive a direct-stream download.

    Caller should fall back to the legacy yt-dlp subprocess path."""


def _pick_best_video(formats: list[dict]) -> Optional[dict]:
    """Pick the highest-bitrate avc1 (H.264) video stream.

    Prefer avc1 over hev1/av01 for broadest player compatibility — the
    UI renders these straight into <video> tags. yt-dlp's own default
    selector follows the same ordering."""
    video_only = [
        f
        for f in formats
        if f.get("url")
        and (f.get("vcodec") or "none") != "none"
        and (f.get("acodec") or "none") == "none"
    ]
    if not video_only:
        return None

    # Rank: avc1 first, then hev1, then anything else; within each
    # codec class, highest tbr wins.
    def _codec_priority(vc: str) -> int:
        if vc.startswith("avc1"):
            return 0
        if vc.startswith("hev1") or vc.startswith("hvc1"):
            return 1
        return 2

    video_only.sort(
        key=lambda f: (
            _codec_priority(f.get("vcodec") or ""),
            -float(f.get("tbr") or 0),
        )
    )
    return video_only[0]


def _pick_best_audio(formats: list[dict]) -> Optional[dict]:
    """Highest-bitrate mp4a audio (so it pairs with H.264 in mp4)."""
    audio_only = [
        f
        for f in formats
        if f.get("url")
        and (f.get("acodec") or "none") != "none"
        and (f.get("vcodec") or "none") == "none"
    ]
    if not audio_only:
        return None
    audio_only.sort(key=lambda f: -float(f.get("tbr") or 0))
    return audio_only[0]


async def _download_segment(
    client: httpx.AsyncClient,
    url: str,
    headers: dict,
    dst_path: str,
    on_progress: Optional[Callable[[int, int], Awaitable[None]]] = None,
) -> int:
    """Stream a single m4s URL to disk, returns bytes written.

    Raises httpx.HTTPError or OSError on failure — caller decides
    whether to fall back."""
    written = 0
    # Bilibili m4s URLs are signed with a query-string token; httpx
    # follows redirects by default which is what we want.
    async with client.stream(
        "GET", url, headers=headers, timeout=_M4S_TIMEOUT_SEC
    ) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("Content-Length") or 0)
        async with aiofiles.open(dst_path, "wb") as f:
            async for chunk in resp.aiter_bytes(chunk_size=_CHUNK_BYTES):
                if not chunk:
                    continue
                await f.write(chunk)
                written += len(chunk)
                if on_progress is not None:
                    try:
                        await on_progress(written, total)
                    except Exception:
                        # Progress callback failures must not abort the
                        # download — UI updates are best-effort.
                        pass
    return written


async def _ffmpeg_merge(
    video_path: str,
    audio_path: str,
    output_path: str,
) -> None:
    """Remux video + audio into a single mp4 with -c copy (no re-encode).

    Raises RuntimeError on non-zero ffmpeg exit."""
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        video_path,
        "-i",
        audio_path,
        "-c",
        "copy",
        # Default mp4 container; do not move moov here — the existing
        # post-download `optimize_video_for_streaming` step does that.
        output_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **safe_popen_kwargs(),
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120.0)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("ffmpeg merge timed out after 120s")

    if proc.returncode != 0:
        err = (stderr or b"").decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"ffmpeg merge exit={proc.returncode}: {err}")


async def download_from_ytdlp_formats(
    *,
    ytdlp_formats: list[dict],
    output_dir: str,
    platform_id: str,
    progress_callback: Optional[Callable[[int, int], Awaitable[None]]] = None,
) -> DirectStreamResult:
    """Pull best-video + best-audio m4s via httpx, ffmpeg-merge to mp4.

    Raises DirectStreamUnavailable if the format list is missing the
    needed streams — caller should fall back to yt-dlp subprocess.

    Other exceptions (httpx.HTTPError, RuntimeError from ffmpeg, OSError)
    propagate up so the strategy layer can decide whether to retry or
    fall back."""
    if not ytdlp_formats:
        raise DirectStreamUnavailable("ytdlp_formats is empty")

    video = _pick_best_video(ytdlp_formats)
    audio = _pick_best_audio(ytdlp_formats)
    if video is None or audio is None:
        raise DirectStreamUnavailable(
            f"missing video/audio stream in ytdlp_formats "
            f"(video={'ok' if video else 'none'}, audio={'ok' if audio else 'none'})"
        )

    os.makedirs(output_dir, exist_ok=True)
    video_tmp = os.path.join(output_dir, ".video.m4s.part")
    audio_tmp = os.path.join(output_dir, ".audio.m4s.part")
    output_path = os.path.join(output_dir, "video.mp4")

    logger.info(
        f"[DirectStream] {platform_id}: pulling video={video.get('format_id')} "
        f"+ audio={audio.get('format_id')} via httpx"
    )

    # Pull both segments through the boundary-aware async client so
    # SSRF policy still applies at the URL layer (initial validate_url
    # already happened at parse time; this is defense in depth).
    async with safe_async_client(follow_redirects=True) as client:
        # Parallel download — bilibili usually serves video and audio
        # from different CDN edges, so they don't contend.
        v_task = asyncio.create_task(
            _download_segment(
                client,
                video["url"],
                video.get("http_headers") or {},
                video_tmp,
                on_progress=progress_callback,
            )
        )
        a_task = asyncio.create_task(
            _download_segment(
                client,
                audio["url"],
                audio.get("http_headers") or {},
                audio_tmp,
            )
        )
        try:
            v_bytes, a_bytes = await asyncio.gather(v_task, a_task)
        except Exception:
            # Clean up half-written partials so a retry starts fresh.
            for p in (video_tmp, audio_tmp):
                try:
                    if os.path.exists(p):
                        os.remove(p)
                except Exception:
                    pass
            raise

    logger.info(
        f"[DirectStream] {platform_id}: pulled video={v_bytes}B audio={a_bytes}B, "
        f"merging via ffmpeg"
    )

    try:
        await _ffmpeg_merge(video_tmp, audio_tmp, output_path)
    finally:
        # Remove partials regardless of merge outcome.
        for p in (video_tmp, audio_tmp):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass

    size = os.path.getsize(output_path)
    logger.success(f"[DirectStream] {platform_id}: merged {size}B → {output_path}")
    return DirectStreamResult(
        file_path=output_path,
        file_size=size,
        video_format_id=video.get("format_id"),
        audio_format_id=audio.get("format_id"),
    )
