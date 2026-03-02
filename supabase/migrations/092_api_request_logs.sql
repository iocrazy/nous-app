-- Migration 088: API Request Logs & Frontend Error Logs
-- Global audit logging for all API requests and frontend error reporting

-- ============================================
-- Snowflake ID Infrastructure (idempotent)
-- 53-bit structure: timestamp(41) | sequence(12)
-- Custom epoch: 2024-01-01 00:00:00 UTC
-- ============================================

CREATE SEQUENCE IF NOT EXISTS snowflake_seq
  CYCLE
  MINVALUE 0
  MAXVALUE 4095;

CREATE OR REPLACE FUNCTION generate_snowflake_id()
RETURNS BIGINT AS $$
DECLARE
  epoch BIGINT := 1704067200000;  -- 2024-01-01 00:00:00 UTC in ms
  now_ms BIGINT;
  seq INT;
  result BIGINT;
BEGIN
  now_ms := (EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT - epoch;
  seq := nextval('snowflake_seq') % 4096;
  result := (now_ms << 12) | seq;
  RETURN result;
END;
$$ LANGUAGE plpgsql;

-- ============================================
-- Table: api_request_logs
-- ============================================

CREATE TABLE IF NOT EXISTS api_request_logs (
    id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    request_id VARCHAR(36) NOT NULL,
    user_id UUID,
    auth_type VARCHAR(20) DEFAULT 'anonymous',
    method VARCHAR(10) NOT NULL,
    path VARCHAR(500) NOT NULL,
    query_params JSONB,
    request_body JSONB,
    status_code INTEGER,
    response_time_ms INTEGER,
    ip_address VARCHAR(45),
    user_agent TEXT,
    error_detail TEXT,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_api_request_logs_timestamp ON api_request_logs (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_api_request_logs_user_id ON api_request_logs (user_id);
CREATE INDEX IF NOT EXISTS idx_api_request_logs_path ON api_request_logs (path);
CREATE INDEX IF NOT EXISTS idx_api_request_logs_status_code ON api_request_logs (status_code);
CREATE INDEX IF NOT EXISTS idx_api_request_logs_request_id ON api_request_logs (request_id);
CREATE INDEX IF NOT EXISTS idx_api_request_logs_method ON api_request_logs (method);
CREATE INDEX IF NOT EXISTS idx_api_request_logs_composite ON api_request_logs (path, status_code, timestamp DESC);

-- ============================================
-- Table: frontend_error_logs
-- ============================================

CREATE TABLE IF NOT EXISTS frontend_error_logs (
    id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    user_id UUID,
    session_id VARCHAR(100),
    error_type VARCHAR(100) NOT NULL,
    message TEXT,
    stack TEXT,
    url VARCHAR(1000),
    component VARCHAR(255),
    user_agent TEXT,
    metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_frontend_error_logs_created_at ON frontend_error_logs (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_frontend_error_logs_user_id ON frontend_error_logs (user_id);
CREATE INDEX IF NOT EXISTS idx_frontend_error_logs_error_type ON frontend_error_logs (error_type);

-- ============================================
-- RLS Policies
-- ============================================

ALTER TABLE api_request_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE frontend_error_logs ENABLE ROW LEVEL SECURITY;

-- Service role: full access
CREATE POLICY "service_role_api_request_logs" ON api_request_logs
    FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY "service_role_frontend_error_logs" ON frontend_error_logs
    FOR ALL TO service_role USING (true) WITH CHECK (true);

-- Authenticated users (admin): read only
CREATE POLICY "admin_read_api_request_logs" ON api_request_logs
    FOR SELECT TO authenticated USING (true);

CREATE POLICY "admin_read_frontend_error_logs" ON frontend_error_logs
    FOR SELECT TO authenticated USING (true);

-- Allow authenticated users to insert frontend errors (for error reporting)
CREATE POLICY "authenticated_insert_frontend_error_logs" ON frontend_error_logs
    FOR INSERT TO authenticated WITH CHECK (true);

-- Allow anonymous users to insert frontend errors (errors can happen pre-login)
CREATE POLICY "anon_insert_frontend_error_logs" ON frontend_error_logs
    FOR INSERT TO anon WITH CHECK (true);

-- ============================================
-- Cleanup Function
-- ============================================

CREATE OR REPLACE FUNCTION cleanup_old_request_logs(retention_days INTEGER DEFAULT 30)
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM api_request_logs
    WHERE timestamp < NOW() - (retention_days || ' days')::INTERVAL;
    GET DIAGNOSTICS deleted_count = ROW_COUNT;

    DELETE FROM frontend_error_logs
    WHERE created_at < NOW() - (retention_days || ' days')::INTERVAL;

    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;
