-- 051_snowflake_migration.sql
-- Migrate 12 business tables from UUID PKs to BIGINT Snowflake IDs
-- Tables: teams, folders, resources, projects, project_files, file_versions,
--         review_comments, project_tasks, shares, team_invites, point_transactions, orders
-- Also: convert polymorphic scope_id/object_id columns to TEXT
-- Depends on: 050_snowflake_id_infrastructure.sql (generate_snowflake_id function)

-- ============================================================================
-- SECTION 0: Drop ALL RLS policies on affected tables
-- Must happen FIRST so we can drop columns, alter types, and drop functions.
-- ============================================================================

DO $$
DECLARE
  r RECORD;
BEGIN
  -- Drop all policies on tables that will have structural changes
  FOR r IN (
    SELECT schemaname, tablename, policyname
    FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename IN (
        'teams', 'team_members', 'notifications', 'team_invites',
        'projects', 'project_files', 'file_versions', 'review_comments',
        'project_members', 'project_tasks', 'task_assets',
        'project_workflows', 'shares', 'share_views',
        'resources', 'resource_items', 'resource_tags', 'resource_versions',
        'folders', 'team_quotas', 'member_quotas',
        'point_transactions', 'orders', 'team_plans'
      )
  ) LOOP
    EXECUTE format('DROP POLICY IF EXISTS %I ON %I.%I', r.policyname, r.schemaname, r.tablename);
  END LOOP;

  -- Conditionally drop policies on collections (may not exist)
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'collections') THEN
    FOR r IN (SELECT policyname FROM pg_policies WHERE schemaname = 'public' AND tablename = 'collections') LOOP
      EXECUTE format('DROP POLICY IF EXISTS %I ON collections', r.policyname);
    END LOOP;
  END IF;

  -- Conditionally drop policies on video_collections
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'video_collections') THEN
    FOR r IN (SELECT policyname FROM pg_policies WHERE schemaname = 'public' AND tablename = 'video_collections') LOOP
      EXECUTE format('DROP POLICY IF EXISTS %I ON video_collections', r.policyname);
    END LOOP;
  END IF;

  -- Conditionally drop tag policies (if scope_id column exists)
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'tags' AND column_name = 'scope_id'
  ) THEN
    FOR r IN (SELECT policyname FROM pg_policies WHERE schemaname = 'public' AND tablename = 'tags') LOOP
      EXECUTE format('DROP POLICY IF EXISTS %I ON tags', r.policyname);
    END LOOP;
  END IF;
END $$;

-- Drop helper function (all dependent policies are now gone)
DROP FUNCTION IF EXISTS get_user_team_ids(UUID);

-- ============================================================================
-- SECTION 0B: Remove affected tables from supabase_realtime publication
-- Tables with REPLICA IDENTITY DEFAULT (PK-based) will fail UPDATE after PK drop.
-- Remove them now, re-add at the end with REPLICA IDENTITY FULL.
-- ============================================================================

DO $$
DECLARE
  r RECORD;
BEGIN
  FOR r IN (
    SELECT schemaname, tablename
    FROM pg_publication_tables
    WHERE pubname = 'supabase_realtime'
      AND schemaname = 'public'
      AND tablename IN (
        'teams', 'team_members', 'notifications', 'team_invites',
        'projects', 'project_files', 'file_versions', 'review_comments',
        'project_members', 'project_tasks', 'task_assets',
        'project_workflows', 'shares', 'share_views',
        'resources', 'resource_items', 'resource_tags', 'resource_versions',
        'folders', 'team_quotas', 'member_quotas',
        'point_transactions', 'orders', 'team_plans',
        'collections', 'video_collections'
      )
  ) LOOP
    EXECUTE format('ALTER PUBLICATION supabase_realtime DROP TABLE %I.%I', r.schemaname, r.tablename);
  END LOOP;
