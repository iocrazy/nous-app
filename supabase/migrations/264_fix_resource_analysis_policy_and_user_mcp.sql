-- Fix-up for migrations 262 / 263.
--
-- mig 262 used a BARE ``uid()`` in the resource_analysis RLS policy. The CI
-- migration runner applies as the ``postgres`` role, whose search_path does not
-- resolve bare ``uid()`` ("function uid() does not exist") — so under
-- ``ON_ERROR_STOP`` the batch aborted at CREATE POLICY: resource_analysis got the
-- table + indexes + RLS-enabled but NO policy, and 263 (user_mcp_servers) never
-- ran at all. (It worked on dev only because that was applied as a superuser.)
--
-- This completes both using the schema-qualified ``auth.uid()`` (resolves under
-- any search_path). Idempotent.

-- 1. resource_analysis: add the owner RLS policy that 262 failed to create.
DROP POLICY IF EXISTS "Manage analysis of owned resources" ON resource_analysis;
CREATE POLICY "Manage analysis of owned resources"
    ON resource_analysis
    FOR ALL
    USING (
        EXISTS (
            SELECT 1 FROM resources
            WHERE resources.id = resource_analysis.resource_id
              AND resources.creator_id = auth.uid()
        )
    );

-- 2. user_mcp_servers: 263 never ran — create it now (idempotent).
CREATE TABLE IF NOT EXISTS user_mcp_servers (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  name         TEXT NOT NULL CHECK (name ~ '^[A-Za-z0-9_]+$'),
  url          TEXT NOT NULL CHECK (url ~ '^https?://'),
  bearer_token TEXT,
  description  TEXT,
  enabled      BOOLEAN NOT NULL DEFAULT true,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (user_id, name)
);

CREATE INDEX IF NOT EXISTS idx_user_mcp_servers_user
  ON user_mcp_servers (user_id) WHERE enabled = true;

COMMENT ON TABLE user_mcp_servers IS
  'G1+G5: per-user outbound MCP server registrations. Used by '
  'ai_library_chat_wiring to construct an MCPOutboundRegistry for '
  'each chat turn so the AgentRunner can dispatch to those servers.';

ALTER TABLE user_mcp_servers ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "user_mcp_servers_owner_all" ON user_mcp_servers;
CREATE POLICY "user_mcp_servers_owner_all"
  ON user_mcp_servers
  FOR ALL
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());

NOTIFY pgrst, 'reload schema';
