-- ============================================================
-- Prevent TOCTOU double-dispatch for unified_tasks.
--
-- The check-then-insert pattern in ai_router / media_router could
-- let two concurrent requests both pass the "no active task" check
-- and both dispatch duplicate work (and in the past, double-bill).
--
-- This partial unique index makes the insert fail at the DB layer
-- when an active task for the same (resource_id, task_type) exists.
-- Callers should catch unique-violation and treat it as "already
-- in progress".
-- ============================================================

CREATE UNIQUE INDEX IF NOT EXISTS idx_unified_tasks_active_per_resource_type
    ON public.unified_tasks (resource_id, task_type)
    WHERE status IN ('pending', 'queued', 'processing', 'running');
