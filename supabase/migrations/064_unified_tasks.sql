-- 064_unified_tasks.sql
-- Unified task center: single table for all task types (download/upload/transcode/ai_pipeline)

CREATE TABLE IF NOT EXISTS unified_tasks (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      UUID NOT NULL REFERENCES auth.users(id),
  task_type    VARCHAR(20) NOT NULL,  -- 'download' | 'upload' | 'transcode' | 'ai_pipeline'
  status       VARCHAR(20) NOT NULL DEFAULT 'pending',
                -- pending -> processing -> completed | failed | cancelled

  -- Target association
  resource_id  TEXT,           -- associated resource ID (upload/transcode)
  video_id     TEXT,           -- associated video platform_id (download/ai)

  -- Celery task ID for cancel/revoke
  celery_task_id TEXT,

  -- Progress tracking
  progress     SMALLINT DEFAULT 0 CHECK (progress >= 0 AND progress <= 100),
  speed        BIGINT,              -- bytes/sec (transfer types) or null
  total_bytes  BIGINT,              -- total file size

  -- Display info
  title        TEXT NOT NULL,       -- display name (filename/video title)
  subtitle     TEXT,                -- subtitle (e.g. "480p -> 720p -> 1080p")
  error_msg    TEXT,                -- error message on failure

  -- Type-specific data
  metadata     JSONB DEFAULT '{}',

  -- Timestamps
  created_at   TIMESTAMPTZ DEFAULT now(),
  started_at   TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  updated_at   TIMESTAMPTZ DEFAULT now()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_unified_tasks_user_status ON unified_tasks(user_id, status);
CREATE INDEX IF NOT EXISTS idx_unified_tasks_type ON unified_tasks(task_type);
CREATE INDEX IF NOT EXISTS idx_unified_tasks_user_active ON unified_tasks(user_id, created_at DESC)
  WHERE status IN ('pending', 'processing');

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION update_unified_tasks_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_unified_tasks_updated_at
  BEFORE UPDATE ON unified_tasks
  FOR EACH ROW
  EXECUTE FUNCTION update_unified_tasks_updated_at();

-- Enable Realtime
ALTER PUBLICATION supabase_realtime ADD TABLE unified_tasks;

-- RLS
ALTER TABLE unified_tasks ENABLE ROW LEVEL SECURITY;

CREATE POLICY unified_tasks_select ON unified_tasks
  FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY unified_tasks_insert ON unified_tasks
  FOR INSERT WITH CHECK (auth.uid() = user_id);

CREATE POLICY unified_tasks_update ON unified_tasks
  FOR UPDATE USING (auth.uid() = user_id);

CREATE POLICY unified_tasks_delete ON unified_tasks
  FOR DELETE USING (auth.uid() = user_id);

-- Service role bypass (for backend/Celery workers)
CREATE POLICY unified_tasks_service_all ON unified_tasks
  FOR ALL USING (
    current_setting('request.jwt.claims', true)::jsonb->>'role' = 'service_role'
  );
