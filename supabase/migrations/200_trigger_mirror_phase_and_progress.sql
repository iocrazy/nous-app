-- 200_trigger_mirror_phase_and_progress.sql
--
-- D8-A follow-up: extend `mirror_dbos_lifecycle_to_tracking` so it also
-- mirrors `phase` and `progress` from dbos.workflow_status.status changes.
--
-- The original mig 180 trigger only updated:
--   * status (mapped from DBOS status enum)
--   * started_at, completed_at
--   * error_msg
--
-- It LEFT phase + progress untouched. That broke the Task Center UI in
-- a subtle way: the frontend renders cards based on `phase` (queued /
-- in_progress / completed / failed), not on `status`. So a workflow that
-- DBOS-completes successfully would have:
--   status   = 'completed'   (correct, set by trigger)
--   phase    = 'queued'      (NEVER moved past initial — frontend shows
--                              "Initializing..." forever)
--   progress = 0             (never touched)
--
-- Repro: parse a Douyin URL → DBOS workflow finishes (PG status SUCCESS)
-- → admin DB shows task_tracking.status='completed' but Task Center UI
-- shows the row stuck at "30% Initializing".
--
-- Fix: extend the trigger to also derive `phase` from the DBOS status
-- (queued / in_progress / completed / failed / cancelled), and bump
-- progress to 100 on terminal-success, leave it on terminal-failure
-- so the operator can still see how far the run got before it died.
--
-- Same anti-regression guard as the status branch: a row already in a
-- manual terminal state (failed / cancelled) doesn't get regressed by
-- a late-arriving SUCCESS event.

CREATE OR REPLACE FUNCTION public.mirror_dbos_lifecycle_to_tracking()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
  mapped_status TEXT;
  mapped_phase  TEXT;
BEGIN
  mapped_status := CASE NEW.status
    WHEN 'PENDING'                       THEN 'pending'
    WHEN 'ENQUEUED'                      THEN 'pending'
    WHEN 'RUNNING'                       THEN 'processing'
    WHEN 'SUCCESS'                       THEN 'completed'
    WHEN 'CANCELLED'                     THEN 'cancelled'
    WHEN 'ERROR'                         THEN 'failed'
    WHEN 'RETRIES_EXCEEDED'              THEN 'failed'
    WHEN 'MAX_RECOVERY_ATTEMPTS_EXCEEDED' THEN 'failed'
    ELSE NULL
  END;

  mapped_phase := CASE NEW.status
    WHEN 'PENDING'                       THEN 'queued'
    WHEN 'ENQUEUED'                      THEN 'queued'
    WHEN 'RUNNING'                       THEN 'in_progress'
    WHEN 'SUCCESS'                       THEN 'completed'
    WHEN 'CANCELLED'                     THEN 'cancelled'
    WHEN 'ERROR'                         THEN 'failed'
    WHEN 'RETRIES_EXCEEDED'              THEN 'failed'
    WHEN 'MAX_RECOVERY_ATTEMPTS_EXCEEDED' THEN 'failed'
    ELSE NULL
  END;

  UPDATE public.task_tracking SET
    -- Preserve manual terminal failure: if the row is already 'failed' or
    -- 'cancelled' (set by record_workflow_failure or user cancel), don't
    -- let an incoming SUCCESS regress it to 'completed'.
    status = CASE
      WHEN status IN ('failed', 'cancelled') AND mapped_status = 'completed'
        THEN status
      ELSE COALESCE(mapped_status, status)
    END,

    -- Same anti-regression rule for phase. UI reads this; without it the
    -- card stays "Initializing..." forever even after DBOS reports SUCCESS.
    phase = CASE
      WHEN phase IN ('failed', 'cancelled') AND mapped_phase = 'completed'
        THEN phase
      ELSE COALESCE(mapped_phase, phase)
    END,

    -- Bump progress to 100 on terminal SUCCESS so the UI bar fills.
    -- On terminal failure, leave progress as-is so operators can see how
    -- far the run got before it died (often a useful diagnostic).
    progress = CASE
      WHEN NEW.status = 'SUCCESS' THEN 100
      ELSE progress
    END,

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

    updated_at = now()
  WHERE dbos_workflow_id = NEW.workflow_uuid;

  RETURN NEW;
END;
$function$;

COMMENT ON FUNCTION public.mirror_dbos_lifecycle_to_tracking() IS
  'D8-A trigger function. Mirrors lifecycle from dbos.workflow_status to
   public.task_tracking on UPDATE. Mirrors: status, phase, progress,
   started_at, completed_at, error_msg. Frontend Task Center reads phase,
   so omitting it (mig 180) caused all parse/download/transcode rows to
   stay "Initializing" forever even after DBOS reported SUCCESS. Mig 200
   fixed that.';

-- Backfill: any existing task_tracking rows whose dbos_workflow_id maps
-- to a SUCCESS / ERROR / CANCELLED workflow but whose phase is still
-- queued/in_progress (because they were processed by the broken mig 180
-- trigger) — re-derive phase/progress from the DBOS status now.
UPDATE public.task_tracking tt
SET
  phase = CASE ws.status
    WHEN 'SUCCESS'                       THEN 'completed'
    WHEN 'CANCELLED'                     THEN 'cancelled'
    WHEN 'ERROR'                         THEN 'failed'
    WHEN 'RETRIES_EXCEEDED'              THEN 'failed'
    WHEN 'MAX_RECOVERY_ATTEMPTS_EXCEEDED' THEN 'failed'
    ELSE tt.phase
  END,
  progress = CASE
    WHEN ws.status = 'SUCCESS' THEN 100
    ELSE tt.progress
  END,
  updated_at = now()
FROM dbos.workflow_status ws
WHERE tt.dbos_workflow_id = ws.workflow_uuid
  AND ws.status IN (
    'SUCCESS', 'ERROR', 'CANCELLED',
    'RETRIES_EXCEEDED', 'MAX_RECOVERY_ATTEMPTS_EXCEEDED'
  )
  AND tt.phase NOT IN ('completed', 'failed', 'cancelled');
