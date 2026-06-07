-- Scale-safe distinct count of resources in a team scope.
--
-- The sidebar "My Uploads" / "My Downloads" badges for a TEAM scope were computed
-- by fetching resource_items.resource_id for the scope (a list silently capped at
-- PostgREST's 1000-row ceiling) and counting resources `.in(those ids)`. Past 1000
-- items the count was wrong AND the giant `.in(...)` URL risked a Kong/nginx 502.
-- PostgREST also can't COUNT(DISTINCT ...), and a plain head:true count over the
-- junction double-counts a resource that sits in multiple folders within one scope.
--
-- This RPC does the COUNT(DISTINCT) in SQL — exact at any scale, one round-trip.
-- SECURITY INVOKER so the caller's RLS applies (a member counts only their team's
-- rows; the frontend already relies on resource_items RLS for the same visibility).
-- scope_id is passed as text and cast to avoid JS BIGINT precision loss (Snowflake
-- ids exceed 2^53).

CREATE OR REPLACE FUNCTION public.count_scope_resources(
  p_scope_id text,
  p_web boolean
) RETURNS bigint
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = public
AS $$
  SELECT count(DISTINCT ri.resource_id)
  FROM public.resource_items ri
  JOIN public.resources r ON r.id = ri.resource_id
  WHERE ri.scope_id = p_scope_id::bigint
    AND r.is_trashed = false
    AND (CASE WHEN p_web THEN r.source_type = 'web'
              ELSE r.source_type IS DISTINCT FROM 'web' END);
$$;

GRANT EXECUTE ON FUNCTION public.count_scope_resources(text, boolean) TO authenticated;

NOTIFY pgrst, 'reload schema';
