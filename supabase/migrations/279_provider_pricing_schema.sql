-- 279_provider_pricing_schema.sql
-- ============================================================================
-- Phase 0.5 of Canvas + AI Infrastructure 17-week upgrade plan.
-- See docs/plans/canvas-ai-upgrade-plan.md v1.2.
--
-- Cost accounting foundation. 7 tables + 1 view that together let the adapter
-- wrapper compute per-call cost with full accuracy:
--
--   1. provider_pricing      historical rate table, multi-token / multi-modal
--   2. provider_contracts    enterprise discounts + tenant_id + volume tiers
--   3. provider_credits      free trials / promotions / SLA refunds / prepay
--   4. fx_rates              multi-currency conversion history
--   5. provider_byok_keys    user-supplied keys (zero-bill identification)
--   6. provider_monthly_spend cached rollup for volume-tier lookup
--   7. cost_audit_log        compliance audit trail for price/contract changes
--   8. provider_effective_rate (VIEW)  enterprise-discount-aware rate lookup
--
-- Design decisions (locked in canvas-ai-upgrade-plan v1.1 / v1.2):
--   - Historical archive on provider_pricing: effective_from / effective_to,
--     old rows immutable so a price change never rewrites past calls.
--   - All rates in USD on table; fx_rates resolves to billing currency at
--     compute time, fx_rate_used persists in cost_snapshot for replay.
--   - provider_contracts.tenant_id BIGINT NULL: NULL = platform-default,
--     non-null = team-specific (Multi-tenant SaaS expansion path).
--   - Schema covers SLA refunds via provider_credits.credit_type, no
--     separate SLA table (rare event, small amount, plan §1 #13).
--   - No 5% soft-overage soft-cap field (overage_rate_multiplier on contracts
--     handles the common case; soft-cap can be added in JSONB later).
--
-- RLS:
--   - All 7 tables: service_role full (adapter / admin write at runtime).
--   - No user-level read policy: this is admin-managed financial data.
--   - cost_snapshot field on agent_run_events (separate migration) is the
--     user-visible surface.
--
-- ID strategy:
--   - BIGINT snowflake for pricing/contracts/credits/byok_keys/audit_log
--     (admin-managed entities, BIGINT for consistency with rest of mediahub).
--   - Compound PK for provider_monthly_spend (rollup cache).
--   - fx_rates uses BIGINT snowflake (history table).
-- ============================================================================


-- ========================================================================
-- 1. provider_pricing — historical rates per (provider, model, modality, region, contract)
-- ========================================================================

CREATE TABLE IF NOT EXISTS provider_pricing (
  id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),

  -- Identity
  provider_slug TEXT NOT NULL,         -- 'anthropic' / 'openai' / 'qwen' / 'jimeng' / 'nous_center'
  model_slug TEXT NOT NULL,            -- 'claude-sonnet-4-6' / 'gpt-5' / 'qwen-plus'
  region TEXT,                         -- NULL = global; 'cn-shanghai' / 'us-east' etc
  modality TEXT NOT NULL,              -- See CHECK constraint below

  -- Text-mode token rates (USD per 1M tokens)
  list_input_per_1m_usd       DECIMAL(12, 6),
  list_output_per_1m_usd      DECIMAL(12, 6),
  list_cache_read_per_1m_usd  DECIMAL(12, 6),  -- typically list_input * 0.1
  list_cache_write_per_1m_usd DECIMAL(12, 6),  -- typically list_input * 1.25
  list_reasoning_per_1m_usd   DECIMAL(12, 6),  -- o1/o3/Claude extended thinking
  list_tool_use_per_1m_usd    DECIMAL(12, 6),  -- function-call overhead
  list_vision_per_1m_usd      DECIMAL(12, 6),  -- image-input tokens
  list_audio_per_1m_usd       DECIMAL(12, 6),  -- audio-input tokens

  -- Media-mode per-unit rates (USD)
  list_image_per_unit_usd       DECIMAL(12, 6),  -- single image
  list_image_per_megapixel_usd  DECIMAL(12, 6),  -- by resolution
  list_video_per_second_usd     DECIMAL(12, 6),
  list_audio_per_second_usd     DECIMAL(12, 6),
  list_audio_per_char_usd       DECIMAL(12, 6),  -- TTS per character

  -- Discounts & policies
  batch_discount_pct          DECIMAL(5, 2) NOT NULL DEFAULT 0,  -- async batch discount
  cache_ttl_seconds           INT NOT NULL DEFAULT 300,           -- Anthropic default
  fail_billing_policy         TEXT NOT NULL DEFAULT 'no_charge',

  -- Contract binding (FK added at end of file after contracts table exists)
  contract_id TEXT,

  -- Historical archive (immutable once effective_to set)
  effective_from TIMESTAMPTZ NOT NULL DEFAULT now(),
  effective_to   TIMESTAMPTZ,

  -- Free-form extension hook (promotions, regional adjustments, etc)
  extra JSONB NOT NULL DEFAULT '{}'::jsonb,

  -- Provenance
  source         TEXT,                  -- 'anthropic_pricing_page_2026_06_01'
  source_url     TEXT,
  source_pdf_url TEXT,
  added_by       UUID,
  added_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

  UNIQUE (provider_slug, model_slug, region, modality, contract_id, effective_from)
);

