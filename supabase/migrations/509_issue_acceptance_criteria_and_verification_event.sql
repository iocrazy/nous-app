-- 509 — issues: acceptance criteria (who wrote them) + transcript event 'verification'.
--
-- Why (issue completion loop, spec docs/superpowers/specs/2026-09-26-issue-completion-loop-design.md):
-- FinishIssue(completed) used to be "the model says it is done". The loop needs a
-- recorded completion standard per issue (a person's, or the agent's proposal) and
-- a transcript event carrying the verifier's verdict.
--
-- Columns are nullable and are NOT on mig 170's immutable list, so creator /
-- assignee may edit them through PATCH like title / description (verified by
-- tests/db/test_migration_509_acceptance_criteria.py). Idempotent.
BEGIN;

ALTER TABLE public.issues
  ADD COLUMN IF NOT EXISTS acceptance_criteria text,
  ADD COLUMN IF NOT EXISTS acceptance_criteria_source text;

ALTER TABLE public.issues
  DROP CONSTRAINT IF EXISTS issues_acceptance_criteria_len_check;
ALTER TABLE public.issues
  ADD CONSTRAINT issues_acceptance_criteria_len_check
  CHECK (acceptance_criteria IS NULL OR char_length(acceptance_criteria) <= 4000);

ALTER TABLE public.issues
  DROP CONSTRAINT IF EXISTS issues_acceptance_criteria_source_check;
ALTER TABLE public.issues
  ADD CONSTRAINT issues_acceptance_criteria_source_check
  CHECK (acceptance_criteria_source IS NULL OR acceptance_criteria_source IN ('user', 'agent'));

COMMENT ON COLUMN public.issues.acceptance_criteria IS
  '509: completion criteria the verifier checks against; <= 4000 chars.';
COMMENT ON COLUMN public.issues.acceptance_criteria_source IS
  '509: who wrote acceptance_criteria — user (locked for the agent) | agent (proposal).';

-- issue_create_atomic (173) INSERTs an explicit column list, so without this
-- POST /issues would accept acceptance_criteria and silently drop it. Same
-- signature → CREATE OR REPLACE keeps the ACL, but the grant is restated
-- anyway (CLAUDE.md: a SECURITY DEFINER rewrite handles its ACL in the same
-- migration). Backend-only function: never executable by anon / authenticated.
CREATE OR REPLACE FUNCTION public.issue_create_atomic(payload JSONB)
RETURNS public.issues
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  next_n  INTEGER;
  pfx     TEXT;
  ident   TEXT;
  new_row public.issues;
BEGIN
  UPDATE public.issue_sequence
  SET counter = counter + 1
  WHERE scope = 'global'
  RETURNING counter, prefix INTO next_n, pfx;

  IF next_n IS NULL THEN
    RAISE EXCEPTION 'issue_sequence row missing for scope=global';
  END IF;

  ident := pfx || '-' || next_n::text;

  INSERT INTO public.issues (
    issue_number, identifier,
    team_id, project_id, parent_id,
    title, description,
    status, priority,
    assignee_agent_id, assignee_user_id,
    created_by_agent_id, created_by_user_id,
    dbos_workflow_id, execution_state,
    origin_kind, origin_id, origin_fingerprint,
    request_depth, billing_code,
    acceptance_criteria, acceptance_criteria_source
  ) VALUES (
    next_n, ident,
    (payload->>'team_id')::BIGINT,
    (payload->>'project_id')::BIGINT,
    (payload->>'parent_id')::BIGINT,
    payload->>'title',
    payload->>'description',
    COALESCE(payload->>'status', 'backlog'),
    COALESCE(payload->>'priority', 'medium'),
    (payload->>'assignee_agent_id')::UUID,
    (payload->>'assignee_user_id')::UUID,
    (payload->>'created_by_agent_id')::UUID,
    (payload->>'created_by_user_id')::UUID,
    payload->>'dbos_workflow_id',
    payload->'execution_state',
    COALESCE(payload->>'origin_kind', 'manual'),
    payload->>'origin_id',
    COALESCE(payload->>'origin_fingerprint', 'default'),
    COALESCE((payload->>'request_depth')::INTEGER, 0),
    payload->>'billing_code',
    payload->>'acceptance_criteria',
    payload->>'acceptance_criteria_source'
  )
  RETURNING * INTO new_row;

  RETURN new_row;
END;
$$;

REVOKE ALL ON FUNCTION public.issue_create_atomic(JSONB) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.issue_create_atomic(JSONB)
  TO service_role, mediahub_app, mediahub_dbos;

COMMENT ON FUNCTION public.issue_create_atomic IS
  'Atomic issue creation: allocates MH-N identifier and inserts in a single PG transaction. PR-D2.1. '
  '509: also inserts acceptance_criteria / acceptance_criteria_source.';

-- Transcript event whitelist: 492's list + 'verification'. The event-type array
-- must stay the FIRST array literal in this file, and no comment above it may
-- spell the array constructor (tests/models/test_transcript_event_types_phase2a
-- slices the first match of that constructor, comments included).
ALTER TABLE public.agent_run_transcript_events
  DROP CONSTRAINT IF EXISTS agent_run_transcript_events_event_type_check;
ALTER TABLE public.agent_run_transcript_events
  ADD CONSTRAINT agent_run_transcript_events_event_type_check
  CHECK (event_type = ANY (ARRAY[
    'user'::text, 'assistant'::text, 'tool_call'::text, 'error'::text, 'system'::text,
    'llm_retry'::text, 'todo_write'::text,
    'compaction_start'::text, 'compaction_summary'::text, 'compaction_end'::text,
    'turn_end'::text,
    'step_start'::text, 'step_end'::text, 'inbox_claimed'::text,
    'deliverable'::text, 'budget_check'::text,
    'question_asked'::text, 'question_answered'::text,
    'capability_denied'::text,
    'fork'::text,
    'subagent_spawned'::text, 'subagent_done'::text, 'schedule_set'::text,
    'media_job_done'::text,
    'verification'::text
  ]));
COMMENT ON CONSTRAINT agent_run_transcript_events_event_type_check
  ON public.agent_run_transcript_events IS
  'Allowed transcript event types. 436/443/453/459/460/461/492 as before. '
  '509: verification (completion verifier verdict for an issue run: '
  '{verdict, reason, attempt, unmet, verifier_run_id}).';

COMMIT;
