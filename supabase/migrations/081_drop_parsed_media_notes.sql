-- 081_drop_parsed_media_notes.sql
-- Remove notes and rating from parsed_media; both are now only stored in the resources table.

ALTER TABLE parsed_media DROP COLUMN IF EXISTS notes;
ALTER TABLE parsed_media DROP COLUMN IF EXISTS rating;
