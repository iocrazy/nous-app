-- 069_project_folders.sql
-- Add folder support within projects

CREATE TABLE IF NOT EXISTS project_folders (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  parent_id UUID REFERENCES project_folders(id) ON DELETE CASCADE,
  name VARCHAR(200) NOT NULL DEFAULT 'New Folder',
  created_by UUID REFERENCES auth.users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_project_folders_project ON project_folders(project_id);
CREATE INDEX IF NOT EXISTS idx_project_folders_parent ON project_folders(project_id, parent_id);

-- Add folder_id to project_files
ALTER TABLE project_files ADD COLUMN IF NOT EXISTS folder_id UUID
  REFERENCES project_folders(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_project_files_folder ON project_files(project_id, folder_id);

-- RLS
ALTER TABLE project_folders ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can read project folders" ON project_folders FOR SELECT
  USING (project_id IN (SELECT id FROM projects));
CREATE POLICY "Users can manage project folders" ON project_folders FOR ALL
  USING (project_id IN (SELECT id FROM projects))
  WITH CHECK (project_id IN (SELECT id FROM projects));
CREATE POLICY "Service role full access on project_folders" ON project_folders FOR ALL
  USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- Auto-update updated_at
DROP TRIGGER IF EXISTS update_project_folders_updated_at ON project_folders;
CREATE TRIGGER update_project_folders_updated_at
  BEFORE UPDATE ON project_folders FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
