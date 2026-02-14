-- 051_snowflake_migration.sql
-- Migrate 12 business tables from UUID PKs to BIGINT Snowflake IDs
-- Tables: teams, folders, resources, projects, project_files, file_versions,
--         review_comments, project_tasks, shares, team_invites, point_transactions, orders
-- Also: convert polymorphic scope_id/object_id columns to TEXT
-- Depends on: 050_snowflake_id_infrastructure.sql (generate_snowflake_id function)

-- ============================================================================
-- SECTION 1: Add new_id BIGINT columns and populate with Snowflake IDs
-- ============================================================================

ALTER TABLE teams ADD COLUMN new_id BIGINT;
ALTER TABLE folders ADD COLUMN new_id BIGINT;
ALTER TABLE resources ADD COLUMN new_id BIGINT;
ALTER TABLE projects ADD COLUMN new_id BIGINT;
ALTER TABLE project_files ADD COLUMN new_id BIGINT;
ALTER TABLE file_versions ADD COLUMN new_id BIGINT;
ALTER TABLE review_comments ADD COLUMN new_id BIGINT;
ALTER TABLE project_tasks ADD COLUMN new_id BIGINT;
ALTER TABLE shares ADD COLUMN new_id BIGINT;
ALTER TABLE team_invites ADD COLUMN new_id BIGINT;
ALTER TABLE point_transactions ADD COLUMN new_id BIGINT;
ALTER TABLE orders ADD COLUMN new_id BIGINT;

UPDATE teams SET new_id = generate_snowflake_id();
UPDATE folders SET new_id = generate_snowflake_id();
UPDATE resources SET new_id = generate_snowflake_id();
UPDATE projects SET new_id = generate_snowflake_id();
UPDATE project_files SET new_id = generate_snowflake_id();
UPDATE file_versions SET new_id = generate_snowflake_id();
UPDATE review_comments SET new_id = generate_snowflake_id();
UPDATE project_tasks SET new_id = generate_snowflake_id();
UPDATE shares SET new_id = generate_snowflake_id();
UPDATE team_invites SET new_id = generate_snowflake_id();
UPDATE point_transactions SET new_id = generate_snowflake_id();
UPDATE orders SET new_id = generate_snowflake_id();

-- Verify uniqueness with temp indexes
CREATE UNIQUE INDEX idx_teams_new_id ON teams(new_id);
CREATE UNIQUE INDEX idx_folders_new_id ON folders(new_id);
CREATE UNIQUE INDEX idx_resources_new_id ON resources(new_id);
CREATE UNIQUE INDEX idx_projects_new_id ON projects(new_id);
CREATE UNIQUE INDEX idx_project_files_new_id ON project_files(new_id);
CREATE UNIQUE INDEX idx_file_versions_new_id ON file_versions(new_id);
CREATE UNIQUE INDEX idx_review_comments_new_id ON review_comments(new_id);
CREATE UNIQUE INDEX idx_project_tasks_new_id ON project_tasks(new_id);
CREATE UNIQUE INDEX idx_shares_new_id ON shares(new_id);
CREATE UNIQUE INDEX idx_team_invites_new_id ON team_invites(new_id);
CREATE UNIQUE INDEX idx_point_transactions_new_id ON point_transactions(new_id);
CREATE UNIQUE INDEX idx_orders_new_id ON orders(new_id);

-- ============================================================================
-- SECTION 2: Drop all FK constraints referencing migrating tables (dynamic)
-- ============================================================================

DO $$
DECLARE
  r RECORD;
BEGIN
  FOR r IN (
    SELECT con.conname, con.conrelid::regclass::text AS child_table
    FROM pg_constraint con
    JOIN pg_class cls ON cls.oid = con.confrelid
    WHERE con.contype = 'f'
      AND cls.relname IN (
        'teams', 'folders', 'resources', 'projects', 'project_files',
        'file_versions', 'review_comments', 'project_tasks', 'shares',
        'team_invites', 'point_transactions', 'orders'
      )
      AND cls.relnamespace = 'public'::regnamespace
  ) LOOP
    EXECUTE format('ALTER TABLE %s DROP CONSTRAINT IF EXISTS %I', r.child_table, r.conname);
  END LOOP;
END $$;

-- ============================================================================
-- SECTION 3: Drop primary keys on migrating tables + affected composite PKs
-- ============================================================================

DO $$
DECLARE
  r RECORD;
BEGIN
  FOR r IN (
    SELECT con.conname, con.conrelid::regclass::text AS tbl
    FROM pg_constraint con
    WHERE con.contype = 'p'
      AND con.conrelid::regclass::text IN (
        -- 12 migrating tables
        'teams', 'folders', 'resources', 'projects', 'project_files',
        'file_versions', 'review_comments', 'project_tasks', 'shares',
        'team_invites', 'point_transactions', 'orders',
        -- Composite PKs with changing columns
        'team_members', 'project_members', 'resource_tags', 'team_quotas'
      )
  ) LOOP
    EXECUTE format('ALTER TABLE %s DROP CONSTRAINT IF EXISTS %I', r.tbl, r.conname);
  END LOOP;
