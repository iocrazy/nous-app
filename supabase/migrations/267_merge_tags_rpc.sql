-- Atomic tag merge: re-point every resource_tags row from the source tags onto
-- the target (deduping against the PK), then delete the source tags.
-- SECURITY DEFINER + in-function ownership checks: the backend calls with the
-- service key, so RLS is bypassed; ownership is enforced here via p_user.
-- Accepts text ids (Python passes snowflake ids as strings) and casts to bigint.

CREATE OR REPLACE FUNCTION merge_tags(
  p_target  text,
  p_sources text[],
  p_user    uuid
) RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_target  bigint := p_target::bigint;
  v_sources bigint[];
  v_count   bigint;
  v_bad     int;
BEGIN
  -- Normalize sources: cast to bigint, drop the target if present, dedupe.
  SELECT array_agg(DISTINCT s::bigint)
    INTO v_sources
    FROM unnest(p_sources) AS s
   WHERE s::bigint <> v_target;

  IF v_sources IS NULL OR array_length(v_sources, 1) IS NULL THEN
    RAISE EXCEPTION 'merge_tags: no source tags to merge';
  END IF;

  -- Target must exist and be the caller's own user tag.
  IF NOT EXISTS (
    SELECT 1 FROM tags
     WHERE id = v_target AND type = 'user' AND user_id = p_user
  ) THEN
    RAISE EXCEPTION 'merge_tags: target % is not an owned user tag', v_target;
  END IF;

  -- Every source must be the caller's own user tag.
  SELECT count(*) INTO v_bad FROM tags
   WHERE id = ANY(v_sources) AND (type <> 'user' OR user_id <> p_user);
  IF v_bad > 0 THEN
    RAISE EXCEPTION 'merge_tags: one or more source tags are not owned user tags';
  END IF;

  -- All sources must exist.
  IF (SELECT count(*) FROM tags WHERE id = ANY(v_sources)) <> array_length(v_sources, 1) THEN
    RAISE EXCEPTION 'merge_tags: one or more source tags do not exist';
  END IF;

  -- Re-point with dedup against PK (resource_id, tag_id).
  INSERT INTO resource_tags (resource_id, tag_id, source, confidence, created_at)
  SELECT resource_id, v_target, source, confidence, created_at
    FROM resource_tags
   WHERE tag_id = ANY(v_sources)
  ON CONFLICT (resource_id, tag_id) DO NOTHING;

  -- Delete the source tags (FK ON DELETE CASCADE clears their resource_tags).
  DELETE FROM tags WHERE id = ANY(v_sources);

  SELECT count(*) INTO v_count FROM resource_tags WHERE tag_id = v_target;
  RETURN v_count;
END;
$$;

REVOKE ALL ON FUNCTION merge_tags(text, text[], uuid) FROM public;
GRANT EXECUTE ON FUNCTION merge_tags(text, text[], uuid) TO service_role, authenticated;
