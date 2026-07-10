-- 352_projects_default_stage.sql — D4 (Projects final UI spec 2026-07-10):
-- every project must have a SOP stage; backfill NULLs to the first catalog stage.
-- Idempotent; no history rows are fabricated (history stays genuine transitions).
UPDATE public.projects
SET current_stage_id = (
  SELECT id FROM public.project_stages ORDER BY sort_order ASC LIMIT 1
)
WHERE current_stage_id IS NULL
  AND EXISTS (SELECT 1 FROM public.project_stages);
