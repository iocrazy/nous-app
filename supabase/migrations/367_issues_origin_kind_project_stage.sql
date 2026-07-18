-- 367: allow 'project_stage' as an issues.origin_kind.
--
-- WHY THIS EXISTS
-- ---------------
-- The "project stage → auto todo" feature stamps an issue whenever a project's
-- SOP stage advances (origin_id = 'project_stage:{project_id}:{stage_id}'), and
-- closes the previous stage's issue. That origin needs its own origin_kind so
-- the reverse lookup (issues_origin_idx) and the frontend back-link
-- (issueOrigin.ts) can distinguish it from manual / routine / agent origins.
--
-- The enum lives in a single CHECK constraint (issues_origin_kind_check, first
-- created in migration 166). Postgres has no ALTER for a CHECK's expression, so
-- the contract is DROP then ADD with the widened value set.
--
-- Runs as the connecting role (postgres under CI = the issues table owner); no
-- SET ROLE — see migration 365's ROOT CAUSE for why SET ROLE would drop the
-- privileges needed to alter a postgres-owned table.

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
      'project_stage'
    )
  );

-- PostgREST caches the schema; without this the widened constraint lingers in
-- its cache until a restart (see the CI-migration-skips-PostgREST-reload trap).
NOTIFY pgrst, 'reload schema';
