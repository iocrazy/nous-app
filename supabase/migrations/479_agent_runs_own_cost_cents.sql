-- 479: agent_runs.own_cost_cents —— 这条 run **自己**花的钱，不含任何后代。
-- 老列 cost_cents 的含义是「自身 + 已经报到父级的后代」：进程内子 agent 与后台子
-- agent 会报上来，Delegate 出去的同级 agent 永远不会，崩溃写方根本不碰它。于是
-- 只读 root 行的读面（议题预算门禁 spent_cents_for_issue、/usage/issues、效率页）
-- 漏掉 Delegate 链的钱，对所有行求和的读面（agent 月度预算停机、异常告警）又把
-- 子 run 算两遍。同一列没有任何一种安全的求和方式。
-- 新列的定义只有一句：own_cost_cents = metadata_json.cost.own_cents + media_cents，
-- 与 tree_charge.spend_of_run(cost).total、与喂给 ai_usage_hourly 的数是同一个。
-- 写方是 RunEventWriter.mirror_stmt（每个事件一次）与 RunRecorder._finish（终态）。
-- 老列保留：单行展示仍要「这个 run 连同它派出去的活一共多少」，只是不再参与聚合。
-- spec: docs/superpowers/specs/2026-09-21-agent-run-own-cost-column-design.md

ALTER TABLE public.agent_runs
    ADD COLUMN IF NOT EXISTS own_cost_cents NUMERIC(12, 6);

COMMENT ON COLUMN public.agent_runs.own_cost_cents IS
    'This run''s OWN spend in cents (cost.own_cents + cost.media_cents), no '
    'descendants, BYOK not subtracted. Safe to SUM by issue / agent / '
    'COALESCE(root_run_id, id). Written by RunEventWriter.mirror_stmt and '
    'RunRecorder._finish. NULL = never computed (pre-479 rows the backfill '
    'could not settle).';

COMMENT ON COLUMN public.agent_runs.cost_cents IS
    'DISPLAY ONLY. This run plus the descendants that have REPORTED back '
    '(in-process and background subagents; never Delegate peers; crash '
    'writers leave it stale). Do NOT SUM it — use own_cost_cents.';

-- 回填 ① 有 cost 视图的行：按定义算。
UPDATE public.agent_runs
   SET own_cost_cents = COALESCE((metadata_json->'cost'->>'own_cents')::numeric, 0)
                      + COALESCE((metadata_json->'cost'->>'media_cents')::numeric, 0)
 WHERE own_cost_cents IS NULL
   AND metadata_json ? 'cost';

-- 回填 ② 没有 cost 视图、也没有子 run 的行：没有后代 ⇒ 老列就是自身。
UPDATE public.agent_runs a
   SET own_cost_cents = COALESCE(a.cost_cents, 0)
 WHERE a.own_cost_cents IS NULL
   AND NOT (a.metadata_json ? 'cost')
   AND NOT EXISTS (SELECT 1 FROM public.agent_runs c WHERE c.parent_run_id = a.id);

-- ③ 没有 cost 视图但有子 run 的行：老列里混着后代，拆不开，留 NULL（读方 COALESCE 0）
--   只报数，不猜。生产 2026-09-21 实测 0 行。
DO $$
DECLARE n bigint;
BEGIN
  SELECT count(*) INTO n FROM public.agent_runs WHERE own_cost_cents IS NULL;
  RAISE NOTICE '479: % agent_runs rows left with own_cost_cents = NULL (no cost view, has children)', n;
END $$;

-- ④ 3c 修 agent_worker 戳 issue_id 之前落库的子 run 缺 issue_id / conversation_id：
--   从 root 抄。不补的话，去掉 root 过滤后这些行仍不进议题求和。生产实测 7 行。
UPDATE public.agent_runs c
   SET issue_id        = COALESCE(c.issue_id, r.issue_id),
       conversation_id = COALESCE(c.conversation_id, r.conversation_id)
  FROM public.agent_runs r
 WHERE r.id = c.root_run_id
   AND (c.issue_id IS NULL AND r.issue_id IS NOT NULL
        OR c.conversation_id IS NULL AND r.conversation_id IS NOT NULL);
