-- 177: PR-D7 phase 2 — flip dbos_workflow_routing all rows to 'dbos'.
--
-- After D7 phase 2 commits (3 production .delay() callsites switched to
-- start_workflow_routed), every dispatch path consults this table. With
-- all rows at 'dbos', no Celery work fires from start_workflow_routed.
-- The Celery worker process can be docker-compose down'd.
--
-- Rollback: per-task_type rollback by setting that row back to 'celery'.
--   UPDATE public.dbos_workflow_routing SET mode='celery'
--    WHERE task_type='<offending>';
-- Requires the legacy Celery worker to be running again to actually
-- execute (just toggling the row doesn't bring Celery back online).
--
-- Pre-flight check (operator runs first):
--   - Migration 175 applied + unmigrated_count=0 (D6 schema ready)
--   - DBOS scheduler proven on dev (D7 E2E report verified
--     update_system_status + agent_runs_sweeper firing)
--   - All 3 production .delay() callsites in this commit confirmed:
--       app/workflows/download.py        (thumbnail chain)
--       app/workflows/scheduled_recovery.py (retry_failed_downloads)
--       app/api/script_ai_router.py      (script outline)
--
-- Connection role: same SET ROLE pattern as 175/176 — works under
-- both Supabase CLI (service_role) and direct psql (mediahub_dbos).
SET ROLE service_role;

UPDATE public.dbos_workflow_routing
   SET mode = 'dbos',
       notes = COALESCE(notes, '') || ' | flipped to dbos in PR-D7'
 WHERE mode <> 'dbos';

-- Snapshot for migration log
DO $$
DECLARE
  v_dbos     INTEGER;
  v_celery   INTEGER;
  v_shadow   INTEGER;
  v_other    INTEGER;
BEGIN
  SELECT COUNT(*) INTO v_dbos   FROM public.dbos_workflow_routing WHERE mode='dbos';
  SELECT COUNT(*) INTO v_celery FROM public.dbos_workflow_routing WHERE mode='celery';
  SELECT COUNT(*) INTO v_shadow FROM public.dbos_workflow_routing WHERE mode='shadow';
  SELECT COUNT(*) INTO v_other  FROM public.dbos_workflow_routing
                                 WHERE mode NOT IN ('dbos','celery','shadow');
  RAISE NOTICE
    '[migration 177] routing snapshot: dbos=%, celery=%, shadow=%, other=%',
    v_dbos, v_celery, v_shadow, v_other;
END $$;
