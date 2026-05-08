-- 210_ensure_supabase_realtime_publication.sql
--
-- 5/7 sb-mediahub → mediahub-sb-prod migration didn't carry over the
-- contents of the `supabase_realtime` publication. The publication
-- itself existed (created by the supabase realtime container at boot)
-- but had ZERO tables in it, so every realtime subscribe in the
-- frontend (TaskManagerContext / issues / agent runs) silently
-- received no events even though the channel "connected" successfully.
--
-- Symptom that surfaced it: a parse_workflow finished with
-- status=failed (lifecycle trigger from migration 209 wrote the new
-- phase to task_tracking), but the frontend Active Tasks panel kept
-- showing "Parse Initializing… 1 Running" indefinitely. db queries
-- against task_tracking confirmed phase='failed' was already there
-- — the bridge from db → frontend was missing.
--
-- Fix: ensure the four tables we depend on for realtime delivery are
-- members of `supabase_realtime`. dev was already correct with these
-- four; prod had none.
--
-- Idempotency: ALTER PUBLICATION ADD TABLE … fails if the table is
-- already a member, so we DROP TABLE first (noop on missing) then
-- ADD. Wrapped in DO block to also handle the case where the
-- publication itself doesn't exist on a fresh stack — the supabase
-- realtime container creates it but only after first launch.

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_publication WHERE pubname = 'supabase_realtime') THEN
    RAISE NOTICE 'supabase_realtime publication does not exist yet — skipping. Re-run after supabase-realtime container has booted.';
    RETURN;
  END IF;

  -- Drop-then-add so this is safe to re-run regardless of current state.
  ALTER PUBLICATION supabase_realtime DROP TABLE IF EXISTS public.task_tracking;
  ALTER PUBLICATION supabase_realtime DROP TABLE IF EXISTS public.issues;
  ALTER PUBLICATION supabase_realtime DROP TABLE IF EXISTS public.issue_messages;
  ALTER PUBLICATION supabase_realtime DROP TABLE IF EXISTS dbos.workflow_status;

  ALTER PUBLICATION supabase_realtime ADD TABLE public.task_tracking;
  ALTER PUBLICATION supabase_realtime ADD TABLE public.issues;
  ALTER PUBLICATION supabase_realtime ADD TABLE public.issue_messages;

  IF EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'dbos' AND table_name = 'workflow_status'
  ) THEN
    ALTER PUBLICATION supabase_realtime ADD TABLE dbos.workflow_status;
  ELSE
    RAISE NOTICE 'dbos.workflow_status does not exist yet — re-run after DBOS init.';
  END IF;

  RAISE NOTICE 'supabase_realtime publication updated.';
END
$$;
