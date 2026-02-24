-- 078_standardize_ids.sql
-- Migrate core UUID tables to Snowflake BIGINT for consistency and performance.
-- generate_snowflake_id() exists from migration 050.
-- Strategy: add new_id column, populate, update FK refs, swap columns.

BEGIN;

-- ============================================================================
-- 1. tags (14 rows, referenced by resource_tags.tag_id)
-- ============================================================================

-- Add new BIGINT id column
ALTER TABLE tags ADD COLUMN new_id BIGINT;
UPDATE tags SET new_id = generate_snowflake_id();

-- Update referencing table: resource_tags
ALTER TABLE resource_tags DROP CONSTRAINT IF EXISTS resource_tags_pkey;
ALTER TABLE resource_tags DROP CONSTRAINT IF EXISTS resource_tags_tag_id_fkey;

ALTER TABLE resource_tags ADD COLUMN new_tag_id BIGINT;
UPDATE resource_tags rt SET new_tag_id = t.new_id FROM tags t WHERE rt.tag_id = t.id;
ALTER TABLE resource_tags DROP COLUMN tag_id;
ALTER TABLE resource_tags RENAME COLUMN new_tag_id TO tag_id;

-- Swap tags.id
ALTER TABLE tags DROP CONSTRAINT IF EXISTS tags_pkey;
ALTER TABLE tags DROP COLUMN id;
ALTER TABLE tags RENAME COLUMN new_id TO id;
ALTER TABLE tags ADD PRIMARY KEY (id);
ALTER TABLE tags ALTER COLUMN id SET DEFAULT generate_snowflake_id();

-- Recreate resource_tags PK and FK
ALTER TABLE resource_tags ADD PRIMARY KEY (resource_id, tag_id);
ALTER TABLE resource_tags
  ADD CONSTRAINT resource_tags_tag_id_fkey
  FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE;

-- ============================================================================
-- 2. resource_versions (28 rows, referenced by review_status.version_id,
--    review_comments.version_id)
-- ============================================================================

ALTER TABLE resource_versions ADD COLUMN new_id BIGINT;
UPDATE resource_versions SET new_id = generate_snowflake_id();

-- Drop FKs from review_status and review_comments
ALTER TABLE review_status DROP CONSTRAINT IF EXISTS review_status_version_id_fkey;
ALTER TABLE review_comments DROP CONSTRAINT IF EXISTS review_comments_version_id_fkey;

-- Update review_status.version_id (UUID -> BIGINT via mapping)
ALTER TABLE review_status ADD COLUMN new_version_id BIGINT;
UPDATE review_status rs SET new_version_id = rv.new_id
FROM resource_versions rv WHERE rs.version_id = rv.id;
ALTER TABLE review_status DROP COLUMN version_id;
ALTER TABLE review_status RENAME COLUMN new_version_id TO version_id;

-- Update review_comments.version_id
ALTER TABLE review_comments ADD COLUMN new_version_id BIGINT;
UPDATE review_comments rc SET new_version_id = rv.new_id
FROM resource_versions rv WHERE rc.version_id = rv.id;
ALTER TABLE review_comments DROP COLUMN version_id;
ALTER TABLE review_comments RENAME COLUMN new_version_id TO version_id;

-- Swap resource_versions.id
ALTER TABLE resource_versions DROP CONSTRAINT IF EXISTS resource_versions_pkey;
ALTER TABLE resource_versions DROP CONSTRAINT IF EXISTS file_versions_pkey;
ALTER TABLE resource_versions DROP COLUMN id;
ALTER TABLE resource_versions RENAME COLUMN new_id TO id;
ALTER TABLE resource_versions ADD PRIMARY KEY (id);
ALTER TABLE resource_versions ALTER COLUMN id SET DEFAULT generate_snowflake_id();

-- Recreate FKs
ALTER TABLE review_status
  ADD CONSTRAINT review_status_version_id_fkey
  FOREIGN KEY (version_id) REFERENCES resource_versions(id) ON DELETE CASCADE;
ALTER TABLE review_comments
  ADD CONSTRAINT review_comments_version_id_fkey
  FOREIGN KEY (version_id) REFERENCES resource_versions(id) ON DELETE SET NULL;

-- ============================================================================
-- 3. resource_items (29 rows, no inbound FKs)
-- ============================================================================

ALTER TABLE resource_items ADD COLUMN new_id BIGINT;
UPDATE resource_items SET new_id = generate_snowflake_id();

ALTER TABLE resource_items DROP CONSTRAINT IF EXISTS resource_items_pkey;
ALTER TABLE resource_items DROP COLUMN id;
ALTER TABLE resource_items RENAME COLUMN new_id TO id;
ALTER TABLE resource_items ADD PRIMARY KEY (id);
ALTER TABLE resource_items ALTER COLUMN id SET DEFAULT generate_snowflake_id();

-- ============================================================================
-- 4. unified_tasks (14 rows, no inbound FKs)
-- ============================================================================

