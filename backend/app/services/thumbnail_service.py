# app/services/thumbnail_service.py

"""
Thumbnail Service

Generates thumbnails for uploaded resources:
- Video: Extract frame at 1 second via ffmpeg, scale to 320px width
- Image: Resize to max 320px via Pillow
- Audio/Document: Skip (return None)

Uploads generated thumbnails to Supabase Storage bucket "thumbnails"
and returns the public URL.
"""

import asyncio
import tempfile
from pathlib import Path
from typing import Optional

from loguru import logger
from PIL import Image

from app.core.config import settings
from app.db.supabase_client import get_async_supabase_admin
from app.repositories.resources_repository import ResourcesRepository

THUMBNAIL_MAX_WIDTH = 320
THUMBNAIL_QUALITY = 85


class ThumbnailService:
    """Generate and upload thumbnails for resources."""

    def __init__(self):
        self.repo = ResourcesRepository()

    async def generate_thumbnail(
        self,
        resource_id: str,
        file_path: str,
        mime_type: str,
        scope_type: str,
        scope_id: str,
    ) -> Optional[str]:
        """
        Generate a thumbnail and upload to Supabase Storage.

        Args:
            resource_id: UUID of the resource.
            file_path: Relative file path (e.g. "resources/{id}/{name}").
            mime_type: MIME type of the original file.
            scope_type: "personal" or "team".
            scope_id: User ID or team ID.

        Returns:
            Public URL of the uploaded thumbnail, or None on failure/skip.
        """
        try:
            # Resolve absolute path from relative path
            abs_path = Path(settings.DOWNLOAD_PATH) / file_path
            if not abs_path.exists():
                logger.warning(
                    f"Thumbnail skipped: source file not found at {abs_path}"
                )
                return None

            # Determine file type from MIME
            if mime_type.startswith("video/"):
                thumbnail_bytes = await self._generate_video_thumbnail(str(abs_path))
            elif mime_type.startswith("image/"):
                thumbnail_bytes = await self._generate_image_thumbnail(str(abs_path))
            else:
                # Audio and document types: no thumbnail
                logger.debug(
                    f"Thumbnail skipped for resource {resource_id}: "
                    f"unsupported type {mime_type}"
                )
                return None

            if not thumbnail_bytes:
                return None

            # Upload to Supabase Storage
            storage_path = (
                f"{scope_type}/{scope_id}/{resource_id}.jpg"
            )
            public_url = await self._upload_to_storage(
                storage_path, thumbnail_bytes
            )

            if public_url:
                # Update resource record with thumbnail path
                await self.repo.update_resource(
                    resource_id, {"thumbnail_path": public_url}
                )
                logger.info(
                    f"Thumbnail generated for resource {resource_id}: {public_url}"
                )

            return public_url

        except Exception as e:
            logger.error(
                f"Thumbnail generation failed for resource {resource_id}: {e}"
            )
            return None

    # ------------------------------------------------------------------ #
    # Video thumbnail: extract frame with ffmpeg
    # ------------------------------------------------------------------ #

    async def _generate_video_thumbnail(self, filepath: str) -> Optional[bytes]:
        """
        Extract a frame at 1 second from the video using ffmpeg,
        scaled to 320px width while maintaining aspect ratio.

        Returns JPEG bytes or None on failure.
        """
        try:
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                tmp_path = tmp.name

            proc = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-y",
                "-ss", "1",
                "-i", filepath,
                "-vframes", "1",
                "-vf", f"scale={THUMBNAIL_MAX_WIDTH}:-1",
                "-q:v", "3",
                tmp_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()

            if proc.returncode != 0:
                logger.warning(
                    f"ffmpeg thumbnail extraction failed for {filepath}: "
                    f"{stderr.decode(errors='replace')[:500]}"
                )
                # Clean up temp file
                Path(tmp_path).unlink(missing_ok=True)
                return None

            # Read the generated thumbnail
            tmp_file = Path(tmp_path)
            if not tmp_file.exists() or tmp_file.stat().st_size == 0:
                logger.warning(
                    f"ffmpeg produced empty thumbnail for {filepath}"
                )
                tmp_file.unlink(missing_ok=True)
                return None

            thumbnail_bytes = tmp_file.read_bytes()
            tmp_file.unlink(missing_ok=True)
            return thumbnail_bytes

        except Exception as e:
            logger.warning(f"Video thumbnail generation failed for {filepath}: {e}")
            return None

    # ------------------------------------------------------------------ #
    # Image thumbnail: resize with Pillow
    # ------------------------------------------------------------------ #

    async def _generate_image_thumbnail(self, filepath: str) -> Optional[bytes]:
        """
        Resize an image to max 320px width using Pillow.

        Returns JPEG bytes or None on failure.
        """
        try:
            # Run Pillow operations in a thread to avoid blocking the event loop
            return await asyncio.to_thread(self._resize_image, filepath)
        except Exception as e:
            logger.warning(f"Image thumbnail generation failed for {filepath}: {e}")
            return None

    def _resize_image(self, filepath: str) -> Optional[bytes]:
        """Synchronous image resize using Pillow."""
        try:
            with Image.open(filepath) as img:
                # Convert to RGB if necessary (handles RGBA, P, etc.)
                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")

                # Only resize if wider than max width
                if img.width > THUMBNAIL_MAX_WIDTH:
                    ratio = THUMBNAIL_MAX_WIDTH / img.width
                    new_height = int(img.height * ratio)
                    img = img.resize(
                        (THUMBNAIL_MAX_WIDTH, new_height),
                        Image.LANCZOS,
                    )

                # Save to bytes
                with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                    tmp_path = tmp.name

                img.save(tmp_path, format="JPEG", quality=THUMBNAIL_QUALITY)
                result = Path(tmp_path).read_bytes()
                Path(tmp_path).unlink(missing_ok=True)
                return result

        except Exception as e:
            logger.warning(f"Pillow resize failed for {filepath}: {e}")
            return None

    # ------------------------------------------------------------------ #
    # Upload to Supabase Storage
    # ------------------------------------------------------------------ #

    async def _upload_to_storage(
        self, storage_path: str, data: bytes
    ) -> Optional[str]:
        """
        Upload thumbnail bytes to the 'thumbnails' bucket in Supabase Storage.

        Args:
            storage_path: Path within the bucket (e.g. "personal/{id}/{resource_id}.jpg").
            data: JPEG bytes to upload.

        Returns:
            Public URL of the uploaded file, or None on failure.
        """
        try:
            client = await get_async_supabase_admin()
            bucket = client.storage.from_("thumbnails")

            # Upload (upsert to handle re-generation)
            await bucket.upload(
                path=storage_path,
                file=data,
                file_options={
                    "content-type": "image/jpeg",
                    "upsert": "true",
                },
            )

            # Get public URL
            public_url = bucket.get_public_url(storage_path)
            return public_url

        except Exception as e:
            logger.error(f"Failed to upload thumbnail to storage: {e}")
            return None
