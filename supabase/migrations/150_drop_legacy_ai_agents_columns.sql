-- Migration 150: drop legacy ai_agents columns (persona / config_json / rules).
--
-- Background
-- ----------
-- The AI Library agent framework (migration 138 + 139 + 140) introduced the
-- IDENTITY / SOUL / AGENT.md split. Since then, every system-preset agent has
-- migrated its prompt content into ``agent_md`` (length 891–2640 chars per
-- agent as of 2026-04-23), and ``persona`` has been kept around only as a
-- legacy NOT NULL column populated with throwaway "{Name} agent for MediaHub"
-- placeholders (<30 chars).
--
-- ``config_json`` and ``rules`` were never wired up by the runner / composer
-- and are empty (``{}`` / ``[]``) for every agent.
--
-- This migration drops all three. The Python model has already been updated
-- in the same PR to stop reading or writing them; the prompt composer reads
-- agent_md exclusively (no persona fallback), seed_loader stops setting the
-- persona placeholder, and the user-facing create endpoint no longer sets
-- persona on INSERT.
--
-- Rollback
-- --------
-- Pre-migration sanity check (run before applying):
--
--   SELECT slug, length(coalesce(agent_md, '')) AS agent_md_len,
--          length(coalesce(persona, ''))   AS persona_len,
--          (config_json != '{}'::jsonb)    AS config_used,
--          (rules != '[]'::jsonb)          AS rules_used
--   FROM ai_agents;
--
-- Every row should show agent_md_len > 0 and persona/config/rules unused.
-- If any row has agent_md_len = 0, fix the seed first (re-run startup
-- SeedLoader or repopulate via PATCH) before dropping persona.
--
-- Down: ``ALTER TABLE ai_agents ADD COLUMN persona text NOT NULL DEFAULT '';``
-- (The placeholder content is recoverable from git via the seed loader.)

ALTER TABLE ai_agents
  DROP COLUMN IF EXISTS persona,
  DROP COLUMN IF EXISTS config_json,
  DROP COLUMN IF EXISTS rules;
