-- 138_ai_library_phase1.sql
-- AI Library Phase 1: create ai_agents family (if missing) + Phase 1 extensions
--
-- Context: Migration 121_ai_agent_framework.sql was written but never applied to
-- the database. This combined migration:
--   1. Idempotently creates ai_agents / ai_sessions / ai_messages / ai_usage_logs
--      (same DDL as 121, preset INSERTs skipped — seed loader handles seeding).
--   2. Applies Phase 1 extensions originally planned as a separate migration:
--      slug/identity_md/soul_md/agent_md/is_system_preset/user_id on ai_agents,
--      slug/body_md/frontmatter_json on skills, new skill_files + agent_skills
--      tables, agent_id + agent_slug on ai_sessions.
--   3. Enables RLS on the new tables with policies aligned to real schema
--      (projects.owner_id — NOT projects.user_id).
--
-- Idempotent: safe to re-run. All CREATE statements use IF NOT EXISTS; every
-- policy is re-created via DROP POLICY IF EXISTS + CREATE POLICY.

BEGIN;

-- =============================================================================
-- 1. ai_agents family (copied verbatim from 121 — seed INSERTs intentionally omitted)
-- =============================================================================

-- AI Agents
CREATE TABLE IF NOT EXISTS ai_agents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  description TEXT,
  persona TEXT NOT NULL,
  model TEXT DEFAULT 'qwen-max',
  temperature DECIMAL DEFAULT 0.7,
  max_tokens INT DEFAULT 4096,
  config_json JSONB DEFAULT '{}',
  rules JSONB DEFAULT '[]',
  team_id BIGINT,
  project_id BIGINT,
  created_by UUID,
  enabled BOOLEAN DEFAULT true,
  sort_order INT DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- AI Sessions
