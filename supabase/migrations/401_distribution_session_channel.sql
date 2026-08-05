-- Migration 401: Distribution — session channel on social_accounts (S1)
--
-- Adds the third publish channel's storage. Until now every row in
-- social_accounts was an OAuth binding (mig 351); the session channel binds an
-- account by driving a real browser and keeping the platform's web session.
-- See docs/superpowers/specs/2026-08-04-distribution-session-channel-design.md §3.1.
--
--   auth_type          — 'oauth' (mig 351 semantics, the default so every
--                        existing row keeps meaning exactly what it meant) or
--                        'session' (cookie/storage_state binding).
--   session_state      — Fernet ciphertext of Playwright's storage_state JSON,
--                        encrypted at the application layer (app.core.secret_box)
--                        exactly like access_token. This column NEVER sees
--                        plaintext: the cleartext storage_state is allowed to
--                        exist only inside the nous-browser process memory —
--                        never on disk, never in a log, never in DBOS workflow
--                        input/output (spec §7.6).
--   session_checked_at — last time the session was proven live by the health
--                        sweep (spec §4.4). NULL = never checked; the sweep
--                        orders by this column NULLS FIRST so fresh bindings
--                        get verified first.
--
-- status gains 'needs_relogin': distinct from 'expired' because the remedy is
-- different (OAuth expiry → reauthorize URL; session death → rescan a QR code),
-- and the frontend's "needs attention" counter must treat both as actionable
-- (spec §4.3 #4 — silently counting 0 while accounts are offline is exactly the
-- silent-no-op class CLAUDE.md forbids).
--
-- Idempotent: ADD COLUMN IF NOT EXISTS + DROP CONSTRAINT IF EXISTS before every
-- ADD CONSTRAINT; re-apply is a no-op. CHECKs are added as separately named
-- constraints (not inline on ADD COLUMN) so a re-run reconciles the constraint
-- even when the column already exists.

BEGIN;

ALTER TABLE public.social_accounts
  ADD COLUMN IF NOT EXISTS auth_type VARCHAR(10) NOT NULL DEFAULT 'oauth',
  ADD COLUMN IF NOT EXISTS session_state TEXT,
  ADD COLUMN IF NOT EXISTS session_checked_at TIMESTAMPTZ;

ALTER TABLE public.social_accounts DROP CONSTRAINT IF EXISTS social_accounts_auth_type_check;
ALTER TABLE public.social_accounts ADD CONSTRAINT social_accounts_auth_type_check
  CHECK (auth_type IN ('oauth', 'session'));

-- 351 declared status's CHECK inline on the column, so Postgres named it
-- social_accounts_status_check. Drop-then-add is the only way to widen it.
ALTER TABLE public.social_accounts DROP CONSTRAINT IF EXISTS social_accounts_status_check;
ALTER TABLE public.social_accounts ADD CONSTRAINT social_accounts_status_check
  CHECK (status IN ('active', 'expired', 'needs_relogin'));

COMMENT ON COLUMN public.social_accounts.auth_type IS
  'How this account is bound: oauth (open-platform token, mig 351) or session (browser storage_state, mig 401).';
COMMENT ON COLUMN public.social_accounts.session_state IS
  'Fernet ciphertext of the Playwright storage_state JSON. NEVER store plaintext here — cleartext lives only in nous-browser process memory (spec §7.6).';
COMMENT ON COLUMN public.social_accounts.session_checked_at IS
  'Last successful session validation (spec §4.4 health sweep). NULL = never checked.';

-- Health sweep candidate scan: auth_type='session' AND status='active' ordered
-- by session_checked_at. Partial index keeps it to the session rows only.
CREATE INDEX IF NOT EXISTS idx_social_accounts_session_check
    ON public.social_accounts (session_checked_at NULLS FIRST)
    WHERE auth_type = 'session';

NOTIFY pgrst, 'reload schema';

COMMIT;
