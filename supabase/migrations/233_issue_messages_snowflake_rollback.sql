-- 233_issue_messages_snowflake_rollback.sql
-- Restore issue_messages.id to UUID. Requires a pre-migration pg_dump
-- snapshot — column-level revert is impossible because the old
-- UUID values are not preserved after step 2 of 233.

BEGIN;

-- Operator procedure:
--   1. pg_restore --schema=public --table=issue_messages issue_messages.dump
--   2. Re-apply migrations 234+ if any came in after 233.
--
-- This file intentionally does NOT execute DDL because:
--   a) The old UUID values are gone after step 2 of the forward migration.
--   b) Any rows written after migration 233 was applied reference BIGINT IDs;
--      restoring the UUID table would leave them dangling.
--
-- A true rollback requires restoring the table from the pre-migration
-- pg_dump snapshot and then replaying any migrations numbered >= 234.

DO $$ BEGIN
    RAISE NOTICE 'Manual rollback required — see header comment for operator procedure.';
END $$;

COMMIT;
