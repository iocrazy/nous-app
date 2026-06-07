-- 271_monitoring_stats_rpc.sql
--
-- Scale-correct aggregation for the admin monitoring dashboard
-- (`GET /admin/monitoring/stats`).
--
-- The endpoint used to fetch ALL api_request_logs (capped 10000) + ALL
-- application_logs (capped 5000) for the window and compute 7 stat sections in
-- Python. At 100k+ log volume the REST caps make every number an undercount
-- (computed from at most the first 1000/N rows), and the ORM path would pull
-- the whole window into the app each load.
--
-- This RPC computes all 7 sections in SQL over the FULL window and returns only
-- the small aggregated payload. The numbers become *correct* at scale (not a
-- byte-for-byte match of the previously-capped Python output) — that is the
-- intent of the scale-Tier-2 fix. Only the time-bucket key formats are kept
-- identical to the old `_bucket_key` so the dashboard chart x-axis is unchanged:
--   * bucket >= 1440 min  -> "YYYY-MM-DDT00:00"  (per-day)
--   * bucket >= 60 min    -> "YYYY-MM-DDTHH:00"  (hour-aligned)
--   * else                -> "YYYY-MM-DDTHH:MI"  (minute-aligned)
-- Buckets are aligned to each row's UTC midnight (strides 5/60/360 all divide a
-- day), matching the old minute-from-midnight truncation.
--
-- p95 uses percentile_disc (a standard discrete percentile) rather than the old
-- ad-hoc index formula — correctness over bug-parity.
--
-- SECURITY DEFINER (owner postgres) so admin stats see all rows regardless of
-- RLS — mirrors the service-role REST path it replaces.

