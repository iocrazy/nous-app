-- Video Access Logs for tracking user behavior
CREATE TABLE video_access_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    video_id BIGINT NOT NULL REFERENCES douyin_videos(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    action VARCHAR(20) NOT NULL
        CHECK (action IN ('view', 'play', 'share', 'download_again', 'export')),
    metadata JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
);

-- Indexes for efficient queries
CREATE INDEX idx_access_logs_video ON video_access_logs(video_id);
CREATE INDEX idx_access_logs_user ON video_access_logs(user_id);
CREATE INDEX idx_access_logs_created ON video_access_logs(created_at DESC);
CREATE INDEX idx_access_logs_action ON video_access_logs(action);

-- RLS Policies
ALTER TABLE video_access_logs ENABLE ROW LEVEL SECURITY;

CREATE POLICY "View own access logs" ON video_access_logs
    FOR SELECT USING (user_id = auth.uid());

CREATE POLICY "Create own access logs" ON video_access_logs
    FOR INSERT WITH CHECK (user_id = auth.uid());
