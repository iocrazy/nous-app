-- Add module-level permission control per team
-- Each team has a JSONB array of enabled module keys.
-- Default: all 7 controllable modules enabled.

ALTER TABLE teams
ADD COLUMN IF NOT EXISTS enabled_modules JSONB
DEFAULT '["parser","resources","library","projects","ai_analysis","dashboard","cleanup"]'::jsonb;

-- Backfill existing teams: all modules enabled
UPDATE teams
SET enabled_modules = '["parser","resources","library","projects","ai_analysis","dashboard","cleanup"]'::jsonb
WHERE enabled_modules IS NULL;
