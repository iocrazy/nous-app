-- 067_ai_status_on_resources.sql
-- Move AI status tracking from parsed_media (shared) to resources (per-user)
-- Add group_id to unified_tasks for linking AI sub-tasks
--
-- Why per-user: parsed_media is shared (one record per media file).
-- AI operations are triggered per-user, so status should live on resources
-- (which has creator_id). Transcript/summary artifacts remain shared on
-- media_transcripts/media_summaries (same audio = same transcript).

BEGIN;

-- ═══════════════════════════════════════════════════════════════
-- 1. Add AI status columns to resources (per-user tracking)
-- ═══════════════════════════════════════════════════════════════

ALTER TABLE resources
  ADD COLUMN IF NOT EXISTS transcript_status VARCHAR(20) NOT NULL DEFAULT 'none',
  ADD COLUMN IF NOT EXISTS summary_status VARCHAR(20) NOT NULL DEFAULT 'none',
  ADD COLUMN IF NOT EXISTS visual_analysis_status VARCHAR(20) NOT NULL DEFAULT 'none';

COMMENT ON COLUMN resources.transcript_status IS 'Per-user AI transcript status: none|pending|processing|completed|failed';
COMMENT ON COLUMN resources.summary_status IS 'Per-user AI summary status: none|pending|processing|completed|failed';
COMMENT ON COLUMN resources.visual_analysis_status IS 'Per-user AI visual analysis status: none|pending|processing|completed|failed';

-- Partial indexes for filtering resources by AI status (only non-default values)
CREATE INDEX IF NOT EXISTS idx_resources_transcript_status
  ON resources(transcript_status) WHERE transcript_status != 'none';
CREATE INDEX IF NOT EXISTS idx_resources_summary_status
  ON resources(summary_status) WHERE summary_status != 'none';

-- ═══════════════════════════════════════════════════════════════
-- 2. Add group_id to unified_tasks (link AI sub-tasks)
-- ═══════════════════════════════════════════════════════════════

ALTER TABLE unified_tasks
  ADD COLUMN IF NOT EXISTS group_id UUID;

CREATE INDEX IF NOT EXISTS idx_unified_tasks_group
  ON unified_tasks(group_id) WHERE group_id IS NOT NULL;

COMMENT ON COLUMN unified_tasks.group_id IS 'Groups related sub-tasks (e.g. 3 AI steps for same media)';

-- ═══════════════════════════════════════════════════════════════
-- 3. Backfill: copy AI status from parsed_media to resources
--    Only updates resources that have a linked media_id and where
--    parsed_media has non-default AI status values.
-- ═══════════════════════════════════════════════════════════════

UPDATE resources r
SET
  transcript_status = COALESCE(pm.transcript_status, 'none'),
  summary_status = COALESCE(pm.summary_status, 'none'),
  visual_analysis_status = COALESCE(pm.visual_analysis_status, 'none')
FROM parsed_media pm
WHERE r.media_id = pm.id
  AND r.transcript_status = 'none'
  AND (
    (pm.transcript_status IS NOT NULL AND pm.transcript_status != 'none')
    OR (pm.summary_status IS NOT NULL AND pm.summary_status != 'none')
    OR (pm.visual_analysis_status IS NOT NULL AND pm.visual_analysis_status != 'none')
  );

COMMIT;
