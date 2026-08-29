-- 445_asset_library_core.sql
--
-- Asset Library (spec 2026-08-28-asset-library-loadout-design §3).
--
-- Why NEW tables instead of widening project_characters / project_lib_entities:
-- assets are TEAM-scoped and referenced by many projects (decision 1); the old
-- tables are project-private and will be migrated INTO these (§4) then dropped
-- in P6. Files are never moved or copied — asset_files only points at existing
-- resources rows (decision 5). readiness is derived in code, never stored.
--
-- Creation order matters: asset_files has an FK to asset_loadouts.

-- 1) assets ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS assets (
    id                 BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    -- NULL only for global system presets (is_system_preset) — see CHECK below.
    scope_id           BIGINT REFERENCES teams(id) ON DELETE CASCADE,
    asset_type         TEXT NOT NULL
                       CHECK (asset_type IN ('character','location','prop','costume','prompt','audio')),
    -- audio: music|sfx|voice ; prompt: character|storyboard|product|lighting|…
    subtype            TEXT,
    name               TEXT NOT NULL,
    -- character: lead/support/antagonist/'' ; location: exterior/interior ; others ''.
    role_tag           TEXT NOT NULL DEFAULT '',
    description        TEXT NOT NULL DEFAULT '',
    -- Type-specific attributes (location.time_of_day[], audio.duration_ms,
    -- audio.loop_range, prompt.placeholders[], board_layout). Opaque here.
    attrs              JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Consistency prompt (character/location/prop/costume) or template body (prompt).
    prompt_positive    TEXT,
    prompt_negative    TEXT,
    prompt_positive_zh TEXT,
    prompt_negative_zh TEXT,
    -- prompt type only: {"midjourney": "--ar 1:1 …", "seedream": "…", "flux": "…"}
    platform_params    JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Card portrait; NULL = first file in the primary slot.
    cover_file_id      BIGINT REFERENCES resources(id) ON DELETE SET NULL,
    source             TEXT NOT NULL DEFAULT 'manual'
                       CHECK (source IN ('manual','script_import','generated','migrated','duplicated','system_preset')),
    duplicated_from    BIGINT REFERENCES assets(id) ON DELETE SET NULL,
    is_system_preset   BOOLEAN NOT NULL DEFAULT false,
    tags               JSONB NOT NULL DEFAULT '{}'::jsonb,
    sort_order         INT NOT NULL DEFAULT 0,
    created_by         UUID,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at         TIMESTAMPTZ,
    CONSTRAINT assets_scope_or_preset CHECK (scope_id IS NOT NULL OR is_system_preset)
);

CREATE INDEX IF NOT EXISTS idx_assets_scope_type
    ON assets (scope_id, asset_type) WHERE deleted_at IS NULL;

-- Decision 14: same-name-same-type within a scope must not silently create a
-- second entity. Partial so a soft-deleted row frees the name.
CREATE UNIQUE INDEX IF NOT EXISTS uq_assets_scope_type_name
    ON assets (COALESCE(scope_id, 0), asset_type, lower(name)) WHERE deleted_at IS NULL;

-- 2) asset_loadouts ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS asset_loadouts (
    id           BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    -- character assets only (service-enforced).
    asset_id     BIGINT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    is_default   BOOLEAN NOT NULL DEFAULT false,
    -- ⊆ this character's `wears` / `holds` link targets (service-enforced).
    costume_ids  BIGINT[] NOT NULL DEFAULT '{}',
    prop_ids     BIGINT[] NOT NULL DEFAULT '{}',
    prompt_extra TEXT,
    sort_order   INT NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_asset_loadouts_asset ON asset_loadouts (asset_id, sort_order);
CREATE UNIQUE INDEX IF NOT EXISTS uq_loadout_default ON asset_loadouts (asset_id) WHERE is_default;

-- 3) asset_files ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS asset_files (
    asset_id     BIGINT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    resource_id  BIGINT NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    -- Slot names live in app/services/assets/slots.py; 'unsorted' always valid.
    slot         TEXT NOT NULL,
    loadout_id   BIGINT REFERENCES asset_loadouts(id) ON DELETE SET NULL,
    sort_order   INT NOT NULL DEFAULT 0,
    note         TEXT,
    attached_by  UUID,
    attached_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (asset_id, resource_id, slot)
);

CREATE INDEX IF NOT EXISTS idx_asset_files_resource ON asset_files (resource_id);

-- 4) asset_links ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS asset_links (
    from_asset_id BIGINT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    to_asset_id   BIGINT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    relation      TEXT NOT NULL
                  CONSTRAINT asset_links_relation_check
                  CHECK (relation IN ('wears','holds','ambience_of','voice_of')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (from_asset_id, to_asset_id, relation),
    CONSTRAINT asset_links_no_self CHECK (from_asset_id <> to_asset_id)
);

CREATE INDEX IF NOT EXISTS idx_asset_links_to ON asset_links (to_asset_id, relation);

-- 5) asset_project_refs --------------------------------------------------------
CREATE TABLE IF NOT EXISTS asset_project_refs (
    asset_id    BIGINT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    project_id  BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    linked_by   UUID,
    linked_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (asset_id, project_id)
);

CREATE INDEX IF NOT EXISTS idx_apr_project ON asset_project_refs (project_id);

-- 6) canvas_asset_refs (mirrors canvas_resource_refs; maintained in P4) --------
CREATE TABLE IF NOT EXISTS canvas_asset_refs (
    canvas_id   BIGINT NOT NULL REFERENCES canvases(id) ON DELETE CASCADE,
    asset_id    BIGINT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    node_id     TEXT NOT NULL,
    loadout_id  BIGINT REFERENCES asset_loadouts(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (canvas_id, asset_id, node_id)
);

CREATE INDEX IF NOT EXISTS idx_car_asset ON canvas_asset_refs (asset_id);

-- 7) RLS: service_role only (mirror mig 441 cover_template_usage). The frontend
-- must never read these via PostgREST — every read goes through /api/v1/assets
-- so the team-membership gate is the single authority.
ALTER TABLE assets             ENABLE ROW LEVEL SECURITY;
ALTER TABLE asset_loadouts     ENABLE ROW LEVEL SECURITY;
ALTER TABLE asset_files        ENABLE ROW LEVEL SECURITY;
ALTER TABLE asset_links        ENABLE ROW LEVEL SECURITY;
ALTER TABLE asset_project_refs ENABLE ROW LEVEL SECURITY;
ALTER TABLE canvas_asset_refs  ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS assets_service_role_all ON assets;
CREATE POLICY assets_service_role_all ON assets FOR ALL TO service_role USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS asset_loadouts_service_role_all ON asset_loadouts;
CREATE POLICY asset_loadouts_service_role_all ON asset_loadouts FOR ALL TO service_role USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS asset_files_service_role_all ON asset_files;
CREATE POLICY asset_files_service_role_all ON asset_files FOR ALL TO service_role USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS asset_links_service_role_all ON asset_links;
CREATE POLICY asset_links_service_role_all ON asset_links FOR ALL TO service_role USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS asset_project_refs_service_role_all ON asset_project_refs;
CREATE POLICY asset_project_refs_service_role_all ON asset_project_refs FOR ALL TO service_role USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS canvas_asset_refs_service_role_all ON canvas_asset_refs;
CREATE POLICY canvas_asset_refs_service_role_all ON canvas_asset_refs FOR ALL TO service_role USING (true) WITH CHECK (true);

-- PostgREST schema reload (CI-migration-skips-reload trap).
NOTIFY pgrst, 'reload schema';
