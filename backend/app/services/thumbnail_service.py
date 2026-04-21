# app/services/thumbnail_service.py

"""
Thumbnail & Preview Sprite Service

Generates thumbnails and hover-scrub preview sprites for uploaded resources.
All generated files are saved to the same directory as the source file on disk.

- Video thumbnail: Extract frame at 1s via ffmpeg, scale to 320px width
- Video sprite: Extract N frames evenly, combine into horizontal strip
- Image thumbnail: Resize to max 320px via Pillow
- Audio thumbnail: Generate waveform visualization via ffmpeg + Pillow
- Document: Skip (return None)

Storage layout (same dir as source file):
  uploads/{resource_id}/
    ├── original_file.mp4
    ├── thumbnail.jpg        ← single frame thumbnail
    └── preview_sprite.jpg   ← horizontal sprite strip for hover scrub
"""

import asyncio
import struct
from pathlib import Path
from typing import Optional

from loguru import logger
from PIL import Image, ImageDraw

from app.core.config import settings
from app.repositories.resources_repository import ResourcesRepository

THUMBNAIL_MAX_WIDTH = 320
THUMBNAIL_QUALITY = 85
SPRITE_FRAME_WIDTH = 200
SPRITE_FRAME_COUNT = 10
SPRITE_QUALITY = 75

# Audio waveform settings
WAVEFORM_WIDTH = 320
WAVEFORM_HEIGHT = 180
WAVEFORM_BAR_COUNT = 160
WAVEFORM_BG_COLOR = (15, 15, 23)  # near-black
WAVEFORM_BAR_COLOR = (99, 102, 241)  # indigo-500
WAVEFORM_BAR_BRIGHT = (139, 142, 255)  # lighter bar center