END $$;

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
-- SECTION 6: Convert FK columns to BIGINT using add/update/drop/rename
-- PostgreSQL does NOT support ALTER TYPE ... USING (SELECT ...) with subqueries.
-- Instead: add _new column, UPDATE FROM join, drop old, rename.
-- ============================================================================

DO $$
DECLARE
  conv JSONB;
  tbl TEXT;
  col TEXT;
  ref_tbl TEXT;
BEGIN
  FOR conv IN SELECT * FROM jsonb_array_elements('[
    {"t":"team_members",      "c":"team_id",         "r":"teams"},
    {"t":"notifications",     "c":"team_id",         "r":"teams"},
    {"t":"team_invites",      "c":"team_id",         "r":"teams"},
    {"t":"projects",          "c":"team_id",         "r":"teams"},
    {"t":"team_quotas",       "c":"team_id",         "r":"teams"},
    {"t":"member_quotas",     "c":"team_id",         "r":"teams"},
    {"t":"point_transactions","c":"team_id",         "r":"teams"},
    {"t":"orders",            "c":"team_id",         "r":"teams"},
    {"t":"project_workflows", "c":"team_id",         "r":"teams"},
    {"t":"team_plans",        "c":"team_id",         "r":"teams"},
    {"t":"project_files",     "c":"project_id",      "r":"projects"},
    {"t":"project_tasks",     "c":"project_id",      "r":"projects"},
    {"t":"project_members",   "c":"project_id",      "r":"projects"},
    {"t":"file_versions",     "c":"file_id",         "r":"project_files"},
    {"t":"review_comments",   "c":"file_id",         "r":"project_files"},
    {"t":"task_assets",       "c":"file_id",         "r":"project_files"},
    {"t":"shares",            "c":"project_file_id", "r":"project_files"},
    {"t":"review_comments",   "c":"version_id",      "r":"file_versions"},
    {"t":"shares",            "c":"version_id",      "r":"file_versions"},
    {"t":"resource_items",    "c":"resource_id",     "r":"resources"},
    {"t":"resource_tags",     "c":"resource_id",     "r":"resources"},
    {"t":"resource_versions", "c":"resource_id",     "r":"resources"},
    {"t":"shares",            "c":"resource_id",     "r":"resources"},
    {"t":"task_assets",       "c":"resource_id",     "r":"resources"},
    {"t":"folders",           "c":"parent_id",       "r":"folders"},
    {"t":"resource_items",    "c":"folder_id",       "r":"folders"},
    {"t":"shares",            "c":"folder_id",       "r":"folders"},
    {"t":"share_views",       "c":"share_id",        "r":"shares"},
    {"t":"review_comments",   "c":"share_id",        "r":"shares"},
    {"t":"task_assets",       "c":"task_id",         "r":"project_tasks"}
  ]'::jsonb)
  LOOP
    tbl := conv->>'t';
    col := conv->>'c';
    ref_tbl := conv->>'r';

    -- Add new BIGINT column
    EXECUTE format('ALTER TABLE %I ADD COLUMN %I BIGINT', tbl, col || '_new');

    -- Populate via JOIN (NULLs stay NULL naturally)
    EXECUTE format(
      'UPDATE %I x SET %I = r.new_id FROM %I r WHERE r.id = x.%I',
      tbl, col || '_new', ref_tbl, col
    );

    -- Drop old UUID column
    EXECUTE format('ALTER TABLE %I DROP COLUMN %I', tbl, col);

    -- Rename new column to original name
    EXECUTE format('ALTER TABLE %I RENAME COLUMN %I TO %I', tbl, col || '_new', col);
  END LOOP;

  -- Handle collections.team_id conditionally
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'collections' AND column_name = 'team_id'
  ) THEN
    EXECUTE 'ALTER TABLE collections ADD COLUMN team_id_new BIGINT';
    EXECUTE 'UPDATE collections x SET team_id_new = r.new_id FROM teams r WHERE r.id = x.team_id';
    EXECUTE 'ALTER TABLE collections DROP COLUMN team_id';
    EXECUTE 'ALTER TABLE collections RENAME COLUMN team_id_new TO team_id';
  END IF;
