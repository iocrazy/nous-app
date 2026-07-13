-- 358_project_lib_entities.sql
--
-- Location + prop libraries (character canvas epic SP1, user-extended
-- 2026-07-13). One GENERALIZED table serves both — adding future library
-- kinds (costumes, vehicles) is a CHECK widen, not a new table. Characters
-- keep their already-shipped dedicated table (mig 357, role_tag semantics).
--
-- badge_tag carries the per-type badge: location → interior/exterior,
-- prop → hero/set/costume. Free-form grouped chips live in tags jsonb.

CREATE TABLE IF NOT EXISTS project_lib_entities (
    id           BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    project_id   BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    entity_type  TEXT NOT NULL CHECK (entity_type IN ('location', 'prop')),
    name         VARCHAR(100) NOT NULL,
    badge_tag    TEXT NOT NULL DEFAULT '',
    description  TEXT NOT NULL DEFAULT '',
    tags         JSONB NOT NULL DEFAULT '{}'::jsonb,
    cover_url    TEXT,
    -- 'manual' | 'script' (locations extract from scene headers; props have
    -- no derivation source — always manual).
    source       TEXT NOT NULL DEFAULT 'manual'
                 CHECK (source IN ('manual', 'script')),
    sort_order   INT NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Extract upserts by (project, type, name).
CREATE UNIQUE INDEX IF NOT EXISTS uq_project_lib_entities_ptn
    ON project_lib_entities (project_id, entity_type, name);

CREATE INDEX IF NOT EXISTS idx_project_lib_entities_project_type
    ON project_lib_entities (project_id, entity_type, sort_order);

-- canvases.kind += location / prop (each gets its own preset-workflow face).
ALTER TABLE canvases DROP CONSTRAINT IF EXISTS canvases_kind_check;
ALTER TABLE canvases
    ADD CONSTRAINT canvases_kind_check
    CHECK (kind IN ('smart', 'classic', 'character', 'location', 'prop'));

NOTIFY pgrst, 'reload schema';
