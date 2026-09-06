-- 457_folders_refuse_system_mutation.sql
--
-- mig 441 put the system-folder guard in the API layer (409 system_folder on
-- rename / move / trash / delete). The frontend's folder move writes
-- `folders` through PostgREST directly and never meets that guard, so batch
-- Move could relocate Chat Uploads or the cover-template library with no
-- error anywhere. The guard now lives where every writer passes.
--
-- Same four columns as `_refuse_if_system` in resources_folders_router.py, so
-- the two layers refuse exactly the same set of mutations. Everything else on
-- a system folder stays writable — icon, colour, sort_order, updated_at, and
-- (crucially) is_system / system_key themselves, which is how
-- cover_templates_repository.ensure_folder adopts a plain folder.
--
-- OLD.is_system, not NEW: keying on NEW would let one UPDATE clear the flag
-- and move the folder in the same statement.
--
-- Deleting a system folder row is left to the API layer's 409 — a BEFORE
-- DELETE guard would also block the legitimate scope-teardown paths.
CREATE OR REPLACE FUNCTION public.folders_refuse_system_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.is_system THEN
    IF NEW.parent_id IS DISTINCT FROM OLD.parent_id
       OR NEW.library_id IS DISTINCT FROM OLD.library_id
       OR NEW.name IS DISTINCT FROM OLD.name
       OR NEW.is_trashed IS DISTINCT FROM OLD.is_trashed THEN
      RAISE EXCEPTION 'system_folder' USING
        ERRCODE = 'check_violation',
        DETAIL = format('folder %s (%s) is a system folder and cannot be renamed, moved or trashed', OLD.id, OLD.system_key),
        HINT = 'system_folder';
    END IF;
  END IF;
  RETURN NEW;
END $$;

COMMENT ON FUNCTION public.folders_refuse_system_mutation() IS
    'mig 457: refuses rename / move / trash of an is_system folder for every writer, including the direct PostgREST writes the API guard (mig 441) cannot see.';

DROP TRIGGER IF EXISTS trg_folders_refuse_system_mutation ON public.folders;
CREATE TRIGGER trg_folders_refuse_system_mutation
  BEFORE UPDATE ON public.folders
  FOR EACH ROW EXECUTE FUNCTION public.folders_refuse_system_mutation();
