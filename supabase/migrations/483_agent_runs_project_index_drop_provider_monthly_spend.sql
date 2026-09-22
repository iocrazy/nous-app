-- 483: 两件 schema 收尾（3d 第 1 批）。
--
-- ① agent_runs.project_id 从 145 建表起没有任何索引，而它是两个真过滤方的谓词：
--    efficiency_groups 的 scope（按项目看效率页）与 count_auto_dispatches_today
--    （autopilot 每次派发前都查 project_id = ? AND trigger = 'issue_dispatch_auto'
--    AND started_at >= ?）。partial 是因为 project_id 大面积为 NULL；带 started_at
--    让后一条查询整个走索引。
-- ② provider_monthly_spend（279 建）零写方、零读方、前端零引用，只剩 ORM 声明。
--    DROP 与删 ORM 类同批 —— schema-drift 两向零容忍，拆开任一侧都红。
--    ⚠️ agent_run_events 不在此列：scope_resolver._audit 仍在往里写，它有真写方，
--    留不留是产品判断而不是 schema 收尾，所以这一批不碰。

CREATE INDEX IF NOT EXISTS idx_agent_runs_project_started
    ON public.agent_runs (project_id, started_at DESC)
    WHERE project_id IS NOT NULL;

DROP TABLE IF EXISTS public.provider_monthly_spend;
