-- Rollback for migration 241.
--
-- (CI auto-detect skips *_rollback.sql, so this never auto-applies.)
--
-- This migration is a one-way data repair: it remaps 13 resource_items.scope_id
-- values from a stale user UUID to the owner's personal-team snowflake. The
-- original (wrong) UUID values are not retained, and reverting would re-break
-- the rows (make those resources invisible to their owner again), so there is
-- no meaningful automatic rollback. No-op by design.
--
-- If a revert is truly required, the original value for each affected row was
-- the personal team's owner_id:
--   UPDATE public.resource_items ri
--      SET scope_id = t.owner_id::text
--     FROM public.teams t
--    WHERE t.id::text = ri.scope_id AND t.kind = 'personal';
-- (NOTE: this would also rewrite any legitimately personal-scoped rows, so do
--  NOT run it blindly — it is documentation, not a safe script.)

SELECT 1;
