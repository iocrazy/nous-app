-- 384: resources.gen_prompt_negative / _zh — negative generation prompt.
--
-- Extends the bilingual asset prompt block (284/289) with the negative
-- side. A1111-style assets carry "Negative prompt: ..." in PNG metadata;
-- the upload extractor and the info-panel editor both write here.
-- Char cap (20000) is enforced at the API schema layer, same as gen_prompt.

ALTER TABLE public.resources
    ADD COLUMN IF NOT EXISTS gen_prompt_negative TEXT,
    ADD COLUMN IF NOT EXISTS gen_prompt_negative_zh TEXT;

COMMENT ON COLUMN public.resources.gen_prompt_negative IS
    'Negative AI generation prompt (EN side), counterpart of gen_prompt.';
COMMENT ON COLUMN public.resources.gen_prompt_negative_zh IS
    'Negative AI generation prompt (ZH side), counterpart of gen_prompt_zh.';
