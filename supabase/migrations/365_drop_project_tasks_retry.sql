-- 365: finish what migration 176 started — drop the empty `project_tasks` shell.
--
-- WHY THIS EXISTS
-- ---------------
-- Migration 176 half-landed. Its `task_assets` drop went through, its
-- `project_tasks` drop did not, and prod has carried an empty orphan table
-- ever since while the whole codebase moved on:
--
--   projects_repository.py:61,237   "was removed with the table in migration 176"
--   test_projects_repository_orm.py "9 tables — project_tasks was dropped in migration 176"
--
-- Everyone believed it was gone. Only the database disagreed.
--
-- ROOT CAUSE (measured 2026-07-15, not guessed)
-- ---------------------------------------------
-- 176 opens with `SET ROLE service_role;`. The CI runner connects as
-- `postgres` (run-migration.yml: `psql -U postgres`), and `project_tasks` is
-- OWNED BY postgres — so the script voluntarily dropped its own privileges
-- and then tried to drop a table it no longer owned:
--
--   ERROR: permission denied for table project_tasks
--
-- `task_assets` survived the same line only because `DROP TABLE IF EXISTS`
-- on an already-absent table is a no-op success, not a privilege check.
--
-- This migration therefore does NOT `SET ROLE`. It runs as the connecting
-- role (postgres in CI = the owner). That single difference is the fix.
--
-- STATE AT WRITE TIME (verified read-only against prod 2026-07-15)
-- ---------------------------------------------------------------
--   project_tasks   0 rows        ← empty shell
--   issues          10 rows       ← the data has lived here since 175
--   task_assets     absent        ← 176 already took it
--   owner           postgres
--
-- The guard from 176 is kept verbatim below: this still refuses to run if a
-- single unmigrated row ever reappears. An empty table makes it trivially
-- true today, but the guard is what makes the DROP safe rather than merely
-- convenient.

DO $$
DECLARE
  v_unmigrated INTEGER;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.tables
     WHERE table_schema = 'public' AND table_name = 'project_tasks'
  ) THEN
    RAISE NOTICE '[migration 365] project_tasks already gone — nothing to do.';
    RETURN;
  END IF;

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
      'Investigate before retrying — 176''s backfill contract still holds.',
      v_unmigrated;
  END IF;

  RAISE NOTICE '[migration 365] project_tasks drop OK — 0 unmigrated rows.';
END $$;

-- No SET ROLE: see ROOT CAUSE above. Runs as the owner (postgres under CI).
DROP TABLE IF EXISTS public.project_tasks CASCADE;

-- PostgREST caches the schema; without this the dropped table lingers in its
-- cache until a restart (see the CI-migration-skips-PostgREST-reload trap).
NOTIFY pgrst, 'reload schema';
