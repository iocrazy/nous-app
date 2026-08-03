-- 400: AI Library 管理面 B1（spec 2026-08-02）— agent 分组列。
-- 列名避开 SQL 保留字 group；NULL = 未分组（UI 落"工具组"兜底）。
ALTER TABLE public.ai_agents ADD COLUMN IF NOT EXISTS agent_group TEXT;

COMMENT ON COLUMN public.ai_agents.agent_group IS
  'Roster grouping for the AI Library gallery: writing / art / tools. NULL = ungrouped (UI falls back to tools).';
