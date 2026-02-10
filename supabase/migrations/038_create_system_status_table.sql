-- 038: Create system_status table for Realtime system monitoring
-- Single-row table that stores aggregated system metrics (queue, storage, network, workers, tasks)
-- Updated by Celery Beat every 30s, pushed to admin clients via Supabase Realtime

CREATE TABLE IF NOT EXISTS system_status (
    id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),  -- Force single row
    queue JSONB NOT NULL DEFAULT '{}',
    workers JSONB NOT NULL DEFAULT '[]',
    storage JSONB NOT NULL DEFAULT '{}',
    network JSONB NOT NULL DEFAULT '{}',
    active_tasks JSONB NOT NULL DEFAULT '[]',
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Seed the single row
INSERT INTO system_status (id) VALUES (1) ON CONFLICT DO NOTHING;

-- RLS: all authenticated users can read, only service_role can write
ALTER TABLE system_status ENABLE ROW LEVEL SECURITY;

CREATE POLICY "auth_select" ON system_status
    FOR SELECT USING (auth.role() = 'authenticated');

CREATE POLICY "service_upsert" ON system_status
    FOR ALL USING (auth.role() = 'service_role');

-- Enable Realtime
ALTER TABLE system_status REPLICA IDENTITY FULL;
ALTER PUBLICATION supabase_realtime ADD TABLE system_status;
