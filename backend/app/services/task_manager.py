# app/services/task_manager.py

"""
Task Manager Service

Manages download task status in Redis with support for:
- Task status tracking (pending, downloading, completed, failed)
- Auto retry with exponential backoff
- Queue statistics
"""

import json
from datetime import datetime
from typing import Optional, List, Dict, Any
from enum import Enum
from loguru import logger


class TaskStatus(str, Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskManager:
    """Manages download task status in Redis."""

    TASK_PREFIX = "task:"
    STATS_KEY = "task:stats"
    TASK_LIST_KEY = "task:list"
    MAX_RETRIES = 3
    TASK_TTL = 604800  # 7 days

    def __init__(self, redis_client):
        self.redis = redis_client

    def _task_key(self, aweme_id: str) -> str:
        return f"{self.TASK_PREFIX}{aweme_id}"

    def _now(self) -> str:
        return datetime.utcnow().isoformat() + "Z"

    # ========== Task CRUD ==========

    def create_task(
        self,
        aweme_id: str,
        video_title: str,
        total_size: int = 0
    ) -> Dict[str, Any]:
        """Create a new task with pending status."""
        task = {
            "aweme_id": aweme_id,
            "video_title": video_title[:50] if video_title else "Unknown",
            "status": TaskStatus.PENDING.value,
            "percent": 0,
            "downloaded": 0,
            "total": total_size,
            "speed": "0 B/s",
            "retry_count": 0,
            "max_retries": self.MAX_RETRIES,
            "error": None,
            "started_at": self._now(),
            "updated_at": self._now(),
        }

        key = self._task_key(aweme_id)
        self.redis.setex(key, self.TASK_TTL, json.dumps(task, ensure_ascii=False))

        # Add to task list
        self.redis.sadd(self.TASK_LIST_KEY, aweme_id)

        # Update stats
        self._increment_stat(TaskStatus.PENDING)

        logger.debug(f"Task created: {aweme_id}")
        return task

    def get_task(self, aweme_id: str) -> Optional[Dict[str, Any]]:
        """Get task by aweme_id."""
        key = self._task_key(aweme_id)
        data = self.redis.get(key)
        if data:
            return json.loads(data)
        return None

    def update_task(self, aweme_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update task fields."""
        task = self.get_task(aweme_id)
        if not task:
            return None

        old_status = task.get("status")
        task.update(updates)
        task["updated_at"] = self._now()

        key = self._task_key(aweme_id)
        self.redis.setex(key, self.TASK_TTL, json.dumps(task, ensure_ascii=False))

        # Update stats if status changed
        new_status = task.get("status")
        if old_status != new_status:
            self._decrement_stat(TaskStatus(old_status))
            self._increment_stat(TaskStatus(new_status))

        return task

    def delete_task(self, aweme_id: str) -> bool:
        """Delete a task."""
        task = self.get_task(aweme_id)
        if task:
            self._decrement_stat(TaskStatus(task["status"]))
            self.redis.delete(self._task_key(aweme_id))
            self.redis.srem(self.TASK_LIST_KEY, aweme_id)
            return True
        return False

    # ========== Status Transitions ==========

    def start_download(self, aweme_id: str, total_size: int = 0) -> Optional[Dict[str, Any]]:
        """Transition task to downloading status."""
        return self.update_task(aweme_id, {
            "status": TaskStatus.DOWNLOADING.value,
            "total": total_size,
            "percent": 0,
            "downloaded": 0,
        })

    def update_progress(
        self,
        aweme_id: str,
        downloaded: int,
        total: int,
        speed: str = "0 B/s"
    ) -> Optional[Dict[str, Any]]:
        """Update download progress."""
        percent = int((downloaded / total) * 100) if total > 0 else 0
        return self.update_task(aweme_id, {
            "downloaded": downloaded,
            "total": total,
            "percent": percent,
            "speed": speed,
        })

    def complete_task(self, aweme_id: str) -> Optional[Dict[str, Any]]:
        """Mark task as completed."""
        return self.update_task(aweme_id, {
            "status": TaskStatus.COMPLETED.value,
            "percent": 100,
            "error": None,
        })

    def fail_task(self, aweme_id: str, error: str) -> Optional[Dict[str, Any]]:
        """Mark task as failed and handle retry logic."""
        task = self.get_task(aweme_id)
        if not task:
            return None

        retry_count = task.get("retry_count", 0) + 1

        if retry_count < self.MAX_RETRIES:
            # Will be retried
            return self.update_task(aweme_id, {
                "status": TaskStatus.FAILED.value,
                "retry_count": retry_count,
                "error": error,
            })
        else:
            # Max retries reached, permanent failure
            return self.update_task(aweme_id, {
                "status": TaskStatus.FAILED.value,
                "retry_count": retry_count,
                "error": f"Max retries reached. Last error: {error}",
            })

    def can_retry(self, aweme_id: str) -> bool:
        """Check if task can be retried."""
        task = self.get_task(aweme_id)
        if not task:
            return False
        return task.get("retry_count", 0) < self.MAX_RETRIES

    def reset_for_retry(self, aweme_id: str) -> Optional[Dict[str, Any]]:
        """Reset task for manual retry (keeps retry_count)."""
        return self.update_task(aweme_id, {
            "status": TaskStatus.PENDING.value,
            "percent": 0,
            "downloaded": 0,
            "error": None,
        })

    def force_retry(self, aweme_id: str) -> Optional[Dict[str, Any]]:
        """Force retry task (resets retry_count)."""
        return self.update_task(aweme_id, {
            "status": TaskStatus.PENDING.value,
            "percent": 0,
            "downloaded": 0,
            "retry_count": 0,
            "error": None,
        })

    # ========== Statistics ==========

    def _increment_stat(self, status: TaskStatus):
        """Increment status counter."""
        stats = self.get_stats()
        stats[status.value] = stats.get(status.value, 0) + 1
        self.redis.set(self.STATS_KEY, json.dumps(stats))

    def _decrement_stat(self, status: TaskStatus):
        """Decrement status counter."""
        stats = self.get_stats()
        stats[status.value] = max(0, stats.get(status.value, 0) - 1)
        self.redis.set(self.STATS_KEY, json.dumps(stats))

    def get_stats(self) -> Dict[str, int]:
        """Get queue statistics."""
        data = self.redis.get(self.STATS_KEY)
        if data:
            return json.loads(data)
        return {
            "pending": 0,
            "downloading": 0,
            "completed": 0,
            "failed": 0,
        }

    def recalculate_stats(self) -> Dict[str, int]:
        """Recalculate stats from actual tasks."""
        stats = {
            "pending": 0,
            "downloading": 0,
            "completed": 0,
            "failed": 0,
        }

        for aweme_id in self.get_all_task_ids():
            task = self.get_task(aweme_id)
            if task:
                status = task.get("status", "pending")
                stats[status] = stats.get(status, 0) + 1

        self.redis.set(self.STATS_KEY, json.dumps(stats))
        return stats

    # ========== Task Listing ==========

    def get_all_task_ids(self) -> List[str]:
        """Get all task IDs."""
        return [id.decode() if isinstance(id, bytes) else id
                for id in self.redis.smembers(self.TASK_LIST_KEY)]

    def get_tasks(
        self,
        status: Optional[TaskStatus] = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Get tasks with optional status filter."""
        all_ids = self.get_all_task_ids()
        tasks = []

        for aweme_id in all_ids:
            task = self.get_task(aweme_id)
            if task:
                if status is None or task.get("status") == status.value:
                    tasks.append(task)

        # Sort by updated_at descending
        tasks.sort(key=lambda t: t.get("updated_at", ""), reverse=True)

        return tasks[offset:offset + limit]

    def get_failed_tasks(self) -> List[Dict[str, Any]]:
        """Get all failed tasks."""
        return self.get_tasks(status=TaskStatus.FAILED)

    def get_pending_tasks(self) -> List[Dict[str, Any]]:
        """Get all pending tasks."""
        return self.get_tasks(status=TaskStatus.PENDING)

    # ========== Cleanup ==========

    def cleanup_completed(self, keep_recent: int = 100) -> int:
        """Remove old completed tasks, keeping the most recent ones."""
        completed = self.get_tasks(status=TaskStatus.COMPLETED, limit=10000)

        if len(completed) <= keep_recent:
            return 0

        to_remove = completed[keep_recent:]
        removed = 0

        for task in to_remove:
            if self.delete_task(task["aweme_id"]):
                removed += 1

        return removed

    def clear_all(self):
        """Clear all tasks (use with caution)."""
        for aweme_id in self.get_all_task_ids():
            self.redis.delete(self._task_key(aweme_id))

        self.redis.delete(self.TASK_LIST_KEY)
        self.redis.delete(self.STATS_KEY)
        logger.warning("All tasks cleared")


# Global instance getter
_task_manager: Optional[TaskManager] = None


def get_task_manager() -> TaskManager:
    """Get or create TaskManager instance."""
    global _task_manager
    if _task_manager is None:
        from app.celery_app import celery_app
        _task_manager = TaskManager(celery_app.backend.client)
    return _task_manager
