-- 180_task_tracking_rename.sql
--
-- Rename `unified_tasks` → `task_tracking` and re-anchor it as a 1:1 sidecar
-- of dbos.workflow_status. This is the core D8-A migration.
--
-- Why this rename + restructure:
--   * `unified_tasks` was named in the pre-DBOS era when it merged
--     video_tasks + download_tasks. The `unified` modifier no longer
--     reflects current architecture.
--   * After DBOS adoption (PR-D2..D7), every dispatched workflow has a
--     dbos_workflow_id (UUID). The legacy column `celery_task_id` was
--     repurposed to hold this value — confusing name, must be renamed.
--   * DBOS owns lifecycle truth (status/started_at/error in
--     dbos.workflow_status). This table mirrors lifecycle for
--     Supabase Realtime convenience and stores user-facing fields
--     (title/subtitle/progress/phase) that DBOS doesn't carry.
--
-- Schema changes:
--   1. RENAME table unified_tasks → task_tracking
--   2. RENAME column celery_task_id → dbos_workflow_id
--   3. Drop BIGINT id PK; switch PK to dbos_workflow_id (TEXT — matches
--      dbos.workflow_status.workflow_uuid type)
--   4. Add FK dbos_workflow_id → dbos.workflow_status(workflow_uuid)
--      ON DELETE CASCADE (gc connected when DBOS row gc'd)
--   5. Add cost_cents column (AI billing surface — agent_runs has same data
--      but task-centric admin view wants it inline)
--   6. NOT adding parent_task_id — DBOS already tracks parent_workflow_id;
--      consumers can join dbos.workflow_status directly.
--
-- Lifecycle mirror (replaces app-layer @tracked_workflow):
--   7. Helper public.dbos_error_to_text(text) — friendly stringify the
--      pickled exception that DBOS stores in workflow_status.error.
--   8. Trigger function public.mirror_dbos_lifecycle_to_tracking() —
--      maps DBOS status to our 5-state task lifecycle and copies
--      started_at/completed_at/error_msg fields.
--   9. AFTER UPDATE trigger on dbos.workflow_status — fires on lifecycle
--      transitions and propagates to task_tracking.
--
-- Application contract after this migration:
--   * Router code dispatches: pre-create task_tracking row with title/
--     subtitle/business-id fields, then DBOS.start_workflow.
--   * Workflow code: only updates title/subtitle/progress/phase via
--     UnifiedTaskManager.update_progress (or equivalent helper).
--   * NEVER manually write status/started_at/completed_at/error_msg —
--     the trigger owns those.
--   * @tracked_workflow decorator is removed in app code (D8-A backend
--     cleanup commit).
--
-- Rollback note: this migration is destructive (column rename, PK swap,
-- table rename). Roll back via 181_revert_task_tracking.sql if needed.

BEGIN;

-- ================================================================
-- Step 0: drop dead task_assets (orphan from PR-D7 issues backfill)
-- ================================================================
-- task_assets was supposed to drop with project_tasks in migration 176
-- (its FK was task_id REFERENCES project_tasks ON DELETE CASCADE), but
-- the table itself survived. Its RLS policies wrongly reference
-- unified_tasks (clearly a copy-paste from project_tasks era), which
-- would block our column drop below. The table has zero callers in
-- backend/admin/frontend code and zero rows in dev — drop it.
DROP TABLE IF EXISTS public.task_assets CASCADE;

-- ================================================================
-- Step 1: rename table
-- ================================================================
ALTER TABLE public.unified_tasks RENAME TO task_tracking;

-- ================================================================
-- Step 2: rename celery_task_id → dbos_workflow_id (keep TEXT)
-- ================================================================
-- Existing values: all UUID strings (verified on dev — 132 rows, all match
-- /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/).
ALTER TABLE public.task_tracking RENAME COLUMN celery_task_id TO dbos_workflow_id;

-- Backfill any NULL values (legacy rows pre-pre-gen pattern) by deleting
-- them — they can't be associated with a DBOS workflow anyway.
DELETE FROM public.task_tracking WHERE dbos_workflow_id IS NULL;

ALTER TABLE public.task_tracking ALTER COLUMN dbos_workflow_id SET NOT NULL;

-- ================================================================
-- Step 3: swap PK from id (BIGINT) to dbos_workflow_id (TEXT)
-- ================================================================
ALTER TABLE public.task_tracking DROP CONSTRAINT unified_tasks_pkey;
ALTER TABLE public.task_tracking DROP COLUMN id;
ALTER TABLE public.task_tracking ADD PRIMARY KEY (dbos_workflow_id);

-- ================================================================
-- Step 4: FK to dbos.workflow_status (cascade gc)
-- ================================================================
-- Safety net: backfill any task_tracking row whose dbos_workflow_id no
-- longer exists in dbos.workflow_status (rare — would indicate manual
-- DBOS sys-DB cleanup). Drop the orphan row so the FK doesn't reject.
DELETE FROM public.task_tracking
WHERE NOT EXISTS (
  SELECT 1 FROM dbos.workflow_status ws
  WHERE ws.workflow_uuid = public.task_tracking.dbos_workflow_id
);

ALTER TABLE public.task_tracking
  ADD CONSTRAINT task_tracking_dbos_fk
  FOREIGN KEY (dbos_workflow_id)
  REFERENCES dbos.workflow_status(workflow_uuid)
  ON DELETE CASCADE
  DEFERRABLE INITIALLY IMMEDIATE;

-- ================================================================
-- Step 5: add cost_cents column
-- ================================================================
ALTER TABLE public.task_tracking ADD COLUMN cost_cents INT NOT NULL DEFAULT 0;

-- ================================================================
-- Step 6: rename indexes (cosmetic but keeps DDL grep'able)
-- ================================================================
ALTER INDEX idx_unified_tasks_active_per_resource_type
  RENAME TO idx_task_tracking_active_per_resource_type;
ALTER INDEX idx_unified_tasks_dedup_key
  RENAME TO idx_task_tracking_dedup_key;
ALTER INDEX idx_unified_tasks_phase
  RENAME TO idx_task_tracking_phase;
ALTER INDEX idx_unified_tasks_type
  RENAME TO idx_task_tracking_type;
ALTER INDEX idx_unified_tasks_user_active
  RENAME TO idx_task_tracking_user_active;
ALTER INDEX idx_unified_tasks_user_status
  RENAME TO idx_task_tracking_user_status;
ALTER INDEX unified_tasks_issue_id_idx
  RENAME TO task_tracking_issue_id_idx;

-- ================================================================
-- Step 7: rename RLS policies
-- ================================================================
ALTER POLICY "unified_tasks_select" ON public.task_tracking RENAME TO "task_tracking_select";
ALTER POLICY "unified_tasks_insert" ON public.task_tracking RENAME TO "task_tracking_insert";
ALTER POLICY "unified_tasks_update" ON public.task_tracking RENAME TO "task_tracking_update";
ALTER POLICY "unified_tasks_delete" ON public.task_tracking RENAME TO "task_tracking_delete";

-- ================================================================
-- Step 8: replace publication entry for Supabase Realtime
-- ================================================================
-- Frontend Task Center subscribes to task_tracking via Realtime. The
-- subsequent trigger (step 11) keeps lifecycle fields fresh, so a single
-- channel on this table delivers everything the UI needs.
DO $$
BEGIN
  -- DROP if it was added (depends on previous publication state)
  PERFORM 1 FROM pg_publication_tables
  WHERE pubname = 'supabase_realtime' AND tablename = 'unified_tasks';
  IF FOUND THEN
    EXECUTE 'ALTER PUBLICATION supabase_realtime DROP TABLE public.unified_tasks';
  END IF;
EXCEPTION WHEN undefined_object THEN
  -- publication or table not present; safe to ignore
  NULL;
END $$;

ALTER PUBLICATION supabase_realtime ADD TABLE public.task_tracking;

-- Replication identity FULL so DELETE events carry the full row payload
-- (Realtime needs this for the frontend to know which row vanished).
ALTER TABLE public.task_tracking REPLICA IDENTITY FULL;

-- ================================================================
-- Step 9: dbos_error_to_text helper
-- ================================================================
-- DBOS persists workflow_status.error as a base64-encoded pickled
-- exception (TEXT column on their side). For UI display we want a short
-- friendly string. The detailed exception is available via
-- /api/v1/workflows/{id}/status which calls _stringify_error in the
-- backend (uses Python pickle to deserialize). For inline display in
-- task_tracking.error_msg we just substitute a hint.
CREATE OR REPLACE FUNCTION public.dbos_error_to_text(err TEXT)
RETURNS TEXT
LANGUAGE plpgsql
IMMUTABLE
AS $$
BEGIN
  IF err IS NULL OR length(err) = 0 THEN
    RETURN NULL;
  END IF;
  -- Pickle protocol 4 starts with 0x80 0x04 → base64 'gAS' / 'gAQ'
  -- Pickle protocol 5 starts with 0x80 0x05 → base64 'gAU' / 'gAV'
  IF length(err) > 80
     AND substring(err, 1, 3) = ANY (ARRAY['gAS', 'gAU', 'gAQ', 'gAV'])
  THEN
    RETURN 'Workflow failed — open detail to see the exception.';
  END IF;
  RETURN substring(err, 1, 500);
END;
$$;

COMMENT ON FUNCTION public.dbos_error_to_text(TEXT) IS
  'Friendly-stringify a DBOS workflow_status.error value for the
   task_tracking.error_msg cache. Long base64-pickle payloads collapse to
   a hint pointing the user at the detail endpoint; plain-text errors
   pass through truncated to 500 chars.';

-- ================================================================
-- Step 10: trigger function — DBOS lifecycle → task_tracking
-- ================================================================
CREATE OR REPLACE FUNCTION public.mirror_dbos_lifecycle_to_tracking()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, dbos
AS $$
DECLARE
  mapped_status TEXT;
BEGIN
  -- DBOS lifecycle states → app-level 5-state machine
  mapped_status := CASE NEW.status
    WHEN 'PENDING'                      THEN 'pending'
    WHEN 'ENQUEUED'                     THEN 'pending'
    WHEN 'RUNNING'                      THEN 'processing'
    WHEN 'SUCCESS'                      THEN 'completed'
    WHEN 'CANCELLED'                    THEN 'cancelled'
    WHEN 'ERROR'                        THEN 'failed'
    WHEN 'RETRIES_EXCEEDED'             THEN 'failed'
    WHEN 'MAX_RECOVERY_ATTEMPTS_EXCEEDED' THEN 'failed'
    ELSE NULL  -- unknown DBOS state → leave existing row.status untouched
  END;

  UPDATE public.task_tracking SET
    status = COALESCE(mapped_status, status),

    started_at = COALESCE(
      CASE WHEN NEW.started_at_epoch_ms IS NOT NULL
           THEN to_timestamp(NEW.started_at_epoch_ms / 1000.0)
      END,
      started_at
    ),

    completed_at = CASE
      WHEN NEW.status IN (
        'SUCCESS', 'ERROR', 'CANCELLED',
        'RETRIES_EXCEEDED', 'MAX_RECOVERY_ATTEMPTS_EXCEEDED'
      )
      THEN to_timestamp(NEW.updated_at / 1000.0)
      ELSE completed_at
    END,

    error_msg = CASE
      WHEN NEW.error IS NOT NULL THEN public.dbos_error_to_text(NEW.error)
      ELSE error_msg
    END,

    updated_at = NOW()
  WHERE dbos_workflow_id = NEW.workflow_uuid;

  RETURN NEW;
END;
$$;

COMMENT ON FUNCTION public.mirror_dbos_lifecycle_to_tracking() IS
  'AFTER UPDATE trigger on dbos.workflow_status. Mirrors lifecycle fields
   (status, started_at, completed_at, error_msg) into the matching
   public.task_tracking row by dbos_workflow_id = workflow_uuid.
   Replaces the application-layer @tracked_workflow decorator pattern.
   SECURITY DEFINER so it can write to public.task_tracking even though
   the trigger fires under the dbos schema owner role.';

-- ================================================================
-- Step 11: install trigger on dbos.workflow_status
-- ================================================================
-- Permission: dbos schema is owned by mediahub_dbos. We have postgres
-- superuser permission via 174_grant_service_role_to_mediahub_dbos.sql.
DROP TRIGGER IF EXISTS trg_mirror_dbos_lifecycle ON dbos.workflow_status;
CREATE TRIGGER trg_mirror_dbos_lifecycle
AFTER UPDATE OF status, error, started_at_epoch_ms, updated_at
ON dbos.workflow_status
FOR EACH ROW
EXECUTE FUNCTION public.mirror_dbos_lifecycle_to_tracking();

-- Backfill: run the trigger logic once for all currently-existing DBOS
-- workflows, so any task_tracking rows created before this migration
-- get their lifecycle synced.
UPDATE public.task_tracking AS t
SET
  status = CASE ws.status
    WHEN 'PENDING'                      THEN 'pending'
    WHEN 'ENQUEUED'                     THEN 'pending'
    WHEN 'RUNNING'                      THEN 'processing'
    WHEN 'SUCCESS'                      THEN 'completed'
    WHEN 'CANCELLED'                    THEN 'cancelled'
    WHEN 'ERROR'                        THEN 'failed'
    WHEN 'RETRIES_EXCEEDED'             THEN 'failed'
    WHEN 'MAX_RECOVERY_ATTEMPTS_EXCEEDED' THEN 'failed'
    ELSE t.status
  END,
  started_at = COALESCE(
    CASE WHEN ws.started_at_epoch_ms IS NOT NULL
         THEN to_timestamp(ws.started_at_epoch_ms / 1000.0)
    END,
    t.started_at
  ),
  completed_at = CASE
    WHEN ws.status IN (
      'SUCCESS', 'ERROR', 'CANCELLED',
      'RETRIES_EXCEEDED', 'MAX_RECOVERY_ATTEMPTS_EXCEEDED'
    )
    THEN to_timestamp(ws.updated_at / 1000.0)
    ELSE t.completed_at
  END,
  error_msg = CASE
    WHEN ws.error IS NOT NULL THEN public.dbos_error_to_text(ws.error)
    ELSE t.error_msg
  END,
  updated_at = NOW()
FROM dbos.workflow_status ws
WHERE ws.workflow_uuid = t.dbos_workflow_id;

-- ================================================================
-- Step 12: table comment documents the architecture
-- ================================================================
COMMENT ON TABLE public.task_tracking IS
  'User-facing sidecar of dbos.workflow_status (1:1 by dbos_workflow_id).
   DBOS owns execution truth (status/started_at/error in dbos.workflow_status);
   the trg_mirror_dbos_lifecycle trigger auto-syncs those fields HERE for
   Supabase Realtime convenience. Application code only writes
   title/subtitle/progress/phase/metadata/business-id columns — never
   touches status/started_at/completed_at/error_msg. Renamed from
   unified_tasks in migration 180.';

COMMENT ON COLUMN public.task_tracking.dbos_workflow_id IS
  'PK; equals dbos.workflow_status.workflow_uuid (FK CASCADE).';

COMMENT ON COLUMN public.task_tracking.status IS
  'Mirror of DBOS lifecycle (5-state). Updated by trigger ONLY — never
   write directly. Source of truth: dbos.workflow_status.status.';

COMMENT ON COLUMN public.task_tracking.phase IS
  'Free-form business phase (e.g. parsing/downloading/waiting_for_human_review).
   Application code is the truth source for this column. DBOS lifecycle stays
   in `status` column; phase is finer-grained business detail.';

COMMENT ON COLUMN public.task_tracking.cost_cents IS
  'Aggregated AI cost in cents for tasks that triggered AI workflows. Updated
   by application code reading agent_runs at task completion time.';

COMMIT;
