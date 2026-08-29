-- 446_asset_library_generated_canvas_ext.sql
--
-- Asset Library P0 (spec §3.7): generated_media becomes the "Generated" inbox
-- (review_state), can remember which asset a generation was dispatched from
-- (source_asset_id → pre-fills Save-as-Asset), and canvases can be owned by an
-- asset (the entity's optional workshop canvas — decision 13). project_id on
-- canvases stays NOT NULL: it records which project the canvas was opened from.
--
-- NOT DONE HERE: widening a CHECK on generated_media.origin_kind. That column
-- has never had one (mig 307 created it as a bare `TEXT NOT NULL` with the
-- allowed values only in a comment; nothing since has added a constraint, and
-- schema_baseline.sql confirms it). Inventing one now would not be a widening
-- but a new restriction on live data — and it would immediately be wrong:
-- idx_genmedia_node_shot (mig 354) is predicated on origin_kind IN
-- ('shot_generate','shot_video'), neither of which appears in the spec's list.
-- 'storyboard' / 'cover_studio' / 'chat_upload' are therefore already accepted.

-- 1) generated_media -------------------------------------------------------------
ALTER TABLE generated_media
    ADD COLUMN IF NOT EXISTS review_state TEXT NOT NULL DEFAULT 'unreviewed'
        CONSTRAINT generated_media_review_state_check
        CHECK (review_state IN ('unreviewed','saved','in_assets','deleted')),
    ADD COLUMN IF NOT EXISTS source_asset_id BIGINT REFERENCES assets(id) ON DELETE SET NULL;

-- Already-promoted rows are "saved" (P1 backfills in_assets once asset_files exist).
UPDATE generated_media SET review_state = 'saved'
 WHERE promoted_resource_id IS NOT NULL AND review_state = 'unreviewed';

CREATE INDEX IF NOT EXISTS idx_genmedia_scope_state_created
    ON generated_media (scope_id, review_state, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_genmedia_source_asset
    ON generated_media (source_asset_id) WHERE source_asset_id IS NOT NULL;

-- 2) canvases --------------------------------------------------------------------
ALTER TABLE canvases
    ADD COLUMN IF NOT EXISTS asset_id BIGINT REFERENCES assets(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_canvases_asset ON canvases (asset_id) WHERE asset_id IS NOT NULL;

ALTER TABLE canvases DROP CONSTRAINT IF EXISTS canvases_kind_check;
ALTER TABLE canvases
    ADD CONSTRAINT canvases_kind_check
    CHECK (kind IN ('smart', 'lite', 'classic', 'character', 'location', 'prop', 'costume', 'storyboard'));

NOTIFY pgrst, 'reload schema';
