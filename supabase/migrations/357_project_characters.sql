-- 357_project_characters.sql
--
-- Character canvas epic (2026-07-13) PR-CC1: the authored character library.
--
-- Why a NEW table: the old storyboard_characters hangs off the retired
-- storyboard_projects tree (mig 107) — reviving that FK would drag a retired
-- subsystem back into the main project chain. Workspace "characters" today
-- are read-only entities derived from script cues (get_project_entities);
-- this table is where they become authored rows (the Extract action upserts
-- by name) that the character bible cards and the character canvas bind to.
--
-- Also widens canvases.kind for the character canvas face: a kind='character'
-- canvas reuses the smart pipeline (composer/generation/polling) with the
-- CharacterNode + preset agent workflow on top.

-- 1) project_characters ------------------------------------------------------
CREATE TABLE IF NOT EXISTS project_characters (
    id           BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    project_id   BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name         VARCHAR(100) NOT NULL,
    -- Display role on the bible card: lead / support / antagonist / '' (unset).
    role_tag     TEXT NOT NULL DEFAULT ''
                 CHECK (role_tag IN ('', 'lead', 'support', 'antagonist')),
    description  TEXT NOT NULL DEFAULT '',
    -- Grouped chips: {"personality": [...], "conflict": [...], "habit": [...],
    -- "appearance": [...]} — free-form groups, client-rendered.
    tags         JSONB NOT NULL DEFAULT '{}'::jsonb,
    portrait_url TEXT,
    -- 'manual' (authored in the library) | 'script' (Extract from script).
    source       TEXT NOT NULL DEFAULT 'manual'
                 CHECK (source IN ('manual', 'script')),
    sort_order   INT NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Extract-from-script upserts by (project, name) — enforce it.
CREATE UNIQUE INDEX IF NOT EXISTS uq_project_characters_project_name
    ON project_characters (project_id, name);

CREATE INDEX IF NOT EXISTS idx_project_characters_project
    ON project_characters (project_id, sort_order);

-- 2) canvases.kind += 'character' --------------------------------------------
ALTER TABLE canvases DROP CONSTRAINT IF EXISTS canvases_kind_check;
ALTER TABLE canvases
    ADD CONSTRAINT canvases_kind_check
    CHECK (kind IN ('smart', 'classic', 'character'));

-- PostgREST schema reload (CI-migration-skips-reload trap).
NOTIFY pgrst, 'reload schema';
