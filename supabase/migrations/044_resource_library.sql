-- 044_resource_library.sql
-- Resource library: folders, resources, resource_items, resource_versions
-- All folder operations are database-only (zero physical IO)

-- ============================================================================
-- Part 1: folders — Virtual folder tree
-- ============================================================================

CREATE TABLE IF NOT EXISTS folders (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name        VARCHAR(200) NOT NULL,
  parent_id   UUID REFERENCES folders(id) ON DELETE CASCADE,
  scope_type  VARCHAR(20) NOT NULL CHECK (scope_type IN ('personal', 'team')),
  scope_id    UUID NOT NULL,             -- user_id or team_id
  created_by  UUID NOT NULL REFERENCES auth.users(id),
  sort_order  INTEGER NOT NULL DEFAULT 0,
  is_system   BOOLEAN NOT NULL DEFAULT false,
  icon        VARCHAR(50),
  color       VARCHAR(20),
  visibility  VARCHAR(20) NOT NULL DEFAULT 'inherited'
    CHECK (visibility IN ('inherited', 'restricted')),
  is_trashed  BOOLEAN NOT NULL DEFAULT false,
  trashed_at  TIMESTAMPTZ,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================================
-- Part 2: resources — Core resource records
-- ============================================================================

CREATE TABLE IF NOT EXISTS resources (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  creator_id       UUID NOT NULL REFERENCES auth.users(id),
  source_type      VARCHAR(20) NOT NULL CHECK (source_type IN ('web', 'upload')),
  video_id         UUID REFERENCES videos(id) ON DELETE SET NULL,
  filename         VARCHAR(500) NOT NULL,
  file_type        VARCHAR(50),           -- video / image / audio / document
  mime_type        VARCHAR(100),
  file_path        TEXT,                   -- physical path (immutable IDs only)
  file_size_bytes  BIGINT,
  duration_seconds INTEGER,
  resolution       VARCHAR(50),
  thumbnail_path   TEXT,
  cover_image_path TEXT,
  current_version  INTEGER NOT NULL DEFAULT 1,
  is_trashed       BOOLEAN NOT NULL DEFAULT false,
  trashed_at       TIMESTAMPTZ,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================================
-- Part 3: resource_items — Many-to-many (resource ↔ workspace)
-- ============================================================================

CREATE TABLE IF NOT EXISTS resource_items (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  resource_id  UUID NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
  scope_type   VARCHAR(20) NOT NULL CHECK (scope_type IN ('personal', 'team')),
  scope_id     UUID NOT NULL,             -- user_id or team_id
  folder_id    UUID REFERENCES folders(id) ON DELETE SET NULL,
  added_by     UUID REFERENCES auth.users(id),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================================
-- Part 4: resource_versions — Version management (upload only)
-- ============================================================================

CREATE TABLE IF NOT EXISTS resource_versions (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  resource_id      UUID NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
  version_number   INTEGER NOT NULL,
  filename         VARCHAR(500),
  file_path        TEXT,
  file_size_bytes  BIGINT,
  mime_type        VARCHAR(100),
  duration_seconds INTEGER,
  resolution       VARCHAR(50),
  thumbnail_path   TEXT,
  uploaded_by      UUID REFERENCES auth.users(id),
  notes            TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (resource_id, version_number)
);

-- ============================================================================
-- Part 5: Indexes
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_folders_scope
  ON folders (scope_type, scope_id);

CREATE INDEX IF NOT EXISTS idx_folders_parent
  ON folders (parent_id);

CREATE INDEX IF NOT EXISTS idx_folders_trashed
  ON folders (scope_type, scope_id, is_trashed)
  WHERE is_trashed = true;

CREATE INDEX IF NOT EXISTS idx_resources_creator
  ON resources (creator_id);

CREATE INDEX IF NOT EXISTS idx_resources_video
  ON resources (video_id)
  WHERE video_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_resources_trashed
  ON resources (is_trashed)
  WHERE is_trashed = true;

CREATE INDEX IF NOT EXISTS idx_resources_file_path
  ON resources (file_path)
  WHERE file_path IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_resource_items_scope
  ON resource_items (scope_type, scope_id);

CREATE INDEX IF NOT EXISTS idx_resource_items_resource
  ON resource_items (resource_id);

CREATE INDEX IF NOT EXISTS idx_resource_items_folder
  ON resource_items (folder_id)
  WHERE folder_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_resource_versions_resource
  ON resource_versions (resource_id);

-- ============================================================================
-- Part 6: Auto-update updated_at triggers
-- Reuses the existing update_updated_at_column() function
-- ============================================================================

DROP TRIGGER IF EXISTS update_folders_updated_at ON folders;
CREATE TRIGGER update_folders_updated_at
  BEFORE UPDATE ON folders
  FOR EACH ROW
  EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_resources_updated_at ON resources;
CREATE TRIGGER update_resources_updated_at
  BEFORE UPDATE ON resources
  FOR EACH ROW
  EXECUTE FUNCTION update_updated_at_column();

-- ============================================================================
-- Part 7: Enable RLS
-- ============================================================================

ALTER TABLE folders ENABLE ROW LEVEL SECURITY;
ALTER TABLE resources ENABLE ROW LEVEL SECURITY;
ALTER TABLE resource_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE resource_versions ENABLE ROW LEVEL SECURITY;

-- ============================================================================
-- Part 8: RLS Policies — folders
-- ============================================================================

-- SELECT: personal folders for owner, team folders for team members
DROP POLICY IF EXISTS "Users can read own folders" ON folders;
CREATE POLICY "Users can read own folders"
  ON folders FOR SELECT
  USING (
    (scope_type = 'personal' AND scope_id = auth.uid())
    OR (scope_type = 'team' AND scope_id IN (SELECT get_user_team_ids(auth.uid())))
  );

DROP POLICY IF EXISTS "Users can create folders" ON folders;
CREATE POLICY "Users can create folders"
  ON folders FOR INSERT
  WITH CHECK (
    created_by = auth.uid()
    AND (
      (scope_type = 'personal' AND scope_id = auth.uid())
      OR (scope_type = 'team' AND scope_id IN (SELECT get_user_team_ids(auth.uid())))
    )
  );

DROP POLICY IF EXISTS "Users can update own scope folders" ON folders;
CREATE POLICY "Users can update own scope folders"
  ON folders FOR UPDATE
  USING (
    (scope_type = 'personal' AND scope_id = auth.uid())
    OR (scope_type = 'team' AND scope_id IN (SELECT get_user_team_ids(auth.uid())))
  )
  WITH CHECK (
    (scope_type = 'personal' AND scope_id = auth.uid())
    OR (scope_type = 'team' AND scope_id IN (SELECT get_user_team_ids(auth.uid())))
  );

DROP POLICY IF EXISTS "Users can delete own scope folders" ON folders;
CREATE POLICY "Users can delete own scope folders"
  ON folders FOR DELETE
  USING (
    (scope_type = 'personal' AND scope_id = auth.uid())
    OR (scope_type = 'team' AND scope_id IN (SELECT get_user_team_ids(auth.uid())))
  );

DROP POLICY IF EXISTS "Service role full access on folders" ON folders;
CREATE POLICY "Service role full access on folders"
  ON folders FOR ALL
  USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ============================================================================
-- Part 9: RLS Policies — resources
-- ============================================================================

-- SELECT: creator can always read; team members can read via resource_items
DROP POLICY IF EXISTS "Users can read own resources" ON resources;
CREATE POLICY "Users can read own resources"
  ON resources FOR SELECT
  USING (
    creator_id = auth.uid()
    OR id IN (
      SELECT resource_id FROM resource_items
      WHERE (scope_type = 'personal' AND scope_id = auth.uid())
         OR (scope_type = 'team' AND scope_id IN (SELECT get_user_team_ids(auth.uid())))
    )
  );

DROP POLICY IF EXISTS "Users can create resources" ON resources;
CREATE POLICY "Users can create resources"
  ON resources FOR INSERT
  WITH CHECK (creator_id = auth.uid());

DROP POLICY IF EXISTS "Creators can update resources" ON resources;
CREATE POLICY "Creators can update resources"
  ON resources FOR UPDATE
  USING (creator_id = auth.uid())
  WITH CHECK (creator_id = auth.uid());

DROP POLICY IF EXISTS "Creators can delete resources" ON resources;
CREATE POLICY "Creators can delete resources"
  ON resources FOR DELETE
  USING (creator_id = auth.uid());

DROP POLICY IF EXISTS "Service role full access on resources" ON resources;
CREATE POLICY "Service role full access on resources"
  ON resources FOR ALL
  USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ============================================================================
-- Part 10: RLS Policies — resource_items
-- ============================================================================

DROP POLICY IF EXISTS "Users can read resource items in scope" ON resource_items;
CREATE POLICY "Users can read resource items in scope"
  ON resource_items FOR SELECT
  USING (
    (scope_type = 'personal' AND scope_id = auth.uid())
    OR (scope_type = 'team' AND scope_id IN (SELECT get_user_team_ids(auth.uid())))
  );

DROP POLICY IF EXISTS "Users can create resource items" ON resource_items;
CREATE POLICY "Users can create resource items"
  ON resource_items FOR INSERT
  WITH CHECK (
    added_by = auth.uid()
    AND (
      (scope_type = 'personal' AND scope_id = auth.uid())
      OR (scope_type = 'team' AND scope_id IN (SELECT get_user_team_ids(auth.uid())))
    )
  );

DROP POLICY IF EXISTS "Users can delete resource items in scope" ON resource_items;
CREATE POLICY "Users can delete resource items in scope"
  ON resource_items FOR DELETE
  USING (
    (scope_type = 'personal' AND scope_id = auth.uid())
    OR (scope_type = 'team' AND scope_id IN (SELECT get_user_team_ids(auth.uid())))
  );

DROP POLICY IF EXISTS "Service role full access on resource_items" ON resource_items;
CREATE POLICY "Service role full access on resource_items"
  ON resource_items FOR ALL
  USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ============================================================================
-- Part 11: RLS Policies — resource_versions
-- ============================================================================

DROP POLICY IF EXISTS "Users can read resource versions" ON resource_versions;
CREATE POLICY "Users can read resource versions"
  ON resource_versions FOR SELECT
  USING (
    resource_id IN (SELECT id FROM resources)
  );

DROP POLICY IF EXISTS "Users can create resource versions" ON resource_versions;
CREATE POLICY "Users can create resource versions"
  ON resource_versions FOR INSERT
  WITH CHECK (
    uploaded_by = auth.uid()
    AND resource_id IN (SELECT id FROM resources)
  );

DROP POLICY IF EXISTS "Service role full access on resource_versions" ON resource_versions;
CREATE POLICY "Service role full access on resource_versions"
  ON resource_versions FOR ALL
  USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ============================================================================
-- Part 12: Realtime
-- ============================================================================

ALTER PUBLICATION supabase_realtime ADD TABLE folders;
ALTER PUBLICATION supabase_realtime ADD TABLE resources;
ALTER PUBLICATION supabase_realtime ADD TABLE resource_items;

-- ============================================================================
-- Done!
-- Verify: SELECT count(*) FROM folders;          -- 0
--         SELECT count(*) FROM resources;         -- 0
--         SELECT count(*) FROM resource_items;    -- 0
--         SELECT count(*) FROM resource_versions; -- 0
-- ============================================================================
