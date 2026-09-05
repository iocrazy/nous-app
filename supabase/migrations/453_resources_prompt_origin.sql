-- 453: resources.prompt_origin — WHO wrote the prompt text on this row.
--
-- Three writers share gen_prompt / gen_prompt_zh / slide_prompts and until now
-- nothing recorded which one wrote last: PNG-metadata extraction and the
-- canvas's own generations ("extracted"), AI captioning ("captioned"), and the
-- user typing in the resource detail page ("typed"). Their reuse value differs
-- a lot, and the unified Prompts view (spec 2026-09-05-unified-prompts-library)
-- sorts and labels by it. Rule: origin follows the last writer of the positive
-- text; every writer stamps it in the same patch that writes the text.
--
-- Nullable: rows that predate this migration are backfilled by the
-- `resource_prompt_origin` admin backfill (dry-run first), not here — the
-- derivation looks at four columns and belongs in reviewable Python, and a
-- NULL is an honest "unknown" until it runs.

ALTER TABLE public.resources
    ADD COLUMN IF NOT EXISTS prompt_origin text
    CONSTRAINT resources_prompt_origin_check
        CHECK (prompt_origin IN ('typed', 'extracted', 'captioned'));

COMMENT ON COLUMN public.resources.prompt_origin IS
    'Last writer of gen_prompt/gen_prompt_zh/slide_prompts: typed | extracted | captioned (mig 453)';

-- PostgREST schema cache (see mig 284 for why NOTIFY rather than a restart).
NOTIFY pgrst, 'reload schema';
