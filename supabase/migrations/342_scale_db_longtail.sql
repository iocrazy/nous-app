-- 342_scale_db_longtail.sql — million-files P4 (design:
-- docs/superpowers/specs/2026-07-06-million-files-single-user-design.md)
--
-- ① pg_trgm GIN index on resources.filename: the library's server-side
--    keyword search is `filename ILIKE '%term%'` (buildResourceItemsQuery /
--    search_scope_resources) — a sequential scan at 1M rows (~seconds).
--    A trigram index serves the same ILIKE in ~10ms with ZERO code change.
--
--    Plain CREATE INDEX (not CONCURRENTLY): the CI migration runner may wrap
--    statements in a transaction, where CONCURRENTLY is illegal. Today's
--    tables are small (lock is sub-second); a future million-row DB that
--    needs a rebuild can do so manually with CONCURRENTLY.
--
-- ② count_scope_resources capped at 100k: an exact COUNT over a million-row
--    scope costs hundreds of ms and the sidebar calls it on every scope
--    switch. Beyond the cap the true number stops being useful — the UI
--    shows "100,000+". The subquery LIMIT bounds the work at O(cap).

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS idx_resources_filename_trgm
  ON public.resources USING gin (filename gin_trgm_ops);

-- Notes (resources.notes is the other ILIKE target in the search fragment).
CREATE INDEX IF NOT EXISTS idx_resources_notes_trgm
  ON public.resources USING gin (notes gin_trgm_ops);

-- Cap the sidebar count at 100k (returns at most 100_001 so the client can
-- detect "more than the cap" and render 100,000+).
CREATE OR REPLACE FUNCTION public.count_scope_resources(
  p_scope_id text,
  p_web boolean
) RETURNS bigint
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = public
AS $$
  SELECT count(*) FROM (
    SELECT DISTINCT ri.resource_id
    FROM public.resource_items ri
    JOIN public.resources r ON r.id = ri.resource_id
    WHERE ri.scope_id = p_scope_id::bigint
      AND r.is_trashed = false
      AND (CASE WHEN p_web THEN r.source_type = 'web'
                ELSE r.source_type IS DISTINCT FROM 'web' END)
    LIMIT 100001
  ) capped;
$$;

GRANT EXECUTE ON FUNCTION public.count_scope_resources(text, boolean) TO authenticated;

NOTIFY pgrst, 'reload schema';
