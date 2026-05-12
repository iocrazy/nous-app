-- 215_point_transactions_daily_gift_types.sql
--
-- Fix point_transactions_type_check constraint to allow `daily_gift` and
-- `daily_gift_reclaim` values written by scheduled_quotas workflows.
--
-- ── Background ────────────────────────────────────────────────────────
-- `grant_daily_free_points_step` calls PointsService.add_points(type="daily_gift")
-- and `reclaim_daily_free_points_step` indirectly writes
-- `type="daily_gift_reclaim"` via PointsService.reclaim_daily_gift().
--
-- The original constraint (CHECK type IN ('purchase','consume','refund',
-- 'gift','admin_adjust')) doesn't include either of those, so every
-- daily 00:00 UTC grant_daily_free_points cron tick errored out with:
--
--   PG 23514: new row for relation "point_transactions" violates check
--   constraint "point_transactions_type_check"
--
-- Observed silently failing every day since the workflow shipped — log
-- only appears in dbos.workflow_status (status='ERROR'), not in the
-- application_logs error funnel because the exception is swallowed by
-- DBOS step retry policy.
--
-- ── Why distinct types rather than just `gift` ───────────────────────
-- Operations needs to distinguish:
--   * `gift`              — manual/promotional credit (one-time)
--   * `daily_gift`        — automated daily free quota
--   * `daily_gift_reclaim`— claw-back of unused daily quota next day
--
-- Each has different accounting semantics (manual gifts go to revenue
-- attribution, daily_gift is a cost-of-acquisition line item, reclaim
-- nets it). Keeping them as distinct types lets the billing dashboard
-- and any future reconciliation script tell them apart without parsing
-- the description text.
--
-- ── Migration ─────────────────────────────────────────────────────────
-- DROP + ADD is the standard pattern; PG validates ALL existing rows
-- against the new constraint. Safe here because:
--   * existing rows only carry `gift` (verified in prod via
--     `SELECT DISTINCT type FROM point_transactions`)
--   * the new constraint is a strict superset of the old one

ALTER TABLE public.point_transactions
    DROP CONSTRAINT IF EXISTS point_transactions_type_check;

ALTER TABLE public.point_transactions
    ADD CONSTRAINT point_transactions_type_check
    CHECK (
        (type)::text = ANY (
            ARRAY[
                'purchase'::character varying,
                'consume'::character varying,
                'refund'::character varying,
                'gift'::character varying,
                'admin_adjust'::character varying,
                'daily_gift'::character varying,
                'daily_gift_reclaim'::character varying
            ]::text[]
        )
    );

COMMENT ON CONSTRAINT point_transactions_type_check ON public.point_transactions IS
    'Allowed transaction types. Extended by mig 215 to add daily_gift / daily_gift_reclaim used by scheduled_quotas workflows.';
