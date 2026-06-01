-- Add a jsonb metadata column to parsed_media for rich source-specific data.
-- Soda (汽水音乐) stores album / artists / stats / colors / quality / lyrics here;
-- other platforms may use it later. Additive, nullable, default empty object.
ALTER TABLE parsed_media ADD COLUMN IF NOT EXISTS metadata jsonb DEFAULT '{}'::jsonb;

-- PostgREST caches the table schema; without a reload, REST writes/reads of the
-- new column are silently dropped (PGRST204 / legacy path). Force a reload.
NOTIFY pgrst, 'reload schema';
