-- 337_chat_media_object_store.sql
-- Phase 1a foundation for moving small chat/AI-generated images off the flat
-- filesystem tree into a Supabase Storage bucket (plan:
-- docs/superpowers/plans/2026-07-05-chat-images-object-storage.md).
--
-- This migration is INERT on its own: it provisions the bucket + a dedup
-- column. No code writes to the bucket until FEATURE_CHAT_MEDIA_OBJECT_STORE
-- flips on (Phase 1b wires the write path; Phase 2 flips the flag).

-- ============================================================================
-- Part 1: private `chat-media` bucket
-- ============================================================================
-- Private (public=false): unlike the legacy public `thumbnails` bucket (mig
-- 052), chat images must NOT be world-readable by URL. All access in Phase 1
-- goes through the backend service-role key (which bypasses RLS) — either a
-- signed URL or a stream proxy. Browser-direct RLS policies are Phase 2.

INSERT INTO storage.buckets (id, name, public)
VALUES ('chat-media', 'chat-media', false)
ON CONFLICT (id) DO NOTHING;

-- No storage.objects RLS policies are added here on purpose: with a private
-- bucket and backend-only service-role access, permissive policies would only
-- widen the surface. Phase 2 adds membership-joined SELECT policies when (and
-- if) we let the browser read objects directly.

-- ============================================================================
-- Part 2: content hash column on generated_media (dedup + integrity)
-- ============================================================================
-- sha256 of the file bytes. Populated only for new object-store writes; NULL
-- for every existing filesystem row (backfill is out of scope). Lets the write
-- path skip re-uploading an identical image (content-addressed key = dedup).

ALTER TABLE public.generated_media
  ADD COLUMN IF NOT EXISTS content_sha256 TEXT;

-- Partial index: dedup lookups are always (scope_id, content_sha256) and only
-- ever match rows that have a hash, so keep the index small.
CREATE INDEX IF NOT EXISTS idx_generated_media_scope_sha256
  ON public.generated_media (scope_id, content_sha256)
  WHERE content_sha256 IS NOT NULL;

-- PostgREST schema cache must be reloaded after a column add or the new column
-- 404s on the REST path until the next restart.
NOTIFY pgrst, 'reload schema';
