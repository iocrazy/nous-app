-- 448: 0-5 star rating on inspiration notes.
--
-- Mirrors the resources.rating column added by migration 074 (same type, same
-- CHECK, same DEFAULT 0) so the shared frontend RatingStars component and the
-- Optional[int] ge=0 le=5 schema idiom carry over unchanged.
--
-- Nullable with DEFAULT 0: PostgreSQL backfills existing rows with the default
-- on ADD COLUMN, so pre-448 notes read back as 0 (unrated), not NULL.
--
-- No index: this release ships rate + display only. A partial index like
-- 074's idx_resources_rating belongs with the rating FILTER, not before it.

ALTER TABLE public.inspiration_notes
  ADD COLUMN IF NOT EXISTS rating SMALLINT
    CHECK (rating >= 0 AND rating <= 5) DEFAULT 0;

NOTIFY pgrst, 'reload schema';
