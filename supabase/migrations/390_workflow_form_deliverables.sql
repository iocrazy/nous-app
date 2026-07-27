-- Migration 390: Form-based deliverables (M3 PR-I, W2)
--
-- Template-layer form schema (workflow_template_nodes.form_schema) describes
-- the fields a node's deliverable form should render; it is authored/edited
-- on the template and copied verbatim into the instance at instantiation
-- (project_stage_nodes.form_schema), same pattern as events/completion_policy
-- (mig 386). The instance additionally carries form_data — the actual
-- user-entered values for that live node — which is instance-only (no
-- template-side counterpart, same shape as metadata mig 389).
ALTER TABLE workflow_template_nodes
  ADD COLUMN IF NOT EXISTS form_schema JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE project_stage_nodes
  ADD COLUMN IF NOT EXISTS form_schema JSONB NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS form_data JSONB NOT NULL DEFAULT '{}'::jsonb;
NOTIFY pgrst, 'reload schema';
