-- Add `icon` column to ai_agents so each agent can carry its own lucide icon
-- name (rendered in the sidebar list + agent editor). Nullable — when unset,
-- the UI falls back to a default `Bot` icon. Stored as short text (icon slug
-- from the frontend allow-list, e.g. 'bot', 'sparkles', 'brain-circuit').
ALTER TABLE ai_agents
  ADD COLUMN IF NOT EXISTS icon TEXT;
