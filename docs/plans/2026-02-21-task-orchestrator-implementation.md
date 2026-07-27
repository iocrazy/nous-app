# TaskOrchestrator Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace ad-hoc Celery task status tracking with a centralized TaskOrchestrator providing state machine validation, global dedup with multi-user subscription, error classification, and fine-grained phase visibility.

**Architecture:** B+C hybrid — State machine (`TaskPhase` enum + `VALID_TRANSITIONS`) ensures no illegal state changes. Redis SET NX provides dedup locks. Celery signals (`task_prerun`, `task_success`, `task_failure`) automate lifecycle. `subscribers` JSONB in `unified_tasks` enables multi-user download sharing.

**Tech Stack:** Python 3.12, FastAPI, Celery 5, Redis, Supabase PostgreSQL, React 19 + TypeScript

---

## Task 1: Database Migration — Add Orchestrator Columns

**Files:**
- Create: `supabase/migrations/084_task_orchestrator.sql`

**Step 1: Write the migration SQL**

```sql
-- 084_task_orchestrator.sql
-- Add TaskOrchestrator columns to unified_tasks table

-- Phase: fine-grained lifecycle stage (queued → dedup_check → processing → completed/failed/cancelled)
ALTER TABLE unified_tasks
  ADD COLUMN IF NOT EXISTS phase TEXT DEFAULT 'queued';

-- Dedup key: identifies duplicate tasks across users (e.g. "download:platform_id_123")
ALTER TABLE unified_tasks
  ADD COLUMN IF NOT EXISTS dedup_key TEXT;

-- Subscribers: array of {user_id, resource_id, subscribed_at} for multi-user dedup
ALTER TABLE unified_tasks
  ADD COLUMN IF NOT EXISTS subscribers JSONB DEFAULT '[]';

-- Error code: structured error classification (e.g. "NETWORK_TIMEOUT", "RESOURCE_404")
ALTER TABLE unified_tasks
  ADD COLUMN IF NOT EXISTS error_code TEXT;

-- Index for dedup lookups
CREATE INDEX IF NOT EXISTS idx_unified_tasks_dedup_key
  ON unified_tasks (dedup_key) WHERE dedup_key IS NOT NULL;

-- Index for phase filtering
CREATE INDEX IF NOT EXISTS idx_unified_tasks_phase
  ON unified_tasks (phase) WHERE phase IN ('queued', 'dedup_check', 'processing');
```

**Step 2: Execute migration on local Supabase**

Run: `psql -h 127.0.0.1 -p 54322 -U postgres -d postgres -f supabase/migrations/084_task_orchestrator.sql`
Expected: ALTER TABLE, CREATE INDEX — no errors

**Step 3: Verify columns exist**

Run: `psql -h 127.0.0.1 -p 54322 -U postgres -d postgres -c "SELECT column_name, data_type, column_default FROM information_schema.columns WHERE table_name = 'unified_tasks' AND column_name IN ('phase', 'dedup_key', 'subscribers', 'error_code');"`
Expected: 4 rows showing the new columns

**Step 4: Commit**

```bash
git add supabase/migrations/084_task_orchestrator.sql
git commit -m "feat: add TaskOrchestrator columns to unified_tasks (phase, dedup_key, subscribers, error_code)"
```

---

## Task 2: TaskOrchestrator Core Module — Phase Enum + Transitions + Error Codes

**Files:**
- Create: `backend/app/services/task_orchestrator.py`

**Step 1: Create the TaskOrchestrator module with phase enum, transitions, and error codes**

Create `backend/app/services/task_orchestrator.py`:

