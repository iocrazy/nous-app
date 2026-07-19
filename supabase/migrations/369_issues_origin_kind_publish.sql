-- 369: allow 'publish' as an issues.origin_kind.
--
-- The publish→issue mirror (user decision 2026-07-18: "发布就应该触发管理")
-- stamps a work item for every distribution publish batch
-- (origin_id = 'publish:{publish_task_id}') so the todolist is the one place
-- all work is managed. Same DROP+ADD contract as migrations 166/367 —
-- Postgres has no ALTER for a CHECK expression. No SET ROLE (mig 365 lesson).
--
-- REMINDER: this enum has FOUR mirrors — this CHECK, models/reviews.py,
-- schemas/issue.py (pinned by test_issue_origin_kind_mirrors), and the TS
-- union in issuesService.ts. All four change together.

ALTER TABLE public.issues
  DROP CONSTRAINT IF EXISTS issues_origin_kind_check;

ALTER TABLE public.issues
  ADD CONSTRAINT issues_origin_kind_check CHECK (
    origin_kind IN (
      'manual',
      'chat_delegate',
      'celery_pipeline',
      'agent_dispatch',
      'routine',
      'escalation',
      'project_stage',
      'publish'
    )
  );

NOTIFY pgrst, 'reload schema';
