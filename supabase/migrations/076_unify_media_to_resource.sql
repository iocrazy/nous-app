-- 076_unify_media_to_resource.sql
-- Rename media_* satellite tables to resource_*, update FKs to point to resources.
-- All tables have 0 rows so restructuring is safe.
-- Views already dropped in 075.

BEGIN;

-- ============================================================================
-- 1. media_summaries -> resource_summaries
-- ============================================================================
ALTER TABLE media_summaries RENAME TO resource_summaries;

-- Drop old FK (named video_summaries_video_id_fkey from original creation)
ALTER TABLE resource_summaries DROP CONSTRAINT IF EXISTS video_summaries_video_id_fkey;

-- Rename column and add new FK to resources
ALTER TABLE resource_summaries RENAME COLUMN media_id TO resource_id;
ALTER TABLE resource_summaries
  ADD CONSTRAINT resource_summaries_resource_id_fkey
  FOREIGN KEY (resource_id) REFERENCES resources(id) ON DELETE CASCADE;

-- Update unique constraint (old PK was on id, add unique on resource_id)
ALTER TABLE resource_summaries DROP CONSTRAINT IF EXISTS video_summaries_pkey;
ALTER TABLE resource_summaries ADD PRIMARY KEY (id);
ALTER TABLE resource_summaries ADD CONSTRAINT resource_summaries_resource_id_key UNIQUE (resource_id);

-- ============================================================================
-- 2. media_transcripts -> resource_transcripts
-- ============================================================================
ALTER TABLE media_transcripts RENAME TO resource_transcripts;

ALTER TABLE resource_transcripts DROP CONSTRAINT IF EXISTS video_transcripts_video_id_fkey;

ALTER TABLE resource_transcripts RENAME COLUMN media_id TO resource_id;
ALTER TABLE resource_transcripts
  ADD CONSTRAINT resource_transcripts_resource_id_fkey
  FOREIGN KEY (resource_id) REFERENCES resources(id) ON DELETE CASCADE;

ALTER TABLE resource_transcripts DROP CONSTRAINT IF EXISTS video_transcripts_pkey;
ALTER TABLE resource_transcripts ADD PRIMARY KEY (id);
ALTER TABLE resource_transcripts ADD CONSTRAINT resource_transcripts_resource_id_key UNIQUE (resource_id);

-- ============================================================================
-- 3. media_analysis -> resource_analysis
-- ============================================================================
ALTER TABLE media_analysis RENAME TO resource_analysis;

-- Drop old PK and FK
ALTER TABLE resource_analysis DROP CONSTRAINT IF EXISTS video_analysis_pkey;
ALTER TABLE resource_analysis DROP CONSTRAINT IF EXISTS video_analysis_video_id_fkey;

-- Rename column
ALTER TABLE resource_analysis RENAME COLUMN media_id TO resource_id;

-- Recreate PK (composite: resource_id + analysis_level)
ALTER TABLE resource_analysis
  ADD CONSTRAINT resource_analysis_pkey PRIMARY KEY (resource_id, analysis_level);

ALTER TABLE resource_analysis
  ADD CONSTRAINT resource_analysis_resource_id_fkey
  FOREIGN KEY (resource_id) REFERENCES resources(id) ON DELETE CASCADE;

-- ============================================================================
-- 4. media_access_logs -> resource_access_logs
-- ============================================================================
ALTER TABLE media_access_logs RENAME TO resource_access_logs;

ALTER TABLE resource_access_logs DROP CONSTRAINT IF EXISTS video_access_logs_video_id_fkey;
-- Keep user FK as-is (video_access_logs_user_id_fkey)

ALTER TABLE resource_access_logs RENAME COLUMN media_id TO resource_id;
ALTER TABLE resource_access_logs
  ADD CONSTRAINT resource_access_logs_resource_id_fkey
  FOREIGN KEY (resource_id) REFERENCES resources(id) ON DELETE CASCADE;

-- Update indexes
DROP INDEX IF EXISTS idx_media_access_logs_media_id;
CREATE INDEX IF NOT EXISTS idx_resource_access_logs_resource_id
  ON resource_access_logs(resource_id);

-- ============================================================================
-- 5. DROP media_collections (replaced by resource_items + folders, 0 rows)
-- ============================================================================
DROP TABLE IF EXISTS media_collections;

COMMIT;