END $$;

-- ============================================================================
-- SECTION 4: Drop unique constraints on columns that will change type
-- ============================================================================

DO $$
DECLARE
  r RECORD;
BEGIN
  FOR r IN (
    SELECT con.conname, con.conrelid::regclass::text AS tbl
    FROM pg_constraint con
    WHERE con.contype = 'u'
      AND con.conrelid::regclass::text IN (
        'team_quotas', 'member_quotas', 'file_versions', 'resource_versions',
        'share_views', 'team_plans', 'access_overrides'
      )
  ) LOOP
    EXECUTE format('ALTER TABLE %s DROP CONSTRAINT IF EXISTS %I', r.tbl, r.conname);
  END LOOP;
END $$;

-- ============================================================================
-- SECTION 5: Drop indexes on changing columns
-- ============================================================================

-- team_id indexes
DROP INDEX IF EXISTS idx_team_members_role;
DROP INDEX IF EXISTS idx_notifications_team;
DROP INDEX IF EXISTS idx_team_invites_team;
DROP INDEX IF EXISTS idx_member_quotas_team_user;
DROP INDEX IF EXISTS idx_point_transactions_team_user;
DROP INDEX IF EXISTS idx_orders_team_user_status;
DROP INDEX IF EXISTS idx_projects_team;
DROP INDEX IF EXISTS idx_projects_visibility;
DROP INDEX IF EXISTS idx_project_workflows_team;
DROP INDEX IF EXISTS idx_team_plans_team;

-- project_id indexes
DROP INDEX IF EXISTS idx_project_files_project;
DROP INDEX IF EXISTS idx_project_tasks_project;
DROP INDEX IF EXISTS idx_project_tasks_status;
DROP INDEX IF EXISTS idx_project_members_project;

-- file_id / version_id / share_id indexes
DROP INDEX IF EXISTS idx_file_versions_file;
DROP INDEX IF EXISTS idx_review_comments_file;
DROP INDEX IF EXISTS idx_review_comments_share;

-- resource_id / folder_id indexes
DROP INDEX IF EXISTS idx_resource_items_resource;
DROP INDEX IF EXISTS idx_resource_items_folder;
DROP INDEX IF EXISTS idx_resource_versions_resource;
DROP INDEX IF EXISTS idx_resource_tags_resource;
DROP INDEX IF EXISTS idx_shares_resource;
DROP INDEX IF EXISTS idx_shares_project_file;
DROP INDEX IF EXISTS idx_shares_folder;

-- task / share_views indexes
DROP INDEX IF EXISTS idx_share_views_share;
DROP INDEX IF EXISTS idx_task_assets_task;
DROP INDEX IF EXISTS idx_task_assets_resource;
DROP INDEX IF EXISTS idx_task_assets_file;

-- scope_id / object_id indexes (changing to TEXT)
DROP INDEX IF EXISTS idx_folders_scope;
DROP INDEX IF EXISTS idx_folders_parent;
DROP INDEX IF EXISTS idx_folders_trashed;
DROP INDEX IF EXISTS idx_resource_items_scope;
DROP INDEX IF EXISTS idx_tags_scope;
DROP INDEX IF EXISTS idx_smart_collections_scope;
DROP INDEX IF EXISTS idx_access_overrides_object;

-- Temporary new_id indexes (will be replaced by PKs)
DROP INDEX IF EXISTS idx_teams_new_id;
DROP INDEX IF EXISTS idx_folders_new_id;
DROP INDEX IF EXISTS idx_resources_new_id;
DROP INDEX IF EXISTS idx_projects_new_id;
DROP INDEX IF EXISTS idx_project_files_new_id;
DROP INDEX IF EXISTS idx_file_versions_new_id;
DROP INDEX IF EXISTS idx_review_comments_new_id;
DROP INDEX IF EXISTS idx_project_tasks_new_id;
DROP INDEX IF EXISTS idx_shares_new_id;
DROP INDEX IF EXISTS idx_team_invites_new_id;
DROP INDEX IF EXISTS idx_point_transactions_new_id;
DROP INDEX IF EXISTS idx_orders_new_id;

-- ============================================================================
-- SECTION 6: Convert FK columns to BIGINT using new_id mapping
-- ============================================================================

-- ---- Referencing teams(id) ----

ALTER TABLE team_members
  ALTER COLUMN team_id TYPE BIGINT
  USING (SELECT t.new_id FROM teams t WHERE t.id = team_id);

ALTER TABLE notifications
  ALTER COLUMN team_id TYPE BIGINT
  USING (SELECT t.new_id FROM teams t WHERE t.id = team_id);

