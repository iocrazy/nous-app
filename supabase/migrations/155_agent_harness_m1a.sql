-- M1.A: Agent harness schema extensions for Hook system + retry/FALLBACK.
--
-- Adds:
--   1. ai_agents.budget_per_run_cents — per-run BudgetGuard threshold
--      (148 already added monthly_token_budget / monthly_cost_cents_budget;
--       this is the per-call ceiling that BudgetGuard reads from RunRecorder.
--       accumulated_cost_cents in-memory, no DB query at hook time).
--   2. ai_agents.fallback_models TEXT[] — ordered fallback chain.
--      adapter exhausts retries on primary then walks this list.
--      Empty array = no fallback (current behaviour).
--   3. agent_run_events — high-frequency per-tool-call audit trail.
--      CostAuditor PostToolUse hook writes one row per tool iteration.
--      Cost rollup queries (Usage page, BudgetGuard tree-aware in M2) read this.
--
-- RLS: agent_run_events follows agent_runs ownership pattern. Service role
-- write (hooks run server-side), users read their own runs' events.

ALTER TABLE ai_agents
  ADD COLUMN IF NOT EXISTS budget_per_run_cents NUMERIC(8, 2) DEFAULT 50.0,
  ADD COLUMN IF NOT EXISTS fallback_models TEXT[] NOT NULL DEFAULT '{}';

COMMENT ON COLUMN ai_agents.budget_per_run_cents IS
  'Per-run BudgetGuard threshold in cents. NULL = unlimited.';
COMMENT ON COLUMN ai_agents.fallback_models IS
  'Ordered fallback model chain. Adapter walks this after primary exhausts retries.';

CREATE TABLE IF NOT EXISTS agent_run_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
  iteration SMALLINT NOT NULL,

  -- Tool call context
  tool_name TEXT,
  tool_args_summary TEXT,                  -- truncated args, full in metadata_json

  -- Per-iteration deltas (NOT accumulated)
  prompt_tokens_delta INT NOT NULL DEFAULT 0,
  completion_tokens_delta INT NOT NULL DEFAULT 0,
  cost_cents_delta NUMERIC(10, 6) NOT NULL DEFAULT 0,
  duration_ms INT,

  -- Hook outcomes (which hooks fired, what they decided)
  hook_decisions JSONB NOT NULL DEFAULT '{}',

  -- Model + provider snapshot (may differ from agent_runs.model if FALLBACK kicked in)
  model TEXT,
  provider TEXT,

  -- Failure detail when iteration aborted
  error_code TEXT,
  error_message TEXT,

  metadata_json JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_agent_run_events_run
  ON agent_run_events(run_id, iteration);
CREATE INDEX IF NOT EXISTS idx_agent_run_events_created
  ON agent_run_events(created_at DESC);

ALTER TABLE agent_run_events ENABLE ROW LEVEL SECURITY;

-- Service role full access (hooks run server-side).
DROP POLICY IF EXISTS "events_service_full" ON agent_run_events;
CREATE POLICY "events_service_full" ON agent_run_events FOR ALL TO service_role
  USING (true) WITH CHECK (true);

-- Users read their own runs' events. Mirrors agent_runs RLS pattern.
DROP POLICY IF EXISTS "own_events_readable" ON agent_run_events;
CREATE POLICY "own_events_readable" ON agent_run_events FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM agent_runs r
      WHERE r.id = agent_run_events.run_id
        AND r.user_id = auth.uid()
    )
  );
