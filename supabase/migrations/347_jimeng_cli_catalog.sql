-- Migration 347: jimeng-cli catalog seed rows
--
-- Registers the dreamina (即梦) CLI as an image + video generation provider in
-- the mediahub_models catalog. db_registry.resolve_image_provider /
-- resolve_video_provider dispatch on actual_provider='jimeng-cli' → the
-- subprocess JimengCliProvider (no api_key — the CLI's OAuth session on the NAS
-- is the credential; see docs/runbook/jimeng-cli.md).
--
-- Columns verified against information_schema (mediahub_models ← nous_models
-- rename, mig 334): there is NO separate `provider` column — dispatch keys on
-- `actual_provider`. `api_key` is NOT NULL, so the CLI rows carry '' (empty),
-- not NULL. `type` accepts 'image'/'video' since mig 345.
--
-- Idempotent: name is UNIQUE → ON CONFLICT (name) DO NOTHING. Re-apply is a
-- no-op. The Ark image row (admin-inserted on prod) stays enabled as a
-- lower-priority fallback; the dispatcher prefers jimeng-cli when both enabled.

BEGIN;

INSERT INTO public.mediahub_models
    (name, display_name, type, actual_provider, actual_model, api_key,
     is_enabled, sort_order, description)
VALUES
    ('jimeng-cli-image', 'Dreamina (即梦) Image', 'image', 'jimeng-cli', '5.0', '',
     TRUE, 10, 'dreamina CLI text-to-image (primary shot Generate provider)'),
    ('jimeng-cli-seedance', 'Dreamina (即梦) Video', 'video', 'jimeng-cli',
     'seedance2.0fast', '',
     TRUE, 10, 'dreamina CLI text/image-to-video (Seedance 2.0 fast)')
ON CONFLICT (name) DO NOTHING;

COMMIT;

-- Refresh PostgREST's schema/row cache.
NOTIFY pgrst, 'reload schema';
