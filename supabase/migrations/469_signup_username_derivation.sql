-- 469_signup_username_derivation.sql
--
-- `handle_new_user()` can fail the whole signup. Two ways, both reachable today.
--
-- The function runs as an AFTER INSERT trigger on `auth.users`, so anything it
-- raises rolls the user row back with it: the person does not get a broken
-- profile, they get "signup failed" and no account at all.
--
-- ① NO EMAIL → the team name is NULL
-- ===================================
--     uname := COALESCE(raw_user_meta_data->>'username', split_part(email,'@',1))
--     INSERT INTO teams (name, ...) VALUES (uname || '''s Workspace', ...)
--
-- `split_part(NULL, '@', 1)` is NULL, so `uname` is NULL, so the concatenation
-- is NULL, and `teams.name` is NOT NULL. Every emailless signup dies here.
--
-- This is not hypothetical — production has both emailless paths **enabled**:
--
--     GOTRUE_EXTERNAL_PHONE_ENABLED=true
--     GOTRUE_EXTERNAL_ANONYMOUS_USERS_ENABLED=true
--
-- Nobody has used them yet (11 users, all with an email), which is the only
-- reason this has never fired. The first phone or anonymous signup would.
--
-- ② DUPLICATE LOCAL PART → the username collides
-- ===============================================
-- `user_profiles.username` carries a UNIQUE constraint, and the derivation is
-- the email's local part — so `test@a.com` and `test@b.com` both want `test`.
-- The `ON CONFLICT (id) DO NOTHING` does NOT cover that: the conflict is on
-- `user_profiles_username_key`, a different constraint, so it raises.
--
-- Production already holds the near miss: `test@mediahub.dev` has username
-- `test`, and `test@test.com` only avoided the collision because its signup
-- form happened to pass `testuser` in the metadata.
--
-- THE FIX
-- =======
-- Derive a name that always exists, then make it unique before inserting.
-- Nothing about the happy path changes: an email signup still becomes its local
-- part, and an explicit `username` in the metadata still wins over everything.
--
-- ⚠️ Deliberately NOT changed: the suffix only appears on a genuine collision,
-- so no existing user's name moves. This migration writes no rows at all — it
-- only redefines the function for signups from here on.

CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER AS $$
DECLARE
    uname TEXT;
    base  TEXT;
BEGIN
    -- The first non-empty of: what they asked to be called, their email's local
    -- part, and finally a uuid-derived name that always exists. NULLIF collapses
    -- '' into "absent" — an empty string would sail through COALESCE and produce
    -- the "'s Workspace" team nobody wants.
    --
    -- ⚠️ `NEW.phone` is deliberately NOT one of the arms, though it exists on
    -- `auth.users` and a phone signup is exactly the case that used to fail.
    -- Two reasons: a username is shown to other people (team member lists), and
    -- a phone number is not a display name — it is PII that would leak into one.
    -- And referencing the column would make this function depend on a field
    -- `supabase/ci_bootstrap.sql`'s `auth.users` stub does not carry, so no test
    -- we own could ever run it. A phone signup gets `user_<short uuid>`, same as
    -- an anonymous one, and can rename itself afterwards.
    base := COALESCE(
        NULLIF(NEW.raw_user_meta_data->>'username', ''),
        NULLIF(split_part(COALESCE(NEW.email, ''), '@', 1), ''),
        'user_' || substr(replace(NEW.id::text, '-', ''), 1, 8)
    );

    -- `user_profiles.username` is UNIQUE. Two people whose emails share a local
    -- part both derive the same name, and the loser's signup used to die on
    -- `user_profiles_username_key`. Suffix with the user's own uuid, which is
    -- unique by construction — so this terminates, no retry loop needed.
    uname := base;
    IF EXISTS (SELECT 1 FROM public.user_profiles WHERE username = uname) THEN
        uname := base || '_' || substr(replace(NEW.id::text, '-', ''), 1, 8);
    END IF;
    -- Paranoia for the 1-in-4-billion case where the short id also collides:
    -- fall back to the full uuid. `username` is varchar(255), so it fits.
    IF EXISTS (SELECT 1 FROM public.user_profiles WHERE username = uname) THEN
        uname := base || '_' || replace(NEW.id::text, '-', '');
    END IF;

    -- Create user profile (idempotent on re-fire; the username arm above is
    -- what makes the INSERT itself safe).
    INSERT INTO public.user_profiles (id, username, role)
    VALUES (NEW.id, uname, 'user')
    ON CONFLICT (id) DO NOTHING;

    -- Auto-create personal team.
    -- kind='personal' is explicit so the column DEFAULT ('collaborative')
    -- does not take effect.
    -- Idempotent: uq_teams_owner_personal prevents a second personal team.
    INSERT INTO public.teams (name, owner_id, kind)
    VALUES (
        uname || '''s Workspace',
        NEW.id,
        'personal'
    )
    ON CONFLICT DO NOTHING;

    -- Hand them their initial tags (mig 468). Idempotent; ungrouped if the
    -- tag_groups rows are absent.
    PERFORM public.seed_initial_tags(NEW.id);

    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;

NOTIFY pgrst, 'reload schema';
