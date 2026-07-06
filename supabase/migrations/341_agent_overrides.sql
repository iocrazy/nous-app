-- Migration 341: agent_overrides — per-user / per-team customization layer
-- over SYSTEM PRESET agents.
--
-- Problem: customizing a system agent required forking it into a brand-new
-- ai_agents row (count inflation, skill bindings / fallback chain NOT copied,
-- runs/sessions statistics split from the original). New model: the system
-- agent row stays the single identity; a user (or team) stores only the
-- fields they changed here, merged at read time:
--
--     effective agent = user override ?? team override ?? system row
--
-- "复位 / reset to defaults" = DELETE the override row. ai_agents.id never
-- changes, so every agent_id FK (agent_runs / agent_skills / ai_sessions /
-- agent_memory ...) is untouched by customization.
--
-- Whole-field semantics: a NULL column means "inherit"; a NON-NULL column
-- replaces the system value entirely (user decision 2026-07-05).

CREATE TABLE IF NOT EXISTS agent_overrides (
  id              BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
  agent_id        UUID NOT NULL REFERENCES ai_agents(id) ON DELETE CASCADE,
  -- Exactly one scope: personal override (user_id) or team override (team_id).
  user_id         UUID,
  team_id         BIGINT REFERENCES teams(id) ON DELETE CASCADE,
  -- Overridable fields (NULL = inherit from the system agent row).
  identity_md     TEXT,
  soul_md         TEXT,
  agent_md        TEXT,
  model           TEXT,
  temperature     NUMERIC,
  max_tokens      INT,
  fallback_models TEXT[],
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT agent_overrides_one_scope CHECK (
    ((user_id IS NOT NULL)::int + (team_id IS NOT NULL)::int) = 1
  )
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_agent_overrides_user
  ON agent_overrides(agent_id, user_id) WHERE user_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_agent_overrides_team
  ON agent_overrides(agent_id, team_id) WHERE team_id IS NOT NULL;
-- Reverse lookups: "all overrides for this user / these teams" (sidebar badges).
CREATE INDEX IF NOT EXISTS idx_agent_overrides_user ON agent_overrides(user_id)
  WHERE user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_agent_overrides_team ON agent_overrides(team_id)
  WHERE team_id IS NOT NULL;

-- RLS: personal rows are fully owned by their user; team rows are readable
-- by members and writable by owner/admin. (Backend runs service-role — these
-- policies are the defense-in-depth layer, consistent with ai_agents.)
ALTER TABLE agent_overrides ENABLE ROW LEVEL SECURITY;

CREATE POLICY agent_overrides_user_all ON agent_overrides
  FOR ALL USING (user_id = auth.uid());

CREATE POLICY agent_overrides_team_read ON agent_overrides
  FOR SELECT USING (
    team_id IN (SELECT tm.team_id FROM team_members tm WHERE tm.user_id = auth.uid())
  );

CREATE POLICY agent_overrides_team_write ON agent_overrides
  FOR ALL USING (
    team_id IN (
      SELECT tm.team_id FROM team_members tm
      WHERE tm.user_id = auth.uid() AND tm.role IN ('owner', 'admin')
    )
  );

NOTIFY pgrst, 'reload schema';
