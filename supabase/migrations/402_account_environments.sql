-- Migration 402: Distribution — per-account browser environment (S1 storage, S4 UI)
--
-- One environment per session-bound account, pinned for the account's whole
-- life. The platform's risk engine reads the browser's fingerprint surface as
-- an identity: an account that publishes from a new IP / timezone / UA every
-- run looks exactly like a stolen session. So this is a 1:1 table (UNIQUE on
-- account_id), not a history table — the row is meant to be written once and
-- edited rarely.
-- See docs/superpowers/specs/2026-08-04-distribution-session-channel-design.md §3.2.
--
--   proxy_url              — Fernet ciphertext (app.core.secret_box). It carries
--                            the proxy's user:password, so it is a secret on the
--                            same footing as social_accounts.access_token and
--                            never leaves the repository layer in cleartext.
--                            NULL = direct connection.
--   user_agent / locale /
--   timezone_id / geo_*    — fed straight into Playwright's context options.
--                            These three MUST agree with the proxy's exit-IP
--                            geography (spec §3.2): a Beijing IP with an
--                            America/New_York timezone is a risk signal, not a
--                            harmless default.
--   fingerprint_profile_id — reserved for S6 (AdsPower / 比特浏览器 profile id).
--                            Unused in S1–S5; present so switching from
--                            chromium.launch() to connect_over_cdp() is a
--                            service-layer change with no migration.
--
-- RLS: service-role only, same as 351_distribution_accounts.sql — this table is
-- reached solely by service_role repository code, never via PostgREST from the
-- frontend (a world-readable proxy credential column would be the 265 class of
-- bug all over again).
--
-- Idempotent: CREATE TABLE / CREATE INDEX IF NOT EXISTS + DROP POLICY IF EXISTS
-- before CREATE POLICY; re-apply is a no-op.

BEGIN;

CREATE TABLE IF NOT EXISTS public.account_environments (
    id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    account_id BIGINT NOT NULL UNIQUE
        REFERENCES public.social_accounts (id) ON DELETE CASCADE,
    proxy_url TEXT,                              -- Fernet ciphertext (contains credentials); NULL = direct
    user_agent TEXT,
    locale VARCHAR(20) DEFAULT 'zh-CN',
    timezone_id VARCHAR(50) DEFAULT 'Asia/Shanghai',
    geo_lat DOUBLE PRECISION,
    geo_lng DOUBLE PRECISION,
    fingerprint_profile_id TEXT,                 -- S6: AdsPower profile id
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE public.account_environments IS
  'Per-account browser environment for the session publish channel (mig 402, spec §3.2). One row per account, pinned long-term.';
COMMENT ON COLUMN public.account_environments.proxy_url IS
  'Fernet ciphertext of the proxy URL including credentials. NULL = direct connection. Never returned to the frontend.';
COMMENT ON COLUMN public.account_environments.fingerprint_profile_id IS
  'Reserved for S6 (AdsPower/BitBrowser profile id). Unused while the browser is self-hosted Playwright.';

-- account_id already carries a UNIQUE index from the column constraint; no
-- separate lookup index is needed (every read is by account_id).

ALTER TABLE public.account_environments ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Service role full access on account_environments" ON public.account_environments;
CREATE POLICY "Service role full access on account_environments" ON public.account_environments FOR ALL
  USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');

NOTIFY pgrst, 'reload schema';

COMMIT;
