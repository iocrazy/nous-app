-- 360_genmedia_entity_index.sql
-- CC5 asset backlink: generations dispatched from an entity branch carry
-- entity_kind/entity_id inside params jsonb (stamped by the canvas at
-- dispatch). The library asset strips filter on entity_id, so give that
-- lookup an expression index. Partial: only rows that carry the key.
-- (359 is claimed by the storage-unification branch — numbering gap is fine.)
CREATE INDEX IF NOT EXISTS idx_genmedia_entity_id
    ON public.generated_media ((params->>'entity_id'), created_at DESC)
    WHERE params->>'entity_id' IS NOT NULL;
