-- 319_chat_realtime.sql — add chat tables to supabase_realtime so the frontend can subscribe.
-- RLS (318) filters events per subscriber. REPLICA IDENTITY FULL so UPDATE payloads carry full rows.
DO $$
BEGIN
  ALTER TABLE public.channel_messages REPLICA IDENTITY FULL;
  IF NOT EXISTS (
    SELECT 1 FROM pg_publication_tables
    WHERE pubname='supabase_realtime' AND schemaname='public' AND tablename='channel_messages'
  ) THEN
    ALTER PUBLICATION supabase_realtime ADD TABLE public.channel_messages;
    RAISE NOTICE 'channel_messages added to supabase_realtime.';
  END IF;

  ALTER TABLE public.channel_members REPLICA IDENTITY FULL;
  IF NOT EXISTS (
    SELECT 1 FROM pg_publication_tables
    WHERE pubname='supabase_realtime' AND schemaname='public' AND tablename='channel_members'
  ) THEN
    ALTER PUBLICATION supabase_realtime ADD TABLE public.channel_members;
    RAISE NOTICE 'channel_members added to supabase_realtime.';
  END IF;
END $$;
