-- Migration 345: allow image/video model types in the catalog (Ark Seedream t2i etc.)
--
-- The image provider_registry ships EMPTY, so image generation (shot Generate,
-- legacy canvas) resolves its provider from the mediahub_models catalog
-- (house rule: provider config lives in the DB, not env). But the existing
-- `mediahub_models_type_check` only permitted ('llm','embedding','tts','asr'),
-- so INSERTing a type='image' row was rejected — leaving the catalog with no
-- image row and every generation KeyErroring / raising "no image model
-- configured".
--
-- This widens the allowed set to include 'image' (needed now) and 'video'
-- (pre-authorized for the future video-provider path). Drop-then-add rather
-- than a rename so the predicate itself changes. Idempotent: DROP IF EXISTS +
-- a fresh ADD; re-apply is a no-op on an already-widened constraint because we
-- drop first.
--
-- The image catalog row itself is inserted separately on prod (admin-managed
-- credentials) — this migration only removes the schema barrier.

BEGIN;

ALTER TABLE public.mediahub_models
  DROP CONSTRAINT IF EXISTS mediahub_models_type_check;

ALTER TABLE public.mediahub_models
  ADD CONSTRAINT mediahub_models_type_check
  CHECK (type = ANY (ARRAY['llm', 'embedding', 'tts', 'asr', 'image', 'video']));

COMMIT;

-- Refresh PostgREST schema cache so the API picks up the widened constraint.
NOTIFY pgrst, 'reload schema';
