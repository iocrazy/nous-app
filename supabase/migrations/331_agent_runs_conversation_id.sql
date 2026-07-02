-- 331_agent_runs_conversation_id.sql — Phase 1.5: structural run→conversation
-- link (was metadata-json only). Nullable: non-chat triggers stay NULL.
-- Phase 2 (direct_agent) reuses this column per the unified-conversation spec.

ALTER TABLE public.agent_runs
  ADD COLUMN IF NOT EXISTS conversation_id BIGINT;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                 WHERE conname = 'agent_runs_conversation_id_fkey') THEN
    ALTER TABLE public.agent_runs
      ADD CONSTRAINT agent_runs_conversation_id_fkey
      FOREIGN KEY (conversation_id) REFERENCES public.conversations(id)
      ON DELETE SET NULL;
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_agent_runs_conversation
  ON public.agent_runs (conversation_id) WHERE conversation_id IS NOT NULL;

-- agent_runs is written via PostgREST (supabase client) — reload the schema
-- cache so the new column is insertable immediately (known trap).
NOTIFY pgrst, 'reload schema';