ALTER TABLE team_invites
  ALTER COLUMN team_id TYPE BIGINT
  USING (SELECT t.new_id FROM teams t WHERE t.id = team_id);

ALTER TABLE projects
  ALTER COLUMN team_id TYPE BIGINT
  USING (SELECT t.new_id FROM teams t WHERE t.id = team_id);

ALTER TABLE team_quotas
  ALTER COLUMN team_id TYPE BIGINT
  USING (SELECT t.new_id FROM teams t WHERE t.id = team_id);

ALTER TABLE member_quotas
  ALTER COLUMN team_id TYPE BIGINT
  USING (SELECT t.new_id FROM teams t WHERE t.id = team_id);

ALTER TABLE point_transactions
  ALTER COLUMN team_id TYPE BIGINT
  USING (SELECT t.new_id FROM teams t WHERE t.id = team_id);

ALTER TABLE orders
  ALTER COLUMN team_id TYPE BIGINT
  USING (SELECT t.new_id FROM teams t WHERE t.id = team_id);

ALTER TABLE project_workflows
  ALTER COLUMN team_id TYPE BIGINT
  USING (SELECT t.new_id FROM teams t WHERE t.id = team_id);

ALTER TABLE team_plans
  ALTER COLUMN team_id TYPE BIGINT
  USING (SELECT t.new_id FROM teams t WHERE t.id = team_id);

-- Handle collections table if it exists
DO $$ BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'collections' AND column_name = 'team_id'
  ) THEN
    EXECUTE 'ALTER TABLE collections ALTER COLUMN team_id TYPE BIGINT
      USING (SELECT t.new_id FROM teams t WHERE t.id = team_id)';
  END IF;
END $$;

-- ---- Referencing projects(id) ----

ALTER TABLE project_files
  ALTER COLUMN project_id TYPE BIGINT
  USING (SELECT p.new_id FROM projects p WHERE p.id = project_id);

ALTER TABLE project_tasks
  ALTER COLUMN project_id TYPE BIGINT
  USING (SELECT p.new_id FROM projects p WHERE p.id = project_id);

ALTER TABLE project_members
  ALTER COLUMN project_id TYPE BIGINT
  USING (SELECT p.new_id FROM projects p WHERE p.id = project_id);

-- ---- Referencing project_files(id) ----

ALTER TABLE file_versions
  ALTER COLUMN file_id TYPE BIGINT
  USING (SELECT pf.new_id FROM project_files pf WHERE pf.id = file_id);

ALTER TABLE review_comments
  ALTER COLUMN file_id TYPE BIGINT
  USING (SELECT pf.new_id FROM project_files pf WHERE pf.id = file_id);

ALTER TABLE task_assets
  ALTER COLUMN file_id TYPE BIGINT
  USING (SELECT pf.new_id FROM project_files pf WHERE pf.id = file_id);

ALTER TABLE shares
  ALTER COLUMN project_file_id TYPE BIGINT
  USING (SELECT pf.new_id FROM project_files pf WHERE pf.id = project_file_id);

-- ---- Referencing file_versions(id) ----

ALTER TABLE review_comments
  ALTER COLUMN version_id TYPE BIGINT
  USING (SELECT fv.new_id FROM file_versions fv WHERE fv.id = version_id);

ALTER TABLE shares
  ALTER COLUMN version_id TYPE BIGINT
  USING (SELECT fv.new_id FROM file_versions fv WHERE fv.id = version_id);

-- ---- Referencing resources(id) ----

ALTER TABLE resource_items
  ALTER COLUMN resource_id TYPE BIGINT
  USING (SELECT r.new_id FROM resources r WHERE r.id = resource_id);

ALTER TABLE resource_tags
  ALTER COLUMN resource_id TYPE BIGINT
  USING (SELECT r.new_id FROM resources r WHERE r.id = resource_id);

ALTER TABLE resource_versions
  ALTER COLUMN resource_id TYPE BIGINT
  USING (SELECT r.new_id FROM resources r WHERE r.id = resource_id);

ALTER TABLE shares
  ALTER COLUMN resource_id TYPE BIGINT
  USING (SELECT r.new_id FROM resources r WHERE r.id = resource_id);

ALTER TABLE task_assets
  ALTER COLUMN resource_id TYPE BIGINT
  USING (SELECT r.new_id FROM resources r WHERE r.id = resource_id);

-- ---- Referencing folders(id) ----

ALTER TABLE folders
  ALTER COLUMN parent_id TYPE BIGINT
  USING (SELECT f.new_id FROM folders f WHERE f.id = parent_id);

ALTER TABLE resource_items
  ALTER COLUMN folder_id TYPE BIGINT
  USING (SELECT f.new_id FROM folders f WHERE f.id = folder_id);

ALTER TABLE shares
  ALTER COLUMN folder_id TYPE BIGINT
  USING (SELECT f.new_id FROM folders f WHERE f.id = folder_id);

