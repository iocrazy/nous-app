-- 362_storage_migration_routing.sql
-- Storage-unification epic PR-4 (Task 4.2 follow-up): seed
-- storage_migration into the DBOS routing table so
-- start_workflow_routed('storage_migration', ...) dispatches via DBOS
-- explicitly (unknown task_types already default to 'dbos', this row is
-- for consistency/observability with every other registered task_type).
--
-- Idempotent: ON CONFLICT DO NOTHING — safe to re-run.
INSERT INTO dbos_workflow_routing (task_type, mode)
VALUES ('storage_migration', 'dbos')
ON CONFLICT (task_type) DO NOTHING;
