-- 209_ensure_dbos_lifecycle_trigger.sql
--
-- Reinstall trg_mirror_dbos_lifecycle on dbos.workflow_status — same
-- contract as 180/182, but defensive about ordering so it works against
-- a fresh DB where DBOS initialised its schema AFTER the migrations
-- ran.
--
-- ── Why this is needed ──────────────────────────────────────────────
-- 2026-05-07: ES256 cutover migrated user data sb-mediahub →
-- mediahub-sb-prod. The dbos schema didn't come along — it gets
-- materialised by the DBOS SDK at backend launch on first run, which
-- happened LATER than supabase migrations 180/182/184 ran on the new
-- DB. Those migrations executed against a DB where dbos.workflow_status
-- didn't exist, so:
--   - 182's `CREATE TRIGGER … ON dbos.workflow_status` would have
--     raised "relation does not exist" and rolled back, but the
--     migration was marked applied anyway (depending on runner).
--   - Result: dev had the trigger (dbos was bootstrapped before
--     migrations) but prod silently didn't.
--
-- Visible symptom: media fetch raised RuntimeError → DBOS marked the
-- workflow ERROR → task_tracking row stayed phase='queued'
-- subtitle='Initializing…' forever, because nothing mirrored the
-- terminal state into task_tracking. UI showed "still parsing" while
-- the workflow had been dead for 14 minutes.
--
-- ── Idempotent guard ────────────────────────────────────────────────
-- DO block instead of bare CREATE so this is safe against:
--   - DB where dbos.workflow_status doesn't exist yet
--     (skip with NOTICE; on fresh deploy run again after DBOS init).
--   - DB where the trigger already exists (DROP + CREATE is no-op).
-- The trigger spec is identical to 182.

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'dbos' AND table_name = 'workflow_status'
  ) THEN
    DROP TRIGGER IF EXISTS trg_mirror_dbos_lifecycle ON dbos.workflow_status;

    CREATE TRIGGER trg_mirror_dbos_lifecycle
      AFTER INSERT OR UPDATE OF status, error, started_at_epoch_ms, updated_at
      ON dbos.workflow_status
      FOR EACH ROW
      EXECUTE FUNCTION public.mirror_dbos_lifecycle_to_tracking();

    RAISE NOTICE 'trg_mirror_dbos_lifecycle installed on dbos.workflow_status';
  ELSE
    RAISE NOTICE 'dbos.workflow_status does not exist yet — skipping trigger install. Re-run this migration after DBOS schema is materialised (i.e. after first backend launch with DBOS_DATABASE_URL set).';
  END IF;
END
$$;
