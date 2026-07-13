-- Dual-canvas Phase 1 hotfix: the DB CHECK never learned kind='lite'
-- (#1322 widened the Pydantic enums only), so creating a Smart (lite)
-- canvas failed with 23514 in prod. 'classic' stays in the enum: retired
-- rows soft-deleted by 361 still exist and the rebuilt CHECK validates
-- existing rows.
ALTER TABLE canvases DROP CONSTRAINT IF EXISTS canvases_kind_check;
ALTER TABLE canvases
    ADD CONSTRAINT canvases_kind_check
    CHECK (kind IN ('smart', 'lite', 'classic', 'character', 'location', 'prop'));

NOTIFY pgrst, 'reload schema';