```python
"""
TaskOrchestrator — centralized Celery task lifecycle management.

Provides:
- State machine: TaskPhase enum + VALID_TRANSITIONS
- Dedup: Redis SET NX lock per (task_type, dedup_key)
- Multi-user subscription: subscribers JSONB in unified_tasks
- Error classification: structured error codes
"""

import json
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from loguru import logger


class TaskPhase(str, Enum):
    """Fine-grained task lifecycle phases."""
    QUEUED = "queued"
    DEDUP_CHECK = "dedup_check"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Legal state transitions
VALID_TRANSITIONS: dict[TaskPhase, set[TaskPhase]] = {
    TaskPhase.QUEUED:      {TaskPhase.DEDUP_CHECK, TaskPhase.CANCELLED},
    TaskPhase.DEDUP_CHECK: {TaskPhase.PROCESSING, TaskPhase.COMPLETED, TaskPhase.FAILED},
    TaskPhase.PROCESSING:  {TaskPhase.COMPLETED, TaskPhase.FAILED, TaskPhase.CANCELLED},
    TaskPhase.COMPLETED:   set(),
    TaskPhase.FAILED:      {TaskPhase.QUEUED},  # retry
    TaskPhase.CANCELLED:   set(),
}

# Dedup key generators per task type
DEDUP_KEY_FIELDS: dict[str, str] = {
    "download": "platform_id",
    "transcode": "version_id",
    "ai_extract": "platform_id",
    "ai_transcription": "platform_id",
    "ai_summary": "platform_id",
}

# Error classification
ERROR_CODES: dict[str, dict] = {
    "NETWORK_TIMEOUT": {
        "message": "Network timeout",
        "retryable": True,
    },
    "RESOURCE_404": {
        "message": "Source deleted or unavailable",
        "retryable": False,
    },
    "STORAGE_FULL": {
        "message": "Storage full",
        "retryable": False,
    },
    "RATE_LIMITED": {
        "message": "Rate limited",
        "retryable": True,
        "auto_retry": True,
    },
    "TRANSCODE_FAILED": {
        "message": "Transcode failed: unsupported format",
        "retryable": False,
    },
    "AI_QUOTA_EXCEEDED": {
        "message": "AI quota exceeded",
        "retryable": False,
    },
    "UNKNOWN": {
        "message": "Unexpected error",
        "retryable": True,
    },
}

# Redis lock TTL
DEDUP_LOCK_TTL = 3600  # 1 hour
LOCK_RENEWAL_INTERVAL = 600  # 10 minutes


class TaskOrchestrator:
    """Centralized task lifecycle manager with dedup and multi-user subscription."""

    def __init__(self):
        self._lock_renewals: dict[str, float] = {}  # task_id -> last_renewal_time

    async def _get_client(self):
        from app.db.supabase_client import get_async_supabase_admin
        return await get_async_supabase_admin()

    def _get_redis(self):
        """Get Redis client from Celery backend."""
        from app.celery_app import celery_app
        return celery_app.backend.client

    # ─── Phase transitions ──────────────────────────────

    async def transition(
        self,
        task_id: str,
        new_phase: TaskPhase,
        *,
        error_code: Optional[str] = None,
    ):
        """Transition task to new phase with validation."""
        client = await self._get_client()
        result = await (
            client.table("unified_tasks")
            .select("phase")
            .eq("id", task_id)
            .single()
            .execute()
        )
        if not result.data:
            logger.warning(f"[Orchestrator] Task {task_id} not found for transition")
            return

        current = TaskPhase(result.data.get("phase", "queued"))
        allowed = VALID_TRANSITIONS.get(current, set())

        if new_phase not in allowed:
            logger.warning(
                f"[Orchestrator] Invalid transition {current} → {new_phase} "
                f"for task {task_id}. Allowed: {allowed}"
            )
            return

        updates: dict = {"phase": new_phase.value}
        if error_code:
            updates["error_code"] = error_code

        # Sync status field for backward compatibility
        if new_phase == TaskPhase.PROCESSING:
            updates["status"] = "processing"
            updates["started_at"] = datetime.now(timezone.utc).isoformat()
        elif new_phase == TaskPhase.COMPLETED:
            updates["status"] = "completed"
            updates["progress"] = 100
            updates["completed_at"] = datetime.now(timezone.utc).isoformat()
        elif new_phase == TaskPhase.FAILED:
            updates["status"] = "failed"
            updates["completed_at"] = datetime.now(timezone.utc).isoformat()
        elif new_phase == TaskPhase.CANCELLED:
            updates["status"] = "cancelled"

        await client.table("unified_tasks").update(updates).eq("id", task_id).execute()
        logger.debug(f"[Orchestrator] {task_id}: {current} → {new_phase}")

    # ─── Dedup: acquire or subscribe ────────────────────

    def make_dedup_key(self, task_type: str, identifier: str) -> str:
        """Build dedup lock key."""
        return f"task:{task_type}:{identifier}"

    async def acquire_or_subscribe(
        self,
        task_type: str,
        dedup_identifier: str,
        user_id: str,
        resource_id: str,
    ) -> dict:
        """Try to acquire dedup lock. If locked, subscribe to existing task.

        Returns:
            {"action": "created", "task_id": ..., "dedup_key": ...}
            or {"action": "subscribed", "task_id": ..., "dedup_key": ...}
            or {"action": "completed", "task_id": ..., "dedup_key": ...}  # already done
        """
        redis = self._get_redis()
        dedup_key = self.make_dedup_key(task_type, dedup_identifier)

        # Try atomic lock
        acquired = redis.set(dedup_key, "", nx=True, ex=DEDUP_LOCK_TTL)

        if acquired:
            # First requester: create task
            return {"action": "created", "dedup_key": dedup_key}

        # Lock exists: another task is running
        # Find the existing task by dedup_key
        client = await self._get_client()
        existing = await (
            client.table("unified_tasks")
            .select("id, phase, subscribers")
            .eq("dedup_key", dedup_key)
            .in_("phase", ["queued", "dedup_check", "processing"])
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )

        if not existing.data:
            # Lock exists but no active task — check if completed recently
            completed = await (
                client.table("unified_tasks")
                .select("id, phase")
                .eq("dedup_key", dedup_key)
                .eq("phase", "completed")
                .order("completed_at", desc=True)
                .limit(1)
                .execute()
            )
            if completed.data:
                return {
                    "action": "completed",
                    "task_id": completed.data[0]["id"],
                    "dedup_key": dedup_key,
                }
            # Stale lock, force acquire
            redis.set(dedup_key, "", ex=DEDUP_LOCK_TTL)
            return {"action": "created", "dedup_key": dedup_key}

        existing_task = existing.data[0]
        task_id = existing_task["id"]

        # Add subscriber
        subscribers = existing_task.get("subscribers") or []
        subscriber = {
            "user_id": user_id,
            "resource_id": resource_id,
            "subscribed_at": datetime.now(timezone.utc).isoformat(),
        }
        subscribers.append(subscriber)

        await (
            client.table("unified_tasks")
            .update({"subscribers": subscribers})
            .eq("id", task_id)
            .execute()
        )

        logger.info(
            f"[Orchestrator] User {user_id[:8]} subscribed to existing "
            f"{task_type} task {task_id} (dedup: {dedup_key})"
        )

        return {
            "action": "subscribed",
            "task_id": str(task_id),
            "dedup_key": dedup_key,
        }

    # ─── Subscriber notification ────────────────────────

    async def notify_subscribers(
        self,
        task_id: str,
        *,
        success: bool = True,
        error_code: Optional[str] = None,
    ):
        """Notify all subscribers of task completion/failure.

        Updates each subscriber's resource record with the result.
        """
        client = await self._get_client()
        result = await (
            client.table("unified_tasks")
            .select("subscribers, task_type, media_id, metadata")
            .eq("id", task_id)
            .single()
            .execute()
        )
        if not result.data:
            return

        subscribers = result.data.get("subscribers") or []
        if not subscribers:
            return

        task_type = result.data.get("task_type")
        media_id = result.data.get("media_id")

        from app.repositories.resources_repository import ResourcesRepository
        res_repo = ResourcesRepository()

        for sub in subscribers:
            sub_resource_id = sub.get("resource_id")
            if not sub_resource_id:
                continue

            try:
                if success and task_type == "download":
                    # Copy completed download statuses + file paths to subscriber's resource
                    await self._copy_download_result_to_resource(
                        res_repo, sub_resource_id, media_id
                    )
                elif not success:
                    # Mark subscriber's resource as failed
                    await self._mark_resource_failed(
                        res_repo, sub_resource_id, task_type
                    )
            except Exception as e:
                logger.warning(
                    f"[Orchestrator] Failed to notify subscriber "
                    f"{sub.get('user_id', '?')[:8]}: {e}"
                )

        logger.info(
            f"[Orchestrator] Notified {len(subscribers)} subscribers "
            f"for task {task_id} (success={success})"
        )

    async def _copy_download_result_to_resource(
        self, res_repo, resource_id: str, media_id: str
    ):
        """Copy download result from global parsed_media to subscriber's resource."""
        from app.repositories.media_repository import MediaRepository
        media_repo = MediaRepository()
        media = await media_repo.get_by_id(media_id)
        if not media:
            return

        updates = {}
        if media.get("video_download_status") == "completed":
            updates["video_download_status"] = "completed"
        if media.get("music_download_status") == "completed":
            updates["music_download_status"] = "completed"
        if media.get("cover_download_status") == "completed":
            updates["cover_download_status"] = "completed"
        if media.get("image_download_status") == "completed":
            updates["image_download_status"] = "completed"
        if media.get("download_path"):
            updates["file_path"] = media["download_path"]
        if media.get("cover_download_path"):
            updates["cover_image_path"] = media["cover_download_path"]

        if updates:
            await res_repo.update_resource(resource_id, updates)

    async def _mark_resource_failed(self, res_repo, resource_id: str, task_type: str):
        """Mark subscriber's resource download statuses as failed."""
        if task_type == "download":
            await res_repo.update_download_status(resource_id, {
                "video_download_status": "failed",
                "cover_download_status": "failed",
            })

    # ─── Lock management ────────────────────────────────

    def renew_lock(self, dedup_key: str):
        """Renew dedup lock TTL. Call periodically during long tasks."""
        now = time.time()
        last = self._lock_renewals.get(dedup_key, 0)
        if now - last < LOCK_RENEWAL_INTERVAL:
            return
        self._lock_renewals[dedup_key] = now

        redis = self._get_redis()
        redis.expire(dedup_key, DEDUP_LOCK_TTL)

    def release_lock(self, dedup_key: str):
        """Release dedup lock after task completes."""
        redis = self._get_redis()
        redis.delete(dedup_key)
        self._lock_renewals.pop(dedup_key, None)

    # ─── Error classification ───────────────────────────

    @staticmethod
    def classify_error(exception: Exception) -> str:
        """Classify exception into an error code."""
        error_str = str(exception).lower()

        if "timeout" in error_str or "timed out" in error_str:
            return "NETWORK_TIMEOUT"
        if "404" in error_str or "not found" in error_str:
            return "RESOURCE_404"
        if "no space" in error_str or "disk full" in error_str or "storage" in error_str:
            return "STORAGE_FULL"
        if "rate limit" in error_str or "429" in error_str or "too many" in error_str:
            return "RATE_LIMITED"
        if "transcode" in error_str or "ffmpeg" in error_str or "codec" in error_str:
            return "TRANSCODE_FAILED"
        if "quota" in error_str or "limit exceeded" in error_str:
            return "AI_QUOTA_EXCEEDED"

        return "UNKNOWN"


# ─── Singleton ──────────────────────────────────────

_orchestrator: Optional[TaskOrchestrator] = None


def get_orchestrator() -> TaskOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = TaskOrchestrator()
    return _orchestrator
```

