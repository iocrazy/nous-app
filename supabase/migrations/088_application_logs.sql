-- Application logs table for storing loguru output from FastAPI and Celery workers
-- Enables database-level log querying and monitoring

CREATE TABLE IF NOT EXISTS application_logs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    level VARCHAR(10) NOT NULL,
    message TEXT NOT NULL,
    module VARCHAR(255),
    function VARCHAR(255),
    line INTEGER,
    file_path VARCHAR(500),
    exception TEXT,
    extra JSONB DEFAULT '{}',
    logged_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_application_logs_logged_at ON application_logs(logged_at DESC);
CREATE INDEX IF NOT EXISTS idx_application_logs_level ON application_logs(level);
CREATE INDEX IF NOT EXISTS idx_application_logs_module ON application_logs(module);

-- Auto-cleanup: delete logs older than 7 days (keeps table small)
-- Run via pg_cron or scheduled task
COMMENT ON TABLE application_logs IS 'Application logs from FastAPI and Celery workers (auto-cleaned after 7 days)';
