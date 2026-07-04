-- 335_project_file_comments_and_archive.sql
-- Phase A PR-A2 (spec 2026-07-04-projects-module-security-and-redesign):
-- 1) Dedicated comments table for the project-files review surface.
--    Migration 062 dropped the 043-era review_comments columns this surface
--    used (file_id / timestamp_seconds / drawing_data) and rebuilt the table
--    for the RESOURCES review system (resource_id FK). The projects comment
--    endpoints have been 500-ing since. project_files.id / file_versions.id
--    are BIGINT snowflakes (051), so they get their own table instead of
--    overloading review_comments.
-- 2) projects.archived_at — the UI has had Active/Archived filters with no
--    backing column; this makes Archive real.

CREATE TABLE IF NOT EXISTS project_file_comments (
  id                BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
  file_id           BIGINT NOT NULL REFERENCES project_files(id) ON DELETE CASCADE,
  version_id        BIGINT REFERENCES file_versions(id) ON DELETE SET NULL,
  author_id         UUID NOT NULL REFERENCES auth.users(id),
  content           TEXT NOT NULL,
  timestamp_seconds FLOAT,
  drawing_data      JSONB,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_project_file_comments_file
  ON project_file_comments(file_id);
CREATE INDEX IF NOT EXISTS idx_project_file_comments_version
  ON project_file_comments(version_id);

ALTER TABLE project_file_comments ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service role full access on project_file_comments"
  ON project_file_comments FOR ALL
  USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

ALTER TABLE projects ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_projects_archived_at
  ON projects(archived_at) WHERE archived_at IS NOT NULL;

NOTIFY pgrst, 'reload schema';
