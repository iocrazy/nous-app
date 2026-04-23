-- agent_runs: one row per agent invocation. Drives pulse indicator,
-- token/cost rollup, run history, and (future) memory FK anchor.
--
-- Lifecycle: running → (completed|failed|cancelled|heartbeat_lost). Terminal
-- rows never transition back. Cancel is polling-based: caller sets
-- cancel_requested=true, runner observes it between tool iterations and
-- flips status itself. No LISTEN/NOTIFY — Supabase's default pgbouncer
-- transaction pool kills it.
--
-- Price snapshot columns (prompt_cents_per_1k_snapshot + completion_*)
-- are pinned at run-start from ai_model_prices so admin price edits
-- never rewrite history.
--
-- RLS: user owns own runs; team members read team-scoped; project
-- owners + team members read project-scoped. Matches ai_agents pattern.
CREATE TABLE agent_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id UUID NOT NULL REFERENCES ai_agents(id) ON DELETE CASCADE,
  session_id UUID REFERENCES ai_sessions(id) ON DELETE SET NULL,
  user_id UUID NOT NULL,
  team_id BIGINT,
  project_id BIGINT,

  -- Lifecycle
  status TEXT NOT NULL CHECK (status IN
    ('running', 'completed', 'failed', 'cancelled', 'heartbeat_lost')),
  cancel_requested BOOLEAN NOT NULL DEFAULT false,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  ended_at TIMESTAMPTZ,
  heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT now(),

  -- Usage (unit: US cents; sub-cent precision OK)
  model TEXT,
  provider TEXT,
  prompt_tokens INT NOT NULL DEFAULT 0,
  completion_tokens INT NOT NULL DEFAULT 0,
  total_tokens INT GENERATED ALWAYS AS (prompt_tokens + completion_tokens) STORED,
  prompt_cents_per_1k_snapshot NUMERIC(12, 6),
  completion_cents_per_1k_snapshot NUMERIC(12, 6),
  cost_cents NUMERIC(12, 6),

  -- Context
  trigger TEXT NOT NULL,
  skill_slugs_used TEXT[] NOT NULL DEFAULT '{}',

  -- Content: display-only; full content lives in metadata_json
  input_summary TEXT,
  output_summary TEXT,
  error_code TEXT,
  error_message TEXT,
  metadata_json JSONB NOT NULL DEFAULT '{}',

  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_agent_runs_agent_started  ON agent_runs(agent_id, started_at DESC);
CREATE INDEX idx_agent_runs_session        ON agent_runs(session_id)
  WHERE session_id IS NOT NULL;
CREATE INDEX idx_agent_runs_running_agent  ON agent_runs(agent_id)
  WHERE status = 'running';
CREATE INDEX idx_agent_runs_running_user   ON agent_runs(user_id)
  WHERE status = 'running';
CREATE INDEX idx_agent_runs_heartbeat      ON agent_runs(heartbeat_at)
  WHERE status = 'running';
CREATE INDEX idx_agent_runs_billing        ON agent_runs(team_id, created_at DESC)
  WHERE team_id IS NOT NULL;
CREATE INDEX idx_agent_runs_cancel_pending ON agent_runs(id)
  WHERE status = 'running' AND cancel_requested = true;

ALTER TABLE agent_runs ENABLE ROW LEVEL SECURITY;

CREATE POLICY "own_runs_readable" ON agent_runs FOR SELECT
  USING (user_id = auth.uid());

CREATE POLICY "team_runs_readable" ON agent_runs FOR SELECT
  USING (
    team_id IS NOT NULL
    AND EXISTS (
      SELECT 1 FROM team_members tm
      WHERE tm.team_id = agent_runs.team_id
        AND tm.user_id = auth.uid()
    )
  );

CREATE POLICY "project_runs_readable" ON agent_runs FOR SELECT
  USING (
    project_id IS NOT NULL
    AND EXISTS (
      SELECT 1 FROM projects p
      LEFT JOIN team_members tm ON tm.team_id = p.team_id
      WHERE p.id = agent_runs.project_id
        AND (p.owner_id = auth.uid() OR tm.user_id = auth.uid())
    )
  );

-- Service role writes (backend); users never INSERT/UPDATE runs directly.
CREATE POLICY "service_role_all" ON agent_runs FOR ALL TO service_role
  USING (true) WITH CHECK (true);

-- Publish to Realtime; verify Realtime RLS enforcement is on for this project
-- before consuming the channel from the client (finding #1).
ALTER PUBLICATION supabase_realtime ADD TABLE agent_runs;