class ThumbnailService:
    """Generate thumbnails and preview sprites, saved to local disk."""

    def __init__(self):
        self.repo = ResourcesRepository()

    async def generate_thumbnail(
        self,
        resource_id: str,
        file_path: str,
        mime_type: str,
        scope_type: str = "",
        scope_id: str = "",
    ) -> Optional[str]:
        """
        Generate a thumbnail and save it next to the source file.

        Args:
            resource_id: ID of the resource.
            file_path: Relative file path (e.g. "teams/{uid}/uploads/{id}/name.mp4").
            mime_type: MIME type of the original file.
            scope_type: Unused (kept for API compat).
            scope_id: Unused (kept for API compat).

        Returns:
            Relative path to the saved thumbnail, or None on failure/skip.
        """
        try:
            abs_path = Path(settings.DOWNLOAD_PATH) / file_path
            if not abs_path.exists():
                logger.warning(
                    f"Thumbnail skipped: source file not found at {abs_path}"
                )
                return None

            # Target: save thumbnail.webp in the same directory as source file
            # (webp ≈ 30-50% smaller than jpeg at equivalent quality)
            thumb_abs = abs_path.parent / "thumbnail.webp"

            if mime_type.startswith("video/"):
                ok = await self._generate_video_thumbnail(str(abs_path), str(thumb_abs))
                # Also generate preview sprite for hover scrub
                sprite_abs = abs_path.parent / "preview_sprite.jpg"
                await self._generate_video_sprite(str(abs_path), str(sprite_abs))
            elif mime_type.startswith("image/"):
                ok = await self._generate_image_thumbnail(str(abs_path), str(thumb_abs))
            elif mime_type.startswith("audio/"):
                # Save as PNG for waveform (better for sharp lines)
                thumb_abs = abs_path.parent / "thumbnail.png"
                ok = await self._generate_audio_thumbnail(str(abs_path), str(thumb_abs))
            else:
                logger.debug(
                    f"Thumbnail skipped for resource {resource_id}: "
                    f"unsupported type {mime_type}"
                )
                return None

            if not ok:
                return None

            # Store relative path (same pattern as file_path)
            thumb_relative = str(thumb_abs.relative_to(Path(settings.DOWNLOAD_PATH)))

            await self.repo.update_resource(
                resource_id, {"thumbnail_path": thumb_relative}
            )
            logger.info(
                f"Thumbnail generated for resource {resource_id}: {thumb_relative}"
            )
            return thumb_relative

        except Exception as e:
            logger.error(f"Thumbnail generation failed for resource {resource_id}: {e}")
            return None

    # ------------------------------------------------------------------ #
    # Video thumbnail: extract single frame with ffmpeg
    # ------------------------------------------------------------------ #

    async def _generate_video_thumbnail(self, src_path: str, dst_path: str) -> bool:
        """Extract a frame at 1s, scaled to 320px width. Returns True on success."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-y",
                "-ss",
                "1",
                "-i",
                src_path,
                "-vframes",
                "1",
                "-vf",
                f"scale={THUMBNAIL_MAX_WIDTH}:-1",
                "-c:v",
                "libwebp",
                "-quality",
                "80",
                "-preset",
                "photo",
                dst_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()

            if proc.returncode != 0:
                logger.warning(
                    f"ffmpeg thumbnail failed for {src_path}: "
                    f"{stderr.decode(errors='replace')[:500]}"
                )
                return False

            out = Path(dst_path)
            if not out.exists() or out.stat().st_size == 0:
                logger.warning(f"ffmpeg produced empty thumbnail for {src_path}")
                out.unlink(missing_ok=True)
                return False

            return True

        except Exception as e:
            logger.warning(f"Video thumbnail failed for {src_path}: {e}")
            return False

    # ------------------------------------------------------------------ #
    # Video sprite: extract N frames into horizontal strip
    # ------------------------------------------------------------------ #

    async def _generate_video_sprite(self, src_path: str, dst_path: str) -> bool:
        """
        Generate a horizontal sprite sheet of N frames for hover scrub.
        Frames are evenly distributed across the video duration.
        """
        try:
            # Get video duration
            duration = await self._get_video_duration(src_path)
            if not duration or duration < 2:
                logger.debug(f"Video too short for sprite: {src_path} ({duration}s)")
                return False

            # Calculate timestamps for N frames (skip first/last 0.5s)
            start = 0.5
            end = duration - 0.5
            interval = (end - start) / (SPRITE_FRAME_COUNT - 1)
            timestamps = [start + i * interval for i in range(SPRITE_FRAME_COUNT)]

            # Extract frames to temp files
            frame_paths = []
            for i, ts in enumerate(timestamps):
                frame_path = Path(dst_path).parent / f"_sprite_frame_{i}.jpg"
                proc = await asyncio.create_subprocess_exec(
                    "ffmpeg",
                    "-y",
                    "-ss",
                    f"{ts:.2f}",
                    "-i",
                    src_path,
                    "-vframes",
                    "1",
                    "-vf",
                    f"scale={SPRITE_FRAME_WIDTH}:-1",
                    "-q:v",
                    "4",
                    str(frame_path),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.communicate()
                if frame_path.exists() and frame_path.stat().st_size > 0:
                    frame_paths.append(frame_path)

            if len(frame_paths) < 3:
                # Not enough frames, clean up
                for fp in frame_paths:
                    fp.unlink(missing_ok=True)
                return False

            # Combine frames into horizontal strip using Pillow (in thread)
            ok = await asyncio.to_thread(
                self._combine_sprite_frames, frame_paths, dst_path
            )

            # Clean up temp frame files
            for fp in frame_paths:
                fp.unlink(missing_ok=True)

            if ok:
                logger.info(
                    f"Preview sprite generated ({len(frame_paths)} frames): {dst_path}"
                )
            return ok

        except Exception as e:
            logger.warning(f"Sprite generation failed for {src_path}: {e}")
            return False

    async def _get_video_duration(self, filepath: str) -> Optional[float]:
        """Get video duration in seconds using ffprobe."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                filepath,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                return float(stdout.decode().strip())
            return None
        except Exception:
            return None

    def _combine_sprite_frames(self, frame_paths: list[Path], dst_path: str) -> bool:
        """Combine individual frame images into a horizontal strip."""
        try:
            images = [Image.open(fp) for fp in frame_paths]
            # Use first frame dimensions as reference
            w, h = images[0].size
            # Create horizontal strip
            sprite = Image.new("RGB", (w * len(images), h))
            for i, img in enumerate(images):
                # Resize if dimensions differ
                if img.size != (w, h):
                    img = img.resize((w, h), Image.LANCZOS)
                sprite.paste(img, (i * w, 0))
                img.close()

            sprite.save(dst_path, format="JPEG", quality=SPRITE_QUALITY)
            sprite.close()
            return True
        except Exception as e:
            logger.warning(f"Sprite combine failed: {e}")
            return False

    # ------------------------------------------------------------------ #
    # Image thumbnail: resize with Pillow
    # ------------------------------------------------------------------ #

    async def _generate_image_thumbnail(self, src_path: str, dst_path: str) -> bool:
        """Resize an image to max 320px width. Returns True on success."""
        try:
            return await asyncio.to_thread(self._resize_image, src_path, dst_path)
        except Exception as e:
            logger.warning(f"Image thumbnail failed for {src_path}: {e}")
            return False

    def _is_animated(self, img: Image.Image) -> bool:
        """Check if a PIL Image has multiple frames (animated GIF/APNG/WebP)."""
        try:
            return getattr(img, "is_animated", False) or img.n_frames > 1
        except Exception:
            return False

    def _resize_image(self, src_path: str, dst_path: str) -> bool:
        """Synchronous image resize using Pillow.

        Animated images (GIF/APNG/WebP) are skipped — the cover endpoint
        will fall back to serving the original file so animation is preserved.
        """
        try:
            with Image.open(src_path) as img:
                if self._is_animated(img):
                    logger.info(f"Skipping thumbnail for animated image: {src_path}")
                    return False

                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")

                if img.width > THUMBNAIL_MAX_WIDTH:
                    ratio = THUMBNAIL_MAX_WIDTH / img.width
                    new_height = int(img.height * ratio)
                    img = img.resize(
                        (THUMBNAIL_MAX_WIDTH, new_height),
                        Image.LANCZOS,
                    )

                img.save(dst_path, format="WEBP", quality=THUMBNAIL_QUALITY, method=4)
                return True

        except Exception as e:
            logger.warning(f"Pillow resize failed for {src_path}: {e}")
            return False

    # ------------------------------------------------------------------ #
    # Audio thumbnail: waveform visualization
    # ------------------------------------------------------------------ #

    async def _generate_audio_thumbnail(self, src_path: str, dst_path: str) -> bool:
        """Generate waveform visualization thumbnail for audio files."""
        try:
            # Extract raw PCM data: mono, 8kHz, 16-bit signed LE
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-y",
                "-i",
                src_path,
                "-ac",
                "1",
                "-ar",
                "8000",
                "-f",
                "s16le",
                "pipe:1",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0 or not stdout:
                logger.warning(
                    f"ffmpeg audio PCM extraction failed for {src_path}: "
                    f"{stderr.decode(errors='replace')[:300]}"
                )
                return False

            # Parse PCM samples
            sample_count = len(stdout) // 2
            if sample_count < 100:
                logger.warning(f"Audio too short for waveform: {src_path}")
                return False

            samples = struct.unpack(f"<{sample_count}h", stdout)

            # Draw waveform in thread (CPU-bound)
            ok = await asyncio.to_thread(self._draw_waveform, samples, dst_path)

            if ok:
                out = Path(dst_path)
                if not out.exists() or out.stat().st_size == 0:
                    return False

            return ok

        except Exception as e:
            logger.warning(f"Audio thumbnail failed for {src_path}: {e}")
            return False

    def _draw_waveform(self, samples: tuple, dst_path: str) -> bool:
        """Draw Eagle-style waveform visualization using Pillow."""
        try:
            W = WAVEFORM_WIDTH
            H = WAVEFORM_HEIGHT
            BAR_COUNT = WAVEFORM_BAR_COUNT

            img = Image.new("RGB", (W, H), WAVEFORM_BG_COLOR)
            draw = ImageDraw.Draw(img)

            # Downsample: compute RMS amplitude per chunk
            chunk_size = max(1, len(samples) // BAR_COUNT)
            amplitudes = []
            for i in range(BAR_COUNT):
                start = i * chunk_size
                end = min(start + chunk_size, len(samples))
                if start >= len(samples):
                    amplitudes.append(0.0)
                    continue
                chunk = samples[start:end]
                rms = (sum(s * s for s in chunk) / len(chunk)) ** 0.5
                amplitudes.append(rms)

            # Normalize to 0..1
            max_amp = max(amplitudes) if amplitudes else 1.0
            if max_amp < 1.0:
                max_amp = 1.0
            normalized = [a / max_amp for a in amplitudes]

            # Draw symmetric waveform bars
            center_y = H // 2
            max_bar_h = (H - 16) // 2  # leave top/bottom margin
            bar_w = max(1, W // BAR_COUNT)
            gap = (W - bar_w * BAR_COUNT) // 2  # center horizontally

            for i, amp in enumerate(normalized):
                x = gap + i * bar_w
                bar_h = max(1, int(amp * max_bar_h))

                # Color gradient: brighter at center, darker at tips
                if amp > 0.6:
                    color = WAVEFORM_BAR_BRIGHT
                elif amp > 0.3:
                    color = WAVEFORM_BAR_COLOR
                else:
                    # Dim bars for quiet parts
                    color = (
                        WAVEFORM_BAR_COLOR[0] * 2 // 3,
                        WAVEFORM_BAR_COLOR[1] * 2 // 3,
                        WAVEFORM_BAR_COLOR[2] * 2 // 3,
                    )

                # Draw upper half
                draw.rectangle(
                    [x, center_y - bar_h, x + bar_w - 1, center_y - 1],
                    fill=color,
                )
                # Draw lower half (mirror)
                draw.rectangle(
                    [x, center_y + 1, x + bar_w - 1, center_y + bar_h],
                    fill=color,
                )

            # Thin center line
            draw.line(
                [(gap, center_y), (gap + bar_w * BAR_COUNT - 1, center_y)],
                fill=(50, 50, 70),
                width=1,
            )

            img.save(dst_path, format="PNG")
            img.close()
            return True

        except Exception as e:
            logger.warning(f"Waveform draw failed: {e}")
            return False
