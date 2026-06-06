-- ============================================================
-- BUG 7: atomic, idempotent order-confirm-and-credit RPC.
--
-- Both payment_service.handle_callback and admin/credits_router.confirm_order
-- previously did TWO un-transactioned awaits: update_order(...'paid'...) THEN
-- add_points(...). A crash between them left the order 'paid' but the points
-- uncredited; a duplicate callback could double-credit (the only ref-unique
-- index — mig 123's idx_point_transactions_unique_refund — is refund-only).
--
-- This RPC does both in ONE transaction and is idempotent on (order):
--   1. Locks the order row (FOR UPDATE).
--   2. If already 'paid' AND a 'order' ledger row exists -> no-op
--      (already_credited).
--   3. If already 'paid' but NO ledger row -> the crash gap -> credit once.
--   4. Marks paid + credits team_quotas + inserts the ledger row atomically.
--   5. The partial unique index enforces at-most-one credit per order; the
--      unique_violation handler rolls back the balance bump (race safety).
--
-- Mirrors mig 120/123 conventions (SECURITY DEFINER, search_path=public,
-- TABLE return, partial-unique idempotency index).
-- ============================================================

CREATE UNIQUE INDEX IF NOT EXISTS idx_point_transactions_unique_order
    ON public.point_transactions (reference_type, reference_id)
    WHERE reference_type = 'order' AND reference_id IS NOT NULL;

CREATE OR REPLACE FUNCTION public.rpc_confirm_order_and_credit(p_order_id BIGINT)
RETURNS TABLE (success BOOLEAN, already_credited BOOLEAN, points_added INTEGER, new_balance INTEGER, reason TEXT)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public
AS $$
DECLARE
    v_order       orders%ROWTYPE;
    v_ref_id      TEXT := p_order_id::text;
    v_new_balance INTEGER;
    v_existing    BIGINT;
BEGIN
    SELECT * INTO v_order FROM orders WHERE id = p_order_id FOR UPDATE;
    IF NOT FOUND THEN
        RETURN QUERY SELECT FALSE, FALSE, 0, NULL::INTEGER, 'Order not found'::TEXT; RETURN;
    END IF;
    IF v_order.payment_status = 'paid' THEN
        SELECT id INTO v_existing FROM point_transactions
            WHERE reference_type = 'order' AND reference_id = v_ref_id LIMIT 1;
        IF FOUND THEN
            SELECT points_balance INTO v_new_balance FROM team_quotas WHERE team_id = v_order.team_id;
            RETURN QUERY SELECT TRUE, TRUE, 0, v_new_balance, NULL::TEXT; RETURN;
        END IF;
        -- paid but no ledger row = the crash gap -> fall through and credit.
    ELSIF v_order.payment_status <> 'pending' THEN
        RETURN QUERY SELECT FALSE, FALSE, 0, NULL::INTEGER,
            format('Order status is ''%s'', cannot confirm', v_order.payment_status)::TEXT; RETURN;
    END IF;
    UPDATE orders SET payment_status='paid', paid_at=COALESCE(paid_at, now()), updated_at=now()
        WHERE id = p_order_id;
    INSERT INTO team_quotas (team_id, points_balance) VALUES (v_order.team_id, 0)
        ON CONFLICT (team_id) DO NOTHING;
    UPDATE team_quotas SET points_balance = points_balance + v_order.points_amount
        WHERE team_id = v_order.team_id RETURNING points_balance INTO v_new_balance;
    BEGIN
        INSERT INTO point_transactions (team_id, user_id, amount, balance_after, type, reference_type, reference_id, description)
        VALUES (v_order.team_id, v_order.user_id, v_order.points_amount, v_new_balance,
                'purchase', 'order', v_ref_id,
                format('Purchased %s points (order %s)', v_order.points_amount, p_order_id));
    EXCEPTION WHEN unique_violation THEN
        UPDATE team_quotas SET points_balance = points_balance - v_order.points_amount
            WHERE team_id = v_order.team_id RETURNING points_balance INTO v_new_balance;
        RETURN QUERY SELECT TRUE, TRUE, 0, v_new_balance, NULL::TEXT; RETURN;
    END;
    RETURN QUERY SELECT TRUE, FALSE, v_order.points_amount, v_new_balance, NULL::TEXT;
END;
$$;

GRANT EXECUTE ON FUNCTION public.rpc_confirm_order_and_credit(BIGINT) TO service_role, authenticated;

NOTIFY pgrst, 'reload schema';
