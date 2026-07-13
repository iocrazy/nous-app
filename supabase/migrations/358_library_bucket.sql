-- 358_library_bucket.sql
-- Storage unification (spec 2026-07-12): one master bucket for uploads /
-- project_files / storyboard originals. Private — service key only.
INSERT INTO storage.buckets (id, name, public)
VALUES ('library', 'library', false)
ON CONFLICT (id) DO NOTHING;

-- Cleanup: mig 052's thumbnails bucket has been dormant (0 objects) since the
-- thumbnail workload moved to filesystem+nginx. Guard: only drop when empty.
DELETE FROM storage.buckets b
WHERE b.id = 'thumbnails'
  AND NOT EXISTS (SELECT 1 FROM storage.objects o WHERE o.bucket_id = 'thumbnails');
