-- 297_fix_canvases_realtime_publication.sql
--
-- Fixes 296_canvases_realtime_publication.sql, which failed to apply on prod
-- with `ERROR: syntax error at or near "EXISTS"`. The cause:
--   ALTER PUBLICATION supabase_realtime DROP TABLE IF EXISTS public.canvases;
-- `ALTER PUBLICATION ... DROP TABLE` has NO `IF EXISTS` clause in any
-- PostgreSQL version (210_ensure_supabase_realtime_publication.sql carries the
-- same latent bug and must have been hand-applied). Because 296 is a single DO
-- block, the parse error aborted it atomically — canvases was never added to
-- the publication and REPLICA IDENTITY was never set.
--
-- The run-migration workflow only applies migrations ADDED in the merge commit
-- (git diff --diff-filter=A), so editing 296 in place would not re-run it. This
-- new file re-does the work correctly and idempotently. (296 is also corrected
-- in place for fresh-stack full-replay safety.)

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_publication WHERE pubname = 'supabase_realtime') THEN
    RAISE NOTICE 'supabase_realtime publication does not exist yet — skipping. Re-run after supabase-realtime container has booted.';
    RETURN;
  END IF;

  -- REPLICA IDENTITY FULL so UPDATE events carry the complete new row
  -- (applyRemoteUpdate needs base_updated_at + the full row to rebase/conflict).
  ALTER TABLE public.canvases REPLICA IDENTITY FULL;

  -- Add only if not already a member — ADD TABLE on an existing member errors,
  -- and there is no DROP TABLE IF EXISTS to make this unconditionally re-runnable.
  IF NOT EXISTS (
    SELECT 1 FROM pg_publication_tables
    WHERE pubname = 'supabase_realtime'
      AND schemaname = 'public'
      AND tablename = 'canvases'
  ) THEN
    ALTER PUBLICATION supabase_realtime ADD TABLE public.canvases;
  END IF;

  RAISE NOTICE 'canvases added to supabase_realtime publication with REPLICA IDENTITY FULL.';
END
$$;
