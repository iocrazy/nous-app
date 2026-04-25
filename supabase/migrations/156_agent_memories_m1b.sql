-- M1.B: agent_memories schema extensions for Memory v1.
--
-- 5-layer namespace via 'scope' enum. M1.B writes only the 'agent_user'
-- layer (per plan-eng-review Cross-Model Tension #2 — outside voice
-- challenged 5 layers as design speculation, accepted: schema keeps all
-- 5 fields for forward compat, writers only use agent_user in M1.B).

ALTER TABLE agent_memories
  ADD COLUMN IF NOT EXISTS user_id UUID,
  ADD COLUMN IF NOT EXISTS scope TEXT,
  ADD COLUMN IF NOT EXISTS when_to_use TEXT,
  ADD COLUMN IF NOT EXISTS reinforcement_count INT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS last_recalled_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS extracted_from TEXT;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'agent_memories_scope_check'
  ) THEN
    ALTER TABLE agent_memories
      ADD CONSTRAINT agent_memories_scope_check
      CHECK (scope IS NULL OR scope IN
        ('session', 'agent_user', 'user_global', 'team_agent', 'root_tree'));
  END IF;
END$$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'agent_memories_extracted_from_check'
  ) THEN
    ALTER TABLE agent_memories
      ADD CONSTRAINT agent_memories_extracted_from_check
      CHECK (extracted_from IS NULL OR extracted_from IN ('user_msg', 'assistant_msg'));
  END IF;
END$$;

CREATE INDEX IF NOT EXISTS idx_agent_memories_namespace
  ON agent_memories(agent_id, user_id, scope, created_at DESC)
  WHERE user_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_agent_memories_embedding
  ON agent_memories USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 100);

DROP POLICY IF EXISTS "own_memories_readable" ON agent_memories;
CREATE POLICY "own_memories_readable" ON agent_memories FOR SELECT
  USING (
    user_id = auth.uid()
    OR (
      user_id IS NULL
      AND EXISTS (
        SELECT 1 FROM ai_agents a
        WHERE a.id = agent_memories.agent_id
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

COMMENT ON COLUMN agent_memories.scope IS
  'Namespace layer. M1.B writes only agent_user; M2/M3 may activate others.';
COMMENT ON COLUMN agent_memories.when_to_use IS
  'Embedding is built on THIS field, not summary. RemiMem pattern: better recall.';
COMMENT ON COLUMN agent_memories.reinforcement_count IS
  'Increments each time this memory is recalled. Used in salience scoring (MemU pattern).';
COMMENT ON COLUMN agent_memories.extracted_from IS
  'Which message channel this came from. Mem Zero dual-prompt isolation.';
