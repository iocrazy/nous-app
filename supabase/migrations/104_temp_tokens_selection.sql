-- 104: Add selection column to temp_tokens for storing tag picker results
ALTER TABLE temp_tokens ADD COLUMN IF NOT EXISTS selection TEXT[] DEFAULT '{}';