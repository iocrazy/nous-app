-- M2: Persistent Workforce schema.
-- 5 new tables + agent_runs tree fields + cascade cancel trigger.
-- Per plan-eng-review: borrows lifecycle pattern from unified_tasks but
-- stays UUID-keyed (runtime tables use UUID, not Snowflake BIGINT).

-- ai_agents: mark which agents are eligible to run as persistent workers.
ALTER TABLE ai_agents
  ADD COLUMN IF NOT EXISTS persistent BOOLEAN NOT NULL DEFAULT false;

-- agent_runs: tree-aware fields for multi-agent dispatch (M2 needs them).
-- parent_run_id chain enables cost rollup per delegation tree.
-- root_run_id is denormalised so SUM(cost_cents) WHERE root_run_id=X is
-- a single index hit instead of recursive CTE.
ALTER TABLE agent_runs
  ADD COLUMN IF NOT EXISTS parent_run_id UUID REFERENCES agent_runs(id) ON DELETE CASCADE,
  ADD COLUMN IF NOT EXISTS root_run_id UUID REFERENCES agent_runs(id),
  ADD COLUMN IF NOT EXISTS agent_depth SMALLINT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS delegation_reason TEXT;

CREATE INDEX IF NOT EXISTS idx_agent_runs_root_tree
  ON agent_runs(root_run_id, started_at)
  WHERE root_run_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_agent_runs_parent
  ON agent_runs(parent_run_id)
  WHERE parent_run_id IS NOT NULL;

-- ─────────────────────────────────────────────────────────────────
-- 1. agent_workers — one row per agent. Runtime state.
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_workers (
  agent_id UUID PRIMARY KEY REFERENCES ai_agents(id) ON DELETE CASCADE,
  state TEXT NOT NULL CHECK (state IN
    ('idle', 'working', 'waiting_for_other', 'blocked', 'paused', 'terminated')),
  current_task_id UUID,
  worker_pid INT,
  worker_hostname TEXT,
  heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  state_changed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_agent_workers_heartbeat
  ON agent_workers(heartbeat_at)
  WHERE state IN ('idle', 'working', 'waiting_for_other');

ALTER TABLE agent_workers ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "workers_service_full" ON agent_workers;
CREATE POLICY "workers_service_full" ON agent_workers FOR ALL TO service_role
  USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "workers_owner_readable" ON agent_workers;
CREATE POLICY "workers_owner_readable" ON agent_workers FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM ai_agents a
      WHERE a.id = agent_workers.agent_id
        AND (
          a.user_id = auth.uid()
          OR (a.team_id IS NOT NULL AND EXISTS (
            SELECT 1 FROM team_members tm
            WHERE tm.team_id = a.team_id AND tm.user_id = auth.uid()
          ))
        )
    )
  );

