-- 257_drop_dead_get_dashboard_stats.sql
--
-- Drop the orphaned, broken get_dashboard_stats(uuid) function.
--
-- This function (last defined in migration 066) references objects that have
-- since been DROPPED, so any invocation now raises an error at runtime:
--   * parsed_media.user_id   — column dropped in migration 083
--   * media_tags             — table dropped in migration 077
--   * need_download_music    — dropped in migration 089
--
-- It has NO application caller (no .rpc("get_dashboard_stats") anywhere in the
-- backend), yet it is still GRANT EXECUTE ... TO authenticated (migrations
-- 037/059), so any authenticated client that calls it directly gets a 500.
-- Dashboard stats are computed elsewhere now. Drop the dead function so the
-- API surface no longer exposes a guaranteed-failing RPC.

DROP FUNCTION IF EXISTS get_dashboard_stats(uuid);

NOTIFY pgrst, 'reload schema';
