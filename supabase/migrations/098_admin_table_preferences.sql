-- 098_admin_table_preferences.sql
-- Server-side persistence for admin table preferences (filters, sorts, column visibility)

CREATE TABLE IF NOT EXISTS admin_table_preferences (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  table_key TEXT NOT NULL,
  filters JSONB DEFAULT '[]'::jsonb,
  sorts JSONB DEFAULT '[]'::jsonb,
  visible_columns TEXT[],
  column_order TEXT[],
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(user_id, table_key)
);

ALTER TABLE admin_table_preferences ENABLE ROW LEVEL SECURITY;

CREATE POLICY "admin_table_preferences_select"
  ON admin_table_preferences FOR SELECT
  USING (auth.uid() = user_id);

CREATE POLICY "admin_table_preferences_insert"
  ON admin_table_preferences FOR INSERT
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY "admin_table_preferences_update"
  ON admin_table_preferences FOR UPDATE
  USING (auth.uid() = user_id);

CREATE POLICY "admin_table_preferences_delete"
  ON admin_table_preferences FOR DELETE
  USING (auth.uid() = user_id);

CREATE INDEX idx_admin_table_prefs_user_table
  ON admin_table_preferences(user_id, table_key);
