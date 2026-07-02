-- 328_conversations_rls.sql — RLS guarding the frontend Realtime/PostgREST path.
-- Backend uses db_engine (privileged) + in-app checks. Mirrors 321_chat_rls.sql.

CREATE OR REPLACE FUNCTION public.is_conversation_member(p_user UUID, p_conversation BIGINT)
RETURNS BOOLEAN LANGUAGE SQL SECURITY DEFINER STABLE
SET search_path = public, pg_temp
AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.conversation_members
    WHERE conversation_id = p_conversation AND member_type='user' AND user_id = p_user
  );
$$;
GRANT EXECUTE ON FUNCTION public.is_conversation_member(UUID, BIGINT) TO authenticated;

CREATE OR REPLACE FUNCTION public.conversation_member_joined_at(p_user UUID, p_conversation BIGINT)
RETURNS TIMESTAMPTZ LANGUAGE SQL SECURITY DEFINER STABLE
SET search_path = public, pg_temp
AS $$
  SELECT joined_at FROM public.conversation_members
  WHERE conversation_id = p_conversation AND member_type='user' AND user_id = p_user;
$$;
GRANT EXECUTE ON FUNCTION public.conversation_member_joined_at(UUID, BIGINT) TO authenticated;

ALTER TABLE public.conversations        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.conversation_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.messages             ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.message_attachments  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.message_refs         ENABLE ROW LEVEL SECURITY;

CREATE POLICY conversations_select ON public.conversations
  FOR SELECT USING (
    (type = 'public' AND scope_id IN (SELECT public.get_user_team_ids((SELECT auth.uid()))))
    OR public.is_conversation_member((SELECT auth.uid()), id)
  );

CREATE POLICY conversation_members_select ON public.conversation_members
  FOR SELECT USING (
    public.is_conversation_member((SELECT auth.uid()), conversation_id)
  );

CREATE POLICY messages_select ON public.messages
  FOR SELECT USING (
    public.is_conversation_member((SELECT auth.uid()), conversation_id)
    AND EXISTS (
      SELECT 1 FROM public.conversations c
      WHERE c.id = messages.conversation_id
        AND (
          c.history_mode = 'shared'
          OR messages.created_at >=
               public.conversation_member_joined_at((SELECT auth.uid()), messages.conversation_id)
        )
    )
  );

CREATE POLICY message_attachments_select ON public.message_attachments
  FOR SELECT USING (
    EXISTS (SELECT 1 FROM public.messages m
            WHERE m.id = message_attachments.message_id
              AND public.is_conversation_member((SELECT auth.uid()), m.conversation_id))
  );

CREATE POLICY message_refs_select ON public.message_refs
  FOR SELECT USING (
    EXISTS (SELECT 1 FROM public.messages m
            WHERE m.id = message_refs.message_id
              AND public.is_conversation_member((SELECT auth.uid()), m.conversation_id))
  );

-- Service-role full access (backend privileged path).
CREATE POLICY conversations_service_all ON public.conversations FOR ALL
  USING (current_setting('request.jwt.claims', true)::jsonb->>'role' = 'service_role');
CREATE POLICY conversation_members_service_all ON public.conversation_members FOR ALL
  USING (current_setting('request.jwt.claims', true)::jsonb->>'role' = 'service_role');
CREATE POLICY messages_service_all ON public.messages FOR ALL
  USING (current_setting('request.jwt.claims', true)::jsonb->>'role' = 'service_role');
CREATE POLICY message_attachments_service_all ON public.message_attachments FOR ALL
  USING (current_setting('request.jwt.claims', true)::jsonb->>'role' = 'service_role');
CREATE POLICY message_refs_service_all ON public.message_refs FOR ALL
  USING (current_setting('request.jwt.claims', true)::jsonb->>'role' = 'service_role');
