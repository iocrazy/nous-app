-- 348: rename-deprecate the legacy storyboard workbench tables (Phase B P4
-- cutover follow-up — PR-V2 #1109 tombstoned every /storyboard/* route with
-- 410 Gone on 2026-07-06; the 410s have been stable in prod since).
--
-- RENAME, not DROP (reversibility first — spec v3 §2.3 deviation recorded in
-- the P4 plan). Data at retirement time: storyboard_projects=1 test row,
-- frames=0. The kept bridge table `script_storyboard_links` keeps its FKs —
-- Postgres FK constraints track table oids, so they follow the rename and
-- stay valid. `build_project_style_fragment` (the only active-path reader)
-- swallows all failures by design ("style is seasoning").
--
-- Restore: ALTER TABLE zzz_deprecated_<name> RENAME TO <name>;
-- DROP is a future housekeeping decision once a release cycle passes clean.

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'storyboard_assets',
        'storyboard_characters',
        'storyboard_edges',
        'storyboard_frame_characters',
        'storyboard_frames',
        'storyboard_nodes',
        'storyboard_projects',
        'storyboard_video_assets'
    ] LOOP
        IF EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = t
        ) AND NOT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'zzz_deprecated_' || t
        ) THEN
            EXECUTE format('ALTER TABLE public.%I RENAME TO %I', t, 'zzz_deprecated_' || t);
            EXECUTE format(
                'COMMENT ON TABLE public.%I IS %L',
                'zzz_deprecated_' || t,
                'Retired 2026-07-07 (Phase B P4 cutover, PR-V2 #1109 tombstoned the '
                || 'routes with 410). Restore: ALTER TABLE RENAME back. '
                || 'See docs/superpowers/plans/2026-07-07-phase-b-p4-versioning.md Task 5.'
            );
        END IF;
    END LOOP;
END $$;

NOTIFY pgrst, 'reload schema';
