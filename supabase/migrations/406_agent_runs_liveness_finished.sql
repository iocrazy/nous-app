-- 406_agent_runs_liveness_finished.sql
--
-- Add the missing "ran to completion" terminal value to
-- agent_runs.liveness_state, and backfill the rows that never had one.
--
-- WHY THIS VALUE AND NOT 'dead'
-- -----------------------------
-- Migration 207 modelled liveness as a DEGRADATION ladder
-- (running → silent → stuck → dead) plus 'cancelled'. It has no value for the
-- ordinary happy path, so a run that finished perfectly sat at
-- liveness_state='running' forever — indistinguishable, by that column alone,
-- from one still in flight.
--
-- The tempting shortcut is to reuse 'dead' as "no longer live". That is wrong
-- and has already been rejected once:
--
--   * dead ⟺ failed is a whole-DB invariant. Both writers of 'dead'
--     (liveness_scanner._mark_dead, services/liveness/reconcile) set
--     status='failed' in the SAME statement, and 207's column comment defines
--     dead as "the process actually died".
--   * The agent fault badge reads liveness_state IN ('stuck','dead')
--     (AgentRunsRepository.dead_run_reasons_by_agent). Marking healthy
--     completions dead would light up every agent's fault badge.
--   * AgentRunsRepository.mark_empty_output's docstring records the earlier
--     attempt verbatim: "do not 'fix' it by reaching for 'dead'" — it needs
--     "its own terminal value + migration". This is that migration.
--
-- SAFETY OF ADDING A VALUE
-- ------------------------
-- No reader has to learn 'finished' to stay correct, because every consumer
-- of liveness_state is already gated on status as well, or enumerates the
-- states it cares about positively:
--   * liveness_scanner's candidate query is `WHERE status='running'` — a
--     finished row never enters the scan at all;
--   * stranded_issue_monitor's "is there a live run" predicate is
--     `status='running' AND liveness_state IN ('running','silent','stuck')`;
--   * dead_run_reasons_by_agent asks for IN ('stuck','dead');
--   * the mig 206/208/398 bridge triggers copy the value into issue_messages
--     meta jsonb without asserting on it.
-- The frontend's LivenessPill is a total map over the union type, so it is
-- updated in the same change.

BEGIN;

-- Widen the CHECK. Drop-then-add rather than NOT VALID: the table is small
-- and the backfill below has to satisfy it anyway.
ALTER TABLE public.agent_runs
  DROP CONSTRAINT IF EXISTS agent_runs_liveness_state_check;

ALTER TABLE public.agent_runs
  ADD CONSTRAINT agent_runs_liveness_state_check
  CHECK (liveness_state IN
    ('running','silent','stuck','dead','cancelled','finished'));

-- Backfill: rows that reached a terminal status before this value existed.
-- Idempotent (the liveness_state predicate makes a re-run a no-op) and
-- narrow: only rows still parked on 'running', so a row the scanner
-- legitimately left at silent/stuck/dead is never rewritten.
--
-- Observed on the dev DB at authoring time: 68 completed rows on 'running'.
UPDATE public.agent_runs
   SET liveness_state = 'finished'
 WHERE status = 'completed'
   AND liveness_state = 'running';

-- 'cancelled' was defined by 207 as "mirrors agent_runs.status" but no writer
-- ever set it — the cancel path left these on 'running' too. RunRecorder now
-- writes it; this backfills the history.
UPDATE public.agent_runs
   SET liveness_state = 'cancelled'
 WHERE status = 'cancelled'
   AND liveness_state = 'running';

-- NOT backfilled, deliberately: status='failed' rows stay on 'running'.
-- Mapping them to 'dead' would break the dead ⟺ failed ops contract described
-- above (an application-level exception is a different event from "the
-- process died", and would flood the agent fault badge). Typing the failed
-- path is a separate decision with its own value, not a side effect of this
-- migration. Dev DB currently has 30 such rows.

COMMENT ON COLUMN public.agent_runs.liveness_state IS
  'paperclip-style liveness, orthogonal to status.
   running→silent→stuck→dead is the degradation ladder driven by the scanner
   in app/workflows/liveness_scanner.py (heartbeat_at + last_useful_action_at
   thresholds). finished = ran to completion normally, written by RunRecorder
   alongside status=completed. cancelled mirrors status=cancelled.
   dead is reserved for "the process actually died" and is always written
   together with status=failed — do not use it as a generic terminal value.';

COMMIT;
