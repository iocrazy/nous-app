-- Add 'enabled' column to tags table for visibility control
-- When enabled=false, the tag won't appear in frontend GET /api/v1/tags
ALTER TABLE tags ADD COLUMN IF NOT EXISTS enabled boolean NOT NULL DEFAULT true;

CREATE INDEX IF NOT EXISTS idx_tags_enabled ON tags(enabled);

COMMENT ON COLUMN tags.enabled IS 'Whether this tag is visible in the frontend API. Controlled via admin panel.';
