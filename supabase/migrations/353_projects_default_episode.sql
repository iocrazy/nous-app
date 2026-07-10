-- 353_projects_default_episode.sql — PR-8 Task C (final spec G3)
--
-- Every project must have an Episode 1; existing script_projects with no
-- episode_id get pointed at their project's earliest episode (sort_order
-- ASC). Backfill only — new projects get their Episode 1 + script at
-- create time (see ProjectsService.create_project, PR-8 Task B). Idempotent:
-- both statements are guarded (NOT EXISTS / IS NULL), safe to re-run.
--
-- Columns verified against the local dev DB (episodes: id/project_id/title/
-- sort_order/created_at/updated_at, migration 338; script_projects.episode_id
-- nullable bigint, migration 338).

INSERT INTO public.episodes (project_id, title, sort_order)
SELECT p.id, 'Episode 1', 1 FROM public.projects p
WHERE NOT EXISTS (SELECT 1 FROM public.episodes e WHERE e.project_id = p.id);

UPDATE public.script_projects sp
SET episode_id = (
  SELECT e.id FROM public.episodes e
  WHERE e.project_id = sp.project_id ORDER BY e.sort_order ASC LIMIT 1
)
WHERE sp.episode_id IS NULL AND sp.project_id IS NOT NULL;
