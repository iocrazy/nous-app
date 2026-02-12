-- Migration: Enable Realtime for all applicable tables
-- User selected "D) 全部都要" - enable Realtime for all features

-- Step 1: Set REPLICA IDENTITY FULL for tables that need Realtime but don't have it yet
ALTER TABLE teams REPLICA IDENTITY FULL;
ALTER TABLE smart_collections REPLICA IDENTITY FULL;
ALTER TABLE user_profiles REPLICA IDENTITY FULL;
ALTER TABLE team_invites REPLICA IDENTITY FULL;

-- Step 2: Add tables to supabase_realtime publication (skip if already member)
DO $$
DECLARE
    tbl TEXT;
BEGIN
    FOREACH tbl IN ARRAY ARRAY[
        'notifications', 'user_notifications', 'team_members', 'user_logs',
        'teams', 'smart_collections', 'user_profiles', 'team_invites'
    ]
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_publication_tables
            WHERE pubname = 'supabase_realtime' AND tablename = tbl
        ) THEN
            EXECUTE format('ALTER PUBLICATION supabase_realtime ADD TABLE %I', tbl);
        END IF;
    END LOOP;
END $$;

-- Verify the changes
SELECT schemaname, tablename
FROM pg_publication_tables
WHERE pubname = 'supabase_realtime'
ORDER BY tablename;
