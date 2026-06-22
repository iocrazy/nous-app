-- 308 — allow resources.source_type='generated' (Generated Media Plan 3 promote).
ALTER TABLE public.resources DROP CONSTRAINT IF EXISTS resources_source_type_check;
ALTER TABLE public.resources ADD CONSTRAINT resources_source_type_check
  CHECK (source_type IN ('web', 'upload', 'generated'));
