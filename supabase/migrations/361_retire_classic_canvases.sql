-- Retire classic canvases (canvas 1.0 engine removed 2026-07-13):
-- soft-delete every remaining classic row into the trash (30-day window).
UPDATE public.canvases SET deleted_at = now() WHERE kind = 'classic' AND deleted_at IS NULL;
