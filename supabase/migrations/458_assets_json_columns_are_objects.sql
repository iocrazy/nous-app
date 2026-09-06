-- 458_assets_json_columns_are_objects.sql
--
-- `attrs` / `platform_params` / `tags` are `jsonb NOT NULL DEFAULT '{}'`, and
-- NOT NULL does not stop the JSON value `null`. On 2026-09-06 one hand-seeded
-- system preset ("360全景图") carried `platform_params = 'null'::jsonb`; because
-- presets are unioned into every scope's listing, `GET /assets` (All / Prompts
-- tabs) and `GET /assets/search` (the chat @-picker) raised
-- ResponseValidationError for EVERY user until the row was repaired by hand.
-- The repair is repeated here idempotently, then the shape is enforced at the
-- table so the next seed cannot reintroduce it.
UPDATE public.assets
   SET attrs           = CASE WHEN jsonb_typeof(attrs)           = 'object' THEN attrs           ELSE '{}'::jsonb END,
       platform_params = CASE WHEN jsonb_typeof(platform_params) = 'object' THEN platform_params ELSE '{}'::jsonb END,
       tags            = CASE WHEN jsonb_typeof(tags)            = 'object' THEN tags            ELSE '{}'::jsonb END
 WHERE jsonb_typeof(attrs) <> 'object'
    OR jsonb_typeof(platform_params) <> 'object'
    OR jsonb_typeof(tags) <> 'object';

ALTER TABLE public.assets DROP CONSTRAINT IF EXISTS assets_json_columns_are_objects;
ALTER TABLE public.assets ADD CONSTRAINT assets_json_columns_are_objects
  CHECK (jsonb_typeof(attrs) = 'object'
     AND jsonb_typeof(platform_params) = 'object'
     AND jsonb_typeof(tags) = 'object');
