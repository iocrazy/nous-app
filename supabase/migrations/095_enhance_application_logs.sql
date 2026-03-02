-- Enhance application_logs with RLS and cleanup function
-- (table already created by 088_application_logs.sql)

ALTER TABLE application_logs ENABLE ROW LEVEL SECURITY;

CREATE POLICY IF NOT EXISTS "service_role_all" ON application_logs
    FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY IF NOT EXISTS "authenticated_select" ON application_logs
    FOR SELECT TO authenticated USING (true);

CREATE OR REPLACE FUNCTION cleanup_old_application_logs(retention_days INTEGER DEFAULT 7)
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM application_logs
    WHERE logged_at < NOW() - (retention_days || ' days')::INTERVAL;
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;
