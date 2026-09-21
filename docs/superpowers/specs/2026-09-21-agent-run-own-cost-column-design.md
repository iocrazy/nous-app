# agent_runs 自身花费列：让「花了多少」在任意树深、任意委派链上都能安全求和

- 日期：2026-09-21
- 状态：用户已确认（2026-09-21），实施计划 `docs/superpowers/plans/2026-09-21-agent-run-own-cost-column.md`
- 前身：harness 3c 记的「3d 第 0 票」（真栈实证 B3 FAIL）
- 侦察：`.superpowers/sdd/2026-09-21-3d-0-tree-cost/recon.md`（master `e8105861d`，全部 file:line 出处在那里）

## 1. 问题

`agent_runs.cost_cents` 这一列的含义是「**自身 + 已经报到父级的后代**」。它在不同的行、不同的
时刻含义不同：

| 情形 | 列里是什么 |
|---|---|
| 无子的 run | 自身 |
| 有进程内同步子 agent | 自身 + 子树 |
| 有后台子 agent（`task(await=false)`） | 自身 + 子树，**但只在子报到之后**；之前是自身 |
| 有 `Delegate` 出去的同级 agent | 自身（**永远不含**，这条链全程不向父 run 写任何花费事件） |
| 崩溃终态（三个崩溃写方） | 这三个写方不碰这一列，停在上一次的值，常为 NULL |

读方于是没有一种安全的求和方式，两头都已经错了：

**少计（只读 root 行、当「回合总花费」用）**——`Delegate` 链的钱全漏：
- **议题预算门禁** `spent_cents_for_issue`：agent 只要把活 `Delegate` 出去，`issues.budget_cents`
  就拦不住。这是实时闸门，漏了就是真花出去了，比报表少计严重。
- 议题 rollup / 驾驶舱 Budget 格、`/usage/issues/{id}`、`/ai-library/usage/efficiency`、
  聊天气泡消耗行 `/ai-library/runs/costs`、`done` 状态帧。

**多计（对所有行求和、无 root 过滤）**——父行含子、子行又算一次：
- **agent 月度预算停机** `monthly_usage_by_agent`、**花费异常告警** `agent_cost_anomaly`、
  `/ai-library/usage` 模型/日趋势、admin telemetry、`cost_cents_7d` 卡片、agent 详情页 run 分组。

`Delegate` 最多嵌套 3 层，树深可到 4，所以「让 `Delegate` 也回写直接父级」这类两层修法不够，
也修不了多计那一头。

生产量级（2026-09-21，近 30 天）：140 棵树，6 棵含 workforce 子 run，漏计 ¢1.07（约 2%）。
现在量小，是结构性缺陷而不是钱在流失——趁小修。

积分扣费**不受影响**：`tree_charge` 早就按行读 `metadata_json.cost` 的自身两道聚合，不用这一列。
本设计不改任何扣费逻辑。

## 2. 设计

### 2.1 新列

```
agent_runs.own_cost_cents  NUMERIC(12,6)  NULL
```

**定义（唯一一句）：这个 run 自己花的钱 = `cost.own_cents + cost.media_cents`，不含任何后代。**
与 `_finish` 喂给 `ai_usage_hourly` 的 `own_media_cents`、与 `tree_charge.spend_of_run` 的
`total` 是同一个数。真实花费口径，**不扣 BYOK**（BYOK 只影响积分，不影响「花了多少」）。

NULL = 从没算过（只可能出现在回填前的极短窗口）。读方一律 `COALESCE(own_cost_cents, 0)`。

老列 `cost_cents` **保留、写方不动**，语义照旧，只用于「单行展示」（run 列表/详情/live、
workforce 面板 recent_runs）。它不再参与任何聚合；列注释与 ORM docstring 写明这一点。
不删、不改语义的理由：改它的语义要同时动两个写方和全部读方，且单行展示确实需要「这个 run
连同它派出去的活一共多少」这个数。

### 2.2 写方：跟着镜像走

`RunEventWriter.mirror_stmt()` 每次折叠事件都会 UPDATE 该行的 `metadata_json`。新列搭在**同一条
语句**的 `.values(...)` 里，值取自同一份内存视图：

