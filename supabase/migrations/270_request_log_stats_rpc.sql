-- 270_request_log_stats_rpc.sql
--
-- Scale-correct aggregation for the admin "request log stats" endpoint.
--
-- The endpoint (`GET /admin/request-logs/stats`) used to fetch EVERY
-- api_request_logs row since a cutoff (`stats_since`) and aggregate in Python.
-- Two scale failure modes:
--   * REST path (PostgREST) silently caps the fetch at 1000 rows → stats are
--     computed from at most 1000 logs → wrong/undercounted at any real volume.
--   * ORM path has no cap → fetches the entire window (53k+ rows and growing)
--     into the app every dashboard load → unbounded fetch / memory blow-up.
--
-- This RPC pushes the whole aggregation into SQL (GROUP BY), returning only the
-- small aggregated payload. Correct at any scale, one round-trip, no row dump.
-- Both the REST and ORM repos call it, so the result is identical regardless of
-- the USE_ORM_ADMIN_REQUEST_LOGS flag.
--
-- Shape mirrors RequestLogStats (by_method / by_status / top_paths / by_hour /
-- total) exactly, including the existing semantics:
--   * by_method:  COALESCE(method,'UNKNOWN'), ordered by count desc
--   * by_status:  "<n>xx" groups (status_code/100), NULL status excluded
--   * top_paths:  top 20 paths by count; avg over NON-zero response times,
--                 floored to int, 0 when no non-zero samples (matches the old
--                 `int(sum(t for t in times if t)/max(len(...),1))`)
--   * by_hour:    "YYYY-MM-DDTHH" UTC bucket (matches the old `ts[:13]` slice)
--
-- SECURITY DEFINER (owner postgres) so admin stats see all rows regardless of
-- RLS — mirrors the service-role REST path it replaces.

CREATE OR REPLACE FUNCTION public.rpc_request_log_stats(p_since timestamptz)
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  WITH base AS (
    SELECT method, status_code, path, response_time_ms, "timestamp" AS ts
    FROM public.api_request_logs
    WHERE "timestamp" >= p_since
  )
  SELECT jsonb_build_object(
    'total', (SELECT count(*) FROM base),
    'by_method', COALESCE((
      SELECT jsonb_agg(jsonb_build_object('method', m, 'count', c) ORDER BY c DESC, m)
      FROM (
        SELECT COALESCE(method, 'UNKNOWN') AS m, count(*) AS c
        FROM base GROUP BY 1
      ) q
    ), '[]'::jsonb),
    'by_status', COALESCE((
      SELECT jsonb_agg(jsonb_build_object('status_group', g, 'count', c) ORDER BY g)
      FROM (
        SELECT (status_code / 100)::text || 'xx' AS g, count(*) AS c
        FROM base WHERE status_code IS NOT NULL GROUP BY 1
      ) q
    ), '[]'::jsonb),
    'top_paths', COALESCE((
      SELECT jsonb_agg(
        jsonb_build_object('path', p, 'count', c, 'avg_response_time_ms', a)
        ORDER BY c DESC, p
      )
      FROM (
        SELECT
          COALESCE(path, '') AS p,
          count(*) AS c,
          COALESCE(
            floor(
              avg(response_time_ms) FILTER (
                WHERE response_time_ms IS NOT NULL AND response_time_ms <> 0
              )
            )::int,
            0
          ) AS a
        FROM base
        GROUP BY 1
        ORDER BY count(*) DESC
        LIMIT 20
      ) q
    ), '[]'::jsonb),
    'by_hour', COALESCE((
      SELECT jsonb_agg(jsonb_build_object('hour', h, 'count', c) ORDER BY h)
      FROM (
        SELECT to_char(ts AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24') AS h, count(*) AS c
        FROM base GROUP BY 1
      ) q
    ), '[]'::jsonb)
  );
$$;

GRANT EXECUTE ON FUNCTION public.rpc_request_log_stats(timestamptz) TO authenticated, service_role;

NOTIFY pgrst, 'reload schema';
