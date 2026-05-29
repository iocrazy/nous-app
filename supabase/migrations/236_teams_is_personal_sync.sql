-- supabase/migrations/236_teams_is_personal_sync.sql
-- Keep teams.is_personal in sync with teams.kind.
--
-- PR-A added the `kind` column ('personal' | 'collaborative') but left the
-- legacy `is_personal` boolean alone — no backfill, no trigger. Result:
-- every personal team auto-created by PR-A had `kind='personal'` but
-- `is_personal=false`, which broke `fetchPersonalTeam()` (the frontend
-- service that filters by `is_personal=true`) and therefore the entire
-- personal-mode UI (My Uploads, folders, smart folders).
--
-- This migration:
--   1. Backfills `is_personal = (kind='personal')` everywhere
--   2. Installs a trigger that maintains the invariant on INSERT/UPDATE
--
-- A future PR-E should drop `is_personal` entirely; for now this keeps
-- the two columns from drifting.

BEGIN;

-- 1. Backfill any rows where the two columns disagree
UPDATE public.teams
   SET is_personal = (kind = 'personal')
 WHERE is_personal IS DISTINCT FROM (kind = 'personal');

-- 2. Trigger: enforce the invariant on every write
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

-- 3. Sanity: assert no drift remains
DO $$
DECLARE
    drift int;
BEGIN
    SELECT COUNT(*) INTO drift
      FROM public.teams
     WHERE is_personal IS DISTINCT FROM (kind = 'personal');
    IF drift > 0 THEN
        RAISE EXCEPTION 'teams.is_personal drift after backfill: % rows', drift;
    END IF;
END $$;

COMMIT;
