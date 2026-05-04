-- 201_workflow_health_classifier.sql
--
-- Workflow health classifier infrastructure (D8-B follow-up).
--
-- Why this exists
-- ---------------
-- Today the DBOS workflow_status table tracks a workflow's lifecycle but
-- doesn't distinguish "running long because it's a big job" from "wedged
-- because a downstream call is hanging" from "worker died and nobody is
-- running it any more". The system has no visibility into the difference,
-- so any sweeper that auto-cancels by elapsed time will eventually kill
-- somebody's legitimate 6-hour transcription.
--
-- The classifier reads three independent signals:
--   * heartbeat_at — set by the workflow body itself every 30s. Fresh
--     means "the worker process is alive AND the asyncio loop is making
--     progress". Stale (>5min by default) means LOST — the worker died
--     between heartbeats, or the loop is wedged.
--   * progress / phase — bumped by the workflow as steps complete. If
--     heartbeat is fresh but progress hasn't moved, the workflow is
--     STUCK_IN_STEP (one step taking too long, possibly the user's
--     intent), which we surface but never auto-cancel.
--   * workflow_timeout_policy — per-task-type expected vs hard ceiling.
--     "Expected" is when we tell the user "this is taking longer than
--     usual, want to cancel?". "Hard" is the upper bound for ORPHAN
--     detection (a PENDING task that never started executing within hard
--     ceiling × 3 is presumably stuck in DBOS executor and safe to mark
--     cancelled — the workflow body never ran, no user state to lose).
--
-- User control
-- ------------
-- Three new task_tracking columns let the user override system defaults:
--   * max_duration_minutes — user-set hard ceiling. NULL = use type policy.
--   * expected_duration_minutes — user tells us upfront this will be slow.
--   * do_not_auto_cancel — opt out of any sweeper action. Even ORPHAN
--     classification only logs, never cancels.
--
-- The sweeper itself (next migration / app code) NEVER cancels HEALTHY
-- / SLOW / STUCK_IN_STEP / STALLED — those are user-decision states.
-- Only LOST and ORPHAN_PENDING get auto-action, and ORPHAN_PENDING only
-- when do_not_auto_cancel is false.

-- ── Step 1: per-task-type timeout policy ───────────────────────────
CREATE TABLE IF NOT EXISTS public.workflow_timeout_policy (
    task_type TEXT PRIMARY KEY,
    expected_duration_seconds INT NOT NULL,
    hard_ceiling_seconds INT NOT NULL,
    heartbeat_stale_seconds INT NOT NULL DEFAULT 300,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (hard_ceiling_seconds >= expected_duration_seconds)
);

COMMENT ON TABLE  public.workflow_timeout_policy IS
    'Per-task-type duration policy used by the workflow health classifier.
     expected_duration_seconds = when we tell the user "running long".
     hard_ceiling_seconds = upper bound for ORPHAN_PENDING detection.
     heartbeat_stale_seconds = how long without heartbeat = LOST.';
COMMENT ON COLUMN public.workflow_timeout_policy.expected_duration_seconds IS
    'Beyond this, user gets a "running long" notification (no auto-action).';
COMMENT ON COLUMN public.workflow_timeout_policy.hard_ceiling_seconds  IS
    'A workflow stuck PENDING (never picked up by executor) for hard_ceiling × 3 = ORPHAN_PENDING. Safe to auto-cancel because the body never ran.';
COMMENT ON COLUMN public.workflow_timeout_policy.heartbeat_stale_seconds IS
    'Heartbeat older than this with status=RUNNING ⇒ LOST (worker died).';

-- Sane defaults for the task_types currently in flight. Operator can
-- ALTER any row at runtime; the classifier reads on every tick.
INSERT INTO public.workflow_timeout_policy
    (task_type, expected_duration_seconds, hard_ceiling_seconds, heartbeat_stale_seconds, description)
