-- Migration: Create Vector Search Function
-- Description: Function for semantic similarity search using pgvector

-- Function for vector similarity search
CREATE OR REPLACE FUNCTION match_videos_by_embedding(
    query_embedding vector(1536),
    match_threshold float DEFAULT 0.7,
    match_count int DEFAULT 10
)
RETURNS TABLE (
    video_id bigint,
    title text,
    description text,
    cover_url text,
    similarity float
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        va.video_id,
        dv.title,
        dv.desc as description,
        dv.cover_url,
        (1 - (va.content_embedding <=> query_embedding))::float as similarity
    FROM video_analysis va
    JOIN douyin_videos dv ON dv.id = va.video_id
    WHERE va.content_embedding IS NOT NULL
    AND (1 - (va.content_embedding <=> query_embedding)) > match_threshold
    ORDER BY va.content_embedding <=> query_embedding
    LIMIT match_count;
END;
$$;

-- Create index for faster vector similarity search
CREATE INDEX IF NOT EXISTS idx_video_analysis_embedding
ON video_analysis
USING ivfflat (content_embedding vector_cosine_ops)
WITH (lists = 100);

-- Comment on function
COMMENT ON FUNCTION match_videos_by_embedding IS 'Search videos by semantic similarity using embeddings. Returns videos with similarity above threshold, ordered by relevance.';
