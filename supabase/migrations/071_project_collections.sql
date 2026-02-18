-- 071_project_collections.sql
-- Collection links for external file uploads to projects

CREATE TABLE IF NOT EXISTS project_collections (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  collection_code VARCHAR(20) UNIQUE NOT NULL,
  collection_name VARCHAR(200) NOT NULL,
  allowed_types   TEXT[],
  max_file_size_mb INT DEFAULT 500,
  deadline        TIMESTAMPTZ,
  is_active       BOOLEAN DEFAULT true,
  created_by      UUID REFERENCES auth.users(id),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_project_collections_project
  ON project_collections (project_id);

CREATE INDEX IF NOT EXISTS idx_project_collections_code
  ON project_collections (collection_code);

-- RLS
ALTER TABLE project_collections ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can read project collections" ON project_collections;
CREATE POLICY "Users can read project collections"
  ON project_collections FOR SELECT
  USING (
    created_by = auth.uid()
    OR is_active = true
  );

DROP POLICY IF EXISTS "Users can create collections" ON project_collections;
CREATE POLICY "Users can create collections"
  ON project_collections FOR INSERT
  WITH CHECK (created_by = auth.uid());

DROP POLICY IF EXISTS "Users can update own collections" ON project_collections;
CREATE POLICY "Users can update own collections"
  ON project_collections FOR UPDATE
  USING (created_by = auth.uid())
  WITH CHECK (created_by = auth.uid());

DROP POLICY IF EXISTS "Users can delete own collections" ON project_collections;
CREATE POLICY "Users can delete own collections"
  ON project_collections FOR DELETE
  USING (created_by = auth.uid());

DROP POLICY IF EXISTS "Service role full access on project_collections" ON project_collections;
CREATE POLICY "Service role full access on project_collections"
  ON project_collections FOR ALL
  USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');
