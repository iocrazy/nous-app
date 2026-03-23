-- Add music_play_urls column to parsed_media for carousel/image-text types
-- These types (aweme_type 2/68) have standalone background music that must be
-- downloaded separately (not extracted from video like standard video types).
ALTER TABLE parsed_media
  ADD COLUMN IF NOT EXISTS music_play_urls JSONB DEFAULT '[]'::JSONB;

COMMENT ON COLUMN parsed_media.music_play_urls
  IS 'Standalone music play URLs for carousel/image-text content (type 2/68). List of fallback URLs from music.play_url.url_list.';
