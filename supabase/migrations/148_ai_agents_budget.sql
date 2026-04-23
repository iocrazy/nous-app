-- Per-agent monthly budget columns. Sweeper recomputes monthly usage vs.
-- the budget and flips paused_reason='budget' when exceeded. Runner's
-- pre-flight check rejects runs when paused_reason IS NOT NULL.
--
-- Nullable budgets = unlimited (no guard). paused_reason also carries
-- 'manual' for admin-initiated pauses (UI in PR C5).
ALTER TABLE ai_agents
  ADD COLUMN IF NOT EXISTS monthly_token_budget INT,
  ADD COLUMN IF NOT EXISTS monthly_cost_cents_budget NUMERIC(12, 6),
  ADD COLUMN IF NOT EXISTS paused_reason TEXT;

-- Sanity CHECK: paused_reason only takes known values.
ALTER TABLE ai_agents
  ADD CONSTRAINT ai_agents_paused_reason_check
  CHECK (paused_reason IS NULL OR paused_reason IN ('budget', 'manual'));