ALTER TABLE unified_tasks ADD COLUMN new_id BIGINT;
UPDATE unified_tasks SET new_id = generate_snowflake_id();

ALTER TABLE unified_tasks DROP CONSTRAINT IF EXISTS unified_tasks_pkey;
ALTER TABLE unified_tasks DROP COLUMN id;
ALTER TABLE unified_tasks RENAME COLUMN new_id TO id;
ALTER TABLE unified_tasks ADD PRIMARY KEY (id);
ALTER TABLE unified_tasks ALTER COLUMN id SET DEFAULT generate_snowflake_id();

-- ============================================================================
-- 5. task_assets (0 rows, no inbound FKs)
-- ============================================================================

ALTER TABLE task_assets DROP CONSTRAINT IF EXISTS task_assets_pkey;
ALTER TABLE task_assets ALTER COLUMN id DROP DEFAULT;
ALTER TABLE task_assets ALTER COLUMN id SET DATA TYPE BIGINT USING (generate_snowflake_id());
ALTER TABLE task_assets ALTER COLUMN id SET DEFAULT generate_snowflake_id();
ALTER TABLE task_assets ADD PRIMARY KEY (id);

-- task_assets.task_id also needs to be BIGINT (RLS policies depend on it)
DROP POLICY IF EXISTS "Users can read task assets" ON task_assets;
DROP POLICY IF EXISTS "Users can create task assets" ON task_assets;
DROP POLICY IF EXISTS "Users can delete task assets" ON task_assets;
DROP POLICY IF EXISTS "Service role full access on task_assets" ON task_assets;

ALTER TABLE task_assets ALTER COLUMN task_id DROP DEFAULT;
ALTER TABLE task_assets ALTER COLUMN task_id SET DATA TYPE BIGINT USING NULL;

-- Recreate RLS policies (simplified — service role bypass + user access via task ownership)
CREATE POLICY "Service role full access on task_assets" ON task_assets
  FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Users can read task assets" ON task_assets
  FOR SELECT USING (
    EXISTS (
      SELECT 1 FROM unified_tasks ut
      WHERE ut.id = task_assets.task_id
        AND ut.user_id = auth.uid()
    )
  );
CREATE POLICY "Users can create task assets" ON task_assets
  FOR INSERT WITH CHECK (
    EXISTS (
      SELECT 1 FROM unified_tasks ut
      WHERE ut.id = task_assets.task_id
        AND ut.user_id = auth.uid()
    )
  );
CREATE POLICY "Users can delete task assets" ON task_assets
  FOR DELETE USING (
    EXISTS (
      SELECT 1 FROM unified_tasks ut
      WHERE ut.id = task_assets.task_id
        AND ut.user_id = auth.uid()
    )
  );

-- ============================================================================
-- 6. notifications (0 rows, referenced by user_notifications.notification_id)
-- ============================================================================

-- Drop FK from user_notifications first
ALTER TABLE user_notifications DROP CONSTRAINT IF EXISTS user_notifications_notification_id_fkey;

ALTER TABLE notifications DROP CONSTRAINT IF EXISTS notifications_pkey;
ALTER TABLE notifications ALTER COLUMN id DROP DEFAULT;
ALTER TABLE notifications ALTER COLUMN id SET DATA TYPE BIGINT USING (generate_snowflake_id());
ALTER TABLE notifications ALTER COLUMN id SET DEFAULT generate_snowflake_id();
ALTER TABLE notifications ADD PRIMARY KEY (id);

-- Convert user_notifications.notification_id to BIGINT too
ALTER TABLE user_notifications ALTER COLUMN notification_id SET DATA TYPE BIGINT USING NULL;

-- Recreate FK
ALTER TABLE user_notifications
  ADD CONSTRAINT user_notifications_notification_id_fkey
  FOREIGN KEY (notification_id) REFERENCES notifications(id) ON DELETE CASCADE;

-- ============================================================================
-- 7. user_logs (61 rows)
-- ============================================================================

ALTER TABLE user_logs ADD COLUMN new_id BIGINT;
UPDATE user_logs SET new_id = generate_snowflake_id();

ALTER TABLE user_logs DROP CONSTRAINT IF EXISTS user_logs_pkey;
ALTER TABLE user_logs DROP COLUMN id;
ALTER TABLE user_logs RENAME COLUMN new_id TO id;
ALTER TABLE user_logs ADD PRIMARY KEY (id);
ALTER TABLE user_logs ALTER COLUMN id SET DEFAULT generate_snowflake_id();

-- ============================================================================
-- 8. search_logs (0 rows)
-- ============================================================================

ALTER TABLE search_logs DROP CONSTRAINT IF EXISTS search_logs_pkey;
ALTER TABLE search_logs ALTER COLUMN id DROP DEFAULT;
ALTER TABLE search_logs ALTER COLUMN id SET DATA TYPE BIGINT USING (generate_snowflake_id());
ALTER TABLE search_logs ALTER COLUMN id SET DEFAULT generate_snowflake_id();
ALTER TABLE search_logs ADD PRIMARY KEY (id);

COMMIT;
