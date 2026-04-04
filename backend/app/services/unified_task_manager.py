# app/services/unified_task_manager.py

"""
UnifiedTaskManager — Single source of truth for unified_tasks lifecycle.

Merges the former TaskTracker (status/progress) and TaskOrchestrator
(phase/dedup/subscribers) into one class.  ``phase`` is the canonical
state machine; ``status`` is derived automatically via _PHASE_TO_STATUS.

Key design decisions:
  - Every lifecycle method (start/complete/fail/cancel) is **idempotent**:
    calling complete() on an already-terminal task logs a debug message
    and returns silently.  This eliminates the race between task body
    and Celery signal handlers.
  - Progress updates are throttled to max 1 DB write per second per task.
  - Redis-based dedup locks prevent duplicate downloads / AI pipelines.
  - Subscriber fan-out copies results to all dedup subscribers on completion.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

from loguru import logger


# ─── Phase Enum ───────────────────────────────────────────────────────

class TaskPhase(str, Enum):
    """Fine-grained task lifecycle phases."""
    QUEUED = "queued"
    DEDUP_CHECK = "dedup_check"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ─── Valid Transitions ────────────────────────────────────────────────

VALID_TRANSITIONS: Dict[TaskPhase, set[TaskPhase]] = {
    TaskPhase.QUEUED:      {TaskPhase.DEDUP_CHECK, TaskPhase.PROCESSING, TaskPhase.COMPLETED, TaskPhase.FAILED, TaskPhase.CANCELLED},
    TaskPhase.DEDUP_CHECK: {TaskPhase.PROCESSING, TaskPhase.COMPLETED, TaskPhase.FAILED},
    TaskPhase.PROCESSING:  {TaskPhase.COMPLETED, TaskPhase.FAILED, TaskPhase.CANCELLED},
    TaskPhase.COMPLETED:   set(),  # terminal
    TaskPhase.FAILED:      {TaskPhase.QUEUED},  # retry path
    TaskPhase.CANCELLED:   set(),  # terminal
}

_TERMINAL_PHASES = {TaskPhase.COMPLETED, TaskPhase.FAILED, TaskPhase.CANCELLED}


# ─── Dedup Key Fields ────────────────────────────────────────────────

DEDUP_KEY_FIELDS: Dict[str, str] = {
    "parse":             "url",
    "download":          "platform_id",
    "transcode":         "version_id",
    "ai_extract":        "platform_id",
    "ai_transcription":  "platform_id",
    "ai_summary":        "platform_id",
}


# ─── Error Codes ──────────────────────────────────────────────────────

ERROR_CODES: Dict[str, Dict[str, Any]] = {
    "NETWORK_TIMEOUT":    {"retryable": True,  "auto_retry": False},
    "RESOURCE_404":       {"retryable": False},
    "STORAGE_FULL":       {"retryable": False},
    "RATE_LIMITED":       {"retryable": True,  "auto_retry": True},
    "TRANSCODE_FAILED":   {"retryable": False},
    "AI_QUOTA_EXCEEDED":  {"retryable": False},
    "UNKNOWN":            {"retryable": True,  "auto_retry": False},
}


# ─── Constants ────────────────────────────────────────────────────────

DEDUP_LOCK_TTL = 3600           # seconds (1 hour)
LOCK_RENEWAL_INTERVAL = 600     # seconds (10 minutes)


# ─── Phase → legacy status mapping ───────────────────────────────────

_PHASE_TO_STATUS = {
    TaskPhase.QUEUED:      "pending",
    TaskPhase.DEDUP_CHECK: "pending",
    TaskPhase.PROCESSING:  "processing",
    TaskPhase.COMPLETED:   "completed",
    TaskPhase.FAILED:      "failed",
    TaskPhase.CANCELLED:   "cancelled",
}


# ─── UnifiedTaskManager ──────────────────────────────────────────────

class UnifiedTaskManager:
    """Unified task lifecycle manager with phase state machine, dedup, and subscriber fan-out."""

    THROTTLE_INTERVAL = 1.0  # seconds between progress writes

    def __init__(self) -> None:
        self._last_progress: Dict[str, float] = {}   # task_id -> last_write_time
        self._last_progress_value: Dict[str, int] = {}  # task_id -> last_progress_percent
        self._last_renewal: Dict[str, float] = {}     # dedup_key -> last renewal epoch

    # ── Internal helpers ──────────────────────────────────────────────

    async def _get_client(self):
        """Lazy-import async Supabase admin client."""
        from app.db.supabase_client import get_async_supabase_admin
        return await get_async_supabase_admin()

    def _get_redis(self):
        """Get Redis connection from Celery backend."""
        from app.celery_app import celery_app
        return celery_app.backend.client

    async def _get_phase(self, task_id: str) -> TaskPhase:
        """Fetch the current phase of a task."""
        client = await self._get_client()
        result = await (
            client.table("unified_tasks")
            .select("phase")
            .eq("id", task_id)
            .single()
            .execute()
        )
        raw = result.data.get("phase", "queued")
        try:
            return TaskPhase(raw)
        except ValueError:
            return TaskPhase.QUEUED

    def _validate_transition(self, current: TaskPhase, target: TaskPhase) -> None:
        """Raise ValueError if the transition is invalid."""
        allowed = VALID_TRANSITIONS.get(current, set())
        if target not in allowed:
            raise ValueError(
                f"Invalid phase transition: {current.value} -> {target.value}. "
                f"Allowed: {[p.value for p in allowed]}"
            )

    async def _atomic_update(self, task_id: str, updates: Dict[str, Any]) -> None:
        """Write updates to a unified_tasks row."""
        client = await self._get_client()
        await (
            client.table("unified_tasks")
            .update(updates)
            .eq("id", task_id)
            .execute()
        )

    # ── Lifecycle: create ─────────────────────────────────────────────

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
        dedup_key: Optional[str] = None,
    ) -> str:
        """Create a unified_tasks row. Returns the task UUID.

        Sets phase=QUEUED and status=pending at creation time.
        If dedup_key is provided it is written in the same INSERT,
        eliminating the former two-step create+patch pattern.
        """
        client = await self._get_client()
        row: Dict[str, Any] = {
            "user_id": user_id,
            "task_type": task_type,
            "title": title[:200] if title else "Untitled",
            "status": "pending",
            "phase": TaskPhase.QUEUED.value,
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
        if dedup_key:
            row["dedup_key"] = dedup_key

        result = await client.table("unified_tasks").insert(row).execute()
        task_id = result.data[0]["id"]
        logger.debug(f"[TaskManager] Created {task_type} task {task_id}: {title[:40]}")
        return task_id

    # ── Lifecycle: start ──────────────────────────────────────────────

    async def start(self, task_id: str) -> None:
        """Transition to PROCESSING phase.

        Idempotent: already-PROCESSING or terminal tasks are silently skipped.
        Allows transitions from QUEUED or DEDUP_CHECK.
        """
        current = await self._get_phase(task_id)
        if current == TaskPhase.PROCESSING:
            return  # already processing
        if current in _TERMINAL_PHASES:
            logger.debug(f"[TaskManager] start() skipped: {task_id} already terminal ({current.value})")
            return
        self._validate_transition(current, TaskPhase.PROCESSING)
        now_iso = datetime.now(timezone.utc).isoformat()
        await self._atomic_update(task_id, {
            "phase": TaskPhase.PROCESSING.value,
            "status": _PHASE_TO_STATUS[TaskPhase.PROCESSING],
            "started_at": now_iso,
        })
        logger.debug(f"[TaskManager] Started {task_id} ({current.value} -> processing)")

    # ── Lifecycle: update_progress ────────────────────────────────────

    async def update_progress(
        self,
        task_id: str,
        progress: int,
        *,
        speed: Optional[int] = None,
        subtitle: Optional[str] = None,
        title: Optional[str] = None,
        metadata_patch: Optional[dict] = None,
    ) -> None:
        """Update progress (0-100). Throttled to 1 write/sec per task.

        Does not change phase — only updates progress/speed/subtitle/title.
        """
        now = time.time()
        last = self._last_progress.get(task_id, 0)
        # Skip throttle when title is being updated (important state change)
        if title is None and now - last < self.THROTTLE_INTERVAL:
            return
        self._last_progress[task_id] = now
        # Only log at INFO for significant changes (every 10%), DEBUG for the rest
        prev_progress = self._last_progress_value.get(task_id, 0)
        if progress // 10 > prev_progress // 10 or progress >= 100:
            logger.info(f"[TaskManager] Progress: task={task_id}, {progress}%")
        self._last_progress_value[task_id] = progress

        client = await self._get_client()
        updates: Dict[str, Any] = {
            "progress": min(max(progress, 0), 100),
            "status": "processing",
        }
        if speed is not None:
            updates["speed"] = speed
        if subtitle is not None:
            updates["subtitle"] = subtitle
        if title is not None:
            updates["title"] = title
        if metadata_patch:
            existing = await client.table("unified_tasks").select("metadata").eq("id", task_id).single().execute()
            merged = {**(existing.data.get("metadata") or {}), **metadata_patch}
            updates["metadata"] = merged

        await client.table("unified_tasks").update(updates).eq("id", task_id).execute()

    # ── Lifecycle: complete ───────────────────────────────────────────

    async def complete(
        self,
        task_id: str,
        *,
        subtitle: Optional[str] = None,
        metadata_patch: Optional[dict] = None,
    ) -> None:
        """Transition to COMPLETED phase.

        Idempotent: already-terminal tasks log a debug message and return.
        """
        current = await self._get_phase(task_id)
        if current in _TERMINAL_PHASES:
            logger.debug(f"[TaskManager] complete() skipped: {task_id} already terminal ({current.value})")
            return

        now_iso = datetime.now(timezone.utc).isoformat()
        updates: Dict[str, Any] = {
            "phase": TaskPhase.COMPLETED.value,
            "status": _PHASE_TO_STATUS[TaskPhase.COMPLETED],
            "progress": 100,
            "completed_at": now_iso,
            "subtitle": subtitle or "",
        }
        if metadata_patch:
            client = await self._get_client()
            existing = await client.table("unified_tasks").select("metadata").eq("id", task_id).single().execute()
            merged = {**(existing.data.get("metadata") or {}), **metadata_patch}
            updates["metadata"] = merged

        await self._atomic_update(task_id, updates)
        self._last_progress.pop(task_id, None)
        self._last_progress_value.pop(task_id, None)
        logger.debug(f"[TaskManager] Completed {task_id}")

    # ── Lifecycle: fail ───────────────────────────────────────────────

    async def fail(self, task_id: str, error_msg: str, *, error_code: Optional[str] = None) -> None:
        """Transition to FAILED phase.

        Idempotent: already-terminal tasks log a debug message and return.
        error_code is optional — can be produced by classify_error().
        """
        current = await self._get_phase(task_id)
        if current in _TERMINAL_PHASES:
            logger.debug(f"[TaskManager] fail() skipped: {task_id} already terminal ({current.value})")
            return

        now_iso = datetime.now(timezone.utc).isoformat()
        updates: Dict[str, Any] = {
            "phase": TaskPhase.FAILED.value,
            "status": _PHASE_TO_STATUS[TaskPhase.FAILED],
            "error_msg": error_msg[:500] if error_msg else "Unknown error",
            "completed_at": now_iso,
        }
        if error_code:
            updates["error_code"] = error_code

        await self._atomic_update(task_id, updates)
        self._last_progress.pop(task_id, None)
        self._last_progress_value.pop(task_id, None)
        logger.debug(f"[TaskManager] Failed {task_id}: {error_msg[:80]}")

    # ── Lifecycle: cancel ─────────────────────────────────────────────

    async def cancel(self, task_id: str, user_id: str) -> None:
        """Transition to CANCELLED phase and revoke Celery task if possible.

        Idempotent: already-terminal tasks are silently skipped.
        """
        client = await self._get_client()
        result = await (
            client.table("unified_tasks")
            .select("celery_task_id, phase")
            .eq("id", task_id)
            .eq("user_id", user_id)
            .single()
            .execute()
        )
        if not result.data:
            logger.warning(f"[TaskManager] Cancel: task {task_id} not found for user {user_id}")
            return

        current_raw = result.data.get("phase", "queued")
        try:
            current = TaskPhase(current_raw)
        except ValueError:
            current = TaskPhase.QUEUED

        if current in _TERMINAL_PHASES:
            logger.debug(f"[TaskManager] cancel() skipped: {task_id} already terminal ({current.value})")
            return

        celery_id = result.data.get("celery_task_id")

        await (
            client.table("unified_tasks")
            .update({
                "phase": TaskPhase.CANCELLED.value,
                "status": _PHASE_TO_STATUS[TaskPhase.CANCELLED],
            })
            .eq("id", task_id)
            .eq("user_id", user_id)
            .execute()
        )
        self._last_progress.pop(task_id, None)
        self._last_progress_value.pop(task_id, None)

        if celery_id:
            try:
                from app.celery_app import celery_app
                celery_app.control.revoke(celery_id, terminate=True)
                logger.info(f"[TaskManager] Revoked Celery task {celery_id}")
            except Exception as e:
                logger.warning(f"[TaskManager] Failed to revoke Celery task {celery_id}: {e}")

        logger.debug(f"[TaskManager] Cancelled {task_id}")

    # ── Query helpers ─────────────────────────────────────────────────

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
            "by_type": {"parse": 0, "download": 0, "upload": 0, "transcode": 0, "ai_pipeline": 0, "ai_extract": 0, "ai_transcription": 0, "ai_summary": 0},
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
        """Reset a failed task for retry (phase -> QUEUED)."""
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

        now_iso = datetime.now(timezone.utc).isoformat()
        await client.table("unified_tasks").update({
            "phase": TaskPhase.QUEUED.value,
            "status": "pending",
            "progress": 0,
            "error_msg": None,
            "error_code": None,
            "started_at": None,
            "completed_at": None,
            "updated_at": now_iso,
        }).eq("id", task_id).execute()

        return task

    # ── Dedup key helpers ─────────────────────────────────────────────

    @staticmethod
    def make_dedup_key(task_type: str, identifier: str) -> str:
        """Build a dedup key string, e.g. ``"task:download:7412345678901234"``."""
        return f"task:{task_type}:{identifier}"

    # ── Acquire or subscribe ──────────────────────────────────────────

    async def acquire_or_subscribe(
        self,
        task_type: str,
        dedup_identifier: str,
        user_id: str,
        resource_id: str,
    ) -> Dict[str, Any]:
        """Attempt to acquire a dedup lock, or subscribe to existing work.

        Returns one of:
            {"action": "created",    "dedup_key": "..."}
            {"action": "subscribed", "task_id": "...", "dedup_key": "..."}
            {"action": "completed"}
        """
        dedup_key = self.make_dedup_key(task_type, dedup_identifier)
        redis = self._get_redis()

        acquired = await asyncio.to_thread(redis.set, dedup_key, "locked", nx=True, ex=DEDUP_LOCK_TTL)
        if acquired:
            logger.debug(f"[TaskManager] Acquired dedup lock: {dedup_key}")
            return {"action": "created", "dedup_key": dedup_key}

        client = await self._get_client()
        active_result = await (
            client.table("unified_tasks")
            .select("id, phase, subscribers")
            .eq("dedup_key", dedup_key)
            .in_("phase", ["queued", "dedup_check", "processing"])
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )

        if active_result.data:
            task = active_result.data[0]
            task_id = task["id"]
            subscribers = task.get("subscribers") or []
            subscribers.append({
                "user_id": user_id,
                "resource_id": resource_id,
                "subscribed_at": datetime.now(timezone.utc).isoformat(),
            })
            await (
                client.table("unified_tasks")
                .update({"subscribers": subscribers})
                .eq("id", task_id)
                .execute()
            )
            logger.info(
                f"[TaskManager] Subscribed user {user_id} to task {task_id} "
                f"(dedup_key={dedup_key})"
            )
            return {"action": "subscribed", "task_id": str(task_id), "dedup_key": dedup_key}

        completed_result = await (
            client.table("unified_tasks")
            .select("id")
            .eq("dedup_key", dedup_key)
            .eq("phase", "completed")
            .order("completed_at", desc=True)
            .limit(1)
            .execute()
        )

        if completed_result.data:
            logger.debug(f"[TaskManager] Dedup key already completed: {dedup_key}")
            return {"action": "completed"}

        await asyncio.to_thread(redis.set, dedup_key, "locked", ex=DEDUP_LOCK_TTL)
        logger.warning(f"[TaskManager] Force-acquired stale dedup lock: {dedup_key}")
        return {"action": "created", "dedup_key": dedup_key}

    # ── Subscriber notification ───────────────────────────────────────

    async def notify_subscribers(
        self,
        task_id: str,
        *,
        success: bool = True,
        error_code: Optional[str] = None,
    ) -> None:
        """Fan-out results to all subscribers of a dedup'd task."""
        from app.repositories.resources_repository import ResourcesRepository

        client = await self._get_client()
        result = await (
            client.table("unified_tasks")
            .select("task_type, media_id, subscribers")
            .eq("id", task_id)
            .single()
            .execute()
        )
        task = result.data
        if not task:
            logger.warning(f"[TaskManager] notify_subscribers: task {task_id} not found")
            return

        subscribers = task.get("subscribers") or []
        if not subscribers:
            return

        task_type = task.get("task_type", "")
        media_id = task.get("media_id")
        res_repo = ResourcesRepository()

        for sub in subscribers:
            resource_id = sub.get("resource_id")
            if not resource_id:
                continue
            try:
                if success and task_type == "download" and media_id:
                    await self._copy_download_result_to_resource(
                        res_repo, resource_id, media_id
                    )
                elif not success:
                    await self._mark_resource_failed(res_repo, resource_id, task_type)
            except Exception as e:
                logger.error(
                    f"[TaskManager] Failed to notify subscriber "
                    f"resource={resource_id}: {e}"
                )

        logger.info(
            f"[TaskManager] Notified {len(subscribers)} subscriber(s) "
            f"for task {task_id} (success={success})"
        )

    async def _copy_download_result_to_resource(
        self,
        res_repo: Any,
        resource_id: str,
        media_id: str,
    ) -> None:
        """Copy download statuses and paths from parsed_media to a resource row."""
        from app.repositories.media_repository import MediaRepository

        media_repo = MediaRepository()
        media = await media_repo.get_by_id(media_id)
        if not media:
            logger.warning(
                f"[TaskManager] _copy_download_result: "
                f"parsed_media {media_id} not found"
            )
            return

        updates: Dict[str, Any] = {}
        for status_field in (
            "video_download_status",
            "music_download_status",
            "cover_download_status",
            "image_download_status",
        ):
            value = media.get(status_field)
            if value is not None:
                updates[status_field] = value

        if updates:
            await res_repo.update_resource(resource_id, updates)
            logger.debug(
                f"[TaskManager] Copied download result to resource {resource_id} "
                f"from media {media_id}"
            )

    async def _mark_resource_failed(
        self,
        res_repo: Any,
        resource_id: str,
        task_type: str,
    ) -> None:
        """Mark resource download statuses as failed."""
        if task_type == "download":
            statuses = {
                "video_download_status": "failed",
                "music_download_status": "failed",
                "cover_download_status": "failed",
                "image_download_status": "failed",
            }
        elif task_type in ("ai_extract", "ai_transcription", "ai_summary"):
            statuses = {}
        else:
            statuses = {}

        if statuses:
            await res_repo.update_download_status(resource_id, statuses)
            logger.debug(
                f"[TaskManager] Marked resource {resource_id} as failed "
                f"for task_type={task_type}"
            )

    # ── Lock management ───────────────────────────────────────────────

    def renew_lock(self, dedup_key: str) -> bool:
        """Renew Redis TTL on a dedup lock, throttled by LOCK_RENEWAL_INTERVAL."""
        now = time.time()
        last = self._last_renewal.get(dedup_key, 0)
        if now - last < LOCK_RENEWAL_INTERVAL:
            return False

        redis = self._get_redis()
        redis.expire(dedup_key, DEDUP_LOCK_TTL)
        self._last_renewal[dedup_key] = now
        logger.debug(f"[TaskManager] Renewed lock TTL: {dedup_key}")
        return True

    def release_lock(self, dedup_key: str) -> None:
        """Delete a dedup lock from Redis."""
        redis = self._get_redis()
        redis.delete(dedup_key)
        self._last_renewal.pop(dedup_key, None)
        logger.debug(f"[TaskManager] Released lock: {dedup_key}")

    # ── Error classification ──────────────────────────────────────────

    @staticmethod
    def classify_error(exception: Exception) -> str:
        """Classify an exception into a structured error code."""
        msg = str(exception).lower()

        if any(kw in msg for kw in ("timeout", "timed out", "connect timeout")):
            return "NETWORK_TIMEOUT"
        if any(kw in msg for kw in ("404", "not found", "does not exist")):
            return "RESOURCE_404"
        if any(kw in msg for kw in ("disk full", "no space", "storage full", "quota exceeded")):
            if "ai" in msg or "openai" in msg or "whisper" in msg:
                return "AI_QUOTA_EXCEEDED"
            return "STORAGE_FULL"
        if any(kw in msg for kw in ("rate limit", "too many requests", "429")):
            return "RATE_LIMITED"
        if any(kw in msg for kw in ("transcode", "ffmpeg", "codec")):
            return "TRANSCODE_FAILED"
        if any(kw in msg for kw in ("ai quota", "ai_quota", "openai", "whisper")):
            return "AI_QUOTA_EXCEEDED"

        return "UNKNOWN"


# ─── Singleton ────────────────────────────────────────────────────────

_manager: Optional[UnifiedTaskManager] = None


def get_task_manager() -> UnifiedTaskManager:
    """Get the module-level UnifiedTaskManager singleton."""
    global _manager
    if _manager is None:
        _manager = UnifiedTaskManager()
    return _manager


# Backward-compatible aliases
get_task_tracker = get_task_manager
get_orchestrator = get_task_manager
