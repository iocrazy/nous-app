-- 286: per-agent run limits (paperclip port P4 — Configuration limits).
--
-- Paperclip's agent config carries adapterConfig.timeoutSec and
-- heartbeat.maxConcurrentRuns. MediaHub equivalents on ai_agents:
--   timeout_sec          — max wall-clock for one run's tool loop. Checked
--                          between LLM iterations by AgentRunner (it bounds
--                          the LOOP; a single hung HTTP call is bounded by
--                          the adapter's own client timeout). NULL = no cap.
--   max_concurrent_runs  — RunRecorder pre-flight rejects a new run when the
--                          agent already has this many status='running' rows
--                          (AgentBusyError, surfaced like a pause). NULL =
--                          unlimited.
-- (paperclip's cheap_model is intentionally NOT ported — nothing in the
-- runner consumes a secondary model yet; a dead config knob is worse than
-- no knob.)

ALTER TABLE public.ai_agents
    ADD COLUMN IF NOT EXISTS timeout_sec integer
        CHECK (timeout_sec IS NULL OR timeout_sec >= 0),
    ADD COLUMN IF NOT EXISTS max_concurrent_runs integer
        CHECK (max_concurrent_runs IS NULL OR max_concurrent_runs >= 1);

NOTIFY pgrst, 'reload schema';
