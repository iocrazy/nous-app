-- 196: REVERT 195. Migration 195 was wrong.
--
-- Background: mig 195 attempted to flip 16 task_types from 'celery'
-- to 'shadow' to enable a parallel-run comparison window. This was
-- based on the OLD design (PR-D2.1 mig 169) where 'shadow' meant
-- "DBOS runs alongside Celery, Celery output canonical".
--
-- That design no longer exists. PR-D7 phase 2 (mig 177, 2026-04-29)
-- removed Celery entirely and tightened start_workflow_routed to ONLY
-- accept mode='dbos' — anything else raises RuntimeError. So flipping
-- to 'shadow' immediately broke dispatch for transcode / download /
-- parse / storyboard_* / ai_extract / ai_summary / ai_transcription /
-- script_outline_gen / memory_tasks / agent_runs_sweeper / thumbnail.
--
-- This migration restores them to 'dbos' (the post-D7 canonical state).
--
-- Rollback strategy: still per-row UPDATE to disable a single task_type
-- via mode='off' (the "kill switch" pattern documented in
-- dbos_orchestrator.py — any non-'dbos' value disables dispatch).
-- Don't use 'shadow' — it has no semantics post-D7.

UPDATE public.dbos_workflow_routing
SET mode = 'dbos',
    notes = '196: restored to dbos (post-D7 canonical) — 195 was a regression',
    updated_by = 'migration_196'
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
AND mode = 'shadow';   -- only revert rows that 195 actually flipped
