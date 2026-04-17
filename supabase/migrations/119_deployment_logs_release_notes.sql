-- Add release_notes (markdown, written by Claude Code) and published_by
ALTER TABLE deployment_logs ADD COLUMN IF NOT EXISTS release_notes TEXT;
ALTER TABLE deployment_logs ADD COLUMN IF NOT EXISTS published_by TEXT DEFAULT 'auto';
