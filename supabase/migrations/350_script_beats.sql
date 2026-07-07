-- 350_script_beats.sql — Beats view (PR-BT1): classic beat sheet.
-- Ordered beat cards (title + summary + optional linked scene ids) hanging off a
-- script. No AI, no auto-derivation (v1). Same shape/RLS template as
-- 343_script_shots.sql. scene_ids is an ordered JSONB array of scene id strings
-- (lightweight; no M:N table — a beat links a handful of scenes, YAGNI).
CREATE TABLE IF NOT EXISTS script_beats (
  id          BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
  script_id   BIGINT NOT NULL REFERENCES script_projects(id) ON DELETE CASCADE,
  title       VARCHAR(200) NOT NULL,
  summary     TEXT,
  scene_ids   JSONB NOT NULL DEFAULT '[]'::jsonb,
  sort_order  INTEGER NOT NULL DEFAULT 0,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_script_beats_script ON script_beats(script_id, sort_order);

ALTER TABLE script_beats ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Service role full access on script_beats" ON script_beats;
CREATE POLICY "Service role full access on script_beats" ON script_beats FOR ALL
  USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
NOTIFY pgrst, 'reload schema';