-- ---- Referencing shares(id) ----

ALTER TABLE share_views
  ALTER COLUMN share_id TYPE BIGINT
  USING (SELECT s.new_id FROM shares s WHERE s.id = share_id);

ALTER TABLE review_comments
  ALTER COLUMN share_id TYPE BIGINT
  USING (SELECT s.new_id FROM shares s WHERE s.id = share_id);

-- ---- Referencing project_tasks(id) ----

ALTER TABLE task_assets
  ALTER COLUMN task_id TYPE BIGINT
  USING (SELECT pt.new_id FROM project_tasks pt WHERE pt.id = task_id);

-- ============================================================================
-- SECTION 7: Convert polymorphic columns to TEXT
-- ============================================================================

-- folders.scope_id: UUID → TEXT, then update team scope values
ALTER TABLE folders ALTER COLUMN scope_id TYPE TEXT;
UPDATE folders
SET scope_id = (SELECT t.new_id::text FROM teams t WHERE t.id = scope_id::uuid)
WHERE scope_type = 'team';

-- resource_items.scope_id: UUID → TEXT
ALTER TABLE resource_items ALTER COLUMN scope_id TYPE TEXT;
UPDATE resource_items
SET scope_id = (SELECT t.new_id::text FROM teams t WHERE t.id = scope_id::uuid)
WHERE scope_type = 'team';

-- tags.scope_id (if column exists from 045)
DO $$ BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'tags' AND column_name = 'scope_id'
  ) THEN
    ALTER TABLE tags ALTER COLUMN scope_id TYPE TEXT;
    UPDATE tags
    SET scope_id = (SELECT t.new_id::text FROM teams t WHERE t.id = scope_id::uuid)
    WHERE scope_type = 'team';
  END IF;
END $$;

-- smart_collections.scope_id (if column exists from 045)
DO $$ BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'smart_collections' AND column_name = 'scope_id'
  ) THEN
    ALTER TABLE smart_collections ALTER COLUMN scope_id TYPE TEXT;
    UPDATE smart_collections
    SET scope_id = (SELECT t.new_id::text FROM teams t WHERE t.id = scope_id::uuid)
    WHERE scope_type = 'team';
  END IF;
END $$;

-- access_overrides.object_id: UUID → TEXT, update folder/project references
ALTER TABLE access_overrides ALTER COLUMN object_id TYPE TEXT;
UPDATE access_overrides
SET object_id = (SELECT f.new_id::text FROM folders f WHERE f.id = object_id::uuid)
WHERE object_type = 'folder';
UPDATE access_overrides
SET object_id = (SELECT p.new_id::text FROM projects p WHERE p.id = object_id::uuid)
WHERE object_type = 'project';

-- ============================================================================
-- SECTION 8: Replace PK columns on 12 migrating tables
-- Drop old UUID id, rename new_id → id, set NOT NULL + default
-- ============================================================================

ALTER TABLE teams DROP COLUMN id;
ALTER TABLE teams RENAME COLUMN new_id TO id;
ALTER TABLE teams ALTER COLUMN id SET NOT NULL;
ALTER TABLE teams ALTER COLUMN id SET DEFAULT generate_snowflake_id();

ALTER TABLE folders DROP COLUMN id;
ALTER TABLE folders RENAME COLUMN new_id TO id;
ALTER TABLE folders ALTER COLUMN id SET NOT NULL;
ALTER TABLE folders ALTER COLUMN id SET DEFAULT generate_snowflake_id();

ALTER TABLE resources DROP COLUMN id;
ALTER TABLE resources RENAME COLUMN new_id TO id;
ALTER TABLE resources ALTER COLUMN id SET NOT NULL;
ALTER TABLE resources ALTER COLUMN id SET DEFAULT generate_snowflake_id();

ALTER TABLE projects DROP COLUMN id;
ALTER TABLE projects RENAME COLUMN new_id TO id;
ALTER TABLE projects ALTER COLUMN id SET NOT NULL;
ALTER TABLE projects ALTER COLUMN id SET DEFAULT generate_snowflake_id();

ALTER TABLE project_files DROP COLUMN id;
ALTER TABLE project_files RENAME COLUMN new_id TO id;
ALTER TABLE project_files ALTER COLUMN id SET NOT NULL;
ALTER TABLE project_files ALTER COLUMN id SET DEFAULT generate_snowflake_id();

ALTER TABLE file_versions DROP COLUMN id;
ALTER TABLE file_versions RENAME COLUMN new_id TO id;
ALTER TABLE file_versions ALTER COLUMN id SET NOT NULL;
ALTER TABLE file_versions ALTER COLUMN id SET DEFAULT generate_snowflake_id();

