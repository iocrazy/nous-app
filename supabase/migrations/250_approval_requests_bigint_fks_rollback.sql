-- Rollback for migration 250. (CI auto-detect skips *_rollback.sql.)
-- Restores session_id / run_id to UUID. Safe only while the table is empty
-- (bigint values can't be cast back to uuid).
ALTER TABLE public.agent_approval_requests
  ALTER COLUMN session_id TYPE uuid USING NULL::uuid;

ALTER TABLE public.agent_approval_requests
  ALTER COLUMN run_id TYPE uuid USING NULL::uuid;
