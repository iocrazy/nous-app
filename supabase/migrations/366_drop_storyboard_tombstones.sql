-- 366: drop the eight storyboard tombstone tables — finish the retirement.
--
-- WHY THIS EXISTS
-- ---------------
-- The ReactFlow Storyboard Workbench was retired in stages:
--   #1109  turned every /storyboard/* HTTP route into 410 Gone
--   mig 348 rename-deprecated the eight tables to zzz_deprecated_storyboard_*
--           (rename only — the tables and their 0 rows were left in place)
--   #1408  extracted the one generic capability (image/video generation) the
--           live script editor still borrowed, off the storyboard module
--   this PR deletes all remaining storyboard business code + the 8 ORM models
--
-- With the models gone, those eight tables would become "unmapped live tables"
-- that the schema-drift gate flags. Rather than exempt them, this migration
-- drops them — the coherent end state the #1408/this-PR sequence was building
-- toward, and the piece mig 348's rename explicitly deferred.
--
-- BRIDGE TABLE (kept)
-- ------------------
-- public.script_storyboard_links stays (a live table, columns + data
-- preserved). Its two FKs pointed at the tombstones being dropped:
--   script_storyboard_links_storyboard_node_id_fkey    -> ..._nodes.id
--   script_storyboard_links_storyboard_project_id_fkey -> ..._projects.id
-- Those constraints are dropped explicitly below (rather than left to the
-- table-drop CASCADE) so the intent is legible; storyboard_node_id /
-- storyboard_project_id remain as plain bigint columns with no FK — the same
-- convention as auth.users references (see app/models/__init__.py).
--
-- SAFETY
-- ------
-- No SET ROLE (mig 176's lesson, restated in mig 365: self-demoting to a role
-- that does not own the table yields "permission denied"; run as the connecting
-- role, which is the owner under CI). Guarded: refuses to drop any tombstone
-- that is not empty. Idempotent: a second run is a clean no-op.
--
-- THE ONE KNOWN TEST ROW (first prod run refused — the guard working)
-- -------------------------------------------------------------------
-- mig 348's own header recorded "Data at retirement time: storyboard_projects=
-- 1 test row, frames=0", and the first prod apply of this migration was
-- correctly refused by the guard on exactly that row. Inspected read-only on
-- prod 2026-07-16 before writing the removal below:
--
--   id=324214569136407  team_id=310812366953241  name='12312'
--   created 2026-07-05 (the retirement window), description/cover empty,
--   viewport at defaults, status=active,
--   script_storyboard_links references: 0
--
-- A keyboard-mash test row, unreachable since #1109's 410s, referenced by
-- nothing. It is deleted BY EXACT ID below — the guard itself stays at the
-- strict 0-row discipline so any OTHER unexpected row still refuses the drop.

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
     WHERE table_schema = 'public'
       AND table_name = 'zzz_deprecated_storyboard_projects'
  ) THEN
    DELETE FROM public.zzz_deprecated_storyboard_projects
     WHERE id = 324214569136407 AND name = '12312';
  END IF;
END $$;

DO $$
DECLARE
  v_tbl TEXT;
  v_cnt BIGINT;
  v_tables TEXT[] := ARRAY[
    'zzz_deprecated_storyboard_frame_characters',
    'zzz_deprecated_storyboard_video_assets',
    'zzz_deprecated_storyboard_frames',
    'zzz_deprecated_storyboard_edges',
    'zzz_deprecated_storyboard_nodes',
    'zzz_deprecated_storyboard_characters',
    'zzz_deprecated_storyboard_assets',
    'zzz_deprecated_storyboard_projects'
  ];
BEGIN
  FOREACH v_tbl IN ARRAY v_tables LOOP
    IF EXISTS (
      SELECT 1 FROM information_schema.tables
       WHERE table_schema = 'public' AND table_name = v_tbl
    ) THEN
      EXECUTE format('SELECT COUNT(*) FROM public.%I', v_tbl) INTO v_cnt;
      IF v_cnt > 0 THEN
        RAISE EXCEPTION
          'storyboard tombstone drop refused: public.% has % row(s). '
          'Migration 348 rename-deprecated these tables at 0 rows — '
          'investigate before dropping.', v_tbl, v_cnt;
      END IF;
    END IF;
  END LOOP;
  RAISE NOTICE '[migration 366] storyboard tombstone guard OK — all present tables empty.';
END $$;

-- Drop the bridge table's FKs into the tombstones (columns/data stay).
ALTER TABLE public.script_storyboard_links
  DROP CONSTRAINT IF EXISTS script_storyboard_links_storyboard_node_id_fkey;
ALTER TABLE public.script_storyboard_links
  DROP CONSTRAINT IF EXISTS script_storyboard_links_storyboard_project_id_fkey;

-- Drop the eight tombstones. Children first for legibility; CASCADE + IF EXISTS
-- make the order and any residual dependency irrelevant (and keep re-runs safe).
DROP TABLE IF EXISTS public.zzz_deprecated_storyboard_frame_characters CASCADE;
DROP TABLE IF EXISTS public.zzz_deprecated_storyboard_video_assets CASCADE;
DROP TABLE IF EXISTS public.zzz_deprecated_storyboard_frames CASCADE;
DROP TABLE IF EXISTS public.zzz_deprecated_storyboard_edges CASCADE;
DROP TABLE IF EXISTS public.zzz_deprecated_storyboard_nodes CASCADE;
DROP TABLE IF EXISTS public.zzz_deprecated_storyboard_characters CASCADE;
DROP TABLE IF EXISTS public.zzz_deprecated_storyboard_assets CASCADE;
DROP TABLE IF EXISTS public.zzz_deprecated_storyboard_projects CASCADE;

-- PostgREST caches the schema; without this the dropped tables linger in its
-- cache until a restart (the CI-migration-skips-PostgREST-reload trap).
NOTIFY pgrst, 'reload schema';
