-- ============================================================
-- Atomic points consumption to prevent double-spending.
--
-- Replaces the non-atomic read-then-write pattern in
-- points_service.check_and_consume with a single-statement
-- check + decrement + member-usage increment.
--
-- Returns a row with:
--   success      BOOLEAN   — whether consumption succeeded
--   points_cost  INTEGER   — amount deducted (0 when denied)
--   balance_after INTEGER  — team_quotas.points_balance after op
--   reason       TEXT      — failure reason (NULL on success)
-- ============================================================

CREATE OR REPLACE FUNCTION public.rpc_consume_team_points(
    p_team_id UUID,
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

    -- Atomic decrement: only succeeds if team has enough balance.
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

    -- Enforce monthly member limit if configured
    IF p_monthly_limit_check THEN
        SELECT monthly_points_limit, points_used_this_month
          INTO v_monthly_limit, v_used_this_month
          FROM member_quotas
         WHERE team_id = p_team_id
           AND user_id = p_user_id;

        IF v_monthly_limit IS NOT NULL
           AND (COALESCE(v_used_this_month, 0) + p_points_cost) > v_monthly_limit
        THEN
            -- Rollback the decrement to keep balance consistent
            UPDATE team_quotas
               SET points_balance = points_balance + p_points_cost
             WHERE team_id = p_team_id;

            RETURN QUERY SELECT FALSE, p_points_cost, v_balance + p_points_cost,
                format('Monthly points limit exceeded. Limit: %s, used: %s, required: %s.',
                       v_monthly_limit, COALESCE(v_used_this_month, 0), p_points_cost)::TEXT;
            RETURN;
        END IF;
    END IF;

    -- Increment per-member monthly counter (best-effort upsert)
    UPDATE member_quotas
       SET points_used_this_month = COALESCE(points_used_this_month, 0) + p_points_cost
     WHERE team_id = p_team_id
       AND user_id = p_user_id;

    RETURN QUERY SELECT TRUE, p_points_cost, v_balance, NULL::TEXT;
END;
$$;

GRANT EXECUTE ON FUNCTION public.rpc_consume_team_points(UUID, UUID, INTEGER, BOOLEAN)
    TO service_role, authenticated;
