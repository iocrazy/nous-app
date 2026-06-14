-- 296_canvases_realtime_publication.sql
--
-- Phase 6a — Realtime node-state sync (cross-tab, last-writer-wins).
--
-- Adds the `canvases` table to the `supabase_realtime` publication so
-- that Supabase Realtime delivers UPDATE events to the frontend hook
-- `useCanvasRealtime`. The hook calls `applyRemoteUpdate` in the
-- Zustand store, which handles self-echo / stale / dirty-edit guards.
--
-- Why REPLICA IDENTITY FULL:
--   Without it, UPDATE events contain only changed columns in payload.new
--   and NULL for unchanged columns. applyRemoteUpdate needs the full row
--   (base_updated_at + nodes_json + connections_json) to decide whether
--   to rebase or conflict, so FULL identity is required.
--   task_tracking (migration 180) uses the same setting.
--
-- Why no new RLS policy:
--   RLS on canvases already gates SELECT to project members (migration 280,
--   lines 118-126). Supabase Realtime inherits RLS for postgres_changes
--   subscriptions, so no additional policy is needed.
--
-- Idempotency:
--   `ALTER PUBLICATION ... DROP TABLE IF EXISTS` is NOT valid PostgreSQL
--   (ALTER PUBLICATION DROP TABLE has no IF EXISTS clause in any version),
--   so we guard the ADD with a pg_publication_tables membership check
--   instead. REPLICA IDENTITY FULL is idempotent on its own. Wrapped in a
--   DO block that skips gracefully when the publication doesn't exist yet
--   (fresh stack where supabase-realtime container hasn't booted).

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_publication WHERE pubname = 'supabase_realtime') THEN
    RAISE NOTICE 'supabase_realtime publication does not exist yet — skipping. Re-run after supabase-realtime container has booted.';
    RETURN;
  END IF;

  -- REPLICA IDENTITY FULL so UPDATE events carry the complete new row.
  ALTER TABLE public.canvases REPLICA IDENTITY FULL;

  -- Add the table only if it is not already a publication member (ADD TABLE
  -- on an existing member errors; there is no DROP TABLE IF EXISTS to lean on).
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
