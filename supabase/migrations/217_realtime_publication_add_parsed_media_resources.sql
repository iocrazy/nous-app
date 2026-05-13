-- 217_realtime_publication_add_parsed_media_resources.sql
--
-- 5/13 incident: My Downloads page didn't refresh after a new download
-- finished — the new card only appeared after a manual page reload.
--
-- Root cause traced via pg_publication_tables: the supabase_realtime
-- publication on prod (mediahub-sb-prod) only contained
--   issue_messages, issues, task_tracking
-- so every Supabase realtime subscription against parsed_media or
-- resources silently received zero events. The channel "subscribed"
-- successfully (Supabase's realtime container only fans out events for
-- tables that are actually published; absent tables fail silently).
--
-- Migration 210 (5/7) restored the publication contents after the
-- prod stack migration but only listed the four tables the frontend
-- was subscribing to *at that time*. parsed_media and resources
-- subscriptions were added to useLibrary later without anyone
-- updating the publication.
--
-- Idempotent via per-table EXISTS check (avoids ALTER PUBLICATION
-- DROP TABLE IF EXISTS which the supabase pg-meta SQL parser does
-- not accept even though PG14+ standard supports it).

DO $$
DECLARE
  v_has_pm  BOOLEAN;
  v_has_res BOOLEAN;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_publication WHERE pubname = 'supabase_realtime') THEN
    RAISE NOTICE 'supabase_realtime publication does not exist yet — skipping.';
    RETURN;
  END IF;

  SELECT EXISTS(
    SELECT 1 FROM pg_publication_tables
    WHERE pubname='supabase_realtime' AND schemaname='public' AND tablename='parsed_media'
  ) INTO v_has_pm;

  SELECT EXISTS(
    SELECT 1 FROM pg_publication_tables
    WHERE pubname='supabase_realtime' AND schemaname='public' AND tablename='resources'
  ) INTO v_has_res;

  IF NOT v_has_pm THEN
    EXECUTE 'ALTER PUBLICATION supabase_realtime ADD TABLE public.parsed_media';
    RAISE NOTICE 'added parsed_media to supabase_realtime';
  END IF;

  IF NOT v_has_res THEN
    EXECUTE 'ALTER PUBLICATION supabase_realtime ADD TABLE public.resources';
    RAISE NOTICE 'added resources to supabase_realtime';
  END IF;
END
$$;
