-- 487: DROP agent_run_events（mig 155 的成本审计表）。
-- 2026-09-22 实测：239 行 / 256 kB；211 行是 CostAuditorHook（3c §3.2 已退役）留下的
-- 旧成本行，28 行是 scope_resolver 的 granted 审计，denied 七周 0 行；零读方（API /
-- 前端 / admin 全无）。同名曾让 mig 285 的 transcript 写入静默 no-op（见 397）。
-- 审计需求本身保留：scope_resolver 的 denied 决策改写 alert_history（admin Alerts 页，
-- 唯一有人看的审计面），granted 不再落库。
--
-- 278 的七个 admin 成本看板视图全部只读这张表的 cost_snapshot，仓库里（backend /
-- frontend / admin / scripts / deploy）没有任何一处 SELECT 它们 —— 与表同批删除。
-- 逐个点名而不是 DROP TABLE ... CASCADE：库里若还有仓库之外建的依赖对象，
-- 让这条迁移大声失败，而不是被 CASCADE 悄悄一起带走。
DROP VIEW IF EXISTS public.v_admin_today_total_cost;
DROP VIEW IF EXISTS public.v_admin_cost_by_provider_30d;
DROP VIEW IF EXISTS public.v_admin_cost_by_agent_slug_30d;
DROP VIEW IF EXISTS public.v_admin_top_users_cost_30d;
DROP VIEW IF EXISTS public.v_admin_cache_hit_rate_30d;
DROP VIEW IF EXISTS public.v_admin_cost_anomalies_24h;
DROP VIEW IF EXISTS public.v_admin_outcome_distribution_30d;
DROP TABLE IF EXISTS public.agent_run_events;
