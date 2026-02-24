-- 058_libraries.sql
-- Phase 2: Team Libraries entity + schema extensions for smart folders
-- Depends on: 050 (snowflake_id_infrastructure), 051 (snowflake_migration)

-- ============================================================================
-- Part 1: libraries — Team resource libraries
-- ============================================================================

CREATE TABLE IF NOT EXISTS libraries (
  id          BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
  name        TEXT NOT NULL,
  scope_type  TEXT NOT NULL CHECK (scope_type IN ('team')),
  scope_id    TEXT NOT NULL,          -- team_id (TEXT after snowflake migration)
  created_by  UUID NOT NULL REFERENCES auth.users(id),
  icon        TEXT,                    -- emoji or icon name
  color       TEXT,                    -- hex color
  sort_order  INTEGER DEFAULT 0,
  visibility  TEXT NOT NULL DEFAULT 'inherited'
    CHECK (visibility IN ('inherited', 'restricted')),
  created_at  TIMESTAMPTZ DEFAULT NOW(),
  updated_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_libraries_scope
  ON libraries(scope_type, scope_id);

-- ============================================================================
-- Part 2: Add library_id to folders and resource_items
-- ============================================================================

ALTER TABLE folders
  ADD COLUMN IF NOT EXISTS library_id BIGINT REFERENCES libraries(id) ON DELETE CASCADE;

ALTER TABLE resource_items
  ADD COLUMN IF NOT EXISTS library_id BIGINT REFERENCES libraries(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_folders_library
  ON folders(library_id) WHERE library_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_resource_items_library
  ON resource_items(library_id) WHERE library_id IS NOT NULL;

-- ============================================================================
-- Part 3: Add smart folder columns to folders
-- ============================================================================

ALTER TABLE folders
  ADD COLUMN IF NOT EXISTS is_smart BOOLEAN DEFAULT FALSE;

ALTER TABLE folders
  ADD COLUMN IF NOT EXISTS smart_rules JSONB;

CREATE INDEX IF NOT EXISTS idx_folders_smart
  ON folders(is_smart) WHERE is_smart = TRUE;

-- ============================================================================
-- Part 4: Update access_overrides to support library object_type
-- ============================================================================

-- Drop the old CHECK constraint and recreate with 'library' included
ALTER TABLE access_overrides
  DROP CONSTRAINT IF EXISTS access_overrides_object_type_check;

ALTER TABLE access_overrides
  ADD CONSTRAINT access_overrides_object_type_check
  CHECK (object_type IN ('library', 'folder', 'project'));

-- ============================================================================
-- Part 5: Add library_id to shares (polymorphic target)
-- ============================================================================

ALTER TABLE shares
  ADD COLUMN IF NOT EXISTS library_id BIGINT REFERENCES libraries(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_shares_library
  ON shares(library_id) WHERE library_id IS NOT NULL;

-- ============================================================================
-- Part 6: Enable RLS on libraries
-- ============================================================================

ALTER TABLE libraries ENABLE ROW LEVEL SECURITY;

-- SELECT: team members can read their team's libraries
DROP POLICY IF EXISTS "Team members can read libraries" ON libraries;
CREATE POLICY "Team members can read libraries"
  ON libraries FOR SELECT
  USING (
    scope_id IN (SELECT get_user_team_ids_text(auth.uid()))
  );

-- INSERT: team members can create libraries
DROP POLICY IF EXISTS "Team members can create libraries" ON libraries;
CREATE POLICY "Team members can create libraries"
  ON libraries FOR INSERT
  WITH CHECK (
    created_by = auth.uid()
    AND scope_id IN (SELECT get_user_team_ids_text(auth.uid()))
  );

-- UPDATE: library creator or team admin can update
DROP POLICY IF EXISTS "Library creators can update" ON libraries;
CREATE POLICY "Library creators can update"
  ON libraries FOR UPDATE
  USING (
    created_by = auth.uid()
    OR scope_id IN (SELECT get_user_team_ids_text(auth.uid()))
  )
  WITH CHECK (
    created_by = auth.uid()
    OR scope_id IN (SELECT get_user_team_ids_text(auth.uid()))
  );

-- DELETE: library creator or team admin can delete
DROP POLICY IF EXISTS "Library creators can delete" ON libraries;
CREATE POLICY "Library creators can delete"
  ON libraries FOR DELETE
  USING (
    created_by = auth.uid()
    OR scope_id IN (SELECT get_user_team_ids_text(auth.uid()))
  );

-- Service role bypass
DROP POLICY IF EXISTS "Service role full access on libraries" ON libraries;
CREATE POLICY "Service role full access on libraries"
  ON libraries FOR ALL
  USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ============================================================================
-- Part 7: Enable Realtime
-- ============================================================================

ALTER PUBLICATION supabase_realtime ADD TABLE libraries;

-- ============================================================================
-- Done!
-- Verify: SELECT count(*) FROM libraries;  -- 0
--         \d folders  -- should show library_id, is_smart, smart_rules columns
--         \d resource_items  -- should show library_id column
-- ============================================================================
