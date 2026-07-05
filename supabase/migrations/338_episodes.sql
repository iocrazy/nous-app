-- 338_episodes.sql — Phase B P1: episodes 维度（spec v3 §2.1）
CREATE TABLE IF NOT EXISTS episodes (
  id          BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
  project_id  BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  title       VARCHAR(200) NOT NULL DEFAULT 'Ep 1',
  sort_order  INTEGER NOT NULL DEFAULT 0,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_episodes_project ON episodes(project_id, sort_order);
ALTER TABLE episodes ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Service role full access on episodes" ON episodes;
CREATE POLICY "Service role full access on episodes" ON episodes FOR ALL
  USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');

ALTER TABLE script_projects ADD COLUMN IF NOT EXISTS episode_id BIGINT REFERENCES episodes(id) ON DELETE RESTRICT;
CREATE INDEX IF NOT EXISTS idx_script_projects_episode ON script_projects(episode_id);
NOTIFY pgrst, 'reload schema';
