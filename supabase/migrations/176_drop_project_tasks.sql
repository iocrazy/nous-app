-- 176: PR-D7 — drop project_tasks table after issues backfill (175).
--
-- GUARDED: refuses to run if any project_tasks row hasn't been mirrored
-- into issues. The check is inline (no persistent view since service_role
-- can't CREATE on schema public — see migration 175 note).
--
-- DESTRUCTIVE: drops project_tasks + task_assets (FK cascade). Frontend
-- KanbanBoard + projectTasksService MUST be removed BEFORE this runs;
-- the live IssuesPage replaces both.
--
-- Run order on cutover day:
--   a. Verify backfill coverage:
--        SELECT COUNT(*) FROM public.project_tasks pt
--          WHERE NOT EXISTS (
--            SELECT 1 FROM public.issues i
--             WHERE i.origin_kind='migrated_project_task'
--               AND i.origin_fingerprint=pt.id::text);  → 0
--   b. Apply this migration
--   c. Deploy frontend with KanbanBoard removed (issues-only nav)
--
-- Connection role: same SET ROLE pattern as migration 175 so the script
-- works under both Supabase CLI and direct psql / mediahub_dbos.
SET ROLE service_role;

DO $$
DECLARE
  v_unmigrated INTEGER;
BEGIN
  SELECT COUNT(*) INTO v_unmigrated
    FROM public.project_tasks pt
   WHERE NOT EXISTS (
     SELECT 1 FROM public.issues i
      WHERE i.origin_kind = 'migrated_project_task'
        AND i.origin_fingerprint = pt.id::text
   );

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
