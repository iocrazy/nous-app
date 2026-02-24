-- 070_project_members.sql
-- Add project members table for member management within projects

CREATE TABLE IF NOT EXISTS project_members (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  role VARCHAR(20) NOT NULL DEFAULT 'viewer'
    CHECK (role IN ('admin', 'editor', 'viewer')),
  invited_by UUID REFERENCES auth.users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(project_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_project_members_project ON project_members(project_id);
CREATE INDEX IF NOT EXISTS idx_project_members_user ON project_members(user_id);

-- RLS
ALTER TABLE project_members ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can read project members" ON project_members FOR SELECT
  USING (project_id IN (SELECT id FROM projects));

CREATE POLICY "Admins can manage members" ON project_members FOR ALL
  USING (
    project_id IN (SELECT id FROM projects WHERE owner_id = auth.uid())
    OR (user_id = auth.uid() AND role = 'admin')
  )
  WITH CHECK (project_id IN (SELECT id FROM projects WHERE owner_id = auth.uid()));

CREATE POLICY "Service role full access on project_members" ON project_members FOR ALL
  USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');
