-- Scope parsed_media reads/writes to the owner of a resource that points to it.
--
-- Found by the same behavioral RLS audit as migration 265 (2026-06-07): any
-- authenticated user could SELECT all 859 parsed_media rows and UPDATE any of
-- them, because the policies were `auth.role() = 'authenticated'` (i.e. any
-- logged-in user). parsed_media has NO owner column — it is a content-deduped
-- shared cache: one row per parsed video, shared across users who parse the same
-- content (verified on prod: 2 rows are referenced by resources from 2 distinct
-- creators each). Ownership lives in the `resources` layer
-- (resources.media_id -> parsed_media.id, resources.creator_id = owner).
--
-- So the correct rule is JOIN-based: "you may see/modify a parsed_media row iff
-- you own a resource that points to it." This serves the dedup case correctly
-- (both owners of a shared row can see it; nobody else can) and matches how the
-- frontend reads it (useLibrary.ts point-fetches parsed_media by id for a
-- resource the user already owns). Indexes idx_resources_media_id +
-- idx_resources_creator make the EXISTS sub-select an index lookup.
--
-- Scope:
--   * SELECT  -> EXISTS(owned resource)   [was: any authenticated — the read leak]
--   * UPDATE  -> EXISTS(owned resource)   [was: any authenticated — write exposure]
--   * INSERT  -> UNCHANGED. A brand-new parse inserts parsed_media BEFORE its
--               resource exists, so an EXISTS check would chicken-and-egg fail.
--               The frontend never inserts parsed_media directly (grep-verified);
--               the backend inserts via service_role (bypassrls). Adding a row to
--               a content-deduped shared cache is benign.
--   * DELETE  -> UNCHANGED (already admin-only).
--
-- Backend is unaffected (service_role / postgres pooler, both bypassrls). RLS
-- here only governs the frontend publishable-key path. Idempotent.

DROP POLICY IF EXISTS "Authenticated users can view all videos"   ON public.parsed_media;
DROP POLICY IF EXISTS "Authenticated users can update videos"     ON public.parsed_media;
DROP POLICY IF EXISTS "View parsed_media of owned resources"      ON public.parsed_media;
DROP POLICY IF EXISTS "Update parsed_media of owned resources"    ON public.parsed_media;

CREATE POLICY "View parsed_media of owned resources"
    ON public.parsed_media
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM public.resources r
            WHERE r.media_id = parsed_media.id
              AND r.creator_id = (SELECT auth.uid())
        )
    );

CREATE POLICY "Update parsed_media of owned resources"
    ON public.parsed_media
    FOR UPDATE
    USING (
        EXISTS (
            SELECT 1 FROM public.resources r
            WHERE r.media_id = parsed_media.id
              AND r.creator_id = (SELECT auth.uid())
        )
    );

-- RLS does not gate TRUNCATE/REFERENCES/TRIGGER (table-level privileges). The
-- blanket Supabase grant gave anon+authenticated TRUNCATE on parsed_media — a
-- table-wipe vector. Revoke it (SELECT/INSERT/UPDATE/DELETE stay grant+RLS-gated;
-- the INSERT path is preserved).
REVOKE TRUNCATE, REFERENCES, TRIGGER ON public.parsed_media FROM anon, authenticated;

NOTIFY pgrst, 'reload schema';
