-- 063_file_hash_dedup.sql
-- Add file_hash column to resources and resource_versions for duplicate detection

ALTER TABLE resources
  ADD COLUMN IF NOT EXISTS file_hash VARCHAR(64);

ALTER TABLE resource_versions
  ADD COLUMN IF NOT EXISTS file_hash VARCHAR(64);

-- Index for fast duplicate lookups (scoped by creator)
CREATE INDEX IF NOT EXISTS idx_resources_file_hash
  ON resources (file_hash) WHERE file_hash IS NOT NULL AND is_trashed = false;

COMMENT ON COLUMN resources.file_hash IS 'SHA-256 hex digest of the latest version file content';
COMMENT ON COLUMN resource_versions.file_hash IS 'SHA-256 hex digest of this version file content';