CREATE TABLE IF NOT EXISTS ai_sessions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL,
  team_id BIGINT,
  project_id BIGINT,
  title TEXT DEFAULT 'New Chat',
  context_type TEXT,
  context_id TEXT,
  total_tokens INT DEFAULT 0,
  message_count INT DEFAULT 0,
  status TEXT DEFAULT 'active',
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- AI Messages
CREATE TABLE IF NOT EXISTS ai_messages (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id UUID REFERENCES ai_sessions(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK (role IN ('system', 'user', 'assistant')),
  content TEXT NOT NULL,
  agent_id UUID,
  skill_id UUID,
  metadata_json JSONB DEFAULT '{}',
  prompt_tokens INT DEFAULT 0,
  completion_tokens INT DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ai_messages_session ON ai_messages(session_id, created_at);

-- Indexes for ai_sessions (list_sessions filters by user_id + sorts by updated_at)
CREATE INDEX IF NOT EXISTS idx_ai_sessions_user ON ai_sessions(user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_ai_sessions_project ON ai_sessions(project_id, updated_at DESC) WHERE project_id IS NOT NULL;

-- AI Usage Logs
CREATE TABLE IF NOT EXISTS ai_usage_logs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL,
  team_id BIGINT,
  project_id BIGINT,
  session_id UUID,
  agent_id UUID,
  action TEXT,
  model TEXT NOT NULL,
  prompt_tokens INT NOT NULL,
  completion_tokens INT NOT NULL,
  total_tokens INT GENERATED ALWAYS AS (prompt_tokens + completion_tokens) STORED,
  cost_points DECIMAL,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ai_usage_user ON ai_usage_logs(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_ai_usage_project ON ai_usage_logs(project_id, created_at);

-- Extend skills table for AI integration (from 121)
ALTER TABLE skills ADD COLUMN IF NOT EXISTS prompt_template TEXT;
ALTER TABLE skills ADD COLUMN IF NOT EXISTS input_schema JSONB;
-- skills.output_format already exists in current schema — IF NOT EXISTS makes this a no-op
ALTER TABLE skills ADD COLUMN IF NOT EXISTS output_format TEXT DEFAULT 'text';
ALTER TABLE skills ADD COLUMN IF NOT EXISTS default_agent_id UUID;

COMMENT ON TABLE ai_agents IS 'AI Agent definitions with personas and configurations';
COMMENT ON TABLE ai_sessions IS 'Multi-turn conversation threads per user per project';
COMMENT ON TABLE ai_messages IS 'Chat messages within AI sessions';
COMMENT ON TABLE ai_usage_logs IS 'Token consumption tracking for billing';

-- =============================================================================
-- 2. Phase 1 extensions on ai_agents
-- =============================================================================

ALTER TABLE ai_agents ADD COLUMN IF NOT EXISTS slug TEXT;
ALTER TABLE ai_agents ADD COLUMN IF NOT EXISTS identity_md TEXT;
ALTER TABLE ai_agents ADD COLUMN IF NOT EXISTS soul_md TEXT;
ALTER TABLE ai_agents ADD COLUMN IF NOT EXISTS agent_md TEXT;
ALTER TABLE ai_agents ADD COLUMN IF NOT EXISTS is_system_preset BOOLEAN DEFAULT false;
ALTER TABLE ai_agents ADD COLUMN IF NOT EXISTS user_id UUID;

-- Unique slug per (user_id, is_system_preset) — system presets are global (user_id IS NULL)
CREATE UNIQUE INDEX IF NOT EXISTS ux_ai_agents_slug_system ON ai_agents(slug) WHERE is_system_preset = true;
CREATE UNIQUE INDEX IF NOT EXISTS ux_ai_agents_slug_user ON ai_agents(user_id, slug) WHERE is_system_preset = false AND user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_ai_agents_user ON ai_agents(user_id) WHERE user_id IS NOT NULL;

COMMENT ON COLUMN ai_agents.slug IS 'Stable identifier (e.g. script_ai, summarize); unique per scope';
COMMENT ON COLUMN ai_agents.identity_md IS 'Markdown: who the agent is (static persona)';
COMMENT ON COLUMN ai_agents.soul_md IS 'Markdown: core values / tone / voice';
COMMENT ON COLUMN ai_agents.agent_md IS 'Markdown: operating instructions / capabilities';
COMMENT ON COLUMN ai_agents.is_system_preset IS 'true = platform-managed preset, false = user-created';

-- NOTE: No seed UPDATE. Seed loader (Task 9/10) inserts script_ai fresh with correct slug.

-- =============================================================================
-- 3. Phase 1 extensions on skills
-- =============================================================================

ALTER TABLE skills ADD COLUMN IF NOT EXISTS slug TEXT;
ALTER TABLE skills ADD COLUMN IF NOT EXISTS body_md TEXT;
ALTER TABLE skills ADD COLUMN IF NOT EXISTS frontmatter_json JSONB DEFAULT '{}';

-- Unique slug per creator scope — null created_by slugs still need uniqueness at system level
CREATE UNIQUE INDEX IF NOT EXISTS ux_skills_slug_creator ON skills(created_by, slug) WHERE created_by IS NOT NULL AND slug IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_skills_slug_system ON skills(slug) WHERE created_by IS NULL AND slug IS NOT NULL;

COMMENT ON COLUMN skills.slug IS 'Stable identifier (e.g. translate, compress)';
COMMENT ON COLUMN skills.body_md IS 'Markdown body (skill prompt/instructions)';
COMMENT ON COLUMN skills.frontmatter_json IS 'YAML frontmatter parsed as JSON (metadata, I/O schema hints)';

-- =============================================================================
-- 4. skill_files — supplementary files per skill (e.g. references, examples)
-- =============================================================================

CREATE TABLE IF NOT EXISTS skill_files (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  skill_id BIGINT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
  filename TEXT NOT NULL,
  content TEXT NOT NULL,
  mime_type TEXT DEFAULT 'text/markdown',
  sort_order INT DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_skill_files_skill ON skill_files(skill_id, sort_order);
CREATE UNIQUE INDEX IF NOT EXISTS ux_skill_files_filename ON skill_files(skill_id, filename);

COMMENT ON TABLE skill_files IS 'Supplementary files attached to a skill (references, templates, examples)';

-- =============================================================================
-- 5. agent_skills — M:N binding of agents to skills
-- =============================================================================

CREATE TABLE IF NOT EXISTS agent_skills (
  agent_id UUID NOT NULL REFERENCES ai_agents(id) ON DELETE CASCADE,
  skill_id BIGINT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
  sort_order INT DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (agent_id, skill_id)
);
CREATE INDEX IF NOT EXISTS idx_agent_skills_agent ON agent_skills(agent_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_agent_skills_skill ON agent_skills(skill_id);

COMMENT ON TABLE agent_skills IS 'Binds agents to their available skills (lazy-readable skill injection)';

-- =============================================================================
-- 6. Bind ai_sessions to agents
-- =============================================================================

ALTER TABLE ai_sessions ADD COLUMN IF NOT EXISTS agent_id UUID;
ALTER TABLE ai_sessions ADD COLUMN IF NOT EXISTS agent_slug TEXT;

CREATE INDEX IF NOT EXISTS idx_ai_sessions_agent ON ai_sessions(agent_id) WHERE agent_id IS NOT NULL;

COMMENT ON COLUMN ai_sessions.agent_id IS 'Bound agent for consistency across session turns';
COMMENT ON COLUMN ai_sessions.agent_slug IS 'Denormalized agent slug — avoids join for hot read paths';

-- =============================================================================
-- 7. RLS — enable + policies
-- =============================================================================

ALTER TABLE ai_agents ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_usage_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE skill_files ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_skills ENABLE ROW LEVEL SECURITY;

-- ---- ai_agents policies ----
-- Read: system presets (everyone), user-owned (user_id=auth.uid()), team-scope (member),
--       project-scope (project owner). created_by kept as historical fallback.
DROP POLICY IF EXISTS ai_agents_read ON ai_agents;
CREATE POLICY ai_agents_read ON ai_agents FOR SELECT
  USING (
    is_system_preset = true
    OR user_id = auth.uid()
    OR created_by = auth.uid()
    OR team_id IN (SELECT team_id FROM team_members WHERE user_id = auth.uid())
    OR project_id IN (SELECT id FROM projects WHERE owner_id = auth.uid())
  );

DROP POLICY IF EXISTS ai_agents_insert ON ai_agents;
CREATE POLICY ai_agents_insert ON ai_agents FOR INSERT
  WITH CHECK (
    is_system_preset = false
    AND (user_id = auth.uid() OR created_by = auth.uid())
  );

DROP POLICY IF EXISTS ai_agents_update ON ai_agents;
CREATE POLICY ai_agents_update ON ai_agents FOR UPDATE
  USING (
    is_system_preset = false
    AND (user_id = auth.uid() OR created_by = auth.uid())
  )
  WITH CHECK (
    is_system_preset = false
    AND (user_id = auth.uid() OR created_by = auth.uid())
  );

DROP POLICY IF EXISTS ai_agents_delete ON ai_agents;
CREATE POLICY ai_agents_delete ON ai_agents FOR DELETE
  USING (
    is_system_preset = false
    AND (user_id = auth.uid() OR created_by = auth.uid())
  );

-- ---- ai_sessions policies ----
DROP POLICY IF EXISTS ai_sessions_read ON ai_sessions;
CREATE POLICY ai_sessions_read ON ai_sessions FOR SELECT
  USING (
    user_id = auth.uid()
    OR team_id IN (SELECT team_id FROM team_members WHERE user_id = auth.uid())
    OR project_id IN (SELECT id FROM projects WHERE owner_id = auth.uid())
  );

DROP POLICY IF EXISTS ai_sessions_insert ON ai_sessions;
CREATE POLICY ai_sessions_insert ON ai_sessions FOR INSERT
  WITH CHECK (user_id = auth.uid());

DROP POLICY IF EXISTS ai_sessions_update ON ai_sessions;
CREATE POLICY ai_sessions_update ON ai_sessions FOR UPDATE
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());

DROP POLICY IF EXISTS ai_sessions_delete ON ai_sessions;
CREATE POLICY ai_sessions_delete ON ai_sessions FOR DELETE
  USING (user_id = auth.uid());

-- ---- ai_messages policies (inherit via session ownership) ----
DROP POLICY IF EXISTS ai_messages_read ON ai_messages;
CREATE POLICY ai_messages_read ON ai_messages FOR SELECT
  USING (
    session_id IN (
      SELECT id FROM ai_sessions
      WHERE user_id = auth.uid()
         OR team_id IN (SELECT team_id FROM team_members WHERE user_id = auth.uid())
         OR project_id IN (SELECT id FROM projects WHERE owner_id = auth.uid())
    )
  );

DROP POLICY IF EXISTS ai_messages_insert ON ai_messages;
CREATE POLICY ai_messages_insert ON ai_messages FOR INSERT
  WITH CHECK (
    session_id IN (SELECT id FROM ai_sessions WHERE user_id = auth.uid())
  );

DROP POLICY IF EXISTS ai_messages_delete ON ai_messages;
CREATE POLICY ai_messages_delete ON ai_messages FOR DELETE
  USING (
    session_id IN (SELECT id FROM ai_sessions WHERE user_id = auth.uid())
  );

-- ---- ai_usage_logs policies (read-only from user; writes via service_role) ----
DROP POLICY IF EXISTS ai_usage_logs_read ON ai_usage_logs;
CREATE POLICY ai_usage_logs_read ON ai_usage_logs FOR SELECT
  USING (
    user_id = auth.uid()
    OR team_id IN (SELECT team_id FROM team_members WHERE user_id = auth.uid() AND role IN ('owner', 'admin'))
  );

-- ---- skill_files policies (inherit via owning skill) ----
DROP POLICY IF EXISTS skill_files_read ON skill_files;
CREATE POLICY skill_files_read ON skill_files FOR SELECT
  USING (
    skill_id IN (
      SELECT id FROM skills
      WHERE is_public = true
         OR created_by = auth.uid()
         OR team_id IN (SELECT team_id FROM team_members WHERE user_id = auth.uid())
         OR project_id IN (SELECT id FROM projects WHERE owner_id = auth.uid())
    )
  );

DROP POLICY IF EXISTS skill_files_insert ON skill_files;
CREATE POLICY skill_files_insert ON skill_files FOR INSERT
  WITH CHECK (
    skill_id IN (SELECT id FROM skills WHERE created_by = auth.uid())
  );

DROP POLICY IF EXISTS skill_files_update ON skill_files;
CREATE POLICY skill_files_update ON skill_files FOR UPDATE
  USING (skill_id IN (SELECT id FROM skills WHERE created_by = auth.uid()))
  WITH CHECK (skill_id IN (SELECT id FROM skills WHERE created_by = auth.uid()));

DROP POLICY IF EXISTS skill_files_delete ON skill_files;
CREATE POLICY skill_files_delete ON skill_files FOR DELETE
  USING (skill_id IN (SELECT id FROM skills WHERE created_by = auth.uid()));

-- ---- agent_skills policies (inherit via owning agent) ----
DROP POLICY IF EXISTS agent_skills_read ON agent_skills;
CREATE POLICY agent_skills_read ON agent_skills FOR SELECT
  USING (
    agent_id IN (
      SELECT id FROM ai_agents
      WHERE is_system_preset = true
         OR user_id = auth.uid()
         OR created_by = auth.uid()
         OR team_id IN (SELECT team_id FROM team_members WHERE user_id = auth.uid())
         OR project_id IN (SELECT id FROM projects WHERE owner_id = auth.uid())
    )
  );

DROP POLICY IF EXISTS agent_skills_insert ON agent_skills;
CREATE POLICY agent_skills_insert ON agent_skills FOR INSERT
  WITH CHECK (
    agent_id IN (
      SELECT id FROM ai_agents
      WHERE is_system_preset = false
        AND (user_id = auth.uid() OR created_by = auth.uid())
    )
  );

DROP POLICY IF EXISTS agent_skills_delete ON agent_skills;
CREATE POLICY agent_skills_delete ON agent_skills FOR DELETE
  USING (
    agent_id IN (
      SELECT id FROM ai_agents
      WHERE is_system_preset = false
        AND (user_id = auth.uid() OR created_by = auth.uid())
    )
  );

COMMIT;