ALTER TABLE review_comments DROP COLUMN id;
ALTER TABLE review_comments RENAME COLUMN new_id TO id;
ALTER TABLE review_comments ALTER COLUMN id SET NOT NULL;
ALTER TABLE review_comments ALTER COLUMN id SET DEFAULT generate_snowflake_id();

ALTER TABLE project_tasks DROP COLUMN id;
ALTER TABLE project_tasks RENAME COLUMN new_id TO id;
ALTER TABLE project_tasks ALTER COLUMN id SET NOT NULL;
ALTER TABLE project_tasks ALTER COLUMN id SET DEFAULT generate_snowflake_id();

ALTER TABLE shares DROP COLUMN id;
ALTER TABLE shares RENAME COLUMN new_id TO id;
ALTER TABLE shares ALTER COLUMN id SET NOT NULL;
ALTER TABLE shares ALTER COLUMN id SET DEFAULT generate_snowflake_id();

ALTER TABLE team_invites DROP COLUMN id;
ALTER TABLE team_invites RENAME COLUMN new_id TO id;
ALTER TABLE team_invites ALTER COLUMN id SET NOT NULL;
ALTER TABLE team_invites ALTER COLUMN id SET DEFAULT generate_snowflake_id();

ALTER TABLE point_transactions DROP COLUMN id;
ALTER TABLE point_transactions RENAME COLUMN new_id TO id;
ALTER TABLE point_transactions ALTER COLUMN id SET NOT NULL;
ALTER TABLE point_transactions ALTER COLUMN id SET DEFAULT generate_snowflake_id();

ALTER TABLE orders DROP COLUMN id;
ALTER TABLE orders RENAME COLUMN new_id TO id;
ALTER TABLE orders ALTER COLUMN id SET NOT NULL;
ALTER TABLE orders ALTER COLUMN id SET DEFAULT generate_snowflake_id();

-- ============================================================================
-- SECTION 9: Recreate primary keys
-- ============================================================================

-- 12 migrating tables
ALTER TABLE teams ADD PRIMARY KEY (id);
ALTER TABLE folders ADD PRIMARY KEY (id);
ALTER TABLE resources ADD PRIMARY KEY (id);
ALTER TABLE projects ADD PRIMARY KEY (id);
ALTER TABLE project_files ADD PRIMARY KEY (id);
ALTER TABLE file_versions ADD PRIMARY KEY (id);
ALTER TABLE review_comments ADD PRIMARY KEY (id);
ALTER TABLE project_tasks ADD PRIMARY KEY (id);
ALTER TABLE shares ADD PRIMARY KEY (id);
ALTER TABLE team_invites ADD PRIMARY KEY (id);
ALTER TABLE point_transactions ADD PRIMARY KEY (id);
ALTER TABLE orders ADD PRIMARY KEY (id);

-- Composite PKs
ALTER TABLE team_members ADD PRIMARY KEY (team_id, user_id);
ALTER TABLE project_members ADD PRIMARY KEY (project_id, user_id);
ALTER TABLE resource_tags ADD PRIMARY KEY (resource_id, tag_id);
ALTER TABLE team_quotas ADD PRIMARY KEY (team_id);

-- ============================================================================
-- SECTION 10: Recreate unique constraints
-- ============================================================================

ALTER TABLE member_quotas
  ADD CONSTRAINT member_quotas_team_id_user_id_key UNIQUE (team_id, user_id);
ALTER TABLE file_versions
  ADD CONSTRAINT file_versions_file_id_version_number_key UNIQUE (file_id, version_number);
ALTER TABLE resource_versions
  ADD CONSTRAINT resource_versions_resource_id_version_number_key UNIQUE (resource_id, version_number);
ALTER TABLE share_views
  ADD CONSTRAINT share_views_share_id_viewer_id_key UNIQUE (share_id, viewer_id);
ALTER TABLE team_plans
  ADD CONSTRAINT team_plans_team_id_key UNIQUE (team_id);
ALTER TABLE access_overrides
  ADD CONSTRAINT access_overrides_object_type_object_id_user_id_key UNIQUE (object_type, object_id, user_id);

-- ============================================================================
-- SECTION 11: Recreate FK constraints
-- ============================================================================

-- ---- team_id → teams(id) ----
ALTER TABLE team_members
  ADD CONSTRAINT team_members_team_id_fkey FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE;
ALTER TABLE notifications
  ADD CONSTRAINT notifications_team_id_fkey FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE;
ALTER TABLE team_invites
  ADD CONSTRAINT team_invites_team_id_fkey FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE;
ALTER TABLE projects
  ADD CONSTRAINT projects_team_id_fkey FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE;
ALTER TABLE team_quotas
  ADD CONSTRAINT team_quotas_team_id_fkey FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE;
ALTER TABLE member_quotas
  ADD CONSTRAINT member_quotas_team_id_fkey FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE;
ALTER TABLE point_transactions
  ADD CONSTRAINT point_transactions_team_id_fkey FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE;
