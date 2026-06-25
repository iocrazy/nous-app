-- 318_chat_rls.sql — Team Chat PHASE-1: RLS guarding the frontend Realtime/PostgREST path.
-- Backend uses db_engine (privileged) + in-app checks; these policies protect direct client access.

-- SECURITY DEFINER helpers (mirror get_user_team_ids style) avoid RLS recursion.
CREATE OR REPLACE FUNCTION public.is_channel_member(p_user UUID, p_channel BIGINT)
RETURNS BOOLEAN LANGUAGE SQL SECURITY DEFINER STABLE AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.channel_members
    WHERE channel_id = p_channel AND user_id = p_user
  );
$$;
GRANT EXECUTE ON FUNCTION public.is_channel_member(UUID, BIGINT) TO authenticated;

CREATE OR REPLACE FUNCTION public.channel_member_joined_at(p_user UUID, p_channel BIGINT)
RETURNS TIMESTAMPTZ LANGUAGE SQL SECURITY DEFINER STABLE AS $$
  SELECT joined_at FROM public.channel_members
  WHERE channel_id = p_channel AND user_id = p_user;
$$;
GRANT EXECUTE ON FUNCTION public.channel_member_joined_at(UUID, BIGINT) TO authenticated;

ALTER TABLE public.channels         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.channel_members  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.channel_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_channels   ENABLE ROW LEVEL SECURITY;

-- channels: public channels visible to the team; group/dm only to members.
-- (CHAT-SEC-02 — non-members cannot even see a private group's existence.)
CREATE POLICY channels_select ON public.channels
  FOR SELECT USING (
    (type = 'public' AND team_id IN (SELECT public.get_user_team_ids((SELECT auth.uid()))))
    OR public.is_channel_member((SELECT auth.uid()), id)
  );

-- channel_members: a member sees the member rows of channels they belong to.
CREATE POLICY channel_members_select ON public.channel_members
  FOR SELECT USING (
    public.is_channel_member((SELECT auth.uid()), channel_id)
  );

-- channel_messages (the critical one): member AND history-mode gate.
CREATE POLICY channel_messages_select ON public.channel_messages
  FOR SELECT USING (
    public.is_channel_member((SELECT auth.uid()), channel_id)
    AND (
      EXISTS (
        SELECT 1 FROM public.channels c
        WHERE c.id = channel_messages.channel_id
          AND (
            c.history_mode = 'shared'
            OR channel_messages.created_at >=
                 public.channel_member_joined_at((SELECT auth.uid()), channel_messages.channel_id)
          )
      )
    )
  );

-- agent_channels: visible to members of the channel.
CREATE POLICY agent_channels_select ON public.agent_channels
  FOR SELECT USING (
    public.is_channel_member((SELECT auth.uid()), channel_id)
  );

-- Service-role full access (backend writes use db_engine which connects privileged;
-- this also covers any service_role PostgREST path). Mirrors migration 064 pattern.
CREATE POLICY channels_service_all ON public.channels FOR ALL
  USING (current_setting('request.jwt.claims', true)::jsonb->>'role' = 'service_role');
CREATE POLICY channel_members_service_all ON public.channel_members FOR ALL
  USING (current_setting('request.jwt.claims', true)::jsonb->>'role' = 'service_role');
CREATE POLICY channel_messages_service_all ON public.channel_messages FOR ALL
  USING (current_setting('request.jwt.claims', true)::jsonb->>'role' = 'service_role');
CREATE POLICY agent_channels_service_all ON public.agent_channels FOR ALL
  USING (current_setting('request.jwt.claims', true)::jsonb->>'role' = 'service_role');
