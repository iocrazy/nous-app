-- Migration 140: Relax legacy NOT NULL constraints superseded by Phase 1 markdown fields.
--
-- Migration 138 introduced identity_md / soul_md / agent_md (ai_agents) and
-- body_md (skills) to replace the legacy single-text `persona` / `content_md`
-- columns. The seed loader and the new editor UI only populate the new
-- fields; the legacy columns are kept as runtime fallbacks inside the prompt
-- composer (see app/services/prompt_composer.py) but are no longer required.
--
-- Keeping them NOT NULL causes inserts from the seed loader to fail with
-- 23502. Making them nullable lets the new surface write rows cleanly while
-- readers continue to check the legacy column opportunistically when
-- present.

BEGIN;

ALTER TABLE public.ai_agents
    ALTER COLUMN persona DROP NOT NULL;

ALTER TABLE public.skills
    ALTER COLUMN content_md DROP NOT NULL;

COMMENT ON COLUMN public.ai_agents.persona IS
    'Legacy single-text persona (superseded by identity_md / soul_md / agent_md). Kept nullable for backward-compatible fallback in prompt_composer.';

COMMENT ON COLUMN public.skills.content_md IS
    'Legacy single-text skill content (superseded by body_md). Kept nullable for backward-compatible fallback.';

COMMIT;

-- Trigger PostgREST schema reload so the column nullability change is
-- picked up by the API layer without a manual reboot.
NOTIFY pgrst, 'reload schema';