ALTER TABLE orders
  ADD CONSTRAINT orders_team_id_fkey FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE;
ALTER TABLE project_workflows
  ADD CONSTRAINT project_workflows_team_id_fkey FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE;
ALTER TABLE team_plans
  ADD CONSTRAINT team_plans_team_id_fkey FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE;

-- ---- project_id → projects(id) ----
ALTER TABLE project_files
  ADD CONSTRAINT project_files_project_id_fkey FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE;
ALTER TABLE project_tasks
  ADD CONSTRAINT project_tasks_project_id_fkey FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE;
ALTER TABLE project_members
  ADD CONSTRAINT project_members_project_id_fkey FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE;

-- ---- file_id → project_files(id) ----
ALTER TABLE file_versions
  ADD CONSTRAINT file_versions_file_id_fkey FOREIGN KEY (file_id) REFERENCES project_files(id) ON DELETE CASCADE;
ALTER TABLE review_comments
  ADD CONSTRAINT review_comments_file_id_fkey FOREIGN KEY (file_id) REFERENCES project_files(id) ON DELETE CASCADE;
ALTER TABLE task_assets
  ADD CONSTRAINT task_assets_file_id_fkey FOREIGN KEY (file_id) REFERENCES project_files(id) ON DELETE CASCADE;
ALTER TABLE shares
  ADD CONSTRAINT shares_project_file_id_fkey FOREIGN KEY (project_file_id) REFERENCES project_files(id) ON DELETE CASCADE;

-- ---- version_id → file_versions(id) ----
ALTER TABLE review_comments
  ADD CONSTRAINT review_comments_version_id_fkey FOREIGN KEY (version_id) REFERENCES file_versions(id) ON DELETE SET NULL;
ALTER TABLE shares
  ADD CONSTRAINT shares_version_id_fkey FOREIGN KEY (version_id) REFERENCES file_versions(id) ON DELETE SET NULL;

-- ---- resource_id → resources(id) ----
ALTER TABLE resource_items
  ADD CONSTRAINT resource_items_resource_id_fkey FOREIGN KEY (resource_id) REFERENCES resources(id) ON DELETE CASCADE;
ALTER TABLE resource_tags
  ADD CONSTRAINT resource_tags_resource_id_fkey FOREIGN KEY (resource_id) REFERENCES resources(id) ON DELETE CASCADE;
ALTER TABLE resource_versions
  ADD CONSTRAINT resource_versions_resource_id_fkey FOREIGN KEY (resource_id) REFERENCES resources(id) ON DELETE CASCADE;
ALTER TABLE shares
  ADD CONSTRAINT shares_resource_id_fkey FOREIGN KEY (resource_id) REFERENCES resources(id) ON DELETE CASCADE;
ALTER TABLE task_assets
  ADD CONSTRAINT task_assets_resource_id_fkey FOREIGN KEY (resource_id) REFERENCES resources(id) ON DELETE CASCADE;

-- ---- folder_id → folders(id) ----
ALTER TABLE folders
  ADD CONSTRAINT folders_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES folders(id) ON DELETE CASCADE;
ALTER TABLE resource_items
  ADD CONSTRAINT resource_items_folder_id_fkey FOREIGN KEY (folder_id) REFERENCES folders(id) ON DELETE SET NULL;
ALTER TABLE shares
  ADD CONSTRAINT shares_folder_id_fkey FOREIGN KEY (folder_id) REFERENCES folders(id) ON DELETE CASCADE;

-- ---- share_id → shares(id) ----
ALTER TABLE share_views
  ADD CONSTRAINT share_views_share_id_fkey FOREIGN KEY (share_id) REFERENCES shares(id) ON DELETE CASCADE;
ALTER TABLE review_comments
  ADD CONSTRAINT review_comments_share_id_fkey FOREIGN KEY (share_id) REFERENCES shares(id) ON DELETE SET NULL;

-- ---- task_id → project_tasks(id) ----
ALTER TABLE task_assets
  ADD CONSTRAINT task_assets_task_id_fkey FOREIGN KEY (task_id) REFERENCES project_tasks(id) ON DELETE CASCADE;

-- ============================================================================
-- SECTION 12: Recreate indexes
-- ============================================================================

-- team_id indexes
CREATE INDEX idx_team_members_role ON team_members(team_id, role);
CREATE INDEX idx_notifications_team ON notifications(team_id);
CREATE INDEX idx_team_invites_team ON team_invites(team_id);
CREATE INDEX idx_member_quotas_team_user ON member_quotas(team_id, user_id);
CREATE INDEX idx_point_transactions_team_user ON point_transactions(team_id, user_id);
CREATE INDEX idx_orders_team_user_status ON orders(team_id, user_id, payment_status);
CREATE INDEX idx_projects_team ON projects(team_id);
CREATE INDEX idx_projects_visibility ON projects(team_id, visibility) WHERE visibility = 'restricted';
CREATE INDEX idx_project_workflows_team ON project_workflows(team_id);
CREATE INDEX idx_team_plans_team ON team_plans(team_id);