END $$;

-- ============================================================================
-- SECTION 6B: Restore NOT NULL on FK columns that were originally NOT NULL
-- ============================================================================

ALTER TABLE team_invites      ALTER COLUMN team_id      SET NOT NULL;
ALTER TABLE member_quotas     ALTER COLUMN team_id      SET NOT NULL;
ALTER TABLE point_transactions ALTER COLUMN team_id     SET NOT NULL;
ALTER TABLE orders            ALTER COLUMN team_id      SET NOT NULL;
ALTER TABLE project_workflows ALTER COLUMN team_id      SET NOT NULL;
ALTER TABLE team_plans        ALTER COLUMN team_id      SET NOT NULL;
ALTER TABLE project_files     ALTER COLUMN project_id   SET NOT NULL;
ALTER TABLE project_tasks     ALTER COLUMN project_id   SET NOT NULL;
ALTER TABLE project_members   ALTER COLUMN project_id   SET NOT NULL;
ALTER TABLE file_versions     ALTER COLUMN file_id      SET NOT NULL;
ALTER TABLE review_comments   ALTER COLUMN file_id      SET NOT NULL;
ALTER TABLE resource_items    ALTER COLUMN resource_id  SET NOT NULL;
ALTER TABLE resource_versions ALTER COLUMN resource_id  SET NOT NULL;
ALTER TABLE share_views       ALTER COLUMN share_id     SET NOT NULL;
ALTER TABLE task_assets       ALTER COLUMN task_id      SET NOT NULL;
-- team_members.team_id, resource_tags.resource_id, team_quotas.team_id
-- are part of composite PKs and will get NOT NULL from ADD PRIMARY KEY

-- ============================================================================
-- SECTION 7: Convert polymorphic columns to TEXT
-- (Runs BEFORE Section 8 so we can still use teams.id UUID for lookups)
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
  ADD CONSTRAINT projects_team_id_fkey FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE SET NULL;
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

-- ---- collections.team_id → teams(id) (conditional) ----
DO $$ BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'collections' AND column_name = 'team_id'
  ) THEN
    EXECUTE 'ALTER TABLE collections ADD CONSTRAINT collections_team_id_fkey FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE SET NULL';
  END IF;
END $$;

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
-- SECTION 13: Recreate helper functions
-- ============================================================================

-- get_user_team_ids: now returns SETOF BIGINT (team_members.team_id is BIGINT)
CREATE OR REPLACE FUNCTION get_user_team_ids(p_user_id UUID)
RETURNS SETOF BIGINT
LANGUAGE SQL
SECURITY DEFINER
STABLE
AS $$
  SELECT DISTINCT team_id FROM team_members WHERE user_id = p_user_id;
$$;

GRANT EXECUTE ON FUNCTION get_user_team_ids(UUID) TO authenticated;

-- get_user_team_ids_text: returns SETOF TEXT for scope_id comparisons
CREATE OR REPLACE FUNCTION get_user_team_ids_text(p_user_id UUID)
RETURNS SETOF TEXT
LANGUAGE SQL
SECURITY DEFINER
STABLE
AS $$
  SELECT DISTINCT team_id::text FROM team_members WHERE user_id = p_user_id;
$$;

GRANT EXECUTE ON FUNCTION get_user_team_ids_text(UUID) TO authenticated;

-- ============================================================================
-- SECTION 14: Recreate ALL RLS policies
-- ============================================================================

-- ---- teams (from 009 + 010) ----
CREATE POLICY "Users can view teams they belong to" ON teams
  FOR SELECT USING (
    owner_id = auth.uid()
    OR id IN (SELECT get_user_team_ids(auth.uid()))
  );
CREATE POLICY "Users can create teams" ON teams
  FOR INSERT WITH CHECK (owner_id = auth.uid());
