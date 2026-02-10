-- 038: Create system_status table for Realtime system monitoring
-- Single-row table that stores aggregated system metrics (queue, storage, network, workers, tasks)
-- Updated by Celery Beat every 30s, pushed to admin clients via Supabase Realtime

-- Fixed UUID for the single-row constraint
-- 00000000-0000-0000-0000-000000000001

CREATE TABLE IF NOT EXISTS system_status (
    id UUID PRIMARY KEY DEFAULT '00000000-0000-0000-0000-000000000001'::uuid
        CHECK (id = '00000000-0000-0000-0000-000000000001'::uuid),
    queue JSONB NOT NULL DEFAULT '{}',
    workers JSONB NOT NULL DEFAULT '[]',
    storage JSONB NOT NULL DEFAULT '{}',
    network JSONB NOT NULL DEFAULT '{}',
    active_tasks JSONB NOT NULL DEFAULT '[]',
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO system_status (id) VALUES ('00000000-0000-0000-0000-000000000001') ON CONFLICT DO NOTHING;

-- RLS: all authenticated users can read, only service_role can write
ALTER TABLE system_status ENABLE ROW LEVEL SECURITY;

CREATE POLICY "auth_select" ON system_status
    FOR SELECT USING (auth.role() = 'authenticated');

CREATE POLICY "service_upsert" ON system_status
    FOR ALL USING (auth.role() = 'service_role');

-- Auto-update updated_at on every upsert
CREATE OR REPLACE FUNCTION update_system_status_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_system_status_updated_at
  BEFORE UPDATE ON system_status
  FOR EACH ROW
  EXECUTE FUNCTION update_system_status_updated_at();

-- Enable Realtime
ALTER TABLE system_status REPLICA IDENTITY FULL;
ALTER PUBLICATION supabase_realtime ADD TABLE system_status;
