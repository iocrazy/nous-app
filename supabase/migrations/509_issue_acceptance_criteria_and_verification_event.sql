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
