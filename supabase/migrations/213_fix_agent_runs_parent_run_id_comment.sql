-- 213: fix incorrect COMMENT on agent_runs.parent_run_id (post #203)
--
-- PR #203 wrote a COMMENT claiming "ON DELETE SET NULL preserves
-- sub-run audit trail when parent is purged". That description is
-- WRONG: the column was originally added in migration 159
-- (workforce_schema_m2) with ON DELETE CASCADE, and #203's
-- ADD COLUMN IF NOT EXISTS no-op'd against the existing FK definition.
-- Only the COMMENT statement actually ran, planting the wrong text.
--
-- Verified in prod 2026-05-09 via information_schema.referential_constraints:
--   ('agent_runs_parent_run_id_fkey', ..., 'CASCADE')
--
-- Accept the existing CASCADE (it's run for months under workforce
-- delegation without issue) and update the COMMENT to match reality.
-- For spawn-and-return sub-agents this is equally fine: a sub-run
-- without its parent has no narrative anchor anyway.

COMMENT ON COLUMN public.agent_runs.parent_run_id IS
  'When this run was spawned via the Task tool by another agent_run, '
  'or via workforce Delegate, points to the parent. NULL for top-level '
  'runs. ON DELETE CASCADE — deleting the parent removes its sub-runs '
  '(originally set in migration 159; spawn-and-return semantics also '
  'fine with this since orphan sub-runs lose narrative meaning).';
