-- 295_project_sop_stages.sql
-- Phase 5b (Canvas+AI plan): project lifecycle SOP stages.
-- Global seeded catalog (project_stages); projects.current_stage_id points at the
-- active stage; project_stage_history is append-only transition audit.
-- NOTE: source plan said "FK -> workflow_nodes.id BIGINT" but workflow_nodes.id is
-- UUID + Kanban semantics; resolved to a dedicated BIGINT catalog (OPEN QUESTION #1, Option B).
-- Plan used migration 290 but 290-294 already existed; bumped to 295.

CREATE TABLE IF NOT EXISTS project_stages (
    id                BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    slug              TEXT NOT NULL UNIQUE,
    name              TEXT NOT NULL,
    sort_order        INT  NOT NULL DEFAULT 0,
    tools_recommended JSONB NOT NULL DEFAULT '[]',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

DROP TRIGGER IF EXISTS trg_project_stages_updated_at ON project_stages;
CREATE TRIGGER trg_project_stages_updated_at
    BEFORE UPDATE ON project_stages
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

INSERT INTO project_stages (slug, name, sort_order, tools_recommended) VALUES
    ('planning',   'Planning',   10, '["files","tasks"]'),
    ('script',     'Script',     20, '["scripts","files"]'),
    ('storyboard', 'Storyboard', 30, '["storyboard","scripts"]'),
    ('generation', 'Generation', 40, '["storyboard","output"]'),
    ('review',     'Review',     50, '["output","tasks"]'),
    ('delivery',   'Delivery',   60, '["output","files"]')
ON CONFLICT (slug) DO NOTHING;

ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS current_stage_id BIGINT
    REFERENCES project_stages(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_projects_current_stage
    ON projects (current_stage_id) WHERE current_stage_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS project_stage_history (
    id              BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    project_id      BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    stage_id        BIGINT NOT NULL REFERENCES project_stages(id),
    entered_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    exited_at       TIMESTAMPTZ,
    transitioned_by UUID REFERENCES auth.users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_project_stage_history_project
    ON project_stage_history (project_id, entered_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_project_stage_history_open
    ON project_stage_history (project_id) WHERE exited_at IS NULL;

ALTER TABLE project_stages ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS project_stages_read ON project_stages;
CREATE POLICY project_stages_read ON project_stages
    FOR SELECT USING (auth.role() = 'authenticated' OR auth.role() = 'service_role');
DROP POLICY IF EXISTS project_stages_service_role ON project_stages;
CREATE POLICY project_stages_service_role ON project_stages
    FOR ALL USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');

ALTER TABLE project_stage_history ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS project_stage_history_member_all ON project_stage_history;
CREATE POLICY project_stage_history_member_all ON project_stage_history
    FOR ALL
    USING (project_id IN (SELECT id FROM projects WHERE owner_id = auth.uid()
               OR team_id IN (SELECT team_id FROM team_members WHERE user_id = auth.uid())))
    WITH CHECK (project_id IN (SELECT id FROM projects WHERE owner_id = auth.uid()
               OR team_id IN (SELECT team_id FROM team_members WHERE user_id = auth.uid())));
DROP POLICY IF EXISTS project_stage_history_service_role ON project_stage_history;
CREATE POLICY project_stage_history_service_role ON project_stage_history
    FOR ALL USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
