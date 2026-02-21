-- 082_download_isolation.sql
-- Multi-user download isolation: add per-user download status to resources,
-- add image_download_status to parsed_media.

-- ============================================================
-- 1. parsed_media: add image support
-- ============================================================
ALTER TABLE parsed_media
  ADD COLUMN IF NOT EXISTS image_download_status download_status DEFAULT 'skipped',
  ADD COLUMN IF NOT EXISTS image_download_path TEXT;

-- For image-type records, migrate video_download_status → image_download_status
-- (images currently piggyback on video_download_status)
UPDATE parsed_media
SET image_download_status = video_download_status,
    image_download_path = download_path,
    video_download_status = 'skipped',
    download_path = NULL
WHERE media_type IN ('images', 'image');

-- ============================================================
-- 2. resources: add per-user download status columns
-- ============================================================
ALTER TABLE resources
  ADD COLUMN IF NOT EXISTS video_download_status download_status DEFAULT 'skipped',
  ADD COLUMN IF NOT EXISTS music_download_status download_status DEFAULT 'skipped',
  ADD COLUMN IF NOT EXISTS cover_download_status download_status DEFAULT 'skipped',
  ADD COLUMN IF NOT EXISTS image_download_status download_status DEFAULT 'skipped';

-- ============================================================
-- 3. Data migration: copy existing statuses from parsed_media to resources
-- ============================================================
UPDATE resources r
SET
  video_download_status = pm.video_download_status,
  music_download_status = pm.music_download_status,
  cover_download_status = pm.cover_download_status,
  image_download_status = pm.image_download_status
FROM parsed_media pm
WHERE r.media_id = pm.id
  AND r.media_id IS NOT NULL;

-- ============================================================
-- 4. Indexes
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_resources_video_dl_status
  ON resources (creator_id, video_download_status)
  WHERE video_download_status <> 'skipped';

CREATE INDEX IF NOT EXISTS idx_parsed_media_image_status
  ON parsed_media (image_download_status)
  WHERE image_download_status <> 'skipped';