-- ─────────────────────────────────────────────────────────────────
-- 2. agent_inbox — incoming messages (user + system + cross-agent).
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_inbox (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  recipient_agent_id UUID NOT NULL REFERENCES ai_agents(id) ON DELETE CASCADE,
  sender_kind TEXT NOT NULL CHECK (sender_kind IN
    ('user', 'agent', 'system', 'schedule')),
  sender_user_id UUID,
  sender_agent_id UUID REFERENCES ai_agents(id) ON DELETE SET NULL,
  message_type TEXT NOT NULL CHECK (message_type IN
    ('task', 'question', 'notification', 'approval_request', 'cancel', 'status_query')),
  payload JSONB NOT NULL,
  status TEXT NOT NULL DEFAULT 'unread' CHECK (status IN
    ('unread', 'reading', 'processed', 'dismissed', 'expired')),
  priority SMALLINT NOT NULL DEFAULT 5,
  reading_claimed_at TIMESTAMPTZ,
  reading_claimed_by TEXT,                -- worker hostname:pid
  task_id UUID,                            -- FK set below after agent_tasks created
  reply_to_message_id UUID REFERENCES agent_inbox(id) ON DELETE SET NULL,
  dedup_key TEXT,                          -- borrowed from unified_tasks pattern
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  processed_at TIMESTAMPTZ,
  expires_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_inbox_recipient_unread
  ON agent_inbox(recipient_agent_id, priority DESC, created_at)
  WHERE status = 'unread';
CREATE INDEX IF NOT EXISTS idx_inbox_recipient_active
  ON agent_inbox(recipient_agent_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_inbox_dedup
  ON agent_inbox(recipient_agent_id, dedup_key)
  WHERE dedup_key IS NOT NULL AND status IN ('unread', 'reading');

ALTER TABLE agent_inbox ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "inbox_service_full" ON agent_inbox;
CREATE POLICY "inbox_service_full" ON agent_inbox FOR ALL TO service_role
  USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "inbox_sender_readable" ON agent_inbox;
CREATE POLICY "inbox_sender_readable" ON agent_inbox FOR SELECT
  USING (sender_user_id = auth.uid());

-- ─────────────────────────────────────────────────────────────────
-- 3. agent_tasks — per-agent task queue with lifecycle state machine.
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_tasks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id UUID NOT NULL REFERENCES ai_agents(id) ON DELETE CASCADE,
  parent_task_id UUID REFERENCES agent_tasks(id) ON DELETE SET NULL,
  root_task_id UUID REFERENCES agent_tasks(id) ON DELETE SET NULL,
  inbox_message_id UUID REFERENCES agent_inbox(id) ON DELETE SET NULL,
  user_id UUID NOT NULL,
  title TEXT,
  payload JSONB NOT NULL,
  lifecycle_status TEXT NOT NULL DEFAULT 'queued' CHECK (lifecycle_status IN
    ('queued', 'assigned', 'in_progress', 'waiting_for_other', 'blocked',
     'done', 'failed', 'cancelled')),
  current_run_id UUID REFERENCES agent_runs(id) ON DELETE SET NULL,
  result JSONB,
  error_code TEXT,
  error_message TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  assigned_at TIMESTAMPTZ,
  started_at TIMESTAMPTZ,
  ended_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_tasks_queue
  ON agent_tasks(agent_id, created_at)
  WHERE lifecycle_status IN ('queued', 'assigned');
CREATE INDEX IF NOT EXISTS idx_tasks_active
  ON agent_tasks(agent_id, started_at DESC)
  WHERE lifecycle_status IN ('in_progress', 'waiting_for_other');
CREATE INDEX IF NOT EXISTS idx_tasks_user_recent
  ON agent_tasks(user_id, created_at DESC);

-- Now add the FK from agent_inbox.task_id → agent_tasks.id (deferred until both tables exist).
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'agent_inbox_task_id_fkey'
  ) THEN
    ALTER TABLE agent_inbox
      ADD CONSTRAINT agent_inbox_task_id_fkey
      FOREIGN KEY (task_id) REFERENCES agent_tasks(id) ON DELETE SET NULL;
  END IF;
END$$;

ALTER TABLE agent_tasks ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "tasks_service_full" ON agent_tasks;
CREATE POLICY "tasks_service_full" ON agent_tasks FOR ALL TO service_role
  USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "tasks_owner_readable" ON agent_tasks;
CREATE POLICY "tasks_owner_readable" ON agent_tasks FOR SELECT
  USING (user_id = auth.uid());

-- ─────────────────────────────────────────────────────────────────
-- 4. agent_state_history — audit trail for state machine transitions.
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_state_history (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id UUID NOT NULL REFERENCES ai_agents(id) ON DELETE CASCADE,
  from_state TEXT,
  to_state TEXT NOT NULL,
  trigger TEXT NOT NULL,                   -- 'task_assigned' / 'task_completed' / 'cancel' / 'heartbeat_lost'
  task_id UUID REFERENCES agent_tasks(id) ON DELETE SET NULL,
  metadata_json JSONB NOT NULL DEFAULT '{}',
  changed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_state_history_agent
  ON agent_state_history(agent_id, changed_at DESC);

ALTER TABLE agent_state_history ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "state_history_service_full" ON agent_state_history;
CREATE POLICY "state_history_service_full" ON agent_state_history FOR ALL TO service_role
  USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "state_history_owner_readable" ON agent_state_history;
CREATE POLICY "state_history_owner_readable" ON agent_state_history FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM ai_agents a
      WHERE a.id = agent_state_history.agent_id
        AND a.user_id = auth.uid()
    )
  );

