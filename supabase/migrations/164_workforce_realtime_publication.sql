-- 164: Add workforce tables to the supabase_realtime publication so the
-- Workforce dashboard can subscribe to live INSERT/UPDATE/DELETE
-- events instead of polling.
--
-- Without this, `supabase.channel(...).on('postgres_changes', { table:
-- 'agent_workers', ... })` silently never fires, and the dashboard
-- only refreshes via the safety poll (30s).
--
-- Idempotent: skips tables already in the publication, so re-running
-- the migration on a partial environment doesn't error.

DO $$
DECLARE
    tbl text;
    target_tables text[] := ARRAY[
        'agent_workers',
        'agent_inbox',
        'agent_outbox',
        'agent_state_history',
        'agent_tasks'
    ];
BEGIN
    FOREACH tbl IN ARRAY target_tables LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_publication_tables
            WHERE pubname = 'supabase_realtime'
              AND schemaname = 'public'
              AND tablename = tbl
        ) THEN
            EXECUTE format(
                'ALTER PUBLICATION supabase_realtime ADD TABLE public.%I',
                tbl
            );
        END IF;
    END LOOP;
END $$;
