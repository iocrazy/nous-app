-- Migration 396: unblock DELETE /api/v1/projects/{id}
--
-- Every FK pointing at projects(id) declares an ON DELETE action EXCEPT the two
-- fixed here, both added before that was house style. With no clause Postgres
-- defaults to NO ACTION, so the referencing row blocks the parent delete
-- outright:
--
--   ERROR: update or delete on table "projects" violates foreign key constraint
--          "script_projects_project_id_fkey" on table "script_projects"
--
-- ProjectsService.delete_project has no pre-pass that clears these, so any
-- project created with workflow_method set (which auto-creates a script_projects
-- row) has been undeletable via the API — a live probe on 2026-07-31 hit exactly
-- this and could not clean up its own test project.
--
-- Two different actions, because the two tables are owned differently:
--
--   script_projects.project_id  NOT NULL  → CASCADE.  A script project cannot
--     exist without its parent project; it is child data by construction.
--     script_chapters/scenes/shots already cascade off script_projects, so this
--     one edge transitively cleans the whole script subtree.
--   skills.project_id           NULLABLE  → SET NULL.  The column arrived on
--     style_templates in mig 114 (renamed to skills in that same migration —
--     Postgres keeps the original constraint name through a table rename, hence
--     the name discovery below rather than a hardcoded skills_* name). A skill
--     is library content that lives happily without a project; scoping one to a
--     project must not make deleting that project destroy it.
--
-- storyboard_projects had the third such FK (mig 110) but that table was
-- tombstoned and dropped in mig 366, so there is nothing left to fix there.
--
-- Constraint names are auto-generated, so each block discovers the real name
-- from pg_constraint by (table, column, referenced table) instead of assuming
-- one. Re-running is safe: the drop/re-add is unconditional and idempotent.

-- script_projects.project_id → ON DELETE CASCADE
DO $$
DECLARE
  v_conname text;
BEGIN
  IF to_regclass('public.script_projects') IS NULL THEN
    RAISE NOTICE 'mig396: public.script_projects absent, skipping';
    RETURN;
  END IF;

  SELECT c.conname INTO v_conname
  FROM pg_constraint c
  JOIN pg_attribute a
    ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
  WHERE c.conrelid = 'public.script_projects'::regclass
    AND c.confrelid = 'public.projects'::regclass
    AND c.contype = 'f'
    AND cardinality(c.conkey) = 1
    AND a.attname = 'project_id';

  IF v_conname IS NOT NULL THEN
    EXECUTE format(
      'ALTER TABLE public.script_projects DROP CONSTRAINT %I', v_conname
    );
  END IF;

  ALTER TABLE public.script_projects
    ADD CONSTRAINT script_projects_project_id_fkey
    FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;
END$$;

-- skills.project_id → ON DELETE SET NULL
DO $$
DECLARE
  v_conname text;
BEGIN
  IF to_regclass('public.skills') IS NULL THEN
    RAISE NOTICE 'mig396: public.skills absent, skipping';
    RETURN;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'skills'
      AND column_name = 'project_id'
  ) THEN
    RAISE NOTICE 'mig396: public.skills.project_id absent, skipping';
    RETURN;
  END IF;

  SELECT c.conname INTO v_conname
  FROM pg_constraint c
  JOIN pg_attribute a
    ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
  WHERE c.conrelid = 'public.skills'::regclass
    AND c.confrelid = 'public.projects'::regclass
    AND c.contype = 'f'
    AND cardinality(c.conkey) = 1
    AND a.attname = 'project_id';

  IF v_conname IS NOT NULL THEN
    EXECUTE format('ALTER TABLE public.skills DROP CONSTRAINT %I', v_conname);
  END IF;

  ALTER TABLE public.skills
    ADD CONSTRAINT skills_project_id_fkey
    FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE SET NULL;
END$$;

NOTIFY pgrst, 'reload schema';
