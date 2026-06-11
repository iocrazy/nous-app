-- 283_project_style_profile.sql
-- Phase 4 M8 (Canvas+AI plan): per-project style profile.
-- One row per project: freeform style guidance (style_md), structured
-- visual style (visual_style jsonb), and reference links/resources
-- (reference_links jsonb array). Generalizes what storyboard_characters
-- captures per-character up to the project level; consumed by AI
-- generation prompts and (later) Graphiti project-group ingestion.

CREATE TABLE IF NOT EXISTS project_style_profile (
    project_id      BIGINT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    style_md        TEXT NOT NULL DEFAULT '',
    visual_style    JSONB NOT NULL DEFAULT '{}',
    reference_links JSONB NOT NULL DEFAULT '[]',
    updated_by      UUID REFERENCES auth.users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

DROP TRIGGER IF EXISTS trg_project_style_profile_updated_at ON project_style_profile;
CREATE TRIGGER trg_project_style_profile_updated_at
    BEFORE UPDATE ON project_style_profile
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- RLS: project owner or a member of the project's team; service role full.
ALTER TABLE project_style_profile ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS project_style_profile_member_all ON project_style_profile;
CREATE POLICY project_style_profile_member_all ON project_style_profile
    FOR ALL
    USING (
        project_id IN (
            SELECT id FROM projects
            WHERE owner_id = auth.uid()
               OR team_id IN (
                    SELECT team_id FROM team_members WHERE user_id = auth.uid()
               )
        )
    )
    WITH CHECK (
        project_id IN (
            SELECT id FROM projects
            WHERE owner_id = auth.uid()
               OR team_id IN (
                    SELECT team_id FROM team_members WHERE user_id = auth.uid()
               )
        )
    );

DROP POLICY IF EXISTS project_style_profile_service_role ON project_style_profile;
CREATE POLICY project_style_profile_service_role ON project_style_profile
    FOR ALL
    USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');
