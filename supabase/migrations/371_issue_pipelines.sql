-- 371: content relay pipelines (W2b).
--
-- WHAT
-- ----
-- A pipeline is a FIXED, ordered relay of agent steps (e.g. Topic → Script →
-- Storyboard → Final cut → Publish). Running a pipeline against a parent issue
-- creates the step-1 child issue assigned to that step's agent; when a step's
-- child reaches `done`, the next step's child is auto-created and dispatched;
-- all children hang off the same parent issue. This is the sequential fan-OUT
-- half — the existing sub-issue barrier (subissue_barrier.py) already does the
-- fan-IN roll-up when every child goes terminal, and the two compose.
--
-- There is NO LLM "leader picks member" — the order and the agents are static
-- rows in issue_pipeline_steps, edited by a human. That is the whole point of a
-- pipeline versus an ad-hoc delegation.
--
-- IDS: snowflake bigint via generate_snowflake_id() server default (same as
-- teams / projects / issues). No SET ROLE (migration 365 lesson — it drops the
-- privileges needed to touch postgres-owned objects under CI).

-- ── issue_pipelines: the pipeline definition (per team) ──────────────────
CREATE TABLE IF NOT EXISTS public.issue_pipelines (
    id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    team_id BIGINT NOT NULL REFERENCES public.teams (id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    enabled BOOLEAN NOT NULL DEFAULT true,
    created_by_user_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS issue_pipelines_team_idx
    ON public.issue_pipelines (team_id);

-- ── issue_pipeline_steps: the ordered relay steps ────────────────────────
CREATE TABLE IF NOT EXISTS public.issue_pipeline_steps (
    id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    pipeline_id BIGINT NOT NULL
        REFERENCES public.issue_pipelines (id) ON DELETE CASCADE,
    step_order INT NOT NULL,
    agent_id UUID NOT NULL REFERENCES public.ai_agents (id) ON DELETE CASCADE,
    title_template TEXT NOT NULL,
    prompt_template TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (pipeline_id, step_order)
);

CREATE INDEX IF NOT EXISTS issue_pipeline_steps_pipeline_idx
    ON public.issue_pipeline_steps (pipeline_id);

-- ── issue_pipeline_runs: one active relay per parent issue ───────────────
CREATE TABLE IF NOT EXISTS public.issue_pipeline_runs (
    id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    pipeline_id BIGINT NOT NULL
        REFERENCES public.issue_pipelines (id) ON DELETE CASCADE,
    parent_issue_id BIGINT NOT NULL
        REFERENCES public.issues (id) ON DELETE CASCADE,
    current_step INT NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'completed', 'halted', 'cancelled')),
    halted_reason TEXT,
    started_by_user_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS issue_pipeline_runs_parent_idx
    ON public.issue_pipeline_runs (parent_issue_id);

CREATE INDEX IF NOT EXISTS issue_pipeline_runs_pipeline_idx
    ON public.issue_pipeline_runs (pipeline_id);

-- At most ONE running run per parent issue — the 409 guard in
-- pipeline_relay.start_pipeline_run is backed by this DB-level truth.
CREATE UNIQUE INDEX IF NOT EXISTS issue_pipeline_runs_one_running_per_parent
    ON public.issue_pipeline_runs (parent_issue_id)
    WHERE status = 'running';

-- ── widen issues.origin_kind to allow pipeline-step children ─────────────
-- Same DROP+ADD contract as migrations 166 / 367 / 369 — Postgres has no ALTER
-- for a CHECK expression. Pipeline-step children are stamped
-- origin_kind='pipeline', origin_id='pipeline:{run_id}:{step_order}'.
--
-- REMINDER: this enum has FOUR mirrors — this CHECK, models/reviews.py,
-- schemas/issue.py (pinned by test_issue_origin_kind_mirrors), and the TS
-- union in issuesService.ts. All four change together.
ALTER TABLE public.issues
    DROP CONSTRAINT IF EXISTS issues_origin_kind_check;

ALTER TABLE public.issues
    ADD CONSTRAINT issues_origin_kind_check CHECK (
        origin_kind IN (
            'manual',
            'chat_delegate',
            'celery_pipeline',
            'agent_dispatch',
            'routine',
            'escalation',
            'project_stage',
            'publish',
            'pipeline'
        )
    );

NOTIFY pgrst, 'reload schema';
