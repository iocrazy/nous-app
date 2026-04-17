-- Migration 116: Nous Models & AI Usage Tracking
-- Adds nous_models table for admin-configured platform AI models
-- Extends point_transactions with usage tracking fields

-- 1. nous_models table (Snowflake BIGINT ID)
CREATE TABLE IF NOT EXISTS nous_models (
  id              BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
  name            TEXT NOT NULL UNIQUE,
  display_name    TEXT NOT NULL,
  category        TEXT NOT NULL CHECK (category IN ('transcription', 'summarization', 'analysis')),
  actual_provider TEXT NOT NULL,
  actual_model    TEXT NOT NULL,
  api_key         TEXT NOT NULL,
  app_id          TEXT,
  base_url        TEXT,
  pricing_type    TEXT NOT NULL DEFAULT 'per_hour' CHECK (pricing_type IN ('per_hour', 'per_request', 'per_token')),
  pricing_value   NUMERIC NOT NULL DEFAULT 8,
  is_enabled      BOOLEAN NOT NULL DEFAULT TRUE,
  sort_order      INT NOT NULL DEFAULT 0,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2. Add fields to point_transactions for usage tracking
ALTER TABLE point_transactions
  ADD COLUMN IF NOT EXISTS provider TEXT,
  ADD COLUMN IF NOT EXISTS model TEXT,
  ADD COLUMN IF NOT EXISTS duration_seconds NUMERIC,
  ADD COLUMN IF NOT EXISTS is_nous BOOLEAN DEFAULT FALSE;

-- 3. RLS: admin-only write, public read for enabled models
ALTER TABLE nous_models ENABLE ROW LEVEL SECURITY;

CREATE POLICY nous_models_admin_all ON nous_models
  FOR ALL
  USING (
    EXISTS (
      SELECT 1 FROM user_profiles
      WHERE id = auth.uid() AND role = 'admin'
    )
  );

CREATE POLICY nous_models_public_read ON nous_models
  FOR SELECT
  USING (is_enabled = TRUE);

-- 4. Indexes
CREATE INDEX IF NOT EXISTS idx_nous_models_category ON nous_models(category, is_enabled);
CREATE INDEX IF NOT EXISTS idx_point_transactions_is_nous ON point_transactions(is_nous) WHERE is_nous = TRUE;
