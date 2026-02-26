# app/services/task_orchestrator.py

"""
TaskOrchestrator — Phase-based state machine, dedup lock, subscriber notification.

Wraps the existing TaskTracker with:
  - Fine-grained phase lifecycle (QUEUED → DEDUP_CHECK → PROCESSING → COMPLETED/FAILED/CANCELLED)
  - Redis-based dedup locks so identical downloads are not duplicated
  - Subscriber fan-out: when a dedup'd task completes, all subscribers get the result
  - Structured error classification with retry hints

The existing ``unified_tasks.status`` field is kept in sync via ``transition()``
so that the frontend TaskCenter continues to work without changes.
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
    TaskPhase.QUEUED:      {TaskPhase.DEDUP_CHECK, TaskPhase.CANCELLED},
    TaskPhase.DEDUP_CHECK: {TaskPhase.PROCESSING, TaskPhase.COMPLETED, TaskPhase.FAILED},
    TaskPhase.PROCESSING:  {TaskPhase.COMPLETED, TaskPhase.FAILED, TaskPhase.CANCELLED},
    TaskPhase.COMPLETED:   set(),  # terminal
    TaskPhase.FAILED:      {TaskPhase.QUEUED},  # retry path
    TaskPhase.CANCELLED:   set(),  # terminal
}


# ─── Dedup Key Fields ────────────────────────────────────────────────

DEDUP_KEY_FIELDS: Dict[str, str] = {
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


# ─── TaskOrchestrator ────────────────────────────────────────────────

class TaskOrchestrator:
    """Phase-based task state machine with dedup lock and subscriber fan-out."""

    def __init__(self) -> None:
        self._last_renewal: Dict[str, float] = {}  # dedup_key → last renewal epoch

    # ── Internal helpers ──────────────────────────────────────────────

    async def _get_client(self):
        """Lazy-import async Supabase admin client."""
        from app.db.supabase_client import get_async_supabase_admin
        return await get_async_supabase_admin()

    def _get_redis(self):
        """Get Redis connection from Celery backend."""
        from app.celery_app import celery_app
        return celery_app.backend.client

    # ── Phase transition ──────────────────────────────────────────────

    async def transition(
        self,
        task_id: str,
        new_phase: TaskPhase,
        *,
        error_code: Optional[str] = None,
    ) -> None:
        """Validate and execute a phase transition.

        Also syncs the legacy ``status`` field so existing frontend code
        (TaskCenter) continues to work.

        Args:
            task_id: The unified_tasks row ID.
            new_phase: Target phase.
            error_code: Optional structured error code (only for FAILED transitions).

        Raises:
            ValueError: If the transition is invalid.
        """
        client = await self._get_client()

        # Fetch current phase
        result = await (
            client.table("unified_tasks")
            .select("phase")
            .eq("id", task_id)
            .single()
            .execute()
        )
        current_raw = result.data.get("phase", "queued")
        try:
            current = TaskPhase(current_raw)
        except ValueError:
            current = TaskPhase.QUEUED

        # Validate
        allowed = VALID_TRANSITIONS.get(current, set())
        if new_phase not in allowed:
            raise ValueError(
                f"Invalid phase transition: {current.value} → {new_phase.value}. "
                f"Allowed: {[p.value for p in allowed]}"
            )

        now_iso = datetime.now(timezone.utc).isoformat()
        updates: Dict[str, Any] = {
            "phase": new_phase.value,
            "status": _PHASE_TO_STATUS[new_phase],
        }

        # Timestamps
        if new_phase == TaskPhase.PROCESSING:
            updates["started_at"] = now_iso
        elif new_phase in (TaskPhase.COMPLETED, TaskPhase.FAILED, TaskPhase.CANCELLED):
            updates["completed_at"] = now_iso
        if new_phase == TaskPhase.COMPLETED:
            updates["progress"] = 100

        # Error code
        if error_code and new_phase == TaskPhase.FAILED:
            updates["error_code"] = error_code

        await (
            client.table("unified_tasks")
            .update(updates)
            .eq("id", task_id)
            .execute()
        )
        logger.debug(
            f"[Orchestrator] {task_id}: {current.value} → {new_phase.value}"
            + (f" (error={error_code})" if error_code else "")
        )

    # ── Dedup key helpers ─────────────────────────────────────────────

    @staticmethod
    def make_dedup_key(task_type: str, identifier: str) -> str:
        """Build a dedup key string.

        Returns:
            e.g. ``"task:download:7412345678901234"``
        """
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
            ``{"action": "created",     "dedup_key": "..."}``
            ``{"action": "subscribed",  "task_id": "...", "dedup_key": "..."}``
            ``{"action": "completed"}``

        Logic:
            1. Try Redis SET NX.
            2. If acquired → caller should proceed to create + run the task.
            3. If lock exists → look for an active task in DB with same dedup_key:
               a. Active task found → add subscriber entry, return "subscribed".
               b. No active task, but completed task found → return "completed".
               c. Lock exists but no task → stale lock, force-acquire.
        """
        dedup_key = self.make_dedup_key(task_type, dedup_identifier)
        redis = self._get_redis()

        # Attempt SET NX
        acquired = await asyncio.to_thread(redis.set, dedup_key, "locked", nx=True, ex=DEDUP_LOCK_TTL)
        if acquired:
            logger.debug(f"[Orchestrator] Acquired dedup lock: {dedup_key}")
            return {"action": "created", "dedup_key": dedup_key}

        # Lock exists — look for active task in DB
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

            # Add subscriber
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
                f"[Orchestrator] Subscribed user {user_id} to task {task_id} "
                f"(dedup_key={dedup_key})"
            )
            return {"action": "subscribed", "task_id": str(task_id), "dedup_key": dedup_key}

        # No active task — check completed
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
            logger.debug(f"[Orchestrator] Dedup key already completed: {dedup_key}")
            return {"action": "completed"}

        # Stale lock — force acquire
        await asyncio.to_thread(redis.set, dedup_key, "locked", ex=DEDUP_LOCK_TTL)
        logger.warning(f"[Orchestrator] Force-acquired stale dedup lock: {dedup_key}")
        return {"action": "created", "dedup_key": dedup_key}

    # ── Subscriber notification ───────────────────────────────────────

    async def notify_subscribers(
        self,
        task_id: str,
        *,
        success: bool = True,
        error_code: Optional[str] = None,
    ) -> None:
        """Fan-out results to all subscribers of a dedup'd task.

        On success (download type): copies download results from parsed_media
        to each subscriber's resource row.
        On failure: marks each subscriber's resource download statuses as failed.

        Args:
            task_id: The primary task that completed.
            success: Whether the task succeeded.
            error_code: Optional error code if failed.
        """
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
            logger.warning(f"[Orchestrator] notify_subscribers: task {task_id} not found")
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
                    f"[Orchestrator] Failed to notify subscriber "
                    f"resource={resource_id}: {e}"
                )

        logger.info(
            f"[Orchestrator] Notified {len(subscribers)} subscriber(s) "
            f"for task {task_id} (success={success})"
        )

    async def _copy_download_result_to_resource(
        self,
        res_repo: Any,
        resource_id: str,
        media_id: str,
    ) -> None:
        """Copy download statuses and paths from parsed_media to a resource row.

        Fields copied:
            video_download_status, music_download_status,
            cover_download_status, image_download_status
        """
        from app.repositories.media_repository import MediaRepository

        media_repo = MediaRepository()
        media = await media_repo.get_by_id(media_id)
        if not media:
            logger.warning(
                f"[Orchestrator] _copy_download_result: "
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
                f"[Orchestrator] Copied download result to resource {resource_id} "
                f"from media {media_id}"
            )

    async def _mark_resource_failed(
        self,
        res_repo: Any,
        resource_id: str,
        task_type: str,
    ) -> None:
        """Mark resource download statuses as failed.

        For download tasks, sets all four download status fields to 'failed'.
        For other task types, only marks the relevant field.
        """
        if task_type == "download":
            statuses = {
                "video_download_status": "failed",
                "music_download_status": "failed",
                "cover_download_status": "failed",
                "image_download_status": "failed",
            }
        elif task_type in ("ai_extract", "ai_transcription", "ai_summary"):
            # AI tasks don't affect download statuses directly;
            # the resource remains as-is for now.
            statuses = {}
        else:
            statuses = {}

        if statuses:
            await res_repo.update_download_status(resource_id, statuses)
            logger.debug(
                f"[Orchestrator] Marked resource {resource_id} as failed "
                f"for task_type={task_type}"
            )

    # ── Lock management ───────────────────────────────────────────────

    def renew_lock(self, dedup_key: str) -> bool:
        """Renew Redis TTL on a dedup lock, throttled by LOCK_RENEWAL_INTERVAL.

        Returns True if the TTL was actually renewed, False if throttled.
        """
        now = time.time()
        last = self._last_renewal.get(dedup_key, 0)
        if now - last < LOCK_RENEWAL_INTERVAL:
            return False

        redis = self._get_redis()
        redis.expire(dedup_key, DEDUP_LOCK_TTL)
        self._last_renewal[dedup_key] = now
        logger.debug(f"[Orchestrator] Renewed lock TTL: {dedup_key}")
        return True

    def release_lock(self, dedup_key: str) -> None:
        """Delete a dedup lock from Redis."""
        redis = self._get_redis()
        redis.delete(dedup_key)
        self._last_renewal.pop(dedup_key, None)
        logger.debug(f"[Orchestrator] Released lock: {dedup_key}")

    # ── Error classification ──────────────────────────────────────────

    @staticmethod
    def classify_error(exception: Exception) -> str:
        """Classify an exception into a structured error code by keyword matching.

        Returns one of the keys from ``ERROR_CODES``.
        """
        msg = str(exception).lower()

        if any(kw in msg for kw in ("timeout", "timed out", "connect timeout")):
            return "NETWORK_TIMEOUT"
        if any(kw in msg for kw in ("404", "not found", "does not exist")):
            return "RESOURCE_404"
        if any(kw in msg for kw in ("disk full", "no space", "storage full", "quota exceeded")):
            # Distinguish AI quota from storage
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

_orchestrator: Optional[TaskOrchestrator] = None


def get_orchestrator() -> TaskOrchestrator:
    """Get the module-level TaskOrchestrator singleton."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = TaskOrchestrator()
    return _orchestrator