VALUES
    ('parse',              300,    1800,  300,
     'URL → parsed_media metadata. Most parses finish in <1min; cap allows yt-dlp 304-redirect retries.'),
    ('download',           1800,   14400, 600,
     'yt-dlp download. Long videos legitimately take 30-60min. Heartbeat allowance is large because ffmpeg can saturate the loop briefly during muxing.'),
    ('transcode',          1800,   14400, 600,
     'ffmpeg transcode to HLS. Same envelope as download.'),
    ('thumbnail',          120,    600,   180,
     'ffmpeg thumbnail extraction. Always fast; long means the input is broken.'),
    ('ai_transcription',   900,    7200,  300,
     'Whisper transcribe. 1hr cap accommodates large batched chunks; user can extend per-task.'),
    ('ai_summary',         600,    1800,  300,
     'LLM summary over transcript. Fast unless context window is huge.'),
    ('ai_extract',         600,    3600,  300,
     'L1 cover image visual analysis. Fast for thumbnails.'),
    ('ai_visual_analysis', 1800,   7200,  300,
     'Full-video visual analysis. Heavier than ai_extract.'),
    ('script_outline_gen', 600,    1800,  300,
     'LLM script outline generation.'),
    ('agent_workforce',    600,    3600,  300,
     'Multi-agent task delegation. Wide envelope because subagents may delegate further.'),
    -- DBOS @scheduled sweepers / dispatch — should always be quick.
    ('outbox_dispatch',    30,     120,   60,
     'Internal: agent_outbox dispatch tick (5s cron).'),
    ('inbox_dispatch',     30,     120,   60,
     'Internal: agent_inbox dispatch tick (10s cron).'),
    ('agent_runs_sweeper', 30,     120,   60,
     'Internal: agent_runs heartbeat sweeper (1min cron).'),
    ('workflow_health',    60,     300,   60,
     'Internal: this very sweeper.')
ON CONFLICT (task_type) DO NOTHING;

-- ── Step 2: task_tracking columns for heartbeat + user overrides ───
ALTER TABLE public.task_tracking
    ADD COLUMN IF NOT EXISTS heartbeat_at              TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS health_status             TEXT,
    ADD COLUMN IF NOT EXISTS health_notified_at        TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS max_duration_minutes      INT,
    ADD COLUMN IF NOT EXISTS expected_duration_minutes INT,
    ADD COLUMN IF NOT EXISTS do_not_auto_cancel        BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN public.task_tracking.heartbeat_at IS
    'Set by the workflow body itself every ~30s. NULL = workflow has not
     started writing heartbeats yet. Stale = worker died (LOST).';
COMMENT ON COLUMN public.task_tracking.health_status IS
    'Last classification by workflow_health_sweeper.
     One of: HEALTHY / SLOW / STUCK_IN_STEP / STALLED / LOST / ORPHAN_PENDING / NULL.';
COMMENT ON COLUMN public.task_tracking.health_notified_at IS
    'Last time we surfaced a "running long" notification to the user.
     Used to dedupe — we notify once per classification flip.';
COMMENT ON COLUMN public.task_tracking.max_duration_minutes IS
    'User-set hard cap. NULL = use workflow_timeout_policy.hard_ceiling.
     The sweeper RESPECTS this even when do_not_auto_cancel=false:
     surpassing max_duration_minutes flips status=timed_out (which IS
     auto-action; user opted into it by setting the field).';
COMMENT ON COLUMN public.task_tracking.expected_duration_minutes IS
    'User-declared "this is supposed to be slow". Above this we DO NOT
     emit "running long" notifications. Below max_duration_minutes still
     auto-times-out.';
COMMENT ON COLUMN public.task_tracking.do_not_auto_cancel IS
    'Opt out of any sweeper-driven action. Even ORPHAN_PENDING and LOST
     classifications only log; never flip status. User must cancel manually.';

-- Index for the sweeper: fetch active workflows + their heartbeat freshness.
CREATE INDEX IF NOT EXISTS idx_task_tracking_active_heartbeat
    ON public.task_tracking (heartbeat_at)
    WHERE phase IN ('queued', 'in_progress');