CREATE POLICY "Owners can update their teams" ON teams
  FOR UPDATE USING (owner_id = auth.uid());
CREATE POLICY "Owners can delete their teams" ON teams
  FOR DELETE USING (owner_id = auth.uid());

-- ---- team_members (from 010) ----
CREATE POLICY "Members can view team members" ON team_members
  FOR SELECT USING (
    team_id IN (SELECT get_user_team_ids(auth.uid()))
  );
CREATE POLICY "Users can add themselves or owners can add" ON team_members
  FOR INSERT WITH CHECK (
    user_id = auth.uid()
    OR team_id IN (SELECT id FROM teams WHERE owner_id = auth.uid())
  );
CREATE POLICY "Users can leave or owners can remove" ON team_members
  FOR DELETE USING (
    user_id = auth.uid()
    OR team_id IN (SELECT id FROM teams WHERE owner_id = auth.uid())
  );

-- ---- notifications (from 010) ----
CREATE POLICY "Users can view relevant notifications" ON notifications
  FOR SELECT USING (
    type = 'system'
    OR team_id IN (SELECT get_user_team_ids(auth.uid()))
  );
CREATE POLICY "Team owners can create team notifications" ON notifications
  FOR INSERT WITH CHECK (
    type = 'team' AND
    team_id IN (SELECT id FROM teams WHERE owner_id = auth.uid())
  );

-- ---- team_invites (from 022) ----
CREATE POLICY "Team members can view invites" ON team_invites
  FOR SELECT USING (
    team_id IN (SELECT team_id FROM team_members WHERE user_id = auth.uid())
  );
CREATE POLICY "Team owners can create invites" ON team_invites
  FOR INSERT WITH CHECK (
    team_id IN (SELECT id FROM teams WHERE owner_id = auth.uid())
  );
CREATE POLICY "Team owners can update invites" ON team_invites
  FOR UPDATE USING (
    team_id IN (SELECT id FROM teams WHERE owner_id = auth.uid())
  );
CREATE POLICY "Team owners can delete invites" ON team_invites
  FOR DELETE USING (
    team_id IN (SELECT id FROM teams WHERE owner_id = auth.uid())
  );

-- ---- projects (from 042) ----
CREATE POLICY "Users can read own projects" ON projects
  FOR SELECT USING (
    owner_id = auth.uid()
    OR team_id IN (SELECT get_user_team_ids(auth.uid()))
  );
CREATE POLICY "Users can create projects" ON projects
  FOR INSERT WITH CHECK (owner_id = auth.uid());
CREATE POLICY "Owners can update projects" ON projects
  FOR UPDATE USING (owner_id = auth.uid())
  WITH CHECK (owner_id = auth.uid());
CREATE POLICY "Owners can delete projects" ON projects
  FOR DELETE USING (owner_id = auth.uid());
CREATE POLICY "Service role full access on projects" ON projects
  FOR ALL USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ---- project_files (from 042) ----
CREATE POLICY "Users can read project files" ON project_files
  FOR SELECT USING (project_id IN (SELECT id FROM projects));
CREATE POLICY "Users can create project files" ON project_files
  FOR INSERT WITH CHECK (project_id IN (SELECT id FROM projects));
CREATE POLICY "Users can update project files" ON project_files
  FOR UPDATE
  USING (project_id IN (SELECT id FROM projects))
  WITH CHECK (project_id IN (SELECT id FROM projects));
CREATE POLICY "Users can delete project files" ON project_files
  FOR DELETE USING (project_id IN (SELECT id FROM projects));
CREATE POLICY "Service role full access on project_files" ON project_files
  FOR ALL USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ---- file_versions (from 043) ----
CREATE POLICY "Users can read file versions" ON file_versions
  FOR SELECT USING (
    file_id IN (
      SELECT pf.id FROM project_files pf
      WHERE pf.project_id IN (SELECT id FROM projects)
    )
  );
