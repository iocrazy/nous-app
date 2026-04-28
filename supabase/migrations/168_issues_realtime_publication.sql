-- 168: PR-D1 — add issues to supabase_realtime publication.
--
-- Enables Supabase Realtime postgres_changes for the issues table so the
-- frontend Issues page can subscribe and update live as DBOS workflows
-- transition status (backlog → todo → in_progress → in_review → done).
--
-- Per design doc P12: chat token streaming uses SSE separately; only issue
-- table mutations go through Realtime to avoid write-amplification.
--
-- Idempotent: ADD TABLE is no-op if already a member.

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_publication_tables
    WHERE pubname = 'supabase_realtime'
      AND schemaname = 'public'
      AND tablename = 'issues'
  ) THEN
    ALTER PUBLICATION supabase_realtime ADD TABLE public.issues;
  END IF;
END
$$;

-- We deliberately do NOT add issue_sequence to the publication; counter row
-- changes are an internal mechanism, not a user-visible signal.
