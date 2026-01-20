-- Migration: Update Vector Search Function to include aweme_id and other fields
-- Description: Add aweme_id, author, view_count, created_at to search results

-- Drop existing function first (required to change return type)
DROP FUNCTION IF EXISTS match_videos_by_embedding(vector(1536), float, int);

-- Recreate function with new fields
CREATE OR REPLACE FUNCTION match_videos_by_embedding(
    query_embedding vector(1536),
    match_threshold float DEFAULT 0.7,
    match_count int DEFAULT 10
)
RETURNS TABLE (
    video_id bigint,
    aweme_id text,
    title text,
    description text,
    cover_url text,
    author text,
    view_count bigint,
    created_at timestamptz,
    similarity float
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        va.video_id,
        dv.aweme_id,
        dv.title,
        dv.desc as description,
        dv.cover_url,
        dv.author,
        COALESCE(dv.view_count, 0)::bigint as view_count,
        dv.created_at,
        (1 - (va.content_embedding <=> query_embedding))::float as similarity
    FROM video_analysis va
    JOIN douyin_videos dv ON dv.id = va.video_id
    WHERE va.content_embedding IS NOT NULL
    AND (1 - (va.content_embedding <=> query_embedding)) > match_threshold
    ORDER BY va.content_embedding <=> query_embedding
    LIMIT match_count;
END;
$$;

-- Comment on function
COMMENT ON FUNCTION match_videos_by_embedding IS 'Search videos by semantic similarity using embeddings. Returns videos with aweme_id, metadata, and similarity above threshold, ordered by relevance.';
