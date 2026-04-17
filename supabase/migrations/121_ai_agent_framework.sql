-- 121_ai_agent_framework.sql
-- AI Agent Framework: agents, sessions, messages, usage logs

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

-- Indexes for ai_sessions (list_sessions queries filter by user_id + sort by updated_at)
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

-- Extend skills table for AI integration
ALTER TABLE skills ADD COLUMN IF NOT EXISTS prompt_template TEXT;
ALTER TABLE skills ADD COLUMN IF NOT EXISTS input_schema JSONB;
ALTER TABLE skills ADD COLUMN IF NOT EXISTS output_format TEXT DEFAULT 'text';
ALTER TABLE skills ADD COLUMN IF NOT EXISTS default_agent_id UUID;

-- Preset agents (system-level, created_by = NULL)
INSERT INTO ai_agents (name, description, persona, model, temperature, created_by) VALUES
('Writer', 'Professional screenwriter for story creation and chapter expansion',
 E'You are a professional screenwriter. You excel at story structure, vivid scene descriptions, and natural dialogue.\n\nOUTPUT FORMAT:\n- Scene headings: <h2>场景N：场景名 – 时间 – 内/外景</h2>\n- Action/description: <p>paragraph text</p>\n- Character dialogue: <p><strong>角色名</strong>：（动作描述）台词内容</p>\n- Scene separator: <hr>\n- Output raw HTML fragments only.',
 'qwen-max', 0.8, NULL),
('Dialogue', 'Dialogue specialist for natural character conversations',
 E'You are a dialogue expert. You make characters speak naturally with distinct voices.\nEach character should have unique speech patterns, vocabulary, and emotional expression.\nWrite dialogue in the format: <p><strong>角色名</strong>：（动作描述）台词内容</p>',
 'qwen-plus', 0.85, NULL),
('Editor', 'Senior editor for reviewing and improving content',
 E'You are a senior script editor. Review content for:\n1. Consistency (character names, timeline, locations)\n2. Pacing (too fast/slow, tension curve)\n3. Character development (motivations, arcs)\n4. Plot holes\n\nProvide specific, actionable feedback with line references.',
 'qwen-max', 0.5, NULL),
('Critic', 'Story critic for evaluation and scoring',
 E'You are a professional story critic. Analyze and score (1-10) these dimensions:\n- Structure (3-act, beats, turning points)\n- Characters (depth, growth, relationships)\n- Dialogue (authenticity, subtext)\n- Pacing (rhythm, tension)\n- Theme (clarity, resonance)\n\nOutput as JSON: {"scores": {...}, "overall": N, "summary": "...", "suggestions": [...]}',
 'qwen-turbo', 0.3, NULL),
('Scene', 'Scene description specialist for visual storytelling',
 E'You are a visual storytelling expert. Write detailed, cinematic scene descriptions with:\n- Lighting and atmosphere\n- Camera angles and movement suggestions\n- Spatial relationships between characters and objects\n- Sensory details (sounds, textures, smells)\n\nOutput in HTML format using <h2> for scene headings and <p> for descriptions.',
 'qwen-plus', 0.7, NULL);

COMMENT ON TABLE ai_agents IS 'AI Agent definitions with personas and configurations';
COMMENT ON TABLE ai_sessions IS 'Multi-turn conversation threads per user per project';
COMMENT ON TABLE ai_messages IS 'Chat messages within AI sessions';
COMMENT ON TABLE ai_usage_logs IS 'Token consumption tracking for billing';
