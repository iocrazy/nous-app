-- M1.B Memory v1 RPCs: cosine recall + reinforcement update.
-- SECURITY DEFINER so service-role hooks can call them; they themselves
-- enforce user_id isolation in the WHERE clause (★ P0).

CREATE OR REPLACE FUNCTION recall_agent_memories(
  p_user_id UUID,
  p_agent_id UUID,
  p_query_embedding VECTOR(1536),
  p_limit INT DEFAULT 10,
  p_scope TEXT DEFAULT 'agent_user'
)
RETURNS TABLE (
  id UUID,
  agent_id UUID,
  user_id UUID,
  scope TEXT,
  summary TEXT,
  when_to_use TEXT,
  extracted_from TEXT,
  reinforcement_count INT,
  last_recalled_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ,
  metadata_json JSONB,
  cosine_similarity FLOAT
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT
    m.id, m.agent_id, m.user_id, m.scope, m.summary, m.when_to_use,
    m.extracted_from, m.reinforcement_count, m.last_recalled_at,
    m.created_at, m.metadata_json,
    1 - (m.embedding <=> p_query_embedding) AS cosine_similarity
  FROM agent_memories m
  WHERE m.user_id = p_user_id          -- ★ P0 isolation
    AND m.agent_id = p_agent_id
    AND m.scope = p_scope
    AND m.embedding IS NOT NULL
  ORDER BY m.embedding <=> p_query_embedding
  LIMIT p_limit;
$$;

CREATE OR REPLACE FUNCTION reinforce_agent_memories(
  p_memory_ids UUID[],
  p_now TIMESTAMPTZ
)
RETURNS VOID
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
  UPDATE agent_memories
     SET reinforcement_count = reinforcement_count + 1,
         last_recalled_at = p_now
   WHERE id = ANY(p_memory_ids);
$$;

REVOKE ALL ON FUNCTION recall_agent_memories FROM PUBLIC;
REVOKE ALL ON FUNCTION reinforce_agent_memories FROM PUBLIC;
GRANT EXECUTE ON FUNCTION recall_agent_memories TO service_role, authenticated;
GRANT EXECUTE ON FUNCTION reinforce_agent_memories TO service_role;
