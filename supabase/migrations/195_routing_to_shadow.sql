-- D2 (PR-D3 follow-up): flip ported workflows to 'shadow' mode.
--
-- Ports completed in commits before this migration:
--   parse                       — app/workflows/parse.py
--   download                    — app/workflows/download.py
--   transcode                   — app/workflows/transcode.py
--   thumbnail                   — app/workflows/thumbnail.py (PR-D2.3 PoC)
--   ai_extract                  — app/workflows/analyze_l1.py
--   ai_transcription            — PR-D2.2
--   ai_summary                  — PR-D2.2
--   storyboard_image_gen        — app/workflows/storyboard.py
--   storyboard_video_gen        — app/workflows/storyboard.py
--   storyboard_script_split     — app/workflows/storyboard.py
--   storyboard_video_analysis   — app/workflows/storyboard.py
--   storyboard_scene_detect     — app/workflows/storyboard.py
--   storyboard_export           — app/workflows/storyboard.py
--   script_outline_gen          — app/workflows/script_outline.py
--   memory_tasks                — app/workflows/write_memory.py
--   agent_runs_sweeper          — app/workflows/agent_runs_sweeper.py
--
-- 'shadow' = DBOS runs in parallel; Celery output stays canonical.
-- We compare logs / agent_runs counters to detect divergence before
-- promoting any task_type to 'dbos' (canonical).
--
-- NOT flipping (kept on celery):
--   upload   — handler-side synchronous, no Celery task to compare to
--   signals  — Celery signal hooks; redesign needed before port
--
-- Rollback: re-set mode='celery' for the offending row. No code deploy
-- needed (FastAPI lifespan re-reads the table on tick).

UPDATE public.dbos_workflow_routing
SET mode = 'shadow',
    notes = 'D2: ported to DBOS — parallel-run window for divergence comparison',
    updated_by = 'migration_195'
WHERE task_type IN (
  'parse',
  'download',
  'transcode',
  'thumbnail',
  'ai_extract',
  'ai_transcription',
  'ai_summary',
  'storyboard_image_gen',
  'storyboard_video_gen',
  'storyboard_script_split',
  'storyboard_video_analysis',
  'storyboard_scene_detect',
  'storyboard_export',
  'script_outline_gen',
  'memory_tasks',
  'agent_runs_sweeper'
)
AND mode = 'celery';   -- only flip rows that haven't been touched
                       -- (idempotent re-run is safe; manually-set 'dbos' rows preserved)
