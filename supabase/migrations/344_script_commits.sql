-- 344_script_commits.sql — Phase B P4: version management (spec v3 §5-1).
-- A commit is a manual "tag": a per-scene op_seq watermark map plus an ordered
-- snapshot of the script's scene set at commit time. diff replays the op ledger
-- between two commits' watermarks; rollback replays the inverse batch through the
-- existing If-Match protocol (append-only — no ledger rewrite).
CREATE TABLE IF NOT EXISTS script_commits (
  id           BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
  script_id    BIGINT NOT NULL REFERENCES script_projects(id) ON DELETE CASCADE,
  message      VARCHAR(200) NOT NULL,
  watermarks   JSONB NOT NULL,   -- {scene_id: op_seq} — per-scene ledger high-water at commit
  scene_ids    JSONB NOT NULL,   -- ordered [{id, sort_order, heading...}] scene-set snapshot
  created_by   VARCHAR(64) NOT NULL,  -- user uuid
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_script_commits_script
  ON script_commits(script_id, created_at DESC);

ALTER TABLE script_commits ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Service role full access on script_commits" ON script_commits;
CREATE POLICY "Service role full access on script_commits" ON script_commits FOR ALL
  USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
NOTIFY pgrst, 'reload schema';
