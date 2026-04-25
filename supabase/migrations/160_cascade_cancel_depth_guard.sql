-- TODO-AI-013: cascade_cancel_run trigger fan-out guard.
--
-- Original trigger fires AFTER UPDATE of cancel_requested. Its body
-- UPDATEs every child run with cancel_requested=true, which RE-FIRES
-- the trigger on each child. For a tree of N running children, you
-- get N nested executions of the same WHERE-scan. Worst case quadratic
-- locking + write amplification on agent_runs.
--
-- Fix: early-return when pg_trigger_depth() > 1. The first invocation
-- (depth=1, on the root row) does the WHERE root_run_id=... UPDATE
-- once, hitting all children in a single statement. Each child UPDATE
-- fires the trigger at depth=2 — guard returns immediately, no further
-- cascade. Net: O(1) trigger executions, O(1) UPDATE statement,
-- correct semantics preserved.
CREATE OR REPLACE FUNCTION cascade_cancel_run() RETURNS TRIGGER AS $$
BEGIN
  -- Recursion guard: only the outermost call should fan out.
  IF pg_trigger_depth() > 1 THEN
    RETURN NEW;
  END IF;

  IF NEW.cancel_requested = true AND OLD.cancel_requested = false THEN
    UPDATE agent_runs
       SET cancel_requested = true
     WHERE root_run_id = COALESCE(NEW.root_run_id, NEW.id)
       AND status = 'running'
       AND id != NEW.id;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION cascade_cancel_run() IS
  'Cancels child runs in a delegation tree when the root cancel_requested flips. '
  'Guarded by pg_trigger_depth() to prevent N-deep recursive fan-out (TODO-AI-013).';
