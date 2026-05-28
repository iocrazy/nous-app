-- 231_ai_sessions_snowflake_rollback.sql
-- Restore ai_sessions.id to UUID. Requires a pre-migration pg_dump
-- snapshot — column-level revert is impossible because the old
-- UUID values are not preserved after step 4 of 231.

BEGIN;

-- Operator procedure:
--   1. pg_restore --schema=public --table=ai_sessions ai_sessions.dump
--   2. pg_restore --schema=public --table=issues issues.dump
--   3. pg_restore --schema=public --table=ai_session_memory ai_session_memory.dump
--   4. pg_restore --schema=public --table=agent_runs agent_runs.dump
--   5. pg_restore --schema=public --table=ai_messages ai_messages.dump
--   6. Re-apply migrations 232+ if any came in after 231.
--
-- This file intentionally does NOT execute DDL because:
--   a) The old UUID values are gone after step 4 of the forward migration.
--   b) Any FK rows written after migration 231 was applied reference BIGINT IDs;
--      restoring UUID tables would leave them dangling.
--
-- A true rollback requires restoring all five tables from the pre-migration
-- pg_dump snapshot and then replaying any migrations numbered >= 232.

DO $$ BEGIN
    RAISE NOTICE 'Manual rollback required — see header comment for operator procedure.';
END $$;

COMMIT;
