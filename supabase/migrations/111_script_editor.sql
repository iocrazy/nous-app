-- 111_script_editor.sql
-- Script Editor Foundation — P2

-- 1. Script projects (1:N with projects)
CREATE TABLE IF NOT EXISTS script_projects (
  id bigint PRIMARY KEY DEFAULT generate_snowflake_id(),
  project_id bigint NOT NULL REFERENCES projects(id),
  team_id bigint NOT NULL REFERENCES teams(id),
  display_code text,
  name varchar(200) NOT NULL,
  description text,
  settings_json jsonb DEFAULT '{}',
  viewport_json jsonb,
  status text DEFAULT 'active',
  created_by uuid NOT NULL REFERENCES auth.users(id),
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_script_projects_project_id
  ON script_projects(project_id);
CREATE INDEX IF NOT EXISTS idx_script_projects_team_id
  ON script_projects(team_id);

-- 2. Script chapters (tree structure via parent_chapter_id)
CREATE TABLE IF NOT EXISTS script_chapters (
  id bigint PRIMARY KEY DEFAULT generate_snowflake_id(),
  script_id bigint NOT NULL REFERENCES script_projects(id) ON DELETE CASCADE,
  parent_chapter_id bigint REFERENCES script_chapters(id),
  chapter_number int,
  title text,
  summary text,
  content text,
  branch_label text,
  branch_type text,
  position_x float DEFAULT 0,
  position_y float DEFAULT 0,
  width float,
  height float,
  data_json jsonb DEFAULT '{}',
  sort_order int DEFAULT 0,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_script_chapters_script_id
  ON script_chapters(script_id);
CREATE INDEX IF NOT EXISTS idx_script_chapters_parent
  ON script_chapters(parent_chapter_id);

-- 3. Trigger: auto-update updated_at on script_projects
CREATE OR REPLACE FUNCTION update_script_projects_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_script_projects_updated_at
  BEFORE UPDATE ON script_projects
  FOR EACH ROW EXECUTE FUNCTION update_script_projects_updated_at();

-- 4. Trigger: auto-update updated_at on script_chapters
CREATE OR REPLACE FUNCTION update_script_chapters_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_script_chapters_updated_at
  BEFORE UPDATE ON script_chapters
  FOR EACH ROW EXECUTE FUNCTION update_script_chapters_updated_at();
