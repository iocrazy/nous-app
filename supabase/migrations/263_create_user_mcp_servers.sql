-- Create the user_mcp_servers table (per-user outbound MCP server registrations).
--
-- Originally migration 194, which was never applied to the current self-hosted
-- prod instance (table absent — verified 2026-06-07; prod was restored from a
-- snapshot that predates 194, and the CI runner only applies newly-ADDED files).
-- ai_library_router / ai_library_chat_wiring already reference this table, so the
-- per-user MCP-server feature is broken until it exists.
--
-- Idempotent re-statement of mig 194. Matches app.models.users.UserMcpServers.

CREATE TABLE IF NOT EXISTS user_mcp_servers (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,

  -- Identity (namespace prefix on tool names). alphanumeric + underscore, no '.'.
  name         TEXT NOT NULL CHECK (name ~ '^[A-Za-z0-9_]+$'),

  url          TEXT NOT NULL CHECK (url ~ '^https?://'),
  bearer_token TEXT,             -- nullable; encrypted at the app layer (secret_box)

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

-- RLS: only the owner can SELECT/INSERT/UPDATE/DELETE (service-role bypasses).
ALTER TABLE user_mcp_servers ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "user_mcp_servers_owner_all" ON user_mcp_servers;
CREATE POLICY "user_mcp_servers_owner_all"
  ON user_mcp_servers
  FOR ALL
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());

NOTIFY pgrst, 'reload schema';