**Step 2: Verify import works**

Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/backend && uv run python -c "from app.services.task_orchestrator import TaskOrchestrator, TaskPhase, get_orchestrator; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add backend/app/services/task_orchestrator.py
git commit -m "feat: add TaskOrchestrator core — phase state machine, dedup lock, subscriber notification, error classification"
```

---

## Task 3: Celery Signals — Automatic Phase Transitions

**Files:**
- Create: `backend/app/tasks/signals.py`
- Modify: `backend/app/celery_app.py:17-20` — register signals module

**Step 1: Create Celery signals handler**

Create `backend/app/tasks/signals.py`:

```python
"""
Celery signal handlers for TaskOrchestrator integration.

Automatically transitions task phases on prerun/success/failure events.
Only applies to tasks that have an orchestrator-managed unified_task_id.
"""

import asyncio
from typing import Optional

from celery.signals import task_prerun, task_success, task_failure
from loguru import logger


def _run_async(coro):
    """Run async coroutine from sync Celery context."""
    try:
        loop = asyncio.get_running_loop()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


def _get_unified_task_id(kwargs: dict) -> Optional[str]:
    """Extract unified_task_id from task kwargs if present."""
    return kwargs.get("_unified_task_id")


def _get_dedup_key(kwargs: dict) -> Optional[str]:
    """Extract dedup_key from task kwargs if present."""
    return kwargs.get("_dedup_key")


