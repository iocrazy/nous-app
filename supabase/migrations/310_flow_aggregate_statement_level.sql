-- 310_flow_aggregate_statement_level.sql
-- ============================================================
-- Make the task_flows aggregate trigger STATEMENT-LEVEL.
--
-- Problem (root cause of the 185-track playlist 57014 storm):
--   migration 203 installed `trg_task_tracking_flow_aggregate` as a
--   FOR EACH ROW trigger. On every child task_tracking insert / phase
--   change it ran `SELECT count(*) ... WHERE flow_id = X` (an O(N) scan
--   of every sibling) and then `UPDATE task_flows WHERE id = X` (the one
--   shared parent row). When a big batch shares a single flow (a 185-track
--   Soda playlist dispatched at once), that is:
--     * ~740 trigger firings (185 inserts + ~3 phase transitions each),
--     * each re-scanning up to 185 rows  -> O(N^2) work, and
--     * each grabbing the SAME task_flows row lock -> serialization.
--   The dispatch burst holds the parent-row lock long enough that the
--   workers' own single-row task_tracking writes wait past
--   statement_timeout and fail with 57014 ("canceling statement due to
--   statement timeout").
--
-- Fix:
--   Re-implement the aggregate as a STATEMENT-LEVEL trigger using
--   transition tables. It now fires ONCE per statement (not once per row)
--   and re-aggregates each affected flow exactly once via a set-based
--   UPDATE. A bulk INSERT of 185 rows -> 1 firing -> 1 re-aggregation
--   instead of 185. The state-derivation semantics are byte-for-byte the
--   same as migration 203 — only the firing granularity changes.
--
-- Route-C compliant: task_flows aggregate counters are derived metadata,
-- still maintained solely by the DB (no business code touches them).
-- ============================================================

CREATE OR REPLACE FUNCTION public.update_flow_aggregate_stmt()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
    v_flow_ids UUID[];
BEGIN
    -- Collect the distinct set of flow_ids touched by THIS statement from
    -- the transition table(s) that exist for the firing event. Guarded by
    -- TG_OP so a branch never references a transition table the firing
    -- trigger did not declare.
    IF TG_OP = 'INSERT' THEN
        SELECT array_agg(DISTINCT flow_id)
          INTO v_flow_ids
          FROM new_rows
         WHERE flow_id IS NOT NULL;
    ELSIF TG_OP = 'DELETE' THEN
        SELECT array_agg(DISTINCT flow_id)
          INTO v_flow_ids
          FROM old_rows
         WHERE flow_id IS NOT NULL;
    ELSE  -- UPDATE
        -- Postgres forbids `UPDATE OF <cols>` together with transition
        -- tables, so we replicate the mig-203 "only act when phase or
        -- flow_id changed" gate here by joining new<->old on the PK and
        -- keeping only genuinely-changed rows. A row may move between
        -- flows, so collect BOTH its old and new flow_id.
        SELECT array_agg(DISTINCT fid)
          INTO v_flow_ids
          FROM (
              SELECT n.flow_id AS fid
                FROM new_rows n
                JOIN old_rows o ON o.id = n.id
               WHERE n.phase   IS DISTINCT FROM o.phase
                  OR n.flow_id IS DISTINCT FROM o.flow_id
              UNION
              SELECT o.flow_id AS fid
                FROM new_rows n
                JOIN old_rows o ON o.id = n.id
               WHERE n.phase   IS DISTINCT FROM o.phase
                  OR n.flow_id IS DISTINCT FROM o.flow_id
          ) s
         WHERE fid IS NOT NULL;
    END IF;

    -- Nothing relevant changed (0 rows, or none carried a flow_id).
    IF v_flow_ids IS NULL OR array_length(v_flow_ids, 1) IS NULL THEN
        RETURN NULL;
    END IF;

    -- Recompute every affected flow's counters + derived state in ONE
    -- set-based UPDATE. Counts come from a single grouped scan of
    -- task_tracking (same atomic, race-free recompute as migration 203,
    -- just batched across the affected flows).
    UPDATE public.task_flows f SET
        total_tasks     = a.total,
        completed_tasks = a.done,
        failed_tasks    = a.failed,
        cancelled_tasks = a.cancelled,
        state = CASE
            -- Don't regress a manually-cancelled flow back to running.
            WHEN f.state = 'cancelled' AND a.derived_state = 'running'
                THEN 'cancelled'
            ELSE a.derived_state
        END,
        updated_at = now(),
        completed_at = CASE
            WHEN a.derived_state IN ('completed', 'failed', 'cancelled', 'partial')
                 AND f.completed_at IS NULL
            THEN now()
            ELSE f.completed_at
        END
    FROM (
        SELECT
            t.flow_id,
            count(*)                                                    AS total,
            count(*) FILTER (WHERE t.phase = 'completed')               AS done,
            count(*) FILTER (WHERE t.phase IN ('failed', 'lost', 'timed_out')) AS failed,
            count(*) FILTER (WHERE t.phase = 'cancelled')               AS cancelled,
            CASE
                WHEN count(*) FILTER (WHERE t.phase = 'completed')
                   + count(*) FILTER (WHERE t.phase IN ('failed', 'lost', 'timed_out'))
                   + count(*) FILTER (WHERE t.phase = 'cancelled') < count(*)
                    THEN 'running'
                WHEN count(*) FILTER (WHERE t.phase IN ('failed', 'lost', 'timed_out')) > 0
                   AND count(*) FILTER (WHERE t.phase = 'completed') = 0
                   AND count(*) FILTER (WHERE t.phase = 'cancelled') = 0
                    THEN 'failed'
                WHEN count(*) FILTER (WHERE t.phase = 'cancelled') > 0
                   AND count(*) FILTER (WHERE t.phase = 'completed') = 0
                   AND count(*) FILTER (WHERE t.phase IN ('failed', 'lost', 'timed_out')) = 0
                    THEN 'cancelled'
                WHEN count(*) FILTER (WHERE t.phase = 'completed') = count(*)
                    THEN 'completed'
                ELSE 'partial'
            END AS derived_state
        FROM public.task_tracking t
        WHERE t.flow_id = ANY(v_flow_ids)
        GROUP BY t.flow_id
    ) a
    WHERE f.id = a.flow_id;

    RETURN NULL;  -- AFTER STATEMENT triggers ignore the return value.
END;
$function$;

COMMENT ON FUNCTION public.update_flow_aggregate_stmt() IS
    'Statement-level replacement for update_flow_aggregate() (mig 203).
     Fires once per statement via transition tables and re-aggregates each
     affected flow exactly once — eliminates the O(N^2) re-aggregation +
     hot-parent-row lock contention that caused 57014 on big batched flows.';

-- Replace the row-level trigger with three statement-level triggers (one
-- per event, each declaring only the transition tables valid for it).
DROP TRIGGER IF EXISTS trg_task_tracking_flow_aggregate ON public.task_tracking;
DROP TRIGGER IF EXISTS trg_task_tracking_flow_agg_ins ON public.task_tracking;
DROP TRIGGER IF EXISTS trg_task_tracking_flow_agg_upd ON public.task_tracking;
DROP TRIGGER IF EXISTS trg_task_tracking_flow_agg_del ON public.task_tracking;

CREATE TRIGGER trg_task_tracking_flow_agg_ins
AFTER INSERT ON public.task_tracking
REFERENCING NEW TABLE AS new_rows
FOR EACH STATEMENT
EXECUTE FUNCTION public.update_flow_aggregate_stmt();

-- NOTE: no `UPDATE OF phase, flow_id` column list — Postgres forbids that
-- with transition tables. The function itself gates on phase/flow_id change.
CREATE TRIGGER trg_task_tracking_flow_agg_upd
AFTER UPDATE ON public.task_tracking
REFERENCING NEW TABLE AS new_rows OLD TABLE AS old_rows
FOR EACH STATEMENT
EXECUTE FUNCTION public.update_flow_aggregate_stmt();

CREATE TRIGGER trg_task_tracking_flow_agg_del
AFTER DELETE ON public.task_tracking
REFERENCING OLD TABLE AS old_rows
FOR EACH STATEMENT
EXECUTE FUNCTION public.update_flow_aggregate_stmt();

COMMENT ON TRIGGER trg_task_tracking_flow_agg_ins ON public.task_tracking IS
    'Statement-level flow aggregate (mig 310). Replaces the row-level
     trg_task_tracking_flow_aggregate from mig 203.';
