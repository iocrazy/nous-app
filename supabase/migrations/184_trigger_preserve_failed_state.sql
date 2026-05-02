-- 184: trigger preserves manual 'failed' state from workflow-level catch
--
-- Background: ai_*_workflow bodies wrap their logic in try/except calling
-- record_workflow_failure() — to keep DBOSMaxStepRetriesExceeded from
-- propagating into the in-process worker thread (would damage the host
-- FastAPI process). The handler writes task_tracking.status='failed' +
-- error_msg + error_code, then the workflow body returns the failure dict
-- normally. DBOS sees the workflow function returned without raising and
-- marks dbos.workflow_status='SUCCESS'.
--
-- The mirror trigger then fires on that SUCCESS row and stomps our
-- manually-set 'failed' back to 'completed'. Final state: status='completed'
-- with error_msg='DBOSMaxStepRetriesExceeded: ...'. Inconsistent.
--
-- Fix: trigger never regresses an already-terminal failure ('failed' or
-- 'cancelled') back to 'completed'. The handler is the source of truth in
-- that case.
--
-- Verified by: rows 399b0d60-... and 7fd720f4-... (ai_transcription
-- workflows where run_whisper exhausted retries) showed status='completed'
-- + error_msg populated before this migration.

CREATE OR REPLACE FUNCTION public.mirror_dbos_lifecycle_to_tracking()
RETURNS TRIGGER AS $$
DECLARE
  mapped_status TEXT;
BEGIN
  mapped_status := CASE NEW.status
    WHEN 'PENDING'                      THEN 'pending'
    WHEN 'ENQUEUED'                     THEN 'pending'
    WHEN 'RUNNING'                      THEN 'processing'
    WHEN 'SUCCESS'                      THEN 'completed'
    WHEN 'CANCELLED'                    THEN 'cancelled'
    WHEN 'ERROR'                        THEN 'failed'
    WHEN 'RETRIES_EXCEEDED'             THEN 'failed'
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
$$ LANGUAGE plpgsql;

-- Backfill: fix the 2 known-bad rows from before this migration.
UPDATE public.task_tracking
SET status = 'failed'
WHERE error_msg IS NOT NULL
  AND error_code IS NOT NULL
  AND status = 'completed';
