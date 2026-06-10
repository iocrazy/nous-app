-- 280_canvas_core_schema.sql
-- ============================================================================
-- Phase 1 Day 1 of Canvas + AI Infrastructure 17-week upgrade plan.
-- See docs/plans/canvas-ai-upgrade-plan.md v1.2 Phase 1.
--
-- Lays the DB skeleton for the canvas core (smart + classic dual-mode):
--   1. canvases — one row per canvas, owned by a project
--   2. projects.current_canvas_id — pointer to the active canvas of a project
--   3. user_settings.canvas_mode_preference — per-user default mode override
--
-- Design notes (locked in plan v1.2 decisions):
--   - kind ENUM in CHECK form: 'smart' (Infinite-Canvas style) or 'classic'
--     (legacy block-editor). A canvas locks its mode; users can have a
--     preference that biases the new-canvas default.
--
--   - nodes_json + connections_json hold the DERIVED state — that's what
--     the frontend renders. Persisted alongside the ops arrays so a cold
--     fetch doesn't have to replay every op to render.
--
--   - node_ops_json + connection_ops_json are the CRDT escape hatch: every
--     mutation is also appended as an op. Today we use optimistic locking
--     (base_updated_at) — keeping ops as append-only arrays means a future
--     switch to Yjs only needs to swap the array type, the schema stays.
--     Plan v1.2 §3 explicitly calls this out as load-bearing.
--
--   - base_updated_at = optimistic-lock token. Frontend sends the value it
--     read; backend rejects writes whose token != current with HTTP 409.
--     Phase 1 Week 3 wires the 409 conflict UI.
--
--   - No FK on projects.current_canvas_id → canvases(id) because the
--     project's primary canvas can be NULL at create time and is mutated
--     a lot; we'd rather not pay the FK check on every project update.
--     Stale pointer is handled in app code (treat as "no current canvas").
--
--   - user_settings.canvas_mode_preference is a TYPED column (not buried
--     in settings_json) — typed columns avoid the JSONB-clobber footgun
--     (#485) and let the picker query without JSONB path parsing.
-- ============================================================================


-- ========================================================================
-- 1. canvases table
-- ========================================================================

CREATE TABLE IF NOT EXISTS canvases (
  id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
  project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  name VARCHAR(200) NOT NULL DEFAULT 'Untitled',
  kind TEXT NOT NULL DEFAULT 'smart',
  viewport_json JSONB NOT NULL DEFAULT '{"x":0,"y":0,"zoom":1}'::jsonb,
  nodes_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  connections_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  node_ops_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  connection_ops_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  base_updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by UUID
);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'canvases_kind_check'
  ) THEN
    ALTER TABLE canvases
      ADD CONSTRAINT canvases_kind_check
      CHECK (kind IN ('smart', 'classic'));
  END IF;
END$$;

CREATE INDEX IF NOT EXISTS idx_canvases_project
  ON canvases(project_id);
CREATE INDEX IF NOT EXISTS idx_canvases_kind
  ON canvases(kind);
CREATE INDEX IF NOT EXISTS idx_canvases_project_updated
  ON canvases(project_id, updated_at DESC);

COMMENT ON TABLE canvases IS
  'Phase 1 Day 1. One canvas = one editing surface inside a project.
   Smart kind = Infinite-Canvas; classic kind = legacy block-editor.
   nodes_json/connections_json hold derived state for fast cold reads;
   node_ops_json/connection_ops_json are append-only ops, kept as the
   CRDT escape hatch (future Yjs swap-in without schema change).';
COMMENT ON COLUMN canvases.base_updated_at IS
  'Optimistic-lock token. PUT requests must echo the value they read;
   mismatch → 409 conflict (handled by Phase 1 Week 3 frontend).';
COMMENT ON COLUMN canvases.node_ops_json IS
  'Append-only ops log. Today derived state is the source of truth at
   render time; this is kept so a future Yjs/CRDT swap-in does not need
   a schema migration. See plan v1.2 §3 CRDT escape hatch.';


