-- Remove music_download_urls and need_download_music columns
-- Audio is now extracted from video via ffmpeg, not downloaded via URL
ALTER TABLE parsed_media DROP COLUMN IF EXISTS music_download_urls;
ALTER TABLE parsed_media DROP COLUMN IF EXISTS need_download_music;
