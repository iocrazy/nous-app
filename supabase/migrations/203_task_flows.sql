-- 203_task_flows.sql
--
-- Task Flows: group N related tasks into a single user-visible "flow"
-- with aggregate progress and cascade-cancel.
--
-- Why
-- ---
-- Today, "process this Douyin URL" creates 4-6 separate task_tracking
-- rows (parse + download_video + download_cover + transcribe + summary
-- + analyze) and the user sees 6 cards in their TaskCenter even though
-- they only triggered ONE thing. There's no way to:
--   * see total progress for the user's submission as a unit
--   * cancel "the whole job" without cancelling individual children
--   * know which child belongs to which submission when the user
--     submits 3 URLs in a row
--
-- A flow is a parent row that holds the user-visible name + aggregate
-- progress; child task_tracking rows reference it via flow_id.
--
-- The cascade trigger updates flow.completed_tasks / failed_tasks /
-- state automatically as children transition.

CREATE TABLE IF NOT EXISTS public.task_flows (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL,
    name            TEXT NOT NULL,
    state           TEXT NOT NULL DEFAULT 'running',
    cascade_cancel  BOOLEAN NOT NULL DEFAULT true,
    total_tasks     INT NOT NULL DEFAULT 0,
    completed_tasks INT NOT NULL DEFAULT 0,
    failed_tasks    INT NOT NULL DEFAULT 0,
    cancelled_tasks INT NOT NULL DEFAULT 0,
    metadata        JSONB DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at    TIMESTAMPTZ,
    CONSTRAINT task_flows_state_check CHECK (
        state IN ('running', 'completed', 'failed', 'cancelled', 'partial')
    )
);

CREATE INDEX IF NOT EXISTS idx_task_flows_user_state
    ON public.task_flows (user_id, state);
CREATE INDEX IF NOT EXISTS idx_task_flows_created
    ON public.task_flows (created_at DESC);

COMMENT ON TABLE public.task_flows IS
    'Parent grouping for related tasks. One flow = one user submission
     (e.g., "Process URL X"). Child tasks reference via task_tracking.flow_id.
     Aggregate counters maintained by trigger trg_task_tracking_flow_aggregate.';
COMMENT ON COLUMN public.task_flows.state IS
    'running / completed (all children done OK) / failed (any child failed
     and cascade_cancel=true) / cancelled (user cancelled) / partial
     (mixed: some completed, some cancelled — terminal but not pure success)';
COMMENT ON COLUMN public.task_flows.cascade_cancel IS
    'When true, cancelling the flow cancels every non-terminal child task
     via the lifecycle bus. When false, cancel marks the flow alone.';

-- ── task_tracking.flow_id column ─────────────────────────────────
ALTER TABLE public.task_tracking
    ADD COLUMN IF NOT EXISTS flow_id UUID REFERENCES public.task_flows(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_task_tracking_flow_id
    ON public.task_tracking (flow_id)
    WHERE flow_id IS NOT NULL;

COMMENT ON COLUMN public.task_tracking.flow_id IS
    'Parent flow this task belongs to. NULL for unflow''d tasks (legacy
     ad-hoc dispatches still allowed). FK ON DELETE SET NULL so deleting
     a flow doesn''t cascade-delete child tasks (keep them for history).';

-- ── Trigger: maintain flow aggregate counters + state ────────────
CREATE OR REPLACE FUNCTION public.update_flow_aggregate()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
    v_flow_id UUID;
    v_total INT;
    v_done  INT;
    v_failed INT;
    v_cancelled INT;
    v_state TEXT;
BEGIN
    v_flow_id := COALESCE(NEW.flow_id, OLD.flow_id);
    IF v_flow_id IS NULL THEN
        RETURN COALESCE(NEW, OLD);
    END IF;

    -- Recompute child status counts atomically. Doing it via a single
    -- SELECT FROM task_tracking is simpler than maintaining per-row
    -- delta arithmetic (which fails under race conditions / phase
    -- skips like queued -> failed without going through running).
    SELECT
        count(*),
        count(*) FILTER (WHERE phase = 'completed'),
        count(*) FILTER (WHERE phase IN ('failed', 'lost', 'timed_out')),
        count(*) FILTER (WHERE phase = 'cancelled')
    INTO v_total, v_done, v_failed, v_cancelled
    FROM public.task_tracking
    WHERE flow_id = v_flow_id;

    -- Derive the flow state from child terminal counts.
    -- Terminal: completed + failed + cancelled = total
    IF v_done + v_failed + v_cancelled < v_total THEN
        v_state := 'running';
    ELSIF v_failed > 0 AND v_done = 0 AND v_cancelled = 0 THEN
        v_state := 'failed';
    ELSIF v_cancelled > 0 AND v_done = 0 AND v_failed = 0 THEN
        v_state := 'cancelled';
    ELSIF v_done = v_total THEN
        v_state := 'completed';
    ELSE
        -- Mixed terminal — partial success.
        v_state := 'partial';
    END IF;

    UPDATE public.task_flows SET
        total_tasks     = v_total,
        completed_tasks = v_done,
        failed_tasks    = v_failed,
        cancelled_tasks = v_cancelled,
        state           = CASE
            -- Don't regress a manually-cancelled flow back to running.
            WHEN state = 'cancelled' AND v_state = 'running' THEN 'cancelled'
            ELSE v_state
        END,
        updated_at      = now(),
        completed_at    = CASE
            WHEN v_state IN ('completed', 'failed', 'cancelled', 'partial')
                AND completed_at IS NULL
            THEN now()
            ELSE completed_at
        END
    WHERE id = v_flow_id;

    RETURN COALESCE(NEW, OLD);
END;
$function$;

COMMENT ON FUNCTION public.update_flow_aggregate() IS
    'Trigger function. Recomputes task_flows counters + derived state on
     any task_tracking row INSERT/UPDATE/DELETE that touches flow_id or
     phase. Atomic — uses a single SELECT to avoid delta race conditions.';

DROP TRIGGER IF EXISTS trg_task_tracking_flow_aggregate ON public.task_tracking;
CREATE TRIGGER trg_task_tracking_flow_aggregate
AFTER INSERT OR UPDATE OF phase, flow_id OR DELETE
ON public.task_tracking
FOR EACH ROW
EXECUTE FUNCTION public.update_flow_aggregate();

COMMENT ON TRIGGER trg_task_tracking_flow_aggregate ON public.task_tracking IS
    'Keeps task_flows.total/completed/failed/cancelled in sync with child
     phase transitions. Fires on INSERT, on UPDATE of phase or flow_id, and
     on DELETE.';
