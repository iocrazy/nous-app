# app/services/thumbnail_service.py

"""
Thumbnail & Preview Sprite Service

Generates thumbnails and hover-scrub preview sprites for uploaded resources.
All generated files are saved to the same directory as the source file on disk.

- Video thumbnail: Extract frame at 1s via ffmpeg, scale to 320px width
- Video sprite: Extract N frames evenly, combine into horizontal strip
- Image thumbnail: Resize to max 320px via Pillow
- Audio/Document: Skip (return None)

Storage layout (same dir as source file):
  uploads/{resource_id}/
    ├── original_file.mp4
    ├── thumbnail.jpg        ← single frame thumbnail
    └── preview_sprite.jpg   ← horizontal sprite strip for hover scrub
"""

import asyncio
from pathlib import Path
from typing import Optional

from loguru import logger
from PIL import Image

from app.core.config import settings
from app.repositories.resources_repository import ResourcesRepository

THUMBNAIL_MAX_WIDTH = 320
THUMBNAIL_QUALITY = 85
SPRITE_FRAME_WIDTH = 200
SPRITE_FRAME_COUNT = 10
SPRITE_QUALITY = 75


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

            # Target: save thumbnail.jpg in the same directory as source file
            thumb_abs = abs_path.parent / "thumbnail.jpg"

            if mime_type.startswith("video/"):
                ok = await self._generate_video_thumbnail(str(abs_path), str(thumb_abs))
                # Also generate preview sprite for hover scrub
                sprite_abs = abs_path.parent / "preview_sprite.jpg"
                await self._generate_video_sprite(str(abs_path), str(sprite_abs))
            elif mime_type.startswith("image/"):
                ok = await self._generate_image_thumbnail(str(abs_path), str(thumb_abs))
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
            logger.error(
                f"Thumbnail generation failed for resource {resource_id}: {e}"
            )
            return None

    # ------------------------------------------------------------------ #
    # Video thumbnail: extract single frame with ffmpeg
    # ------------------------------------------------------------------ #

    async def _generate_video_thumbnail(
        self, src_path: str, dst_path: str
    ) -> bool:
        """Extract a frame at 1s, scaled to 320px width. Returns True on success."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y",
                "-ss", "1",
                "-i", src_path,
                "-vframes", "1",
                "-vf", f"scale={THUMBNAIL_MAX_WIDTH}:-1",
                "-q:v", "3",
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

    async def _generate_video_sprite(
        self, src_path: str, dst_path: str
    ) -> bool:
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
                    "ffmpeg", "-y",
                    "-ss", f"{ts:.2f}",
                    "-i", src_path,
                    "-vframes", "1",
                    "-vf", f"scale={SPRITE_FRAME_WIDTH}:-1",
                    "-q:v", "4",
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
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
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

    def _combine_sprite_frames(
        self, frame_paths: list[Path], dst_path: str
    ) -> bool:
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

    async def _generate_image_thumbnail(
        self, src_path: str, dst_path: str
    ) -> bool:
        """Resize an image to max 320px width. Returns True on success."""
        try:
            return await asyncio.to_thread(self._resize_image, src_path, dst_path)
        except Exception as e:
            logger.warning(f"Image thumbnail failed for {src_path}: {e}")
            return False

    def _resize_image(self, src_path: str, dst_path: str) -> bool:
        """Synchronous image resize using Pillow."""
        try:
            with Image.open(src_path) as img:
                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")

                if img.width > THUMBNAIL_MAX_WIDTH:
                    ratio = THUMBNAIL_MAX_WIDTH / img.width
                    new_height = int(img.height * ratio)
                    img = img.resize(
                        (THUMBNAIL_MAX_WIDTH, new_height),
                        Image.LANCZOS,
                    )

                img.save(dst_path, format="JPEG", quality=THUMBNAIL_QUALITY)
                return True

        except Exception as e:
            logger.warning(f"Pillow resize failed for {src_path}: {e}")
            return False
