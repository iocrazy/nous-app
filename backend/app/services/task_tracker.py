# app/services/task_tracker.py

"""
Unified Task Tracker — writes to the unified_tasks table.

Provides lifecycle management for all task types:
  download, upload, transcode, ai_pipeline

Progress updates are throttled to max 1 DB write per second per task.
"""

import time
from datetime import datetime, timezone
from typing import Optional

from loguru import logger


class TaskTracker:
    """Unified task lifecycle manager."""

    THROTTLE_INTERVAL = 1.0  # seconds between progress writes

    def __init__(self):
        self._last_progress: dict[str, float] = {}  # task_id -> last_write_time

    async def _get_client(self):
        from app.db.supabase_client import get_async_supabase_admin
        return await get_async_supabase_admin()

    async def create(
        self,
        user_id: str,
        task_type: str,
        title: str,
        *,
        resource_id: Optional[str] = None,
        media_id: Optional[str] = None,
        group_id: Optional[str] = None,
        celery_task_id: Optional[str] = None,
        total_bytes: Optional[int] = None,
        subtitle: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> str:
        """Create a unified_tasks row. Returns the task UUID."""
        client = await self._get_client()
        row = {
            "user_id": user_id,
            "task_type": task_type,
            "title": title[:200] if title else "Untitled",
            "status": "pending",
            "progress": 0,
        }
        if resource_id:
            row["resource_id"] = resource_id
        if media_id:
            row["media_id"] = media_id
        if group_id:
            row["group_id"] = group_id
        if celery_task_id:
            row["celery_task_id"] = celery_task_id
        if total_bytes is not None:
            row["total_bytes"] = total_bytes
        if subtitle:
            row["subtitle"] = subtitle
        if metadata:
            row["metadata"] = metadata

        result = await client.table("unified_tasks").insert(row).execute()
        task_id = result.data[0]["id"]
        logger.debug(f"[TaskTracker] Created {task_type} task {task_id}: {title[:40]}")
        return task_id

    async def start(self, task_id: str):
        """Mark task as processing."""
        client = await self._get_client()
        await client.table("unified_tasks").update({
            "status": "processing",
            "started_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", task_id).execute()
        logger.debug(f"[TaskTracker] Started {task_id}")

    async def update_progress(
        self,
        task_id: str,
        progress: int,
        *,
        speed: Optional[int] = None,
        subtitle: Optional[str] = None,
        metadata_patch: Optional[dict] = None,
    ):
        """Update progress (0-100). Throttled to 1 write/sec per task."""
        now = time.time()
        last = self._last_progress.get(task_id, 0)
        if now - last < self.THROTTLE_INTERVAL:
            return
        self._last_progress[task_id] = now

        client = await self._get_client()
        updates: dict = {
            "progress": min(max(progress, 0), 100),
            "status": "processing",
        }
        if speed is not None:
            updates["speed"] = speed
        if subtitle is not None:
            updates["subtitle"] = subtitle
        if metadata_patch:
            existing = await client.table("unified_tasks").select("metadata").eq("id", task_id).single().execute()
            merged = {**(existing.data.get("metadata") or {}), **metadata_patch}
            updates["metadata"] = merged

        await client.table("unified_tasks").update(updates).eq("id", task_id).execute()

    async def complete(self, task_id: str, *, metadata_patch: Optional[dict] = None):
        """Mark task as completed."""
        client = await self._get_client()
        now_iso = datetime.now(timezone.utc).isoformat()
        updates: dict = {
            "status": "completed",
            "progress": 100,
            "completed_at": now_iso,
        }
        if metadata_patch:
            existing = await client.table("unified_tasks").select("metadata").eq("id", task_id).single().execute()
            merged = {**(existing.data.get("metadata") or {}), **metadata_patch}
            updates["metadata"] = merged

        await client.table("unified_tasks").update(updates).eq("id", task_id).execute()
        self._last_progress.pop(task_id, None)
        logger.debug(f"[TaskTracker] Completed {task_id}")

    async def fail(self, task_id: str, error_msg: str):
        """Mark task as failed."""
        client = await self._get_client()
        await client.table("unified_tasks").update({
            "status": "failed",
            "error_msg": error_msg[:500] if error_msg else "Unknown error",
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", task_id).execute()
        self._last_progress.pop(task_id, None)
        logger.debug(f"[TaskTracker] Failed {task_id}: {error_msg[:80]}")

    async def cancel(self, task_id: str, user_id: str):
        """Mark task as cancelled and revoke Celery task if possible."""
        client = await self._get_client()
        # Get celery_task_id before updating (scoped to user)
        result = (
            await client.table("unified_tasks")
            .select("celery_task_id")
            .eq("id", task_id)
            .eq("user_id", user_id)
            .single()
            .execute()
        )
        if not result.data:
            logger.warning(f"[TaskTracker] Cancel: task {task_id} not found for user {user_id}")
            return
        celery_id = result.data.get("celery_task_id")

        await (
            client.table("unified_tasks")
            .update({"status": "cancelled"})
            .eq("id", task_id)
            .eq("user_id", user_id)
            .execute()
        )
        self._last_progress.pop(task_id, None)

        # Revoke Celery task
        if celery_id:
            try:
                from app.celery_app import celery_app
                celery_app.control.revoke(celery_id, terminate=True)
                logger.info(f"[TaskTracker] Revoked Celery task {celery_id}")
            except Exception as e:
                logger.warning(f"[TaskTracker] Failed to revoke Celery task {celery_id}: {e}")

        logger.debug(f"[TaskTracker] Cancelled {task_id}")

    # ─── Query helpers ─────────────────────────────────────

    async def get_active_tasks(self, user_id: str, limit: int = 20) -> list[dict]:
        """Get active (pending/processing) tasks for a user."""
        client = await self._get_client()
        result = await (
            client.table("unified_tasks")
            .select("*")
            .eq("user_id", user_id)
            .in_("status", ["pending", "processing"])
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []

    async def get_tasks(
        self,
        user_id: str,
        *,
        task_type: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """Get paginated tasks for a user."""
        client = await self._get_client()
        query = (
            client.table("unified_tasks")
            .select("*")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .range(offset, offset + limit - 1)
        )
        if task_type:
            query = query.eq("task_type", task_type)
        if status:
            query = query.eq("status", status)
        result = await query.execute()
        return result.data or []

    async def get_stats(self, user_id: str) -> dict:
        """Get task counts by type and status."""
        client = await self._get_client()
        result = await (
            client.table("unified_tasks")
            .select("task_type, status")
            .eq("user_id", user_id)
            .execute()
        )

        stats = {
            "by_type": {"download": 0, "upload": 0, "transcode": 0, "ai_pipeline": 0, "ai_extract": 0, "ai_transcription": 0, "ai_summary": 0},
            "by_status": {"pending": 0, "processing": 0, "completed": 0, "failed": 0, "cancelled": 0},
            "active_total": 0,
        }
        for row in result.data or []:
            t = row["task_type"]
            s = row["status"]
            if t in stats["by_type"]:
                stats["by_type"][t] += 1
            if s in stats["by_status"]:
                stats["by_status"][s] += 1
            if s in ("pending", "processing"):
                stats["active_total"] += 1
        return stats

    async def delete_task(self, task_id: str, user_id: str) -> bool:
        """Delete a task record."""
        client = await self._get_client()
        result = await (
            client.table("unified_tasks")
            .delete()
            .eq("id", task_id)
            .eq("user_id", user_id)
            .execute()
        )
        return len(result.data or []) > 0

    async def clear_completed(self, user_id: str, keep_recent: int = 50) -> int:
        """Delete old completed/failed tasks, keeping the most recent ones."""
        client = await self._get_client()
        # Get IDs to keep
        keep = await (
            client.table("unified_tasks")
            .select("id")
            .eq("user_id", user_id)
            .in_("status", ["completed", "failed", "cancelled"])
            .order("completed_at", desc=True)
            .limit(keep_recent)
            .execute()
        )
        keep_ids = [r["id"] for r in (keep.data or [])]

        # Delete the rest
        query = (
            client.table("unified_tasks")
            .delete()
            .eq("user_id", user_id)
            .in_("status", ["completed", "failed", "cancelled"])
        )
        if keep_ids:
            for kid in keep_ids:
                query = query.neq("id", kid)
        result = await query.execute()
        return len(result.data or [])

    async def retry_task(self, task_id: str, user_id: str) -> Optional[dict]:
        """Reset a failed task for retry."""
        client = await self._get_client()
        result = await (
            client.table("unified_tasks")
            .select("*")
            .eq("id", task_id)
            .eq("user_id", user_id)
            .single()
            .execute()
        )
        task = result.data
        if not task or task["status"] not in ("failed", "cancelled"):
            return None

        # Reset status
        await client.table("unified_tasks").update({
            "status": "pending",
            "progress": 0,
            "error_msg": None,
            "started_at": None,
            "completed_at": None,
        }).eq("id", task_id).execute()

        return task


# Singleton
_tracker: Optional[TaskTracker] = None


def get_task_tracker() -> TaskTracker:
    global _tracker
    if _tracker is None:
        _tracker = TaskTracker()
    return _tracker
