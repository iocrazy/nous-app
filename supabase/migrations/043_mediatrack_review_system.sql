-- 043_mediatrack_review_system.sql
-- MediaTrack Phase 1.5: Video review, version management, and review status
-- Execute in Supabase SQL Editor or via `supabase db push`

-- ============================================================================
-- Part 1: ALTER project_files — add review_status and current_version
-- ============================================================================

ALTER TABLE project_files
  ADD COLUMN IF NOT EXISTS review_status VARCHAR(30)
    CHECK (review_status IN ('pending_review', 'in_review', 'feedback_collected', 'approved'));

ALTER TABLE project_files
  ADD COLUMN IF NOT EXISTS current_version INTEGER NOT NULL DEFAULT 1;

-- ============================================================================
-- Part 2: file_versions — version history for project files
-- ============================================================================

CREATE TABLE IF NOT EXISTS file_versions (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  file_id          UUID NOT NULL REFERENCES project_files(id) ON DELETE CASCADE,
  version_number   INTEGER NOT NULL,
  filename         VARCHAR(500),
  file_path        TEXT,                     -- Relative from DOWNLOAD_PATH
  file_size_bytes  BIGINT,
  mime_type        VARCHAR(100),
  duration_seconds INTEGER,
  resolution       VARCHAR(50),
  fps              DECIMAL(6,2),
  video_codec      VARCHAR(50),
  audio_codec      VARCHAR(50),
  video_bitrate_kbps INTEGER,
  audio_bitrate_kbps INTEGER,
  audio_channels   INTEGER,
  audio_sample_rate INTEGER,
  thumbnail_path   TEXT,
  cover_image_path TEXT,
  uploaded_by      UUID REFERENCES auth.users(id),
  notes            TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  UNIQUE (file_id, version_number)
);

-- ============================================================================
-- Part 3: review_comments — timestamped review feedback
-- ============================================================================

