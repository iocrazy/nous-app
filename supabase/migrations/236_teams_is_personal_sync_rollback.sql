-- supabase/migrations/236_teams_is_personal_sync_rollback.sql
-- Rollback for 236_teams_is_personal_sync.sql.
--
-- Removes the sync trigger and helper function. The backfill is not
-- reverted (no point — leaving is_personal=true on personal teams is
-- harmless; reverting would only re-introduce the drift this migration
-- was designed to eliminate).

BEGIN;

DROP TRIGGER IF EXISTS teams_sync_is_personal_trg ON public.teams;
DROP FUNCTION IF EXISTS public.teams_sync_is_personal();

COMMIT;
