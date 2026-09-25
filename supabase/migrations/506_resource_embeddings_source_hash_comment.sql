-- 506: resource_embeddings.source_hash COMMENT catches up with what the column
-- has meant since PR #2417.
--
-- Why
-- ===
-- 499 wrote the column comment as "Not yet used to trigger re-embeds; see
-- spec §9". That stopped being true one PR later: the value is now
-- "<DOC_VERSION>:<sha1>" (app/services/library/embedding_document.py) and
-- the backfill's pending_for_user() selects rows whose prefix is not the
-- running DOC_VERSION (stale_version) or whose updated_at predates the
-- resource's summary / transcript (stale_source). A reader who trusts the
-- catalog comment concludes a DOC_VERSION bump does nothing — the opposite
-- of what happens (every row becomes a candidate for one paid re-embed).
--
-- Rows written before the versioned format hold a bare sha1; the backfill
-- relabels those without an embedding call when the digest matches
-- ("rehashed"), so this comment also names that legacy shape.
--
-- COMMENT ON is metadata only: no rewrite, no lock beyond the catalog row,
-- idempotent.

COMMENT ON COLUMN public.resource_embeddings.source_hash IS
  '"<doc_version>:<sha1>" of the embedded document (embedding_document.py). '
  'A DOC_VERSION bump marks every row stale (re-embedded by the backfill); '
  'a row whose updated_at predates the resource summary/transcript is stale too. '
  'Legacy rows hold a bare sha1 and are relabelled, not re-embedded, when the digest matches.';
