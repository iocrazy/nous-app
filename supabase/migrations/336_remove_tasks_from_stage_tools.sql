-- 336_remove_tasks_from_stage_tools.sql
-- PR-A3 (Task 9 cleanup): migration 295's seed put a "tasks" slug into
-- project_stages.tools_recommended for the planning/review stages. The Tasks
-- surface itself was removed in Task 8 (065d2bf6) — the frontend's
-- StageToolGrid already skips unknown tool slugs silently, so today "tasks"
-- is a phantom catalog entry with no matching TOOL_CATALOG mapping.
--
-- Idempotent: re-running finds no remaining '"tasks"' element and is a no-op.

UPDATE project_stages
SET tools_recommended = COALESCE(
    (
        SELECT jsonb_agg(elem)
        FROM jsonb_array_elements(tools_recommended) elem
        WHERE elem <> '"tasks"'::jsonb
    ),
    '[]'::jsonb
)
WHERE tools_recommended @> '["tasks"]'::jsonb;

NOTIFY pgrst, 'reload schema';
