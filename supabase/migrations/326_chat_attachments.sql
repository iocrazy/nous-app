-- 326 — chat_attachments: independent store for chat image uploads.
--
-- Chat images are NOT the resource library. Each image posted to a channel lands
-- here with provenance (scope_id=team, channel_id, creator_id). The file is
-- written to {DOWNLOAD_PATH}/teams/{team}/chat/{uuid}/{filename}. Only when the
-- user explicitly "Saves to library" is the file copied into resources (promote).
--
-- Mirrors generated_media (307): service-role-only RLS so PostgREST never exposes
-- rows to anon/authenticated. Backend reads/writes via the direct engine (BYPASSRLS).

CREATE TABLE IF NOT EXISTS public.chat_attachments (
    id                   BIGINT      PRIMARY KEY DEFAULT generate_snowflake_id(),
    scope_id             BIGINT      NOT NULL,           -- teams.id (owning team of the channel)
    channel_id           BIGINT      NOT NULL REFERENCES public.channels(id) ON DELETE CASCADE,
    creator_id           UUID        NOT NULL,           -- the uploading user
    mime                 TEXT,
    file_path            TEXT        NOT NULL,           -- relative to DOWNLOAD_PATH
    file_size_bytes      BIGINT,
    width                INT,
    height               INT,
    promoted_resource_id BIGINT,                         -- set when user "Saves to library"
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chat_attachments_channel
    ON public.chat_attachments (channel_id);

ALTER TABLE public.chat_attachments ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS chat_attachments_service_role_all ON public.chat_attachments;
CREATE POLICY chat_attachments_service_role_all ON public.chat_attachments
    FOR ALL TO service_role USING (true) WITH CHECK (true);

COMMENT ON TABLE public.chat_attachments IS
    'Independent chat image store (Task 2, plan 2026-06-30). '
    'Files live at {DOWNLOAD_PATH}/teams/{team}/chat/{uuid}/{filename}. '
    'Promote to resources (opt-in copy) via POST /api/v1/chat/attachments/{id}/promote. '
    'Backend-only (service-role RLS, same posture as generated_media mig 307).';
