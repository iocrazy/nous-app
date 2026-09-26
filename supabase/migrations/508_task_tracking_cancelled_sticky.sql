-- 508 — task_tracking: 'cancelled' is sticky against later DBOS mirrors.
--
-- Why (fh4 T1, framework hardening batch 4): run_process now polls
-- task_tracking and kills a workflow's child process when the Task Center
-- cancels the task. The Task Center cancel writes phase/status='cancelled'
-- directly and does NOT cancel the DBOS workflow, so the killed step fails
-- and the workflow later reaches ERROR. The previous mirror only protected
-- terminal rows from a regressing SUCCESS; an incoming ERROR / RUNNING
-- overwrote 'cancelled' with 'failed' / 'in_progress', turning a user cancel
-- into a failure card.
--
-- Change: a row already at 'cancelled' keeps it (status and phase) whatever
-- DBOS reports next; 'failed' keeps its existing guard against 'completed'.
-- Everything else (progress, timestamps, error_msg) is unchanged, so the
-- error text of the killed step is still recorded for operators.
--
-- CREATE OR REPLACE keeps the function's ACL and the existing trigger
-- binding (trg_mirror_dbos_lifecycle, mig 209); it is a plain invoker trigger
-- function with no grants to revisit. Idempotent.

CREATE OR REPLACE FUNCTION public.mirror_dbos_lifecycle_to_tracking() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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
    -- fh4 (508): 'cancelled' is a sticky terminal state. The Task Center
    -- cancel writes it directly without cancelling the DBOS workflow; the
    -- worker then kills the child, the step fails, and DBOS ends in ERROR —
    -- which must not overwrite the user's cancel with 'failed'.
    status = CASE
      WHEN status = 'cancelled' THEN status
      WHEN status = 'failed' AND mapped_status = 'completed' THEN status
      ELSE COALESCE(mapped_status, status)
    END,

    -- Same anti-regression rule for phase. UI reads this; without it the
    -- card stays "Initializing..." forever even after DBOS reports SUCCESS.
    phase = CASE
      WHEN phase = 'cancelled' THEN phase
      WHEN phase = 'failed' AND mapped_phase = 'completed' THEN phase
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
$$;