DO $$
BEGIN
  -- modality enum check
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'provider_pricing_modality_check'
  ) THEN
    ALTER TABLE provider_pricing
      ADD CONSTRAINT provider_pricing_modality_check
      CHECK (modality IN (
        'text_chat', 'text_embedding',
        'image_generation', 'image_editing', 'image_input',
        'video_generation', 'video_input',
        'audio_tts', 'audio_stt', 'audio_input',
        'reasoning'
      ));
  END IF;

  -- fail_billing_policy enum check
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'provider_pricing_fail_policy_check'
  ) THEN
    ALTER TABLE provider_pricing
      ADD CONSTRAINT provider_pricing_fail_policy_check
      CHECK (fail_billing_policy IN ('no_charge', 'partial', 'full'));
  END IF;
END$$;

CREATE INDEX IF NOT EXISTS idx_provider_pricing_lookup
  ON provider_pricing (provider_slug, model_slug, modality, effective_from DESC)
  WHERE effective_to IS NULL;

CREATE INDEX IF NOT EXISTS idx_provider_pricing_contract
  ON provider_pricing (contract_id, effective_from DESC)
  WHERE contract_id IS NOT NULL;

COMMENT ON TABLE provider_pricing IS
  'Historical rate table per (provider, model, modality, region, contract).
   effective_to NULL = currently in effect. Old rows kept immutably for audit.';
COMMENT ON COLUMN provider_pricing.fail_billing_policy IS
  'no_charge / partial / full — vendor policy on failed calls.';
COMMENT ON COLUMN provider_pricing.batch_discount_pct IS
  'Async-batch discount %. 0 = no batch tier available.';


-- ========================================================================
-- 2. provider_contracts — enterprise discounts + tenant_id + volume tiers
-- ========================================================================

CREATE TABLE IF NOT EXISTS provider_contracts (
  contract_id TEXT PRIMARY KEY,         -- 'anthropic_2026_q2' / 'qwen_enterprise_v3'
  provider_slug TEXT NOT NULL,

  -- Multi-tenant: NULL = platform default; non-null = team-specific (BYOK-enterprise)
  tenant_id BIGINT,

  -- Discount layers
  enterprise_discount_pct DECIMAL(5, 2) NOT NULL DEFAULT 0,
  volume_tier_json JSONB,
  -- Example:
  -- [
  --   {"threshold_monthly_usd": 1000, "discount_pct": 5},
  --   {"threshold_monthly_usd": 10000, "discount_pct": 15}
  -- ]

  -- Commitment & overage
  monthly_min_commit_usd      DECIMAL(12, 2),
  monthly_min_commit_local    DECIMAL(12, 2),
  monthly_min_commit_currency TEXT,
  overage_rate_multiplier     DECIMAL(5, 2) NOT NULL DEFAULT 1.0,

  -- Prepay
  prepay_total_usd     DECIMAL(12, 2),
  prepay_remaining_usd DECIMAL(12, 2),
  prepay_expires_at    TIMESTAMPTZ,

  -- Lifecycle
  start_date  DATE,
  end_date    DATE,
  auto_renew  BOOLEAN NOT NULL DEFAULT false,

  -- Contacts & artifacts
  contact_email        TEXT,
  contact_phone        TEXT,
  account_manager_name TEXT,
  contract_pdf_url     TEXT,

  status     TEXT NOT NULL DEFAULT 'active',
  signed_at  TIMESTAMPTZ,
  notes      TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'provider_contracts_status_check'
  ) THEN
    ALTER TABLE provider_contracts
      ADD CONSTRAINT provider_contracts_status_check
      CHECK (status IN ('active', 'expired', 'pending', 'terminated'));
  END IF;
