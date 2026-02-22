-- Add music_download_path column to parsed_media
-- Mirrors download_path (video), cover_download_path (cover), image_download_path (images)
ALTER TABLE parsed_media
  ADD COLUMN IF NOT EXISTS music_download_path TEXT;

COMMENT ON COLUMN parsed_media.music_download_path IS 'Relative path to downloaded music file, e.g. global/resources/web/douyin/{id}/music.mp3';
