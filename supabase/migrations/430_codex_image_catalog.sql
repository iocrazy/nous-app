-- 430: codex (GPT Image 2) image provider catalog row.
--
-- CodexCliProvider drives the `gpt-image-2-skill` CLI over the machine
-- owner's local Codex OAuth session (no api_key — the mounted auth.json IS
-- the credential; api_key is NOT NULL so the row carries '').
--
-- Seeded DISABLED on purpose: this provider spends the machine owner's
-- personal ChatGPT subscription quota, so it must not be selectable by every
-- user the moment the backend deploys. The follow-up owner-scoping migration
-- binds the row to its owner and flips is_enabled — until then the row is
-- invisible to catalog queries (they all filter is_enabled).
BEGIN;

INSERT INTO public.mediahub_models
    (name, display_name, type, actual_provider, actual_model, api_key,
     is_enabled, sort_order, description)
VALUES
    ('codex-image', 'GPT Image 2 (Codex)', 'image', 'codex', 'gpt-5.4', '',
     FALSE, 20,
     'gpt-image-2-skill CLI over the local Codex OAuth session (image only)')
ON CONFLICT (name) DO NOTHING;

COMMIT;

NOTIFY pgrst, 'reload schema';
