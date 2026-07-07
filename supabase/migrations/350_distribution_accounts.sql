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

COMMIT;
