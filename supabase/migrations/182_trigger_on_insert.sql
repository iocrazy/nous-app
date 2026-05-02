-- 182_trigger_on_insert.sql
--
-- D8-A follow-up: trg_mirror_dbos_lifecycle (installed in 180) only
-- fires on AFTER UPDATE OF status/error/started_at_epoch_ms/updated_at.
--
-- Observed in dev: a workflow can complete (dbos.workflow_status row
-- shows SUCCESS) while task_tracking stays at status=pending. Two
-- plausible races:
--   (a) DBOS writes the workflow_status row directly with a terminal
--       status via INSERT (no UPDATE → trigger never fires).
--   (b) The UPDATE arrives before the application has finished its
--       pre-create INSERT into task_tracking, so the UPDATE matches
--       0 rows and is silently lost.
--
-- Fix: also fire on INSERT. The trigger function's UPDATE … WHERE
-- dbos_workflow_id = NEW.workflow_uuid is no-op-safe when the
-- task_tracking row doesn't exist yet — and as soon as it does, any
-- subsequent UPDATE on workflow_status will sync it.
--
-- For race (b) we additionally need the application's pre-create to
-- backfill from workflow_status if a SUCCESS/ERROR slipped through
-- before the INSERT — that lives in unified_task_manager.create()
-- (separate change, not in this migration).

BEGIN;

DROP TRIGGER IF EXISTS trg_mirror_dbos_lifecycle ON dbos.workflow_status;

CREATE TRIGGER trg_mirror_dbos_lifecycle
  AFTER INSERT OR UPDATE OF status, error, started_at_epoch_ms, updated_at
  ON dbos.workflow_status
  FOR EACH ROW
  EXECUTE FUNCTION public.mirror_dbos_lifecycle_to_tracking();

COMMIT;
