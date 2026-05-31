-- Rollback for migration 245.
--
-- (CI auto-detect skips *_rollback.sql, so this never auto-applies.)
--
-- This migration is a one-way data repair: it inserts the personal-scope
-- resource_items that the media paths failed to create. Reverting would delete
-- those rows and re-orphan the resources from their owners' libraries, so there
-- is no meaningful automatic rollback. No-op by design.
--
-- If a revert is truly required, the inserted rows are exactly the personal-team
-- resource_items whose owner == the resource creator and which have no sibling
-- item in any other scope. Deleting them blindly risks removing legitimately
-- personal-scoped items, so this is documentation, not a safe script.

SELECT 1;
