-- Rollback for migration 247: restore the status-scoped partial dedup index.
-- (CI auto-detect skips *_rollback.sql, so this never auto-applies.)
DROP INDEX IF EXISTS idx_inbox_dedup;

CREATE UNIQUE INDEX IF NOT EXISTS idx_inbox_dedup
  ON public.agent_inbox (recipient_agent_id, dedup_key)
  WHERE dedup_key IS NOT NULL AND status IN ('unread', 'reading');
