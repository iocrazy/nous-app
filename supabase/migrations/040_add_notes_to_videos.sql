-- 040: Add notes column to videos table
-- Used by LibraryTable for user annotations on saved videos

ALTER TABLE videos ADD COLUMN IF NOT EXISTS notes TEXT DEFAULT '';
