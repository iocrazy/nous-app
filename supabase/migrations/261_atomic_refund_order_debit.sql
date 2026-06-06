-- ============================================================
-- BUG (sibling of mig 260): atomic, idempotent order-REFUND-and-debit RPC.
--
-- admin/credits_router.refund_order previously did TWO un-transactioned awaits:
-- update_order(...'refunded'...) THEN add_points(team_id, amount=-points, ...).
-- WORSE: points_service.add_points rejects amount <= 0 (returns success=False),
-- so the negative-amount refund was a SILENT NO-OP — the order got marked
-- 'refunded' but the granted points were NEVER clawed back (and the two writes
-- were non-atomic to boot).
--
-- This RPC does both in ONE transaction and is idempotent on (order_refund):
--   1. Locks the order row (FOR UPDATE).
--   2. Refundable only if payment_status='paid'.
--      - If already 'refunded' AND an 'order_refund' ledger row exists -> no-op
--        (already_refunded).
--      - If already 'refunded' but NO ledger row -> the crash gap -> debit once.
--      - Any other status (pending/failed/expired) -> success=false.
--   3. POLICY = CLAMP AT 0: deduct = LEAST(points_amount, current_balance);
--      new_balance = current_balance - deduct (NEVER negative). Already-spent
--      points are not clawed back; the business absorbs that. We do NOT block
--      on insufficient balance and we do NOT allow a negative balance.
--   4. Marks refunded + debits team_quotas + inserts the ledger row atomically.
--   5. The partial unique index enforces at-most-one refund per order; the
--      unique_violation handler rolls back the balance debit (race safety).
--
-- The refund idempotency uses reference_type='order_refund' so it never
-- collides with the confirm path's reference_type='order' (mig 260).
--
-- Mirrors mig 260 conventions (SECURITY DEFINER, search_path=public, TABLE
-- return, partial-unique idempotency index, unique_violation rollback). Debits
-- are stored as NEGATIVE point_transactions.amount (mig 041 ledger convention).
-- ============================================================

CREATE UNIQUE INDEX IF NOT EXISTS idx_point_transactions_unique_order_refund
    ON public.point_transactions (reference_type, reference_id)
    WHERE reference_type = 'order_refund' AND reference_id IS NOT NULL;

CREATE OR REPLACE FUNCTION public.rpc_refund_order_and_debit(p_order_id BIGINT)
RETURNS TABLE (success BOOLEAN, already_refunded BOOLEAN, points_debited INTEGER, new_balance INTEGER, reason TEXT)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public
AS $$
DECLARE
    v_order       orders%ROWTYPE;
    v_ref_id      TEXT := p_order_id::text;
    v_balance     INTEGER;
    v_deduct      INTEGER;
    v_new_balance INTEGER;
    v_has_quota   BOOLEAN;
    v_existing    BIGINT;
BEGIN
    SELECT * INTO v_order FROM orders WHERE id = p_order_id FOR UPDATE;
    IF NOT FOUND THEN
        RETURN QUERY SELECT FALSE, FALSE, 0, NULL::INTEGER, 'Order not found'::TEXT; RETURN;
    END IF;

    IF v_order.payment_status = 'refunded' THEN
        SELECT id INTO v_existing FROM point_transactions
            WHERE reference_type = 'order_refund' AND reference_id = v_ref_id LIMIT 1;
        IF FOUND THEN
            SELECT points_balance INTO v_new_balance FROM team_quotas WHERE team_id = v_order.team_id;
            RETURN QUERY SELECT TRUE, TRUE, 0, v_new_balance, NULL::TEXT; RETURN;
        END IF;
        -- refunded but no ledger row = the crash gap -> fall through and debit.
    ELSIF v_order.payment_status <> 'paid' THEN
        RETURN QUERY SELECT FALSE, FALSE, 0, NULL::INTEGER,
            format('Order status is ''%s'', cannot refund', v_order.payment_status)::TEXT; RETURN;
    END IF;

    -- Current balance (0 if no team_quotas row).
    SELECT points_balance INTO v_balance FROM team_quotas WHERE team_id = v_order.team_id;
    v_has_quota := FOUND;

    -- CLAMP AT 0: never claw back more than the team currently has.
    v_deduct := LEAST(v_order.points_amount, COALESCE(v_balance, 0));

    UPDATE orders SET payment_status = 'refunded', updated_at = now()
        WHERE id = p_order_id;

    IF v_has_quota THEN
        UPDATE team_quotas SET points_balance = points_balance - v_deduct
            WHERE team_id = v_order.team_id RETURNING points_balance INTO v_new_balance;
    ELSE
        v_new_balance := 0;  -- no quota row -> nothing to debit
    END IF;

    BEGIN
        INSERT INTO point_transactions (team_id, user_id, amount, balance_after, type, reference_type, reference_id, description)
        VALUES (v_order.team_id, v_order.user_id, -v_deduct, v_new_balance,
                'refund', 'order_refund', v_ref_id,
                format('Refunded order %s (-%s points)', p_order_id, v_deduct));
    EXCEPTION WHEN unique_violation THEN
        -- Race: another worker inserted the refund between our check and insert.
        -- Roll back the balance debit to keep the ledger honest.
        IF v_has_quota THEN
            UPDATE team_quotas SET points_balance = points_balance + v_deduct
                WHERE team_id = v_order.team_id RETURNING points_balance INTO v_new_balance;
        END IF;
        RETURN QUERY SELECT TRUE, TRUE, 0, v_new_balance, NULL::TEXT; RETURN;
    END;

    RETURN QUERY SELECT TRUE, FALSE, v_deduct, v_new_balance, NULL::TEXT;
END;
$$;

GRANT EXECUTE ON FUNCTION public.rpc_refund_order_and_debit(BIGINT) TO service_role, authenticated;

NOTIFY pgrst, 'reload schema';
