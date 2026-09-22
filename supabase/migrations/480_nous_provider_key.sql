-- 480: the self-hosted engine gets its own provider key, and retired
-- catalog rows stop crowding Admin → AI Models.
--
-- WHY
-- ---
-- Admin → AI Models groups rows into provider cards by `actual_provider` and
-- renders that raw string as the card title. Three rows served by our OWN
-- nous-engine gateway carried `actual_provider = 'openai'`, so the card for
-- our self-hosted engine read "openai" — naming a vendor we never call.
--
-- Nothing was broken: `OpenAIProtocol` and `NousProtocol` both build a thin
-- `OpenAICompatibleAdapter` over the row's base_url. This migration changes
-- the NAME, not the wire behaviour.
--
-- DEPLOY ORDER IS FREE. run-migration.yml and deploy-gpu.yml fire
-- independently (CLAUDE.md: "migration 与代码部署无顺序保证"), so both
-- intermediate states must work, and both do:
--   * code first  — rows still on 'openai' keep using OpenAIProtocol.
--   * migration first — rows on 'nous' meet a build that has never heard of
--     the key, `get_chat_protocol` falls back to the default qwen protocol,
--     which is the same OpenAI-compatible adapter over the same base_url.
-- Pinned by test_migration_and_code_are_safe_in_either_deploy_order.

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. Move the nous-engine rows off the OpenAI vendor key.
-- ---------------------------------------------------------------------------
-- Matched by NAME, deliberately, not by a base_url pattern: a predicate like
-- "base_url NOT ILIKE '%api.openai.com%'" would also sweep up an Azure or
-- OpenRouter proxy row, which really is OpenAI and must keep that card. The
-- three names below are the engine's entire footprint as of 2026-09-21.
--
-- The `actual_provider = 'openai'` guard makes this idempotent and keeps it
-- from touching a row someone has already re-pointed somewhere else.
DO $$
DECLARE
    moved INT;
BEGIN
    UPDATE public.mediahub_models
    SET actual_provider = 'nous'
    WHERE actual_provider = 'openai'
      AND name IN (
          'nous-qwen3-llm',
          'nous-qwen3-embedding-8b',
          'mediahub-moss-asr'
      );
    GET DIAGNOSTICS moved = ROW_COUNT;
    RAISE NOTICE '[480] nous-engine rows moved openai -> nous: %', moved;

    -- Not an error: a fresh database (schema-drift's ephemeral one) has none
    -- of these rows, and a re-run has already moved them.
    IF moved = 0 THEN
        RAISE NOTICE '[480] nothing to move (fresh db, or already migrated)';
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 2. NO ROWS ARE DELETED HERE — and that is the finding, not an omission.
-- ---------------------------------------------------------------------------
-- This migration was drafted with a second block that would have dropped
-- `jimeng-cli-image`, `jimeng-cli-seedance` and `Codex (Local)` as the retired
-- server-side halves of families that had moved to the paired daemon. Reading
-- the production catalog first (2026-09-21) killed that idea:
--
--   jimeng-cli-image      enabled, owner_user_id = 8e1584e3-…
--   jimeng-cli-seedance   enabled, owner_user_id = 8e1584e3-…
--   codex-image           enabled, owner_user_id = 8e1584e3-…
--   'Codex (Local)'       does not exist — already gone
--
-- They looked retired only from outside: migration 431's RLS hides owner-scoped
-- rows, so a read that is not the owner sees exactly the same nothing as it
-- would for a disabled row. "Absent from the list I can see" is not "retired",
-- the same way an empty probe result is not a negative finding.
--
-- The rows that ARE disabled today (`mediahub-doubao-seedream-t2i`,
-- `openai-image-flare`, `openai-image-sunburst`) are deliberately left alone:
-- disabled is a switch someone flipped, not a tombstone.

COMMIT;

NOTIFY pgrst, 'reload schema';
