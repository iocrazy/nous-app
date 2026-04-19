-- ============================================================
-- Hotfix: rpc_consume_team_points team_id type mismatch.
--
-- Migration 120 shipped with p_team_id UUID, but team_quotas.team_id
-- is BIGINT (Snowflake since migration 051). The signature mismatch
-- caused every /media/fetch to return HTTP 402 "Points service
-- temporarily unavailable" because the RPC raised a type-cast error
-- that points_service.check_and_consume caught as None.
--
-- This migration drops the wrong-typed function and recreates it
-- with the correct BIGINT signature. Safe to re-apply.
-- ============================================================

DROP FUNCTION IF EXISTS public.rpc_consume_team_points(UUID, UUID, INTEGER, BOOLEAN);

CREATE OR REPLACE FUNCTION public.rpc_consume_team_points(
    p_team_id BIGINT,
    p_user_id UUID,
    p_points_cost INTEGER,
    p_monthly_limit_check BOOLEAN DEFAULT TRUE
)
RETURNS TABLE (
    success BOOLEAN,
    points_cost INTEGER,
    balance_after INTEGER,
    reason TEXT
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_balance INTEGER;
    v_monthly_limit INTEGER;
    v_used_this_month INTEGER;
BEGIN
    IF p_points_cost <= 0 THEN
        RETURN QUERY SELECT TRUE, 0, NULL::INTEGER, NULL::TEXT;
        RETURN;
    END IF;

    UPDATE team_quotas
       SET points_balance = points_balance - p_points_cost
     WHERE team_id = p_team_id
       AND points_balance >= p_points_cost
    RETURNING points_balance INTO v_balance;

    IF NOT FOUND THEN
        SELECT points_balance INTO v_balance
          FROM team_quotas
         WHERE team_id = p_team_id;

        IF v_balance IS NULL THEN
            RETURN QUERY SELECT FALSE, p_points_cost, 0,
                'Team quota not found. Please contact support.'::TEXT;
        ELSE
            RETURN QUERY SELECT FALSE, p_points_cost, v_balance,
                format('Insufficient points balance. Required: %s, available: %s.',
                       p_points_cost, v_balance)::TEXT;
        END IF;
        RETURN;
    END IF;

    IF p_monthly_limit_check THEN
        SELECT monthly_points_limit, points_used_this_month
          INTO v_monthly_limit, v_used_this_month
          FROM member_quotas
         WHERE team_id = p_team_id
           AND user_id = p_user_id;

        IF v_monthly_limit IS NOT NULL
           AND (COALESCE(v_used_this_month, 0) + p_points_cost) > v_monthly_limit
        THEN
            UPDATE team_quotas
               SET points_balance = points_balance + p_points_cost
             WHERE team_id = p_team_id;

            RETURN QUERY SELECT FALSE, p_points_cost, v_balance + p_points_cost,
                format('Monthly points limit exceeded. Limit: %s, used: %s, required: %s.',
                       v_monthly_limit, COALESCE(v_used_this_month, 0), p_points_cost)::TEXT;
            RETURN;
        END IF;
    END IF;

    UPDATE member_quotas
       SET points_used_this_month = COALESCE(points_used_this_month, 0) + p_points_cost
     WHERE team_id = p_team_id
       AND user_id = p_user_id;

    RETURN QUERY SELECT TRUE, p_points_cost, v_balance, NULL::TEXT;
END;
$$;

GRANT EXECUTE ON FUNCTION public.rpc_consume_team_points(BIGINT, UUID, INTEGER, BOOLEAN)
    TO service_role, authenticated;
