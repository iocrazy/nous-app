-- Migration: Create Search Logs Table
-- Description: Track search queries for analytics and suggestions

-- Search logs table
CREATE TABLE IF NOT EXISTS search_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    query TEXT NOT NULL,
    search_type VARCHAR(20) NOT NULL CHECK (search_type IN ('semantic', 'hybrid', 'similar', 'quick')),
    result_count INT DEFAULT 0,
    top_result_video_id BIGINT,
    top_result_similarity FLOAT,
    filters_used JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
);

-- Indexes for analytics
CREATE INDEX idx_search_logs_user ON search_logs(user_id);
CREATE INDEX idx_search_logs_created ON search_logs(created_at DESC);
CREATE INDEX idx_search_logs_query ON search_logs(query);
CREATE INDEX idx_search_logs_type ON search_logs(search_type);

-- RLS Policies
ALTER TABLE search_logs ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view own search logs" ON search_logs
    FOR SELECT USING (user_id = auth.uid());

CREATE POLICY "Users can create own search logs" ON search_logs
    FOR INSERT WITH CHECK (user_id = auth.uid());

-- Function to get popular search queries
CREATE OR REPLACE FUNCTION get_popular_searches(
    days_back INT DEFAULT 7,
    max_results INT DEFAULT 10
)
RETURNS TABLE (
    query TEXT,
    search_count BIGINT,
    avg_results NUMERIC
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        sl.query,
        COUNT(*) as search_count,
        AVG(sl.result_count)::NUMERIC as avg_results
    FROM search_logs sl
    WHERE sl.created_at > now() - (days_back || ' days')::INTERVAL
    AND sl.query IS NOT NULL
    AND LENGTH(sl.query) > 2
    GROUP BY sl.query
    ORDER BY search_count DESC
    LIMIT max_results;
END;
$$;

-- Function to get search analytics
CREATE OR REPLACE FUNCTION get_search_analytics(
    days_back INT DEFAULT 30
)
RETURNS TABLE (
    date DATE,
    total_searches BIGINT,
    unique_users BIGINT,
    semantic_searches BIGINT,
    hybrid_searches BIGINT,
    avg_result_count NUMERIC
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        DATE(sl.created_at) as date,
        COUNT(*) as total_searches,
        COUNT(DISTINCT sl.user_id) as unique_users,
        COUNT(*) FILTER (WHERE sl.search_type = 'semantic') as semantic_searches,
        COUNT(*) FILTER (WHERE sl.search_type = 'hybrid') as hybrid_searches,
        AVG(sl.result_count)::NUMERIC as avg_result_count
    FROM search_logs sl
    WHERE sl.created_at > now() - (days_back || ' days')::INTERVAL
    GROUP BY DATE(sl.created_at)
    ORDER BY date DESC;
END;
$$;

COMMENT ON TABLE search_logs IS 'Tracks search queries for analytics and suggestions';
COMMENT ON FUNCTION get_popular_searches IS 'Returns most popular search queries in the specified time period';
COMMENT ON FUNCTION get_search_analytics IS 'Returns daily search analytics for dashboard';
