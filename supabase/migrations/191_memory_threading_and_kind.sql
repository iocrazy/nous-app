-- Phase M (M1 + M2): episodic threading + kind classification.
--
-- M1: thread_id groups related memories ("the conversation about the
--   failed deployment last Friday"). Memories in same thread retrieved
--   together when one matches.
-- M2: kind enum classifies declarative ("X is true") vs procedural
--   ("how to do X") vs episodic ("what happened on date Y") — retriever
--   applies different ranking weights per kind.

ALTER TABLE agent_memories
  ADD COLUMN IF NOT EXISTS thread_id UUID,
  ADD COLUMN IF NOT EXISTS kind TEXT;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'agent_memories_kind_check'
  ) THEN
    ALTER TABLE agent_memories
      ADD CONSTRAINT agent_memories_kind_check
      CHECK (kind IS NULL OR kind IN ('declarative', 'procedural', 'episodic'));
  END IF;
END$$;

-- Index for thread roll-up queries
CREATE INDEX IF NOT EXISTS idx_agent_memories_thread
  ON agent_memories(thread_id, created_at DESC)
  WHERE thread_id IS NOT NULL AND status = 'active';

-- Index for kind-filtered retrieval
CREATE INDEX IF NOT EXISTS idx_agent_memories_kind_active
  ON agent_memories(agent_id, user_id, kind, created_at DESC)
  WHERE status = 'active' AND kind IS NOT NULL;

COMMENT ON COLUMN agent_memories.thread_id IS
  'Phase M (M3.A): groups related memories. Retriever pulls full thread when one member matches.';
COMMENT ON COLUMN agent_memories.kind IS
  'Phase M (M3.C): declarative=fact / procedural=how-to / episodic=event.';
