-- Phase M (M3.D): per-agent memory injection budget.
--
-- Today retriever uses fixed DEFAULT_TOP_N_FINAL=5 for every agent.
-- Some agents (storyboard with very long context, summarize agent
-- that needs minimal grounding) want different budgets. This adds a
-- nullable per-agent override; NULL = use code default.

ALTER TABLE ai_agents
  ADD COLUMN IF NOT EXISTS memory_injection_top_n INT;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'ai_agents_memory_injection_top_n_check'
  ) THEN
    ALTER TABLE ai_agents
      ADD CONSTRAINT ai_agents_memory_injection_top_n_check
      CHECK (memory_injection_top_n IS NULL
             OR (memory_injection_top_n >= 0 AND memory_injection_top_n <= 50));
  END IF;
END$$;

COMMENT ON COLUMN ai_agents.memory_injection_top_n IS
  'Phase M M3.D: per-agent override for retriever top_n. NULL = use code default (5).';
