-- 224_issues_ai_session_id.sql
-- Spec-1a: back an issue's agent conversation with an ai_session so issue
-- execution runs on the full chat runtime (memory / compaction / sub-agents).
-- Nullable: only set once an agent conversation starts for the issue.
ALTER TABLE public.issues
  ADD COLUMN IF NOT EXISTS ai_session_id UUID
  REFERENCES public.ai_sessions(id) ON DELETE SET NULL;
