-- 255: add parsed_media.download_retry_count for retry give-up.
--
-- The hourly retry_failed_downloads job re-dispatches FAILED downloads. With
-- no attempt counter, a permanently-unrecoverable download (e.g. a deleted
-- douyin slide gallery — "slides download failed: 0/1 files") is re-attempted
-- every hour forever, churning the worker and the logs. This column lets the
-- job give up after N attempts (DOWNLOAD_MAX_RETRY_ATTEMPTS, default 5):
-- the collect step increments it on each re-dispatch and skips rows that have
-- reached the cap (they stay video_download_status='failed', just no longer
-- retried).
--
-- Read/written by the asyncpg media repo (SELECT * / dynamic UPDATE RETURNING
-- *), so no PostgREST schema reload is needed for the retry path. NOT NULL
-- DEFAULT 0 is a catalog-only change in PG11+ (no table rewrite). Idempotent.

ALTER TABLE parsed_media
  ADD COLUMN IF NOT EXISTS download_retry_count INTEGER NOT NULL DEFAULT 0;
