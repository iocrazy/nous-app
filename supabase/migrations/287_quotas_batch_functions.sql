-- 287_quotas_batch_functions.sql
-- Tier-3 scale fix (100k readiness audit §B): grant_daily_free_points /
-- reclaim_daily_free_points ran one query-loop iteration PER personal team
-- (4+ round trips each) — a daily O(users) query storm at 100k users.
-- Replace with two set-based functions; the DBOS steps make ONE call each.
--
-- Semantics preserved from app/workflows/scheduled_quotas.py:
--   grant: idempotent per (user_id, gift_date) via the existing UNIQUE
--          constraint (claim-first, so a re-run can never double-credit);
--          ensures a team_quotas row exists; credits points_balance and
--          records a 'daily_gift' transaction with balance_after.
--   reclaim: for yesterday's 'granted' gifts, consumed = sum of 'consume'
--          transactions since granted_at (capped at amount_granted);
--          reclaim = granted - used, capped at current balance; records a
--          negative 'daily_gift_reclaim' transaction only when > 0; every
--          processed gift flips to status='reclaimed'.

CREATE OR REPLACE FUNCTION public.grant_daily_free_points_batch(
    p_amount integer,
    p_today  date
) RETURNS TABLE (granted integer, skipped integer)
LANGUAGE plpgsql
SET search_path = public
AS $$
DECLARE
    v_granted integer := 0;
    v_total   integer := 0;
BEGIN
    IF p_amount IS NULL OR p_amount <= 0 THEN
        RETURN QUERY SELECT 0, 0;
        RETURN;
    END IF;

    SELECT COUNT(*) INTO v_total FROM teams WHERE kind = 'personal';

    -- Quota rows must pre-exist for the credit UPDATE (CTEs cannot see
    -- each other's inserts, so this is a separate statement).
    INSERT INTO team_quotas (team_id)
    SELECT t.id FROM teams t
    WHERE t.kind = 'personal'
      AND NOT EXISTS (SELECT 1 FROM team_quotas q WHERE q.team_id = t.id);

    WITH eligible AS (
        SELECT t.id AS team_id, t.owner_id AS user_id
        FROM teams t
        WHERE t.kind = 'personal'
    ),
    claimed AS (
        -- Claim the idempotency row FIRST: on conflict the team drops out
        -- of every downstream CTE, so a re-run cannot double-credit.
        INSERT INTO daily_point_gifts
            (user_id, team_id, gift_date, amount_granted, status)
        SELECT e.user_id, e.team_id, p_today, p_amount, 'granted'
        FROM eligible e
        ON CONFLICT (user_id, gift_date) DO NOTHING
        RETURNING team_id, user_id
    ),
    credited AS (
        UPDATE team_quotas q
        SET points_balance = q.points_balance + p_amount,
            updated_at = now()
        FROM claimed c
        WHERE q.team_id = c.team_id
        RETURNING q.team_id, q.points_balance AS balance_after
    ),
    txns AS (
        INSERT INTO point_transactions
            (team_id, user_id, amount, balance_after,
             type, reference_type, description)
        SELECT cr.team_id, c.user_id, p_amount, cr.balance_after,
               'daily_gift', 'daily_gift',
               'Daily free points (' || p_today::text || ')'
        FROM credited cr
        JOIN claimed c ON c.team_id = cr.team_id
        RETURNING 1
    )
    SELECT COUNT(*) INTO v_granted FROM claimed;

    RETURN QUERY SELECT v_granted, v_total - v_granted;
END;
$$;

CREATE OR REPLACE FUNCTION public.reclaim_daily_free_points_batch(
    p_yesterday date
) RETURNS TABLE (reclaimed_count integer, total_reclaimed bigint)
LANGUAGE plpgsql
SET search_path = public
AS $$
DECLARE
    v_count integer := 0;
    v_total bigint  := 0;
BEGIN
    WITH g AS (
        SELECT id, user_id, team_id, amount_granted, granted_at
        FROM daily_point_gifts
        WHERE gift_date = p_yesterday AND status = 'granted'
    ),
    usage AS (
        SELECT g.id AS gift_id, g.user_id, g.team_id, g.amount_granted,
               LEAST(
                   g.amount_granted,
                   COALESCE((
                       SELECT SUM(ABS(t.amount))
                       FROM point_transactions t
                       WHERE t.user_id = g.user_id
                         AND t.team_id = g.team_id
                         AND t.type = 'consume'
                         AND t.created_at >= g.granted_at
                   ), 0)
               )::integer AS used
        FROM g
    ),
    calc AS (
        SELECT u.gift_id, u.user_id, u.team_id, u.used,
               LEAST(u.amount_granted - u.used,
                     COALESCE(q.points_balance, 0))::integer AS reclaim_actual
        FROM usage u
        LEFT JOIN team_quotas q ON q.team_id = u.team_id
    ),
    debited AS (
        UPDATE team_quotas q
        SET points_balance = q.points_balance - c.reclaim_actual,
            updated_at = now()
        FROM calc c
        WHERE q.team_id = c.team_id AND c.reclaim_actual > 0
        RETURNING q.team_id, q.points_balance AS balance_after
    ),
    txns AS (
        INSERT INTO point_transactions
            (team_id, user_id, amount, balance_after,
             type, reference_type, description)
        SELECT d.team_id, c.user_id, -c.reclaim_actual, d.balance_after,
               'daily_gift_reclaim', 'daily_gift_reclaim',
               'Reclaim unused daily gift (' || p_yesterday::text || ')'
        FROM debited d
        JOIN calc c ON c.team_id = d.team_id
        RETURNING 1
    ),
    marked AS (
        UPDATE daily_point_gifts dg
        SET status = 'reclaimed',
            amount_consumed = c.used,
            amount_reclaimed = c.reclaim_actual,
            reclaimed_at = now()
        FROM calc c
        WHERE dg.id = c.gift_id
        RETURNING c.reclaim_actual
    )
    SELECT COUNT(*), COALESCE(SUM(m.reclaim_actual), 0)
    INTO v_count, v_total
    FROM marked m;

    RETURN QUERY SELECT v_count, v_total;
END;
$$;

-- Backend-only entry points (called via the SQLAlchemy engine as postgres).
-- public-schema functions are otherwise exposed through PostgREST /rpc/ —
-- a logged-in user must NOT be able to mint or reclaim points directly.
REVOKE EXECUTE ON FUNCTION public.grant_daily_free_points_batch(integer, date)
    FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.reclaim_daily_free_points_batch(date)
    FROM PUBLIC, anon, authenticated;
