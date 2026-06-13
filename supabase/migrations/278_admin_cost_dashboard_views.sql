-- 278_admin_cost_dashboard_views.sql
-- ============================================================================
-- Phase 0.5-D of Canvas + AI Infrastructure 17-week upgrade plan.
-- See docs/plans/canvas-ai-upgrade-plan.md v1.2 Appendix A.
--
-- 7 admin dashboard MVP views (the ⭐ tier from Appendix A):
--   1. v_admin_today_total_cost            — A.1   Today's total cost (USD + local)
--   2. v_admin_cost_by_provider_30d        — B.1   Cost share by provider
--   3. v_admin_cost_by_agent_slug_30d      — B.4   Cost share by agent slug
--   4. v_admin_top_users_cost_30d          — C.1   Top users by cost
--   5. v_admin_cache_hit_rate_30d          — D.1   Prompt-cache hit rate
--   6. v_admin_cost_anomalies_24h          — D.3   Anomaly alerts (avg+3σ)
--   7. v_admin_outcome_distribution_30d    — E.1   Outcome distribution
--
-- All views read agent_run_events.cost_snapshot (added by migration 277).
-- Rows where cost_snapshot IS NULL (legacy / pre-277) are filtered out.
-- The GIN index from 277 makes JSONB path lookups fast.
--
-- Views are owned by the migration runner (service_role). The admin app
-- already proxies through service_role, so no per-view RLS is needed.
-- Front-end clients (anon / authenticated) cannot SELECT from these views.
-- ============================================================================


-- ========================================================================
-- 1. v_admin_today_total_cost — A.1
-- ========================================================================
-- Today's spend rolled up. Two rows: one for USD aggregate, one per local
-- currency seen in cost_snapshot.cost.local_currency.
-- "Boss's first question every day."
-- ========================================================================

CREATE OR REPLACE VIEW v_admin_today_total_cost AS
WITH today AS (
  SELECT
    (cost_snapshot->'cost'->>'discounted_usd')::NUMERIC             AS discounted_usd,
    (cost_snapshot->'cost'->>'raw_usd')::NUMERIC                    AS raw_usd,
    (cost_snapshot->'cost'->>'saved_by_cache_usd')::NUMERIC         AS saved_by_cache_usd,
    (cost_snapshot->'cost'->>'local_cents')::NUMERIC                AS local_cents,
    cost_snapshot->'cost'->>'local_currency'                        AS local_currency
  FROM agent_run_events
  WHERE cost_snapshot IS NOT NULL
    AND created_at >= date_trunc('day', now() AT TIME ZONE 'UTC')
    AND created_at <  date_trunc('day', now() AT TIME ZONE 'UTC') + INTERVAL '1 day'
)
SELECT
  COALESCE(local_currency, 'USD')             AS currency,
  COUNT(*)                                    AS call_count,
  SUM(discounted_usd)                         AS total_usd,
  SUM(raw_usd)                                AS raw_usd,
  SUM(saved_by_cache_usd)                     AS saved_by_cache_usd,
  SUM(local_cents) / 100.0                    AS total_local,
  ROUND(
    100.0 * SUM(saved_by_cache_usd) /
    NULLIF(SUM(raw_usd), 0), 2
  )                                           AS cache_savings_pct
FROM today
GROUP BY GROUPING SETS ((local_currency), ());

COMMENT ON VIEW v_admin_today_total_cost IS
  'Phase 0.5-D Appendix A.1. Today total cost, USD + per local currency.
   GROUPING SETS gives one row per currency + one rollup row (currency=USD).';


-- ========================================================================
-- 2. v_admin_cost_by_provider_30d — B.1
-- ========================================================================
-- Last 30 days, group by provider_slug.
-- "Anthropic vs Qwen vs Jimeng vs nous-center."
-- ========================================================================

