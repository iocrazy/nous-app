-- 288_agent_capability_profile.sql
-- Phase 4.5 Week 1 (canvas plan): per-agent capability profile.
-- Consumed by the CapabilityGate PreToolUse hook (priority 25) to
-- enforce tool/skill access per agent. Recognized keys (all optional):
--   tool_blacklist          text[]  — tool names this agent may never call
--   allowed_skills          text[]  — when non-empty, Skill() is limited to these slugs
--   max_parallel_delegates  int     — 0 blocks Delegate entirely
--                                     (>0 concurrency enforcement lands with
--                                      M2 concurrent delegate dispatch)
--   context_budget_tokens   int     — abort tool calls once accumulated
--                                     prompt+completion tokens exceed this

ALTER TABLE ai_agents
    ADD COLUMN IF NOT EXISTS capability_profile JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN ai_agents.capability_profile IS
    'Phase 4.5 capability gating: {tool_blacklist, allowed_skills, '
    'max_parallel_delegates, context_budget_tokens}. Empty object = ungated.';