@task_prerun.connect
def on_task_prerun(sender=None, task_id=None, task=None, args=None, kwargs=None, **kw):
    """Called just before a task is executed by a worker."""
    if not kwargs:
        return
    unified_id = _get_unified_task_id(kwargs)
    dedup_key = _get_dedup_key(kwargs)
    if not unified_id:
        return

    try:
        from app.services.task_orchestrator import TaskPhase, get_orchestrator
        orchestrator = get_orchestrator()
        _run_async(orchestrator.transition(unified_id, TaskPhase.PROCESSING))
        if dedup_key:
            orchestrator.renew_lock(dedup_key)
        logger.debug(f"[Signal/prerun] Task {task_id} → PROCESSING")
    except Exception as e:
        logger.warning(f"[Signal/prerun] Failed for {task_id}: {e}")


@task_success.connect
def on_task_success(sender=None, result=None, **kwargs):
    """Called when a task completes successfully."""
    task_kwargs = getattr(sender.request, "kwargs", {}) or {}
    unified_id = _get_unified_task_id(task_kwargs)
    dedup_key = _get_dedup_key(task_kwargs)
    if not unified_id:
        return

    try:
        from app.services.task_orchestrator import TaskPhase, get_orchestrator
        orchestrator = get_orchestrator()
        _run_async(orchestrator.transition(unified_id, TaskPhase.COMPLETED))
        _run_async(orchestrator.notify_subscribers(unified_id, success=True))
        if dedup_key:
            orchestrator.release_lock(dedup_key)
        logger.debug(f"[Signal/success] Task {sender.request.id} → COMPLETED")
    except Exception as e:
        logger.warning(f"[Signal/success] Failed for {sender.request.id}: {e}")


