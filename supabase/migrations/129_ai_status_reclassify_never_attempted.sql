-- =============================================================
-- Fix: migration 128 wrongly flipped zombie 'pending' to 'failed'.
-- Many rows were NEVER attempted — they inherited 'pending' from
-- migration 035's legacy DEFAULT on the old `videos` table (which
-- migration 067 later copied into resources). A user who never
-- clicked Transcribe/Summarize/Analyze shouldn't see 'failed';
-- the right state is 'none'.
--
-- Correct semantics:
--   never had any unified_task for this resource → 'none'
--   had a task that isn't currently live         → 'failed' (real failure)
-- =============================================================

-- ── transcript_status ───────────────────────────────────────
UPDATE public.resources r
   SET transcript_status = 'none'
 WHERE r.transcript_status = 'failed'
   AND NOT EXISTS (
     SELECT 1 FROM public.unified_tasks ut
     WHERE ut.resource_id::text = r.id::text
       AND ut.task_type IN ('ai_extract','ai_transcription','ai_pipeline')
   );

-- ── summary_status ──────────────────────────────────────────
UPDATE public.resources r
   SET summary_status = 'none'
 WHERE r.summary_status = 'failed'
   AND NOT EXISTS (
     SELECT 1 FROM public.unified_tasks ut
     WHERE ut.resource_id::text = r.id::text
       AND ut.task_type IN ('ai_summary','ai_pipeline')
   );

-- ── visual_analysis_status ──────────────────────────────────
UPDATE public.resources r
   SET visual_analysis_status = 'none'
 WHERE r.visual_analysis_status = 'failed'
   AND NOT EXISTS (
     SELECT 1 FROM public.unified_tasks ut
     WHERE ut.resource_id::text = r.id::text
       AND ut.task_type IN ('ai_visual_analysis','ai_pipeline')
   );

-- ── Sweep any remaining <1h 'pending' rows that never had a task
--    (dodged migration 128's updated_at cutoff). ─────────────
UPDATE public.resources r
   SET transcript_status = 'none'
 WHERE r.transcript_status = 'pending'
   AND NOT EXISTS (
     SELECT 1 FROM public.unified_tasks ut
     WHERE ut.resource_id::text = r.id::text
       AND ut.task_type IN ('ai_extract','ai_transcription','ai_pipeline')
   );

UPDATE public.resources r
   SET summary_status = 'none'
 WHERE r.summary_status = 'pending'
   AND NOT EXISTS (
     SELECT 1 FROM public.unified_tasks ut
     WHERE ut.resource_id::text = r.id::text
       AND ut.task_type IN ('ai_summary','ai_pipeline')
   );

UPDATE public.resources r
   SET visual_analysis_status = 'none'
 WHERE r.visual_analysis_status = 'pending'
   AND NOT EXISTS (
     SELECT 1 FROM public.unified_tasks ut
     WHERE ut.resource_id::text = r.id::text
       AND ut.task_type IN ('ai_visual_analysis','ai_pipeline')
   );
