-- 176: PR-D7 — drop project_tasks table after issues backfill (175).
--
-- GUARDED: refuses to run if any project_tasks row hasn't been mirrored
-- into issues. The `unmigrated_project_tasks` view created in migration
-- 175 is the source of truth. If it returns >0 rows, the migration
-- raises and you must:
--   1. Re-run migration 175 (idempotent)
--   2. Investigate any rows whose source data couldn't be backfilled
--   3. Re-run this migration
--
-- DESTRUCTIVE: drops project_tasks + task_assets (FK cascade). Frontend
-- KanbanBoard + projectTasksService MUST be removed BEFORE this runs;
-- the live IssuesPage replaces both.
--
-- Run order on cutover day:
--   a. Verify SELECT count(*) FROM unmigrated_project_tasks; → 0
--   b. Apply this migration
--   c. Deploy frontend with KanbanBoard removed (issues-only nav)
--   d. Drop the unmigrated_project_tasks view (it references nothing
--      after this migration runs; keeping it would break later schema
--      introspection). Done at the bottom of this file.

DO $$
DECLARE
  v_unmigrated INTEGER;
BEGIN
  SELECT COUNT(*) INTO v_unmigrated FROM public.unmigrated_project_tasks;

  IF v_unmigrated > 0 THEN
    RAISE EXCEPTION
      'project_tasks drop refused: % row(s) not yet mirrored into issues. '
      'Run migration 175 first or investigate.',
      v_unmigrated;
  END IF;

  RAISE NOTICE
    '[migration 176] project_tasks drop OK — 0 unmigrated rows.';
END $$;

-- task_assets references project_tasks(id) ON DELETE CASCADE — dropping
-- project_tasks would cascade. We drop both explicitly so the side-effect
-- shows up in migration logs rather than as a hidden cascade.
DROP TABLE IF EXISTS public.task_assets CASCADE;
DROP TABLE IF EXISTS public.project_tasks CASCADE;

-- The mirror-coverage view is now stale — drop it to keep
-- information_schema clean. The corresponding origin_kind in issues
-- (`migrated_project_task`) stays as the audit trail.
DROP VIEW IF EXISTS public.unmigrated_project_tasks;
