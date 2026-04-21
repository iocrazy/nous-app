# app/tasks/download_progress.py

"""
Download Progress Tracking

UnifiedProgressTracker: writes real-time progress to Redis pub/sub
and Supabase unified_tasks lifecycle management.
"""

from loguru import logger


class UnifiedProgressTracker:
    """Progress tracker that writes to Redis (real-time) + UnifiedTaskManager (Supabase lifecycle).

    Real-time progress is published via Redis pub/sub to channel
    ``task_progress:{user_id}`` so the WebSocket endpoint can push it
    to connected clients without polling.
    """

    def __init__(
        self,
        task_id: str,
        redis_client,
        unified_tracker=None,
        unified_task_id=None,
        user_id: str | None = None,
    ):
        self.task_id = task_id
        self.redis = redis_client
        self.unified_tracker = unified_tracker
        self.unified_task_id = unified_task_id
        self.user_id = user_id
        self.last_update = 0
        self._last_downloaded = 0
        self._last_time = 0
        self._speed = 0.0
        # Stage-based progress mapping: maps raw download % to overall task %
        self._stage_offset = 0  # Start percentage for current stage
        self._stage_weight = 100  # Weight of current stage (percentage points)

    def set_stage(self, offset: int, weight: int):
        """Set stage boundaries for overall progress calculation.

        Args:
            offset: Start percentage for current stage (0-95)
            weight: Weight of current stage in percentage points
        """
        self._stage_offset = offset
        self._stage_weight = weight

    def _publish(self, payload: dict):
        """Publish a progress message to Redis pub/sub for WebSocket delivery."""
        if not self.user_id:
            return
        import json

        channel = f"task_progress:{self.user_id}"
        try:
            self.redis.publish(channel, json.dumps(payload))
        except Exception as e:
            logger.debug(f"[ProgressTracker] Redis publish failed: {e}")

    async def update(self, downloaded: int, total: int):
        """Update download progress.

        Writes to Redis (sync, always) and Supabase unified_tasks (async, throttled).
        Must be awaited from an async context (e.g. inside download_file streaming loop).
        """
        import json
        import time

        now = time.time()
        if now - self.last_update < 0.5:
            return
        self.last_update = now

        # Calculate speed
        if self._last_time > 0:
            time_diff = now - self._last_time
            if time_diff > 0:
                self._speed = (downloaded - self._last_downloaded) / time_diff
        self._last_downloaded = downloaded
        self._last_time = now

        if total > 0:
            raw_percent = int((downloaded / total) * 100)
        else:
            # Content-Length unknown: simulate progress using downloaded bytes
            # Ramp up quickly then slow down (asymptotic approach to 90%)
            # e.g. 1MB→18%, 5MB→55%, 10MB→72%, 20MB→84%, 50MB→90%
            mb = downloaded / (1024 * 1024)
            raw_percent = min(int(90 * mb / (mb + 5)), 90) if mb > 0 else 0
        speed_str = self._format_speed(self._speed)

        # Write raw progress to Redis for legacy polling
        progress_data = {
            "percent": raw_percent,
            "downloaded": downloaded,
            "total": total,
            "speed": speed_str,
            "status": "downloading",
        }
        self.redis.setex(
            f"download_progress:{self.task_id}", 3600, json.dumps(progress_data)
        )

        # Map raw download percent to overall task progress using stage boundaries
        overall_percent = self._stage_offset + int(
            raw_percent * self._stage_weight / 100
        )
        overall_percent = min(
            max(overall_percent, 0), 99
        )  # Reserve 100 for explicit completion

        # Publish real-time progress via Redis pub/sub → WebSocket
        self._publish(
            {
                "unified_task_id": self.unified_task_id,
                "celery_task_id": self.task_id,
                "status": "downloading",
                "percent": overall_percent,
                "speed": speed_str,
                "downloaded": downloaded,
                "total": total,
            }
        )

    def _format_speed(self, bytes_per_sec: float) -> str:
        """Format speed as human readable string."""
        if bytes_per_sec < 1024:
            return f"{bytes_per_sec:.0f} B/s"
        elif bytes_per_sec < 1024 * 1024:
            return f"{bytes_per_sec / 1024:.1f} KB/s"
        else:
            return f"{bytes_per_sec / (1024 * 1024):.1f} MB/s"

    def complete(self):
        """Mark download as complete in Redis and publish via pub/sub."""
        import json

        progress_data = {
            "percent": 100,
            "downloaded": 0,
            "total": 0,
            "speed": "0 B/s",
            "status": "completed",
        }
        self.redis.setex(
            f"download_progress:{self.task_id}", 60, json.dumps(progress_data)
        )
        self._publish(
            {
                "unified_task_id": self.unified_task_id,
                "celery_task_id": self.task_id,
                "status": "completed",
                "percent": 100,
                "speed": "0 B/s",
                "downloaded": 0,
                "total": 0,
            }
        )

    def failed(self, error: str):
        """Mark download as failed in Redis and publish via pub/sub."""
        import json

        error_msg = error[:200] if error else "Unknown error"
        progress_data = {
            "percent": 0,
            "status": "failed",
            "error": error_msg,
        }
        self.redis.setex(
            f"download_progress:{self.task_id}", 300, json.dumps(progress_data)
        )
        self._publish(
            {
                "unified_task_id": self.unified_task_id,
                "celery_task_id": self.task_id,
                "status": "failed",
                "percent": 0,
                "speed": "0 B/s",
                "downloaded": 0,
                "total": 0,
                "error": error_msg,
            }
        )


def force_progress(
    tracker: UnifiedProgressTracker, progress: int, subtitle: str = None
):
    """Force a progress update via Redis pub/sub at download stage boundaries.

    Used before/after video, music, cover downloads to ensure the user sees
    meaningful progress even when fine-grained streaming progress isn't
    available (e.g. music/cover downloads, or content-length=0).
    """
    clamped = min(max(progress, 0), 99)
    speed_str = tracker._format_speed(tracker._speed)
    tracker._publish(
        {
            "unified_task_id": tracker.unified_task_id,
            "celery_task_id": tracker.task_id,
            "status": "downloading",
            "percent": clamped,
            "speed": speed_str,
            "downloaded": 0,
            "total": 0,
        }
    )
    logger.debug(f"[Download/Progress] Stage update: {progress}% subtitle={subtitle}")


def calc_stage_ranges(download_video: bool, download_cover: bool) -> dict:
    """Calculate progress ranges for each download stage.

    Returns dict mapping stage name to (offset, weight) tuple.
    Ranges span 0% to 95% (5% reserved for finalization).
    """
    raw_weights = {"video": 85, "cover": 15}
    parts = []
    if download_video:
        parts.append("video")
    if download_cover:
        parts.append("cover")

    if not parts:
        return {}

    total_w = sum(raw_weights[p] for p in parts)
    ranges = {}
    offset = 0
    for p in parts:
        weight = int(raw_weights[p] * 95 / total_w)  # Scale to 95 points (0% to 95%)
        ranges[p] = (offset, weight)
        offset += weight

    return ranges
