-- 316_agent_chat_permissions.sql
-- Team Chat PHASE-0: agent chat capability gating lives inside the existing
-- ai_agents.capability_profile JSONB (Phase 4.5). We add a documented `chat`
-- sub-object: {enabled, read_team_resources, auto_broadcast, allowed_team_ids}.
-- Absent/false everywhere = the agent CANNOT chat (fail-closed; see
-- backend/app/services/ai/permissions/agent_chat_caps.py).
-- No column change is needed (JSONB is schemaless); this migration documents the
-- convention and indexes the "chat-enabled" predicate used by the
-- add-agent-to-group picker and the AI Library "Chat" badge.

COMMENT ON COLUMN public.ai_agents.capability_profile IS
  'Phase 4.5 capability gating + Team Chat PHASE-0. Keys: tool_blacklist, '
  'allowed_skills, max_parallel_delegates, context_budget_tokens, '
  'rate_limit_tool_calls_per_min, '
  'chat{enabled,read_team_resources,auto_broadcast,allowed_team_ids}. '
  'Empty/absent = ungated EXCEPT chat, which is fail-closed (absent = cannot chat).';

-- NOTE: chat.enabled is stored as a JSON boolean (true/false). The `->>`
-- operator textualizes it to the strings 'true'/'false', so the predicate
-- compares against the text 'true' — consistent with how asyncpg/PostgREST
-- write JSON booleans. (agent_chat_caps still requires a real bool when reading.)
CREATE INDEX IF NOT EXISTS idx_ai_agents_chat_enabled
  ON public.ai_agents ((capability_profile -> 'chat' ->> 'enabled'))
  WHERE (capability_profile -> 'chat' ->> 'enabled') = 'true';
