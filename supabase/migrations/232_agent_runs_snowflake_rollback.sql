-- 232_agent_runs_snowflake_rollback.sql
-- Restore agent_runs.id to UUID. Requires a pre-migration pg_dump
-- snapshot — column-level revert is impossible because the old
-- UUID values are not preserved after step 4 of 232.

BEGIN;

-- Operator procedure:
--   1. pg_restore --schema=public --table=agent_runs agent_runs.dump
--   2. pg_restore --schema=public --table=agent_run_events agent_run_events.dump
--   3. pg_restore --schema=public --table=agent_memories agent_memories.dump
--   4. pg_restore --schema=public --table=agent_tasks agent_tasks.dump
--   5. pg_restore --schema=public --table=agent_commitments agent_commitments.dump
--   6. pg_restore --schema=public --table=issue_messages issue_messages.dump
--   7. Re-apply migrations 233+ if any came in after 232.
--
-- This file intentionally does NOT execute DDL because:
--   a) The old UUID values are gone after step 4 of the forward migration.
--   b) Any FK rows written after migration 232 was applied reference BIGINT IDs;
--      restoring UUID tables would leave them dangling.
--   c) The 2 self-FKs (parent_run_id, root_run_id) compound the ordering:
--      agent_runs must be restored before all 5 dependent tables.
--
-- A true rollback requires restoring all six affected tables from the
-- pre-migration pg_dump snapshot and then replaying any migrations
-- numbered >= 233.

DO $$ BEGIN
    RAISE NOTICE 'Manual rollback required — see header comment for operator procedure.';
END $$;

COMMIT;
