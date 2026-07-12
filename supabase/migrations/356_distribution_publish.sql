-- Migration 356: Distribution — publish tasks + per-account results (PR-D2)
--
-- publish_tasks: one publish batch (multi-resource × multi-account). The
-- EXECUTION phase (queued/processing/completed/failed) lives in task_tracking
-- (路线 C) keyed by dbos_workflow_id — this table stores only business
-- decoration + the publish config. Never duplicate phase/status here.
--
-- publish_task_accounts: one row per (task, account). status is the BUSINESS
-- state (includes platform-specific 'pending_share' for the H5 handoff), which
-- business code writes directly — distinct from the DBOS phase. share_id +
-- channel support the dual-channel publish: 'official' (open API → item_id) or
-- 'h5' (schema URL → user finishes on phone → webhook flips pending_share to
-- success). share_id is the webhook's lookup key (the prototype kept it on the
-- task; here it is per-account since H5 is a per-account handoff).
--
-- Idempotent: IF NOT EXISTS everywhere; re-apply is a no-op.

BEGIN;

CREATE TABLE IF NOT EXISTS public.publish_tasks (
    id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    user_id UUID NOT NULL,
    team_id BIGINT,                         -- set when the batch targets team-scoped accounts
    content_type VARCHAR(10) NOT NULL DEFAULT 'video'
        CHECK (content_type IN ('video', 'images', 'article')),
    resource_ids JSONB NOT NULL DEFAULT '[]'::jsonb,   -- references resources.id (as strings)
    title TEXT NOT NULL,
    description TEXT,
    topics JSONB NOT NULL DEFAULT '[]'::jsonb,
    cover_vertical_resource_id BIGINT,
    cover_horizontal_resource_id BIGINT,
    visibility VARCHAR(10) NOT NULL DEFAULT 'public'
        CHECK (visibility IN ('public', 'friends', 'private')),
    ai_content BOOLEAN NOT NULL DEFAULT FALSE,
    allow_download BOOLEAN NOT NULL DEFAULT TRUE,
    distribution_mode VARCHAR(12) NOT NULL DEFAULT 'broadcast'
        CHECK (distribution_mode IN ('broadcast', 'one_to_one')),
    scheduled_at TIMESTAMPTZ,               -- D3 scheduling; D2 always NULL (immediate)
    dbos_workflow_id TEXT,                  -- links to task_tracking (execution source)
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_publish_tasks_user
    ON public.publish_tasks (user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS public.publish_task_accounts (
    id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    task_id BIGINT NOT NULL REFERENCES public.publish_tasks (id) ON DELETE CASCADE,
    account_id BIGINT NOT NULL REFERENCES public.social_accounts (id) ON DELETE CASCADE,
    resource_id BIGINT,                     -- one_to_one assignment; NULL in broadcast
    channel VARCHAR(10) NOT NULL DEFAULT 'h5'
        CHECK (channel IN ('official', 'h5')),
    title TEXT,                             -- per-account override (NULL = inherit task)
    description TEXT,
    topics JSONB,
    share_id TEXT,                          -- H5 webhook lookup key (per account)
    status VARCHAR(16) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'pending_share', 'publishing',
                          'success', 'failed', 'cancelled')),
    error_message TEXT,
    published_url TEXT,
    platform_item_id TEXT,
    published_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_publish_task_accounts_task
    ON public.publish_task_accounts (task_id);
-- Unique + fast webhook lookup by share_id (partial: only H5 rows carry one).
CREATE UNIQUE INDEX IF NOT EXISTS uq_publish_task_accounts_share_id
    ON public.publish_task_accounts (share_id) WHERE share_id IS NOT NULL;

-- Backend-only tables: reached solely by service_role repository code, never via
-- PostgREST from the frontend. Lock down with the same service-role-only pattern
-- as 351_distribution_accounts.sql (see also 265's log-table lockdown).
ALTER TABLE public.publish_tasks ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Service role full access on publish_tasks" ON public.publish_tasks;
CREATE POLICY "Service role full access on publish_tasks" ON public.publish_tasks FOR ALL
  USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');

ALTER TABLE public.publish_task_accounts ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Service role full access on publish_task_accounts" ON public.publish_task_accounts;
CREATE POLICY "Service role full access on publish_task_accounts" ON public.publish_task_accounts FOR ALL
  USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');

NOTIFY pgrst, 'reload schema';

COMMIT;
