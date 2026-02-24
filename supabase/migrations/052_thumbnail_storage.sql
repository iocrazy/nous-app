-- 052_thumbnail_storage.sql
-- Create Supabase Storage bucket for resource thumbnails
-- and set up RLS policies for upload and public read access.

-- ============================================================================
-- Part 1: Create thumbnails bucket (public read access)
-- ============================================================================

INSERT INTO storage.buckets (id, name, public)
VALUES ('thumbnails', 'thumbnails', true)
ON CONFLICT (id) DO NOTHING;

-- ============================================================================
-- Part 2: RLS policies for thumbnails bucket
-- ============================================================================

-- Authenticated users can upload thumbnails
CREATE POLICY "Authenticated users can upload thumbnails"
ON storage.objects FOR INSERT TO authenticated
WITH CHECK (bucket_id = 'thumbnails');

-- Authenticated users can update (upsert) their thumbnails
CREATE POLICY "Authenticated users can update thumbnails"
ON storage.objects FOR UPDATE TO authenticated
USING (bucket_id = 'thumbnails');

-- Public read access for thumbnails
CREATE POLICY "Public read access for thumbnails"
ON storage.objects FOR SELECT TO public
USING (bucket_id = 'thumbnails');
