-- 277_cost_snapshot_columns.sql
-- ============================================================================
-- Phase 0.5-C of Canvas + AI Infrastructure 17-week upgrade plan.
-- See docs/plans/canvas-ai-upgrade-plan.md v1.2.
--
-- Adds cost-tracking columns to existing tables (agent_run_events, ai_messages,
-- agent_runs). The provider_pricing schema lands in a separate migration
-- (PR #586) — no hard FK is added; the cost_snapshot JSONB payload references
-- provider_pricing.id / fx_rates.id / provider_credits.id logically via the
-- fields documented below, so this migration can land independently.
--
-- Migration shape:
--   1. agent_run_events: cost_snapshot JSONB + byok_key_id BIGINT
--                       + parent_run_id BIGINT (M2 prep, NULL in M1.A).
--   2. ai_messages:      tasklet_calls JSONB (array of tasklet invocation
--                       summaries on this message turn).
--   3. agent_runs:       outcome TEXT (pending/user_accepted/user_rejected/
--                       timeout; user-visible value, drives Cost-per-Outcome
--                       admin dashboard view).
--
-- All adds are idempotent (IF NOT EXISTS). No backfill — old rows stay
-- NULL; new writes populate.
--
-- The cost_snapshot JSONB payload format (documented contract — enforced
-- in app/services/ai/cost/snapshot.py, NOT by DB constraints):
--
-- {
--   "provider_slug": "anthropic",
--   "model_slug":    "claude-sonnet-4-6",
--   "modality":      "text_chat",
--   "region":         null,
--   "tokens": {
--     "input":        2400,
--     "output":        350,
--     "cache_read":   1800,
--     "cache_write":   600,
--     "reasoning":      0,
--     "tool_use":      80,
--     "vision":         0
--   },
--   "image_units":     null,
--   "video_seconds":   null,
--   "rates": {
--     "rate_id":       142,        -- FK provider_pricing.id (logical)
--     "contract_id":   "anthropic_2026_q2",
--     "list_input_usd": 3.00,
--     "effective_input_usd": 2.40,
--     "enterprise_discount_pct": 20.0
--   },
--   "discounts_applied": {
--     "enterprise":   20.0,
--     "volume_tier":   5.0,
--     "batch":         null,
--     "credit_id":     null
--   },
--   "cost": {
--     "raw_usd":             0.0102,
--     "discounted_usd":      0.0085,
--     "saved_by_cache_usd":  0.0042,
--     "local_cents":         0.62,
--     "local_currency":      "CNY",
--     "fx_rate_used":        7.30,
--     "fx_rate_id":          88     -- FK fx_rates.id (logical)
--   },
--   "billing": {
--     "is_byok":                  false,
--     "byok_key_id":               null,
--     "call_status":              "success",   -- success|failed|timeout
--     "fail_billing_policy_applied": false,
--     "credit_consumed_id":        null,
--     "credit_consumed_usd":       0
--   },
--   "metadata": {
--     "computed_at":          "2026-06-10T14:23:00Z",
--     "stream_early_stop":    false,
--     "actual_output_tokens": 350,
--     "max_output_tokens":   1024,
--     "otel_semconv_version": "1.27"  -- aligns with OTel GenAI semconv
--   }
-- }
--
-- Field names align with OpenTelemetry GenAI Semantic Conventions where
-- applicable (gen_ai.usage.input_tokens etc) so future Langfuse / OTel
-- collector integration is drop-in.
-- ============================================================================


-- ========================================================================
-- 1. agent_run_events: cost_snapshot + byok_key_id + parent_run_id
-- ========================================================================

ALTER TABLE agent_run_events
  ADD COLUMN IF NOT EXISTS cost_snapshot JSONB,
  ADD COLUMN IF NOT EXISTS byok_key_id BIGINT,
  ADD COLUMN IF NOT EXISTS parent_run_id BIGINT;

COMMENT ON COLUMN agent_run_events.cost_snapshot IS
  'Full per-call cost breakdown. Schema in 166 migration header.
   Written by adapter wrapper after each LLM call. NULL for legacy rows.';
COMMENT ON COLUMN agent_run_events.byok_key_id IS
  'FK provider_byok_keys.id when caller used their own provider key.
   NULL = platform-billed call.';
COMMENT ON COLUMN agent_run_events.parent_run_id IS
  'Snowflake ID of the parent agent_run when this iteration was triggered
   via Delegate / Tasklet from another run. NULL = top-level call.
   M2 will populate; M1.A leaves null.';

CREATE INDEX IF NOT EXISTS idx_agent_run_events_byok
  ON agent_run_events(byok_key_id)
  WHERE byok_key_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_agent_run_events_parent
  ON agent_run_events(parent_run_id)
  WHERE parent_run_id IS NOT NULL;

-- GIN index lets dashboard queries hit cost_snapshot.cost.discounted_usd etc.
CREATE INDEX IF NOT EXISTS idx_agent_run_events_cost_gin
  ON agent_run_events USING gin (cost_snapshot jsonb_path_ops)
  WHERE cost_snapshot IS NOT NULL;


-- ========================================================================
-- 2. ai_messages: tasklet_calls JSONB array
-- ========================================================================

ALTER TABLE ai_messages
  ADD COLUMN IF NOT EXISTS tasklet_calls JSONB NOT NULL DEFAULT '[]'::jsonb;

COMMENT ON COLUMN ai_messages.tasklet_calls IS
  'Array of tasklet invocations triggered by this message turn.
   Each item: {slug, tokens_in, tokens_out, cost_usd, latency_ms, ok}.
   Powers "By Tasklet" admin dashboard view.';

-- ========================================================================
-- 3. agent_runs: outcome TEXT
-- ========================================================================

ALTER TABLE agent_runs
  ADD COLUMN IF NOT EXISTS outcome TEXT;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'agent_runs_outcome_check'
  ) THEN
    ALTER TABLE agent_runs
      ADD CONSTRAINT agent_runs_outcome_check
      CHECK (outcome IS NULL OR outcome IN
        ('pending', 'user_accepted', 'user_rejected', 'timeout'));
  END IF;
END$$;

COMMENT ON COLUMN agent_runs.outcome IS
  'User-visible verdict on this agent run. NULL = legacy / never marked.
   Front-end writes user_accepted / user_rejected. timeout written by
   sweeper. Drives Cost-per-Outcome admin dashboard view.';

CREATE INDEX IF NOT EXISTS idx_agent_runs_outcome
  ON agent_runs(outcome)
  WHERE outcome IS NOT NULL;


-- ========================================================================
-- RLS: no policy changes — these columns inherit existing table RLS.
-- agent_run_events: service_role full + own-events readable (mig 155).
-- ai_messages:     existing policy unchanged.
-- agent_runs:      existing policy unchanged.
-- ========================================================================


-- ========================================================================
-- Verification (manual after apply)
-- ========================================================================
-- 1. \d agent_run_events  → cost_snapshot / byok_key_id / parent_run_id present
-- 2. \d ai_messages       → tasklet_calls present, default []
-- 3. \d agent_runs        → outcome present, CHECK constraint active
-- 4. UPDATE agent_runs SET outcome = 'nonsense' WHERE id = $any  → CHECK rejects
