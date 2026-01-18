-- Video Analysis results with embeddings
CREATE TABLE video_analysis (
    video_id BIGINT PRIMARY KEY REFERENCES douyin_videos(id) ON DELETE CASCADE,

    -- Analysis level tracking
    analysis_level VARCHAR(10) DEFAULT 'none'
        CHECK (analysis_level IN ('none', 'L1', 'L2', 'L3')),

    -- Visual analysis results
    visual_description TEXT,
    detected_objects JSONB DEFAULT '[]'::jsonb,
    detected_scenes JSONB DEFAULT '[]'::jsonb,
    detected_people JSONB DEFAULT '[]'::jsonb,
    detected_text TEXT,

    -- Full text for embedding generation
    full_text_for_embedding TEXT,

    -- Vector embedding (1536 dimensions for OpenAI)
    content_embedding vector(1536),

    -- Metadata
    analysis_model VARCHAR(50),
    analysis_cost DECIMAL(10, 6) DEFAULT 0,
    analyzed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
);

-- Vector similarity search index (IVFFlat for better performance)
CREATE INDEX idx_video_analysis_embedding
    ON video_analysis USING ivfflat (content_embedding vector_cosine_ops)
    WITH (lists = 100);

-- Index for filtering by analysis level
CREATE INDEX idx_video_analysis_level ON video_analysis(analysis_level);

-- RLS Policies
ALTER TABLE video_analysis ENABLE ROW LEVEL SECURITY;

CREATE POLICY "View analysis of owned videos" ON video_analysis
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM douyin_videos
            WHERE douyin_videos.id = video_analysis.video_id
            AND douyin_videos.user_id = auth.uid()
        )
    );

CREATE POLICY "Manage analysis of owned videos" ON video_analysis
    FOR ALL USING (
        EXISTS (
            SELECT 1 FROM douyin_videos
            WHERE douyin_videos.id = video_analysis.video_id
            AND douyin_videos.user_id = auth.uid()
        )
    );

-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_video_analysis_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER video_analysis_updated
    BEFORE UPDATE ON video_analysis
    FOR EACH ROW
    EXECUTE FUNCTION update_video_analysis_timestamp();
