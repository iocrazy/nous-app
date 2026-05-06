-- Sprint 4: agent_commitments — cross-session followups.
--
-- Distinct from agent_memories (which holds "what is true"). Commitments
-- hold "what the agent owes" — promises like "I'll check in tomorrow",
-- "remind you when build #142 finishes", "follow up on the cooldown
-- change in 1 week".
--
-- Three trigger types:
--   time:         fire at a specific timestamp (sweeper polls due rows)
--   event:        fire when a named event occurs (e.g., 'pr.merged:142')
--   next_session: fire next time this user opens a session with this agent
--
-- Lifecycle: pending → fulfilled | cancelled | expired | failed
--
-- Sweeper (deferred to Sprint 4.5): scheduled task scans for due 'time'
-- commitments + reaped 'next_session' on session-open + event subscribers
-- on event publish. This sprint lands the schema + repo + primitive only.

CREATE TABLE agent_commitments (
  id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
  agent_id UUID NOT NULL REFERENCES ai_agents(id) ON DELETE CASCADE,
  user_id UUID,
  session_id UUID,
  description TEXT NOT NULL,
  payload_json JSONB NOT NULL DEFAULT '{}',
  trigger_type TEXT NOT NULL,
  trigger_at TIMESTAMPTZ,
  trigger_event TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  expires_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  fulfilled_at TIMESTAMPTZ,
  fulfillment_run_id UUID REFERENCES agent_runs(id) ON DELETE SET NULL,
  fulfillment_notes TEXT,

  CONSTRAINT commitments_trigger_type_check
    CHECK (trigger_type IN ('time', 'event', 'next_session')),
  CONSTRAINT commitments_status_check
    CHECK (status IN ('pending', 'fulfilled', 'cancelled', 'expired', 'failed')),
  -- time triggers must have trigger_at; event triggers must have trigger_event
  CONSTRAINT commitments_trigger_data_check CHECK (
    (trigger_type = 'time' AND trigger_at IS NOT NULL) OR
    (trigger_type = 'event' AND trigger_event IS NOT NULL) OR
    (trigger_type = 'next_session')
  )
);

-- Sweeper: due 'time' commitments. Partial index keeps it tiny in steady state.
CREATE INDEX idx_commitments_due_time
  ON agent_commitments(trigger_at)
  WHERE status = 'pending' AND trigger_type = 'time';

-- Event publisher: lookup by event name.
CREATE INDEX idx_commitments_pending_event
  ON agent_commitments(trigger_event)
  WHERE status = 'pending' AND trigger_type = 'event';

-- Session-open hook: lookup pending followups for (user, agent).
CREATE INDEX idx_commitments_next_session
  ON agent_commitments(user_id, agent_id)
  WHERE status = 'pending' AND trigger_type = 'next_session';

-- Per-user listing for UI ("my followups").
CREATE INDEX idx_commitments_user_status
  ON agent_commitments(user_id, status, created_at DESC)
  WHERE user_id IS NOT NULL;

ALTER TABLE agent_commitments ENABLE ROW LEVEL SECURITY;

-- Read your own commitments OR your agent's commitments (mirrors
-- agent_memories pattern from migration 156).
CREATE POLICY "own_commitments_readable" ON agent_commitments FOR SELECT
  USING (
    user_id = auth.uid()
    OR (
      user_id IS NULL
      AND EXISTS (
        SELECT 1 FROM ai_agents a
        WHERE a.id = agent_commitments.agent_id
          AND (
            a.user_id = auth.uid()
            OR (a.team_id IS NOT NULL AND EXISTS (
              SELECT 1 FROM team_members tm
              WHERE tm.team_id = a.team_id AND tm.user_id = auth.uid()
            ))
          )
      )
    )
  );

CREATE POLICY "commitments_write_service_only" ON agent_commitments FOR ALL TO service_role
  USING (true) WITH CHECK (true);

COMMENT ON TABLE agent_commitments IS
  'Cross-session followups (Sprint 4). Distinct from agent_memories (facts) — these are promises owed.';
COMMENT ON COLUMN agent_commitments.trigger_type IS
  'time = fire at trigger_at; event = fire on trigger_event publish; next_session = fire on next session open.';
COMMENT ON COLUMN agent_commitments.expires_at IS
  'Auto-cancel after this. NULL = no auto-expire.';
COMMENT ON COLUMN agent_commitments.fulfillment_run_id IS
  'agent_runs row that fulfilled this commitment (for telemetry trace).';
