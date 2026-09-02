-- 447_rename_legacy_project_entity_tables.sql
--
-- Asset Library P3 tail (spec 2026-08-28-asset-library-loadout-design §3.8
-- 退役): rename the two project-local entity tables to `_legacy_*`.
--
-- WHY NOW. The migration workflow `backfill_assets_from_project_entities`
-- ran live in production on 2026-09-02 and reconciled all-present: 8 assets
-- + 8 `asset_project_refs` created, 3 entity canvases linked, and an
-- idempotent re-run reported 0 created / 8 existing / 0 refs added. Every
-- row these two tables held now lives in `assets` + `asset_project_refs`,
-- which is what the workspace pages read (P3 Task 6 moved the four sidebar
-- modules onto `GET /api/v1/projects/{pid}/assets`). Frontend readers and
-- writers of the old tables: zero, re-verified on this PR.
--
-- THIS IS THE RENAME HALF, NOT THE DROP. §3.8 prescribes two steps: rename
-- for one release cycle, then DROP. The DROP is P6. Renaming first is what
-- makes a missed reader loud (42P01 at the first query) instead of silent,
-- and it keeps the data recoverable for the whole window.
--
-- WHAT STILL READS THESE TABLES AFTER THIS MIGRATION. Exactly one thing:
-- the migration workflow itself, via the ORM models whose `__tablename__`
-- this PR repoints at the new names. That is deliberate — an emergency
-- re-run has to stay possible for the length of the legacy window. It is
-- not scheduled and only an admin can start it. All the REST endpoints that
-- used to read them (`/projects/{id}/characters*` and `/projects/{id}/lib/*`)
-- are deleted in this same PR, along with their two repositories.
--
-- DEPENDENT OBJECTS. Postgres resolves constraints, indexes, policies and
-- FKs by OID, so ALTER TABLE ... RENAME leaves every one of them attached
-- and working; only their *names* keep the old spelling. Enumerated from
-- migrations 357 / 358 / 364 and from schema_baseline.sql:
--   * PK           project_characters_pkey, project_lib_entities_pkey
--   * FK           *_project_id_fkey → projects(id) ON DELETE CASCADE
--   * CHECK        *_role_tag_check, *_source_check, *_entity_type_check
--   * INDEX        uq_project_characters_project_name,
--                  idx_project_characters_project,
--                  uq_project_lib_entities_ptn,
--                  idx_project_lib_entities_project_type
--   * RLS POLICY   "Service role full access on project_characters" and the
--                  project_lib_entities twin (mig 364); RLS stays ENABLED
--                  across the rename, so the tables remain service-role-only
--                  through PostgREST.
--   * SEQUENCES    none — `id` defaults to generate_snowflake_id().
--   * VIEWS / TRIGGERS / other tables referencing them: none. Nothing else
--     in supabase/ names either table (mig 375 and 445 only mention them in
--     prose comments).
-- Those stale names are cosmetic for one release cycle and disappear with
-- the P6 DROP, so they are left alone: renaming them would be four more
-- statements that have to stay in lockstep with the ORM `__table_args__`
-- for no behavioural gain.
--
-- RECOVERY. Rename back — no data is touched by this migration:
--   ALTER TABLE IF EXISTS public._legacy_project_characters
--       RENAME TO project_characters;
--   ALTER TABLE IF EXISTS public._legacy_project_lib_entities
--       RENAME TO project_lib_entities;
--   NOTIFY pgrst, 'reload schema';
-- and revert the two `__tablename__` values in
-- backend/app/models/project_library.py in the same breath.
--
-- No SET ROLE: this runs as the connecting role (postgres), per mig 365.

BEGIN;

ALTER TABLE IF EXISTS public.project_characters
    RENAME TO _legacy_project_characters;

ALTER TABLE IF EXISTS public.project_lib_entities
    RENAME TO _legacy_project_lib_entities;

-- PostgREST caches the schema; without this the old names stay routable and
-- the new ones are 404 until the next reload (CI-migration-skips-reload trap).
NOTIFY pgrst, 'reload schema';

COMMIT;
