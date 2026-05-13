-- 216_parsed_media_ytdlp_formats.sql
--
-- Add `ytdlp_formats` jsonb column to parsed_media.
--
-- Background — 2026-05-13: bilibili downloads were intermittently
-- stalling for 60s because the download stage re-invoked yt-dlp from
-- scratch (re-fetching the share-page HTML / wbi sign / playurl API
-- → finally downloading the m4s stream). The metadata stage had
-- already pulled the same info_dict during parse and got the actual
-- stream URLs from `formats[]`, but `_map_metadata_to_media` discarded
-- them. The download-stage yt-dlp then redid the entire scrape and
-- was the one that hit the upstream silence.
--
-- Live spike confirmed (NAS container, 2026-05-13):
--   * httpx GET video.m4s + Referer header → 0.72s for 12MB
--   * httpx GET audio.m4s + Referer header → 2.4s for 12MB
--   * ffmpeg -c copy merge → ~instant, 24.7MB mp4 with h264+aac
--
-- This column stores the salient subset of yt-dlp's `formats[]` array
-- (url, http_headers, format_id, vcodec, acodec, tbr, filesize, ext)
-- so the download stage can pick best-video + best-audio and pull them
-- directly via httpx, skipping the second yt-dlp invocation entirely.
--
-- Schema kept as plain jsonb (not a constrained shape) because the
-- exact yt-dlp format fields evolve with extractor updates — we want
-- to preserve forward-compat without rewriting the migration.
--
-- The legacy `video_download_urls` column is unchanged: existing code
-- still falls back to it (and to yt-dlp subprocess) when
-- ytdlp_formats is empty or stale.

ALTER TABLE parsed_media
  ADD COLUMN IF NOT EXISTS ytdlp_formats jsonb NOT NULL DEFAULT '[]'::jsonb;

COMMENT ON COLUMN parsed_media.ytdlp_formats IS
  'Cached subset of yt-dlp info_dict.formats[] populated at parse time. '
  'Enables download stage to skip re-invoking yt-dlp and pull stream m4s '
  'segments directly via httpx + ffmpeg merge. See migration 216 header '
  'for the 2026-05-13 incident that motivated this.';
