# app/services/transcode_service.py

"""
HLS Transcode Service

Multi-bitrate HLS transcoding using ffmpeg. Generates adaptive streaming
playlists (master.m3u8) with quality tiers: 480p, 720p, 1080p.
Skips tiers above the original video resolution.
"""

import asyncio
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from loguru import logger

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
    TranscodeTier(name="480p", width=854, height=480, bitrate=800, audio_bitrate=96),
    TranscodeTier(name="720p", width=1280, height=720, bitrate=2500, audio_bitrate=128),
    TranscodeTier(name="1080p", width=1920, height=1080, bitrate=5000, audio_bitrate=192),
]


class TranscodeService:
    """HLS multi-bitrate transcoding service."""

    def __init__(self):
        self.repo = ResourcesRepository()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    async def transcode_version(
        self,
        resource_id: str,
        version_id: str,
    ) -> Optional[str]:
        """
        Transcode a resource version to HLS multi-bitrate.

        Returns the relative hls_path (e.g. "teams/.../v1/hls/master.m3u8")
        or None on failure.
        """
        version = await self.repo.get_version_by_id(version_id)
        if not version:
            logger.error(f"Version {version_id} not found")
            return None

        file_path = version.get("file_path")
        if not file_path:
            logger.error(f"Version {version_id} has no file_path")
            return None

        # Mark as processing
        await self.repo.update_version(version_id, {"transcode_status": "processing"})

        base = Path(settings.DOWNLOAD_PATH)
        source = base / file_path

        if not source.exists():
            logger.error(f"Source file not found: {source}")
            await self.repo.update_version(version_id, {"transcode_status": "failed"})
            return None

        # Determine HLS output directory: sibling hls/ folder next to the source file
        hls_dir = source.parent / "hls"

        try:
            # Probe video to get resolution
            width, height = await self._probe_resolution(str(source))
            if not width or not height:
                logger.warning(f"Could not probe resolution for {source}, skipping transcode")
                await self.repo.update_version(version_id, {"transcode_status": "failed"})
                return None

            # Select applicable tiers
            applicable = self._select_tiers(width, height)
            if not applicable:
                logger.info(f"No applicable tiers for {width}x{height}, skipping")
                await self.repo.update_version(version_id, {"transcode_status": "failed"})
                return None

            # Clean up old HLS if exists
            if hls_dir.exists():
                shutil.rmtree(hls_dir)
            hls_dir.mkdir(parents=True, exist_ok=True)

            # Transcode each tier
            for tier in applicable:
                tier_dir = hls_dir / tier.name
                tier_dir.mkdir(parents=True, exist_ok=True)
                success = await self._transcode_tier(str(source), tier, str(tier_dir))
                if not success:
                    logger.error(f"Failed to transcode tier {tier.name} for version {version_id}")
                    await self.repo.update_version(version_id, {"transcode_status": "failed"})
                    return None

            # Generate master playlist
            self._write_master_playlist(hls_dir, applicable)

            # Build relative path
            master_path = hls_dir / "master.m3u8"
            relative_hls = str(master_path.relative_to(base))

            # Update DB
            from datetime import datetime, timezone
            await self.repo.update_version(version_id, {
                "hls_path": relative_hls,
                "transcode_status": "completed",
                "transcode_at": datetime.now(timezone.utc).isoformat(),
            })

            logger.success(
                f"Transcode completed: resource={resource_id}, version={version_id}, "
                f"tiers={[t.name for t in applicable]}"
            )
            return relative_hls

        except Exception as e:
            logger.error(f"Transcode failed for version {version_id}: {e}")
            await self.repo.update_version(version_id, {"transcode_status": "failed"})
            return None

    # ------------------------------------------------------------------ #
    # ffprobe
    # ------------------------------------------------------------------ #

    async def _probe_resolution(self, filepath: str) -> Tuple[Optional[int], Optional[int]]:
        """Probe video file for width and height."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_streams",
                "-select_streams", "v:0",
                filepath,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
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

    # ------------------------------------------------------------------ #
    # Tier selection
    # ------------------------------------------------------------------ #

    def _select_tiers(self, width: int, height: int) -> List[TranscodeTier]:
        """Select tiers at or below the source resolution."""
        applicable = []
        for tier in TIERS:
            if tier.height <= height:
                applicable.append(tier)
        return applicable

    # ------------------------------------------------------------------ #
    # ffmpeg transcoding
    # ------------------------------------------------------------------ #

    async def _transcode_tier(
        self, source: str, tier: TranscodeTier, output_dir: str
    ) -> bool:
        """Transcode source video to a single HLS tier."""
        segment_path = f"{output_dir}/segment_%03d.ts"
        playlist_path = f"{output_dir}/stream.m3u8"

        cmd = [
            "ffmpeg",
            "-y",
            "-i", source,
            "-vf", f"scale={tier.width}:{tier.height}:force_original_aspect_ratio=decrease,pad={tier.width}:{tier.height}:(ow-iw)/2:(oh-ih)/2",
            "-c:v", "libx264",
            "-preset", "medium",
            "-b:v", f"{tier.bitrate}k",
            "-maxrate", f"{int(tier.bitrate * 1.2)}k",
            "-bufsize", f"{tier.bitrate * 2}k",
            "-c:a", "aac",
            "-b:a", f"{tier.audio_bitrate}k",
            "-ac", "2",
            "-ar", "44100",
            "-f", "hls",
            "-hls_time", "6",
            "-hls_list_size", "0",
            "-hls_segment_filename", segment_path,
            "-hls_playlist_type", "vod",
            playlist_path,
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()

            if proc.returncode != 0:
                logger.error(
                    f"ffmpeg failed for tier {tier.name}: {stderr.decode()[-500:]}"
                )
                return False

            logger.info(f"Tier {tier.name} transcoded successfully → {playlist_path}")
            return True
        except Exception as e:
            logger.error(f"ffmpeg execution error for tier {tier.name}: {e}")
            return False

    # ------------------------------------------------------------------ #
    # Master playlist
    # ------------------------------------------------------------------ #

    def _write_master_playlist(
        self, hls_dir: Path, tiers: List[TranscodeTier]
    ) -> None:
        """Write the multi-bitrate master.m3u8 playlist."""
        lines = ["#EXTM3U"]
        for tier in tiers:
            bandwidth = tier.bitrate * 1000  # kbps → bps
            lines.append(
                f"#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},"
                f"RESOLUTION={tier.width}x{tier.height},"
                f"NAME=\"{tier.name}\""
            )
            lines.append(f"{tier.name}/stream.m3u8")

        master = hls_dir / "master.m3u8"
        master.write_text("\n".join(lines) + "\n")
        logger.info(f"Master playlist written: {master}")
