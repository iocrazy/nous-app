-- 474: point_transactions —— 给「这条 run 扣了多少分」那次查询一个索引。
--
-- 3c §3.3 的 issue.rollup 每次轮询都要问一遍 `charged_points_for_references`：
--   SELECT reference_id, sum(amount) FROM point_transactions
--    WHERE type='consume' AND reference_type='agent_run'
--      AND reference_id IN (…) GROUP BY reference_id;
--
-- 这张表上已有的三个 reference_* 索引**一个都覆盖不到它**，全是 partial 且谓词
-- 指向别的用途：
--   idx_point_transactions_unique_order         WHERE reference_type='order'
--   idx_point_transactions_unique_order_refund  WHERE reference_type='order_refund'
--   idx_point_transactions_unique_refund        WHERE type='refund'
-- 剩下的 idx_point_transactions_user_id 是 (user_id)，这条查询不带 user_id。
-- 于是它在一个**被前端轮询**的端点上做全表扫描，而且随积分流水线性变慢 ——
-- 没有任何探针会说出来，只会表现为议题详情页越来越慢。
--
-- 同样写成 partial：这条索引只服务 agent_run 的扣分流水，把 order / gift /
-- daily_gift 那些行挡在索引外面，索引体积跟着「跑过多少次 agent」走而不是跟着
-- 整本流水走。谓词与查询里那两个等值条件**逐字一致**，否则 planner 用不上它。
--
-- 不是 UNIQUE：同一次 run 允许有多行（重试、补扣），求和正是为此。
-- 不用 CONCURRENTLY：run-migration 把整批迁移喂进同一个 psql 事务，
-- CONCURRENTLY 在事务里不合法；这张表的体量下普通建索引的锁窗口可以接受。

CREATE INDEX IF NOT EXISTS idx_point_transactions_agent_run_consume
    ON public.point_transactions (reference_id)
    WHERE type = 'consume' AND reference_type = 'agent_run';

COMMENT ON INDEX public.idx_point_transactions_agent_run_consume IS
    'Serves issue.rollup runs[].charged_points (3c §3.3): the per-run consume '
    'ledger lookup in PointsRepository.charged_points_for_references. Partial '
    'on the same two equality predicates that query uses.';
