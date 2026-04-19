-- =============================================================
-- 1) Create ai_task_status enum + migrate 3 varchar columns to it.
-- 2) Reset zombie 'pending' AI statuses (>1h, no active unified_task)
--    — complements reap_stuck_pending_tasks on unified_tasks.
-- See 129 for a correction pass that re-classifies 'never attempted'.
-- =============================================================

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'ai_task_status') THEN
    CREATE TYPE public.ai_task_status AS ENUM (
      'none','pending','processing','completed','failed','skipped'
    );
  END IF;
END$$;


-- ── Data cleanup BEFORE type change (flip truly-stuck >1h pending) ─
WITH live_pending AS (
  SELECT DISTINCT resource_id::text AS rid
  FROM public.unified_tasks
  WHERE status IN ('pending', 'processing', 'running')
    AND task_type IN ('ai_extract','ai_transcription','ai_summary','ai_pipeline','ai_visual_analysis')
    AND resource_id IS NOT NULL
)
UPDATE public.resources r
   SET transcript_status = 'failed'
 WHERE r.transcript_status = 'pending'
   AND r.updated_at < NOW() - INTERVAL '1 hour'
   AND r.id::text NOT IN (SELECT rid FROM live_pending);

WITH live_pending AS (
  SELECT DISTINCT resource_id::text AS rid
  FROM public.unified_tasks
  WHERE status IN ('pending', 'processing', 'running')
    AND task_type IN ('ai_extract','ai_transcription','ai_summary','ai_pipeline','ai_visual_analysis')
    AND resource_id IS NOT NULL
)
UPDATE public.resources r
   SET summary_status = 'failed'
 WHERE r.summary_status = 'pending'
   AND r.updated_at < NOW() - INTERVAL '1 hour'
   AND r.id::text NOT IN (SELECT rid FROM live_pending);

WITH live_pending AS (
  SELECT DISTINCT resource_id::text AS rid
  FROM public.unified_tasks
  WHERE status IN ('pending', 'processing', 'running')
    AND task_type IN ('ai_extract','ai_transcription','ai_summary','ai_pipeline','ai_visual_analysis')
    AND resource_id IS NOT NULL
)
UPDATE public.resources r
   SET visual_analysis_status = 'failed'
 WHERE r.visual_analysis_status = 'pending'
   AND r.updated_at < NOW() - INTERVAL '1 hour'
   AND r.id::text NOT IN (SELECT rid FROM live_pending);


-- ── Drop partial indexes (::text cast isn't immutable for enum) ──
DROP INDEX IF EXISTS public.idx_resources_transcript_status;
DROP INDEX IF EXISTS public.idx_resources_summary_status;
DROP INDEX IF EXISTS public.idx_resources_visual_analysis_status;

-- ── Column migration: varchar → ai_task_status ─────────────
ALTER TABLE public.resources ALTER COLUMN transcript_status      DROP DEFAULT;
ALTER TABLE public.resources ALTER COLUMN summary_status          DROP DEFAULT;
ALTER TABLE public.resources ALTER COLUMN visual_analysis_status  DROP DEFAULT;

ALTER TABLE public.resources
  ALTER COLUMN transcript_status      TYPE public.ai_task_status USING transcript_status::text::public.ai_task_status,
  ALTER COLUMN summary_status         TYPE public.ai_task_status USING summary_status::text::public.ai_task_status,
  ALTER COLUMN visual_analysis_status TYPE public.ai_task_status USING visual_analysis_status::text::public.ai_task_status;

ALTER TABLE public.resources ALTER COLUMN transcript_status      SET DEFAULT 'none'::public.ai_task_status;
ALTER TABLE public.resources ALTER COLUMN summary_status          SET DEFAULT 'none'::public.ai_task_status;
ALTER TABLE public.resources ALTER COLUMN visual_analysis_status  SET DEFAULT 'none'::public.ai_task_status;

-- ── Recreate partial indexes with enum-native predicate ────
CREATE INDEX idx_resources_transcript_status
  ON public.resources (transcript_status)
  WHERE transcript_status <> 'none'::public.ai_task_status;

CREATE INDEX idx_resources_summary_status
  ON public.resources (summary_status)
  WHERE summary_status <> 'none'::public.ai_task_status;
