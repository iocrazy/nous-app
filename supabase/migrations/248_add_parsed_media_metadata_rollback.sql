-- Rollback for 248_add_parsed_media_metadata.sql
ALTER TABLE parsed_media DROP COLUMN IF EXISTS metadata;
NOTIFY pgrst, 'reload schema';
