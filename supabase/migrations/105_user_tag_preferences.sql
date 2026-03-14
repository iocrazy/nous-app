-- 105_user_tag_preferences.sql
-- User preferences for Eagle-style tag picker (starred tags, display settings, panel size)

CREATE TABLE IF NOT EXISTS user_tag_preferences (
  user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
  starred_tag_ids TEXT[] DEFAULT '{}',
  picker_settings JSONB DEFAULT '{
    "layout": "list",
    "columnWidth": "medium",
    "showStarred": true,
    "showRecently": true,
    "showRecommended": false,
    "showCount": true
  }'::jsonb,
  panel_size JSONB DEFAULT '{"width": 480, "height": 400}'::jsonb,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (user_id)
);

-- RLS
ALTER TABLE user_tag_preferences ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can manage own tag preferences"
  ON user_tag_preferences FOR ALL
  USING (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);

-- Allow service role full access
CREATE POLICY "Service role full access on user_tag_preferences"
  ON user_tag_preferences FOR ALL
  USING (auth.jwt()->>'role' = 'service_role');