CREATE TABLE IF NOT EXISTS review_comments (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  file_id             UUID NOT NULL REFERENCES project_files(id) ON DELETE CASCADE,
  version_id          UUID REFERENCES file_versions(id) ON DELETE SET NULL,
  author_id           UUID NOT NULL REFERENCES auth.users(id),
  content             TEXT NOT NULL,
  timestamp_seconds   DECIMAL(10,3),          -- NULL = general comment
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================================
-- Part 4: Indexes
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_file_versions_file
  ON file_versions (file_id);

CREATE INDEX IF NOT EXISTS idx_file_versions_file_version
  ON file_versions (file_id, version_number);

CREATE INDEX IF NOT EXISTS idx_review_comments_file
  ON review_comments (file_id);

CREATE INDEX IF NOT EXISTS idx_review_comments_timestamp
  ON review_comments (file_id, timestamp_seconds);

CREATE INDEX IF NOT EXISTS idx_project_files_review_status
  ON project_files (project_id, review_status)
  WHERE review_status IS NOT NULL;

-- ============================================================================
-- Part 5: Auto-update updated_at trigger on review_comments
-- Reuses the existing update_updated_at_column() function
-- ============================================================================

DROP TRIGGER IF EXISTS update_review_comments_updated_at ON review_comments;
CREATE TRIGGER update_review_comments_updated_at
  BEFORE UPDATE ON review_comments
  FOR EACH ROW
  EXECUTE FUNCTION update_updated_at_column();

-- ============================================================================
-- Part 6: Enable RLS
-- ============================================================================

ALTER TABLE file_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE review_comments ENABLE ROW LEVEL SECURITY;

-- ============================================================================
-- Part 7: RLS Policies — file_versions
-- Access follows project_files -> projects chain
-- ============================================================================

-- SELECT: users who can see the parent project can read versions
DROP POLICY IF EXISTS "Users can read file versions" ON file_versions;
CREATE POLICY "Users can read file versions"
  ON file_versions FOR SELECT
  USING (
    file_id IN (
      SELECT pf.id FROM project_files pf
      WHERE pf.project_id IN (SELECT id FROM projects)
    )
  );

-- INSERT: users who can see the parent project can add versions
DROP POLICY IF EXISTS "Users can create file versions" ON file_versions;
CREATE POLICY "Users can create file versions"
  ON file_versions FOR INSERT
  WITH CHECK (
    file_id IN (
      SELECT pf.id FROM project_files pf
      WHERE pf.project_id IN (SELECT id FROM projects)
    )
  );

-- UPDATE: users who can see the parent project can update versions
DROP POLICY IF EXISTS "Users can update file versions" ON file_versions;
CREATE POLICY "Users can update file versions"
  ON file_versions FOR UPDATE
  USING (
    file_id IN (
      SELECT pf.id FROM project_files pf
      WHERE pf.project_id IN (SELECT id FROM projects)
    )
  )
  WITH CHECK (
    file_id IN (
      SELECT pf.id FROM project_files pf
      WHERE pf.project_id IN (SELECT id FROM projects)
    )
  );

-- DELETE: users who can see the parent project can delete versions
DROP POLICY IF EXISTS "Users can delete file versions" ON file_versions;
CREATE POLICY "Users can delete file versions"
  ON file_versions FOR DELETE
  USING (
    file_id IN (
      SELECT pf.id FROM project_files pf
      WHERE pf.project_id IN (SELECT id FROM projects)
    )
  );

-- Service role full access
DROP POLICY IF EXISTS "Service role full access on file_versions" ON file_versions;
CREATE POLICY "Service role full access on file_versions"
  ON file_versions FOR ALL
  USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ============================================================================
-- Part 8: RLS Policies — review_comments
-- Access follows project_files -> projects chain
-- Authors can update/delete their own comments
-- ============================================================================

-- SELECT: users who can see the parent project can read comments
DROP POLICY IF EXISTS "Users can read review comments" ON review_comments;
CREATE POLICY "Users can read review comments"
  ON review_comments FOR SELECT
  USING (
    file_id IN (
      SELECT pf.id FROM project_files pf
      WHERE pf.project_id IN (SELECT id FROM projects)
    )
  );

-- INSERT: users who can see the parent project can add comments
DROP POLICY IF EXISTS "Users can create review comments" ON review_comments;
CREATE POLICY "Users can create review comments"
  ON review_comments FOR INSERT
  WITH CHECK (
    author_id = auth.uid()
    AND file_id IN (
      SELECT pf.id FROM project_files pf
      WHERE pf.project_id IN (SELECT id FROM projects)
    )
  );

-- UPDATE: only the comment author can update their own comments
DROP POLICY IF EXISTS "Authors can update own review comments" ON review_comments;
CREATE POLICY "Authors can update own review comments"
  ON review_comments FOR UPDATE
  USING (author_id = auth.uid())
  WITH CHECK (author_id = auth.uid());

-- DELETE: only the comment author can delete their own comments
DROP POLICY IF EXISTS "Authors can delete own review comments" ON review_comments;
CREATE POLICY "Authors can delete own review comments"
  ON review_comments FOR DELETE
  USING (author_id = auth.uid());

-- Service role full access
DROP POLICY IF EXISTS "Service role full access on review_comments" ON review_comments;
CREATE POLICY "Service role full access on review_comments"
  ON review_comments FOR ALL
  USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ============================================================================
-- Part 9: Realtime — add new tables to supabase_realtime publication
-- ============================================================================

ALTER PUBLICATION supabase_realtime ADD TABLE file_versions;
ALTER PUBLICATION supabase_realtime ADD TABLE review_comments;

-- ============================================================================
-- Part 10: Backfill — create V1 records in file_versions for existing files
-- Copies all relevant metadata from project_files into the first version
-- ============================================================================

INSERT INTO file_versions (
  file_id,
  version_number,
  filename,
  file_path,
  file_size_bytes,
  mime_type,
  duration_seconds,
  resolution,
  fps,
  video_codec,
  audio_codec,
  video_bitrate_kbps,
  audio_bitrate_kbps,
  audio_channels,
  audio_sample_rate,
  thumbnail_path,
  cover_image_path,
  uploaded_by,
  notes,
  created_at
)
SELECT
  pf.id,               -- file_id
  1,                    -- version_number (V1)
  pf.filename,
  pf.file_path,
  pf.file_size_bytes,
  pf.mime_type,
  pf.duration_seconds,
  pf.resolution,
  pf.fps,
  pf.video_codec,
  pf.audio_codec,
  pf.video_bitrate_kbps,
  pf.audio_bitrate_kbps,
  pf.audio_channels,
  pf.audio_sample_rate,
  pf.thumbnail_path,
  pf.cover_image_path,
  pf.uploaded_by,
  pf.notes,
  pf.created_at         -- preserve original timestamp
FROM project_files pf
WHERE NOT EXISTS (
  SELECT 1 FROM file_versions fv
  WHERE fv.file_id = pf.id AND fv.version_number = 1
);

-- ============================================================================
-- Done!
-- Verify by running:
--   SELECT count(*) FROM file_versions;      -- should match project_files count
--   SELECT count(*) FROM review_comments;    -- should be 0
--   \d project_files                         -- should show review_status, current_version
-- ============================================================================
