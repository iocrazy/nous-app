-- 343_script_shots.sql — Phase B P3: shots 层（spec v3 §2.1 script_shots 列逐字）
-- 注：spec 计划占 341，合并时已被 341_agent_overrides / 342_scale_db_longtail 占用，顺延至 343。
CREATE TABLE IF NOT EXISTS script_shots (
  id               BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
  scene_id         BIGINT NOT NULL REFERENCES script_scenes(id) ON DELETE CASCADE,
  shot_number      INTEGER,
  shot_type        VARCHAR(20),
  camera_angle     VARCHAR(20),
  camera_movement  VARCHAR(20),
  focal_length     VARCHAR(20),
  lighting         TEXT,
  description      TEXT,
  image_url        TEXT,
  thumbnail_url    TEXT,
  video_url        TEXT,
  status           VARCHAR(20) NOT NULL DEFAULT 'empty',
  sort_order       INTEGER NOT NULL DEFAULT 0,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_script_shots_scene ON script_shots(scene_id, sort_order);

ALTER TABLE script_shots ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Service role full access on script_shots" ON script_shots;
CREATE POLICY "Service role full access on script_shots" ON script_shots FOR ALL
  USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
NOTIFY pgrst, 'reload schema';
