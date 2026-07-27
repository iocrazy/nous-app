# app/services/media/transcode/transcode_probe.py

"""ffprobe / 编码器探测。从 TranscodeService 原样搬出。

拆分理由:这七个方法全部无状态、只读,与转码编排无耦合,却占了
transcode_service.py 相当篇幅。搬出后编排层只留真正的流程控制。
"""

from __future__ import annotations

import asyncio
import json
from typing import List, Optional, Tuple

from loguru import logger

from app.agent_framework.process_lifecycle import safe_popen_kwargs
from app.core.config import settings


class TranscodeProbe:
    """ffprobe 包装 + 编码器能力探测。无状态（除惰性缓存的编码器探测结果）。"""

    # GPU encoder priority order: (codec_name, hwaccel_input_args)
    _GPU_ENCODERS = [
        ("h264_nvenc", ["-hwaccel", "cuda"]),  # NVIDIA
        ("h264_videotoolbox", []),  # macOS
        ("h264_qsv", ["-hwaccel", "qsv"]),  # Intel
    ]

    def __init__(self):
        self._encoder: Optional[str] = None  # lazy-init
        self._hwaccel_args: List[str] = []
        self._preset: str = settings.FFMPEG_PRESET

    # ------------------------------------------------------------------ #
    # GPU encoder detection
    # ------------------------------------------------------------------ #

    async def detect_encoder(self) -> tuple[str, list[str], str]:
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
            if await self.probe_encoder(name):
                logger.info(f"[Transcode] GPU encoder detected: {name}")
                self._encoder = name
                self._hwaccel_args = hwaccel_args
                if "nvenc" in name:
                    self._preset = self.map_nvenc_preset(settings.FFMPEG_PRESET)
                return self._encoder, self._hwaccel_args, self._preset

        # Fallback to CPU
        logger.info("[Transcode] No GPU encoder found, using libx264 (CPU)")
        self._encoder = "libx264"
        return self._encoder, self._hwaccel_args, self._preset

    @staticmethod
    async def probe_encoder(encoder_name: str) -> bool:
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
    def map_nvenc_preset(cpu_preset: str) -> str:
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
    # ffprobe
    # ------------------------------------------------------------------ #

    async def probe_resolution(
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

    async def probe_duration(self, filepath: str) -> Optional[float]:
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

    async def probe_codecs(self, filepath: str) -> tuple[Optional[str], Optional[str]]:
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

    async def probe_bitrate(self, filepath: str) -> Optional[int]:
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
