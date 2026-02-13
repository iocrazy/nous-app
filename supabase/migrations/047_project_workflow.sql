-- 047_project_workflow.sql
-- Project workflow: workflows, nodes, project_members, tasks, task_assets
-- Extends projects table with visibility and workflow_id

-- ============================================================================
-- Part 1: project_workflows — Workflow templates per team
-- ============================================================================

CREATE TABLE IF NOT EXISTS project_workflows (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  team_id    UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  name       VARCHAR(100) NOT NULL,
  is_default BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================================
-- Part 2: workflow_nodes — Stages within a workflow
-- ============================================================================

CREATE TABLE IF NOT EXISTS workflow_nodes (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workflow_id UUID NOT NULL REFERENCES project_workflows(id) ON DELETE CASCADE,
  name        VARCHAR(100) NOT NULL,
  status_type VARCHAR(20) NOT NULL
    CHECK (status_type IN ('not_started', 'in_progress', 'completed')),
  node_type   VARCHAR(30) NOT NULL DEFAULT 'status'
    CHECK (node_type IN ('status', 'milestone', 'gate')),
  sort_order  INTEGER NOT NULL,
  color       VARCHAR(20),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================================
-- Part 3: project_members — Project-level roles
-- ============================================================================

CREATE TABLE IF NOT EXISTS project_members (
  project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  user_id    UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  role       VARCHAR(20) NOT NULL DEFAULT 'viewer'
    CHECK (role IN ('manager', 'editor', 'viewer', 'external')),
  invited_by UUID REFERENCES auth.users(id),
  joined_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (project_id, user_id)
);

-- ============================================================================
-- Part 4: Extend projects table
-- ============================================================================

ALTER TABLE projects
  ADD COLUMN IF NOT EXISTS visibility VARCHAR(20) NOT NULL DEFAULT 'inherited'
    CHECK (visibility IN ('inherited', 'restricted')),
  ADD COLUMN IF NOT EXISTS workflow_id UUID REFERENCES project_workflows(id);

-- ============================================================================
-- Part 5: project_tasks — Tasks within project workflow
-- ============================================================================

CREATE TABLE IF NOT EXISTS project_tasks (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id        UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  workflow_node_id  UUID REFERENCES workflow_nodes(id) ON DELETE SET NULL,
  title             VARCHAR(500) NOT NULL,
  description       TEXT,
  task_type         VARCHAR(30) NOT NULL DEFAULT 'general'
    CHECK (task_type IN ('general', 'storyboard', 'script', 'filming', 'editing', 'review')),
  assignee_id       UUID REFERENCES auth.users(id),
  due_date          DATE,
  sort_order        INTEGER NOT NULL DEFAULT 0,
  status            VARCHAR(20) NOT NULL DEFAULT 'todo'
    CHECK (status IN ('todo', 'in_progress', 'done', 'cancelled', 'on_hold')),
  created_by        UUID REFERENCES auth.users(id),
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================================
-- Part 6: task_assets — Link tasks to resources/files
-- ============================================================================

CREATE TABLE IF NOT EXISTS task_assets (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  task_id     UUID NOT NULL REFERENCES project_tasks(id) ON DELETE CASCADE,
  resource_id UUID REFERENCES resources(id) ON DELETE CASCADE,
  file_id     UUID REFERENCES project_files(id) ON DELETE CASCADE,
  added_by    UUID REFERENCES auth.users(id),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT task_assets_has_target CHECK (
    resource_id IS NOT NULL OR file_id IS NOT NULL
  )
);

-- ============================================================================
-- Part 7: Indexes
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_project_workflows_team
  ON project_workflows (team_id);

CREATE INDEX IF NOT EXISTS idx_workflow_nodes_workflow
  ON workflow_nodes (workflow_id);

CREATE INDEX IF NOT EXISTS idx_workflow_nodes_sort
  ON workflow_nodes (workflow_id, sort_order);

CREATE INDEX IF NOT EXISTS idx_project_members_user
  ON project_members (user_id);

CREATE INDEX IF NOT EXISTS idx_project_members_project
  ON project_members (project_id);

CREATE INDEX IF NOT EXISTS idx_projects_workflow
  ON projects (workflow_id)
  WHERE workflow_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_projects_visibility
  ON projects (team_id, visibility)
  WHERE visibility = 'restricted';

CREATE INDEX IF NOT EXISTS idx_project_tasks_project
  ON project_tasks (project_id);

CREATE INDEX IF NOT EXISTS idx_project_tasks_assignee
  ON project_tasks (assignee_id)
  WHERE assignee_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_project_tasks_status
  ON project_tasks (project_id, status);

CREATE INDEX IF NOT EXISTS idx_project_tasks_workflow_node
  ON project_tasks (workflow_node_id)
  WHERE workflow_node_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_project_tasks_due
  ON project_tasks (due_date)
  WHERE due_date IS NOT NULL AND status NOT IN ('done', 'cancelled');

CREATE INDEX IF NOT EXISTS idx_task_assets_task
  ON task_assets (task_id);

CREATE INDEX IF NOT EXISTS idx_task_assets_resource
  ON task_assets (resource_id)
  WHERE resource_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_task_assets_file
  ON task_assets (file_id)
  WHERE file_id IS NOT NULL;

-- ============================================================================
-- Part 8: Auto-update updated_at triggers
-- ============================================================================

DROP TRIGGER IF EXISTS update_project_tasks_updated_at ON project_tasks;
CREATE TRIGGER update_project_tasks_updated_at
  BEFORE UPDATE ON project_tasks
  FOR EACH ROW
  EXECUTE FUNCTION update_updated_at_column();

-- ============================================================================
-- Part 9: Enable RLS
-- ============================================================================

ALTER TABLE project_workflows ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow_nodes ENABLE ROW LEVEL SECURITY;
ALTER TABLE project_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE project_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE task_assets ENABLE ROW LEVEL SECURITY;

-- ============================================================================
-- Part 10: RLS Policies — project_workflows
-- ============================================================================

DROP POLICY IF EXISTS "Team members can read workflows" ON project_workflows;
CREATE POLICY "Team members can read workflows"
  ON project_workflows FOR SELECT
  USING (team_id IN (SELECT get_user_team_ids(auth.uid())));

DROP POLICY IF EXISTS "Team members can create workflows" ON project_workflows;
CREATE POLICY "Team members can create workflows"
  ON project_workflows FOR INSERT
  WITH CHECK (team_id IN (SELECT get_user_team_ids(auth.uid())));

DROP POLICY IF EXISTS "Team members can update workflows" ON project_workflows;
CREATE POLICY "Team members can update workflows"
  ON project_workflows FOR UPDATE
  USING (team_id IN (SELECT get_user_team_ids(auth.uid())))
  WITH CHECK (team_id IN (SELECT get_user_team_ids(auth.uid())));

DROP POLICY IF EXISTS "Team members can delete workflows" ON project_workflows;
CREATE POLICY "Team members can delete workflows"
  ON project_workflows FOR DELETE
  USING (team_id IN (SELECT get_user_team_ids(auth.uid())));

DROP POLICY IF EXISTS "Service role full access on project_workflows" ON project_workflows;
CREATE POLICY "Service role full access on project_workflows"
  ON project_workflows FOR ALL
  USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ============================================================================
-- Part 11: RLS Policies — workflow_nodes
-- ============================================================================

DROP POLICY IF EXISTS "Users can read workflow nodes" ON workflow_nodes;
CREATE POLICY "Users can read workflow nodes"
  ON workflow_nodes FOR SELECT
  USING (workflow_id IN (SELECT id FROM project_workflows));

DROP POLICY IF EXISTS "Users can manage workflow nodes" ON workflow_nodes;
CREATE POLICY "Users can manage workflow nodes"
  ON workflow_nodes FOR INSERT
  WITH CHECK (workflow_id IN (SELECT id FROM project_workflows));

DROP POLICY IF EXISTS "Users can update workflow nodes" ON workflow_nodes;
CREATE POLICY "Users can update workflow nodes"
  ON workflow_nodes FOR UPDATE
  USING (workflow_id IN (SELECT id FROM project_workflows))
  WITH CHECK (workflow_id IN (SELECT id FROM project_workflows));

DROP POLICY IF EXISTS "Users can delete workflow nodes" ON workflow_nodes;
CREATE POLICY "Users can delete workflow nodes"
  ON workflow_nodes FOR DELETE
  USING (workflow_id IN (SELECT id FROM project_workflows));

DROP POLICY IF EXISTS "Service role full access on workflow_nodes" ON workflow_nodes;
CREATE POLICY "Service role full access on workflow_nodes"
  ON workflow_nodes FOR ALL
  USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ============================================================================
-- Part 12: RLS Policies — project_members
-- ============================================================================

DROP POLICY IF EXISTS "Users can read project members" ON project_members;
CREATE POLICY "Users can read project members"
  ON project_members FOR SELECT
  USING (project_id IN (SELECT id FROM projects));

DROP POLICY IF EXISTS "Users can add project members" ON project_members;
CREATE POLICY "Users can add project members"
  ON project_members FOR INSERT
  WITH CHECK (project_id IN (SELECT id FROM projects));

DROP POLICY IF EXISTS "Users can update project members" ON project_members;
CREATE POLICY "Users can update project members"
  ON project_members FOR UPDATE
  USING (project_id IN (SELECT id FROM projects))
  WITH CHECK (project_id IN (SELECT id FROM projects));

DROP POLICY IF EXISTS "Users can remove project members" ON project_members;
CREATE POLICY "Users can remove project members"
  ON project_members FOR DELETE
  USING (project_id IN (SELECT id FROM projects));

DROP POLICY IF EXISTS "Service role full access on project_members" ON project_members;
CREATE POLICY "Service role full access on project_members"
  ON project_members FOR ALL
  USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ============================================================================
-- Part 13: RLS Policies — project_tasks
-- ============================================================================

DROP POLICY IF EXISTS "Users can read project tasks" ON project_tasks;
CREATE POLICY "Users can read project tasks"
  ON project_tasks FOR SELECT
  USING (project_id IN (SELECT id FROM projects));

DROP POLICY IF EXISTS "Users can create project tasks" ON project_tasks;
CREATE POLICY "Users can create project tasks"
  ON project_tasks FOR INSERT
  WITH CHECK (
    created_by = auth.uid()
    AND project_id IN (SELECT id FROM projects)
  );

DROP POLICY IF EXISTS "Users can update project tasks" ON project_tasks;
CREATE POLICY "Users can update project tasks"
  ON project_tasks FOR UPDATE
  USING (project_id IN (SELECT id FROM projects))
  WITH CHECK (project_id IN (SELECT id FROM projects));

DROP POLICY IF EXISTS "Users can delete project tasks" ON project_tasks;
CREATE POLICY "Users can delete project tasks"
  ON project_tasks FOR DELETE
  USING (project_id IN (SELECT id FROM projects));

DROP POLICY IF EXISTS "Service role full access on project_tasks" ON project_tasks;
CREATE POLICY "Service role full access on project_tasks"
  ON project_tasks FOR ALL
  USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ============================================================================
-- Part 14: RLS Policies — task_assets
-- ============================================================================

DROP POLICY IF EXISTS "Users can read task assets" ON task_assets;
CREATE POLICY "Users can read task assets"
  ON task_assets FOR SELECT
  USING (task_id IN (SELECT id FROM project_tasks));

DROP POLICY IF EXISTS "Users can create task assets" ON task_assets;
CREATE POLICY "Users can create task assets"
  ON task_assets FOR INSERT
  WITH CHECK (
    added_by = auth.uid()
    AND task_id IN (SELECT id FROM project_tasks)
  );

DROP POLICY IF EXISTS "Users can delete task assets" ON task_assets;
CREATE POLICY "Users can delete task assets"
  ON task_assets FOR DELETE
  USING (task_id IN (SELECT id FROM project_tasks));

DROP POLICY IF EXISTS "Service role full access on task_assets" ON task_assets;
CREATE POLICY "Service role full access on task_assets"
  ON task_assets FOR ALL
  USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ============================================================================
-- Part 15: Realtime
-- ============================================================================

ALTER PUBLICATION supabase_realtime ADD TABLE project_workflows;
ALTER PUBLICATION supabase_realtime ADD TABLE project_tasks;
ALTER PUBLICATION supabase_realtime ADD TABLE project_members;

-- ============================================================================
-- Done!
-- Verify: SELECT count(*) FROM project_workflows;  -- 0
--         SELECT count(*) FROM workflow_nodes;      -- 0
--         SELECT count(*) FROM project_members;     -- 0
--         SELECT count(*) FROM project_tasks;       -- 0
--         SELECT count(*) FROM task_assets;         -- 0
--         \d projects  -- should show visibility, workflow_id
-- ============================================================================