CREATE OR REPLACE VIEW v_admin_cost_by_provider_30d AS
SELECT
  cost_snapshot->>'provider_slug'                            AS provider_slug,
  COUNT(*)                                                   AS call_count,
  SUM((cost_snapshot->'cost'->>'discounted_usd')::NUMERIC)   AS total_usd,
  SUM((cost_snapshot->'cost'->>'saved_by_cache_usd')::NUMERIC) AS saved_by_cache_usd,
  SUM((cost_snapshot->'tokens'->>'input')::BIGINT
      + (cost_snapshot->'tokens'->>'output')::BIGINT
      + (cost_snapshot->'tokens'->>'cache_read')::BIGINT
      + (cost_snapshot->'tokens'->>'cache_write')::BIGINT)   AS total_tokens,
  ROUND(
    100.0 * SUM((cost_snapshot->'cost'->>'discounted_usd')::NUMERIC) /
    NULLIF(SUM(SUM((cost_snapshot->'cost'->>'discounted_usd')::NUMERIC)) OVER (), 0),
    2
  )                                                          AS share_pct
FROM agent_run_events
WHERE cost_snapshot IS NOT NULL
  AND created_at >= now() - INTERVAL '30 days'
GROUP BY cost_snapshot->>'provider_slug'
ORDER BY total_usd DESC NULLS LAST;

COMMENT ON VIEW v_admin_cost_by_provider_30d IS
  'Phase 0.5-D Appendix B.1. Last 30 days cost share by provider_slug.
   share_pct uses window function to compute % of total.';


-- ========================================================================
-- 3. v_admin_cost_by_agent_slug_30d — B.4
-- ========================================================================
-- Last 30 days, group by ai_agents.slug. Tells you which agent's prompt
-- should be optimized first.
-- ========================================================================

CREATE OR REPLACE VIEW v_admin_cost_by_agent_slug_30d AS
SELECT
  COALESCE(a.slug, '(unknown)')                              AS agent_slug,
  a.name                                                     AS agent_name,
  COUNT(e.*)                                                 AS call_count,
  COUNT(DISTINCT e.run_id)                                   AS run_count,
  SUM((e.cost_snapshot->'cost'->>'discounted_usd')::NUMERIC) AS total_usd,
  AVG((e.cost_snapshot->'cost'->>'discounted_usd')::NUMERIC) AS avg_per_call_usd,
  SUM((e.cost_snapshot->'tokens'->>'input')::BIGINT
      + (e.cost_snapshot->'tokens'->>'output')::BIGINT
      + (e.cost_snapshot->'tokens'->>'cache_read')::BIGINT
      + (e.cost_snapshot->'tokens'->>'cache_write')::BIGINT) AS total_tokens
FROM agent_run_events e
JOIN agent_runs r ON r.id = e.run_id
LEFT JOIN ai_agents a ON a.id = r.agent_id
WHERE e.cost_snapshot IS NOT NULL
  AND e.created_at >= now() - INTERVAL '30 days'
GROUP BY a.slug, a.name
ORDER BY total_usd DESC NULLS LAST;

COMMENT ON VIEW v_admin_cost_by_agent_slug_30d IS
  'Phase 0.5-D Appendix B.4. Last 30 days cost by ai_agents.slug.
   Includes per-call avg so the highest-impact prompts surface.';


-- ========================================================================
-- 4. v_admin_top_users_cost_30d — C.1
-- ========================================================================
-- Last 30 days, group by agent_runs.user_id. 80/20 finder for power users.
-- Limit applied at query time (ORDER BY ... LIMIT 10/50).
-- ========================================================================

CREATE OR REPLACE VIEW v_admin_top_users_cost_30d AS
SELECT
  r.user_id,
  r.team_id,
  COUNT(e.*)                                                 AS call_count,
  COUNT(DISTINCT e.run_id)                                   AS run_count,
  SUM((e.cost_snapshot->'cost'->>'discounted_usd')::NUMERIC) AS total_usd,
  MAX(e.created_at)                                          AS last_call_at
FROM agent_run_events e
JOIN agent_runs r ON r.id = e.run_id
WHERE e.cost_snapshot IS NOT NULL
  AND e.created_at >= now() - INTERVAL '30 days'
