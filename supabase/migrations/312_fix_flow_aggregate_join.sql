-- 312_fix_flow_aggregate_join.sql
-- ============================================================
-- HOTFIX: migration 310's statement-level flow-aggregate function joins the
-- UPDATE transition tables on `o.id = n.id`, but task_tracking has NO `id`
-- column — its primary key is `dbos_workflow_id`. Every UPDATE on
-- task_tracking therefore raised:
--   (UndefinedColumn) column o.id does not exist
--     LINE: JOIN old_rows o ON o.id = n.id
-- which aborted the triggering statement. User-visible symptom: video fetch
-- (and any task phase change) returned HTTP 500 "Internal server error".
--
-- Fix: join old<->new on the real PK, dbos_workflow_id. Function body is
-- otherwise byte-for-byte identical to migration 310; the three triggers
-- reference it by name and need no change.
-- ============================================================

CREATE OR REPLACE FUNCTION public.update_flow_aggregate_stmt()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
    v_flow_ids UUID[];
BEGIN
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
    ELSE  -- UPDATE: join old<->new on the PK (dbos_workflow_id), keep only
          -- rows whose phase or flow_id genuinely changed; a row may move
          -- between flows, so collect BOTH its old and new flow_id.
        SELECT array_agg(DISTINCT fid)
          INTO v_flow_ids
          FROM (
              SELECT n.flow_id AS fid
                FROM new_rows n
                JOIN old_rows o ON o.dbos_workflow_id = n.dbos_workflow_id
               WHERE n.phase   IS DISTINCT FROM o.phase
                  OR n.flow_id IS DISTINCT FROM o.flow_id
              UNION
              SELECT o.flow_id AS fid
                FROM new_rows n
                JOIN old_rows o ON o.dbos_workflow_id = n.dbos_workflow_id
               WHERE n.phase   IS DISTINCT FROM o.phase
                  OR n.flow_id IS DISTINCT FROM o.flow_id
          ) s
         WHERE fid IS NOT NULL;
    END IF;

    IF v_flow_ids IS NULL OR array_length(v_flow_ids, 1) IS NULL THEN
        RETURN NULL;
    END IF;

    UPDATE public.task_flows f SET
        total_tasks     = a.total,
        completed_tasks = a.done,
        failed_tasks    = a.failed,
        cancelled_tasks = a.cancelled,
        state = CASE
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

    RETURN NULL;
END;
$function$;

COMMENT ON FUNCTION public.update_flow_aggregate_stmt() IS
    'Statement-level flow aggregate (mig 310, join fixed in mig 312:
     old<->new joined on dbos_workflow_id, the real task_tracking PK).';

-- PostgREST schema reload (function-only change; harmless).
NOTIFY pgrst, 'reload schema';
