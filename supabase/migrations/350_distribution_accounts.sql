-- Migration 350: Distribution — social platform accounts + OAuth state
--
-- social_accounts: bound platform accounts (Douyin first). Tokens are
-- Fernet-encrypted at the application layer (app.core.secret_box) before
-- INSERT — this table never sees plaintext tokens.
-- distribution_oauth_states: short-lived CSRF state for the OAuth dance.
-- The media-router prototype kept these in a process dict; with multiple
-- workers that loses states on restart/routing, so they live in Postgres
-- with a 10-minute TTL (expired rows deleted opportunistically on read).
--
-- Idempotent: IF NOT EXISTS everywhere; re-apply is a no-op.

BEGIN;

CREATE TABLE IF NOT EXISTS public.social_accounts (
    id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    scope_type VARCHAR(10) NOT NULL CHECK (scope_type IN ('user', 'team')),
    scope_id TEXT NOT NULL,          -- auth.users.id (uuid text) or teams.id (bigint text)
    platform VARCHAR(50) NOT NULL,
    platform_user_id TEXT NOT NULL,  -- open_id
    username TEXT NOT NULL,
    avatar_url TEXT,
    access_token TEXT,               -- Fernet ciphertext (gAAAAA…)
    refresh_token TEXT,              -- Fernet ciphertext
    token_expires_at TIMESTAMPTZ,
    status VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'expired')),
    created_by UUID NOT NULL,        -- auth user who bound it
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (scope_type, scope_id, platform, platform_user_id)
);

CREATE INDEX IF NOT EXISTS idx_social_accounts_scope
    ON public.social_accounts (scope_type, scope_id);

CREATE TABLE IF NOT EXISTS public.distribution_oauth_states (
    state TEXT PRIMARY KEY,
    user_id UUID NOT NULL,
    platform VARCHAR(50) NOT NULL,
    scope_type VARCHAR(10) NOT NULL,
    scope_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Backend-only tables: reached solely by service_role repository code, never
-- via PostgREST from the frontend. Lock down with the same service-role-only
-- pattern as 344_script_commits.sql (see also 265's retroactive lockdown of
-- world-readable log tables — don't repeat that class of bug here).
ALTER TABLE public.social_accounts ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Service role full access on social_accounts" ON public.social_accounts;
CREATE POLICY "Service role full access on social_accounts" ON public.social_accounts FOR ALL
  USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');

ALTER TABLE public.distribution_oauth_states ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Service role full access on distribution_oauth_states" ON public.distribution_oauth_states;
CREATE POLICY "Service role full access on distribution_oauth_states" ON public.distribution_oauth_states FOR ALL
  USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');

NOTIFY pgrst, 'reload schema';

COMMIT;