END$$;

CREATE INDEX IF NOT EXISTS idx_contracts_tenant
  ON provider_contracts (tenant_id)
  WHERE tenant_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_contracts_provider_status
  ON provider_contracts (provider_slug, status);

-- Now that contracts table exists, hook provider_pricing.contract_id as FK
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'provider_pricing_contract_fk'
  ) THEN
    ALTER TABLE provider_pricing
      ADD CONSTRAINT provider_pricing_contract_fk
      FOREIGN KEY (contract_id) REFERENCES provider_contracts(contract_id)
      ON DELETE SET NULL;
  END IF;
END$$;

COMMENT ON TABLE provider_contracts IS
  'Contract terms (enterprise discount / volume tier / prepay / overage).
   tenant_id NULL = platform-default; non-null = team-specific (BYOK-enterprise).';
COMMENT ON COLUMN provider_contracts.volume_tier_json IS
  'JSONB array of {threshold_monthly_usd, discount_pct} tiers. Applied at compute_cost() time.';


-- ========================================================================
-- 3. provider_credits — free trials / promotions / SLA refunds / prepay
-- ========================================================================

CREATE TABLE IF NOT EXISTS provider_credits (
  id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
  provider_slug TEXT NOT NULL,
  contract_id TEXT REFERENCES provider_contracts(contract_id) ON DELETE SET NULL,

  credit_type TEXT NOT NULL,

  amount_usd    DECIMAL(12, 2) NOT NULL,
  remaining_usd DECIMAL(12, 2) NOT NULL,

  earned_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at  TIMESTAMPTZ,
  consumed_at TIMESTAMPTZ,

  reason       TEXT,
  evidence_url TEXT,                    -- support ticket / email screenshot

  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'provider_credits_type_check'
  ) THEN
    ALTER TABLE provider_credits
      ADD CONSTRAINT provider_credits_type_check
      CHECK (credit_type IN ('free_trial', 'promotion', 'sla_refund', 'goodwill', 'prepay'));
  END IF;
END$$;

CREATE INDEX IF NOT EXISTS idx_credits_consumable
  ON provider_credits (provider_slug, contract_id)
  WHERE remaining_usd > 0 AND consumed_at IS NULL;

COMMENT ON TABLE provider_credits IS
  'Vendor credits: free trial, promotion, SLA refund, goodwill, prepay top-up.
   compute_cost() consumes from this table when remaining_usd > 0.';


-- ========================================================================
-- 4. fx_rates — multi-currency conversion history
-- ========================================================================

CREATE TABLE IF NOT EXISTS fx_rates (
  id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
  from_currency TEXT NOT NULL,
  to_currency   TEXT NOT NULL,
  rate          DECIMAL(15, 8) NOT NULL,
  effective_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  source        TEXT,                   -- 'exchangerate.host' / 'manual' / 'cbr'

  UNIQUE (from_currency, to_currency, effective_at)
);

CREATE INDEX IF NOT EXISTS idx_fx_lookup
  ON fx_rates (from_currency, to_currency, effective_at DESC);

COMMENT ON TABLE fx_rates IS
  'FX rate history. compute_cost() picks the rate effective at call time
   and persists the fx_rate_id in cost_snapshot so old calls replay correctly.';


-- ========================================================================
-- 5. provider_byok_keys — user-supplied keys (zero-bill identification)
-- ========================================================================

