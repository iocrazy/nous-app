-- 448_assets_in_library.sql
--
-- Asset Library: membership in the 资产库 becomes EXPLICIT.
--
-- THE RULING. An entity is "in the library" only when somebody deliberately
-- put it there. Creating an asset by hand, duplicating one, and Save-as-Asset
-- from the Generated inbox are all deliberate acts, so those rows are in.
-- Entities that arrive as a SIDE EFFECT of working on a project are not:
--
--   * `script_import` — 一键导入 lands every name the project's script mentions.
--     That is the project's cast list, not a curated library; the user never
--     picked those rows one by one.
--   * `migrated`      — the 2026-09-02 backfill carried the two legacy
--     project-local tables (`_legacy_project_characters` /
--     `_legacy_project_lib_entities`) into `assets`. Those rows only ever
--     existed inside one project.
--
-- Both stay fully visible and usable on their project's page
-- (`GET /projects/{id}/assets` reads `library='all'`); they are simply absent
-- from the library shelf and from the sidebar badges until someone adds them.
-- "Add To Library" is one click on the project panel or the asset sheet.
--
-- WHY A COLUMN AND NOT "derive it from `source`". Membership is a decision the
-- user makes and unmakes; `source` is provenance, written once at creation and
-- never corrected (see `AssetCreateSource`'s docstring). Deriving one from the
-- other would make "add this script-imported character to my library" either
-- impossible or a provenance forgery.
--
-- DEFAULT true, NOT false. Every path that creates an asset outside the two
-- above is deliberate, so `true` is the right answer for anything this
-- migration did not explicitly flip and for anything a future writer forgets
-- to think about. A `false` default would have quietly emptied the shelf.
--
-- THE ONE-OFF DATA STEP below is idempotent (re-running sets the same rows to
-- the same value). In production TODAY it flips exactly the rows the backfill
-- created — mig 447's header records that run as 8 assets — plus any
-- `script_import` rows 一键导入 has landed since P3. It does NOT touch
-- `manual` / `generated` / `duplicated` / `system_preset`.
--
-- No `SET ROLE` (see CLAUDE.md): this runs as the connecting role, which is
-- `postgres` in CI and the owner in production. `assets` carries no allowlist
-- trigger, so no `session_replication_role` suppression is needed either.

-- 1) The column ------------------------------------------------------------
ALTER TABLE public.assets
    ADD COLUMN IF NOT EXISTS in_library BOOLEAN NOT NULL DEFAULT true;

COMMENT ON COLUMN public.assets.in_library IS
    'True when this asset is a member of the scope''s asset library (the shelf '
    'and the sidebar badges). Project-originated rows (source script_import / '
    'migrated) start false and are added by an explicit user action; every '
    'deliberate creation path starts true.';

-- 2) One-off: project-originated rows are not library members --------------
-- Idempotent by construction — the predicate names the rows, not a point in
-- time, so a re-run is a no-op rather than a second flip.
UPDATE public.assets
   SET in_library = false
 WHERE source IN ('migrated', 'script_import');

-- 3) The shelf's index -----------------------------------------------------
-- Mirrors idx_assets_scope_type (mig 445) with the membership predicate
-- folded in: the shelf query is `scope_id = ? AND asset_type = ? AND
-- in_library AND deleted_at IS NULL`, and the counts query is the same
-- predicate without the type. Kept ALONGSIDE the mig-445 index rather than
-- replacing it — `GET /projects/{id}/assets` and the sheet still read across
-- both membership states.
CREATE INDEX IF NOT EXISTS idx_assets_scope_library
    ON public.assets (scope_id, asset_type)
    WHERE in_library AND deleted_at IS NULL;
