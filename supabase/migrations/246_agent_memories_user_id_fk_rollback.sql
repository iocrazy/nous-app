-- Rollback for migration 246.
-- (CI auto-detect skips *_rollback.sql, so this never auto-applies.)
ALTER TABLE public.agent_memories
  DROP CONSTRAINT IF EXISTS agent_memories_user_id_fkey;
