-- supabase/migrations/238_reinstall_teams_is_personal_trigger.sql
-- Re-install the trigger from mig 236 that PR #368's CI accidentally
-- dropped.
--
-- Cause: run-migration.yml's auto-detect step picked up both
-- 236_teams_is_personal_sync.sql AND 236_teams_is_personal_sync_rollback.sql
-- as "new files in this push" and applied them in alphabetical order.
-- The rollback ran right after the migration and dropped the trigger
-- it had just installed.
--
-- The CI workflow has been patched (same PR) to exclude `*_rollback.sql`
-- from auto-detect going forward; this migration just brings prod back
-- to the intended post-236 state.

BEGIN;

CREATE OR REPLACE FUNCTION public.teams_sync_is_personal()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.is_personal := (NEW.kind = 'personal');
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS teams_sync_is_personal_trg ON public.teams;
CREATE TRIGGER teams_sync_is_personal_trg
BEFORE INSERT OR UPDATE OF kind ON public.teams
FOR EACH ROW
EXECUTE FUNCTION public.teams_sync_is_personal();

-- Sanity: confirm the trigger ended up installed
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'teams_sync_is_personal_trg'
    ) THEN
        RAISE EXCEPTION 'teams_sync_is_personal_trg failed to install';
    END IF;
END $$;

COMMIT;
