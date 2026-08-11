-- 420_agent_permission_audits.sql
--
-- Agent 权限变更审计（spec: docs/superpowers/specs/2026-08-10-agent-permissions-overhaul-design.md §3）。
-- 在此之前 capability_profile 的 PATCH 只有 logger.info 一行日志，无从回答
-- "谁在何时给哪个 agent 开了 write"。qm「查得清」柱子对照下唯一的实缺口。
--
-- append-only：不提供任何 UPDATE/DELETE 路径。快照存的是 parser 解析后的
-- 生效值（与 /agents 响应的 _with_resolved_permissions 同源），不是原始 JSONB
-- ——审计要记录"运行时会放行什么"。
--
-- RLS 不启用：后端内部表，不经 PostgREST，可见性由 API 层按
-- _can_edit_chat_permissions 同款闸门控制（与 script_shot_ops 同口径）。

CREATE TABLE IF NOT EXISTS public.agent_permission_audits (
  id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
  agent_id UUID NOT NULL REFERENCES public.ai_agents(id) ON DELETE CASCADE,
  changed_by UUID NOT NULL,
  before_json JSONB NOT NULL,
  after_json JSONB NOT NULL,
  reason TEXT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_agent_permission_audits_agent
  ON public.agent_permission_audits (agent_id, created_at DESC);