-- ========================================================================
-- 2. canvases auto-updated_at trigger (reuses common pattern)
-- ========================================================================

CREATE OR REPLACE FUNCTION canvases_touch_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_canvases_touch_updated_at ON canvases;
CREATE TRIGGER trg_canvases_touch_updated_at
  BEFORE UPDATE ON canvases
  FOR EACH ROW EXECUTE FUNCTION canvases_touch_updated_at();


-- ========================================================================
-- 3. canvases RLS — scope to project membership
-- ========================================================================

ALTER TABLE canvases ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS canvases_member_select ON canvases;
CREATE POLICY canvases_member_select ON canvases FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM project_members pm
      WHERE pm.project_id = canvases.project_id
        AND pm.user_id = (SELECT auth.uid())
    )
  );

DROP POLICY IF EXISTS canvases_member_insert ON canvases;
CREATE POLICY canvases_member_insert ON canvases FOR INSERT
  WITH CHECK (
    EXISTS (
      SELECT 1 FROM project_members pm
      WHERE pm.project_id = canvases.project_id
        AND pm.user_id = (SELECT auth.uid())
    )
  );

DROP POLICY IF EXISTS canvases_member_update ON canvases;
CREATE POLICY canvases_member_update ON canvases FOR UPDATE
  USING (
    EXISTS (
      SELECT 1 FROM project_members pm
      WHERE pm.project_id = canvases.project_id
        AND pm.user_id = (SELECT auth.uid())
    )
  )
  WITH CHECK (
    EXISTS (
      SELECT 1 FROM project_members pm
      WHERE pm.project_id = canvases.project_id
        AND pm.user_id = (SELECT auth.uid())
    )
  );

DROP POLICY IF EXISTS canvases_member_delete ON canvases;
CREATE POLICY canvases_member_delete ON canvases FOR DELETE
  USING (
    EXISTS (
      SELECT 1 FROM project_members pm
      WHERE pm.project_id = canvases.project_id
        AND pm.user_id = (SELECT auth.uid())
    )
  );


-- ========================================================================
-- 4. projects.current_canvas_id
-- ========================================================================

ALTER TABLE projects
  ADD COLUMN IF NOT EXISTS current_canvas_id BIGINT;

COMMENT ON COLUMN projects.current_canvas_id IS
  'Pointer to canvases.id of the project''s active canvas. No FK because
   the column is mutated frequently and a stale pointer (canvas deleted)
   is treated as "no current canvas" in app code, not an error.';


-- ========================================================================
-- 5. user_settings.canvas_mode_preference
-- ========================================================================

ALTER TABLE user_settings
  ADD COLUMN IF NOT EXISTS canvas_mode_preference TEXT;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'user_settings_canvas_mode_preference_check'
  ) THEN
    ALTER TABLE user_settings
      ADD CONSTRAINT user_settings_canvas_mode_preference_check
      CHECK (canvas_mode_preference IS NULL
             OR canvas_mode_preference IN ('smart', 'classic'));
  END IF;
END$$;

COMMENT ON COLUMN user_settings.canvas_mode_preference IS
  'Per-user default mode for new canvases. NULL = follow canvas.kind
   (no user-level override). smart/classic = force that mode when the
   user opens a new canvas.';


-- ========================================================================
-- Verification (manual after apply)
-- ========================================================================
-- 1. \d canvases  → all columns + CHECK + RLS + triggers present
-- 2. INSERT INTO canvases (project_id) VALUES (<some project>);
--    → id auto-generated, defaults populate, base_updated_at = now()
-- 3. UPDATE canvases SET kind='bad' WHERE id=$1;
--    → CHECK rejects (canvases_kind_check)
-- 4. SELECT * FROM canvases WHERE project_id = $not_member;
--    → 0 rows under non-service role (RLS gates)
-- 5. UPDATE projects SET current_canvas_id = $canvas_id;
--    → succeeds; FK is intentionally absent
-- 6. UPDATE user_settings SET canvas_mode_preference = 'smart';
--    → succeeds; 'invalid' → CHECK rejects
