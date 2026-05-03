-- Phase N (N1): add session_id to agent_memories so writer can store
-- which session each memory was harvested from. Threading
-- (assign_thread_for) needs this for "find recent in same session".

ALTER TABLE agent_memories
  ADD COLUMN IF NOT EXISTS session_id UUID;

-- Index for the threading.assign_thread_for "find recent in session" query
CREATE INDEX IF NOT EXISTS idx_agent_memories_session_recent
  ON agent_memories(session_id, created_at DESC)
  WHERE session_id IS NOT NULL AND status = 'active';

COMMENT ON COLUMN agent_memories.session_id IS
  'Phase N N1: session this memory was harvested from. Used by threading.assign_thread_for to group same-session memories.';
