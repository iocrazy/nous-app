# app/services/transcode_service.py

"""
HLS Transcode Service

Multi-bitrate HLS transcoding using ffmpeg. Generates adaptive streaming
playlists (master.m3u8) with quality tiers: 480p, 720p, 1080p.
Skips tiers above the original video resolution.
"""

import asyncio
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Coroutine, List, Optional, Tuple

from loguru import logger

from app.agent_framework.process_lifecycle import safe_popen_kwargs
from app.core.config import settings
from app.repositories.resources_repository import ResourcesRepository


@dataclass
class TranscodeTier:
    name: str
    width: int
    height: int
    bitrate: int  # kbps
    audio_bitrate: int  # kbps


# Quality tiers — only tiers at or below original resolution are used
TIERS = [
    TranscodeTier(name="480p", width=854, height=480, bitrate=1500, audio_bitrate=128),
    TranscodeTier(name="720p", width=1280, height=720, bitrate=4000, audio_bitrate=128),
    TranscodeTier(
        name="1080p", width=1920, height=1080, bitrate=8000, audio_bitrate=192
    ),
]


class TranscodeService:
    """HLS multi-bitrate transcoding service."""

    # GPU encoder priority order: (codec_name, hwaccel_input_args)
    _GPU_ENCODERS = [
        ("h264_nvenc", ["-hwaccel", "cuda"]),  # NVIDIA
        ("h264_videotoolbox", []),  # macOS
        ("h264_qsv", ["-hwaccel", "qsv"]),  # Intel
    ]

    def __init__(self):
        self.repo = ResourcesRepository()
        self._encoder: Optional[str] = None  # lazy-init
        self._hwaccel_args: List[str] = []
        self._preset: str = settings.FFMPEG_PRESET

    # ------------------------------------------------------------------ #
    # GPU encoder detection
    # ------------------------------------------------------------------ #

    async def _detect_encoder(self) -> tuple[str, list[str], str]:
        """Detect best available encoder. Returns (codec, hwaccel_args, preset)."""
        if self._encoder:
            return self._encoder, self._hwaccel_args, self._preset

        configured = settings.FFMPEG_ENCODER
        if configured != "auto":
            # Explicit config — trust it
            self._encoder = configured
            for name, args in self._GPU_ENCODERS:
                if name == configured:
                    self._hwaccel_args = args
                    break
            return self._encoder, self._hwaccel_args, self._preset

        # Auto-detect: probe each GPU encoder
        for name, hwaccel_args in self._GPU_ENCODERS:
            if await self._probe_encoder(name):
                logger.info(f"[Transcode] GPU encoder detected: {name}")
                self._encoder = name
                self._hwaccel_args = hwaccel_args
                if "nvenc" in name:
                    self._preset = self._map_nvenc_preset(settings.FFMPEG_PRESET)
                return self._encoder, self._hwaccel_args, self._preset

        # Fallback to CPU
        logger.info("[Transcode] No GPU encoder found, using libx264 (CPU)")
        self._encoder = "libx264"
        return self._encoder, self._hwaccel_args, self._preset

    @staticmethod
    async def _probe_encoder(encoder_name: str) -> bool:
        """Test if the given encoder actually works on THIS machine.

        `ffmpeg -encoders` only reflects what ffmpeg was *compiled* with —
        a build with nvenc support lists `h264_nvenc` even on a box with no
        GPU. Probing that way made the NAS pick `h264_nvenc`, fail every
        tier with "No device available", and only then fall back to
        libx264 — one wasted failed attempt per tier plus ERROR-level log
        spam on every transcode. Instead, actually run a 1-frame encode and
        check the return code: a missing GPU / CUDA driver makes this fail
        fast, so the encoder is correctly skipped.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "nullsrc=s=64x64:d=0.1",
                "-c:v",
                encoder_name,
                "-frames:v",
                "1",
                "-f",
                "null",
                "-",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **safe_popen_kwargs(),
            )
            await proc.communicate()
            return proc.returncode == 0
        except Exception:
            return False

    @staticmethod
    def _map_nvenc_preset(cpu_preset: str) -> str:
        """Map CPU preset names to NVENC p1-p7 presets."""
        mapping = {
            "ultrafast": "p1",
            "veryfast": "p2",
            "fast": "p3",
            "medium": "p4",
            "slow": "p5",
            "veryslow": "p6",
        }
        return mapping.get(cpu_preset, "p4")

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    # Type alias for async progress callback: (progress: int, subtitle: str) -> None
    ProgressCallback = Callable[[int, str], Coroutine]

    async def transcode_version(
        self,
        resource_id: str,
        version_id: str,
        on_progress: Optional["TranscodeService.ProgressCallback"] = None,
    ) -> dict[str, Any]:
        """
        Transcode a resource version to HLS multi-bitrate.

        Args:
            on_progress: Optional async callback(progress_int, subtitle_str) for task tracking.

        Returns a structured outcome dict:
          - {"status": "completed", "hls_path": "teams/.../master.m3u8"}
          - {"status": "skipped", "reason": "..."} when intentionally skipped
            (transcoding disabled, file below admin size threshold, etc.)
          - {"status": "failed", "reason": "..."} on real failure (missing
            version row, missing source file, ffmpeg crash, etc.)

        The workflow distinguishes "skipped" from "failed" so a small file
        below the admin threshold isn't surfaced to the user as a transcode
        failure in Activity Logs.
        """
        # Master toggle check (read from DB for Celery worker compatibility)
        db_enabled = await self._get_db_setting("transcode_enabled")
        is_enabled = (
            db_enabled.lower() in ("true", "1", "yes")
            if db_enabled
            else settings.TRANSCODE_ENABLED
        )
        if not is_enabled:
            logger.info("[Transcode] Transcoding is disabled via settings")
            return {"status": "skipped", "reason": "transcoding disabled in settings"}

        version = await self.repo.get_version_by_id(version_id)
        if not version:
            logger.error(f"Version {version_id} not found")
            return {"status": "failed", "reason": f"version {version_id} not found"}

        file_path = version.get("file_path")
        if not file_path:
            logger.error(f"Version {version_id} has no file_path")
            return {"status": "failed", "reason": "version has no file_path"}

        # Size gate (admin-configured). transcode_min_size_mb lives in
        # system_settings and main.py loads it into settings at startup,
        # but the transcode trigger path stopped consuming it during the
        # PR-D7 workflow refactor — small files were transcoded regardless
        # of the admin threshold. Restoring the gate here covers every
        # trigger path (DBOS workflow, chained-from-download, manual
        # re-transcode). min_size_mb = 0 means "transcode everything".
        min_size_raw = await self._get_db_setting("transcode_min_size_mb")
        try:
            min_size_mb = (
                int(min_size_raw)
                if min_size_raw is not None
                else settings.TRANSCODE_MIN_SIZE_MB
            )
        except (TypeError, ValueError):
            min_size_mb = settings.TRANSCODE_MIN_SIZE_MB
        file_size_bytes = version.get("file_size_bytes") or 0
        if min_size_mb > 0 and file_size_bytes < min_size_mb * 1024 * 1024:
            size_mb = file_size_bytes / 1024 / 1024
            logger.info(
                f"[Transcode] Version {version_id} ({size_mb:.1f} MB) is "
                f"below the {min_size_mb} MB admin threshold — skipping HLS"
            )
            await self.repo.update_version(version_id, {"transcode_status": "skipped"})
            return {
                "status": "skipped",
                "reason": f"file {size_mb:.1f} MB below {min_size_mb} MB threshold",
            }

        # Mark as processing
        await self.repo.update_version(version_id, {"transcode_status": "processing"})

        base = Path(settings.DOWNLOAD_PATH)
        source = base / file_path

        if not source.exists():
            logger.error(f"Source file not found: {source}")
            await self.repo.update_version(version_id, {"transcode_status": "failed"})
            return {"status": "failed", "reason": f"source file not found: {source}"}

        # Determine HLS output directory: sibling hls/ folder next to the source file
        hls_dir = source.parent / "hls"

        try:
            # Probe video to get resolution and duration
            width, height = await self._probe_resolution(str(source))
            if not width or not height:
                logger.warning(
                    f"Could not probe resolution for {source}, skipping transcode"
                )
                await self.repo.update_version(
                    version_id, {"transcode_status": "failed"}
                )
                return {
                    "status": "failed",
                    "reason": "could not probe video resolution",
                }

            total_duration = await self._probe_duration(str(source))

            # Detect source codecs for fast-path decision
            video_codec, audio_codec = await self._probe_codecs(str(source))
            is_h264 = video_codec in ("h264",)

            # Clean up old HLS if exists
            if hls_dir.exists():
                shutil.rmtree(hls_dir)
            hls_dir.mkdir(parents=True, exist_ok=True)

            if is_h264:
                # ============================================================
                # H.264 Fast Path: two-phase strategy
                # Phase 1: copy-only segmentation (seconds) → immediate playback
                # Phase 2: enhancement tiers (background) → rewrite playlist
                # ============================================================
                logger.info(
                    f"[Transcode] H.264 fast path: {video_codec}/{audio_codec} "
                    f"for version {version_id}"
                )

                # Phase 1: Fast copy-only segmentation
                if on_progress:
                    await on_progress(10, "Fast segmenting (copy)...")
                source_bitrate = await self._probe_bitrate(str(source))
                passthrough_ok = await self._transcode_passthrough(
                    str(source),
                    hls_dir,
                    audio_codec,
                )

                if not passthrough_ok:
                    logger.warning(
                        "[Transcode] H.264 passthrough failed, falling back to full encode"
                    )
                    # Fall through to standard encoding path below
                else:
                    # Write initial master.m3u8 with source-only tier
                    self._write_master_playlist(
                        hls_dir,
                        [],
                        passthrough=True,
                        source_width=width,
                        source_height=height,
                        source_bitrate=source_bitrate,
                    )

                    # Build relative path
                    master_path = hls_dir / "master.m3u8"
                    relative_hls = str(master_path.relative_to(base))

                    # Mark as completed — user can play HLS immediately
                    await self.repo.update_version(
                        version_id,
                        {
                            "hls_path": relative_hls,
                            "transcode_status": "completed",
                            "transcode_at": datetime.now(timezone.utc).isoformat(),
                        },
                    )

                    if on_progress:
                        await on_progress(50, "HLS ready, encoding quality tiers...")

                    logger.success(
                        f"[Transcode] H.264 fast path completed in seconds: "
                        f"resource={resource_id}, version={version_id}"
                    )

                    # Phase 2: Enhancement tiers (non-blocking for user)
                    applicable = await self._select_tiers(width, height)
                    if applicable:
                        try:
                            for tier in applicable:
                                (hls_dir / tier.name).mkdir(parents=True, exist_ok=True)

                            encoded_tiers = await self._encode_tiers(
                                str(source),
                                applicable,
                                hls_dir,
                                total_duration,
                                on_progress,
                                version_id,
                                progress_base=50,
                                progress_cap=95,
                            )

                            if encoded_tiers:
                                # Rewrite master.m3u8 with all tiers
                                self._write_master_playlist(
                                    hls_dir,
                                    encoded_tiers,
                                    passthrough=True,
                                    source_width=width,
                                    source_height=height,
                                    source_bitrate=source_bitrate,
                                )
                                logger.info(
                                    f"[Transcode] Enhancement tiers added: "
                                    f"{[t.name for t in encoded_tiers]}"
                                )
                        except Exception as e:
                            # Enhancement failure does NOT affect completed HLS
                            logger.warning(
                                f"[Transcode] Enhancement tiers failed (non-fatal): {e}"
                            )

                    if on_progress:
                        await on_progress(100, "Done")
                    return {"status": "completed", "hls_path": relative_hls}

            # ============================================================
            # Standard Path: full encoding (non-H.264 or passthrough failed)
            # ============================================================
            # Select applicable tiers
            applicable = await self._select_tiers(width, height)
            if not applicable:
                logger.info(f"No applicable tiers for {width}x{height}, skipping")
                await self.repo.update_version(
                    version_id, {"transcode_status": "skipped"}
                )
                return {
                    "status": "skipped",
                    "reason": f"no applicable tiers for {width}x{height}",
                }

            # Ensure tier directories exist
            for tier in applicable:
                (hls_dir / tier.name).mkdir(parents=True, exist_ok=True)

            encoded_tiers = await self._encode_tiers(
                str(source),
                applicable,
                hls_dir,
                total_duration,
                on_progress,
                version_id,
                progress_base=0,
                progress_cap=95,
            )

            if not encoded_tiers:
                await self.repo.update_version(
                    version_id, {"transcode_status": "failed"}
                )
                return {"status": "failed", "reason": "all tier encodings failed"}

            # Add passthrough "Original" tier (copy codec, no re-encoding)
            if on_progress:
                await on_progress(95, "Remuxing original...")
            source_bitrate = await self._probe_bitrate(str(source))
            passthrough_ok = await self._transcode_passthrough(
                str(source),
                hls_dir,
                audio_codec,
            )

            # Generate master playlist
            if on_progress:
                await on_progress(98, "Writing playlist...")
            self._write_master_playlist(
                hls_dir,
                encoded_tiers,
                passthrough=passthrough_ok,
                source_width=width,
                source_height=height,
                source_bitrate=source_bitrate,
            )

            # Build relative path
            master_path = hls_dir / "master.m3u8"
            relative_hls = str(master_path.relative_to(base))

            # Update DB
            await self.repo.update_version(
                version_id,
                {
                    "hls_path": relative_hls,
                    "transcode_status": "completed",
                    "transcode_at": datetime.now(timezone.utc).isoformat(),
                },
            )

            if on_progress:
                await on_progress(100, "Done")

            logger.success(
                f"Transcode completed: resource={resource_id}, version={version_id}, "
                f"tiers={[t.name for t in encoded_tiers]}"
            )
            return {"status": "completed", "hls_path": relative_hls}

        except Exception as e:
            logger.error(f"Transcode failed for version {version_id}: {e}")
            await self.repo.update_version(version_id, {"transcode_status": "failed"})
            return {"status": "failed", "reason": str(e)[:200]}

    # ------------------------------------------------------------------ #
    # Tier encoding (shared by both fast-path and standard-path)
    # ------------------------------------------------------------------ #

    async def _encode_tiers(
        self,
        source: str,
        applicable: List[TranscodeTier],
        hls_dir: Path,
        total_duration: Optional[float],
        on_progress: Optional["TranscodeService.ProgressCallback"],
        version_id: str,
        progress_base: int = 0,
        progress_cap: int = 95,
    ) -> Optional[List[TranscodeTier]]:
        """Encode applicable tiers (parallel or serial). Returns list of successful tiers, or None if all failed."""
        num_tiers = len(applicable)
        progress_range = progress_cap - progress_base

        if settings.TRANSCODE_PARALLEL_TIERS and num_tiers > 1:
            # ── Parallel tier encoding ──
            tier_progress_map: dict[str, float] = {}

            def _make_tier_progress(tier: TranscodeTier):
                async def _progress(pct: float):
                    tier_progress_map[tier.name] = pct
                    avg = sum(tier_progress_map.values()) / num_tiers
                    overall = progress_base + int(avg * progress_range / 100)
                    active = ", ".join(
                        f"{k} {int(v)}%" for k, v in sorted(tier_progress_map.items())
                    )
                    if on_progress:
                        await on_progress(
                            min(overall, progress_cap), f"Encoding {active}"
                        )

                return _progress

            results = await asyncio.gather(
                *[
                    self._transcode_tier(
                        source,
                        tier,
                        str(hls_dir / tier.name),
                        total_duration=total_duration,
                        on_progress=_make_tier_progress(tier),
                    )
                    for tier in applicable
                ],
                return_exceptions=True,
            )

            for tier, result in zip(applicable, results):
                if isinstance(result, Exception):
                    logger.error(f"Tier {tier.name} raised exception: {result}")
                elif result is False:
                    logger.error(f"Tier {tier.name} failed")

            successful = [tier for tier, r in zip(applicable, results) if r is True]
            if not successful:
                logger.error(f"All tiers failed for version {version_id}")
                return None
            if on_progress:
                await on_progress(100, "Done")
            return successful
        else:
            # ── Serial tier encoding ──
            encode_weight = progress_range / num_tiers
            for i, tier in enumerate(applicable):
                bp = progress_base + int(i * encode_weight)

                async def tier_progress(
                    pct: float, _ew=encode_weight, _bp=bp, _tier=tier
                ):
                    overall = _bp + int(pct * _ew / 100)
                    if on_progress:
                        await on_progress(
                            min(overall, progress_cap), f"Encoding {_tier.name}"
                        )

                success = await self._transcode_tier(
                    source,
                    tier,
                    str(hls_dir / tier.name),
                    total_duration=total_duration,
                    on_progress=tier_progress,
                )
                if not success:
                    logger.error(
                        f"Failed to transcode tier {tier.name} for version {version_id}"
                    )
                    return None
            return applicable

    # ------------------------------------------------------------------ #
    # ffprobe
    # ------------------------------------------------------------------ #

    async def _probe_resolution(
        self, filepath: str
    ) -> Tuple[Optional[int], Optional[int]]:
        """Probe video file for width and height."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_streams",
                "-select_streams",
                "v:0",
                filepath,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **safe_popen_kwargs(),
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                return None, None

            info = json.loads(stdout)
            for stream in info.get("streams", []):
                w = stream.get("width")
                h = stream.get("height")
                if w and h:
                    return int(w), int(h)
            return None, None
        except Exception as e:
            logger.warning(f"ffprobe failed for {filepath}: {e}")
            return None, None

    async def _probe_duration(self, filepath: str) -> Optional[float]:
        """Probe video file for duration in seconds."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                filepath,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **safe_popen_kwargs(),
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                return None
            info = json.loads(stdout)
            dur = info.get("format", {}).get("duration")
            return float(dur) if dur else None
        except Exception as e:
            logger.warning(f"ffprobe duration failed for {filepath}: {e}")
            return None

    async def _probe_codecs(self, filepath: str) -> tuple[Optional[str], Optional[str]]:
        """Probe video and audio codec names. Returns (video_codec, audio_codec)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_streams",
                filepath,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **safe_popen_kwargs(),
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                return None, None
            info = json.loads(stdout)
            video_codec, audio_codec = None, None
            for stream in info.get("streams", []):
                ct = stream.get("codec_type")
                if ct == "video" and not video_codec:
                    video_codec = stream.get("codec_name")
                elif ct == "audio" and not audio_codec:
                    audio_codec = stream.get("codec_name")
            return video_codec, audio_codec
        except Exception as e:
            logger.warning(f"ffprobe codec detection failed for {filepath}: {e}")
            return None, None

    async def _probe_bitrate(self, filepath: str) -> Optional[int]:
        """Probe video file for overall bitrate (bps)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                filepath,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **safe_popen_kwargs(),
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                return None
            info = json.loads(stdout)
            br = info.get("format", {}).get("bit_rate")
            return int(br) if br else None
        except Exception as e:
            logger.warning(f"ffprobe bitrate failed for {filepath}: {e}")
            return None

    # ------------------------------------------------------------------ #
    # Tier selection
    # ------------------------------------------------------------------ #

    async def _select_tiers(self, width: int, height: int) -> List[TranscodeTier]:
        """Select tiers at or below the source resolution, filtered by admin config.

        Reads from system_settings DB table (not in-memory settings) so Celery
        workers pick up admin changes without restart. §2.4b: async — awaits the
        (now async) ``_get_db_setting`` on the caller's loop.
        """
        tiers_csv = (
            await self._get_db_setting("transcode_tiers")
        ) or settings.TRANSCODE_TIERS
        enabled_names = {t.strip().lower() for t in tiers_csv.split(",") if t.strip()}
        applicable = []
        for tier in TIERS:
            if tier.height <= height and tier.name.lower() in enabled_names:
                applicable.append(tier)
        return applicable

    @staticmethod
    async def _get_db_setting(key: str) -> Optional[str]:
        """Read a single value from system_settings.

        Direct PG via the SQLAlchemy engine (Issue #199). §2.4b: async-native
        — awaited from the async ``transcode_version`` / ``_select_tiers`` on
        their own event loop, instead of bridging ``db_engine.fetch_val``
        through a fresh-loop ``run_async`` shim (ORM-incompatible: asyncpg
        connections are event-loop-bound).
        """
        try:
            from app.db import engine as db_engine

            if not db_engine.is_configured():
                return None
            return await db_engine.fetch_val(
                "SELECT value FROM public.system_settings WHERE key = :k",
                {"k": key},
            )
        except Exception as e:
            logger.warning(f"[Transcode] Failed to read system_settings.{key}: {e}")
        return None

    # ------------------------------------------------------------------ #
    # ffmpeg transcoding
    # ------------------------------------------------------------------ #

    _TIME_RE = re.compile(r"time=(\d{2}):(\d{2}):(\d{2})\.(\d{2})")

    async def _transcode_tier(
        self,
        source: str,
        tier: TranscodeTier,
        output_dir: str,
        total_duration: Optional[float] = None,
        on_progress: Optional[Callable] = None,
    ) -> bool:
        """Transcode source video to a single HLS tier with progress tracking."""
        encoder, hwaccel_args, preset = await self._detect_encoder()
        success = await self._run_ffmpeg_tier(
            source,
            tier,
            output_dir,
            encoder,
            hwaccel_args,
            preset,
            total_duration=total_duration,
            on_progress=on_progress,
        )

        # GPU fallback: if GPU encoder failed, retry with libx264
        if not success and encoder != "libx264":
            logger.warning(
                f"[Transcode] GPU encoder {encoder} failed for {tier.name}, "
                f"falling back to libx264"
            )
            success = await self._run_ffmpeg_tier(
                source,
                tier,
                output_dir,
                "libx264",
                [],
                "medium",
                total_duration=total_duration,
                on_progress=on_progress,
            )

        return success

    async def _run_ffmpeg_tier(
        self,
        source: str,
        tier: TranscodeTier,
        output_dir: str,
        encoder: str,
        hwaccel_args: List[str],
        preset: str,
        total_duration: Optional[float] = None,
        on_progress: Optional[Callable] = None,
    ) -> bool:
        """Run ffmpeg for a single HLS tier with the given encoder settings."""
        segment_path = f"{output_dir}/segment_%03d.ts"
        playlist_path = f"{output_dir}/stream.m3u8"

        cmd = ["ffmpeg", "-y"]

        # GPU hwaccel input flags (before -i)
        if hwaccel_args:
            cmd.extend(hwaccel_args)

        scale_filter = (
            f"scale={tier.width}:{tier.height}"
            f":force_original_aspect_ratio=decrease,"
            f"pad={tier.width}:{tier.height}:(ow-iw)/2:(oh-ih)/2"
        )

        cmd.extend(
            [
                "-i",
                source,
                "-vf",
                scale_filter,
                "-c:v",
                encoder,
                "-preset",
                preset,
                "-b:v",
                f"{tier.bitrate}k",
                "-maxrate",
                f"{int(tier.bitrate * 1.2)}k",
                "-bufsize",
                f"{tier.bitrate * 2}k",
                "-c:a",
                "aac",
                "-b:a",
                f"{tier.audio_bitrate}k",
                "-ac",
                "2",
                "-ar",
                "44100",
                "-f",
                "hls",
                "-hls_time",
                "6",
                "-hls_list_size",
                "0",
                "-hls_segment_filename",
                segment_path,
                "-hls_playlist_type",
                "vod",
                playlist_path,
            ]
        )

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **safe_popen_kwargs(),
            )

            # Read stderr incrementally to parse ffmpeg progress
            stderr_tail = b""
            while True:
                chunk = await proc.stderr.read(512)
                if not chunk:
                    break
                stderr_tail = (stderr_tail + chunk)[
                    -2048:
                ]  # keep last 2KB for error msg

                if on_progress and total_duration and total_duration > 0:
                    # Parse time= from ffmpeg output
                    text = chunk.decode(errors="ignore")
                    match = self._TIME_RE.search(text)
                    if match:
                        h, m, s, cs = (int(x) for x in match.groups())
                        current_sec = h * 3600 + m * 60 + s + cs / 100
                        pct = min(current_sec / total_duration * 100, 99.0)
                        await on_progress(pct)

            await proc.wait()

            if proc.returncode != 0:
                logger.error(
                    f"ffmpeg [{encoder}] failed for tier {tier.name}: "
                    f"{stderr_tail.decode(errors='ignore')[-500:]}"
                )
                return False

            logger.info(f"Tier {tier.name} transcoded [{encoder}] → {playlist_path}")
            return True
        except Exception as e:
            logger.error(
                f"ffmpeg [{encoder}] execution error for tier {tier.name}: {e}"
            )
            return False

    # ------------------------------------------------------------------ #
    # Passthrough (original quality, no re-encoding)
    # ------------------------------------------------------------------ #

    async def _transcode_passthrough(
        self,
        source: str,
        hls_dir: Path,
        audio_codec: Optional[str] = None,
    ) -> bool:
        """Remux source into HLS segments without re-encoding (preserves original quality).

        If source audio is AAC, copy it directly (zero encoding). Otherwise transcode to AAC.
        """
        out_dir = hls_dir / "source"
        out_dir.mkdir(parents=True, exist_ok=True)
        segment_path = f"{out_dir}/segment_%03d.ts"
        playlist_path = f"{out_dir}/stream.m3u8"

        audio_args = (
            ["-c:a", "copy"]
            if audio_codec == "aac"
            else ["-c:a", "aac", "-b:a", "192k"]
        )

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            source,
            "-c:v",
            "copy",
            *audio_args,
            "-f",
            "hls",
            "-hls_time",
            "6",
            "-hls_list_size",
            "0",
            "-hls_segment_filename",
            segment_path,
            "-hls_playlist_type",
            "vod",
            playlist_path,
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **safe_popen_kwargs(),
            )
            _, stderr = await proc.communicate()

            if proc.returncode != 0:
                # Passthrough failed (e.g. non-H.264 source) — not critical, skip it
                logger.warning(
                    f"Passthrough remux failed (codec incompatible?): {stderr.decode()[-300:]}"
                )
                shutil.rmtree(out_dir, ignore_errors=True)
                return False

            logger.info(f"Passthrough tier (Original) created → {playlist_path}")
            return True
        except Exception as e:
            logger.warning(f"Passthrough execution error: {e}")
            shutil.rmtree(out_dir, ignore_errors=True)
            return False

    # ------------------------------------------------------------------ #
    # Master playlist
    # ------------------------------------------------------------------ #

    def _write_master_playlist(
        self,
        hls_dir: Path,
        tiers: List[TranscodeTier],
        *,
        passthrough: bool = False,
        source_width: Optional[int] = None,
        source_height: Optional[int] = None,
        source_bitrate: Optional[int] = None,
    ) -> None:
        """Write the multi-bitrate master.m3u8 playlist."""
        lines = ["#EXTM3U"]
        for tier in tiers:
            bandwidth = tier.bitrate * 1000  # kbps → bps
            lines.append(
                f"#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},"
                f"RESOLUTION={tier.width}x{tier.height},"
                f'NAME="{tier.name}"'
            )
            lines.append(f"{tier.name}/stream.m3u8")

        # Passthrough tier — original quality, highest bandwidth
        if passthrough and source_width and source_height:
            # Use probed bitrate or a generous fallback
            bw = source_bitrate if source_bitrate else 20_000_000
            lines.append(
                f"#EXT-X-STREAM-INF:BANDWIDTH={bw},"
                f"RESOLUTION={source_width}x{source_height},"
                f'NAME="Original"'
            )
            lines.append("source/stream.m3u8")

        master = hls_dir / "master.m3u8"
        # Atomic write: write to temp file then rename to prevent race with active readers
        tmp = master.with_suffix(".m3u8.tmp")
        tmp.write_text("\n".join(lines) + "\n")
        tmp.rename(master)
        logger.info(f"Master playlist written: {master}")
