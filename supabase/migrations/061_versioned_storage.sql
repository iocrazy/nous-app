-- 061_versioned_storage.sql
-- Add HLS fields to resource_versions + backfill existing resources

-- ============================================================================
-- Part 1: Add HLS fields to resource_versions
-- ============================================================================

ALTER TABLE resource_versions
  ADD COLUMN IF NOT EXISTS hls_path TEXT,
  ADD COLUMN IF NOT EXISTS transcode_status VARCHAR(20) DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS transcode_at TIMESTAMPTZ;

COMMENT ON COLUMN resource_versions.hls_path IS 'Relative path to master.m3u8 (null = not transcoded)';
COMMENT ON COLUMN resource_versions.transcode_status IS 'pending / processing / completed / failed';
COMMENT ON COLUMN resource_versions.transcode_at IS 'When transcoding completed';

-- ============================================================================
-- Part 2: Backfill resource_versions for existing resources
-- Every resource should have at least one version record (v1).
-- ============================================================================

INSERT INTO resource_versions (
  resource_id, version_number, filename, file_path,
  file_size_bytes, mime_type, duration_seconds, resolution,
  thumbnail_path, uploaded_by
)
SELECT
  id,
  COALESCE(current_version, 1),
  filename,
  file_path,
  file_size_bytes,
  mime_type,
  duration_seconds,
  resolution,
  thumbnail_path,
  creator_id
FROM resources r
WHERE NOT EXISTS (
  SELECT 1 FROM resource_versions rv WHERE rv.resource_id = r.id
);

-- Ensure current_version is set
UPDATE resources SET current_version = 1 WHERE current_version IS NULL;

-- ============================================================================
-- Part 3: Add RLS policy for version updates/deletes by service role
-- (already exists from 044, but ensure update/delete for users)
-- ============================================================================

DROP POLICY IF EXISTS "Users can update own resource versions" ON resource_versions;
CREATE POLICY "Users can update own resource versions"
  ON resource_versions FOR UPDATE
  USING (
    uploaded_by = auth.uid()
    OR auth.role() = 'service_role'
  );

DROP POLICY IF EXISTS "Users can delete own resource versions" ON resource_versions;
CREATE POLICY "Users can delete own resource versions"
  ON resource_versions FOR DELETE
  USING (
    uploaded_by = auth.uid()
    OR auth.role() = 'service_role'
  );

-- ============================================================================
-- Done!
-- Verify: SELECT count(*) FROM resource_versions;
--         SELECT column_name FROM information_schema.columns
--           WHERE table_name = 'resource_versions' AND column_name IN ('hls_path','transcode_status','transcode_at');
-- ============================================================================
