-- 041_points_system.sql
-- Points capacity & payment system: tables, indexes, RLS policies, seed data
-- Execute in Supabase SQL Editor or via `supabase db push`

-- ============================================================================
-- Part 0: Extend team_members role constraint (add 'admin' if not present)
-- ============================================================================

DO $$
BEGIN
  -- Safely drop and recreate the constraint to include 'admin'
  ALTER TABLE team_members DROP CONSTRAINT IF EXISTS team_members_role_check;
  ALTER TABLE team_members ADD CONSTRAINT team_members_role_check
    CHECK (role IN ('owner', 'admin', 'member'));
END $$;

-- ============================================================================
-- Part 1: point_packages - Purchasable point packages
-- ============================================================================

CREATE TABLE IF NOT EXISTS point_packages (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name VARCHAR(100) NOT NULL,
  description TEXT,
  points_amount INTEGER NOT NULL CHECK (points_amount > 0),
  price_cents INTEGER NOT NULL CHECK (price_cents > 0),
  currency VARCHAR(10) NOT NULL DEFAULT 'CNY',
  is_active BOOLEAN NOT NULL DEFAULT true,
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================================
-- Part 2: team_quotas - Team points balance & storage quota
-- ============================================================================

CREATE TABLE IF NOT EXISTS team_quotas (
  team_id UUID PRIMARY KEY REFERENCES teams(id) ON DELETE CASCADE,
  points_balance INTEGER NOT NULL DEFAULT 0,
  storage_limit_bytes BIGINT NOT NULL DEFAULT 5368709120,  -- 5 GB
  storage_used_bytes BIGINT NOT NULL DEFAULT 0,
  free_points_granted BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================================
-- Part 3: member_quotas - Per-member monthly limits
-- ============================================================================

CREATE TABLE IF NOT EXISTS member_quotas (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  team_id UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  monthly_points_limit INTEGER,  -- NULL = unlimited
  points_used_this_month INTEGER NOT NULL DEFAULT 0,
  reset_at TIMESTAMPTZ NOT NULL DEFAULT (date_trunc('month', NOW()) + INTERVAL '1 month'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (team_id, user_id)
);

-- ============================================================================
-- Part 4: point_transactions - Points ledger
-- ============================================================================

CREATE TABLE IF NOT EXISTS point_transactions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  team_id UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES auth.users(id),
  amount INTEGER NOT NULL,  -- positive = credit, negative = debit
  balance_after INTEGER NOT NULL,
  type VARCHAR(20) NOT NULL CHECK (type IN ('purchase', 'consume', 'refund', 'gift', 'admin_adjust')),
  reference_type VARCHAR(50),
  reference_id VARCHAR(200),
  description TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================================
-- Part 5: point_pricing - Action costs
-- ============================================================================

CREATE TABLE IF NOT EXISTS point_pricing (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  action_type VARCHAR(50) NOT NULL UNIQUE,
  points_cost INTEGER NOT NULL CHECK (points_cost >= 0),
  description TEXT,
  is_active BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================================
-- Part 6: orders - Payment orders
-- ============================================================================

CREATE TABLE IF NOT EXISTS orders (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  team_id UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES auth.users(id),
  package_id UUID REFERENCES point_packages(id),
  points_amount INTEGER NOT NULL CHECK (points_amount > 0),
  amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
  currency VARCHAR(10) NOT NULL DEFAULT 'CNY',
  payment_method VARCHAR(20) NOT NULL CHECK (payment_method IN ('wechat', 'alipay')),
  payment_status VARCHAR(20) NOT NULL DEFAULT 'pending'
    CHECK (payment_status IN ('pending', 'paid', 'failed', 'expired', 'refunded')),
  trade_no VARCHAR(200),
  payment_url TEXT,
  paid_at TIMESTAMPTZ,
  expired_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '30 minutes'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================================
-- Part 7: Indexes
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_point_transactions_team_user
  ON point_transactions (team_id, user_id, type, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_orders_team_user_status
  ON orders (team_id, user_id, payment_status);

CREATE INDEX IF NOT EXISTS idx_orders_trade_no
  ON orders (trade_no) WHERE trade_no IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_member_quotas_team_user
  ON member_quotas (team_id, user_id);

-- ============================================================================
-- Part 8: Auto-update updated_at triggers
-- Reuses the existing update_updated_at_column() function from 001_initial_schema.sql
-- ============================================================================

DROP TRIGGER IF EXISTS update_point_packages_updated_at ON point_packages;
CREATE TRIGGER update_point_packages_updated_at
  BEFORE UPDATE ON point_packages
  FOR EACH ROW
  EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_team_quotas_updated_at ON team_quotas;
CREATE TRIGGER update_team_quotas_updated_at
  BEFORE UPDATE ON team_quotas
  FOR EACH ROW
  EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_member_quotas_updated_at ON member_quotas;
CREATE TRIGGER update_member_quotas_updated_at
  BEFORE UPDATE ON member_quotas
  FOR EACH ROW
  EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_point_pricing_updated_at ON point_pricing;
CREATE TRIGGER update_point_pricing_updated_at
  BEFORE UPDATE ON point_pricing
  FOR EACH ROW
  EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_orders_updated_at ON orders;
CREATE TRIGGER update_orders_updated_at
  BEFORE UPDATE ON orders
  FOR EACH ROW
  EXECUTE FUNCTION update_updated_at_column();

-- ============================================================================
-- Part 9: Seed default pricing data
-- ============================================================================

INSERT INTO point_pricing (action_type, points_cost, description) VALUES
  ('video_parse',         5,  'Parse a single video link'),
  ('video_parse_batch',   4,  'Parse a video link in batch mode (discounted)'),
  ('ai_transcription',   20,  'AI transcription of video audio'),
  ('ai_summary',         15,  'AI-generated video summary'),
  ('ai_visual_analysis', 15,  'AI visual analysis of video content'),
  ('storage_gb_month',   50,  'Storage cost per GB per month')
ON CONFLICT (action_type) DO NOTHING;

-- ============================================================================
-- Part 10: Seed default point packages
-- ============================================================================

INSERT INTO point_packages (name, description, points_amount, price_cents, currency, sort_order) VALUES
  ('Starter Pack',  'Get started with 100 points',                    100,  1000,  'CNY', 1),
  ('Standard Pack', '500 points with 10% bonus value',                500,  4500,  'CNY', 2),
  ('Pro Pack',      '2000 points — best value for power users',      2000, 16000,  'CNY', 3),
  ('Team Pack',     '10000 points for teams with heavy usage',       10000, 70000, 'CNY', 4)
ON CONFLICT DO NOTHING;

-- ============================================================================
-- Part 11: Enable RLS on all new tables
-- ============================================================================

ALTER TABLE point_packages ENABLE ROW LEVEL SECURITY;
ALTER TABLE team_quotas ENABLE ROW LEVEL SECURITY;
ALTER TABLE member_quotas ENABLE ROW LEVEL SECURITY;
ALTER TABLE point_transactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE point_pricing ENABLE ROW LEVEL SECURITY;
ALTER TABLE orders ENABLE ROW LEVEL SECURITY;

-- ============================================================================
-- Part 12: RLS Policies
-- ============================================================================

-- ---- point_packages: anyone can read active packages ----

DROP POLICY IF EXISTS "Anyone can read active packages" ON point_packages;
CREATE POLICY "Anyone can read active packages"
  ON point_packages FOR SELECT
  USING (is_active = true);

DROP POLICY IF EXISTS "Service role full access on point_packages" ON point_packages;
CREATE POLICY "Service role full access on point_packages"
  ON point_packages FOR ALL
  USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ---- point_pricing: anyone can read active pricing ----

DROP POLICY IF EXISTS "Anyone can read active pricing" ON point_pricing;
CREATE POLICY "Anyone can read active pricing"
  ON point_pricing FOR SELECT
  USING (is_active = true);

DROP POLICY IF EXISTS "Service role full access on point_pricing" ON point_pricing;
CREATE POLICY "Service role full access on point_pricing"
  ON point_pricing FOR ALL
  USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ---- team_quotas: team members can read their team's quota ----

DROP POLICY IF EXISTS "Team members can read team quota" ON team_quotas;
CREATE POLICY "Team members can read team quota"
  ON team_quotas FOR SELECT
  USING (
    team_id IN (SELECT get_user_team_ids(auth.uid()))
  );

DROP POLICY IF EXISTS "Service role full access on team_quotas" ON team_quotas;
CREATE POLICY "Service role full access on team_quotas"
  ON team_quotas FOR ALL
  USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ---- member_quotas: members read own, owner/admin read all team ----

DROP POLICY IF EXISTS "Members can read own quota" ON member_quotas;
CREATE POLICY "Members can read own quota"
  ON member_quotas FOR SELECT
  USING (
    user_id = auth.uid()
  );

DROP POLICY IF EXISTS "Owner or admin can read all team member quotas" ON member_quotas;
CREATE POLICY "Owner or admin can read all team member quotas"
  ON member_quotas FOR SELECT
  USING (
    team_id IN (
      SELECT team_id FROM team_members
      WHERE user_id = auth.uid() AND role IN ('owner', 'admin')
    )
  );

DROP POLICY IF EXISTS "Service role full access on member_quotas" ON member_quotas;
CREATE POLICY "Service role full access on member_quotas"
  ON member_quotas FOR ALL
  USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ---- point_transactions: team members read their team's transactions ----

DROP POLICY IF EXISTS "Team members can read team transactions" ON point_transactions;
CREATE POLICY "Team members can read team transactions"
  ON point_transactions FOR SELECT
  USING (
    team_id IN (SELECT get_user_team_ids(auth.uid()))
  );

DROP POLICY IF EXISTS "Service role full access on point_transactions" ON point_transactions;
CREATE POLICY "Service role full access on point_transactions"
  ON point_transactions FOR ALL
  USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ---- orders: user reads own, owner/admin reads team orders ----

DROP POLICY IF EXISTS "Users can read own orders" ON orders;
CREATE POLICY "Users can read own orders"
  ON orders FOR SELECT
  USING (
    user_id = auth.uid()
  );

DROP POLICY IF EXISTS "Owner or admin can read team orders" ON orders;
CREATE POLICY "Owner or admin can read team orders"
  ON orders FOR SELECT
  USING (
    team_id IN (
      SELECT team_id FROM team_members
      WHERE user_id = auth.uid() AND role IN ('owner', 'admin')
    )
  );

DROP POLICY IF EXISTS "Service role full access on orders" ON orders;
CREATE POLICY "Service role full access on orders"
  ON orders FOR ALL
  USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ============================================================================
-- Done!
-- Verify by running:
--   SELECT count(*) FROM point_packages;   -- should be 4
--   SELECT count(*) FROM point_pricing;    -- should be 6
--   SELECT * FROM team_quotas LIMIT 1;
-- ============================================================================
