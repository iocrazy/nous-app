-- 048_permission_billing.sql
-- ReBAC permission overrides + team plan/billing table

-- ============================================================================
-- Part 1: access_overrides — ReBAC permission overrides
-- ============================================================================

CREATE TABLE IF NOT EXISTS access_overrides (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  object_type VARCHAR(20) NOT NULL CHECK (object_type IN ('folder', 'project')),
  object_id   UUID NOT NULL,
  user_id     UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  role        VARCHAR(20) NOT NULL CHECK (role IN ('admin', 'editor', 'viewer', 'none')),
  granted_by  UUID REFERENCES auth.users(id),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (object_type, object_id, user_id)
);

-- ============================================================================
-- Part 2: team_plans — Seat-based billing and feature gating
-- ============================================================================

CREATE TABLE IF NOT EXISTS team_plans (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  team_id              UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  plan_tier            VARCHAR(30) NOT NULL DEFAULT 'free'
    CHECK (plan_tier IN (
      'free',
      'studio_standard', 'studio_pro',
      'enterprise_standard', 'enterprise_pro', 'enterprise_flagship'
    )),
  max_seats            INTEGER NOT NULL DEFAULT 1,
  max_storage_bytes    BIGINT NOT NULL DEFAULT 5368709120,  -- 5GB
  max_project_members  INTEGER NOT NULL DEFAULT 5,
  custom_permissions   BOOLEAN NOT NULL DEFAULT false,
  custom_workflows     BOOLEAN NOT NULL DEFAULT false,
  expires_at           TIMESTAMPTZ,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  UNIQUE (team_id)
);

-- ============================================================================
-- Part 3: Indexes
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_access_overrides_object
  ON access_overrides (object_type, object_id);

CREATE INDEX IF NOT EXISTS idx_access_overrides_user
  ON access_overrides (user_id);

CREATE INDEX IF NOT EXISTS idx_team_plans_team
  ON team_plans (team_id);

CREATE INDEX IF NOT EXISTS idx_team_plans_tier
  ON team_plans (plan_tier);

-- ============================================================================
-- Part 4: Enable RLS
-- ============================================================================

ALTER TABLE access_overrides ENABLE ROW LEVEL SECURITY;
ALTER TABLE team_plans ENABLE ROW LEVEL SECURITY;

-- ============================================================================
-- Part 5: RLS Policies — access_overrides
-- ============================================================================

-- SELECT: users can see overrides that apply to them,
--         or overrides in objects they can access
DROP POLICY IF EXISTS "Users can read own overrides" ON access_overrides;
CREATE POLICY "Users can read own overrides"
  ON access_overrides FOR SELECT
  USING (
    user_id = auth.uid()
    OR granted_by = auth.uid()
  );

-- INSERT/UPDATE/DELETE: only via service role (backend manages overrides)
DROP POLICY IF EXISTS "Service role full access on access_overrides" ON access_overrides;
CREATE POLICY "Service role full access on access_overrides"
  ON access_overrides FOR ALL
  USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ============================================================================
-- Part 6: RLS Policies — team_plans
-- ============================================================================

-- SELECT: team members can read their team's plan
DROP POLICY IF EXISTS "Team members can read team plan" ON team_plans;
CREATE POLICY "Team members can read team plan"
  ON team_plans FOR SELECT
  USING (team_id IN (SELECT get_user_team_ids(auth.uid())));

-- INSERT/UPDATE/DELETE: only via service role (billing backend manages plans)
DROP POLICY IF EXISTS "Service role full access on team_plans" ON team_plans;
CREATE POLICY "Service role full access on team_plans"
  ON team_plans FOR ALL
  USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ============================================================================
-- Part 7: Auto-create free plan for existing teams
-- ============================================================================

INSERT INTO team_plans (team_id, plan_tier, max_seats, max_storage_bytes)
SELECT id, 'free', 1, 5368709120
FROM teams
WHERE id NOT IN (SELECT team_id FROM team_plans)
ON CONFLICT (team_id) DO NOTHING;

-- ============================================================================
-- Done!
-- Verify: SELECT count(*) FROM access_overrides;  -- 0
--         SELECT * FROM team_plans;                -- should have rows for existing teams
-- ============================================================================
