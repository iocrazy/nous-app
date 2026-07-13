-- 363_resources_source_type_derived.sql
--
-- The canvas derive services (crop / split / grid / mask / outpaint) write
-- resources rows with source_type='derived' (derive_persistence.py:169), but
-- no migration ever widened resources_source_type_check past
-- ('web','upload','generated') — 309's shape. Every derive insert has been
-- 500-ing with a 23514 CheckViolation since the derive features shipped
-- (prod has zero source_type='derived' rows). Same failure class as 362's
-- canvases_kind_check: the Python enum learned a value, the DB CHECK didn't.
--
-- Idempotent: DROP IF EXISTS + re-ADD validates existing rows (all of which
-- are within the old set, a strict subset of the new one).

ALTER TABLE public.resources DROP CONSTRAINT IF EXISTS resources_source_type_check;
ALTER TABLE public.resources ADD CONSTRAINT resources_source_type_check
  CHECK (source_type IN ('web', 'upload', 'generated', 'derived'));

NOTIFY pgrst, 'reload schema';