-- project_id indexes
CREATE INDEX idx_project_files_project ON project_files(project_id);
CREATE INDEX idx_project_tasks_project ON project_tasks(project_id);
CREATE INDEX idx_project_tasks_status ON project_tasks(project_id, status);
CREATE INDEX idx_project_members_project ON project_members(project_id);

-- file_id / version_id / share_id indexes
CREATE INDEX idx_file_versions_file ON file_versions(file_id);
CREATE INDEX idx_review_comments_file ON review_comments(file_id);
CREATE INDEX idx_review_comments_share ON review_comments(share_id) WHERE share_id IS NOT NULL;

-- resource_id / folder_id indexes
CREATE INDEX idx_resource_items_resource ON resource_items(resource_id);
CREATE INDEX idx_resource_items_folder ON resource_items(folder_id) WHERE folder_id IS NOT NULL;
CREATE INDEX idx_resource_versions_resource ON resource_versions(resource_id);
CREATE INDEX idx_resource_tags_resource ON resource_tags(resource_id);
CREATE INDEX idx_shares_resource ON shares(resource_id) WHERE resource_id IS NOT NULL;
CREATE INDEX idx_shares_project_file ON shares(project_file_id) WHERE project_file_id IS NOT NULL;
CREATE INDEX idx_shares_folder ON shares(folder_id) WHERE folder_id IS NOT NULL;

-- task / share_views indexes
CREATE INDEX idx_share_views_share ON share_views(share_id);
CREATE INDEX idx_task_assets_task ON task_assets(task_id);
CREATE INDEX idx_task_assets_resource ON task_assets(resource_id) WHERE resource_id IS NOT NULL;
CREATE INDEX idx_task_assets_file ON task_assets(file_id) WHERE file_id IS NOT NULL;

-- scope_id / object_id indexes (now TEXT type)
CREATE INDEX idx_folders_scope ON folders(scope_type, scope_id);
CREATE INDEX idx_folders_parent ON folders(parent_id);
CREATE INDEX idx_folders_trashed ON folders(scope_type, scope_id, is_trashed) WHERE is_trashed = true;
CREATE INDEX idx_resource_items_scope ON resource_items(scope_type, scope_id);
CREATE INDEX idx_access_overrides_object ON access_overrides(object_type, object_id);

DO $$ BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'tags' AND column_name = 'scope_id'
  ) THEN
    CREATE INDEX idx_tags_scope ON tags(scope_type, scope_id);
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'smart_collections' AND column_name = 'scope_id'
  ) THEN
    CREATE INDEX idx_smart_collections_scope ON smart_collections(scope_type, scope_id);
  END IF;
END $$;

-- ============================================================================
-- SECTION 13: Update functions
-- ============================================================================

-- get_user_team_ids: now returns SETOF BIGINT (team_members.team_id is BIGINT)
DROP FUNCTION IF EXISTS get_user_team_ids(UUID);

CREATE OR REPLACE FUNCTION get_user_team_ids(p_user_id UUID)
RETURNS SETOF BIGINT
LANGUAGE SQL
SECURITY DEFINER
STABLE
AS $$
  SELECT DISTINCT team_id FROM team_members WHERE user_id = p_user_id;
$$;

-- get_user_team_ids_text: returns SETOF TEXT for scope_id comparisons
CREATE OR REPLACE FUNCTION get_user_team_ids_text(p_user_id UUID)
RETURNS SETOF TEXT
LANGUAGE SQL
SECURITY DEFINER
STABLE
AS $$
  SELECT DISTINCT team_id::text FROM team_members WHERE user_id = p_user_id;
$$;

-- ============================================================================
-- SECTION 14: Update RLS policies for scope_id columns (TEXT type)
-- ============================================================================

-- ---- folders: drop all, recreate with TEXT comparisons ----
DO $$
DECLARE r RECORD;
BEGIN
  FOR r IN (
    SELECT policyname FROM pg_policies
    WHERE tablename = 'folders' AND schemaname = 'public'
  ) LOOP
    EXECUTE format('DROP POLICY IF EXISTS %I ON folders', r.policyname);
  END LOOP;
END $$;

CREATE POLICY "Users can read own folders"
  ON folders FOR SELECT
  USING (
    (scope_type = 'personal' AND scope_id = auth.uid()::text)
    OR (scope_type = 'team' AND scope_id IN (SELECT get_user_team_ids_text(auth.uid())))
  );

CREATE POLICY "Users can create folders"
  ON folders FOR INSERT
  WITH CHECK (
    created_by = auth.uid()
    AND (
      (scope_type = 'personal' AND scope_id = auth.uid()::text)
      OR (scope_type = 'team' AND scope_id IN (SELECT get_user_team_ids_text(auth.uid())))
    )
  );

