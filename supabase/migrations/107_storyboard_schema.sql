-- Storyboard Schema Migration
-- Date: 2026-03-16
-- Description: Creates all 8 storyboard tables with indexes, triggers, RLS policies, and realtime publication.
-- Depends on: 051_snowflake_migration.sql (generate_snowflake_id, get_user_team_ids)

-- ============================================================================
-- 1. storyboard_projects
-- ============================================================================

CREATE TABLE IF NOT EXISTS storyboard_projects (
    id              BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    team_id         BIGINT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    created_by      UUID NOT NULL REFERENCES auth.users(id) ON DELETE SET NULL,
    name            VARCHAR(200) NOT NULL,
    description     TEXT,
    cover_image_url TEXT,
    viewport_json   JSONB,
    settings_json   JSONB,
    status          VARCHAR(20) NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'archived', 'deleted')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================================
-- 2. storyboard_characters
-- ============================================================================

CREATE TABLE IF NOT EXISTS storyboard_characters (
    id                   BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    project_id           BIGINT NOT NULL REFERENCES storyboard_projects(id) ON DELETE CASCADE,
    name                 VARCHAR(100) NOT NULL,
    description          TEXT,
    reference_image_url  TEXT,
    thumbnail_url        TEXT,
    visual_traits        JSONB,
    sort_order           INT NOT NULL DEFAULT 0,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================================
-- 3. storyboard_nodes
-- ============================================================================

CREATE TABLE IF NOT EXISTS storyboard_nodes (
    id          BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    project_id  BIGINT NOT NULL REFERENCES storyboard_projects(id) ON DELETE CASCADE,
    node_type   VARCHAR(30) NOT NULL
                    CHECK (node_type IN (
                        'upload', 'image_edit', 'storyboard_split', 'storyboard_gen',
                        'text_annotation', 'group', 'export', 'image_to_video'
                    )),
    position_x  FLOAT NOT NULL DEFAULT 0,
    position_y  FLOAT NOT NULL DEFAULT 0,
    width       FLOAT,
    height      FLOAT,
    data_json   JSONB NOT NULL DEFAULT '{}',
    sort_order  INT,
    locked      BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================================
-- 4. storyboard_edges
-- ============================================================================

CREATE TABLE IF NOT EXISTS storyboard_edges (
    id             BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    project_id     BIGINT NOT NULL REFERENCES storyboard_projects(id) ON DELETE CASCADE,
    source_node_id BIGINT NOT NULL REFERENCES storyboard_nodes(id) ON DELETE CASCADE,
    target_node_id BIGINT NOT NULL REFERENCES storyboard_nodes(id) ON DELETE CASCADE,
    source_handle  VARCHAR(50),
    target_handle  VARCHAR(50),
    edge_type      VARCHAR(20) NOT NULL DEFAULT 'default',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================================
-- 5. storyboard_frames
-- ============================================================================

CREATE TABLE IF NOT EXISTS storyboard_frames (
    id               BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    node_id          BIGINT NOT NULL REFERENCES storyboard_nodes(id) ON DELETE CASCADE,
    project_id       BIGINT NOT NULL REFERENCES storyboard_projects(id) ON DELETE CASCADE,
    frame_index      INT NOT NULL,
    image_url        TEXT,
    thumbnail_url    TEXT,
    note             TEXT,
    shot_type        VARCHAR(30),
    camera_angle     VARCHAR(30),
    camera_movement  VARCHAR(30),
    focal_length     VARCHAR(20),
    lighting         TEXT,
    duration_seconds FLOAT NOT NULL DEFAULT 3.0,
    transition_type  VARCHAR(20) NOT NULL DEFAULT 'cut'
                         CHECK (transition_type IN ('cut', 'fade', 'dissolve')),
    annotations_json JSONB,
    sort_order       INT NOT NULL DEFAULT 0,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (node_id, frame_index)
);

-- ============================================================================
-- 6. storyboard_frame_characters (junction)
-- ============================================================================

CREATE TABLE IF NOT EXISTS storyboard_frame_characters (
    frame_id     BIGINT NOT NULL REFERENCES storyboard_frames(id) ON DELETE CASCADE,
    character_id BIGINT NOT NULL REFERENCES storyboard_characters(id) ON DELETE CASCADE,
    PRIMARY KEY (frame_id, character_id)
);

-- ============================================================================
-- 7. storyboard_assets
-- ============================================================================

CREATE TABLE IF NOT EXISTS storyboard_assets (
    id            BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    project_id    BIGINT NOT NULL REFERENCES storyboard_projects(id) ON DELETE CASCADE,
    file_path     TEXT NOT NULL,
    file_hash     VARCHAR(64),
    file_size     BIGINT,
    mime_type     VARCHAR(50),
    width         INT,
    height        INT,
    preview_path  TEXT,
    metadata_json JSONB,
    source_type   VARCHAR(20) NOT NULL DEFAULT 'generated'
                      CHECK (source_type IN ('uploaded', 'generated', 'split', 'imported')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================================
-- 8. storyboard_video_assets
-- ============================================================================

CREATE TABLE IF NOT EXISTS storyboard_video_assets (
    id                 BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    project_id         BIGINT NOT NULL REFERENCES storyboard_projects(id) ON DELETE CASCADE,
    source_frame_id    BIGINT REFERENCES storyboard_frames(id) ON DELETE SET NULL,
    source_node_id     BIGINT REFERENCES storyboard_nodes(id) ON DELETE SET NULL,
    file_path          TEXT NOT NULL,
    thumbnail_path     TEXT,
    duration_seconds   FLOAT,
    width              INT,
    height             INT,
    file_size          BIGINT,
    provider           VARCHAR(30),
    generation_params  JSONB,
    status             VARCHAR(20) NOT NULL DEFAULT 'pending'
                           CHECK (status IN ('pending', 'processing', 'completed', 'failed')),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================================
-- 9. Indexes
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_sb_projects_team       ON storyboard_projects(team_id);
CREATE INDEX IF NOT EXISTS idx_sb_characters_project  ON storyboard_characters(project_id);
CREATE INDEX IF NOT EXISTS idx_sb_nodes_project       ON storyboard_nodes(project_id);
CREATE INDEX IF NOT EXISTS idx_sb_edges_project       ON storyboard_edges(project_id);
CREATE INDEX IF NOT EXISTS idx_sb_frames_node         ON storyboard_frames(node_id);
CREATE INDEX IF NOT EXISTS idx_sb_frames_project      ON storyboard_frames(project_id);
CREATE INDEX IF NOT EXISTS idx_sb_assets_hash         ON storyboard_assets(file_hash);
CREATE INDEX IF NOT EXISTS idx_sb_frame_chars_character ON storyboard_frame_characters(character_id);

-- ============================================================================
-- 10. updated_at trigger function + triggers
-- ============================================================================

CREATE OR REPLACE FUNCTION update_sb_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_sb_projects_updated_at ON storyboard_projects;
CREATE TRIGGER trg_sb_projects_updated_at
    BEFORE UPDATE ON storyboard_projects
    FOR EACH ROW EXECUTE FUNCTION update_sb_updated_at();

DROP TRIGGER IF EXISTS trg_sb_characters_updated_at ON storyboard_characters;
CREATE TRIGGER trg_sb_characters_updated_at
    BEFORE UPDATE ON storyboard_characters
    FOR EACH ROW EXECUTE FUNCTION update_sb_updated_at();

DROP TRIGGER IF EXISTS trg_sb_nodes_updated_at ON storyboard_nodes;
CREATE TRIGGER trg_sb_nodes_updated_at
    BEFORE UPDATE ON storyboard_nodes
    FOR EACH ROW EXECUTE FUNCTION update_sb_updated_at();

DROP TRIGGER IF EXISTS trg_sb_frames_updated_at ON storyboard_frames;
CREATE TRIGGER trg_sb_frames_updated_at
    BEFORE UPDATE ON storyboard_frames
    FOR EACH ROW EXECUTE FUNCTION update_sb_updated_at();

-- ============================================================================
-- 11. Enable RLS
-- ============================================================================

ALTER TABLE storyboard_projects       ENABLE ROW LEVEL SECURITY;
ALTER TABLE storyboard_characters     ENABLE ROW LEVEL SECURITY;
ALTER TABLE storyboard_nodes          ENABLE ROW LEVEL SECURITY;
ALTER TABLE storyboard_edges          ENABLE ROW LEVEL SECURITY;
ALTER TABLE storyboard_frames         ENABLE ROW LEVEL SECURITY;
ALTER TABLE storyboard_frame_characters ENABLE ROW LEVEL SECURITY;
ALTER TABLE storyboard_assets         ENABLE ROW LEVEL SECURITY;
ALTER TABLE storyboard_video_assets   ENABLE ROW LEVEL SECURITY;

-- ============================================================================
-- 12. RLS Policies — storyboard_projects (team_id based)
-- ============================================================================

CREATE POLICY "sb_projects_team_select" ON storyboard_projects
    FOR SELECT USING (
        team_id IN (SELECT get_user_team_ids(auth.uid()))
    );

CREATE POLICY "sb_projects_team_insert" ON storyboard_projects
    FOR INSERT WITH CHECK (
        team_id IN (SELECT get_user_team_ids(auth.uid()))
    );

CREATE POLICY "sb_projects_team_update" ON storyboard_projects
    FOR UPDATE
    USING (team_id IN (SELECT get_user_team_ids(auth.uid())))
    WITH CHECK (team_id IN (SELECT get_user_team_ids(auth.uid())));

CREATE POLICY "sb_projects_team_delete" ON storyboard_projects
    FOR DELETE USING (
        team_id IN (SELECT get_user_team_ids(auth.uid()))
    );

CREATE POLICY "sb_projects_service_role" ON storyboard_projects
    FOR ALL USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');

-- ============================================================================
-- 13. RLS Policies — storyboard_characters
-- ============================================================================

CREATE POLICY "sb_characters_team_select" ON storyboard_characters
    FOR SELECT USING (
        project_id IN (
            SELECT id FROM storyboard_projects
            WHERE team_id IN (SELECT get_user_team_ids(auth.uid()))
        )
    );

CREATE POLICY "sb_characters_team_insert" ON storyboard_characters
    FOR INSERT WITH CHECK (
        project_id IN (
            SELECT id FROM storyboard_projects
            WHERE team_id IN (SELECT get_user_team_ids(auth.uid()))
        )
    );

CREATE POLICY "sb_characters_team_update" ON storyboard_characters
    FOR UPDATE
    USING (
        project_id IN (
            SELECT id FROM storyboard_projects
            WHERE team_id IN (SELECT get_user_team_ids(auth.uid()))
        )
    )
    WITH CHECK (
        project_id IN (
            SELECT id FROM storyboard_projects
            WHERE team_id IN (SELECT get_user_team_ids(auth.uid()))
        )
    );

CREATE POLICY "sb_characters_team_delete" ON storyboard_characters
    FOR DELETE USING (
        project_id IN (
            SELECT id FROM storyboard_projects
            WHERE team_id IN (SELECT get_user_team_ids(auth.uid()))
        )
    );

CREATE POLICY "sb_characters_service_role" ON storyboard_characters
    FOR ALL USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');

-- ============================================================================
-- 14. RLS Policies — storyboard_nodes
-- ============================================================================

CREATE POLICY "sb_nodes_team_select" ON storyboard_nodes
    FOR SELECT USING (
        project_id IN (
            SELECT id FROM storyboard_projects
            WHERE team_id IN (SELECT get_user_team_ids(auth.uid()))
        )
    );

CREATE POLICY "sb_nodes_team_insert" ON storyboard_nodes
    FOR INSERT WITH CHECK (
        project_id IN (
            SELECT id FROM storyboard_projects
            WHERE team_id IN (SELECT get_user_team_ids(auth.uid()))
        )
    );

CREATE POLICY "sb_nodes_team_update" ON storyboard_nodes
    FOR UPDATE
    USING (
        project_id IN (
            SELECT id FROM storyboard_projects
            WHERE team_id IN (SELECT get_user_team_ids(auth.uid()))
        )
    )
    WITH CHECK (
        project_id IN (
            SELECT id FROM storyboard_projects
            WHERE team_id IN (SELECT get_user_team_ids(auth.uid()))
        )
    );

CREATE POLICY "sb_nodes_team_delete" ON storyboard_nodes
    FOR DELETE USING (
        project_id IN (
            SELECT id FROM storyboard_projects
            WHERE team_id IN (SELECT get_user_team_ids(auth.uid()))
        )
    );

CREATE POLICY "sb_nodes_service_role" ON storyboard_nodes
    FOR ALL USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');

-- ============================================================================
-- 15. RLS Policies — storyboard_edges
-- ============================================================================

CREATE POLICY "sb_edges_team_select" ON storyboard_edges
    FOR SELECT USING (
        project_id IN (
            SELECT id FROM storyboard_projects
            WHERE team_id IN (SELECT get_user_team_ids(auth.uid()))
        )
    );

CREATE POLICY "sb_edges_team_insert" ON storyboard_edges
    FOR INSERT WITH CHECK (
        project_id IN (
            SELECT id FROM storyboard_projects
            WHERE team_id IN (SELECT get_user_team_ids(auth.uid()))
        )
    );

CREATE POLICY "sb_edges_team_update" ON storyboard_edges
    FOR UPDATE
    USING (
        project_id IN (
            SELECT id FROM storyboard_projects
            WHERE team_id IN (SELECT get_user_team_ids(auth.uid()))
        )
    )
    WITH CHECK (
        project_id IN (
            SELECT id FROM storyboard_projects
            WHERE team_id IN (SELECT get_user_team_ids(auth.uid()))
        )
    );

CREATE POLICY "sb_edges_team_delete" ON storyboard_edges
    FOR DELETE USING (
        project_id IN (
            SELECT id FROM storyboard_projects
            WHERE team_id IN (SELECT get_user_team_ids(auth.uid()))
        )
    );

CREATE POLICY "sb_edges_service_role" ON storyboard_edges
    FOR ALL USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');

-- ============================================================================
-- 16. RLS Policies — storyboard_frames
-- ============================================================================

CREATE POLICY "sb_frames_team_select" ON storyboard_frames
    FOR SELECT USING (
        project_id IN (
            SELECT id FROM storyboard_projects
            WHERE team_id IN (SELECT get_user_team_ids(auth.uid()))
        )
    );

CREATE POLICY "sb_frames_team_insert" ON storyboard_frames
    FOR INSERT WITH CHECK (
        project_id IN (
            SELECT id FROM storyboard_projects
            WHERE team_id IN (SELECT get_user_team_ids(auth.uid()))
        )
    );

CREATE POLICY "sb_frames_team_update" ON storyboard_frames
    FOR UPDATE
    USING (
        project_id IN (
            SELECT id FROM storyboard_projects
            WHERE team_id IN (SELECT get_user_team_ids(auth.uid()))
        )
    )
    WITH CHECK (
        project_id IN (
            SELECT id FROM storyboard_projects
            WHERE team_id IN (SELECT get_user_team_ids(auth.uid()))
        )
    );

CREATE POLICY "sb_frames_team_delete" ON storyboard_frames
    FOR DELETE USING (
        project_id IN (
            SELECT id FROM storyboard_projects
            WHERE team_id IN (SELECT get_user_team_ids(auth.uid()))
        )
    );

CREATE POLICY "sb_frames_service_role" ON storyboard_frames
    FOR ALL USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');

-- ============================================================================
-- 17. RLS Policies — storyboard_frame_characters
-- ============================================================================

CREATE POLICY "sb_frame_chars_team_select" ON storyboard_frame_characters
    FOR SELECT USING (
        frame_id IN (
            SELECT f.id FROM storyboard_frames f
            JOIN storyboard_projects p ON p.id = f.project_id
            WHERE p.team_id IN (SELECT get_user_team_ids(auth.uid()))
        )
    );

CREATE POLICY "sb_frame_chars_team_insert" ON storyboard_frame_characters
    FOR INSERT WITH CHECK (
        frame_id IN (
            SELECT f.id FROM storyboard_frames f
            JOIN storyboard_projects p ON p.id = f.project_id
            WHERE p.team_id IN (SELECT get_user_team_ids(auth.uid()))
        )
    );

CREATE POLICY "sb_frame_chars_team_delete" ON storyboard_frame_characters
    FOR DELETE USING (
        frame_id IN (
            SELECT f.id FROM storyboard_frames f
            JOIN storyboard_projects p ON p.id = f.project_id
            WHERE p.team_id IN (SELECT get_user_team_ids(auth.uid()))
        )
    );

CREATE POLICY "sb_frame_chars_service_role" ON storyboard_frame_characters
    FOR ALL USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');

-- ============================================================================
-- 18. RLS Policies — storyboard_assets
-- ============================================================================

CREATE POLICY "sb_assets_team_select" ON storyboard_assets
    FOR SELECT USING (
        project_id IN (
            SELECT id FROM storyboard_projects
            WHERE team_id IN (SELECT get_user_team_ids(auth.uid()))
        )
    );

CREATE POLICY "sb_assets_team_insert" ON storyboard_assets
    FOR INSERT WITH CHECK (
        project_id IN (
            SELECT id FROM storyboard_projects
            WHERE team_id IN (SELECT get_user_team_ids(auth.uid()))
        )
    );

CREATE POLICY "sb_assets_team_update" ON storyboard_assets
    FOR UPDATE
    USING (
        project_id IN (
            SELECT id FROM storyboard_projects
            WHERE team_id IN (SELECT get_user_team_ids(auth.uid()))
        )
    )
    WITH CHECK (
        project_id IN (
            SELECT id FROM storyboard_projects
            WHERE team_id IN (SELECT get_user_team_ids(auth.uid()))
        )
    );

CREATE POLICY "sb_assets_team_delete" ON storyboard_assets
    FOR DELETE USING (
        project_id IN (
            SELECT id FROM storyboard_projects
            WHERE team_id IN (SELECT get_user_team_ids(auth.uid()))
        )
    );

CREATE POLICY "sb_assets_service_role" ON storyboard_assets
    FOR ALL USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');

-- ============================================================================
-- 19. RLS Policies — storyboard_video_assets
-- ============================================================================

CREATE POLICY "sb_video_assets_team_select" ON storyboard_video_assets
    FOR SELECT USING (
        project_id IN (
            SELECT id FROM storyboard_projects
            WHERE team_id IN (SELECT get_user_team_ids(auth.uid()))
        )
    );

CREATE POLICY "sb_video_assets_team_insert" ON storyboard_video_assets
    FOR INSERT WITH CHECK (
        project_id IN (
            SELECT id FROM storyboard_projects
            WHERE team_id IN (SELECT get_user_team_ids(auth.uid()))
        )
    );

CREATE POLICY "sb_video_assets_team_update" ON storyboard_video_assets
    FOR UPDATE
    USING (
        project_id IN (
            SELECT id FROM storyboard_projects
            WHERE team_id IN (SELECT get_user_team_ids(auth.uid()))
        )
    )
    WITH CHECK (
        project_id IN (
            SELECT id FROM storyboard_projects
            WHERE team_id IN (SELECT get_user_team_ids(auth.uid()))
        )
    );

CREATE POLICY "sb_video_assets_team_delete" ON storyboard_video_assets
    FOR DELETE USING (
        project_id IN (
            SELECT id FROM storyboard_projects
            WHERE team_id IN (SELECT get_user_team_ids(auth.uid()))
        )
    );

CREATE POLICY "sb_video_assets_service_role" ON storyboard_video_assets
    FOR ALL USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');

-- ============================================================================
-- 20. Realtime publication
-- ============================================================================

ALTER PUBLICATION supabase_realtime ADD TABLE storyboard_projects;
ALTER PUBLICATION supabase_realtime ADD TABLE storyboard_nodes;
ALTER PUBLICATION supabase_realtime ADD TABLE storyboard_frames;