CREATE OR REPLACE FUNCTION public.rpc_monitoring_stats(
  p_start          timestamptz,
  p_end            timestamptz,
  p_bucket_minutes int
)
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  WITH req AS (
    SELECT path, status_code, response_time_ms, "timestamp" AS ts
    FROM public.api_request_logs
    WHERE "timestamp" >= p_start AND "timestamp" <= p_end
  ),
  app AS (
    SELECT level, module, message, logged_at
    FROM public.application_logs
    WHERE logged_at >= p_start AND logged_at <= p_end
  ),
  req_agg AS (
    SELECT
      count(*)                                                      AS total_requests,
      count(*) FILTER (WHERE COALESCE(status_code, 0) >= 400)       AS error_requests,
      COALESCE(sum(COALESCE(response_time_ms, 0)), 0)               AS sum_ms
    FROM req
  ),
  trend AS (
    SELECT
      CASE
        WHEN p_bucket_minutes >= 1440 THEN
          to_char(date_trunc('day', ts AT TIME ZONE 'UTC'), 'YYYY-MM-DD"T"00:00')
        WHEN p_bucket_minutes >= 60 THEN
          to_char(
            date_bin(make_interval(mins => p_bucket_minutes),
                     ts AT TIME ZONE 'UTC',
                     date_trunc('day', ts AT TIME ZONE 'UTC')),
            'YYYY-MM-DD"T"HH24:00')
        ELSE
          to_char(
            date_bin(make_interval(mins => p_bucket_minutes),
                     ts AT TIME ZONE 'UTC',
                     date_trunc('day', ts AT TIME ZONE 'UTC')),
            'YYYY-MM-DD"T"HH24:MI')
      END AS k,
      count(*)                                                AS requests,
      count(*) FILTER (WHERE COALESCE(status_code, 0) >= 400) AS errors
    FROM req
    GROUP BY 1
  ),
  slow AS (
    SELECT
      COALESCE(path, '')                                          AS p,
      round(avg(response_time_ms), 1)                             AS avg_ms,
      percentile_disc(0.95) WITHIN GROUP (ORDER BY response_time_ms) AS p95_ms,
      count(*)                                                    AS c
    FROM req
    WHERE response_time_ms IS NOT NULL AND path IS NOT NULL AND path <> ''
    GROUP BY 1
    ORDER BY avg(response_time_ms) DESC
    LIMIT 10
  ),
  err_ep AS (
    SELECT
      COALESCE(path, '')                          AS p,
      count(*)                                     AS c,
      (array_agg(status_code ORDER BY ts DESC))[1] AS last_status
    FROM req
    WHERE COALESCE(status_code, 0) >= 400
    GROUP BY 1
    ORDER BY count(*) DESC
    LIMIT 10
  ),
  level_dist AS (
    SELECT COALESCE(level, 'UNKNOWN') AS lvl, count(*) AS c
    FROM app GROUP BY 1
  ),
  mod_err AS (
    SELECT
      CASE
        WHEN COALESCE(module, 'unknown') LIKE '%.%'
          THEN reverse(split_part(reverse(module), '.', 1))
        ELSE COALESCE(module, 'unknown')
      END                AS m,
      count(*)           AS c
    FROM app
    WHERE level IN ('ERROR', 'CRITICAL', 'WARNING')
    GROUP BY 1
    ORDER BY count(*) DESC
    LIMIT 10
  ),
  recent AS (
    SELECT
      level,
      NULLIF(
        CASE
          WHEN module LIKE '%.%' THEN reverse(split_part(reverse(module), '.', 1))
          ELSE COALESCE(module, '')
        END, ''
      )                       AS module,
      COALESCE(message, '')   AS message,
      logged_at
    FROM app
    WHERE level IN ('ERROR', 'CRITICAL')
    ORDER BY logged_at DESC
    LIMIT 5
  )
  SELECT jsonb_build_object(
    'overview', jsonb_build_object(
      'total_requests', (SELECT total_requests FROM req_agg),
      'error_rate', (
        SELECT CASE WHEN total_requests > 0
                    THEN round(error_requests::numeric / total_requests * 100, 2)
                    ELSE 0 END
        FROM req_agg
      ),
      'avg_response_ms', (
        SELECT CASE WHEN total_requests > 0
                    THEN round(sum_ms::numeric / total_requests, 1)
                    ELSE 0 END
        FROM req_agg
      ),
      'app_error_count', (
        SELECT count(*) FROM app WHERE level IN ('ERROR', 'CRITICAL')
      ),
      'frontend_error_count', (
        SELECT count(*) FROM public.frontend_error_logs
        WHERE created_at >= p_start AND created_at <= p_end
      )
    ),
    'request_trend', COALESCE((
      SELECT jsonb_agg(
        jsonb_build_object('time', k, 'requests', requests, 'errors', errors)
        ORDER BY k
      ) FROM trend
    ), '[]'::jsonb),
    'top_slow_apis', COALESCE((
      SELECT jsonb_agg(
        jsonb_build_object('path', p, 'avg_ms', avg_ms, 'p95_ms', p95_ms, 'count', c)
        ORDER BY avg_ms DESC, p
      ) FROM slow
    ), '[]'::jsonb),
    'top_error_endpoints', COALESCE((
      SELECT jsonb_agg(
        jsonb_build_object('path', p, 'error_count', c, 'last_status', last_status)
        ORDER BY c DESC, p
      ) FROM err_ep
    ), '[]'::jsonb),
    'log_level_distribution', COALESCE(
      (SELECT jsonb_object_agg(lvl, c) FROM level_dist), '{}'::jsonb
    ),
    'top_error_modules', COALESCE((
      SELECT jsonb_agg(
        jsonb_build_object('module', m, 'count', c)
        ORDER BY c DESC, m
      ) FROM mod_err
    ), '[]'::jsonb),
    'recent_errors', COALESCE((
      SELECT jsonb_agg(
        jsonb_build_object(
          'level', level, 'module', module,
          'message', message, 'logged_at', logged_at
        ) ORDER BY logged_at DESC
      ) FROM recent
    ), '[]'::jsonb)
  );
$$;

GRANT EXECUTE ON FUNCTION public.rpc_monitoring_stats(timestamptz, timestamptz, int)
  TO authenticated, service_role;

NOTIFY pgrst, 'reload schema';
