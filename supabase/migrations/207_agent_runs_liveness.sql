-- 207_agent_runs_liveness.sql
--
-- Multi-dim liveness model on agent_runs (paperclip-inspired, A8.5).
--
-- Why: today mediahub workforce only has agent_workers.heartbeat_at + a
-- 60-second sweeper. That catches "process is gone" but misses the more
-- common LLM failure: "agent's heartbeating fine but hasn't done anything
-- useful in 3 minutes" — typically a stuck tool call or an LLM that
-- hasn't streamed any new tokens.
--
-- The 4-dim model lets the scanner classify runs as:
--   running → silent → stuck → dead
-- with explicit thresholds. UI shows the state, ops can intervene
-- before the user notices.
--
-- Liveness state machine (driven by app/workflows/liveness_scanner.py):
--   running   = healthy
--   silent    = heartbeat OK but lastUsefulAction old; warn in UI
--   stuck     = silent long enough that we want to try a continuation
--               (auto-revive prompt, increments continuation_attempt)
--   dead      = stuck and either (a) continuation_attempt >= max, or
--               (b) heartbeat dead too. Also flips agent_runs.status →
--               'failed' with error_code='liveness_dead'.
--   cancelled = explicit user/system cancel (mirrors agent_runs.status)

BEGIN;

ALTER TABLE public.agent_runs
  ADD COLUMN IF NOT EXISTS liveness_state TEXT NOT NULL DEFAULT 'running'
    CHECK (liveness_state IN ('running','silent','stuck','dead','cancelled')),
  ADD COLUMN IF NOT EXISTS last_useful_action_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS continuation_attempt INT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS output_silence_bytes BIGINT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS liveness_changed_at TIMESTAMPTZ;

-- Backfill last_useful_action_at = started_at for all existing runs so the
-- scanner doesn't immediately mark every running row as silent on first
-- pass.
UPDATE public.agent_runs
   SET last_useful_action_at = started_at
 WHERE last_useful_action_at IS NULL;

-- Future inserts: default last_useful_action_at to started_at.
CREATE OR REPLACE FUNCTION public.agent_runs_default_last_useful_action()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  IF NEW.last_useful_action_at IS NULL THEN
    NEW.last_useful_action_at := COALESCE(NEW.started_at, now());
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_agent_runs_default_last_useful_action ON public.agent_runs;
CREATE TRIGGER trg_agent_runs_default_last_useful_action
BEFORE INSERT ON public.agent_runs
FOR EACH ROW
EXECUTE FUNCTION public.agent_runs_default_last_useful_action();

-- When liveness_state changes, stamp liveness_changed_at so the scanner
-- can implement "stayed in this state for at least N seconds before
-- promoting" thresholds without an extra timeline table.
CREATE OR REPLACE FUNCTION public.agent_runs_track_liveness_change()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  IF NEW.liveness_state IS DISTINCT FROM OLD.liveness_state THEN
    NEW.liveness_changed_at := now();
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_agent_runs_track_liveness_change ON public.agent_runs;
CREATE TRIGGER trg_agent_runs_track_liveness_change
BEFORE UPDATE OF liveness_state ON public.agent_runs
FOR EACH ROW
EXECUTE FUNCTION public.agent_runs_track_liveness_change();

-- Seed liveness_changed_at on existing rows.
UPDATE public.agent_runs
   SET liveness_changed_at = COALESCE(started_at, now())
 WHERE liveness_changed_at IS NULL;

-- Indexes for the scanner: the hot query is "running rows whose useful
-- action / heartbeat / liveness state needs re-evaluation."
CREATE INDEX IF NOT EXISTS idx_agent_runs_liveness_scan
  ON public.agent_runs(liveness_state, heartbeat_at)
  WHERE status = 'running';

CREATE INDEX IF NOT EXISTS idx_agent_runs_useful_action_scan
  ON public.agent_runs(last_useful_action_at)
  WHERE status = 'running';

COMMENT ON COLUMN public.agent_runs.liveness_state IS
  'paperclip-style 5-state liveness orthogonal to status.
   running→silent→stuck→dead transitions are driven by the scanner
   in app/workflows/liveness_scanner.py based on heartbeat_at +
   last_useful_action_at + output_silence_bytes thresholds.';
COMMENT ON COLUMN public.agent_runs.last_useful_action_at IS
  'Last time the agent made observable progress (new output bytes,
   completed step, written file, etc). Distinct from heartbeat_at —
   heartbeat says "process alive," last_useful_action_at says
   "actually doing work." Set by the runtime, not the scanner.';
COMMENT ON COLUMN public.agent_runs.continuation_attempt IS
  'Number of times the scanner has tried to revive this run from
   stuck. Bumped by the scanner; capped to prevent infinite loops.';
COMMENT ON COLUMN public.agent_runs.output_silence_bytes IS
  'Snapshot of how much output the run had at the last scanner pass
   it was found stuck. If the next pass finds the same byte count,
   stuck → dead.';

COMMIT;
