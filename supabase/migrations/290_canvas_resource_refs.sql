-- 290_canvas_resource_refs.sql
-- Materialized junction: which resources each canvas references.
-- Maintained by CanvasService on save (replace-all per canvas).
-- IC-port P4. See docs/superpowers/specs/2026-06-13-project-assets-design.md

CREATE TABLE IF NOT EXISTS canvas_resource_refs (
  canvas_id   BIGINT NOT NULL REFERENCES canvases(id)  ON DELETE CASCADE,
  resource_id BIGINT NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
  role        TEXT   NOT NULL CHECK (role IN ('reference', 'output')),
  node_id     TEXT   NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (canvas_id, resource_id, node_id)
);

CREATE INDEX IF NOT EXISTS idx_crr_resource ON canvas_resource_refs(resource_id);
CREATE INDEX IF NOT EXISTS idx_crr_canvas   ON canvas_resource_refs(canvas_id);

COMMENT ON TABLE canvas_resource_refs IS
  'IC-port P4. One row per (canvas, resource, node) reference, extracted
   from canvases.nodes_json on save. role=reference (shot node ref image)
   or output (prompt-run rendered artifact). Derived/rebuildable — backfill
   script can reconstruct from nodes_json at any time.';

-- RLS: locked to service_role. All app access goes through app.db.engine
-- (bypasses RLS, same as temp_resource_sweeper's reads on folders) via the
-- gated project-assets endpoints, which JOIN + filter by membership. No
-- PostgREST/anon exposure (avoids the world-readable-table class of bug).
ALTER TABLE canvas_resource_refs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS crr_service_role_all ON canvas_resource_refs;
CREATE POLICY crr_service_role_all ON canvas_resource_refs
  FOR ALL TO service_role USING (true) WITH CHECK (true);
