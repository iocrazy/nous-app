-- 181_drop_task_tracking_fk.sql
--
-- Hotfix for D8-A regression. Migration 180 added a FK from
-- task_tracking.dbos_workflow_id → dbos.workflow_status(workflow_uuid).
-- This created an unsolvable race: the application pre-creates the
-- task_tracking row (with friendly title) BEFORE calling
-- DBOS.start_workflow, so dbos.workflow_status doesn't have that
-- workflow_uuid yet → FK violation → row never created → frontend
-- Task Center stays empty.
--
-- The trigger trg_mirror_dbos_lifecycle on dbos.workflow_status
-- (installed in 180) only fires on UPDATE — INSERT happens too early
-- to be useful, and we'd have to derive title/user_id from inside
-- DBOS metadata which is fragile.
--
-- Decision: drop the FK. Consistency moves to the application layer.
-- Stale task_tracking rows whose DBOS workflow has been garbage-
-- collected (DBOS retention is ~90 days by default) become orphans;
-- a separate cleanup job will gc them periodically.
--
-- Trigger stays — it still works whenever the task_tracking row
-- happens to exist before a DBOS workflow_status UPDATE arrives, which
-- is the normal case after pre-create.

BEGIN;

ALTER TABLE public.task_tracking DROP CONSTRAINT IF EXISTS task_tracking_dbos_fk;

COMMENT ON TABLE public.task_tracking IS
  'User-facing sidecar of dbos.workflow_status (1:1 by dbos_workflow_id,
   no FK — application enforces consistency). DBOS owns execution truth
   (status/started_at/error in dbos.workflow_status); the
   trg_mirror_dbos_lifecycle trigger auto-syncs lifecycle fields HERE
   for Supabase Realtime convenience. Application code only writes
   title/subtitle/progress/phase/metadata/business-id columns — never
   touches status/started_at/completed_at/error_msg directly. Renamed
   from unified_tasks in migration 180; FK dropped in 181.';

COMMIT;
