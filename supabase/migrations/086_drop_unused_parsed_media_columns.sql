-- Drop unused columns from parsed_media
-- author_id: never written/read, legacy artifact (author nickname stored in `author` text column)
-- cover_url: never written/read, legacy artifact (cover URLs stored in `cover_urls` text[] array)
-- Note: dynamic_cover_url is KEPT — actively written by douyin_parser and read by getCoverUrl()

ALTER TABLE parsed_media DROP COLUMN IF EXISTS author_id;
ALTER TABLE parsed_media DROP COLUMN IF EXISTS cover_url;
