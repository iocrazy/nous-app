-- 080_resource_last_location.sql
-- Track the last folder/library a resource was in before it became orphaned.
-- Used by restore to recreate the resource_item in the right place.

ALTER TABLE resources
  ADD COLUMN IF NOT EXISTS last_folder_id BIGINT,
  ADD COLUMN IF NOT EXISTS last_library_id BIGINT,
  ADD COLUMN IF NOT EXISTS last_scope_type VARCHAR(20),
  ADD COLUMN IF NOT EXISTS last_scope_id TEXT;