CREATE POLICY "Users can create file versions" ON file_versions
  FOR INSERT WITH CHECK (
    file_id IN (
      SELECT pf.id FROM project_files pf
      WHERE pf.project_id IN (SELECT id FROM projects)
    )
  );
CREATE POLICY "Users can update file versions" ON file_versions
  FOR UPDATE
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
CREATE POLICY "Users can delete file versions" ON file_versions
  FOR DELETE USING (
    file_id IN (
      SELECT pf.id FROM project_files pf
      WHERE pf.project_id IN (SELECT id FROM projects)
    )
  );
CREATE POLICY "Service role full access on file_versions" ON file_versions
  FOR ALL USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ---- review_comments (from 043) ----
CREATE POLICY "Users can read review comments" ON review_comments
  FOR SELECT USING (
    file_id IN (
      SELECT pf.id FROM project_files pf
      WHERE pf.project_id IN (SELECT id FROM projects)
    )
  );
CREATE POLICY "Users can create review comments" ON review_comments
  FOR INSERT WITH CHECK (
    author_id = auth.uid()
    AND file_id IN (
      SELECT pf.id FROM project_files pf
      WHERE pf.project_id IN (SELECT id FROM projects)
    )
  );
CREATE POLICY "Authors can update own review comments" ON review_comments
  FOR UPDATE USING (author_id = auth.uid())
  WITH CHECK (author_id = auth.uid());
CREATE POLICY "Authors can delete own review comments" ON review_comments
  FOR DELETE USING (author_id = auth.uid());
CREATE POLICY "Service role full access on review_comments" ON review_comments
  FOR ALL USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ---- project_workflows (from 047) ----
CREATE POLICY "Team members can read workflows" ON project_workflows
  FOR SELECT USING (team_id IN (SELECT get_user_team_ids(auth.uid())));
CREATE POLICY "Team members can create workflows" ON project_workflows
  FOR INSERT WITH CHECK (team_id IN (SELECT get_user_team_ids(auth.uid())));
CREATE POLICY "Team members can update workflows" ON project_workflows
  FOR UPDATE
  USING (team_id IN (SELECT get_user_team_ids(auth.uid())))
  WITH CHECK (team_id IN (SELECT get_user_team_ids(auth.uid())));
CREATE POLICY "Team members can delete workflows" ON project_workflows
  FOR DELETE USING (team_id IN (SELECT get_user_team_ids(auth.uid())));
CREATE POLICY "Service role full access on project_workflows" ON project_workflows
  FOR ALL USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ---- project_members (from 047) ----
CREATE POLICY "Users can read project members" ON project_members
  FOR SELECT USING (project_id IN (SELECT id FROM projects));
CREATE POLICY "Users can add project members" ON project_members
  FOR INSERT WITH CHECK (project_id IN (SELECT id FROM projects));
CREATE POLICY "Users can update project members" ON project_members
  FOR UPDATE
  USING (project_id IN (SELECT id FROM projects))
  WITH CHECK (project_id IN (SELECT id FROM projects));
CREATE POLICY "Users can remove project members" ON project_members
  FOR DELETE USING (project_id IN (SELECT id FROM projects));
CREATE POLICY "Service role full access on project_members" ON project_members
  FOR ALL USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ---- project_tasks (from 047) ----
CREATE POLICY "Users can read project tasks" ON project_tasks
  FOR SELECT USING (project_id IN (SELECT id FROM projects));
CREATE POLICY "Users can create project tasks" ON project_tasks
  FOR INSERT WITH CHECK (
    created_by = auth.uid()
    AND project_id IN (SELECT id FROM projects)
  );
CREATE POLICY "Users can update project tasks" ON project_tasks
  FOR UPDATE
  USING (project_id IN (SELECT id FROM projects))
  WITH CHECK (project_id IN (SELECT id FROM projects));
CREATE POLICY "Users can delete project tasks" ON project_tasks
  FOR DELETE USING (project_id IN (SELECT id FROM projects));
