-- RPC function: batch count resource_tags by tag IDs
-- Used by tags_repository.get_all_tags for efficient media_count
-- Accepts text[] because Python passes string IDs; casts to bigint internally

CREATE OR REPLACE FUNCTION get_tag_counts_by_ids(p_tag_ids text[])
RETURNS TABLE(tag_id text, count bigint)
LANGUAGE sql STABLE
AS $$
  SELECT rt.tag_id::text AS tag_id, COUNT(*)::bigint AS count
  FROM resource_tags rt
  WHERE rt.tag_id = ANY(
    SELECT unnest(p_tag_ids)::bigint
  )
  GROUP BY rt.tag_id;
$$;
