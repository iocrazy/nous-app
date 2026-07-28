-- 392: prompt 数据线 — 逐张提示词 + JSON 格式反推结果
ALTER TABLE public.resources
    ADD COLUMN IF NOT EXISTS slide_prompts JSONB,
    ADD COLUMN IF NOT EXISTS gen_prompt_json TEXT;
COMMENT ON COLUMN public.resources.slide_prompts IS
    'Per-slide prompts for download albums, keyed by slide filename: {"<name>": {en, zh, neg_en, neg_zh}}. Upload galleries use each child resource''s own gen_prompt columns instead.';
COMMENT ON COLUMN public.resources.gen_prompt_json IS
    'Structured JSON prompt (subject/style/composition/lighting/color/text/aspect_ratio) produced by the caption workflow.';