@task_failure.connect
def on_task_failure(sender=None, task_id=None, exception=None, **kwargs):
    """Called when a task fails."""
    task_kwargs = getattr(sender.request, "kwargs", {}) or {}
    unified_id = _get_unified_task_id(task_kwargs)
    dedup_key = _get_dedup_key(task_kwargs)
    if not unified_id:
        return

    try:
        from app.services.task_orchestrator import TaskPhase, get_orchestrator
        orchestrator = get_orchestrator()
        error_code = orchestrator.classify_error(exception) if exception else "UNKNOWN"
        _run_async(orchestrator.transition(
            unified_id, TaskPhase.FAILED, error_code=error_code
        ))
        _run_async(orchestrator.notify_subscribers(
            unified_id, success=False, error_code=error_code
        ))
        if dedup_key:
            orchestrator.release_lock(dedup_key)
        logger.debug(f"[Signal/failure] Task {task_id} → FAILED ({error_code})")
    except Exception as e:
        logger.warning(f"[Signal/failure] Failed for {task_id}: {e}")
```

**Step 2: Register signals module in celery_app.py**

Modify `backend/app/celery_app.py`. After line 27 (`include=_task_modules,`), the signals module needs to be imported so its decorators register. Add an import after the `celery_app` definition (after line 28):

```python
# Register signal handlers (decorators auto-connect on import)
import app.tasks.signals  # noqa: F401, E402
```

**Step 3: Verify import works**

Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/backend && uv run python -c "from app.tasks.signals import on_task_prerun; print('OK')"`
Expected: `OK`

**Step 4: Commit**

```bash
git add backend/app/tasks/signals.py backend/app/celery_app.py
git commit -m "feat: add Celery signal handlers for automatic TaskOrchestrator phase transitions"
```

---

## Task 4: Integrate Orchestrator into Download Flow (Batch 1)

**Files:**
- Modify: `backend/app/api/media_router.py:290-318` — use orchestrator.acquire_or_subscribe
- Modify: `backend/app/tasks/download_tasks.py:412-470` — pass _unified_task_id and _dedup_key to task kwargs

This is the critical integration task. The orchestrator wraps the existing flow without replacing it.

**Step 1: Modify media_router.py — Add dedup check before dispatch**

In `backend/app/api/media_router.py`, after line 297 (`if need_download:`), insert orchestrator dedup check before dispatching Celery task. The modified section (lines ~298-318) becomes:

```python
        if need_download:
            # ── Orchestrator dedup check ──
            orchestrator_result = None
            dedup_key = None
            try:
                from app.services.task_orchestrator import get_orchestrator
                orchestrator = get_orchestrator()
                orchestrator_result = await orchestrator.acquire_or_subscribe(
                    task_type="download",
                    dedup_identifier=platform_id,
                    user_id=auth.user_id,
                    resource_id=resource_id,
                )
                dedup_key = orchestrator_result.get("dedup_key")

                if orchestrator_result["action"] == "subscribed":
                    # Another user is already downloading this — subscribed
                    logger.info(
                        f"[Orchestrator] Subscribed to existing download for {platform_id}"
                    )
                    download_task_id = f"subscribed:{orchestrator_result['task_id']}"
                    need_download = False  # Skip dispatch
                elif orchestrator_result["action"] == "completed":
                    # Already completed by another task
                    logger.info(
                        f"[Orchestrator] Download already completed for {platform_id}"
                    )
                    download_task_id = f"completed:{orchestrator_result['task_id']}"
                    need_download = False
            except Exception as e:
                logger.warning(f"[Orchestrator] Dedup check failed, proceeding normally: {e}")

            # ── Dispatch Celery task (only if not deduped) ──
            if need_download:
                try:
                    from app.tasks.download_tasks import download_unified_task
                    from app.services.system_monitor_service import check_worker_ready

                    ready, err_msg = check_worker_ready()
                    if not ready:
                        raise RuntimeError(err_msg)

                    download_task = download_unified_task.delay(
                        platform_id=platform_id,
                        user_id=auth.user_id,
                        download_video=need_download_video,
                        download_music=request.music_bool,
                        download_cover=True,
                        media_type=media_type,
                        video_title=video_title[:50] if video_title else "undefined",
                        resource_id=resource_id,
                        _dedup_key=dedup_key,
                    )
                    download_task_id = download_task.id
                    logger.info(f"Celery download task submitted: {download_task_id}")
                except Exception as celery_err:
                    # Existing fallback to FastAPI background tasks...
                    # (keep existing code from lines 320-356 unchanged)
```

