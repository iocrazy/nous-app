-- 198 (G1): persist agent approval-request gates.
--
-- Background: the hook protocol supports `decision == "await_approval"`
-- and AgentRunner returns the ApprovalRequest payload to the chat
-- service. But there was no persistence layer — the chat service
-- handed the dict to the frontend and lost it. No second turn could
-- look up "did the user approve" because there was no row to look up.
--
-- This table closes that loop:
--   - hook fires await_approval → row created with status='pending'
--   - frontend polls / Realtime-subscribes pending rows for current user
--   - user clicks approve → status='approved' + decided_at timestamp
--   - next chat turn (or polling worker) resumes the run with the decision

CREATE TABLE IF NOT EXISTS agent_approval_requests (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  -- Identity / scope
  user_id      UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  agent_id     UUID NOT NULL,            -- ai_agents.id (no FK — agents may
                                          -- be deleted; we keep audit row)
  session_id   UUID,                     -- ai_sessions.id when applicable
  run_id       UUID,                     -- agent_runs.id of the paused run

  -- Decision payload from the hook
  hook_name    TEXT NOT NULL,            -- which hook requested the pause
  reason       TEXT NOT NULL,            -- human-readable why
  payload      JSONB NOT NULL DEFAULT '{}'::jsonb,
                                          -- { tool_name, args, ... } or hook-defined

  -- State machine
  status       TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'approved', 'rejected', 'expired', 'cancelled')),
  decided_at   TIMESTAMPTZ,              -- set when status leaves pending
  decided_by   UUID,                     -- which user approved (usually = user_id)
  decision_note TEXT,                    -- free-form why (rejection reason etc.)

  -- Lifecycle / housekeeping
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at   TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '24 hours',

  -- A run can only have one open approval at a time. Once decided the
  -- index doesn't bind anymore (run_id may be re-used).
  CONSTRAINT one_open_per_run UNIQUE (run_id, status)
    DEFERRABLE INITIALLY DEFERRED
);

CREATE INDEX IF NOT EXISTS idx_approval_requests_user_pending
  ON agent_approval_requests (user_id, created_at DESC)
  WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_approval_requests_run
  ON agent_approval_requests (run_id) WHERE run_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_approval_requests_expires
  ON agent_approval_requests (expires_at) WHERE status = 'pending';

COMMENT ON TABLE agent_approval_requests IS
  'G1: gating rows for hook decisions of type await_approval. The '
  'agent run pauses; the frontend renders pending rows; the user '
  'approves/rejects; a wake-up path resumes the workflow with the '
  'decision. Sweeper marks rows expired after expires_at.';

-- RLS: users see only their own rows
ALTER TABLE agent_approval_requests ENABLE ROW LEVEL SECURITY;

CREATE POLICY "approval_requests_owner_all"
  ON agent_approval_requests
  FOR ALL
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());
