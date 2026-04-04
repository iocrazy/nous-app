-- 113_style_templates.sql
-- P5: Style templates (prompt library)

CREATE TABLE IF NOT EXISTS style_templates (
  id bigint PRIMARY KEY DEFAULT generate_snowflake_id(),
  team_id bigint REFERENCES teams(id),
  name text NOT NULL,
  description text,
  prompt_content text NOT NULL,
  category text,
  is_public boolean DEFAULT false,
  created_by uuid REFERENCES auth.users(id),
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_style_templates_team_id
  ON style_templates(team_id);
CREATE INDEX IF NOT EXISTS idx_style_templates_category
  ON style_templates(category);