**Step 2: Modify download_tasks.py — Pass orchestrator kwargs to unified task**

In `backend/app/tasks/download_tasks.py`, add `_dedup_key` and `_unified_task_id` params to the task function signature (line 412-424):

```python
@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def download_unified_task(
    self,
    platform_id: str,
    user_id: str,
    url: str = None,
    download_video: bool = True,
    download_music: bool = False,
    download_cover: bool = True,
    media_type: int = 0,
    video_title: str = "undefined",
    resource_id: str = None,
    _dedup_key: str = None,       # NEW: orchestrator dedup key
    _unified_task_id: str = None,  # NEW: orchestrator task ID (for signals)
):
```

Then after `unified_task_id` is created (line 468), store it back so signals can pick it up, and set dedup_key on the unified_tasks row:

```python
        # Store orchestrator metadata for Celery signals
        if unified_task_id and _dedup_key:
            try:
                from app.services.task_orchestrator import get_orchestrator, TaskPhase
                orchestrator = get_orchestrator()
                client = await orchestrator._get_client()
                await client.table("unified_tasks").update({
                    "dedup_key": _dedup_key,
                    "phase": TaskPhase.DEDUP_CHECK.value,
                }).eq("id", unified_task_id).execute()
            except Exception:
                pass
        # Make unified_task_id available to signals via kwargs
        self.request.kwargs["_unified_task_id"] = unified_task_id
```

Note: Since `download_unified_task` uses `run_async()` for async calls, the dedup_key update should also use `run_async()`.

**Step 3: Verify backend starts without errors**

Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/backend && uv run python -c "from app.api.media_router import router; print('OK')"`
Expected: `OK`

**Step 4: Commit**

```bash
git add backend/app/api/media_router.py backend/app/tasks/download_tasks.py
git commit -m "feat: integrate TaskOrchestrator into download flow — dedup check + subscriber notification"
```

---

## Task 5: Frontend Types — Add Phase and Error Code

**Files:**
- Modify: `frontend/contexts/TaskManagerContext.tsx:19-43` — add phase, error_code, subscribers to UnifiedTask

**Step 1: Update TaskStatus and UnifiedTask types**

In `frontend/contexts/TaskManagerContext.tsx`, add `TaskPhase` type and extend `UnifiedTask` interface:

After line 19 (`export type TaskStatus = ...`), add:

```typescript
export type TaskPhase = 'queued' | 'dedup_check' | 'processing' | 'completed' | 'failed' | 'cancelled';
```

In the `UnifiedTask` interface (lines 21-43), add these fields after `updated_at` (line 42):

```typescript
  phase?: TaskPhase;
  dedup_key?: string;
  subscribers?: Array<{ user_id: string; resource_id: string; subscribed_at: string }>;
  error_code?: string;
```

**Step 2: Add phase display helpers**

After the `taskCategoryLabel` function (line 375), add:

```typescript
export function taskPhaseLabel(phase?: TaskPhase): string {
  switch (phase) {
    case 'queued': return 'Queued';
    case 'dedup_check': return 'Checking...';
    case 'processing': return 'Processing';
    case 'completed': return 'Done';
    case 'failed': return 'Failed';
    case 'cancelled': return 'Cancelled';
    default: return '';
  }
}

export function isRetryable(errorCode?: string): boolean {
  if (!errorCode) return true;
  const nonRetryable = ['RESOURCE_404', 'STORAGE_FULL', 'TRANSCODE_FAILED', 'AI_QUOTA_EXCEEDED'];
  return !nonRetryable.includes(errorCode);
}

export function errorCodeMessage(errorCode?: string): string {
  const messages: Record<string, string> = {
    'NETWORK_TIMEOUT': 'Network timeout, will auto-retry',
    'RESOURCE_404': 'Source deleted or unavailable',
    'STORAGE_FULL': 'Storage full, please free space',
    'RATE_LIMITED': 'Rate limited, retrying...',
    'TRANSCODE_FAILED': 'Transcode failed: unsupported format',
    'AI_QUOTA_EXCEEDED': 'AI quota exceeded',
    'UNKNOWN': 'Unexpected error',
  };
  return messages[errorCode || ''] || errorCode || 'Unknown error';
}
```

**Step 3: Verify frontend build**

Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/frontend && npm run build`
Expected: Build succeeds