CREATE TABLE IF NOT EXISTS provider_byok_keys (
  id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
  user_id BIGINT,                       -- ai_users.id (BIGINT snowflake)
  team_id BIGINT,                       -- teams.id

  provider_slug TEXT NOT NULL,
  key_hash      TEXT NOT NULL,          -- SHA-256 of the actual key (never store plaintext)
  key_label     TEXT,                   -- user-given label e.g. "my personal anthropic"

  added_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_used_at  TIMESTAMPTZ,
  active        BOOLEAN NOT NULL DEFAULT true,

  UNIQUE (user_id, provider_slug, key_hash)
);

CREATE INDEX IF NOT EXISTS idx_byok_active_lookup
  ON provider_byok_keys (user_id, provider_slug)
  WHERE active = true;

COMMENT ON TABLE provider_byok_keys IS
  'BYOK identification. Calls flagged byok_key_id NOT NULL are zero-billed
   on cost ledger (user owes vendor directly). Latency/quality still tracked.';


-- ========================================================================
-- 6. provider_monthly_spend — cached rollup for volume-tier lookup
-- ========================================================================

CREATE TABLE IF NOT EXISTS provider_monthly_spend (
  provider_slug TEXT NOT NULL,
  contract_id   TEXT NOT NULL,
  year_month    CHAR(7) NOT NULL,       -- '2026-06'

  total_usd        DECIMAL(12, 4) NOT NULL DEFAULT 0,
  total_local_cents BIGINT NOT NULL DEFAULT 0,
  call_count       BIGINT NOT NULL DEFAULT 0,

  last_updated TIMESTAMPTZ NOT NULL DEFAULT now(),

  PRIMARY KEY (provider_slug, contract_id, year_month)
);

COMMENT ON TABLE provider_monthly_spend IS
  'Rollup cache. Background job refreshes from agent_run_events every 5 min.
   compute_cost() reads this to apply volume_tier_json from contracts.';


-- ========================================================================
-- 7. cost_audit_log — compliance trail
-- ========================================================================

CREATE TABLE IF NOT EXISTS cost_audit_log (
  id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
  entity_type TEXT NOT NULL,            -- 'pricing' / 'contract' / 'credit' / 'fx_rate'
  entity_id   TEXT NOT NULL,            -- stringified id of the affected row
  action      TEXT NOT NULL,            -- 'create' / 'update' / 'invalidate' / 'delete'

  changed_by  UUID,
  changed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

  before JSONB,
  after  JSONB,
  reason TEXT
);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'cost_audit_log_action_check'
  ) THEN
    ALTER TABLE cost_audit_log
      ADD CONSTRAINT cost_audit_log_action_check
      CHECK (action IN ('create', 'update', 'invalidate', 'delete'));
  END IF;
END$$;

CREATE INDEX IF NOT EXISTS idx_audit_entity
  ON cost_audit_log (entity_type, entity_id, changed_at DESC);

COMMENT ON TABLE cost_audit_log IS
  'Compliance audit trail for price/contract/credit changes. Append-only.
   Future: tighten with trigger to capture all writes automatically.';


-- ========================================================================
-- 8. provider_effective_rate (VIEW) — auto-apply enterprise discount
-- ========================================================================

