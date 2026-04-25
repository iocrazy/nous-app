-- 162: Promote `analyze` to a persistent M3 agent.
--
-- M3 dispatches inbox messages through AgentWorkerPool only when the
-- target agent has `persistent=true`. Without this flag the
-- `agent_worker.run_one_task` runner exits with `not_persistent` even
-- though everything else (state machine, telemetry, outbox) is wired
-- and ready.
--
-- A concurrent-execution smoke (D milestone) validated that two
-- persistent agents fire Doubao calls in true parallel: 31s wall-clock
-- for two 25-31s turns vs. ~56s if they had been serialised. That
-- relies on `analyze` being persistent so the pool actually owns it.
--
-- Idempotent: re-runs against an already-promoted row are no-ops.

UPDATE ai_agents
SET persistent = true
WHERE slug IN ('summarize', 'analyze')
  AND (persistent IS DISTINCT FROM true);
