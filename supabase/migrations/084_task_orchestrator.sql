-- 084_task_orchestrator.sql
-- Add TaskOrchestrator columns to unified_tasks table

-- Phase: fine-grained lifecycle stage (queued → dedup_check → processing → completed/failed/cancelled)
ALTER TABLE unified_tasks
  ADD COLUMN IF NOT EXISTS phase TEXT DEFAULT 'queued';

-- Dedup key: identifies duplicate tasks across users (e.g. "download:platform_id_123")
ALTER TABLE unified_tasks
  ADD COLUMN IF NOT EXISTS dedup_key TEXT;

-- Subscribers: array of {user_id, resource_id, subscribed_at} for multi-user dedup
ALTER TABLE unified_tasks
  ADD COLUMN IF NOT EXISTS subscribers JSONB DEFAULT '[]';

-- Error code: structured error classification (e.g. "NETWORK_TIMEOUT", "RESOURCE_404")
ALTER TABLE unified_tasks
  ADD COLUMN IF NOT EXISTS error_code TEXT;

-- Index for dedup lookups
CREATE INDEX IF NOT EXISTS idx_unified_tasks_dedup_key
  ON unified_tasks (dedup_key) WHERE dedup_key IS NOT NULL;

-- Index for phase filtering
CREATE INDEX IF NOT EXISTS idx_unified_tasks_phase
  ON unified_tasks (phase) WHERE phase IN ('queued', 'dedup_check', 'processing');
