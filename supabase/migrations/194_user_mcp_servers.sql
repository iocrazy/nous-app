-- G1+G5: per-user MCP server configuration.
--
-- Stores outbound MCP server registrations per user. AgentRunner gets
-- a MCPOutboundRegistry built from this table at chat time.
--
-- Bearer token stored as plain text (TODO when secrets infra lands:
-- pgsodium-encrypted column or external Vault). For now relies on
-- RLS — only the owning user can read.

CREATE TABLE IF NOT EXISTS user_mcp_servers (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,

  -- Identity (becomes namespace prefix on tool names: e.g. 'notion'
  -- → 'notion.create_page'). Must match _is_mcp_tool_name's parser:
  -- alphanumeric + underscore, no '.'.
  name         TEXT NOT NULL CHECK (name ~ '^[A-Za-z0-9_]+$'),

  url          TEXT NOT NULL CHECK (url ~ '^https?://'),
  bearer_token TEXT,             -- nullable; some MCP servers don't auth

  description  TEXT,             -- user-facing label

  enabled      BOOLEAN NOT NULL DEFAULT true,

  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  -- One server per (user, name) — uniqueness sealed at DB
  UNIQUE (user_id, name)
);

CREATE INDEX IF NOT EXISTS idx_user_mcp_servers_user
  ON user_mcp_servers (user_id) WHERE enabled = true;

COMMENT ON TABLE user_mcp_servers IS
  'G1+G5: per-user outbound MCP server registrations. Used by '
  'ai_library_chat_wiring to construct an MCPOutboundRegistry for '
  'each chat turn so the AgentRunner can dispatch to those servers.';

-- RLS: only the owner can SELECT/INSERT/UPDATE/DELETE
ALTER TABLE user_mcp_servers ENABLE ROW LEVEL SECURITY;

CREATE POLICY "user_mcp_servers_owner_all"
  ON user_mcp_servers
  FOR ALL
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());