```python
.values(metadata_json=expr, own_cost_cents=spend_of_run(self.views.get("cost")).total)
```

由此得到三条性质，都不需要额外的写路径：

1. **运行中就是实时的。** 读方不需要「running 读视图、终态读列」的分叉。
2. **崩溃写方不用改。** 三个崩溃写方不碰这一列，它自然停在最后一次镜像的值——与
   `metadata_json.cost` 同样新鲜，与积分扣费读到的是同一个数。
3. **终态后到货的 media 也覆盖到。** `deliverable` 事件走 `_fold_and_mirror`，同一条语句。

`_finish` 的终态 UPDATE 也显式写一次（值同 `own_media_cents`），这样即使最后一次镜像失败
（`persist_views()` 返回 False），列上仍是终态时内存里的真值——**比 JSON 那一侧少一个洞**。

公式不新建：`tree_charge.spend_of_run(cost).total` 已经是「own + media」的唯一实现（积分收口
在用），`_finish` 与 `mirror_stmt` 都从它取值；`_finish` 里现存的那份 `own_media_cents` 加法改为
同一来源，源码扫描守卫钉住「相加只许出现在 `spend_of_run`」。

### 2.3 读方：一个表达式、两种分组

树键（全仓唯一写法，做成 repository 里的一个 helper 表达式）：

```
tree_key = COALESCE(root_run_id, id)      -- root 行自己的 root_run_id 恒为 NULL
own      = COALESCE(own_cost_cents, 0)
```

| 读面 | 现在 | 改成 |
|---|---|---|
| 预算门禁 `spent_cents_for_issue` | `SUM(cost_cents) WHERE … AND parent_run_id IS NULL` | `SUM(own)`，**去掉 root 过滤**（子 run 带 `issue_id`） |
| `/usage/issues/{id}` `issue_totals` | `SUM(cost_cents) FILTER (root)` | `SUM(own)`；run 计数仍 `FILTER (root)` |
| 议题 rollup `spent_cents` | 逐 root 读列/视图再相加 | 同一个 repo 函数（三处必须一致的既有约定） |
| `/usage/efficiency` `efficiency_groups` | `SUM(cost_cents) FILTER (root_only)` | 花费 `SUM(own)` 全行；回合数、时长等仍 `FILTER (root_only)` |
| 聊天气泡 `get_run_costs`、`done` 状态帧 | 读 root 单行列 | 新 repo 函数 `tree_cost_cents(root_ids) -> {root: Decimal}`，`GROUP BY tree_key` |
| 月度预算停机、异常告警、模型/日趋势、admin telemetry、`cost_cents_7d`、agent 详情分组 | `SUM(cost_cents)` 全行（双计） | `SUM(own)` 全行（不重不漏） |

分组键是 agent 的读面（月度预算、异常告警）改完后的语义是「**这个 agent 自己烧了多少**」——
父子不同 agent 时各记各的，这正是按 agent 限额想要的。

`efficiency_groups` 的分组维度取自 root 行（按 agent / trigger 分组看的是「这类回合」）。子 run 的
花费要归到**它的 root 所在的组**，所以这一处是「先按 `tree_key` 聚出每棵树的花费，再 join 回 root
行分组」，不是简单去掉 FILTER。计划里单独成一个 Task。

### 2.4 迁移与回填（mig 478）

一条迁移，三步，全部幂等：

1. `ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS own_cost_cents NUMERIC(12,6)` + 列注释；
   同时给 `cost_cents` 补注释「含已报到的后代，禁止聚合」。
2. 回填 `own_cost_cents`（`WHERE own_cost_cents IS NULL`）：
   - **有 `metadata_json.cost`** → `own_cents + media_cents`（生产 151 行）
   - **无 JSON 且无子行** → `cost_cents`（无子 ⇒ 列值就是自身；生产 143 行）
   - **无 JSON 且有子行** → 留 NULL，`RAISE NOTICE` 报数（生产 **0 行**，不需要猜）
