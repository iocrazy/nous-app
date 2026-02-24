-- 080_parsed_media_rating_notes.sql
-- Add user-editable rating and notes to parsed_media for Downloads panels.

ALTER TABLE parsed_media
  ADD COLUMN IF NOT EXISTS rating SMALLINT CHECK (rating >= 0 AND rating <= 5) DEFAULT 0,
  ADD COLUMN IF NOT EXISTS notes TEXT DEFAULT '';