-- ─────────────────────────────────────────────────────────────────
-- 5. agent_outbox — outgoing messages (audit + Realtime delivery).
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_outbox (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  sender_agent_id UUID NOT NULL REFERENCES ai_agents(id) ON DELETE CASCADE,
  recipient_kind TEXT NOT NULL CHECK (recipient_kind IN
    ('user', 'agent', 'broadcast')),
  recipient_user_id UUID,
  recipient_agent_id UUID REFERENCES ai_agents(id) ON DELETE SET NULL,
  message_type TEXT NOT NULL,
  payload JSONB NOT NULL,
  task_id UUID REFERENCES agent_tasks(id) ON DELETE SET NULL,
  delivered BOOLEAN NOT NULL DEFAULT false,
  delivered_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_outbox_sender
  ON agent_outbox(sender_agent_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_outbox_recipient_user
  ON agent_outbox(recipient_user_id, created_at DESC)
  WHERE recipient_user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_outbox_recipient_agent
  ON agent_outbox(recipient_agent_id, created_at DESC)
  WHERE recipient_agent_id IS NOT NULL;

ALTER TABLE agent_outbox ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "outbox_service_full" ON agent_outbox;
CREATE POLICY "outbox_service_full" ON agent_outbox FOR ALL TO service_role
  USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "outbox_recipient_readable" ON agent_outbox;
CREATE POLICY "outbox_recipient_readable" ON agent_outbox FOR SELECT
  USING (recipient_user_id = auth.uid());

-- ─────────────────────────────────────────────────────────────────
-- Cascade cancel trigger: cancel root run → cancel all children in tree.
-- ─────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION cascade_cancel_run() RETURNS TRIGGER AS $$
BEGIN
  IF NEW.cancel_requested = true AND OLD.cancel_requested = false THEN
    UPDATE agent_runs
       SET cancel_requested = true
     WHERE root_run_id = COALESCE(NEW.root_run_id, NEW.id)
       AND status = 'running'
       AND id != NEW.id;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_cascade_cancel_run ON agent_runs;
CREATE TRIGGER trg_cascade_cancel_run
  AFTER UPDATE OF cancel_requested ON agent_runs
  FOR EACH ROW
  EXECUTE FUNCTION cascade_cancel_run();

-- updated_at auto-bump trigger for agent_tasks
CREATE OR REPLACE FUNCTION bump_agent_tasks_updated_at() RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_bump_agent_tasks_updated_at ON agent_tasks;
CREATE TRIGGER trg_bump_agent_tasks_updated_at
  BEFORE UPDATE ON agent_tasks
  FOR EACH ROW
  EXECUTE FUNCTION bump_agent_tasks_updated_at();

COMMENT ON TABLE agent_workers IS
  'M2 Persistent Workforce: one row per agent runtime state.';
COMMENT ON TABLE agent_inbox IS
  'M2 Persistent Workforce: incoming messages (user/agent/system/schedule).';
COMMENT ON TABLE agent_tasks IS
  'M2 Persistent Workforce: per-agent task queue with lifecycle state machine.';
COMMENT ON TABLE agent_state_history IS
  'M2 Persistent Workforce: audit trail for state machine transitions.';
COMMENT ON TABLE agent_outbox IS
  'M2 Persistent Workforce: outgoing messages, Realtime delivery channel.';
