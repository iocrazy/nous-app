-- 083_cleanup_parsed_media_user_fields.sql
-- Remove user-specific columns from parsed_media (now a global content table).
-- Per-user download preferences are tracked in the resources table.
-- IMPORTANT: Only run AFTER all backend/frontend code is deployed and verified.

-- 1. Drop indexes that reference user_id
DROP INDEX IF EXISTS idx_parsed_media_user_id;
DROP INDEX IF EXISTS idx_parsed_media_user_created;

-- 2. Drop FK constraint on user_id
ALTER TABLE parsed_media DROP CONSTRAINT IF EXISTS douyin_videos_user_id_fkey;

-- 3. Drop dependent RLS policies on related tables (they reference parsed_media.user_id)
DROP POLICY IF EXISTS "Users can update own videos or admin" ON parsed_media;
DROP POLICY IF EXISTS "View analysis of owned videos" ON resource_analysis;
DROP POLICY IF EXISTS "Manage analysis of owned videos" ON resource_analysis;
DROP POLICY IF EXISTS "View transcripts of owned videos" ON resource_transcripts;
DROP POLICY IF EXISTS "Manage transcripts of owned videos" ON resource_transcripts;
DROP POLICY IF EXISTS "View summaries of owned videos" ON resource_summaries;
DROP POLICY IF EXISTS "Manage summaries of owned videos" ON resource_summaries;

-- 4. Drop user-specific columns
ALTER TABLE parsed_media DROP COLUMN IF EXISTS user_id;
ALTER TABLE parsed_media DROP COLUMN IF EXISTS need_download_video;
ALTER TABLE parsed_media DROP COLUMN IF EXISTS need_download_music;
ALTER TABLE parsed_media DROP COLUMN IF EXISTS need_download_cover;

-- 5. Recreate parsed_media UPDATE policy (authenticated users only)
CREATE POLICY "Authenticated users can update videos" ON parsed_media
  FOR UPDATE
  USING (auth.role() = 'authenticated');

-- 6. Recreate dependent RLS policies using resources.creator_id instead of parsed_media.user_id
--    Users can view/manage analysis/transcripts/summaries for resources they own.

CREATE POLICY "View analysis of owned resources" ON resource_analysis
  FOR SELECT
  USING (EXISTS (
    SELECT 1 FROM resources
    WHERE resources.media_id = resource_analysis.resource_id
      AND resources.creator_id = auth.uid()
  ));

CREATE POLICY "Manage analysis of owned resources" ON resource_analysis
  FOR ALL
  USING (EXISTS (
    SELECT 1 FROM resources
    WHERE resources.media_id = resource_analysis.resource_id
      AND resources.creator_id = auth.uid()
  ));

CREATE POLICY "View transcripts of owned resources" ON resource_transcripts
  FOR SELECT
  USING (EXISTS (
    SELECT 1 FROM resources
    WHERE resources.media_id = resource_transcripts.resource_id
      AND resources.creator_id = auth.uid()
  ));

CREATE POLICY "Manage transcripts of owned resources" ON resource_transcripts
  FOR ALL
  USING (EXISTS (
    SELECT 1 FROM resources
    WHERE resources.media_id = resource_transcripts.resource_id
      AND resources.creator_id = auth.uid()
  ));

CREATE POLICY "View summaries of owned resources" ON resource_summaries
  FOR SELECT
  USING (EXISTS (
    SELECT 1 FROM resources
    WHERE resources.media_id = resource_summaries.resource_id
      AND resources.creator_id = auth.uid()
  ));

CREATE POLICY "Manage summaries of owned resources" ON resource_summaries
  FOR ALL
  USING (EXISTS (
    SELECT 1 FROM resources
    WHERE resources.media_id = resource_summaries.resource_id
      AND resources.creator_id = auth.uid()
  ));
