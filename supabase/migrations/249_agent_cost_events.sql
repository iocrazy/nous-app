-- Migration 249: per-call cost ledger (paperclip cost port Phase 1b)
--
-- Borrows paperclip's cost_events: one immutable row per LLM call (vs the
-- per-run aggregate on agent_runs). Foundation for windowed budget
-- aggregation (Phase 2). agent_runs stays the per-run rollup; this is the
-- granular append-only ledger.
--
-- Scope column types mirror agent_runs EXACTLY (verified on prod via
-- information_schema):
--   id/agent_id/user_id = uuid
--   team_id/project_id/session_id/issue_id = bigint

CREATE TABLE IF NOT EXISTS public.agent_cost_events (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id              uuid NOT NULL REFERENCES public.agent_runs(id) ON DELETE CASCADE,
  agent_id            uuid NOT NULL,
  user_id             uuid,
  team_id             bigint,
  project_id          bigint,
  session_id          bigint,
  issue_id            bigint,
  provider            text,
  model               text,
  input_tokens        integer NOT NULL DEFAULT 0,
  cached_input_tokens integer NOT NULL DEFAULT 0,
  output_tokens       integer NOT NULL DEFAULT 0,
  cost_cents          numeric NOT NULL DEFAULT 0,
  occurred_at         timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_cost_events_run
  ON public.agent_cost_events (run_id);
CREATE INDEX IF NOT EXISTS idx_cost_events_user_occurred
  ON public.agent_cost_events (user_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_cost_events_team_occurred
  ON public.agent_cost_events (team_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_cost_events_agent_occurred
  ON public.agent_cost_events (agent_id, occurred_at);
