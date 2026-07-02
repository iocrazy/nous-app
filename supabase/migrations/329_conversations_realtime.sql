-- 329_conversations_realtime.sql — publish messages + conversation_members to supabase_realtime.
-- RLS (328) filters per subscriber. REPLICA IDENTITY FULL so UPDATE payloads carry full rows.
DO $$
BEGIN
  ALTER TABLE public.messages REPLICA IDENTITY FULL;
  IF NOT EXISTS (SELECT 1 FROM pg_publication_tables
                 WHERE pubname='supabase_realtime' AND schemaname='public' AND tablename='messages') THEN
    ALTER PUBLICATION supabase_realtime ADD TABLE public.messages;
    RAISE NOTICE 'messages added to supabase_realtime.';
  END IF;

  ALTER TABLE public.conversation_members REPLICA IDENTITY FULL;
  IF NOT EXISTS (SELECT 1 FROM pg_publication_tables
                 WHERE pubname='supabase_realtime' AND schemaname='public' AND tablename='conversation_members') THEN
    ALTER PUBLICATION supabase_realtime ADD TABLE public.conversation_members;
    RAISE NOTICE 'conversation_members added to supabase_realtime.';
  END IF;
END $$;
