-- 476: match_videos_by_embedding — same resource visibility as the text RPC.
--
-- 463 scoped the vector RPC to the owner but not to the same eligibility the
-- text RPC (rpc_user_media_text_search) applies: is_trashed = false and
-- source_type = 'web'. Hybrid search now merges vector hits under text hits,
-- so a trashed download with a vector would have resurfaced in "My Library"
-- results. Same signature, CREATE OR REPLACE: the ACL 463 set (REVOKEd from
-- PUBLIC / anon / authenticated) is preserved — verified below rather than
-- assumed. Deploy-order safe in both directions.

CREATE OR REPLACE FUNCTION public.match_videos_by_embedding(
  query_embedding vector,
  match_threshold double precision DEFAULT 0.7,
  match_count integer DEFAULT 10,
  p_user_id uuid DEFAULT NULL
)
RETURNS TABLE(
  media_id bigint,
  platform_id text,
  title text,
  description text,
  cover_urls jsonb,
  author text,
  view_count bigint,
  created_at timestamp with time zone,
  similarity double precision
)
LANGUAGE plpgsql
SET search_path TO 'public', 'pg_catalog'
AS $function$
BEGIN
    RETURN QUERY
    -- Two layers on purpose. DISTINCT ON forces its own ORDER BY to start
    -- with pm.id, so applying LIMIT at that level would return the LOWEST
    -- ids that pass the threshold, not the closest. The inner query keeps
    -- one row per media (its best distance); the outer ranks by distance.
    SELECT
        d.media_id,
        d.platform_id,
        d.title,
        d.description,
        d.cover_urls,
        d.author,
        d.view_count,
        d.created_at,
        (1 - d.distance)::float AS similarity
    FROM (
        SELECT DISTINCT ON (pm.id)
            pm.id                        AS media_id,
            pm.platform_id::text         AS platform_id,
            pm.title,
            pm.description,
            pm.cover_urls,
            pm.author::text              AS author,
            COALESCE(pm.view_count, 0)::bigint AS view_count,
            pm.created_at,
            (ra.content_embedding <=> query_embedding) AS distance
        FROM resource_analysis ra
        JOIN resources r     ON r.id = ra.resource_id
        JOIN parsed_media pm ON pm.id = r.media_id
        WHERE ra.content_embedding IS NOT NULL
          AND r.media_id IS NOT NULL
          AND r.is_trashed = false
          AND r.source_type = 'web'
          AND (p_user_id IS NULL OR r.creator_id = p_user_id)
          AND (1 - (ra.content_embedding <=> query_embedding)) > match_threshold
        ORDER BY pm.id, ra.content_embedding <=> query_embedding
    ) d
    ORDER BY d.distance
    LIMIT match_count;
END;
$function$;

-- Belt to 463's brace: the ACL is preserved by CREATE OR REPLACE, but say so
-- explicitly so a future rewrite that recreates the function cannot reopen it.
REVOKE EXECUTE ON FUNCTION public.match_videos_by_embedding(
  vector, double precision, integer, uuid
) FROM PUBLIC, anon, authenticated;