-- ── Step 3: classifier helper function ─────────────────────────────
-- Pure SQL, called from the @DBOS.scheduled sweeper. Returns a row's
-- classification given current state + policy + clock. Kept in PG so
-- ad-hoc queries from psql / admin can use it too.
CREATE OR REPLACE FUNCTION public.classify_workflow_health(
    p_phase            TEXT,
    p_started_at       TIMESTAMPTZ,
    p_heartbeat_at     TIMESTAMPTZ,
    p_progress         INT,
    p_progress_changed_at TIMESTAMPTZ,  -- caller passes updated_at as a proxy
    p_task_type        TEXT,
    p_user_max_minutes INT
) RETURNS TEXT
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    policy RECORD;
    elapsed_seconds INT;
    heartbeat_age_seconds INT;
    progress_age_seconds INT;
    user_hard_seconds INT;
BEGIN
    -- PENDING (i.e., not yet running) — only ORPHAN matters.
    IF p_phase = 'queued' THEN
        SELECT * INTO policy FROM public.workflow_timeout_policy WHERE task_type = p_task_type;
        IF NOT FOUND THEN
            RETURN 'HEALTHY';  -- unknown type, give it benefit of doubt
        END IF;
        elapsed_seconds := EXTRACT(EPOCH FROM (now() - COALESCE(p_started_at, now())))::INT;
        IF elapsed_seconds > policy.hard_ceiling_seconds * 3 THEN
            RETURN 'ORPHAN_PENDING';
        END IF;
        RETURN 'HEALTHY';
    END IF;

    -- RUNNING checks: need a started_at to reason about elapsed time.
    IF p_started_at IS NULL THEN
        RETURN 'HEALTHY';
    END IF;

    SELECT * INTO policy FROM public.workflow_timeout_policy WHERE task_type = p_task_type;
    IF NOT FOUND THEN
        RETURN 'HEALTHY';
    END IF;

    elapsed_seconds := EXTRACT(EPOCH FROM (now() - p_started_at))::INT;
    heartbeat_age_seconds := CASE
        WHEN p_heartbeat_at IS NULL THEN elapsed_seconds  -- never wrote one
        ELSE EXTRACT(EPOCH FROM (now() - p_heartbeat_at))::INT
    END;
    progress_age_seconds := CASE
        WHEN p_progress_changed_at IS NULL THEN elapsed_seconds
        ELSE EXTRACT(EPOCH FROM (now() - p_progress_changed_at))::INT
    END;

    -- LOST: heartbeat is stale beyond policy → worker died.
    -- This is the ONLY auto-action state (besides ORPHAN_PENDING) so the
    -- threshold needs to be conservative.
    IF heartbeat_age_seconds > policy.heartbeat_stale_seconds THEN
        RETURN 'LOST';
    END IF;

    -- User-defined hard cap — if set and exceeded, mark as overage so
    -- caller can flip to timed_out (user opted into auto-cancel by
    -- setting max_duration_minutes).
    user_hard_seconds := CASE
        WHEN p_user_max_minutes IS NOT NULL THEN p_user_max_minutes * 60
        ELSE NULL
    END;
    IF user_hard_seconds IS NOT NULL AND elapsed_seconds > user_hard_seconds THEN
        RETURN 'USER_TIMEOUT';
    END IF;

    -- Below user cap: classify between healthy / slow / stalled / stuck.
    IF elapsed_seconds <= policy.expected_duration_seconds THEN
        IF progress_age_seconds > policy.expected_duration_seconds / 2 THEN
            RETURN 'STUCK_IN_STEP';
        END IF;
        RETURN 'HEALTHY';
    END IF;

    -- Past expected but heartbeat is fresh.
    IF progress_age_seconds > (policy.expected_duration_seconds / 2) THEN
        RETURN 'STALLED';
    END IF;
    RETURN 'SLOW';
END;
$$;

COMMENT ON FUNCTION public.classify_workflow_health IS
    'Pure-SQL classifier used by the workflow health sweeper. Returns
     HEALTHY / SLOW / STUCK_IN_STEP / STALLED / LOST / ORPHAN_PENDING /
     USER_TIMEOUT given the row plus policy. No side effects — caller
     decides whether to act on the classification.';
