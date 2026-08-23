-- 437 — codex per-user daemon registry (C 方案 C1).
--
-- Each row is ONE paired device belonging to ONE user. The daemon runs on
-- the user's own machine with their own codex login; nous never sees that
-- login — it only knows this device exists and holds a token hash to
-- authenticate the device's outbound WebSocket.
--
-- Design: docs/superpowers/specs/2026-08-23-codex-per-user-daemon-design.md

CREATE TABLE IF NOT EXISTS public.codex_daemons (
    id            BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    user_id       UUID        NOT NULL,
    device_name   TEXT        NOT NULL,
    platform      TEXT        NOT NULL DEFAULT '',
    -- sha256 of the device token. The plaintext is returned exactly once at
    -- pairing time and never stored (same rule as API keys).
    token_hash    TEXT        NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at  TIMESTAMPTZ,
    revoked_at    TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS codex_daemons_token_hash_key
    ON public.codex_daemons (token_hash);
CREATE INDEX IF NOT EXISTS codex_daemons_user_idx
    ON public.codex_daemons (user_id)
    WHERE revoked_at IS NULL;

COMMENT ON TABLE public.codex_daemons IS
    'Paired per-user codex daemons (C 方案). token_hash authenticates the '
    'device WebSocket; the user''s codex credentials never leave their machine.';
