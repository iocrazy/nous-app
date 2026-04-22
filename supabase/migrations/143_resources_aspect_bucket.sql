-- Migration 143: add generated column aspect_bucket + index to resources.
-- Lets server-side filters push the Aspect chip into PostgREST queries.
--
-- Buckets (client ids → DB value):
--   portrait   ratio ∈ (0.5,  0.6)    9:16
--   landscape  ratio ∈ (1.7,  1.85)   16:9
--   square     ratio ∈ (0.95, 1.05)   1:1
--   fourThree  ratio ∈ (1.28, 1.4)    4:3
--   other      everything else (incl. null/unparseable)
--
-- The resolution column carries strings like "1920x1080" or "1920:1080"
-- in practice — regex matches both separators. Ratios are computed in
-- NUMERIC (not float) so the GENERATED column is deterministic.

-- 1. Immutable helper that parses "WIDTH{x|X|×|:}HEIGHT" into a bucket label.
--    STABLE + STRICT would normally be fine, but generated STORED columns
--    require IMMUTABLE.
CREATE OR REPLACE FUNCTION public.resource_aspect_bucket(res TEXT)
RETURNS TEXT
LANGUAGE plpgsql
IMMUTABLE
PARALLEL SAFE
AS $$
DECLARE
  m        TEXT[];
  w        NUMERIC;
  h        NUMERIC;
  ratio    NUMERIC;
BEGIN
  IF res IS NULL THEN
    RETURN 'other';
  END IF;
  m := regexp_match(res, '^\s*(\d+)\s*[xX×:]\s*(\d+)\s*$');
  IF m IS NULL THEN
    RETURN 'other';
  END IF;
  w := m[1]::NUMERIC;
  h := m[2]::NUMERIC;
  IF w <= 0 OR h <= 0 THEN
    RETURN 'other';
  END IF;
  ratio := w / h;
  IF ratio > 0.5 AND ratio < 0.6 THEN
    RETURN '9:16';
  ELSIF ratio > 1.7 AND ratio < 1.85 THEN
    RETURN '16:9';
  ELSIF ratio > 0.95 AND ratio < 1.05 THEN
    RETURN '1:1';
  ELSIF ratio > 1.28 AND ratio < 1.4 THEN
    RETURN '4:3';
  ELSE
    RETURN 'other';
  END IF;
END;
$$;

-- 2. Generated stored column — idempotent via DO block (ALTER TABLE ADD
--    COLUMN IF NOT EXISTS doesn't exist for generated columns on some
--    PG versions; do the check manually).
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema='public' AND table_name='resources'
      AND column_name='aspect_bucket'
  ) THEN
    ALTER TABLE public.resources
      ADD COLUMN aspect_bucket TEXT
      GENERATED ALWAYS AS (public.resource_aspect_bucket(resolution)) STORED;
  END IF;
END$$;

-- 3. Index for the ``.in('aspect_bucket', ...)`` filter.
CREATE INDEX IF NOT EXISTS idx_resources_aspect_bucket
  ON public.resources(aspect_bucket);

-- 4. Sanity: confirm the known values exist (non-empty distinct set).
--    SELECT DISTINCT aspect_bucket, COUNT(*) FROM public.resources GROUP BY 1;
