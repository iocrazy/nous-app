-- 060_collections_snowflake_migration.sql
-- Migrate collections.id from BIGINT sequential to BIGINT Snowflake
-- Migrate smart_collections.id from UUID to BIGINT Snowflake
-- Both tables are empty — no data conversion needed, just type swap.

-- ============================================================================
-- SECTION 0: Remove from realtime publication
-- ============================================================================

DO $$
DECLARE r RECORD;
BEGIN
  FOR r IN (
    SELECT schemaname, tablename
    FROM pg_publication_tables
    WHERE pubname = 'supabase_realtime'
      AND schemaname = 'public'
      AND tablename IN ('collections', 'smart_collections', 'video_collections')
  ) LOOP
    EXECUTE format('ALTER PUBLICATION supabase_realtime DROP TABLE %I.%I', r.schemaname, r.tablename);
  END LOOP;
END $$;

-- ============================================================================
-- SECTION 1: Drop RLS policies
-- ============================================================================

DO $$
DECLARE r RECORD;
BEGIN
  FOR r IN (
    SELECT schemaname, tablename, policyname
    FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename IN ('collections', 'smart_collections', 'video_collections')
  ) LOOP
    EXECUTE format('DROP POLICY IF EXISTS %I ON %I.%I', r.policyname, r.schemaname, r.tablename);
  END LOOP;
END $$;

-- ============================================================================
-- SECTION 2: Drop FK constraints referencing collections
-- ============================================================================

ALTER TABLE video_collections DROP CONSTRAINT IF EXISTS video_collections_collection_id_fkey;

-- ============================================================================
-- SECTION 3: Drop PKs
-- ============================================================================

ALTER TABLE video_collections DROP CONSTRAINT IF EXISTS video_collections_pkey;
ALTER TABLE collections DROP CONSTRAINT IF EXISTS collections_pkey;
ALTER TABLE smart_collections DROP CONSTRAINT IF EXISTS smart_collections_pkey;

-- ============================================================================
-- SECTION 4: Drop indexes
-- ============================================================================

DROP INDEX IF EXISTS idx_collections_owner;
DROP INDEX IF EXISTS idx_smart_collections_preset;
DROP INDEX IF EXISTS idx_smart_collections_scope;
DROP INDEX IF EXISTS idx_smart_collections_user;

-- ============================================================================
-- SECTION 5A: Migrate collections.id — sequential BIGINT → Snowflake BIGINT
-- (Same data type, just change the default from nextval to snowflake)
-- ============================================================================

-- Drop the old sequence default
ALTER TABLE collections ALTER COLUMN id DROP DEFAULT;
-- Set snowflake default
ALTER TABLE collections ALTER COLUMN id SET DEFAULT generate_snowflake_id();
-- Drop the orphaned sequence
DROP SEQUENCE IF EXISTS collections_id_seq;

-- ============================================================================
-- SECTION 5B: Migrate smart_collections.id — UUID → BIGINT Snowflake
-- (Table is empty, so just drop and re-add the column)
-- ============================================================================

ALTER TABLE smart_collections DROP COLUMN id;
ALTER TABLE smart_collections ADD COLUMN id BIGINT DEFAULT generate_snowflake_id() NOT NULL;

-- ============================================================================
-- SECTION 5C: Migrate video_collections.collection_id — already BIGINT, no change needed
-- (Type matches; only FK constraint needs recreation)
-- ============================================================================

-- ============================================================================
-- SECTION 6: Recreate PKs
-- ============================================================================

ALTER TABLE collections ADD PRIMARY KEY (id);
ALTER TABLE smart_collections ADD PRIMARY KEY (id);
ALTER TABLE video_collections ADD PRIMARY KEY (video_id, collection_id);

-- ============================================================================
-- SECTION 7: Recreate FK constraints
-- ============================================================================

ALTER TABLE video_collections
  ADD CONSTRAINT video_collections_collection_id_fkey
  FOREIGN KEY (collection_id) REFERENCES collections(id) ON DELETE CASCADE;

-- ============================================================================
-- SECTION 8: Recreate indexes
-- ============================================================================

CREATE INDEX idx_collections_owner ON collections(owner_id);
CREATE INDEX idx_smart_collections_preset ON smart_collections(is_preset);
CREATE INDEX idx_smart_collections_scope ON smart_collections(scope_type, scope_id);
CREATE INDEX idx_smart_collections_user ON smart_collections(user_id);

-- ============================================================================
-- SECTION 9: Recreate RLS policies
-- ============================================================================

-- collections
CREATE POLICY "Users can view their collections" ON collections
  FOR SELECT USING (
    owner_id = auth.uid()
    OR team_id IN (SELECT get_user_team_ids(auth.uid()))
  );
CREATE POLICY "Users can create collections" ON collections
  FOR INSERT WITH CHECK (owner_id = auth.uid());
CREATE POLICY "Users can update collections" ON collections
  FOR UPDATE USING (
    owner_id = auth.uid()
    OR team_id IN (SELECT get_user_team_ids(auth.uid()))
  );
CREATE POLICY "Owners can delete collections" ON collections
  FOR DELETE USING (owner_id = auth.uid());

-- smart_collections
CREATE POLICY "View own collections" ON smart_collections
  FOR SELECT USING (user_id = auth.uid());
CREATE POLICY "Create own collections" ON smart_collections
  FOR INSERT WITH CHECK (user_id = auth.uid());
CREATE POLICY "Update own collections" ON smart_collections
  FOR UPDATE USING (user_id = auth.uid());
CREATE POLICY "Delete own non-preset collections" ON smart_collections
  FOR DELETE USING (user_id = auth.uid() AND is_preset = false);

-- video_collections
CREATE POLICY "View video collections" ON video_collections
  FOR SELECT USING (auth.role() = 'authenticated');
CREATE POLICY "Manage video collections" ON video_collections
  FOR ALL USING (auth.role() = 'authenticated');

-- ============================================================================
-- SECTION 10: Re-enable realtime
-- ============================================================================

ALTER TABLE collections REPLICA IDENTITY FULL;
ALTER TABLE smart_collections REPLICA IDENTITY FULL;
ALTER TABLE video_collections REPLICA IDENTITY FULL;
ALTER PUBLICATION supabase_realtime ADD TABLE collections;
ALTER PUBLICATION supabase_realtime ADD TABLE smart_collections;
ALTER PUBLICATION supabase_realtime ADD TABLE video_collections;

-- ============================================================================
-- SECTION 11: Update comments
-- ============================================================================

COMMENT ON TABLE collections IS 'User/team video collections. PK migrated to Snowflake BIGINT in 060.';
COMMENT ON TABLE smart_collections IS 'Smart folders with dynamic filter rules. PK migrated from UUID to Snowflake BIGINT in 060.';
