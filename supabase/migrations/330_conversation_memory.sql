-- 330_conversation_memory.sql — Phase 1.5: rolling head-summary sidecar for
-- group agent turns (spec "conversation_memory", built early for group chat;
-- Phase 2 direct_agent reuses it). One row per conversation. Backend-only:
-- read/written via db_engine; RLS is service-role-only so PostgREST never
-- exposes it to anon/authenticated (same posture as generated_media, mig 307).

CREATE TABLE IF NOT EXISTS public.conversation_memory (
  conversation_id     BIGINT PRIMARY KEY
                      REFERENCES public.conversations(id) ON DELETE CASCADE,
  summary_md          TEXT        NOT NULL DEFAULT '',
  last_seq_summarized BIGINT      NOT NULL DEFAULT 0,
  model               TEXT,
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.conversation_memory ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS conversation_memory_service_all ON public.conversation_memory;
CREATE POLICY conversation_memory_service_all ON public.conversation_memory
  FOR ALL USING (
    current_setting('request.jwt.claims', true)::jsonb->>'role' = 'service_role'
  );

COMMENT ON TABLE public.conversation_memory IS
  'Rolling compressed summary of a conversation''s older messages (head). '
  'Agents read summary + recent tail; compaction advances last_seq_summarized. '
  'Backend-only (service-role RLS).';
