-- 388_drop_projects_current_stage_id.sql
-- M2 PR-G (task G2): drop the legacy SOP stage cursor column at the DB level.
--
-- G1 already retired every backend code reference to
-- projects.current_stage_id (repository / model / FK declaration / service
-- logic) — the column has been dead weight since then. This migration removes
-- the column-specific artifacts that 295_project_sop_stages.sql created:
--   - the inline FK constraint (Postgres auto-named it
--     projects_current_stage_id_fkey since 295 used an inline
--     `REFERENCES project_stages(id)` on ADD COLUMN, not an explicit
--     CONSTRAINT name)
--   - the partial index idx_projects_current_stage
--   - the column itself
--
-- What stays (verified against 295/352, not guessed):
--   - project_stages (catalog table) — still referenced by
--     project_stage_history and by ProjectStagesRepository for suggestion
--     logic unrelated to the projects.current_stage_id cursor.
--   - project_stage_history (audit table) — historical data, not a
--     column-specific artifact; PR-G does not touch it.
--   - trg_project_stages_updated_at — fires on project_stages, not on the
--     projects.current_stage_id column; unrelated to this drop.
--   - 352_projects_default_stage.sql — a one-time backfill UPDATE, no
--     schema object to drop.
--
-- No trigger, view, or function was ever created exclusively for
-- projects.current_stage_id (checked 295, 352, and 380's retirement
-- comment) — a plain FK + index + column drop is complete.

ALTER TABLE public.projects
    DROP CONSTRAINT IF EXISTS projects_current_stage_id_fkey;

DROP INDEX IF EXISTS public.idx_projects_current_stage;

ALTER TABLE public.projects
    DROP COLUMN IF EXISTS current_stage_id;

NOTIFY pgrst, 'reload schema';
