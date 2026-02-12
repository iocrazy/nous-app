-- 042_mediatrack_projects.sql
-- MediaTrack Phase 1: projects and project_files tables
-- Execute in Supabase SQL Editor or via `supabase db push`

-- ============================================================================
-- Part 1: projects - Project containers for file management
-- ============================================================================

CREATE TABLE IF NOT EXISTS projects (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name VARCHAR(200) NOT NULL,
  description TEXT,
  owner_id UUID NOT NULL REFERENCES auth.users(id),
  team_id UUID REFERENCES teams(id) ON DELETE SET NULL,
  project_type VARCHAR(20) NOT NULL DEFAULT 'personal'
    CHECK (project_type IN ('internal', 'external', 'personal')),
  project_group VARCHAR(100),
  is_starred BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================================
-- Part 2: project_files - Files within projects
-- ============================================================================

CREATE TABLE IF NOT EXISTS project_files (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  filename VARCHAR(500) NOT NULL,
  file_type VARCHAR(50),           -- video / document / image
  mime_type VARCHAR(100),
  file_path TEXT,                   -- Relative from DOWNLOAD_PATH
  file_size_bytes BIGINT,
  video_id UUID REFERENCES videos(id) ON DELETE SET NULL,
  duration_seconds INTEGER,
  resolution VARCHAR(20),
  fps DECIMAL(6,2),
  video_codec VARCHAR(50),
  audio_codec VARCHAR(50),
  video_bitrate_kbps INTEGER,
  audio_bitrate_kbps INTEGER,
  audio_channels INTEGER,
  audio_sample_rate INTEGER,
  thumbnail_path TEXT,
  cover_image_path TEXT,
  uploaded_by UUID REFERENCES auth.users(id),
  notes TEXT,
  is_trashed BOOLEAN NOT NULL DEFAULT false,
  trashed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================================
-- Part 3: Indexes
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_projects_owner
  ON projects (owner_id);

CREATE INDEX IF NOT EXISTS idx_projects_team
  ON projects (team_id);

CREATE INDEX IF NOT EXISTS idx_projects_starred
  ON projects (owner_id, is_starred) WHERE is_starred = true;

CREATE INDEX IF NOT EXISTS idx_project_files_project
  ON project_files (project_id);

CREATE INDEX IF NOT EXISTS idx_project_files_type
  ON project_files (project_id, file_type);

CREATE INDEX IF NOT EXISTS idx_project_files_video
  ON project_files (video_id) WHERE video_id IS NOT NULL;

-- ============================================================================
-- Part 4: Auto-update updated_at triggers
-- Reuses the existing update_updated_at_column() function
-- ============================================================================

DROP TRIGGER IF EXISTS update_projects_updated_at ON projects;
CREATE TRIGGER update_projects_updated_at
  BEFORE UPDATE ON projects
  FOR EACH ROW
  EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_project_files_updated_at ON project_files;
CREATE TRIGGER update_project_files_updated_at
  BEFORE UPDATE ON project_files
  FOR EACH ROW
  EXECUTE FUNCTION update_updated_at_column();

-- ============================================================================
-- Part 5: Enable RLS
-- ============================================================================

ALTER TABLE projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE project_files ENABLE ROW LEVEL SECURITY;

-- ============================================================================
-- Part 6: RLS Policies — projects
-- ============================================================================

-- SELECT: owner sees own projects, team members see team projects
DROP POLICY IF EXISTS "Users can read own projects" ON projects;
CREATE POLICY "Users can read own projects"
  ON projects FOR SELECT
  USING (
    owner_id = auth.uid()
    OR team_id IN (SELECT get_user_team_ids(auth.uid()))
  );

-- INSERT: authenticated users can create projects
DROP POLICY IF EXISTS "Users can create projects" ON projects;
CREATE POLICY "Users can create projects"
  ON projects FOR INSERT
  WITH CHECK (
    owner_id = auth.uid()
  );

-- UPDATE: only owner can update
DROP POLICY IF EXISTS "Owners can update projects" ON projects;
CREATE POLICY "Owners can update projects"
  ON projects FOR UPDATE
  USING (owner_id = auth.uid())
  WITH CHECK (owner_id = auth.uid());

-- DELETE: only owner can delete
DROP POLICY IF EXISTS "Owners can delete projects" ON projects;
CREATE POLICY "Owners can delete projects"
  ON projects FOR DELETE
  USING (owner_id = auth.uid());

-- Service role full access
DROP POLICY IF EXISTS "Service role full access on projects" ON projects;
CREATE POLICY "Service role full access on projects"
  ON projects FOR ALL
  USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ============================================================================
-- Part 7: RLS Policies — project_files
-- ============================================================================

-- SELECT: access follows project access
DROP POLICY IF EXISTS "Users can read project files" ON project_files;
CREATE POLICY "Users can read project files"
  ON project_files FOR SELECT
  USING (
    project_id IN (SELECT id FROM projects)
  );

-- INSERT: users who can see the project can add files
DROP POLICY IF EXISTS "Users can create project files" ON project_files;
CREATE POLICY "Users can create project files"
  ON project_files FOR INSERT
  WITH CHECK (
    project_id IN (SELECT id FROM projects)
  );

-- UPDATE: users who can see the project can update files
DROP POLICY IF EXISTS "Users can update project files" ON project_files;
CREATE POLICY "Users can update project files"
  ON project_files FOR UPDATE
  USING (project_id IN (SELECT id FROM projects))
  WITH CHECK (project_id IN (SELECT id FROM projects));

-- DELETE: users who can see the project can delete files
DROP POLICY IF EXISTS "Users can delete project files" ON project_files;
CREATE POLICY "Users can delete project files"
  ON project_files FOR DELETE
  USING (project_id IN (SELECT id FROM projects));

-- Service role full access
DROP POLICY IF EXISTS "Service role full access on project_files" ON project_files;
CREATE POLICY "Service role full access on project_files"
  ON project_files FOR ALL
  USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ============================================================================
-- Part 8: Realtime
-- ============================================================================

ALTER PUBLICATION supabase_realtime ADD TABLE projects, project_files;

-- ============================================================================
-- Done!
-- Verify by running:
--   SELECT count(*) FROM projects;        -- should be 0
--   SELECT count(*) FROM project_files;   -- should be 0
-- ============================================================================