**Step 4: Commit**

```bash
git add frontend/contexts/TaskManagerContext.tsx
git commit -m "feat: add TaskPhase type, error code helpers to frontend TaskManagerContext"
```

---

## Task 6: Frontend — Display Phase and Error Code in TaskCenterPanel

**Files:**
- Modify: `frontend/components/TaskCenterPanel.tsx` — show phase badge, error code, conditional retry button

**Step 1: Find TaskCenterPanel and identify display locations**

Read `frontend/components/TaskCenterPanel.tsx` to identify where task status is displayed. Modify the status display to show `phase` when available, and show `error_code` message for failed tasks.

Key changes:
1. Import `taskPhaseLabel`, `isRetryable`, `errorCodeMessage` from TaskManagerContext
2. Show phase badge next to status (e.g., "Checking..." with pulse animation during dedup_check)
3. For failed tasks, show `errorCodeMessage(task.error_code)` instead of raw `error_msg`
4. Conditionally show retry button: only when `isRetryable(task.error_code)` is true

**Step 2: Verify frontend build**

Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/frontend && npm run build`
Expected: Build succeeds

**Step 3: Commit**

```bash
git add frontend/components/TaskCenterPanel.tsx
git commit -m "feat: display task phase and structured error codes in TaskCenterPanel"
```

---

## Task 7: Integration Test — End-to-End Dedup Verification

**Files:**
- No new files — manual verification against running local services

**Step 1: Start backend**

Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/backend && uv run uvicorn app.main:app --reload --port 8081`

**Step 2: Start frontend**

Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/frontend && npm run dev -- --port 5176`

**Step 3: Verify new columns in DB**

Run: `psql -h 127.0.0.1 -p 54322 -U postgres -d postgres -c "SELECT id, phase, dedup_key, subscribers, error_code FROM unified_tasks ORDER BY created_at DESC LIMIT 5;"`
Expected: New columns visible (all null/default for existing rows)

**Step 4: Verify frontend build passes**

Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/frontend && npm run build`
Expected: Build succeeds with 0 TypeScript errors

**Step 5: Verify backend imports**

Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/backend && uv run python -c "from app.services.task_orchestrator import get_orchestrator; from app.tasks.signals import on_task_prerun; print('All imports OK')"`
Expected: `All imports OK`

---

## Task 8: AI Tasks Integration (Batch 2)

**Files:**
- Modify: `backend/app/tasks/ai_tasks.py:444-550` — add dedup to chain_ai_pipeline

**Step 1: Add dedup to AI pipeline chain function**

In `backend/app/tasks/ai_tasks.py`, modify `chain_ai_pipeline()` to check orchestrator before dispatching:

```python
def chain_ai_pipeline(
    platform_id: str,
    user_id: str,
    resource_id: str = None,
    transcript_bool: bool = True,
    summary_bool: bool = True,
):
    """Chain AI pipeline tasks with dedup check."""
    # Dedup check for AI extract (the first task in chain)
    try:
        from app.services.task_orchestrator import get_orchestrator
        orchestrator = get_orchestrator()
        result = asyncio.run(orchestrator.acquire_or_subscribe(
            task_type="ai_extract",
            dedup_identifier=platform_id,
            user_id=user_id,
            resource_id=resource_id or "",
        ))
        if result["action"] == "subscribed":
            logger.info(f"[AI] Already processing {platform_id}, subscribed")
            return
        if result["action"] == "completed":
            logger.info(f"[AI] Already completed for {platform_id}")
            return
    except Exception as e:
        logger.warning(f"[AI] Dedup check failed, proceeding: {e}")

    # ... existing chain logic continues unchanged ...
```

**Step 2: Pass _dedup_key to AI task kwargs**

For each AI task in the chain, add `_dedup_key` kwarg so signals can manage locks.

**Step 3: Verify backend import**

Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/backend && uv run python -c "from app.tasks.ai_tasks import chain_ai_pipeline; print('OK')"`
Expected: `OK`

**Step 4: Commit**

```bash
git add backend/app/tasks/ai_tasks.py
git commit -m "feat: integrate TaskOrchestrator dedup into AI pipeline (Batch 2)"
```

---

## Task 9: Transcode Integration (Batch 3)

**Files:**
- Modify: `backend/app/tasks/transcode_tasks.py:134-155` — add dedup to maybe_trigger_transcode

**Step 1: Add dedup check to transcode trigger**

In `backend/app/tasks/transcode_tasks.py`, modify `maybe_trigger_transcode()`:

