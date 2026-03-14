-- 103: Temporary tokens for secure web page access
-- Used by Shortcuts/browser to load tags without exposing API Key in URL

CREATE TABLE IF NOT EXISTS temp_tokens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token VARCHAR(64) NOT NULL UNIQUE,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    scopes TEXT[] NOT NULL DEFAULT '{}',
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Index for fast token lookup
CREATE INDEX idx_temp_tokens_token ON temp_tokens(token);

-- Auto-cleanup expired tokens (older than 1 hour)
CREATE INDEX idx_temp_tokens_expires ON temp_tokens(expires_at);

-- RLS
ALTER TABLE temp_tokens ENABLE ROW LEVEL SECURITY;

-- Service role can do everything
CREATE POLICY "service_role_all" ON temp_tokens
    FOR ALL TO service_role USING (true) WITH CHECK (true);

COMMENT ON TABLE temp_tokens IS 'Short-lived tokens for secure web page access (e.g. Shortcuts tag picker)';
