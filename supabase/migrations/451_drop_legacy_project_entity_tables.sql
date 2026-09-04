-- 451_drop_legacy_project_entity_tables.sql
--
-- Asset Library P6 (spec 2026-08-28-asset-library-loadout-design §3.8): DROP
-- the two project-local entity tables that mig 447 renamed to `_legacy_*`.
-- This is the second and final half of the two-step retirement §3.8
-- prescribes: rename for one release cycle so a missed reader is LOUD (42P01
-- at its first query), then drop.
--
-- NUMBERING. 450_chat_uploads_system_folder.sql (the sibling P6 PR, #2108)
-- landed on master first; this file was rebased onto it and 451 is the next
-- free number. The two are independent in content — 450 adopts the
-- chat-uploads folder, 451 touches only these two tables — so the order
-- between them never mattered for correctness, only for the number not
-- colliding.
--
-- ⚠️ THIS IS IRREVERSIBLE. `DROP TABLE` destroys the rows. There is no
-- rename-back recovery like mig 447 had, and `run-migration.yml` takes NO
-- backup of any kind — it has no pg_dump step, no snapshot, and no DB-side
-- equivalent of the backend chain's `:rollback` image tag. The ONLY recovery
-- is the dump the operator takes by hand BEFORE this merges:
--
--     docker exec nous-db pg_dump -U postgres -p 55434 -d postgres \
--       -t public._legacy_project_characters \
--       -t public._legacy_project_lib_entities \
--       > ~/legacy-project-entities-$(date +%Y%m%d).sql
--
--   (port 55434, not 5432 — see CLAUDE.md. Keep the file OUTSIDE the repo:
--   this repository is public.)
--
-- WHY IT IS SAFE TO DROP NOW. Every row these tables held was migrated into
-- `assets` + `asset_project_refs` by `backfill_assets_from_project_entities`,
-- which ran live in production on 2026-09-02 and reconciled all-present
-- (8 assets + 8 refs created, 3 entity canvases linked; an idempotent re-run
-- reported 0 created / 8 existing / 0 refs added). Mig 447 then deleted every
-- REST reader (`/projects/{id}/characters*`, `/projects/{id}/lib/*`), both
-- repositories and both Pydantic schemas. The single surviving reader was
-- that migration workflow, kept importable only so an emergency re-run stayed
-- possible inside the legacy window; the window closes here, and this PR
-- deletes the workflow, its two ORM models, its `_BACKFILLS` entry and its
-- tests in the SAME commit. It has to be the same commit: the schema-drift
-- gate refuses BOTH "model without table" and "table without model" with a
-- ceiling of zero, so neither half can be green on its own.
--
-- WHAT DOES *NOT* DIE WITH THEM. `app/services/assets/legacy_refs.py` and
-- `GET /api/v1/assets/resolve-legacy` stay. The strings in
-- `assets.attrs.legacy_ids` (`"project_characters"` / `"project_lib_entities"`,
-- deliberately in the PRE-rename spelling) are provenance LABELS, not SQL
-- identifiers — nothing resolves them to a table. That endpoint reads
-- `assets.attrs`, so it keeps mapping pre-P3 canvas cards to their migrated
-- asset after these tables are gone. Deleting it would strand old cards on an
-- `Unmigrated` badge forever.
--
-- NO CASCADE, ON PURPOSE. Every dependent object is OWNED BY these two tables
-- and dies with them; nothing outside them depends on them. Enumerated from
-- schema_baseline.sql (:6015-:6142, :9561-:9613, :11543-:12778, :13949-:14057,
-- :15401-:17350) and mig 447's own DEPENDENT OBJECTS section — the names still
-- carry the PRE-rename spelling because `ALTER TABLE ... RENAME` re-points
-- OIDs, not names, and 447 deliberately left them alone knowing they die here:
--   * PK           project_characters_pkey, project_lib_entities_pkey
--   * FK (OUTBOUND, these tables → projects(id) ON DELETE CASCADE)
--                  project_characters_project_id_fkey,
--                  project_lib_entities_project_id_fkey
--   * CHECK        project_characters_role_tag_check,
--                  project_characters_source_check,
--                  project_lib_entities_entity_type_check,
--                  project_lib_entities_source_check
--   * INDEX        uq_project_characters_project_name,
--                  idx_project_characters_project,
--                  uq_project_lib_entities_ptn,
--                  idx_project_lib_entities_project_type
--   * RLS POLICY   "Service role full access on project_characters" and the
--                  project_lib_entities twin (mig 364)
--   * INBOUND FKs / VIEWS / TRIGGERS / SEQUENCES: none. No other table
--     references either one (`id` defaults to generate_snowflake_id(), so
--     there is no owned sequence), and nothing else in supabase/ names them
--     outside prose comments (migs 375, 445, 449).
-- Writing CASCADE anyway would silently drop anything a future migration
-- attaches, which is precisely the signal this DROP wants to keep loud.
--
-- No SET ROLE: this runs as the connecting role (postgres), per mig 365.

BEGIN;

DROP TABLE IF EXISTS public._legacy_project_characters;

DROP TABLE IF EXISTS public._legacy_project_lib_entities;

-- PostgREST caches the schema; without this the dropped names stay routable
-- until the next reload (CI-migration-skips-reload trap, same as mig 447).
NOTIFY pgrst, 'reload schema';

COMMIT;
