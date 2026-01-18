-- Smart Collections with rule-based filtering
CREATE TABLE smart_collections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    icon VARCHAR(50) DEFAULT '📁',
    description TEXT,

    -- Rule definition (JSON)
    rules JSONB NOT NULL DEFAULT '{
        "match": "all",
        "conditions": []
    }'::jsonb,

    -- Cache for performance (video_id is BIGINT)
    cached_video_ids BIGINT[] DEFAULT '{}',
    cached_count INT DEFAULT 0,
    cached_at TIMESTAMP WITH TIME ZONE,

    -- Settings
    is_preset BOOLEAN DEFAULT false,
    sort_by VARCHAR(50) DEFAULT 'created_at',
    sort_order VARCHAR(10) DEFAULT 'desc' CHECK (sort_order IN ('asc', 'desc')),

    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
);

-- Indexes
CREATE INDEX idx_smart_collections_user ON smart_collections(user_id);
CREATE INDEX idx_smart_collections_preset ON smart_collections(is_preset);

-- RLS Policies
ALTER TABLE smart_collections ENABLE ROW LEVEL SECURITY;

CREATE POLICY "View own collections" ON smart_collections
    FOR SELECT USING (user_id = auth.uid());

CREATE POLICY "Create own collections" ON smart_collections
    FOR INSERT WITH CHECK (user_id = auth.uid());

CREATE POLICY "Update own collections" ON smart_collections
    FOR UPDATE USING (user_id = auth.uid());

CREATE POLICY "Delete own non-preset collections" ON smart_collections
    FOR DELETE USING (user_id = auth.uid() AND is_preset = false);

-- Reuse the trigger function from video_analysis
CREATE TRIGGER smart_collections_updated
    BEFORE UPDATE ON smart_collections
    FOR EACH ROW
    EXECUTE FUNCTION update_video_analysis_timestamp();
