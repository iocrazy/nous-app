-- ===========================================
-- Migration: Enable Realtime for video_collections
-- ===========================================
-- This enables Realtime updates for the video_collections table
-- so that collection changes sync in real-time across clients.

-- Enable Realtime for video_collections table
ALTER PUBLICATION supabase_realtime ADD TABLE video_collections;

-- Also enable Realtime for collections table if not already enabled
DO $$
BEGIN
    -- Check if collections is already in the publication
    IF NOT EXISTS (
        SELECT 1 FROM pg_publication_tables
        WHERE pubname = 'supabase_realtime'
        AND tablename = 'collections'
    ) THEN
        ALTER PUBLICATION supabase_realtime ADD TABLE collections;
    END IF;
END $$;
