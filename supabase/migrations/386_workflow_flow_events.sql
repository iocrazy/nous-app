-- 386_workflow_flow_events.sql
ALTER TABLE workflow_template_nodes
  ADD COLUMN completion_policy TEXT NOT NULL DEFAULT 'owner'
    CHECK (completion_policy IN ('owner','any_editor')),
  ADD COLUMN events JSONB NOT NULL DEFAULT
    '{"notify_on_arrival": true, "notify_on_complete": false, "suggest_agent_run": false}'::jsonb;
ALTER TABLE project_stage_nodes
  ADD COLUMN completion_policy TEXT NOT NULL DEFAULT 'owner'
    CHECK (completion_policy IN ('owner','any_editor')),
  ADD COLUMN events JSONB NOT NULL DEFAULT
    '{"notify_on_arrival": true, "notify_on_complete": false, "suggest_agent_run": false}'::jsonb;
NOTIFY pgrst, 'reload schema';