CREATE POLICY "Service role full access on project_tasks" ON project_tasks
  FOR ALL USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ---- task_assets (from 047) ----
CREATE POLICY "Users can read task assets" ON task_assets
  FOR SELECT USING (task_id IN (SELECT id FROM project_tasks));
CREATE POLICY "Users can create task assets" ON task_assets
  FOR INSERT WITH CHECK (
    added_by = auth.uid()
    AND task_id IN (SELECT id FROM project_tasks)
  );
CREATE POLICY "Users can delete task assets" ON task_assets
  FOR DELETE USING (task_id IN (SELECT id FROM project_tasks));
CREATE POLICY "Service role full access on task_assets" ON task_assets
  FOR ALL USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ---- shares (from 046) ----
CREATE POLICY "Users can read own shares" ON shares
  FOR SELECT USING (
    shared_by = auth.uid()
    OR status = 'active'
  );
CREATE POLICY "Users can create shares" ON shares
  FOR INSERT WITH CHECK (shared_by = auth.uid());
CREATE POLICY "Users can update own shares" ON shares
  FOR UPDATE USING (shared_by = auth.uid())
  WITH CHECK (shared_by = auth.uid());
CREATE POLICY "Users can delete own shares" ON shares
  FOR DELETE USING (shared_by = auth.uid());
CREATE POLICY "Service role full access on shares" ON shares
  FOR ALL USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ---- share_views (from 046) ----
CREATE POLICY "Users can read share views" ON share_views
  FOR SELECT USING (share_id IN (SELECT id FROM shares));
CREATE POLICY "Anyone can create share views" ON share_views
  FOR INSERT WITH CHECK (true);
CREATE POLICY "Viewers can update own share views" ON share_views
  FOR UPDATE USING (viewer_id = auth.uid())
  WITH CHECK (viewer_id = auth.uid());
CREATE POLICY "Service role full access on share_views" ON share_views
  FOR ALL USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ---- folders (from 044, scope_id now TEXT) ----
CREATE POLICY "Users can read own folders" ON folders
  FOR SELECT USING (
    (scope_type = 'personal' AND scope_id = auth.uid()::text)
    OR (scope_type = 'team' AND scope_id IN (SELECT get_user_team_ids_text(auth.uid())))
  );
CREATE POLICY "Users can create folders" ON folders
  FOR INSERT WITH CHECK (
    created_by = auth.uid()
    AND (
      (scope_type = 'personal' AND scope_id = auth.uid()::text)
      OR (scope_type = 'team' AND scope_id IN (SELECT get_user_team_ids_text(auth.uid())))
    )
  );
CREATE POLICY "Users can update own scope folders" ON folders
  FOR UPDATE
  USING (
    (scope_type = 'personal' AND scope_id = auth.uid()::text)
    OR (scope_type = 'team' AND scope_id IN (SELECT get_user_team_ids_text(auth.uid())))
  )
  WITH CHECK (
    (scope_type = 'personal' AND scope_id = auth.uid()::text)
    OR (scope_type = 'team' AND scope_id IN (SELECT get_user_team_ids_text(auth.uid())))
  );
CREATE POLICY "Users can delete own scope folders" ON folders
  FOR DELETE USING (
    (scope_type = 'personal' AND scope_id = auth.uid()::text)
    OR (scope_type = 'team' AND scope_id IN (SELECT get_user_team_ids_text(auth.uid())))
  );
CREATE POLICY "Service role full access on folders" ON folders
  FOR ALL USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ---- resources (from 044, subquery uses resource_items with TEXT scope_id) ----
CREATE POLICY "Users can read own resources" ON resources
  FOR SELECT USING (
    creator_id = auth.uid()
    OR id IN (
      SELECT resource_id FROM resource_items
      WHERE (scope_type = 'personal' AND scope_id = auth.uid()::text)
         OR (scope_type = 'team' AND scope_id IN (SELECT get_user_team_ids_text(auth.uid())))
    )
  );
