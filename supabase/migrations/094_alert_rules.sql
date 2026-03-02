-- Alert rules and alert history for monitoring notifications

-- Alert rules table
CREATE TABLE IF NOT EXISTS alert_rules (
    id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    name VARCHAR(255) NOT NULL,
    metric_type VARCHAR(50) NOT NULL,       -- error_rate, avg_response_time, error_count, log_level_count
    condition VARCHAR(10) NOT NULL,         -- gt, gte, lt, lte, eq
    threshold FLOAT NOT NULL,
    window_minutes INTEGER NOT NULL DEFAULT 5,
    notification_channel VARCHAR(50) NOT NULL DEFAULT 'discord',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    is_muted BOOLEAN NOT NULL DEFAULT FALSE,
    mute_until TIMESTAMPTZ,
    created_by UUID REFERENCES auth.users(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Alert history table
CREATE TABLE IF NOT EXISTS alert_history (
    id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    rule_id BIGINT NOT NULL REFERENCES alert_rules(id) ON DELETE CASCADE,
    rule_name VARCHAR(255) NOT NULL,
    metric_type VARCHAR(50) NOT NULL,
    metric_value FLOAT NOT NULL,
    threshold FLOAT NOT NULL,
    condition VARCHAR(10) NOT NULL,
    message TEXT NOT NULL,
    notified BOOLEAN NOT NULL DEFAULT FALSE,
    resolved BOOLEAN NOT NULL DEFAULT FALSE,
    resolved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_alert_rules_active ON alert_rules (is_active) WHERE is_active = TRUE;
CREATE INDEX idx_alert_history_created_at ON alert_history (created_at DESC);
CREATE INDEX idx_alert_history_rule_id ON alert_history (rule_id);
CREATE INDEX idx_alert_history_resolved ON alert_history (resolved) WHERE resolved = FALSE;

-- RLS
ALTER TABLE alert_rules ENABLE ROW LEVEL SECURITY;
ALTER TABLE alert_history ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service role full access on alert_rules"
    ON alert_rules FOR ALL
    USING (auth.role() = 'service_role');

CREATE POLICY "Authenticated users can read alert_rules"
    ON alert_rules FOR SELECT
    USING (auth.role() = 'authenticated');

CREATE POLICY "Service role full access on alert_history"
    ON alert_history FOR ALL
    USING (auth.role() = 'service_role');

CREATE POLICY "Authenticated users can read alert_history"
    ON alert_history FOR SELECT
    USING (auth.role() = 'authenticated');

-- Cleanup function
CREATE OR REPLACE FUNCTION cleanup_old_alert_history(retention_days INTEGER DEFAULT 30)
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM alert_history
    WHERE created_at < NOW() - (retention_days || ' days')::INTERVAL;
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;
