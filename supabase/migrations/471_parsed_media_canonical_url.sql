-- 471: parsed_media.canonical_url — a dedup key that survives share-link
-- tracking parameters.
--
-- The "already in your library" probe compared `original_url` for exact
-- string equality. Share links carry analytics parameters that differ every
-- time the same video is opened from a different surface
-- (`spm_id_from=333.1007.tianma.1-2-2.click` vs `spm_id_from=333.1391.0.0`
-- for one real bilibili video, 2026-09-08 vs 2026-09-15), so the same content
-- produced different keys and the probe reported "never downloaded" — a full
-- re-parse plus a Download task the in-workflow cache check then discarded.
--
-- The column stays NULL for rows written before the backfill runs; every
-- reader pairs it with the legacy `original_url` equality so a NULL degrades
-- to the old behaviour rather than to a miss. Values are produced by
-- `app/utils/url_canonical.canonical_url` — deliberately NOT
-- reimplemented in SQL, because two implementations of a dedup key drift
-- silently (the only symptom is a redundant download).
--
-- NOT a replacement for `original_url`: downloads keep fetching the URL the
-- user submitted, tokens and all.

ALTER TABLE public.parsed_media
    ADD COLUMN IF NOT EXISTS canonical_url TEXT;

COMMENT ON COLUMN public.parsed_media.canonical_url IS
    'Dedup key derived from original_url (tracking params stripped, host and '
    'scheme lowercased, query sorted). NULL until backfilled. Never used to '
    'fetch — see app/utils/url_canonical.py.';

CREATE INDEX IF NOT EXISTS idx_parsed_media_canonical_url
    ON public.parsed_media (canonical_url);
