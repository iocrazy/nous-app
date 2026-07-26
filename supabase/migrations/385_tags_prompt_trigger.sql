-- 385: tags.prompt_trigger — user-configurable "Prompt tag" switch.
--
-- Assets carrying any tag with prompt_trigger=true show the Prompt panel
-- (collapsed row) and the grid-card badge even before prompt data exists.
-- NOT named show_prompt: that name is already used by canvas node data
-- (frontend/features/canvas-core/smart/factories.ts).

ALTER TABLE public.tags
    ADD COLUMN IF NOT EXISTS prompt_trigger BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN public.tags.prompt_trigger IS
    'When true, assets tagged with this tag surface the Prompt panel/badge.';
