-- 112_script_assets.sql
-- P4: Script assets + script-to-storyboard linkage

-- 1. Script assets (worldview, characters, locations, props, plot points)
CREATE TABLE IF NOT EXISTS script_assets (
  id bigint PRIMARY KEY DEFAULT generate_snowflake_id(),
  script_id bigint NOT NULL REFERENCES script_projects(id) ON DELETE CASCADE,
  asset_type text NOT NULL,
  name text NOT NULL,
  content text,
  data_json jsonb DEFAULT '{}',
  sort_order int DEFAULT 0,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_script_assets_script_id
  ON script_assets(script_id);
CREATE INDEX IF NOT EXISTS idx_script_assets_type
  ON script_assets(script_id, asset_type);

-- 2. Script-to-Storyboard linkage
CREATE TABLE IF NOT EXISTS script_storyboard_links (
  id bigint PRIMARY KEY DEFAULT generate_snowflake_id(),
  chapter_id bigint NOT NULL REFERENCES script_chapters(id) ON DELETE CASCADE,
  storyboard_project_id bigint NOT NULL REFERENCES storyboard_projects(id) ON DELETE CASCADE,
  storyboard_node_id bigint REFERENCES storyboard_nodes(id) ON DELETE SET NULL,
  created_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_script_storyboard_links_chapter
  ON script_storyboard_links(chapter_id);

-- 3. Auto-update triggers
CREATE TRIGGER trg_script_assets_updated_at
  BEFORE UPDATE ON script_assets
  FOR EACH ROW EXECUTE FUNCTION update_script_chapters_updated_at();