3. 回填子 run 缺失的 `issue_id` / `conversation_id`：root 有而子没有的，从 root 抄（生产 7 行，
   都是 3c 修 `agent_worker` 戳 `issue_id` 之前的存量）。不补的话去掉 root 过滤后这 7 行仍然漏。

不加新索引：按议题求和走既有 `idx_agent_runs_issue`，按树走既有 `idx_agent_runs_root_tree` + PK。
表 294 行，回填是一次性小 UPDATE，不需要分批。

迁移不写 `SET ROLE`；`agent_runs` 无受保护列 trigger，普通 UPDATE 即可。ORM 模型同批加列，
过 schema-drift 两向门禁。

**部署顺序**：迁移先行是安全方向（多一列，老代码无感）。代码先行那一侧（列还不存在）会在镜像
UPDATE 上撞 `42703`——所以代码 PR 必须在迁移 PR 上线并核实之后再合。计划里拆成两个 PR 并写死顺序。

### 2.5 顺带修的一个小洞

后台子 agent 崩溃时，`agent_worker` 给父级报的 `subagent_done.cost_cents` 是
`envelope.get("cost_cents") or 0`，而崩溃分支的 envelope 没有这个键 → 报 0。改成读子 run 行上的
真值。这只影响老列 `cost_cents`（单行展示）的准确性，不影响任何聚合——但它就在本次要读的代码
旁边，一并修掉，免得下一个人误以为老列可信。

## 3. 不变量（写进测试）

1. **不重不漏**：对任意一组 run，`SUM(own)` = 这些 run 自身花费之和。构造 4 层树（同步子 +
   后台子 + 两跳 `Delegate`）的真 PG 用例，断言按树、按议题、按 agent 三种分组各自等于手算值，
   且三者总和相等。
2. **与积分账同口径**：同一棵树，`tree_cost_cents(root)` == `bucket_tree(rows).tree_total`。
3. **预算看得见委派**：`Delegate` 子 run 花费计入 `spent_cents_for_issue`（现状红、改后绿）。
4. **崩溃行不归零**：镜像过至少一次的 run 被崩溃写方终结后，`own_cost_cents` 保持最后镜像值。
5. **公式只有一份**：源码扫描守卫——`own_cents` 与 `media_cents` 相加只许出现在
   `tree_charge.spend_of_run` 里。
6. **老列不进聚合**：源码扫描守卫——`func.sum(AgentRuns.cost_cents)` / 裸 SQL `SUM(cost_cents)`
   在 `app/` 下零命中。

## 4. 上线后用户可见的变化

- 议题预算开始计入委派出去的花费，**接近上限的议题会更早停机**。这是修复，不是回归。
- agent 月度用量、异常告警、用量趋势的数字会**下降**（去掉了双计）。
- 聊天气泡消耗行、议题 Budget 格、效率页的花费会**小幅上升**（补上了 `Delegate` 那部分）。
- 不涉及任何扣费、追扣或退款。

## 5. 不做

- `agent_runs.project_id` 加索引（已有票）。
- `ai_usage_hourly` 加 run/树维度（它按小时桶聚合，逐行自身口径本来就不重不漏，不需要）。
- 改老列 `cost_cents` 的语义或删列。
- 孤儿子 run（挂父失败、`parent_run_id`/`root_run_id` 双 NULL）：它是自己的单节点树，花费仍计入
  按议题/按 agent 的求和（只要戳了 `issue_id`），只是不归到原父树。已是 `tree_charge` 文档化的
  Stated Limitation，本次不动。
- 运行中的 `Delegate` 子 run 的花费对预算门禁的可见延迟 = 镜像节奏（每个事件一次），不再另做实时通道。

## 6. 验收

- 真 PG 集成用例接进 `schema-drift.yml`（step 级，更新数词门禁）。
- 真栈：迁移上线后核回填三档读数与 §2.4 一致；代码上线后跑一个含 `Delegate` 的真实回合，核
  `spent_cents_for_issue`、`/usage/issues/{id}`、消耗行、`tree_charge` 四处对同一棵树给出同一个数；
  核月度用量读数下降幅度与「子 run 花费之和」吻合。
- 前端发布后 `npm run e2e:prod`。
