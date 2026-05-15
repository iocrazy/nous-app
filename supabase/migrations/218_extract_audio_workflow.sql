-- 218_extract_audio_workflow.sql
--
-- ⑤ — audio extraction promoted from an inline step inside
-- run_download_step to its own DBOS workflow (extract_audio_workflow)
-- with its own task_tracking row + visible status.
--
-- Two changes:
--   1. parsed_media.extract_audio_status — the workflow writes this
--      (pending → processing → completed/failed). The homepage MediaCard
--      green audio icon reads it to render a 3-state indicator instead
--      of the old binary "extract_audio_path exists?" check.
--
--      NOTE: distinct from music_download_status, which means "music URL
--      was downloaded". ffmpeg-extracted audio is NOT downloaded music —
--      keeping the fields separate is deliberate (see download_helpers).
--
--   2. dbos_workflow_routing — explicit 'dbos' row for the new
--      'extract_audio' task_type. start_workflow_routed defaults unknown
--      task_types to 'dbos' already, but an explicit row keeps the ops
--      dashboard honest and gives a per-task kill-switch.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS + ON CONFLICT DO NOTHING.

ALTER TABLE public.parsed_media
  ADD COLUMN IF NOT EXISTS extract_audio_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (extract_audio_status IN
      ('pending', 'processing', 'completed', 'failed', 'skipped'));

COMMENT ON COLUMN public.parsed_media.extract_audio_status IS
  'Status of the extract_audio_workflow for this media: pending (not run
   yet) / processing / completed / failed / skipped (no video to extract
   audio from, e.g. image carousels). Distinct from music_download_status
   (which tracks downloaded music URLs). Drives the MediaCard audio icon.';

INSERT INTO public.dbos_workflow_routing (task_type, mode, notes) VALUES
  ('extract_audio', 'dbos', 'audio extraction as own DBOS workflow')
ON CONFLICT (task_type) DO NOTHING;