CREATE OR REPLACE VIEW provider_effective_rate AS
SELECT
  p.id,
  p.provider_slug,
  p.model_slug,
  p.region,
  p.modality,
  p.contract_id,
  p.effective_from,
  p.effective_to,
  c.tenant_id,
  c.enterprise_discount_pct,
  c.volume_tier_json,

  -- Static enterprise-discount-aware rates (volume tier applied at runtime)
  (p.list_input_per_1m_usd       * (1 - COALESCE(c.enterprise_discount_pct, 0) / 100.0))::DECIMAL(12, 6)
    AS effective_input_usd,
  (p.list_output_per_1m_usd      * (1 - COALESCE(c.enterprise_discount_pct, 0) / 100.0))::DECIMAL(12, 6)
    AS effective_output_usd,
  (p.list_cache_read_per_1m_usd  * (1 - COALESCE(c.enterprise_discount_pct, 0) / 100.0))::DECIMAL(12, 6)
    AS effective_cache_read_usd,
  (p.list_cache_write_per_1m_usd * (1 - COALESCE(c.enterprise_discount_pct, 0) / 100.0))::DECIMAL(12, 6)
    AS effective_cache_write_usd,
  (p.list_reasoning_per_1m_usd   * (1 - COALESCE(c.enterprise_discount_pct, 0) / 100.0))::DECIMAL(12, 6)
    AS effective_reasoning_usd,
  (p.list_tool_use_per_1m_usd    * (1 - COALESCE(c.enterprise_discount_pct, 0) / 100.0))::DECIMAL(12, 6)
    AS effective_tool_use_usd,
  (p.list_vision_per_1m_usd      * (1 - COALESCE(c.enterprise_discount_pct, 0) / 100.0))::DECIMAL(12, 6)
    AS effective_vision_usd,
  (p.list_audio_per_1m_usd       * (1 - COALESCE(c.enterprise_discount_pct, 0) / 100.0))::DECIMAL(12, 6)
    AS effective_audio_usd,

  -- Media rates (same discount applied)
  (p.list_image_per_unit_usd     * (1 - COALESCE(c.enterprise_discount_pct, 0) / 100.0))::DECIMAL(12, 6)
    AS effective_image_per_unit_usd,
  (p.list_video_per_second_usd   * (1 - COALESCE(c.enterprise_discount_pct, 0) / 100.0))::DECIMAL(12, 6)
    AS effective_video_per_second_usd,

  -- Passthrough fields
  p.batch_discount_pct,
  p.cache_ttl_seconds,
  p.fail_billing_policy,
  p.extra

FROM provider_pricing p
LEFT JOIN provider_contracts c ON p.contract_id = c.contract_id
WHERE p.effective_to IS NULL;

COMMENT ON VIEW provider_effective_rate IS
  'Currently-effective rates with enterprise discount applied.
   Volume tier + BYOK + credit consumption + FX happen at compute_cost() time.';


-- ============================================================================
-- RLS — all 7 tables: service_role full, no user-level read
-- ============================================================================

ALTER TABLE provider_pricing       ENABLE ROW LEVEL SECURITY;
ALTER TABLE provider_contracts     ENABLE ROW LEVEL SECURITY;
ALTER TABLE provider_credits       ENABLE ROW LEVEL SECURITY;
ALTER TABLE fx_rates               ENABLE ROW LEVEL SECURITY;
ALTER TABLE provider_byok_keys     ENABLE ROW LEVEL SECURITY;
ALTER TABLE provider_monthly_spend ENABLE ROW LEVEL SECURITY;
ALTER TABLE cost_audit_log         ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "pricing_service_full" ON provider_pricing;
CREATE POLICY "pricing_service_full" ON provider_pricing
  FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "contracts_service_full" ON provider_contracts;
CREATE POLICY "contracts_service_full" ON provider_contracts
  FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "credits_service_full" ON provider_credits;
CREATE POLICY "credits_service_full" ON provider_credits
  FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "fx_service_full" ON fx_rates;
CREATE POLICY "fx_service_full" ON fx_rates
  FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "byok_service_full" ON provider_byok_keys;
CREATE POLICY "byok_service_full" ON provider_byok_keys
  FOR ALL TO service_role USING (true) WITH CHECK (true);

-- BYOK users can read their own key labels (not the hash) via a future RPC.
-- M0: no direct table read for users.

DROP POLICY IF EXISTS "spend_service_full" ON provider_monthly_spend;
CREATE POLICY "spend_service_full" ON provider_monthly_spend
  FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "audit_service_full" ON cost_audit_log;
CREATE POLICY "audit_service_full" ON cost_audit_log
  FOR ALL TO service_role USING (true) WITH CHECK (true);


-- ============================================================================
-- Verification queries (manual smoke test after apply)
-- ============================================================================

-- 1. modality enum coverage:
--    INSERT into provider_pricing should reject modality='nonsense'
--
-- 2. effective_rate view should compute discounted prices:
--    INSERT a row into provider_contracts (enterprise_discount_pct=20) and
--    provider_pricing referencing it; the view's effective_input_usd should be
--    list_input_per_1m_usd * 0.8.
--
-- 3. RLS isolation:
--    SET role authenticated; SELECT * FROM provider_pricing  -- should be empty.
--    SET role service_role;  SELECT * FROM provider_pricing  -- should work.
