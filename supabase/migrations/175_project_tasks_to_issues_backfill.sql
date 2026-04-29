-- 175: PR-D6 — backfill project_tasks rows into the new issues table.
--
-- NON-DESTRUCTIVE: project_tasks stays in place and keeps serving the
-- legacy KanbanBoard during the frontend swap window. The new IssuesPage
-- reads from issues only. After D7 validation we drop project_tasks.
--
-- Each project_tasks row is mirrored as an issues row with:
--   - origin_kind = 'migrated_project_task'
--   - origin_fingerprint = '<project_tasks.id>' (string of BIGINT)
--   - origin_id           = '<project_tasks.id>'
--   - title / description / project_id / assignee / created_by directly mapped
--   - status mapped per legacy → new enum table below
--
-- Idempotent: a second run skips rows already mirrored (matched on
-- origin_kind + origin_fingerprint).
--
-- Status mapping (legacy project_tasks → issues):
--   todo         → todo
--   in_progress  → in_progress
--   done         → done
--   cancelled    → cancelled
--   on_hold      → blocked
--
-- Identifier allocation:
--   Goes through `issue_create_atomic` (migration 173) so the global
--   MH-N counter stays gap-free under concurrent INSERTs from this
--   backfill AND any live `POST /api/v1/issues` traffic.
--
-- Performance:
--   Uses a row-by-row PL/pgSQL loop because issue_create_atomic is a
--   single-row stored proc (counter UPDATE...RETURNING + INSERT). For
--   the few hundred rows in dev that's <1s. Production-scale (10k+)
--   would want a batched variant — defer until measured.

DO $$
DECLARE
  pt              RECORD;
  v_status        TEXT;
  v_payload       JSONB;
  v_already_count INTEGER;
  v_inserted      INTEGER := 0;
  v_skipped       INTEGER := 0;
BEGIN
  FOR pt IN
    SELECT id, project_id, title, description, status,
           assignee_id, created_by, created_at
      FROM public.project_tasks
     ORDER BY created_at ASC
  LOOP
    -- Skip if already mirrored (idempotency guard).
    SELECT COUNT(*) INTO v_already_count
      FROM public.issues
     WHERE origin_kind = 'migrated_project_task'
       AND origin_fingerprint = pt.id::text;

    IF v_already_count > 0 THEN
      v_skipped := v_skipped + 1;
      CONTINUE;
    END IF;

    -- Status mapping
    v_status := CASE pt.status
                  WHEN 'todo'        THEN 'todo'
                  WHEN 'in_progress' THEN 'in_progress'
                  WHEN 'done'        THEN 'done'
                  WHEN 'cancelled'   THEN 'cancelled'
                  WHEN 'on_hold'     THEN 'blocked'
                  ELSE 'backlog'
                END;

    -- Build issue_create_atomic payload
    v_payload := jsonb_build_object(
      'title',                left(pt.title, 500),
      'description',          left(coalesce(pt.description, ''), 50000),
      'status',               v_status,
      'priority',             'medium',
      'project_id',           pt.project_id,
      'assignee_user_id',     pt.assignee_id,
      'created_by_user_id',   pt.created_by,
      'origin_kind',          'migrated_project_task',
      'origin_id',            pt.id::text,
      'origin_fingerprint',   pt.id::text
    );
    -- Strip JSON null entries — issue_create_atomic doesn't tolerate
    -- explicit nulls for nullable columns where DEFAULT is preferred.
    v_payload := v_payload - ARRAY(
      SELECT key FROM jsonb_each(v_payload) WHERE value = 'null'::jsonb
    );

    PERFORM public.issue_create_atomic(v_payload);
    v_inserted := v_inserted + 1;
  END LOOP;

  RAISE NOTICE
    '[migration 175] project_tasks backfill: inserted=%, skipped=%',
    v_inserted, v_skipped;
END $$;

-- Sanity check view: which project_tasks have NOT been mirrored. Useful
-- during D7 cleanup to confirm coverage before dropping project_tasks.
CREATE OR REPLACE VIEW public.unmigrated_project_tasks AS
  SELECT pt.*
    FROM public.project_tasks pt
   WHERE NOT EXISTS (
     SELECT 1 FROM public.issues i
      WHERE i.origin_kind = 'migrated_project_task'
        AND i.origin_fingerprint = pt.id::text
   );

COMMENT ON VIEW public.unmigrated_project_tasks IS
  'project_tasks rows not yet mirrored into issues. Should be empty before D7 drops project_tasks.';
