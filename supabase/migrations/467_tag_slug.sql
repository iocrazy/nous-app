-- 467_tag_slug.sql
--
-- Give the tags the automation depends on a STABLE KEY, so their display names
-- can stop being load-bearing.
--
-- Today ~24 code sites look these tags up by their English `name`
-- (`Transcript`, `Summary`, `Analyze`, and the thirteen classification names),
-- and every one of those lookups fails SILENTLY when it misses: the AI simply
-- does not run, nothing is logged as an error, and one of them
-- (`download_helpers.py`'s summary de-dup guard) can dispatch a second summary
-- and bill for it twice. That is why system tags are currently read-only —
-- the protection exists to stop a rename from breaking the pipeline, not
-- because the user should not own their own tags.
--
-- `slug` is the key the code will use from here on. It is not shown in the UI
-- and no user-facing endpoint writes it, so renaming a tag is free.
--
-- Deliberately NOT NOT-NULL: only the tags automation actually keys off get a
-- slug. A user's own tag has none, and that absence is meaningful — it is what
-- will stop a hand-made tag called "Summary" from triggering the AI pipeline,
-- which it can do today (the read side matches on name and never checks the
-- tag's type).

ALTER TABLE public.tags ADD COLUMN IF NOT EXISTS slug text;

COMMENT ON COLUMN public.tags.slug IS
  'Stable automation key. NULL for ordinary tags. Never user-editable: the '
  'display name (name / name_zh) is the user''s to change, this is not.';

-- Partial unique: one row per slug, and the many NULLs do not collide.
CREATE UNIQUE INDEX IF NOT EXISTS uniq_tags_slug
  ON public.tags (slug)
  WHERE slug IS NOT NULL;

-- Backfill the sixteen tags the code references, matched on what the code
-- matches on TODAY (`type = 'system'` + the exact English name). Anything that
-- has already been renamed simply gets no slug, which is the honest outcome:
-- the code could not find it before this migration either.
--
-- `lower(name)` is the slug for every one of them, which keeps the mapping
-- obvious at the call sites (`Transcript` → `transcript`).
UPDATE public.tags
   SET slug = lower(name)
 WHERE type = 'system'
   AND slug IS NULL
   AND name IN (
     -- AI pipeline triggers (migration 183, grouped by migration 220)
     'Transcript', 'Summary', 'Analyze',
     -- Auto-classification targets (migration 012 seed; the keys of
     -- `classification_service.KEYWORD_MAPPING`)
     'Food', 'Tutorial', 'Comedy', 'Dance', 'Music', 'Beauty', 'Fashion',
     'Gaming', 'Pets', 'Travel', 'Tech', 'Sports', 'Vlog'
   );

NOTIFY pgrst, 'reload schema';
