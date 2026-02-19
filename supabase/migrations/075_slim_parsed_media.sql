-- 075_slim_parsed_media.sql
-- Remove columns that are redundant or moved to resources table.
-- parsed_media should only hold immutable platform snapshot data.

BEGIN;

-- Drop dependent views first (will be rebuilt as resource_statistics in 079)
DROP VIEW IF EXISTS parsed_media_with_tags;
DROP VIEW IF EXISTS media_statistics;

-- Columns identical/redundant to other columns
ALTER TABLE parsed_media DROP COLUMN IF EXISTS source_url;       -- identical to original_url
ALTER TABLE parsed_media DROP COLUMN IF EXISTS external_id;      -- overlaps with platform_id

-- Boolean flags redundant with status columns (status now on resources via 067)
ALTER TABLE parsed_media DROP COLUMN IF EXISTS transcript_bool;
ALTER TABLE parsed_media DROP COLUMN IF EXISTS summary_bool;

-- User-editable fields that belong on resources (already there via 074)
ALTER TABLE parsed_media DROP COLUMN IF EXISTS notes;

-- AI status columns moved to resources in migration 067
ALTER TABLE parsed_media DROP COLUMN IF EXISTS transcript_status;
ALTER TABLE parsed_media DROP COLUMN IF EXISTS summary_status;
ALTER TABLE parsed_media DROP COLUMN IF EXISTS visual_analysis_status;

COMMIT;
