-- ============================================================
-- Idempotent refund RPC.
--
-- Celery retries (max_retries=2 in ai_tasks) could previously refund
-- the same task up to 3 times. The refund path read balance and wrote
-- it back from Python, which is also non-atomic.
--
-- This RPC:
--   1. Atomically increments team_quotas.points_balance (no read-modify-write)
--   2. Inserts the refund transaction; if a refund transaction for the
--      same (team_id, reference_type, reference_id, type='refund') already
--      exists, the whole operation is a no-op (idempotent)
--
-- The partial unique index enforces at-most-one refund per reference.
-- ============================================================

CREATE UNIQUE INDEX IF NOT EXISTS idx_point_transactions_unique_refund
    ON public.point_transactions (team_id, reference_type, reference_id)
    WHERE type = 'refund' AND reference_id IS NOT NULL;


CREATE OR REPLACE FUNCTION public.rpc_refund_team_points_idempotent(
    p_team_id BIGINT,
    p_user_id UUID,
    p_amount INTEGER,
    p_reference_type TEXT,
    p_reference_id TEXT,
    p_description TEXT
)
RETURNS TABLE (
    success BOOLEAN,
    already_refunded BOOLEAN,
    new_balance INTEGER
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_existing BIGINT;
    v_new_balance INTEGER;
BEGIN
    IF p_amount <= 0 THEN
        RETURN QUERY SELECT FALSE, FALSE, NULL::INTEGER;
        RETURN;
    END IF;

    -- Idempotency check: is this (team, reference_type, reference_id) already refunded?
    IF p_reference_id IS NOT NULL THEN
        SELECT id INTO v_existing
          FROM point_transactions
         WHERE team_id = p_team_id
           AND reference_type = p_reference_type
           AND reference_id = p_reference_id
           AND type = 'refund'
         LIMIT 1;

        IF FOUND THEN
            SELECT points_balance INTO v_new_balance
              FROM team_quotas
             WHERE team_id = p_team_id;
            RETURN QUERY SELECT TRUE, TRUE, v_new_balance;
            RETURN;
        END IF;
    END IF;

    -- Atomic credit (no read-modify-write)
    UPDATE team_quotas
       SET points_balance = points_balance + p_amount
     WHERE team_id = p_team_id
    RETURNING points_balance INTO v_new_balance;

    IF NOT FOUND THEN
        -- No team_quota row — can't refund to a nonexistent ledger
        RETURN QUERY SELECT FALSE, FALSE, NULL::INTEGER;
        RETURN;
    END IF;

    -- Record the refund transaction; unique index makes duplicate inserts fail
    BEGIN
        INSERT INTO point_transactions (
            team_id, user_id, amount, balance_after,
            type, reference_type, reference_id, description
        )
        VALUES (
            p_team_id, p_user_id, p_amount, v_new_balance,
            'refund', p_reference_type, p_reference_id, p_description
        );
    EXCEPTION WHEN unique_violation THEN
        -- Race: another worker inserted the refund between our check and insert.
        -- Roll back the balance increment to keep the ledger honest.
        UPDATE team_quotas
           SET points_balance = points_balance - p_amount
         WHERE team_id = p_team_id
        RETURNING points_balance INTO v_new_balance;

        RETURN QUERY SELECT TRUE, TRUE, v_new_balance;
        RETURN;
    END;

    RETURN QUERY SELECT TRUE, FALSE, v_new_balance;
END;
$$;

GRANT EXECUTE ON FUNCTION public.rpc_refund_team_points_idempotent(
    BIGINT, UUID, INTEGER, TEXT, TEXT, TEXT
) TO service_role, authenticated;