CREATE POLICY "Users can create resources" ON resources
  FOR INSERT WITH CHECK (creator_id = auth.uid());
CREATE POLICY "Creators can update resources" ON resources
  FOR UPDATE USING (creator_id = auth.uid())
  WITH CHECK (creator_id = auth.uid());
CREATE POLICY "Creators can delete resources" ON resources
  FOR DELETE USING (creator_id = auth.uid());
CREATE POLICY "Service role full access on resources" ON resources
  FOR ALL USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ---- resource_items (from 044, scope_id now TEXT) ----
CREATE POLICY "Users can read resource items in scope" ON resource_items
  FOR SELECT USING (
    (scope_type = 'personal' AND scope_id = auth.uid()::text)
    OR (scope_type = 'team' AND scope_id IN (SELECT get_user_team_ids_text(auth.uid())))
  );
CREATE POLICY "Users can create resource items" ON resource_items
  FOR INSERT WITH CHECK (
    added_by = auth.uid()
    AND (
      (scope_type = 'personal' AND scope_id = auth.uid()::text)
      OR (scope_type = 'team' AND scope_id IN (SELECT get_user_team_ids_text(auth.uid())))
    )
  );
CREATE POLICY "Users can delete resource items in scope" ON resource_items
  FOR DELETE USING (
    (scope_type = 'personal' AND scope_id = auth.uid()::text)
    OR (scope_type = 'team' AND scope_id IN (SELECT get_user_team_ids_text(auth.uid())))
  );
CREATE POLICY "Service role full access on resource_items" ON resource_items
  FOR ALL USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ---- resource_tags (from 045) ----
CREATE POLICY "Users can read resource tags" ON resource_tags
  FOR SELECT USING (resource_id IN (SELECT id FROM resources));
CREATE POLICY "Users can create resource tags" ON resource_tags
  FOR INSERT WITH CHECK (
    tagged_by = auth.uid()
    AND resource_id IN (SELECT id FROM resources)
  );
CREATE POLICY "Users can delete own resource tags" ON resource_tags
  FOR DELETE USING (tagged_by = auth.uid());
CREATE POLICY "Service role full access on resource_tags" ON resource_tags
  FOR ALL USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ---- resource_versions (from 044) ----
CREATE POLICY "Users can read resource versions" ON resource_versions
  FOR SELECT USING (resource_id IN (SELECT id FROM resources));
CREATE POLICY "Users can create resource versions" ON resource_versions
  FOR INSERT WITH CHECK (
    uploaded_by = auth.uid()
    AND resource_id IN (SELECT id FROM resources)
  );
CREATE POLICY "Service role full access on resource_versions" ON resource_versions
  FOR ALL USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ---- team_quotas (from 041) ----
CREATE POLICY "Team members can read team quota" ON team_quotas
  FOR SELECT USING (team_id IN (SELECT get_user_team_ids(auth.uid())));
CREATE POLICY "Service role full access on team_quotas" ON team_quotas
  FOR ALL USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ---- member_quotas (from 041) ----
CREATE POLICY "Members can read own quota" ON member_quotas
  FOR SELECT USING (user_id = auth.uid());
CREATE POLICY "Owner or admin can read all team member quotas" ON member_quotas
  FOR SELECT USING (
    team_id IN (
      SELECT team_id FROM team_members
      WHERE user_id = auth.uid() AND role IN ('owner', 'admin')
    )
  );
CREATE POLICY "Service role full access on member_quotas" ON member_quotas
  FOR ALL USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ---- point_transactions (from 041) ----
CREATE POLICY "Team members can read team transactions" ON point_transactions
  FOR SELECT USING (team_id IN (SELECT get_user_team_ids(auth.uid())));
CREATE POLICY "Service role full access on point_transactions" ON point_transactions
  FOR ALL USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ---- orders (from 041) ----
CREATE POLICY "Users can read own orders" ON orders
  FOR SELECT USING (user_id = auth.uid());
