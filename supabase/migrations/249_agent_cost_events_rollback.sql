-- Rollback for migration 249. (CI auto-detect skips *_rollback.sql.)
DROP TABLE IF EXISTS public.agent_cost_events;
