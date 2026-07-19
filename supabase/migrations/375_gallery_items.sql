-- 375_gallery_items.sql
--
-- Gallery as a first-class resource entity (PR-A).
--
-- A "gallery" is one ``resources`` row (file_type='gallery',
-- mime_type='application/x-mediahub-gallery', source_type='upload') whose
-- child images are ordinary ``resources`` rows linked through this junction
-- table. The children are hidden from the normal library listing (the list
-- query excludes any resource that appears as a gallery_items.image_id) so a
-- gallery reads as a single tile in the grid.
--
-- Both FKs cascade on delete: dropping a gallery removes its junction rows
-- (not the child resources — those are real resources that may live on their
-- own), and dropping a child image removes its membership row.
--
-- RLS: backend-only table. Every access path goes through FastAPI with the
-- service-role client (ResourcesRepository); the frontend never touches it via
-- supabase-js. So the service-role-only lockdown pattern from
-- 364_rls_lockdown_project_characters_lib_entities.sql is the correct fix and
-- keeps the table off the anon PostgREST surface.

BEGIN;

CREATE TABLE IF NOT EXISTS public.gallery_items (
    gallery_id BIGINT NOT NULL
        REFERENCES public.resources(id) ON DELETE CASCADE,
    image_id BIGINT NOT NULL
        REFERENCES public.resources(id) ON DELETE CASCADE,
    position INT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (gallery_id, image_id),
    UNIQUE (gallery_id, position)
);

-- Reverse lookup: "is this resource a child of any gallery?" (the list-query
-- exclusion) and "which galleries contain this image?".
CREATE INDEX IF NOT EXISTS idx_gallery_items_image
    ON public.gallery_items (image_id);

ALTER TABLE public.gallery_items ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Service role full access on gallery_items"
    ON public.gallery_items;
CREATE POLICY "Service role full access on gallery_items"
    ON public.gallery_items FOR ALL
    USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');

NOTIFY pgrst, 'reload schema';

COMMIT;