```python
def maybe_trigger_transcode(resource_id: str, version_id: str, mime_type: str, user_id: str = None):
    """Trigger HLS transcode with dedup check."""
    if not mime_type or not mime_type.startswith("video/"):
        return

    # Dedup check
    try:
        import asyncio
        from app.services.task_orchestrator import get_orchestrator
        orchestrator = get_orchestrator()
        result = asyncio.run(orchestrator.acquire_or_subscribe(
            task_type="transcode",
            dedup_identifier=version_id,
            user_id=user_id or "",
            resource_id=resource_id,
        ))
        if result["action"] in ("subscribed", "completed"):
            logger.info(f"[Transcode] Dedup hit for version {version_id}")
            return
        dedup_key = result.get("dedup_key")
    except Exception as e:
        logger.warning(f"[Transcode] Dedup check failed: {e}")
        dedup_key = None

    # ... existing dispatch logic, pass _dedup_key=dedup_key to task.delay() ...
```

**Step 2: Verify backend import**

Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/backend && uv run python -c "from app.tasks.transcode_tasks import maybe_trigger_transcode; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add backend/app/tasks/transcode_tasks.py
git commit -m "feat: integrate TaskOrchestrator dedup into transcode flow (Batch 3)"
```

---

## Task 10: Scheduled Task — Stale Lock Cleanup

**Files:**
- Modify: `backend/app/tasks/scheduled_tasks.py` — add orphaned lock recovery task
- Modify: `backend/app/celery_app.py:55-84` — add beat schedule entry

**Step 1: Add stale lock recovery task**

In `backend/app/tasks/scheduled_tasks.py`, add:

```python
@shared_task(name="app.tasks.scheduled_tasks.recover_stale_orchestrator_locks")
def recover_stale_orchestrator_locks():
    """Recover orphaned dedup locks by checking unified_tasks.

    Scans for tasks stuck in 'processing' phase for more than 1 hour
    and marks them as failed.
    """
    from app.services.task_orchestrator import get_orchestrator, TaskPhase

    async def _recover():
        orchestrator = get_orchestrator()
        client = await orchestrator._get_client()

        # Find tasks stuck in processing for >1 hour
        from datetime import datetime, timedelta, timezone
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

        stale = await (
            client.table("unified_tasks")
            .select("id, dedup_key")
            .eq("phase", "processing")
            .lt("started_at", cutoff)
            .execute()
        )

        for task in (stale.data or []):
            try:
                await orchestrator.transition(
                    task["id"], TaskPhase.FAILED, error_code="NETWORK_TIMEOUT"
                )
                if task.get("dedup_key"):
                    orchestrator.release_lock(task["dedup_key"])
                logger.info(f"[Recovery] Marked stale task {task['id']} as failed")
            except Exception as e:
                logger.warning(f"[Recovery] Failed to recover task {task['id']}: {e}")

    import asyncio
    asyncio.run(_recover())
```

**Step 2: Add beat schedule entry in celery_app.py**

In `backend/app/celery_app.py`, add to `beat_schedule` dict (after line 83):

```python
        "recover-stale-orchestrator-locks-hourly": {
            "task": "app.tasks.scheduled_tasks.recover_stale_orchestrator_locks",
            "schedule": 3600.0,  # Every hour
        },
```

**Step 3: Verify**

Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/backend && uv run python -c "from app.tasks.scheduled_tasks import recover_stale_orchestrator_locks; print('OK')"`
Expected: `OK`

**Step 4: Commit**

```bash
git add backend/app/tasks/scheduled_tasks.py backend/app/celery_app.py
git commit -m "feat: add scheduled recovery for stale orchestrator locks"
```

---

## Task 11: Final Verification

**Step 1: Full frontend build**

Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/frontend && npm run build`
Expected: Build succeeds

**Step 2: Full backend import check**

Run: `cd /Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/backend && uv run python -c "from app.services.task_orchestrator import get_orchestrator, TaskPhase, VALID_TRANSITIONS, ERROR_CODES; from app.tasks.signals import on_task_prerun, on_task_success, on_task_failure; print('All OK')"`
Expected: `All OK`

**Step 3: Verify DB schema**

Run: `psql -h 127.0.0.1 -p 54322 -U postgres -d postgres -c "SELECT column_name FROM information_schema.columns WHERE table_name = 'unified_tasks' ORDER BY ordinal_position;" | grep -E "phase|dedup|subscribers|error_code"`
Expected: All 4 columns present

**Step 4: Git log review**

Run: `git log --oneline -10`
Expected: 10 clean commits for Tasks 1-10
