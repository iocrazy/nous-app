-- 214_fix_initialize_user_credits_null.sql
--
-- Fix `initialize_user_credits` trigger function so it doesn't crash when
-- the `system_settings` row for 'new_user_credits' is missing.
--
-- Bug
-- ---
-- The original function relied on `SELECT INTO` to read the initial credit
-- amount from `system_settings`. When the row was missing (the default on
-- a freshly-bootstrapped Supabase), `SELECT INTO` left `initial_credits`
-- as NULL — the inner `COALESCE((value)::integer, 100)` only handled the
-- case where the row existed but `value` was NULL, NOT the case where the
-- row was missing entirely. Result: subsequent
--
--     INSERT INTO user_credits (user_id, balance, total_earned)
--     VALUES (NEW.id, initial_credits, initial_credits)
--
-- failed with `null value in column "balance" of relation "user_credits"
-- violates not-null constraint`, which propagated as a transaction abort
-- and prevented ANY new row from being added to `user_profiles` (because
-- this is an AFTER INSERT trigger).
--
-- Repro: in prod 2026-05-10, attempting to seed an admin role row
-- (`INSERT INTO public.user_profiles ...`) for an existing auth user
-- failed with this exact constraint error. Worked around at the time by
-- bypassing the trigger via `SET session_replication_role = replica`.
--
-- Fix
-- ---
-- Wrap the SELECT in a scalar subquery so a missing row + missing value
-- both fall through to the default 100. The aggregate-MAX form would also
-- work but is less readable.
--
-- Also defensive: clamp negative results to 0 so a manual misconfiguration
-- (someone setting `new_user_credits = -1` to test) doesn't propagate a
-- negative balance into the credit ledger.

CREATE OR REPLACE FUNCTION public.initialize_user_credits()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public', 'pg_catalog'
AS $function$
DECLARE
    initial_credits INTEGER;
BEGIN
    -- Resolve initial credit amount with two fallback layers:
    --   1. system_settings row missing entirely → COALESCE the scalar
    --      subquery to the literal 100
    --   2. negative configured value → clamp to 0 (defensive)
    initial_credits := GREATEST(
        COALESCE(
            (SELECT (value)::integer FROM system_settings WHERE key = 'new_user_credits'),
            100
        ),
        0
    );

    -- Create credit account with initial balance
    INSERT INTO user_credits (user_id, balance, total_earned)
    VALUES (NEW.id, initial_credits, initial_credits)
    ON CONFLICT (user_id) DO NOTHING;

    -- Log the initial credit gift if credits were given
    IF initial_credits > 0 THEN
        INSERT INTO credit_transactions (user_id, amount, type, description)
        VALUES (NEW.id, initial_credits, 'gift', 'Welcome bonus for new user');
    END IF;

    RETURN NEW;
END;
$function$;
