-- Migration 395: Workflow M4 — Autopilot data layer (spec §1)
--
-- Autopilot ("自动开工,人守关口"): auto_start nodes whose deps are satisfied
-- open + dispatch themselves; review gates never auto-advance. This migration
-- only lands the storage — the engine (autopilot_tick) is a later task.
--
--   project_stage_nodes.brief   — runtime "heads up before you start" text an
--     instance owner may write any time before/while a node is open. Same
--     idiom as form_data: instance-only, no template-side counterpart, PATCH-
--     able (NodePatch), NOT copied from a template (there is nothing to copy
--     from — a template node has no brief).
--   projects.autopilot_enabled  — project-level master switch (default ON;
--     with zero auto_start nodes in a project's workflow the tick still has
--     nothing to do, so defaulting true is inert until a template/node opts
--     an individual node in via events.auto_start).
--   events.auto_start           — NOT a column: it rides the existing
--     workflow_template_nodes.events / project_stage_nodes.events JSONB blob,
--     same as every other flow-rule toggle since mig 386 (notify_on_arrival,
--     suggest_agent_run, prepare_agent_run, ...). Adding a JSONB key needs no
--     migration; only the Pydantic schema (WorkflowNodeEvents) changes.
--   system_settings 'workflow_autopilot' — {"daily_auto_runs": 20} per-project
--     daily cap on AUTO dispatches (manual dispatch/start-early never counts).
--     DO NOTHING on conflict — seeding must never clobber an admin-tuned value
--     on migration replay (same idiom as mig 291/299).

ALTER TABLE project_stage_nodes
  ADD COLUMN IF NOT EXISTS brief TEXT NOT NULL DEFAULT '';
COMMENT ON COLUMN project_stage_nodes.brief IS
  'Instance-only runtime heads-up text, writable any time before/while the node is open (M4 Autopilot, spec §1). Not copied from a template; frozen (read-only) once the node reaches done.';

ALTER TABLE projects
  ADD COLUMN IF NOT EXISTS autopilot_enabled BOOLEAN NOT NULL DEFAULT true;
COMMENT ON COLUMN projects.autopilot_enabled IS
  'Project-level Autopilot master switch (M4, spec §1). Default true, but inert with no events.auto_start nodes in the project''s workflow. Autopilot never crosses a review gate regardless of this flag.';

INSERT INTO system_settings (key, value, description)
VALUES (
  'workflow_autopilot',
  '{"daily_auto_runs": 20}'::jsonb,
  'M4 Autopilot per-project daily cap on AUTO dispatches (manual dispatch/start-early not counted); env->DB override, cached ~60s.'
)
ON CONFLICT (key) DO NOTHING;

NOTIFY pgrst, 'reload schema';
