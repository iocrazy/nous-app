-- 452: put every table the frontend subscribes to back into supabase_realtime.
--
-- Why: 2026-09-04 the production publication was found MISSING agent_runs
-- (mig 145 added it), user_logs, system_status and resource_tags — four tables
-- the frontend has live `postgres_changes` subscriptions on. The Task Center's
-- agent-run cards therefore never received a realtime row: status, todo n/m,
-- retry progress all waited for the next full fetch. Nothing on the stack said
-- so — a subscription to an unpublished table is silently quiet, not an error.
--
-- The publication list had been reset at some point (realtime container
-- re-init / DB move); only mig 210 re-added its own four tables afterwards.
-- This migration is idempotent over the WHOLE frontend subscription set, so
-- re-running it after any future reset repairs everything in one go.
-- `scripts/check-realtime-publication-drift.sh` (config-drift workflow) now
-- red-flags the next reset instead of waiting for someone to notice.
--
-- `ALTER PUBLICATION ... ADD TABLE IF NOT EXISTS` is not valid SQL (see the
-- note on mig 210 in schema_baseline.sql), hence the pg_publication_tables
-- check per table. Tables that do not exist (CI ephemeral schema, future
-- drops) are skipped, not errors.

DO $$
DECLARE
  t text;
  wanted text[] := ARRAY[
    -- keep in sync with `grep -rhoE "table: '[a-z_]+'" frontend/` — the
    -- drift check compares the live publication against that exact grep.
    'agent_runs',
    'conversation_members',
    'inbox_notifications',
    'issue_messages',
    'issues',
    'messages',
    'parsed_media',
    'resource_tags',
    'resources',
    'social_accounts',
    'system_status',
    'task_tracking',
    'user_logs'
  ];
  added int := 0;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_publication WHERE pubname = 'supabase_realtime') THEN
    RAISE NOTICE 'supabase_realtime publication does not exist — skipping (re-run after the realtime container has booted).';
    RETURN;
  END IF;

  FOREACH t IN ARRAY wanted LOOP
    IF to_regclass('public.' || t) IS NULL THEN
      RAISE NOTICE 'public.% does not exist here — skipped', t;
      CONTINUE;
    END IF;
    IF NOT EXISTS (
      SELECT 1 FROM pg_publication_tables
      WHERE pubname = 'supabase_realtime' AND schemaname = 'public' AND tablename = t
    ) THEN
      EXECUTE format('ALTER PUBLICATION supabase_realtime ADD TABLE public.%I', t);
      added := added + 1;
      RAISE NOTICE 'added public.% to supabase_realtime', t;
    END IF;
  END LOOP;

  RAISE NOTICE 'supabase_realtime: % table(s) added', added;
END
$$;
