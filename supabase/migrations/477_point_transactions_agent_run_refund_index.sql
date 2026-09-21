-- 477: point_transactions —— 给「这条 run 退了多少分」那次查询一个索引。
--
-- 474 给 consume 腿建了索引；终审 I6 之后 `charged_points_for_references` 变成**两条
-- 腿**（净扣 = 扣 − 退），而第二条腿一直没有索引可用：
--   SELECT reference_id, sum(amount) FROM point_transactions
--    WHERE type='refund' AND reference_type='agent_run'
--      AND reference_id IN (…) GROUP BY reference_id;
--
-- 表上与 refund 沾边的只有 mig 123 的
--   idx_point_transactions_unique_refund (team_id, reference_type, reference_id)
--     WHERE type='refund' AND reference_id IS NOT NULL
-- —— **首列是 team_id，而这条查询不带 team_id**，所以它最多只能被整个扫一遍再过滤，
-- 最坏就是 point_transactions 顺扫。两者都随积分流水线性变慢，而这条链是**被前端
-- 轮询**的（议题详情页每次刷新都问一遍）—— 正是 474 存在的那个理由，在第二条腿上
-- 又开了一次。今天不咬人（refund 行只有 81 条量级），但它会安静地变慢，没有任何
-- 探针会说出来。
--
-- 形状与 474 逐条对齐，理由也一样：
--   * **partial**，谓词与查询里那两个等值条件**逐字一致** —— 差一个字 planner 就用
--     不上它，而结果仍然正确，所以不会有任何东西报错。索引体积跟着「退过多少次
--     agent 的钱」走，而不是跟着整本流水走。
--   * **不是 UNIQUE**。⚠️ 这一条与 123 那个 unique 索引并存是刻意的：123 保证
--     「同一个 (team, reference_type, reference_id) 至多一笔退款」，本索引只负责让
--     读快起来。把本索引也建成 UNIQUE 等于把 123 的约束换一个更弱的键重述一遍
--     （少了 team_id），跨团队同名引用会写不进去。
--   * **不用 CONCURRENTLY**：run-migration 把整批迁移喂进同一个 psql 事务，
--     CONCURRENTLY 在事务里不合法；这张表的体量下普通建索引的锁窗口可以接受。
--   * **不 SET ROLE**（CLAUDE.md：那是主动降权，在 schema-drift 的裸库上必然
--     permission denied）。

CREATE INDEX IF NOT EXISTS idx_point_transactions_agent_run_refund
    ON public.point_transactions (reference_id)
    WHERE type = 'refund' AND reference_type = 'agent_run';

COMMENT ON INDEX public.idx_point_transactions_agent_run_refund IS
    'Serves the refund leg of PointsRepository.charged_points_for_references '
    '(final-review I6): charged points are net of refunds, so the per-run '
    'lookup runs twice — once per type — and each leg needs its own partial '
    'index on the same two equality predicates. Sibling of '
    'idx_point_transactions_agent_run_consume (mig 474).';
