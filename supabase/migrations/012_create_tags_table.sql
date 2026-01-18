-- Tags table for system and user-defined tags
CREATE TABLE tags (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(50) NOT NULL,
    type VARCHAR(20) NOT NULL CHECK (type IN ('system', 'user', 'time')),
    color VARCHAR(20) DEFAULT '#6366f1',
    icon VARCHAR(50),
    user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),

    CONSTRAINT unique_tag_per_scope UNIQUE(name, type, user_id)
);

-- Indexes
CREATE INDEX idx_tags_type ON tags(type);
CREATE INDEX idx_tags_user ON tags(user_id) WHERE user_id IS NOT NULL;

-- RLS Policies
ALTER TABLE tags ENABLE ROW LEVEL SECURITY;

-- System tags visible to all authenticated users
CREATE POLICY "System tags visible to all" ON tags
    FOR SELECT USING (type = 'system' OR type = 'time');

-- User tags visible only to owner
CREATE POLICY "User tags visible to owner" ON tags
    FOR SELECT USING (user_id = auth.uid());

CREATE POLICY "Users can create own tags" ON tags
    FOR INSERT WITH CHECK (user_id = auth.uid() AND type = 'user');

CREATE POLICY "Users can update own tags" ON tags
    FOR UPDATE USING (user_id = auth.uid() AND type = 'user');

CREATE POLICY "Users can delete own tags" ON tags
    FOR DELETE USING (user_id = auth.uid() AND type = 'user');

-- Insert predefined system tags
INSERT INTO tags (name, type, color, icon) VALUES
    ('Food', 'system', '#ef4444', '🍕'),
    ('Tutorial', 'system', '#3b82f6', '📚'),
    ('Comedy', 'system', '#eab308', '😂'),
    ('Dance', 'system', '#ec4899', '💃'),
    ('Music', 'system', '#8b5cf6', '🎵'),
    ('Beauty', 'system', '#f472b6', '💄'),
    ('Fashion', 'system', '#06b6d4', '👗'),
    ('Gaming', 'system', '#22c55e', '🎮'),
    ('Pets', 'system', '#f97316', '🐱'),
    ('Travel', 'system', '#14b8a6', '✈️'),
    ('Tech', 'system', '#6366f1', '💻'),
    ('Sports', 'system', '#84cc16', '⚽'),
    ('Vlog', 'system', '#a855f7', '📹'),
    ('Other', 'system', '#64748b', '📦');
