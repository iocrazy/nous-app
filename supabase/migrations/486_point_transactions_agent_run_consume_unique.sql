-- 486: 同一 run 不能被扣两次 —— 把 474 的普通 partial 索引升级为 UNIQUE。
-- 代码侧 tree_charge 的 CAS（root 行 billing.charged_at）是主闸门；这一条是 DB 级
-- 纵深防御：戳被外力抹掉（pre_cutover_refund 曾把 charged_at 改写成 refunded_at）时
-- 仍有第二道闸。生产 2026-09-22 实测 99 条 consume 流水 0 重复，可直接建。
-- 违例会让 rpc_consume_team_points 整个事务回滚 → 余额不动 → check_and_consume
-- 回 success=False → token_billing 记 WARNING（少扣不多扣，方向正确）。
DROP INDEX IF EXISTS public.idx_point_transactions_agent_run_consume;
CREATE UNIQUE INDEX IF NOT EXISTS idx_point_transactions_agent_run_consume
    ON public.point_transactions (reference_id)
    WHERE type = 'consume' AND reference_type = 'agent_run';
COMMENT ON INDEX public.idx_point_transactions_agent_run_consume IS
    'UNIQUE since 486: one consume per agent run. Also serves the '
    'charged_points_for_references lookup (474). Do NOT merge with the '
    'refund index (477) into a type IN (...) predicate — both would stop '
    'being implied.';
