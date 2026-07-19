-- 372_ai_usage_observability.sql
--
-- W3c: AI cost observability + per-team budget circuit-breaker.
--
-- Design note (route-C discipline, see CLAUDE.md): the repo ALREADY has a
-- per-call token+cost ledger — public.agent_runs, written by RunRecorder on
-- every LLM turn (team_id / project_id / issue_id / agent_id / user_id /
-- model / provider / prompt+completion+cached tokens / total_tokens GENERATED /
-- trigger / cost_cents, priced server-side from ai_model_prices). Rather than
-- stand up a second, duplicate `ai_usage_events` token store (the dual-source
-- pattern route-C was written in blood to avoid), this migration:
--
--   1. Adds a two-level `attribution` dimension to agent_runs
--      (direct_human = a human clicked; rule_owner = a scheduled routine /
--      pipeline advance fired on the owner's behalf). This is the ONE new fact
--      the existing ledger lacked. NOT multica's six-level waterfall.
--
--   2. Adds ai_usage_hourly — a DERIVED rollup CACHE (not a second source of
--      truth). It is upserted in the SAME code path that finishes an
--      agent_runs row (RunRecorder._finish → ai_usage.record_usage), in app
--      code (NOT a DB trigger) so the accumulation logic stays unit-testable.
--      Range queries (the Usage panel) read ONLY this rollup; per-issue drill
--      reads agent_runs by its issue_id index. No endpoint sums both, so no
--      two-tables-answer-the-same-question inconsistency.
--
--   3. Adds team_ai_budgets — per-team monthly spend ceiling stored in DB
--      (config→DB 铁律; never client-side pricing). Over budget → the autopilot
--      scheduler records the fire as skipped and the pipeline relay halts,
--      instead of dispatching more paid work.
--
-- Pricing deliberately reuses ai_model_prices (already DB-backed, already
-- snapshotted per run by RunRecorder). We do NOT add pricing columns to
-- mediahub_models — that would be a third pricing source and a drift hazard.
--
-- Schema-drift gate: the SQLAlchemy models (backend/app/models/usage.py +
-- the AgentRuns.attribution column in agents.py) are updated in the SAME PR.
-- CI reflects the real DB and diffs it against the models.

-- 1. Two-level attribution on the existing per-run ledger. Nullable: legacy
--    rows and any run whose origin we can't classify stay NULL (unclassified).
ALTER TABLE public.agent_runs
    ADD COLUMN IF NOT EXISTS attribution TEXT;

ALTER TABLE public.agent_runs
    DROP CONSTRAINT IF EXISTS agent_runs_attribution_check;
ALTER TABLE public.agent_runs
    ADD CONSTRAINT agent_runs_attribution_check
    CHECK (attribution IS NULL
           OR attribution IN ('direct_human', 'rule_owner'));

COMMENT ON COLUMN public.agent_runs.attribution IS
    'Two-level cost attribution: direct_human (a human initiated this turn) vs
     rule_owner (a scheduled routine or pipeline advance fired it on the
     schedule/pipeline owner''s behalf). Derived from the issue origin_kind at
     dispatch. NULL = legacy / unclassified.';

-- 2. Hourly rollup cache. Derived from agent_runs; upserted by app code in the
--    same transaction-of-thought as the run finishing. Surrogate snowflake PK +
--    a NULLS-NOT-DISTINCT unique key over the attribution dimensions, so the
--    ON CONFLICT accumulate works even when team/project/agent/model are NULL
--    (agentless or context-less calls still roll up into a single bucket).
CREATE TABLE IF NOT EXISTS public.ai_usage_hourly (
    id                  BIGINT       PRIMARY KEY DEFAULT generate_snowflake_id(),
    bucket_hour         TIMESTAMPTZ  NOT NULL,
    team_id             BIGINT,
    project_id          BIGINT,
    agent_id            UUID,
    model               TEXT,
    module              TEXT         NOT NULL,
    attribution         TEXT         NOT NULL,
    prompt_tokens       BIGINT       NOT NULL DEFAULT 0,
    completion_tokens   BIGINT       NOT NULL DEFAULT 0,
    total_tokens        BIGINT       GENERATED ALWAYS AS
                                     (prompt_tokens + completion_tokens) STORED,
    cached_input_tokens BIGINT       NOT NULL DEFAULT 0,
    cost_cents          NUMERIC(18, 6) NOT NULL DEFAULT 0,
    event_count         INTEGER      NOT NULL DEFAULT 0,
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT ai_usage_hourly_attribution_check
        CHECK (attribution IN ('direct_human', 'rule_owner')),
    CONSTRAINT ai_usage_hourly_team_id_fkey
        FOREIGN KEY (team_id) REFERENCES public.teams (id) ON DELETE SET NULL,
    CONSTRAINT ai_usage_hourly_project_id_fkey
        FOREIGN KEY (project_id) REFERENCES public.projects (id) ON DELETE SET NULL,
    CONSTRAINT ai_usage_hourly_agent_id_fkey
        FOREIGN KEY (agent_id) REFERENCES public.ai_agents (id) ON DELETE SET NULL,
    CONSTRAINT ai_usage_hourly_dims_uq
        UNIQUE NULLS NOT DISTINCT
        (bucket_hour, team_id, project_id, agent_id, model, module, attribution)
);

-- Team dashboard: "this team's spend this month, grouped by X".
CREATE INDEX IF NOT EXISTS idx_ai_usage_hourly_team_bucket
    ON public.ai_usage_hourly (team_id, bucket_hour)
    WHERE team_id IS NOT NULL;

COMMENT ON TABLE public.ai_usage_hourly IS
    'Derived hourly rollup of AI token spend, keyed by the attribution
     dimensions. NOT a source of truth — a cache accumulated by
     app.services.ai_usage.record_usage alongside each agent_runs finish. Range
     queries in the Usage panel read here; per-issue drill reads agent_runs.';

-- 3. Per-team monthly budget ceiling. NULL budget = unlimited.
CREATE TABLE IF NOT EXISTS public.team_ai_budgets (
    team_id              BIGINT       PRIMARY KEY,
    monthly_budget_cents NUMERIC(12, 2),
    updated_by_user_id   UUID,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT team_ai_budgets_team_id_fkey
        FOREIGN KEY (team_id) REFERENCES public.teams (id) ON DELETE CASCADE
);

COMMENT ON TABLE public.team_ai_budgets IS
    'Per-team monthly AI spend ceiling in cents (NULL row or NULL budget =
     unlimited). Read by the budget breaker: when the calendar-month spend from
     ai_usage_hourly meets/exceeds this, the autopilot scheduler records fires
     as skipped and pipeline relays halt instead of dispatching paid work.';

COMMENT ON COLUMN public.team_ai_budgets.monthly_budget_cents IS
    'Monthly ceiling in cents. NULL = unlimited (no breaker). Compared against
     the sum of ai_usage_hourly.cost_cents for the current calendar month.';

-- PostgREST schema cache refresh (new tables/columns must be API-visible).
NOTIFY pgrst, 'reload schema';
