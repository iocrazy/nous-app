-- 212: agent_runs.parent_run_id — sub-agent父子关联
--
-- Phase 3a of issue #199. Phase 3b (subagent_dispatcher.py + Task tool)
-- writes this column when a parent agent spawns a sub-agent via the
-- Task tool. Phase 4 reads it to render Runs tab as a tree.
--
-- ON DELETE SET NULL — losing the parent shouldn't orphan-cascade
-- delete sub-runs (we want the audit trail even after a top-level
-- run is purged for retention reasons).
--
-- Idempotent: safe to re-run.

ALTER TABLE public.agent_runs
  ADD COLUMN IF NOT EXISTS parent_run_id UUID
    REFERENCES public.agent_runs(id) ON DELETE SET NULL;

-- Index supports two access patterns:
--   1. "show me all sub-runs spawned by this parent" — Runs tab tree
--   2. "is this run a sub-run?" — quick != NULL filter at list endpoints
CREATE INDEX IF NOT EXISTS idx_agent_runs_parent
  ON public.agent_runs(parent_run_id)
  WHERE parent_run_id IS NOT NULL;

COMMENT ON COLUMN public.agent_runs.parent_run_id IS
  'When this run was spawned via the Task tool by another agent_run, '
  'points to the parent. NULL for top-level runs. ON DELETE SET NULL '
  'preserves sub-run audit trail when parent is purged.';