CREATE POLICY "Users can update own scope folders"
  ON folders FOR UPDATE
  USING (
    (scope_type = 'personal' AND scope_id = auth.uid()::text)
    OR (scope_type = 'team' AND scope_id IN (SELECT get_user_team_ids_text(auth.uid())))
  )
  WITH CHECK (
    (scope_type = 'personal' AND scope_id = auth.uid()::text)
    OR (scope_type = 'team' AND scope_id IN (SELECT get_user_team_ids_text(auth.uid())))
  );

CREATE POLICY "Users can delete own scope folders"
  ON folders FOR DELETE
  USING (
    (scope_type = 'personal' AND scope_id = auth.uid()::text)
    OR (scope_type = 'team' AND scope_id IN (SELECT get_user_team_ids_text(auth.uid())))
  );

CREATE POLICY "Service role full access on folders"
  ON folders FOR ALL
  USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ---- resource_items: drop all, recreate with TEXT comparisons ----
DO $$
DECLARE r RECORD;
BEGIN
  FOR r IN (
    SELECT policyname FROM pg_policies
    WHERE tablename = 'resource_items' AND schemaname = 'public'
  ) LOOP
    EXECUTE format('DROP POLICY IF EXISTS %I ON resource_items', r.policyname);
  END LOOP;
END $$;

CREATE POLICY "Users can read resource items in scope"
  ON resource_items FOR SELECT
  USING (
    (scope_type = 'personal' AND scope_id = auth.uid()::text)
    OR (scope_type = 'team' AND scope_id IN (SELECT get_user_team_ids_text(auth.uid())))
  );

CREATE POLICY "Users can create resource items"
  ON resource_items FOR INSERT
  WITH CHECK (
    added_by = auth.uid()
    AND (
      (scope_type = 'personal' AND scope_id = auth.uid()::text)
      OR (scope_type = 'team' AND scope_id IN (SELECT get_user_team_ids_text(auth.uid())))
    )
  );

CREATE POLICY "Users can delete resource items in scope"
  ON resource_items FOR DELETE
  USING (
    (scope_type = 'personal' AND scope_id = auth.uid()::text)
    OR (scope_type = 'team' AND scope_id IN (SELECT get_user_team_ids_text(auth.uid())))
  );

CREATE POLICY "Service role full access on resource_items"
  ON resource_items FOR ALL
  USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ---- tags: update if scope_id column exists ----
DO $$
DECLARE r RECORD;
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'tags' AND column_name = 'scope_id'
  ) THEN
    -- Drop all existing tag policies
    FOR r IN (
      SELECT policyname FROM pg_policies
      WHERE tablename = 'tags' AND schemaname = 'public'
    ) LOOP
      EXECUTE format('DROP POLICY IF EXISTS %I ON tags', r.policyname);
    END LOOP;

    -- Recreate with TEXT comparisons
    EXECUTE 'CREATE POLICY "System tags visible to all" ON tags
      FOR SELECT USING (type = ''system'' OR type = ''time'')';

    EXECUTE 'CREATE POLICY "User tags visible to owner" ON tags
      FOR SELECT USING (user_id = auth.uid())';

    EXECUTE 'CREATE POLICY "Team tags visible to members" ON tags
      FOR SELECT USING (
        scope_type = ''team'' AND scope_id IN (SELECT get_user_team_ids_text(auth.uid()))
      )';

    EXECUTE 'CREATE POLICY "Users can create own tags" ON tags
      FOR INSERT WITH CHECK (user_id = auth.uid() AND type = ''user'')';

    EXECUTE 'CREATE POLICY "Users can update own tags" ON tags
      FOR UPDATE USING (user_id = auth.uid() AND type = ''user'')';

    EXECUTE 'CREATE POLICY "Users can delete own tags" ON tags
      FOR DELETE USING (user_id = auth.uid() AND type = ''user'')';

    EXECUTE 'CREATE POLICY "Service role full access on tags" ON tags
      FOR ALL USING (auth.role() = ''service_role'')
      WITH CHECK (auth.role() = ''service_role'')';
  END IF;
END $$;

-- ============================================================================
-- VERIFICATION QUERIES (run manually after migration)
-- ============================================================================
-- SELECT generate_snowflake_id();  -- should return BIGINT
-- SELECT id, pg_typeof(id) FROM teams LIMIT 1;  -- should be bigint
-- SELECT id, pg_typeof(id) FROM projects LIMIT 1;  -- should be bigint
-- SELECT scope_id, pg_typeof(scope_id) FROM folders LIMIT 1;  -- should be text
-- SELECT get_user_team_ids('some-user-uuid');  -- should return BIGINT set
-- SELECT get_user_team_ids_text('some-user-uuid');  -- should return TEXT set
