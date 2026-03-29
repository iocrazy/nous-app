-- 110_project_architecture.sql
-- Project Architecture Redesign — P1

-- 1. Add display_code and modules to projects
ALTER TABLE projects
  ADD COLUMN IF NOT EXISTS display_code text,
  ADD COLUMN IF NOT EXISTS modules_enabled text[]
    DEFAULT '{files,scripts,storyboard,output}';

-- 2. Link storyboard_projects to projects
ALTER TABLE storyboard_projects
  ADD COLUMN IF NOT EXISTS project_id bigint REFERENCES projects(id),
  ADD COLUMN IF NOT EXISTS display_code text;

CREATE INDEX IF NOT EXISTS idx_storyboard_projects_project_id
  ON storyboard_projects(project_id);

-- 3. Display code sequence counters
CREATE TABLE IF NOT EXISTS display_code_counters (
  team_id bigint NOT NULL REFERENCES teams(id),
  year_month text NOT NULL,
  prefix text NOT NULL,
  current_seq int DEFAULT 0,
  PRIMARY KEY (team_id, year_month, prefix)
);

-- 4. Helper function: generate next display code
CREATE OR REPLACE FUNCTION next_display_code(
  p_team_id bigint,
  p_prefix text
) RETURNS text AS $$
DECLARE
  v_ym text;
  v_seq int;
BEGIN
  v_ym := to_char(now(), 'YYYYMM');
  INSERT INTO display_code_counters (team_id, year_month, prefix, current_seq)
  VALUES (p_team_id, v_ym, p_prefix, 1)
  ON CONFLICT (team_id, year_month, prefix)
  DO UPDATE SET current_seq = display_code_counters.current_seq + 1
  RETURNING current_seq INTO v_seq;
  RETURN p_prefix || '-' || v_ym || lpad(v_seq::text, 3, '0');
END;
$$ LANGUAGE plpgsql;
