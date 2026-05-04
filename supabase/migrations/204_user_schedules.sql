-- 204_user_schedules.sql
--
-- User-defined schedules — let users (or system) configure cron-style
-- recurring tasks WITHOUT shipping new code each time. Today every
-- scheduled job is a hard-coded `@DBOS.scheduled` decorator
-- (scheduled_health, scheduled_recovery, etc); ops have no way to
-- temporarily disable a job, change its cadence, or add a new one
-- (e.g., "push daily summary email at 9am") without a deploy.
--
-- This table backs a master-scheduler workflow (added in this PR's
-- code) that runs every minute, scans rows whose `next_fire_at <=
-- now()` AND `enabled=true`, dispatches the corresponding task_type
-- workflow, and computes the next fire time via croniter.
--
-- The master-scheduler approach replaces the per-decorator
-- `@DBOS.scheduled` pattern for user-facing recurring tasks. Internal
-- DBOS sweepers (scheduled_health, scheduled_recovery, etc.) stay as
-- decorators because they're system primitives that ship with code.

CREATE TABLE IF NOT EXISTS public.user_schedules (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID,  -- NULL = system schedule (e.g., daily cleanup job)
    name          TEXT NOT NULL,
    cron_expr     TEXT NOT NULL,    -- 5-field cron ("0 9 * * *") in UTC
    task_type     TEXT NOT NULL,    -- routes to the workflow that runs this
    payload       JSONB NOT NULL DEFAULT '{}'::jsonb,
    lane          TEXT NOT NULL DEFAULT 'scheduled',  -- A9 lane queue routing
    enabled       BOOLEAN NOT NULL DEFAULT true,
    last_fired_at TIMESTAMPTZ,
    next_fire_at  TIMESTAMPTZ NOT NULL,
    fire_count    INT NOT NULL DEFAULT 0,
    fail_count    INT NOT NULL DEFAULT 0,
    last_error    TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_user_schedules_due
    ON public.user_schedules (next_fire_at)
    WHERE enabled = true;
CREATE INDEX IF NOT EXISTS idx_user_schedules_user
    ON public.user_schedules (user_id, enabled);

COMMENT ON TABLE public.user_schedules IS
    'User- or system-configured cron schedules. Master scheduler workflow
     (app/workflows/scheduled_master.py) scans this table every minute and
     dispatches due rows. Replaces hard-coded @DBOS.scheduled decorators
     for user-facing recurring tasks; internal DBOS sweepers still use
     the decorator pattern because they''re system primitives.';
COMMENT ON COLUMN public.user_schedules.user_id IS
    'Owner of the schedule. NULL means system-owned (operator-managed
     via DB / admin tools, not user-facing UI).';
COMMENT ON COLUMN public.user_schedules.cron_expr IS
    '5-field cron expression in UTC (m h dom mon dow). Validated by
     croniter on insert/update at the service layer (DB doesn''t parse
     cron). Examples: "0 9 * * *" daily 9am UTC, "*/15 * * * *" every
     15 min, "0 0 * * 0" weekly Sunday midnight.';
COMMENT ON COLUMN public.user_schedules.next_fire_at IS
    'Pre-computed next fire time, kept in sync by the master scheduler
     after each fire (or by service layer on insert/update). The
     idx_user_schedules_due index makes finding due rows O(log n).';