CREATE POLICY "Owner or admin can read team orders" ON orders
  FOR SELECT USING (
    team_id IN (
      SELECT team_id FROM team_members
      WHERE user_id = auth.uid() AND role IN ('owner', 'admin')
    )
  );
CREATE POLICY "Service role full access on orders" ON orders
  FOR ALL USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ---- team_plans (from 048) ----
CREATE POLICY "Team members can read team plan" ON team_plans
  FOR SELECT USING (team_id IN (SELECT get_user_team_ids(auth.uid())));
CREATE POLICY "Service role full access on team_plans" ON team_plans
  FOR ALL USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ---- collections (conditional, from 009 + 010) ----
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'collections') THEN
    EXECUTE 'CREATE POLICY "Users can view their collections" ON collections
      FOR SELECT USING (
        owner_id = auth.uid()
        OR team_id IN (SELECT get_user_team_ids(auth.uid()))
      )';
    EXECUTE 'CREATE POLICY "Users can create collections" ON collections
      FOR INSERT WITH CHECK (owner_id = auth.uid())';
    EXECUTE 'CREATE POLICY "Users can update collections" ON collections
      FOR UPDATE USING (
        owner_id = auth.uid()
        OR team_id IN (SELECT get_user_team_ids(auth.uid()))
      )';
    EXECUTE 'CREATE POLICY "Owners can delete collections" ON collections
      FOR DELETE USING (owner_id = auth.uid())';
  END IF;
END $$;

-- ---- video_collections (conditional, from 010) ----
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'video_collections') THEN
    EXECUTE 'CREATE POLICY "Collection members can view videos" ON video_collections
      FOR SELECT USING (
        collection_id IN (
          SELECT id FROM collections WHERE
            owner_id = auth.uid() OR
            team_id IN (SELECT get_user_team_ids(auth.uid()))
        )
      )';
    EXECUTE 'CREATE POLICY "Collection members can add videos" ON video_collections
      FOR INSERT WITH CHECK (
        collection_id IN (
          SELECT id FROM collections WHERE
            owner_id = auth.uid() OR
            team_id IN (SELECT get_user_team_ids(auth.uid()))
        )
      )';
    EXECUTE 'CREATE POLICY "Collection members can remove videos" ON video_collections
      FOR DELETE USING (
        collection_id IN (
          SELECT id FROM collections WHERE
            owner_id = auth.uid() OR
            team_id IN (SELECT get_user_team_ids(auth.uid()))
        )
      )';
  END IF;
END $$;

-- ---- tags (conditional, enhanced with scope_id checks) ----
DO $$ BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'tags' AND column_name = 'scope_id'
  ) THEN
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
-- SECTION 15: Re-add tables to supabase_realtime publication
-- Set REPLICA IDENTITY FULL on all affected tables (safest for Supabase Realtime).
-- ============================================================================

DO $$
DECLARE
  tbl TEXT;
BEGIN
  -- Tables that were in the publication before migration
  FOR tbl IN SELECT unnest(ARRAY[
    'teams', 'team_members', 'notifications', 'team_invites',
    'projects', 'project_files', 'file_versions', 'review_comments',
    'project_members', 'project_tasks', 'project_workflows',
    'shares', 'folders', 'resources', 'resource_items'
  ]) LOOP
    EXECUTE format('ALTER TABLE %I REPLICA IDENTITY FULL', tbl);
    EXECUTE format('ALTER PUBLICATION supabase_realtime ADD TABLE %I', tbl);
  END LOOP;

  -- Conditional: collections
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'collections') THEN
    EXECUTE 'ALTER TABLE collections REPLICA IDENTITY FULL';
    EXECUTE 'ALTER PUBLICATION supabase_realtime ADD TABLE collections';
  END IF;

  -- Conditional: video_collections
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'video_collections') THEN
    EXECUTE 'ALTER TABLE video_collections REPLICA IDENTITY FULL';
    EXECUTE 'ALTER PUBLICATION supabase_realtime ADD TABLE video_collections';
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
