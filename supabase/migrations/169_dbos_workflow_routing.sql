-- 169: PR-D2.1 — dbos_workflow_routing flag table
--
-- Per design doc P11: per-task_type routing table that the FastAPI lifespan
-- reads on startup and refreshes periodically. Allows hot-swap between
-- 'celery' (current behavior), 'shadow' (DBOS runs in parallel, output
-- discarded — used to compare vs Celery during PR-D3 shadow window), and
-- 'dbos' (DBOS is canonical, Celery skipped).
--
-- Rollback strategy: per design doc P11, if a particular task_type's DBOS
-- workflow regresses, flip the row to 'celery' — no code deploy needed.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS + ON CONFLICT seeds.

CREATE TABLE IF NOT EXISTS public.dbos_workflow_routing (
  task_type   TEXT PRIMARY KEY,
  mode        TEXT NOT NULL DEFAULT 'celery'
    CHECK (mode IN ('celery', 'shadow', 'dbos')),
  -- Notes for ops: why is this task_type in shadow? when did we flip?
  notes       TEXT,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by  TEXT NOT NULL DEFAULT 'migration'
);

COMMENT ON TABLE public.dbos_workflow_routing IS
  'Per-task_type DBOS routing. mode controls which executor handles the task: celery (legacy), shadow (DBOS parallel-run, output discarded), dbos (DBOS canonical). FastAPI lifespan loads this table at boot + refreshes on tick. Schema PR-D2.1, design doc P11.';

COMMENT ON COLUMN public.dbos_workflow_routing.mode IS
  'celery = use Celery only (default for un-ported tasks); shadow = DBOS runs in parallel for comparison, Celery output is canonical; dbos = DBOS is canonical, Celery skipped.';

-- Touch trigger for updated_at
CREATE OR REPLACE FUNCTION public.touch_dbos_workflow_routing_updated_at() RETURNS trigger AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS dbos_workflow_routing_touch ON public.dbos_workflow_routing;
CREATE TRIGGER dbos_workflow_routing_touch
  BEFORE UPDATE ON public.dbos_workflow_routing
  FOR EACH ROW EXECUTE FUNCTION public.touch_dbos_workflow_routing_updated_at();

-- Seed all known user-visible Celery task_types in 'celery' mode.
-- Source: app/tasks/*.py @shared_task names (verified 2026-04-28, ~23 entries).
-- New task_types not seeded here will be treated as 'celery' by the lookup
-- helper (defensive default), but explicit seeding helps ops dashboards.
INSERT INTO public.dbos_workflow_routing (task_type, mode, notes) VALUES
  ('download',                 'celery', 'PR-D3a candidate'),
  ('parse',                    'celery', 'PR-D3a candidate'),
  ('upload',                   'celery', 'PR-D3a candidate'),
  ('transcode',                'celery', 'PR-D3a candidate'),
  ('thumbnail',                'celery', 'PR-D3a candidate'),
  ('ai_extract',               'celery', 'PR-D3a candidate'),
  ('ai_transcription',         'celery', 'PR-D2.2 first port (small leaf)'),
  ('ai_summary',               'celery', 'PR-D2.2 first port (PoC #8 already validated)'),
  ('storyboard_image_gen',     'celery', 'PR-D3b chain'),
  ('storyboard_video_gen',     'celery', 'PR-D3b chain'),
  ('storyboard_script_split',  'celery', 'PR-D3b chain'),
  ('storyboard_video_analysis','celery', 'PR-D3b chain'),
  ('storyboard_scene_detect',  'celery', 'PR-D3b chain'),
  ('storyboard_export',        'celery', 'PR-D3b chain'),
  ('script_outline_gen',       'celery', 'PR-D3c'),
  ('agent_runs_sweeper',       'celery', 'PR-D3c — scheduled, may stay celery-beat'),
  ('memory_tasks',             'celery', 'PR-D3c'),
  ('signals',                  'celery', 'PR-D3c')
ON CONFLICT (task_type) DO NOTHING;

-- Grants — config table is read by all app roles, written only by admins.
GRANT SELECT ON public.dbos_workflow_routing TO authenticated, anon, service_role, mediahub_app, mediahub_dbos;
GRANT INSERT, UPDATE, DELETE ON public.dbos_workflow_routing TO service_role, mediahub_dbos;

-- RLS — readable to everyone, mutable only via service_role / mediahub_dbos
-- (which use BYPASSRLS / SET ROLE patterns from PoC #11).
ALTER TABLE public.dbos_workflow_routing ENABLE ROW LEVEL SECURITY;

CREATE POLICY dbos_routing_select ON public.dbos_workflow_routing
  FOR SELECT USING (true);

-- No INSERT/UPDATE/DELETE policies → implicit deny for non-bypass roles.
-- service_role bypasses; mediahub_dbos must SET ROLE service_role to mutate.
