-- 108_user_cookies.sql
-- Per-user platform cookie storage (Douyin, Bilibili, YouTube)

CREATE TABLE IF NOT EXISTS user_cookies (
    id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    platform VARCHAR(50) NOT NULL,
    cookie_text TEXT,
    cookie_file TEXT,
    is_valid BOOLEAN NOT NULL DEFAULT true,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(user_id, platform)
);

-- Index for fast per-user platform lookups
CREATE INDEX idx_user_cookies_user_platform ON user_cookies(user_id, platform);

-- Auto-update updated_at on row changes
CREATE TRIGGER trg_user_cookies_updated_at
    BEFORE UPDATE ON user_cookies
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- RLS
ALTER TABLE user_cookies ENABLE ROW LEVEL SECURITY;

-- Users can manage their own cookies
CREATE POLICY "Users can manage own cookies"
    ON user_cookies FOR ALL
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

-- Service role has full access
CREATE POLICY "Service role full access on user_cookies"
    ON user_cookies FOR ALL
    USING (auth.jwt()->>'role' = 'service_role');

COMMENT ON TABLE user_cookies IS 'Per-user platform cookie storage for authenticated media downloads (douyin, bilibili, youtube)';
COMMENT ON COLUMN user_cookies.platform IS 'Platform identifier: douyin, bilibili, youtube';
COMMENT ON COLUMN user_cookies.cookie_text IS 'Raw cookie string pasted by user';
COMMENT ON COLUMN user_cookies.cookie_file IS 'Netscape cookies.txt content uploaded by user';
COMMENT ON COLUMN user_cookies.is_valid IS 'Whether the cookie has been validated successfully';
COMMENT ON COLUMN user_cookies.error_message IS 'Last validation or usage error message';
