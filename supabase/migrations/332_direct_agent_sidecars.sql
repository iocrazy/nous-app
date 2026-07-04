-- 332_direct_agent_sidecars.sql — Phase 2: 1:1 direct_agent fold.
-- (1) conversation_ai_meta: session-scoped fields ai_sessions carries that the
--     shared conversations schema deliberately lacks (parity checklist §3.5/3.10/3.11).
--     One row per direct_agent conversation. Backend-only (service-role RLS).
-- (2) ai_session_memory.session_id: drop the FK to ai_sessions so the SAME
--     BIGINT key can hold a conversations.id for new-store sessions (§3.6).
--     Rows remain keyed one-per-session; integrity is app-enforced post-fold.

CREATE TABLE IF NOT EXISTS public.conversation_ai_meta (
  conversation_id BIGINT PRIMARY KEY
                  REFERENCES public.conversations(id) ON DELETE CASCADE,
  agent_slug      TEXT        NOT NULL,
  agent_id        UUID,
  total_tokens    BIGINT      NOT NULL DEFAULT 0,
  message_count   INTEGER     NOT NULL DEFAULT 0,
  context_type    TEXT,
  context_id      TEXT,
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_conv_ai_meta_slug
  ON public.conversation_ai_meta (agent_slug);

ALTER TABLE public.conversation_ai_meta ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS conversation_ai_meta_service_all ON public.conversation_ai_meta;
CREATE POLICY conversation_ai_meta_service_all ON public.conversation_ai_meta
  FOR ALL USING (
    current_setting('request.jwt.claims', true)::jsonb->>'role' = 'service_role'
  );

ALTER TABLE public.ai_session_memory
  DROP CONSTRAINT IF EXISTS ai_session_memory_session_id_fkey;

COMMENT ON TABLE public.conversation_ai_meta IS
  'AI-session decoration for direct_agent conversations (agent binding, token/'
  'message counters, client grouping hints). Phase 2 sidecar; one row per '
  'direct_agent conversation. Backend-only (service-role RLS).';
