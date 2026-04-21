-- Migration 141: AI Library version capture
-- Adds ai_agent_versions, skill_versions, skill_file_versions parallel tables,
-- plus a current_version INTEGER pointer on each live table. Populated by the
-- versioned repo methods (app-layer, mirroring resource_versions from mig 044).

BEGIN;

-- ─── ai_agent_versions ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ai_agent_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id UUID NOT NULL REFERENCES ai_agents(id) ON DELETE CASCADE,
    version_number INTEGER NOT NULL,
    identity_md TEXT,
    soul_md TEXT,
    agent_md TEXT,
    model TEXT,
    temperature DOUBLE PRECISION,
    max_tokens INTEGER,
    notes TEXT,
    created_by UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ux_ai_agent_versions_agent_version UNIQUE (agent_id, version_number)
);

CREATE INDEX IF NOT EXISTS ix_ai_agent_versions_agent_id_desc
    ON ai_agent_versions(agent_id, version_number DESC);

ALTER TABLE ai_agents
    ADD COLUMN IF NOT EXISTS current_version INTEGER NOT NULL DEFAULT 1;

-- ─── skill_versions ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS skill_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    skill_id BIGINT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    version_number INTEGER NOT NULL,
    body_md TEXT,
    frontmatter_json JSONB,
    notes TEXT,
    created_by UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ux_skill_versions_skill_version UNIQUE (skill_id, version_number)
);

CREATE INDEX IF NOT EXISTS ix_skill_versions_skill_id_desc
    ON skill_versions(skill_id, version_number DESC);

ALTER TABLE skills
    ADD COLUMN IF NOT EXISTS current_version INTEGER NOT NULL DEFAULT 1;

-- ─── skill_file_versions ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS skill_file_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    skill_file_id UUID NOT NULL REFERENCES skill_files(id) ON DELETE CASCADE,
    version_number INTEGER NOT NULL,
    path TEXT NOT NULL,
    content TEXT,
    file_type TEXT,
    binary_url TEXT,
    notes TEXT,
    created_by UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ux_skill_file_versions_file_version UNIQUE (skill_file_id, version_number)
);

CREATE INDEX IF NOT EXISTS ix_skill_file_versions_file_id_desc
    ON skill_file_versions(skill_file_id, version_number DESC);

ALTER TABLE skill_files
    ADD COLUMN IF NOT EXISTS current_version INTEGER NOT NULL DEFAULT 1;

-- ─── RLS ────────────────────────────────────────────────────────────
-- Enable RLS on all 3 version tables.
-- Versions are READ-accessible to anyone who can read the parent live row
-- (the live row's RLS gates access). INSERT is restricted to service role
-- (app uses admin client in the versioned repo methods).
ALTER TABLE ai_agent_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE skill_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE skill_file_versions ENABLE ROW LEVEL SECURITY;

-- Read policies: can read a version row iff you can read its parent live row
CREATE POLICY "read own agent versions" ON ai_agent_versions
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM ai_agents a WHERE a.id = ai_agent_versions.agent_id
            -- ai_agents' own RLS filters further
        )
    );

CREATE POLICY "read own skill versions" ON skill_versions
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM skills s WHERE s.id = skill_versions.skill_id
        )
    );

CREATE POLICY "read own skill file versions" ON skill_file_versions
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM skill_files sf WHERE sf.id = skill_file_versions.skill_file_id
        )
    );

-- No INSERT/UPDATE/DELETE policies: app uses service-role client; nothing else can write.

COMMIT;
