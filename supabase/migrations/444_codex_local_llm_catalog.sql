-- 444: codex-local LLM catalog row (spec 2026-08-27-codex-local-text-llm).
--
-- Numbering note: this landed as 443 in the plan, but 443 was taken by
-- 443_transcript_event_types_phase2.sql on master while this branch was
-- alive. Renumbered to 444 — see the migration-collision discipline.
--
-- Not a server-side provider: chat turns that pick this row are dispatched
-- to the USER's own paired nous-codex daemon (kind="text"). No api_key,
-- no base_url — the credential is the user's local ~/.codex/auth.json.
-- actual_model is '' on purpose: empty means "the user's codex default";
-- admins may pin a model here later.
--
-- Platform row (owner_user_id NULL): visible to every user, but a user with
-- no online daemon gets a typed local_daemon_offline error, never a silent
-- fallback to a paid platform model.
BEGIN;

INSERT INTO public.mediahub_models
    (name, display_name, type, actual_provider, actual_model, api_key,
     is_enabled, sort_order, description)
VALUES
    ('Codex (Local)', 'Codex (Local)', 'llm', 'codex-local', '', '',
     TRUE, 30,
     'Text generation on your own machine via the paired nous-codex daemon. Plain text only — no tools or Skills.')
ON CONFLICT (name) DO NOTHING;

COMMIT;

NOTIFY pgrst, 'reload schema';