GROUP BY r.user_id, r.team_id
ORDER BY total_usd DESC NULLS LAST;

COMMENT ON VIEW v_admin_top_users_cost_30d IS
  'Phase 0.5-D Appendix C.1. Last 30 days cost per user (with team).
   Admin app applies LIMIT 10 / 50. Already ordered DESC by total_usd.';


-- ========================================================================
-- 5. v_admin_cache_hit_rate_30d — D.1
-- ========================================================================
-- Prompt cache hit rate over last 30 days, daily + rolled up.
-- High hit rate = high savings. Cache_read tokens are charged at fraction
-- of normal input rate (typically 10%), so each cached token saves money.
-- ========================================================================

CREATE OR REPLACE VIEW v_admin_cache_hit_rate_30d AS
WITH per_event AS (
  SELECT
    date_trunc('day', created_at) AS day,
    (cost_snapshot->'tokens'->>'input')::BIGINT      AS input_tokens,
    (cost_snapshot->'tokens'->>'cache_read')::BIGINT AS cache_read_tokens,
    (cost_snapshot->'cost'->>'saved_by_cache_usd')::NUMERIC AS saved_usd
  FROM agent_run_events
  WHERE cost_snapshot IS NOT NULL
    AND created_at >= now() - INTERVAL '30 days'
)
SELECT
  day,
  SUM(input_tokens)                                  AS input_tokens,
  SUM(cache_read_tokens)                             AS cache_read_tokens,
  ROUND(
    100.0 * SUM(cache_read_tokens) /
    NULLIF(SUM(input_tokens + cache_read_tokens), 0), 2
  )                                                  AS hit_rate_pct,
  SUM(saved_usd)                                     AS saved_usd
FROM per_event
GROUP BY GROUPING SETS ((day), ())
ORDER BY day NULLS LAST;

COMMENT ON VIEW v_admin_cache_hit_rate_30d IS
  'Phase 0.5-D Appendix D.1. Daily cache hit rate + 30-day rollup (day=NULL).
   hit_rate_pct = cache_read / (input + cache_read).';


-- ========================================================================
-- 6. v_admin_cost_anomalies_24h — D.3
-- ========================================================================
-- Runs in the last 24h whose per-event cost is > 3σ above the historical
-- mean for the same agent. Useful for catching runaway loops and broken
-- tool calls.
--
-- Baseline window: the same agent's events over the last 30 days
-- (excluding the last 24h). Need at least 30 events to compute σ — agents
-- with fewer history events are skipped.
-- ========================================================================

CREATE OR REPLACE VIEW v_admin_cost_anomalies_24h AS
WITH baseline AS (
  SELECT
    r.agent_id,
    AVG((e.cost_snapshot->'cost'->>'discounted_usd')::NUMERIC) AS mean_usd,
    STDDEV_POP((e.cost_snapshot->'cost'->>'discounted_usd')::NUMERIC) AS stddev_usd,
    COUNT(*) AS sample_size
  FROM agent_run_events e
  JOIN agent_runs r ON r.id = e.run_id
  WHERE e.cost_snapshot IS NOT NULL
    AND e.created_at >= now() - INTERVAL '30 days'
    AND e.created_at <  now() - INTERVAL '24 hours'
  GROUP BY r.agent_id
  HAVING COUNT(*) >= 30
),
recent AS (
  SELECT
    e.id,
    e.run_id,
    e.iteration,
    r.agent_id,
    r.user_id,
    a.slug AS agent_slug,
    (e.cost_snapshot->'cost'->>'discounted_usd')::NUMERIC AS event_usd,
    e.cost_snapshot->>'model_slug' AS model_slug,
    e.created_at
  FROM agent_run_events e
  JOIN agent_runs r ON r.id = e.run_id
  LEFT JOIN ai_agents a ON a.id = r.agent_id
  WHERE e.cost_snapshot IS NOT NULL
    AND e.created_at >= now() - INTERVAL '24 hours'
)
SELECT
  recent.id                                          AS event_id,
  recent.run_id,
  recent.iteration,
  recent.agent_slug,
  recent.model_slug,
  recent.user_id,
  recent.event_usd,
  baseline.mean_usd                                  AS baseline_mean_usd,
  baseline.stddev_usd                                AS baseline_stddev_usd,
  ROUND(
    (recent.event_usd - baseline.mean_usd) /
    NULLIF(baseline.stddev_usd, 0), 2
  )                                                  AS sigmas_above,
  recent.created_at
