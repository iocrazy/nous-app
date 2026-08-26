-- 440: resources.gen_params — normalised AI generation parameters.
--
-- Where the generation PROMPT already lives in gen_prompt / gen_prompt_negative,
-- the generation PARAMETERS (model, sampler, steps, cfg, seed, size, loras…)
-- had nowhere to go: the PNG extractor parsed them and threw them away, and
-- promote_generated_media left them on generated_media.params. One jsonb
-- column, one normalised shape regardless of source:
--
--   {"tool": "comfyui" | "a1111" | "nous",
--    "model", "model_hash", "sampler", "scheduler", "steps", "cfg", "seed",
--    "width", "height", "denoise", "loras": [..], "text_encoder", "vae",
--    "provider"}          -- every key optional; absent == unknown
--
-- Written by upload_postprocess (PNG text chunks), promote_generated_media
-- (generated_media.params/model/provider) and the resource_gen_params
-- backfill. Read by the resource detail Prompt section.
ALTER TABLE public.resources
    ADD COLUMN IF NOT EXISTS gen_params jsonb;

COMMENT ON COLUMN public.resources.gen_params IS
    'Normalised AI generation parameters (tool/model/sampler/steps/cfg/seed/size/loras…); every key optional. Sources: PNG metadata on upload, generated_media on promote, backfill.';
