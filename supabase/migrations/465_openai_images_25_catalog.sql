-- 465: gpt-image-2.5 through the OpenAI Images API (spec 2026-09-13).
-- Two disabled rows; the operator pastes the api_key in Admin → AI Models and
-- enables them. "(OpenAI API)" in the display name is the billing signal in
-- the canvas picker (per-token, unlike the codex subscription rows).
-- The codex rows drop the version number: on the subscription path the image
-- model is OpenAI's rollout decision, not ours (measured 2026-09-09).
BEGIN;

INSERT INTO public.mediahub_models
    (name, display_name, type, actual_provider, actual_model, api_key,
     is_enabled, sort_order, description)
VALUES
    ('openai-image-flare', 'GPT Image 2.5 Flare (OpenAI API)', 'image',
     'openai-images', 'gpt-image-2.5-flare', '', FALSE, 21,
     'OpenAI Images API, pay-as-you-go. Fast default; honours exact sizes and xhigh/max quality.'),
    ('openai-image-sunburst', 'GPT Image 2.5 Sunburst (OpenAI API)', 'image',
     'openai-images', 'gpt-image-2.5-sunburst', '', FALSE, 22,
     'OpenAI Images API, pay-as-you-go. Edit precision; slower than Flare.')
ON CONFLICT (name) DO NOTHING;

UPDATE public.mediahub_models SET display_name = 'GPT Image (Codex)'
 WHERE name = 'codex-image' AND display_name = 'GPT Image 2 (Codex)';
UPDATE public.mediahub_models SET display_name = 'GPT Image (Codex, local)'
 WHERE name = 'codex-local-image' AND display_name LIKE 'GPT Image 2%';

COMMIT;
NOTIFY pgrst, 'reload schema';