FROM recent
JOIN baseline ON baseline.agent_id = recent.agent_id
WHERE baseline.stddev_usd > 0
  AND recent.event_usd > baseline.mean_usd + 3 * baseline.stddev_usd
ORDER BY sigmas_above DESC NULLS LAST;

COMMENT ON VIEW v_admin_cost_anomalies_24h IS
  'Phase 0.5-D Appendix D.3. Events from last 24h that are >3σ above the
   agent''s 30-day baseline (baseline excludes the last 24h, needs ≥30
   samples). Catches runaway loops / broken tool calls.';


-- ========================================================================
-- 7. v_admin_outcome_distribution_30d — E.1
-- ========================================================================
-- agent_runs.outcome distribution over last 30 days, with cost rolled up.
-- Tells you what fraction of agent work the user accepts.
-- ========================================================================

CREATE OR REPLACE VIEW v_admin_outcome_distribution_30d AS
WITH run_cost AS (
  SELECT
    r.id           AS run_id,
    r.outcome      AS outcome,
    r.created_at   AS run_created_at,
    SUM((e.cost_snapshot->'cost'->>'discounted_usd')::NUMERIC) AS run_usd
  FROM agent_runs r
  LEFT JOIN agent_run_events e
    ON e.run_id = r.id AND e.cost_snapshot IS NOT NULL
  WHERE r.created_at >= now() - INTERVAL '30 days'
  GROUP BY r.id, r.outcome, r.created_at
)
SELECT
  COALESCE(outcome, '(unmarked)')             AS outcome,
  COUNT(*)                                    AS run_count,
  ROUND(
    100.0 * COUNT(*) / SUM(COUNT(*)) OVER (),
    2
  )                                           AS share_pct,
  SUM(run_usd)                                AS total_usd,
  AVG(run_usd)                                AS avg_per_run_usd
FROM run_cost
GROUP BY outcome
ORDER BY
  CASE COALESCE(outcome, '(unmarked)')
    WHEN 'user_accepted' THEN 1
    WHEN 'pending'       THEN 2
    WHEN 'user_rejected' THEN 3
    WHEN 'timeout'       THEN 4
    ELSE 5
  END;

COMMENT ON VIEW v_admin_outcome_distribution_30d IS
  'Phase 0.5-D Appendix E.1. Last 30 days outcome distribution.
   Captures runs where the user verdict is recorded; (unmarked) bucket
   shows runs with no outcome (legacy or in-flight).';


-- ========================================================================
-- Verification (manual after apply)
-- ========================================================================
-- 1. SELECT * FROM v_admin_today_total_cost;
--      → at least one row (currency='USD' rollup)
-- 2. SELECT provider_slug, total_usd, share_pct
--      FROM v_admin_cost_by_provider_30d;
-- 3. SELECT agent_slug, total_usd FROM v_admin_cost_by_agent_slug_30d
--      LIMIT 10;
-- 4. SELECT user_id, total_usd FROM v_admin_top_users_cost_30d LIMIT 10;
-- 5. SELECT day, hit_rate_pct FROM v_admin_cache_hit_rate_30d
--      WHERE day IS NOT NULL ORDER BY day DESC;
-- 6. SELECT event_id, sigmas_above FROM v_admin_cost_anomalies_24h;
--      → empty until production data has enough variance
-- 7. SELECT outcome, share_pct FROM v_admin_outcome_distribution_30d;
