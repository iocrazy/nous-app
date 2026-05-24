-- 223_daily_point_gifts_team_id_bigint.sql
-- Fix: daily_point_gifts.team_id type mismatch (prod money bug).
--
-- migration 099 created daily_point_gifts.team_id as UUID. But teams.id
-- became BIGINT in the snowflake migration (051), and every other team_id
-- in the points system is BIGINT (member_quotas.team_id, point_transactions
-- .team_id — converted by 051; daily_point_gifts was created later in 099
-- and missed). grant_daily_free_points therefore inserted a bigint teams.id
-- into a UUID column → the insert failed every day:
--   * the idempotency row never persisted (daily_point_gifts stayed empty),
--     so the once-per-day guard never fired → free points granted daily with
--     no reclaim,
--   * reclaim_daily_free_points read an empty table → never clawed back
--     unused points.
--
-- Confirmed in prod 2026-05-24: daily_point_gifts has 0 rows (the insert has
-- never succeeded), so this type change carries no data and is safe.

ALTER TABLE public.daily_point_gifts
  ALTER COLUMN team_id TYPE bigint USING (team_id::text::bigint);
