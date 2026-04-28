-- 167: PR-D1 — back-reference issue_id from execution-layer tables to issues.
--
-- Per design doc: unified_tasks / agent_tasks / project_tasks become
-- execution-layer detail rows; users see them via their parent issue. Each
-- gets an issue_id BIGINT FK that's nullable (legacy rows + system-only tasks
-- never need an issue).
--
-- Idempotent: ADD COLUMN IF NOT EXISTS pattern.

-- =============================================================================
-- unified_tasks.issue_id
-- =============================================================================
ALTER TABLE public.unified_tasks
  ADD COLUMN IF NOT EXISTS issue_id BIGINT REFERENCES public.issues(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS unified_tasks_issue_id_idx
  ON public.unified_tasks(issue_id) WHERE issue_id IS NOT NULL;

COMMENT ON COLUMN public.unified_tasks.issue_id IS
  'Back-reference to issues.id. NULL for legacy rows (pre-PR-D1) and system-only tasks (scheduled cleanup, etc.). User-facing tasks set this on creation.';


-- =============================================================================
-- agent_tasks.issue_id
-- =============================================================================
ALTER TABLE public.agent_tasks
  ADD COLUMN IF NOT EXISTS issue_id BIGINT REFERENCES public.issues(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS agent_tasks_issue_id_idx
  ON public.agent_tasks(issue_id) WHERE issue_id IS NOT NULL;

COMMENT ON COLUMN public.agent_tasks.issue_id IS
  'Back-reference to issues.id. agent_tasks rows that originate from a chat_delegate or agent_dispatch issue link back here.';


-- =============================================================================
-- project_tasks.issue_id (transition: project_tasks becomes filtered view of issues)
-- =============================================================================
-- Per PR-D6 plan: project_tasks frontend view will eventually read from issues
-- via project_id filter. The issue_id column lets us dual-write during the
-- transition: when a user creates an issue in a project, project_tasks is
-- mirrored from it (or vice versa, until cutover).
ALTER TABLE public.project_tasks
  ADD COLUMN IF NOT EXISTS issue_id BIGINT REFERENCES public.issues(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS project_tasks_issue_id_idx
  ON public.project_tasks(issue_id) WHERE issue_id IS NOT NULL;

COMMENT ON COLUMN public.project_tasks.issue_id IS
  'Back-reference to issues.id. Set during PR-D6 dual-write window; NULL for tasks that pre-date the migration. Frontend may read either side.';
