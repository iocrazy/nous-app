-- 298_canvas_graph_run_routing.sql
-- Phase 6d M1: seed canvas_graph_run into the DBOS routing table so
-- start_workflow_routed('canvas_graph_run', ...) dispatches via DBOS.
--
-- Idempotent: ON CONFLICT DO NOTHING — safe to re-run.
INSERT INTO dbos_workflow_routing (task_type, mode)
VALUES ('canvas_graph_run', 'dbos')
ON CONFLICT (task_type) DO NOTHING;
