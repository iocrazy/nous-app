-- agent_memories: scaffold for future memory / RAG retrieval.
--
-- v1 does NOT INSERT into this table (finding #8 — empty-embedding rows
-- are bloat with zero value). The memory PR lands the writer + ivfflat
-- index + retrieval flow. Existing this table now means that PR doesn't
-- need another migration + backfill.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE agent_memories (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id UUID NOT NULL REFERENCES ai_agents(id) ON DELETE CASCADE,
  run_id UUID REFERENCES agent_runs(id) ON DELETE SET NULL,
  summary TEXT NOT NULL,
  embedding VECTOR(1536),
  metadata_json JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_agent_memories_agent ON agent_memories(agent_id, created_at DESC);
-- ivfflat / hnsw index on embedding deferred to memory PR (add when retrieval ships)

ALTER TABLE agent_memories ENABLE ROW LEVEL SECURITY;

-- Mirror agent_runs RLS: read your own agent's memories.
CREATE POLICY "own_memories_readable" ON agent_memories FOR SELECT
  USING (
    EXISTS (
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
  );

CREATE POLICY "memories_write_service_only" ON agent_memories FOR ALL TO service_role
  USING (true) WITH CHECK (true);
