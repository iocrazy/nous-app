-- Video-Tag many-to-many association
CREATE TABLE video_tags (
    video_id BIGINT NOT NULL REFERENCES douyin_videos(id) ON DELETE CASCADE,
    tag_id UUID NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    confidence FLOAT CHECK (confidence >= 0 AND confidence <= 1),
    source VARCHAR(20) CHECK (source IN ('auto', 'manual', 'ai')) DEFAULT 'manual',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),

    PRIMARY KEY (video_id, tag_id)
);

-- Indexes for efficient queries
CREATE INDEX idx_video_tags_video ON video_tags(video_id);
CREATE INDEX idx_video_tags_tag ON video_tags(tag_id);
CREATE INDEX idx_video_tags_source ON video_tags(source);

-- RLS Policies
ALTER TABLE video_tags ENABLE ROW LEVEL SECURITY;

-- Users can view tags on videos they own
CREATE POLICY "View tags on owned videos" ON video_tags
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM douyin_videos
            WHERE douyin_videos.id = video_tags.video_id
            AND douyin_videos.user_id = auth.uid()
        )
    );

-- Users can add tags to videos they own
CREATE POLICY "Add tags to owned videos" ON video_tags
    FOR INSERT WITH CHECK (
        EXISTS (
            SELECT 1 FROM douyin_videos
            WHERE douyin_videos.id = video_tags.video_id
            AND douyin_videos.user_id = auth.uid()
        )
    );

-- Users can remove tags from videos they own
CREATE POLICY "Remove tags from owned videos" ON video_tags
    FOR DELETE USING (
        EXISTS (
            SELECT 1 FROM douyin_videos
            WHERE douyin_videos.id = video_tags.video_id
            AND douyin_videos.user_id = auth.uid()
        )
    );
