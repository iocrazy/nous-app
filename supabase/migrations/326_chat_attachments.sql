-- 326 — Unify media layer: chat uploads become generated_media rows (the
-- single "staged media" tier). Drops the short-lived chat_attachments table
-- (branch-only, never deployed) and adds channel_id to generated_media.
DROP TABLE IF EXISTS public.chat_attachments;

ALTER TABLE public.generated_media
    ADD COLUMN IF NOT EXISTS channel_id BIGINT
    REFERENCES public.channels(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_genmedia_channel
    ON public.generated_media (channel_id);

COMMENT ON TABLE public.generated_media IS
    'Unified "staged media" tier: AI generations (origin_kind agent_run/canvas_run) '
    'and chat uploads (origin_kind chat_upload, channel_id set). Cheap/high-churn; '
    'promote to resources on keep/use. Backend-only (service-role RLS).';
