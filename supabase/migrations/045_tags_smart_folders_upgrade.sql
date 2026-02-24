-- 045_tags_smart_folders_upgrade.sql
-- Extend tags and smart_collections with workspace scope
-- Add resource_tags binding table

-- ============================================================================
-- Part 1: Extend tags table with scope
-- ============================================================================

ALTER TABLE tags
  ADD COLUMN IF NOT EXISTS scope_type VARCHAR(20) DEFAULT 'personal'
    CHECK (scope_type IN ('personal', 'team')),
  ADD COLUMN IF NOT EXISTS scope_id UUID;

-- Backfill: existing tags belong to their creator's personal scope
UPDATE tags SET scope_type = 'personal', scope_id = user_id
WHERE scope_id IS NULL AND user_id IS NOT NULL;

-- System tags have no scope
UPDATE tags SET scope_type = 'personal'
WHERE scope_id IS NULL AND type = 'system';

-- ============================================================================
-- Part 2: resource_tags — Resource-tag binding
-- ============================================================================

CREATE TABLE IF NOT EXISTS resource_tags (
  resource_id UUID NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
  tag_id      UUID NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  tagged_by   UUID REFERENCES auth.users(id),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (resource_id, tag_id)
);

-- ============================================================================
-- Part 3: Extend smart_collections with scope
-- ============================================================================

ALTER TABLE smart_collections
  ADD COLUMN IF NOT EXISTS scope_type VARCHAR(20) DEFAULT 'personal'
    CHECK (scope_type IN ('personal', 'team')),
  ADD COLUMN IF NOT EXISTS scope_id UUID;

-- Backfill: existing smart collections are personal
UPDATE smart_collections SET scope_type = 'personal'
WHERE scope_id IS NULL;

-- ============================================================================
-- Part 4: Indexes
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_tags_scope
  ON tags (scope_type, scope_id);

CREATE INDEX IF NOT EXISTS idx_resource_tags_resource
  ON resource_tags (resource_id);

CREATE INDEX IF NOT EXISTS idx_resource_tags_tag
  ON resource_tags (tag_id);

CREATE INDEX IF NOT EXISTS idx_smart_collections_scope
  ON smart_collections (scope_type, scope_id);

-- ============================================================================
-- Part 5: Enable RLS on resource_tags
-- ============================================================================

ALTER TABLE resource_tags ENABLE ROW LEVEL SECURITY;

-- SELECT: if user can see the resource, they can see its tags
DROP POLICY IF EXISTS "Users can read resource tags" ON resource_tags;
CREATE POLICY "Users can read resource tags"
  ON resource_tags FOR SELECT
  USING (
    resource_id IN (SELECT id FROM resources)
  );

-- INSERT: authenticated users can tag resources they can see
DROP POLICY IF EXISTS "Users can create resource tags" ON resource_tags;
CREATE POLICY "Users can create resource tags"
  ON resource_tags FOR INSERT
  WITH CHECK (
    tagged_by = auth.uid()
    AND resource_id IN (SELECT id FROM resources)
  );

-- DELETE: users can remove tags they added
DROP POLICY IF EXISTS "Users can delete own resource tags" ON resource_tags;
CREATE POLICY "Users can delete own resource tags"
  ON resource_tags FOR DELETE
  USING (tagged_by = auth.uid());

DROP POLICY IF EXISTS "Service role full access on resource_tags" ON resource_tags;
CREATE POLICY "Service role full access on resource_tags"
  ON resource_tags FOR ALL
  USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ============================================================================
-- Done!
-- Verify: SELECT count(*) FROM resource_tags;  -- 0
--         SELECT scope_type FROM tags LIMIT 5;  -- should show 'personal'
-- ============================================================================
