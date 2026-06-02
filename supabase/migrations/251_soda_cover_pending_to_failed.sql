-- 251: clear stuck qishui audio "Cover Downloading..." spinners.
--
-- The soda download workflow's best-effort cover step left
-- cover_download_status='pending' forever when the cover was skipped (no
-- album.url_cover) or its server-side download failed. The UI reads pending as
-- "Cover Downloading..." and spins indefinitely. The workflow is fixed to mark
-- such covers 'failed' (terminal → UI shows "Retry Cover"); this backfills the
-- rows that already got stuck before the fix shipped.
--
-- Scope: only qishui (Soda) audio rows whose cover never landed on disk
-- (cover_download_path IS NULL) and are still pending. Idempotent.

UPDATE parsed_media
SET cover_download_status = 'failed'
WHERE source_platform = 'qishui'
  AND media_type = 'audio'
  AND cover_download_status = 'pending'
  AND cover_download_path IS NULL;
