-- 339_script_scenes_and_ops.sql — Phase B P1: scene 层 + 操作日志（spec v3 §2.1/§2.2）
CREATE TABLE IF NOT EXISTS script_scenes (
  id               BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
  script_id        BIGINT NOT NULL REFERENCES script_projects(id) ON DELETE CASCADE,
  chapter_id       BIGINT REFERENCES script_chapters(id) ON DELETE SET NULL,
  heading_int_ext  VARCHAR(10),
  location_text    TEXT,
  location_id      BIGINT,            -- 实体软引用，无 FK（spec §2.4）
  time_of_day      VARCHAR(20),
  content_json     JSONB NOT NULL DEFAULT '[]'::jsonb,
  content          TEXT NOT NULL DEFAULT '',
  content_version  INTEGER NOT NULL DEFAULT 0,
  position_x       DOUBLE PRECISION, position_y DOUBLE PRECISION,
  width            DOUBLE PRECISION, height DOUBLE PRECISION,
  sort_order       INTEGER NOT NULL DEFAULT 0,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_script_scenes_script ON script_scenes(script_id, chapter_id, sort_order);

CREATE TABLE IF NOT EXISTS script_ops (
  id         BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
  scene_id   BIGINT NOT NULL REFERENCES script_scenes(id) ON DELETE CASCADE,
  op_seq     INTEGER NOT NULL,          -- = 应用后的 content_version
  op_json    JSONB NOT NULL,            -- {"ops":[...], "inverse":[...]}
  actor      VARCHAR(64) NOT NULL,      -- user uuid 或 'copilot'
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_script_ops_scene ON script_ops(scene_id, op_seq);

ALTER TABLE script_scenes ENABLE ROW LEVEL SECURITY;
ALTER TABLE script_ops ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Service role full access on script_scenes" ON script_scenes;
CREATE POLICY "Service role full access on script_scenes" ON script_scenes FOR ALL
  USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
DROP POLICY IF EXISTS "Service role full access on script_ops" ON script_ops;
CREATE POLICY "Service role full access on script_ops" ON script_ops FOR ALL
  USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
NOTIFY pgrst, 'reload schema';
