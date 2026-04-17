-- Deployment logs: record each backend release
CREATE TABLE IF NOT EXISTS deployment_logs (
    id BIGINT PRIMARY KEY DEFAULT (EXTRACT(EPOCH FROM NOW()) * 1000000)::BIGINT + (RANDOM() * 1000)::BIGINT,
    service TEXT NOT NULL,                -- 'backend' | 'frontend' | 'extension'
    version TEXT,                         -- e.g. 'v0.7.1' or 'latest'
    commit_sha TEXT,                      -- short SHA of HEAD
    commit_count INT DEFAULT 0,           -- number of commits included
    commits JSONB DEFAULT '[]'::JSONB,    -- [{ sha, subject, author, type }]
    summary TEXT,                         -- human-readable summary
    deployed_at TIMESTAMPTZ DEFAULT NOW(),
    deployed_by TEXT,                     -- who/what triggered (actor)
    status TEXT DEFAULT 'success',        -- success | failed
    metadata JSONB DEFAULT '{}'::JSONB    -- image tag, build id, etc.
);

CREATE INDEX IF NOT EXISTS idx_deployment_logs_service_deployed_at
    ON deployment_logs (service, deployed_at DESC);

-- Admins can read all, no writes from client (only service role via CI)
ALTER TABLE deployment_logs ENABLE ROW LEVEL SECURITY;

CREATE POLICY "deployment_logs_admin_read"
ON deployment_logs FOR SELECT
USING (
    EXISTS (SELECT 1 FROM auth.users WHERE auth.users.id = auth.uid())
);
