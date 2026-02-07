# backend/app/services/download_progress.py

"""
Download with progress tracking.
Streams file download and reports progress to Redis.
"""

import json
import os

import aiofiles
import httpx
from loguru import logger


class DownloadProgressTracker:
    """Track download progress and report to callback."""

    def __init__(self, task_id: str, redis_client, update_interval: float = 0.5):
        self.task_id = task_id
        self.redis_client = redis_client
        self.update_interval = update_interval
        self.last_update = 0
        self.downloaded = 0
        self.total = 0
        self.speed = 0
        self._last_downloaded = 0
        self._last_time = 0

    def _format_speed(self, bytes_per_sec: float) -> str:
        """Format speed as human readable string."""
        if bytes_per_sec < 1024:
            return f"{bytes_per_sec:.0f} B/s"
        elif bytes_per_sec < 1024 * 1024:
            return f"{bytes_per_sec / 1024:.1f} KB/s"
        else:
            return f"{bytes_per_sec / (1024 * 1024):.1f} MB/s"

    def update(self, downloaded: int, total: int):
        """Update progress and push to Redis."""
        import time

        self.downloaded = downloaded
        self.total = total

        current_time = time.time()

        # Calculate speed
        if self._last_time > 0:
            time_diff = current_time - self._last_time
            if time_diff > 0:
                bytes_diff = downloaded - self._last_downloaded
                self.speed = bytes_diff / time_diff

        # Only update Redis at intervals to reduce overhead
        if current_time - self.last_update >= self.update_interval:
            percent = int((downloaded / total) * 100) if total > 0 else 0

            progress_data = {
                "percent": percent,
                "downloaded": downloaded,
                "total": total,
                "speed": self._format_speed(self.speed),
                "status": "downloading",
            }

            self.redis_client.setex(
                f"download_progress:{self.task_id}",
                300,  # 5 min TTL
                json.dumps(progress_data),
            )

            self.last_update = current_time
            self._last_downloaded = downloaded
            self._last_time = current_time

    def complete(self):
        """Mark download as complete."""
        progress_data = {
            "percent": 100,
            "downloaded": self.total,
            "total": self.total,
            "speed": "0 B/s",
            "status": "completed",
        }
        self.redis_client.setex(
            f"download_progress:{self.task_id}",
            60,  # Keep for 1 min after complete
            json.dumps(progress_data),
        )

    def failed(self, error: str):
        """Mark download as failed."""
        progress_data = {
            "percent": self.downloaded / self.total * 100 if self.total > 0 else 0,
            "downloaded": self.downloaded,
            "total": self.total,
            "speed": "0 B/s",
            "status": "failed",
            "error": error,
        }
        self.redis_client.setex(
            f"download_progress:{self.task_id}", 300, json.dumps(progress_data)
        )


async def download_file_with_progress(
    url: str,
    file_path: str,
    tracker: DownloadProgressTracker,
    headers: dict = None,
    timeout: float = 300,
) -> bool:
    """
    Download file with progress tracking.

    Args:
        url: Download URL
        file_path: Save path
        tracker: Progress tracker instance
        headers: Request headers
        timeout: Download timeout

    Returns:
        bool: Success status
    """
    if headers is None:
        from app.core.utils import Utils

        headers = Utils.get_headers()

    try:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        if os.path.exists(file_path):
            logger.info(f"File exists, skipping: {os.path.basename(file_path)}")
            tracker.complete()
            return True

        async with httpx.AsyncClient() as client:
            async with client.stream(
                "GET",
                url,
                headers=headers,
                follow_redirects=True,
                timeout=timeout,
            ) as response:
                if response.status_code != 200:
                    logger.warning(
                        f"Download failed {url}, status: {response.status_code}"
                    )
                    return False

                total = int(response.headers.get("content-length", 0))
                downloaded = 0

                async with aiofiles.open(file_path, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        await f.write(chunk)
                        downloaded += len(chunk)
                        tracker.update(downloaded, total)

                tracker.complete()
                logger.success(f"Downloaded: {os.path.basename(file_path)}")
                return True

    except Exception as e:
        logger.error(f"Download error {url}: {str(e)}")
        tracker.failed(str(e))
        return False
