-- Wave 5b (B1): ai_session_memory — continuously-maintained session notes.
--
-- Distinct from agent_memories (cross-session facts) and from compactor's
-- one-shot summary (transient, regenerated each compaction). This table
-- holds an evolving markdown document that a background updater
-- maintains across the session, with fixed sections.
--
-- Compactor V2 swaps THIS document in as the head summary instead of
-- spending an LLM call to summarize from scratch on every compaction
-- trigger. Quality is more stable (schema-enforced) and latency is
-- lower (no LLM call at compaction time).
--
-- One row per ai_sessions row. The body_md is the source of truth;
-- sections_json is a parsed view for query / admin UI convenience.

CREATE TABLE ai_session_memory (
  session_id UUID PRIMARY KEY REFERENCES ai_sessions(id) ON DELETE CASCADE,
  body_md TEXT NOT NULL DEFAULT '',
  sections_json JSONB NOT NULL DEFAULT '{}',
  -- Versioning: bumped on every successful update. Lets the background
  -- updater detect concurrent writes (rare — single asyncio loop typical).
  version INT NOT NULL DEFAULT 1,
  last_updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- Baselines for the dual-threshold trigger. The updater computes
  -- "delta since last update" against these.
  tokens_at_last_update INT NOT NULL DEFAULT 0,
  tool_calls_at_last_update INT NOT NULL DEFAULT 0,
  turns_at_last_update INT NOT NULL DEFAULT 0
);

-- One per (user_id, agent_slug) lookup pattern via the FK is fine —
-- session_id PK gives single-row lookup. No additional index needed.

ALTER TABLE ai_session_memory ENABLE ROW LEVEL SECURITY;

-- Read your own session's memory (RLS follows ai_sessions ownership)
CREATE POLICY "own_session_memory_readable" ON ai_session_memory FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM ai_sessions s
      WHERE s.id = ai_session_memory.session_id
        AND s.user_id = auth.uid()
    )
  );

CREATE POLICY "session_memory_write_service_only" ON ai_session_memory
  FOR ALL TO service_role USING (true) WITH CHECK (true);

COMMENT ON TABLE ai_session_memory IS
  'Wave 5b: continuously-maintained session notes with fixed schema. Replaces compactor''s one-shot summary at compaction time.';
COMMENT ON COLUMN ai_session_memory.body_md IS
  'Markdown source of truth. Updated by background updater on dual-threshold trigger.';
COMMENT ON COLUMN ai_session_memory.sections_json IS
  'Parsed-out sections for query/admin: {title, current_state, task_spec, key_files, workflow_steps, errors_and_fixes}.';
COMMENT ON COLUMN ai_session_memory.version IS
  'Optimistic concurrency: bumped on each successful update.';
