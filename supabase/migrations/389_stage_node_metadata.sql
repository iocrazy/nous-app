-- 389_stage_node_metadata.sql
ALTER TABLE project_stage_nodes
  ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;
NOTIFY pgrst, 'reload schema';
