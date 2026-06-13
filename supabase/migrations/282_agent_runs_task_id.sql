-- 282: agent_runs ↔ task_tracking bidirectional linkage (paperclip-style).
--
-- Paperclip links every agent execution both ways:
--   heartbeat_runs.wakeup_request_id ↔ agent_wakeup_requests.run_id
-- MediaHub's service-triggered runs (visual analysis / summary) only created
-- an agent_runs row — the task_tracking row carried no agent linkage, so the
-- agent Dashboard's task panels were always empty for service work.
--
-- This migration adds the run → task half:
--   agent_runs.task_id → task_tracking.dbos_workflow_id (text PK)
-- The task → run half lives in business-PATCHable fields RunRecorder stamps:
--   task_tracking.agent_id (uuid column) + task_tracking.metadata.run_id
-- (per the task-system discipline, metadata/agent_id are business fields;
-- phase/status/progress stay trigger-owned).

ALTER TABLE public.agent_runs
    ADD COLUMN IF NOT EXISTS task_id text
        REFERENCES public.task_tracking(dbos_workflow_id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_agent_runs_task_id
    ON public.agent_runs(task_id)
    WHERE task_id IS NOT NULL;

-- PostgREST schema cache reload so the new column is selectable immediately.
NOTIFY pgrst, 'reload schema';
