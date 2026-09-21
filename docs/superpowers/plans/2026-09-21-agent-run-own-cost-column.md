# agent_runs 自身花费列 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `agent_runs.own_cost_cents`（只记 run 自身花费），让所有花费聚合读面在任意树深、任意委派链上不重不漏——修掉 `Delegate` 链绕过议题预算的少计，和月度预算/异常告警的双计。

**Architecture:** 新列由 `RunEventWriter.mirror_stmt()`（每个事件一次）与 `RunRecorder._finish`（终态）两处写，值一律来自 `tree_charge.spend_of_run(cost).total`（已有的唯一公式）。老列 `cost_cents` 保留仅供单行展示。聚合读面改成 `SUM(COALESCE(own_cost_cents,0))`，按议题 / agent 直接去掉 root 过滤，按树用 `COALESCE(root_run_id,id)` 分组。迁移 478 先上线并回填，代码 PR 后上线。

**Tech Stack:** FastAPI + SQLAlchemy 2 async（ORM，禁 `text()` 裸 SQL）、Postgres（Supabase migration 三位数编号）、pytest（真库用例接 `schema-drift.yml`）。

**Spec:** `docs/superpowers/specs/2026-09-21-agent-run-own-cost-column-design.md`
**侦察（file:line 出处）:** `.superpowers/sdd/2026-09-21-3d-0-tree-cost/recon.md`（不进 git，执行方若找不到就按本计划里的路径与函数名 grep）

## Global Constraints

- `own_cost_cents` 定义唯一：`cost.own_cents + cost.media_cents`，不含后代，不扣 BYOK。值只许由 `app.services.ai.billing.tree_charge.spend_of_run(cost).total` 产生，**禁止第二份加法**。
- 老列 `cost_cents`：**写方不动、语义不动**，只用于单行展示（run 列表 / 详情 / live / workforce 面板）。`app/` 下 `func.sum(AgentRuns.cost_cents)` 与裸 `SUM(cost_cents)` 改完后零命中。
- 树键全仓唯一写法：`func.coalesce(AgentRuns.root_run_id, AgentRuns.id)`（root 行自己的 `root_run_id` 恒为 NULL）。
- 读方一律 `COALESCE(own_cost_cents, 0)`。
- **不改任何扣费逻辑**：`tree_charge.settle_tree_if_closed` / `bucket_tree` / `points_repository` 不碰。
- **部署顺序写死**：PR-A（Task 1，迁移 478 + ORM 列）先合并、`run-migration` 成功、生产 `information_schema` 核到列，之后 PR-B（Task 2–7）才许合并。代码先行会在镜像 UPDATE 上撞 `42703`。
- 禁新增 `text()` 裸 SQL；迁移不写 `SET ROLE`；每 Task 独立 worktree 从 `origin/master` 建，git 命令一律 `git -C <绝对路径>`。
- 提交信息 conventional commits，无 attribution。交付前自查：`cd backend && uv run ruff check <files> && uv run black --check <files>`（CI 的格式门禁是 black）。
- 真 PG 集成用例接 `.github/workflows/schema-drift.yml` step 级；该文件有数词门禁（`thirty-three` = step 数），加 step 必须按文件里注释的命令重数并更新数词，同时把新测试文件加进 `pull_request.paths`。
- 计划里的行号来自 master `e8105861d`，实施时以函数名为准。

## 合并顺序

| PR | Task | 说明 |
|---|---|---|
| PR-A | 1 | mig 478 + ORM 列 + 回填真库用例。**先合，盯 run-migration，核生产列存在与回填三档读数** |
| PR-B | 2–7 | 写方、读面、守卫、崩溃子 run 修复。一个分支多个 commit，Task 2–7 串行在同一 worktree 上做 |

PR-B 的 worktree 必须在 PR-A 合并**之后**从 `origin/master` 建，否则 ORM 里没有这一列。

---

### Task 1: mig 478 —— 加列、回填、补子 run 的 issue_id（PR-A）

**Files:**
- Create: `supabase/migrations/478_agent_runs_own_cost_cents.sql`
- Modify: `backend/app/models/agents.py`（`AgentRuns.cost_cents` 附近，约 :364）
- Create: `backend/tests/db/test_migration_478_own_cost_backfill.py`
- Modify: `.github/workflows/schema-drift.yml`（加 step + paths + 数词）

**Interfaces:**
- Produces: 列 `agent_runs.own_cost_cents NUMERIC(12,6) NULL`；ORM `AgentRuns.own_cost_cents: Mapped[Optional[decimal.Decimal]]`。

- [ ] **Step 1: 写迁移**

```sql
-- 478: agent_runs.own_cost_cents —— 这条 run **自己**花的钱，不含任何后代。
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
    'RunRecorder._finish. NULL = never computed (pre-478 rows the backfill '
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
  RAISE NOTICE '478: % agent_runs rows left with own_cost_cents = NULL (no cost view, has children)', n;
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
```

- [ ] **Step 2: ORM 镜像**

在 `backend/app/models/agents.py` 的 `cost_cents` 那行之后加：

```python
    # 这条 run 自己花的钱（cost.own_cents + media_cents），不含后代。聚合读面只许读它；
    # cost_cents 是「自身 + 已报到的后代」，只供单行展示。mig 478。
    own_cost_cents: Mapped[Optional[decimal.Decimal]] = mapped_column(Numeric(12, 6))
```

- [ ] **Step 3: 写真库回填用例（先红）**

`backend/tests/db/test_migration_478_own_cost_backfill.py`。照 `tests/db/test_points_refund_offset_integration.py` 的 fixture 写法（`INTEGRATION_DATABASE_URL` 缺则 skip；每个用例在回滚事务里跑）。回填 SQL 从迁移文件读出来（剥掉 `--` 注释）在事务里执行，而不是手抄一份。

```python
"""mig 478 回填三档 + 子 run issue_id 补齐 —— 只有真 Postgres 能证明 jsonb 路径与
NOT EXISTS 的行为。四个用例各插一组行、跑迁移正文、断言。"""
import os, re, pathlib, pytest
from sqlalchemy import text  # 测试进程内读迁移文件回放，属 schema 探针例外
from sqlalchemy.ext.asyncio import create_async_engine

DSN = os.environ.get("INTEGRATION_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="needs INTEGRATION_DATABASE_URL")
MIG = pathlib.Path(__file__).resolve().parents[3] / "supabase/migrations/478_agent_runs_own_cost_cents.sql"

def _body() -> str:
    return "\n".join(l for l in MIG.read_text().splitlines() if not l.lstrip().startswith("--"))

@pytest.fixture
async def conn():
    eng = create_async_engine(DSN.replace("postgresql://", "postgresql+asyncpg://"))
    async with eng.connect() as c:
        tx = await c.begin()
        yield c
        await tx.rollback()
    await eng.dispose()

async def _insert(conn, rid, *, cost=None, cost_cents=None, parent=None, root=None, issue=None):
    meta = "'{}'" if cost is None else f"'{{\"cost\": {cost}}}'"
    await conn.execute(text(
        "INSERT INTO agent_runs (id, status, cost_cents, parent_run_id, root_run_id, issue_id, metadata_json, started_at) "
        f"VALUES (:id, 'completed', :cc, :p, :r, :i, {meta}::jsonb, now())"
    ), {"id": rid, "cc": cost_cents, "p": parent, "r": root, "i": issue})

async def _own(conn, rid):
    return (await conn.execute(text("SELECT own_cost_cents FROM agent_runs WHERE id=:id"), {"id": rid})).scalar_one()

async def test_tier1_cost_view_wins_over_column(conn):
    await _insert(conn, 90001, cost='{"own_cents": 1.25, "media_cents": 0.5, "by_child": {"x": 9}}', cost_cents=99)
    await conn.execute(text(_body()))
    assert float(await _own(conn, 90001)) == 1.75   # by_child 不算，老列 99 不算

async def test_tier2_no_view_no_children_copies_column(conn):
    await _insert(conn, 90002, cost_cents=3.5)
    await conn.execute(text(_body()))
    assert float(await _own(conn, 90002)) == 3.5

async def test_tier3_no_view_with_children_stays_null(conn):
    await _insert(conn, 90003, cost_cents=7)
    await _insert(conn, 90004, cost_cents=2, parent=90003, root=90003)
    await conn.execute(text(_body()))
    assert await _own(conn, 90003) is None          # 拆不开就不猜
    assert float(await _own(conn, 90004)) == 2       # 叶子走 tier2

async def test_child_inherits_issue_id_from_root(conn):
    await _insert(conn, 90005, cost_cents=1, issue=5550001)
    await _insert(conn, 90006, cost_cents=1, parent=90005, root=90005)
    await conn.execute(text(_body()))
    got = (await conn.execute(text("SELECT issue_id FROM agent_runs WHERE id=90006"))).scalar_one()
    assert got == 5550001

async def test_idempotent(conn):
    await _insert(conn, 90007, cost='{"own_cents": 1}', cost_cents=50)
    await conn.execute(text(_body())); await conn.execute(text(_body()))
    assert float(await _own(conn, 90007)) == 1
```

⚠️ `agent_runs` 的 NOT NULL 列以实际 schema 为准（`\d agent_runs`）；INSERT 缺列就补默认值。`id` 用 9000x 这类不会撞的值。

- [ ] **Step 4: 本地真库跑红**

Run: `cd backend && INTEGRATION_DATABASE_URL=<本地 drift 库 DSN> uv run pytest tests/db/test_migration_478_own_cost_backfill.py -v`
Expected: 迁移文件不存在时 FAIL（`FileNotFoundError`）；写完迁移后 5 passed。本机无库则 skip，交给 CI step。

- [ ] **Step 5: 接 schema-drift**

在 `.github/workflows/schema-drift.yml`：① `pull_request.paths` 加 `'backend/tests/db/test_migration_478_own_cost_backfill.py'`；② 照 `Assert the search_docs body backfill against a real server` 那个 step 的形状（`working-directory: backend`、`INTEGRATION_DATABASE_URL`、`bash "$GITHUB_WORKSPACE/.github/scripts/pytest-no-full-skip.sh" tests/db/test_migration_478_own_cost_backfill.py -v`）加一个 step，注释写明「三档回填 + 子 run issue_id 补齐只有真库能证」；③ 按文件里数词门禁注释给的命令重数，把 `thirty-three` 全部改成 `thirty-four`（grep 确认无遗漏）。本地 `actionlint` 过。

- [ ] **Step 6: ORM 两向门禁**

Run: `cd backend && uv run pytest tests/db/test_schema_drift.py tests/db/test_orm_index_comparator.py -q`
Expected: pass（新列只在 ORM + 迁移各一份）。

- [ ] **Step 7: Commit + PR-A**

```bash
git add supabase/migrations/478_agent_runs_own_cost_cents.sql backend/app/models/agents.py backend/tests/db/test_migration_478_own_cost_backfill.py .github/workflows/schema-drift.yml
git commit -m "feat(billing): agent_runs.own_cost_cents 列 + 三档回填（mig 478，3d 第 0 票 PR-A）"
```

PR 标题同 commit。**合并后验收（控制方做）**：`run-migration` success；生产 `SELECT count(*) FILTER (WHERE own_cost_cents IS NULL), count(*) FROM agent_runs` 与 NOTICE 一致（预期 NULL = 0）；`SELECT count(*) FROM agent_runs c JOIN agent_runs r ON r.id=c.root_run_id WHERE c.issue_id IS NULL AND r.issue_id IS NOT NULL` = 0。

---

### Task 2: 写方 —— 镜像语句与终态 UPDATE 写 `own_cost_cents`（PR-B 起点）

**Files:**
- Modify: `backend/app/services/ai/runner/run_recorder.py`（`RunEventWriter.mirror_stmt` 约 :1700；`RunRecorder._finish` 约 :870 与 :941）
- Test: `backend/tests/runner/test_own_cost_column_writers.py`

**Interfaces:**
- Consumes: `tree_charge.spend_of_run(cost) -> RunSpend`（`.total` 即自身花费）；Task 1 的 ORM 列。
- Produces: 每次镜像与终态后 `agent_runs.own_cost_cents == spend_of_run(cost).total`。

- [ ] **Step 1: 写失败测试**

`mirror_stmt()` 是纯 builder，可直接编译断言；`_finish` 的 `updates` 字典按现有 `tests/runner/test_persist_views_contract.py` 里拿 `_finish` 语句/桩 session 的办法断言。

```python
from sqlalchemy.dialects import postgresql

def test_mirror_stmt_writes_own_cost_from_spend_of_run(writer_with_views):
    # fixture: RunEventWriter，views["cost"] = {"own_cents": 1.2, "media_cents": 0.3,
    #          "by_child": {"c": 5.0}, "spent_cents": 6.5}
    stmt = writer_with_views.mirror_stmt()
    compiled = stmt.compile(dialect=postgresql.dialect())
    assert "own_cost_cents" in str(compiled)
    assert compiled.params[[k for k in compiled.params if k.startswith("own_cost_cents")][0]] == 1.5
    # by_child 不进去：1.2 + 0.3，不是 6.5

def test_finish_writes_own_cost_not_tree_total(finish_updates):
    # fixture 跑到 _finish 的 updates 组装，own_cents=2.0, media=0.5, children=4.0
    assert finish_updates["own_cost_cents"] == 2.5
    assert finish_updates["cost_cents"] == 6.5   # 老列语义不变

def test_finish_writes_own_cost_even_when_own_cents_unknown(finish_updates_no_rate):
    # own_cents None（费率未知）、media 0.7 → own_cost_cents = 0.7，不是缺席
    assert finish_updates_no_rate["own_cost_cents"] == 0.7
```

- [ ] **Step 2: 跑红**

Run: `cd backend && uv run pytest tests/runner/test_own_cost_column_writers.py -v`
Expected: FAIL —— `own_cost_cents` 不在语句 / updates 里。

- [ ] **Step 3: 实现**

`mirror_stmt()` 的 return 改为：

```python
        from app.services.ai.billing.tree_charge import spend_of_run

        return (
            update(AgentRuns)
            .where(AgentRuns.id == self.run_id)
            .values(
                metadata_json=expr,
                # 自身花费列，跟每次镜像一起落：running 时就是实时的，崩溃写方不碰它
                # 也就停在最后一次镜像的值 —— 与 tree_charge 收口读到的是同一个数。
                own_cost_cents=spend_of_run(self.views.get("cost")).total,
            )
        )
```

`_finish` 里在 `if cost_cents is not None: updates["cost_cents"] = cost_cents` 之后加：

```python
        # 自身花费列（mig 478）。无条件写：own_spend.total 在费率未知时也有 media 那一道。
        # 最后一次镜像失败（persist_views 返回 False）时，这里仍把终态内存里的真值落库。
        updates["own_cost_cents"] = own_spend.total
```

（`own_spend` 已在 :868 算好。确认它在 `updates` 组装处的作用域内；不在就把计算上移，不要复制公式。）

- [ ] **Step 4: 跑绿 + 既有 runner 用例**

Run: `cd backend && uv run pytest tests/runner -q`
Expected: 全绿（`mirror_stmt` 的 pg 编译/往返用例若断言了 values 键集合，更新它们）。

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/ai/runner/run_recorder.py backend/tests/runner/test_own_cost_column_writers.py
git commit -m "feat(billing): 镜像语句与终态 UPDATE 写 own_cost_cents"
```

---

### Task 3: 议题一族 —— 预算门禁、`issue_totals`、rollup 改读自身列并去掉 root 过滤

**Files:**
- Modify: `backend/app/repositories/agent_runs_repository.py::spent_cents_for_issue`（约 :823）
- Modify: `backend/app/repositories/usage_repository.py::issue_totals`（约 :222）
- Modify: `backend/app/services/issues/issue_rollup.py::_run_cents` / 汇总（约 :36, :103）
- Test: `backend/tests/repositories/test_issue_spend_own_cost.py`（单测，桩 session 断言编译后的 SQL）；真库用例见 Task 7。

**Interfaces:**
- Consumes: `AgentRuns.own_cost_cents`。
- Produces: 新 repo 方法 `AgentRunsRepository.own_cost_cents_for_issue_runs(issue_id: int) -> float`（全部行求和，供 rollup 用，与门禁同一条表达式）。

- [ ] **Step 1: 失败测试**

```python
from sqlalchemy.dialects import postgresql

def _sql(stmt): return str(stmt.compile(dialect=postgresql.dialect()))

def test_budget_gate_sums_own_cost_over_all_rows(captured_stmt):
    # fixture 让 read_scope 的 session.execute 捕获语句并返回 0
    s = _sql(captured_stmt(lambda repo: repo.spent_cents_for_issue(issue_id=1)))
    assert "own_cost_cents" in s
    assert "cost_cents" not in s.replace("own_cost_cents", "")
    assert "parent_run_id IS NULL" not in s          # Delegate 子 run 必须进门禁

def test_issue_totals_cost_no_longer_root_filtered(captured_stmt_usage):
    s = _sql(captured_stmt_usage())
    assert "sum(coalesce(agent_runs.own_cost_cents" in s.lower()
    # run_count 仍然只数 root
    assert "count(*) FILTER (WHERE agent_runs.parent_run_id IS NULL)" in s

def test_rollup_uses_repo_sum_not_row_cost(monkeypatch):
    # compute_rollup 的 spent 来自 own_cost_cents_for_issue_runs，而不是逐 root 行 cost_cents
    ...  # 桩 own_cost_cents_for_issue_runs 返回 4.25，传入 runs 的 cost_cents 全是 99，断言 spent == 4.25
```

- [ ] **Step 2: 跑红**

Run: `cd backend && uv run pytest tests/repositories/test_issue_spend_own_cost.py -v` → FAIL。

- [ ] **Step 3: 实现**

`spent_cents_for_issue`：

```python
        stmt = select(
            func.coalesce(func.sum(func.coalesce(AgentRuns.own_cost_cents, 0)), 0)
        ).where(or_(*keys))
        # 不再 parent_run_id IS NULL：own_cost_cents 每行只记自身，全行求和才是这个议题
        # 真花的钱。Delegate 出去的子 run 带着 issue_id 落库，此前正是被 root 过滤挡掉、
        # 让预算对委派花费无感（3d 第 0 票）。
```

docstring 改成如实口径。`issue_totals` 的 `cost_cents` 标签改 `func.coalesce(func.sum(func.coalesce(AgentRuns.own_cost_cents, 0)), 0)`，**不带** `.filter(root_only)`；`run_count` 保持 `FILTER (root_only)`；docstring 里「子 run 的花费已经滚进父行」那一段改写为新口径。

新增 `own_cost_cents_for_issue_runs(issue_id)`：与门禁同一条 select（抽成模块级私有 helper `_own_cost_sum_stmt(*where)` 两处共用），失败 raise（同门禁）。

`issue_rollup`：`spent` 不再 `Σ _run_cents(run)`，改为 `await repo.own_cost_cents_for_issue_runs(issue_id)`（`compute_rollup` 已是 async 且拿得到 issue id；若签名只收 runs，加关键字参数 `spent_cents: float`，由调用方 `issue_service` 传入——选后者时把调用方一并改）。`_run_cents` 若无其他读方则删除。

- [ ] **Step 4: 跑绿**

Run: `cd backend && uv run pytest tests/repositories/test_issue_spend_own_cost.py tests/services/issues tests/repositories -q` → 全绿。

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(billing): 议题预算门禁 / usage / rollup 改读 own_cost_cents，委派子 run 进预算"
```

---

### Task 4: 按 agent 与按模型的聚合 —— 去掉双计

**Files:**
- Modify: `backend/app/repositories/agent_runs_repository.py`：`monthly_usage_by_agent`（约 :1291）、`usage_by_model_daily`（约 :482）、`usage_by_key_daily`（约 :540）、裸 SQL `SUM(cost_cents)` 的 run 分组（约 :225；它是 `scoped_sql` 存量，改字段名即可，不新增裸 SQL）
- Modify: `backend/app/workflows/agent_cost_anomaly.py`（约 :61）
- Modify: `backend/app/api/ai_library_router.py`：`admin_telemetry`（约 :3439–3490）、`get_agents_stats` 的 `cost_cents_7d`（约 :586）
- Test: `backend/tests/repositories/test_agent_aggregates_own_cost.py`

**Interfaces:** 无新接口；返回字段名不变（前端无感）。

- [ ] **Step 1: 失败测试**

对上面每个查询各一条：捕获编译 SQL，断言含 `own_cost_cents`、剥掉 `own_cost_cents` 后不含 `cost_cents`。异常告警那条另断言 CTE 里 `sum(coalesce(agent_runs.own_cost_cents, 0))`。

- [ ] **Step 2: 跑红** → FAIL。

- [ ] **Step 3: 实现**

每处 `func.sum(AgentRuns.cost_cents)` → `func.sum(func.coalesce(AgentRuns.own_cost_cents, 0))`；裸 SQL 里 `SUM(cost_cents)` → `SUM(COALESCE(own_cost_cents, 0))`。`monthly_usage_by_agent` 的 docstring 补一句：「按 agent 求和的是**这个 agent 自己**烧的钱；父子不同 agent 时各记各的——这正是按 agent 限额想要的。此前读 cost_cents 把子 run 算了两遍」。

- [ ] **Step 4: 跑绿**

Run: `cd backend && uv run pytest tests/repositories tests/workflows/test_agent_cost_anomaly*.py tests/api -q -k "usage or telemetry or anomaly or agents_stats"` → 绿。

- [ ] **Step 5: Commit**

```bash
git commit -am "fix(billing): agent 月度预算 / 异常告警 / 用量趋势改读 own_cost_cents，去掉子 run 双计"
```

---

### Task 5: 按树的读面 —— 聊天气泡消耗行、`done` 状态帧、效率页

**Files:**
- Modify: `backend/app/repositories/agent_runs_repository.py`：新增 `tree_cost_cents(root_ids: List[int]) -> Dict[str, float]`；`efficiency_groups`（约 :1091）
- Modify: `backend/app/api/ai_library_router.py::get_run_costs`（约 :2424；docstring :2368）
- Modify: `backend/app/services/issues/issue_chat_stream.py::_cost_cents_for`（约 :75）
- Test: `backend/tests/repositories/test_tree_cost_cents.py`、更新 `tests/api` 里 `runs/costs` 与 `issue_chat_stream` 的既有用例

**Interfaces:**
- Produces: `tree_cost_cents(root_ids) -> {str(root): float}`——每个问到的 root 至少映射到自己（读到 0 行 → 0.0），失败 raise（同 `run_ids_in_trees`）。

- [ ] **Step 1: 失败测试**

```python
def test_tree_cost_cents_groups_by_coalesce_root_id(captured_stmt):
    s = _sql(captured_stmt(lambda repo: repo.tree_cost_cents([1, 2])))
    assert "coalesce(agent_runs.root_run_id, agent_runs.id)" in s.lower()
    assert "own_cost_cents" in s
    assert "GROUP BY" in s

async def test_tree_cost_cents_every_root_present(repo_with_rows):
    # rows: root 1 own 1.0, child(root=1) own 0.5, child(root=1) own 0.25; root 2 无行
    assert await repo_with_rows.tree_cost_cents([1, 2]) == {"1": 1.75, "2": 0.0}

def test_efficiency_groups_cost_is_tree_sum_joined_to_root_group(captured_stmt):
    s = _sql(captured_stmt(lambda repo: repo.efficiency_groups(group_by="agent", frm=..., to=...)))
    assert "own_cost_cents" in s and "cost_cents).filter" not in s
    # 花费来自按树预聚合的子查询，再按 root 行的 key 分组
    assert "coalesce(agent_runs.root_run_id, agent_runs.id)" in s.lower()
```

`get_run_costs` 用例：`cost_cents` 字段来自 `tree_cost_cents`（root 1.75 而非行上 cost_cents 99）。`issue_chat_stream` 用例同理。

- [ ] **Step 2: 跑红** → FAIL。

- [ ] **Step 3: 实现**

```python
    async def tree_cost_cents(self, root_ids: List[int]) -> Dict[str, float]:
        """每个 root → 整棵树的真实花费（Σ own_cost_cents），字符串键。

        树键 COALESCE(root_run_id, id)：root 行自己的 root_run_id 恒为 NULL。每个问到的
        root 至少映射到它自己（0.0）——读空不等于免费。与 tree_charge.bucket_tree 的
        tree_total 是同一个数（不变量用例钉住）。读失败 raise。
        """
        roots = [int(r) for r in root_ids if r is not None]
        if not roots:
            return {}
        tree_key = func.coalesce(AgentRuns.root_run_id, AgentRuns.id)
        stmt = (
            select(tree_key.label("root"), func.sum(func.coalesce(AgentRuns.own_cost_cents, 0)).label("cents"))
            .where(tree_key.in_(roots))
            .group_by(tree_key)
        )
        out = {str(r): 0.0 for r in roots}
        try:
            async with read_scope() as session:
                for root, cents in (await session.execute(stmt)).all():
                    out[str(root)] = round(float(cents or 0), 4)
        except Exception as e:
            logger.error(f"[agent_runs] tree_cost_cents failed: {e}")
            raise
        return out
```

`efficiency_groups`：把 `func.sum(AgentRuns.cost_cents).filter(root_only)` 换成按树预聚合再 join：

```python
        tree_key = func.coalesce(AgentRuns.root_run_id, AgentRuns.id)
        tree_cost = (
            select(tree_key.label("root"), func.sum(func.coalesce(AgentRuns.own_cost_cents, 0)).label("cents"))
            .where(AgentRuns.created_at >= frm, AgentRuns.created_at < to)   # 与 scope 同窗；scope 里的 user/team/project 过滤也要带上
            .group_by(tree_key)
            .subquery("tree_cost")
        )
        # 主查询保持按 root 行分组（run_count / failed / 时长都是回合粒度），花费改从
        # tree_cost 左联：子 run 的钱归到它 root 所在的组。
        rows_stmt = (
            select(key_col.label("key"), ..., func.coalesce(func.sum(tree_cost.c.cents).filter(root_only), 0).label("cost_cents"))
            .select_from(AgentRuns)
            .outerjoin(tree_cost, tree_cost.c.root == AgentRuns.id)
            .where(*scope)
            .group_by(key_col)
        )
```

⚠️ 子查询的 where 必须与 `scope` 一致（把 scope 列表同时传给两边），否则跨窗口的子 run 会被算进/漏掉。docstring :1021–1044 那段「root 行的 cost_cents 已是树总额」改写。

`get_run_costs`：`allowed` 之后 `tree = await repo.tree_cost_cents([r["id"] for r in allowed])`，`"cost_cents": tree.get(str(r["id"]))`；docstring :2368 改成「树总额来自 own_cost_cents 按树求和」。失败与 `charged` 同样 503。
`issue_chat_stream._cost_cents_for(rid)` → `(await repo.tree_cost_cents([rid])).get(str(rid))`。

- [ ] **Step 4: 跑绿**

Run: `cd backend && uv run pytest tests/repositories/test_tree_cost_cents.py tests/api tests/services/issues -q` → 绿。

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(billing): 消耗行 / done 帧 / 效率页按树读 own_cost_cents"
```

---

### Task 6: 守卫 —— 老列不进聚合、公式只有一份；顺带修崩溃子 run 报 0

**Files:**
- Create: `backend/tests/guards/test_cost_cents_never_aggregated.py`（若 `tests/guards` 不存在，放 `tests/services/billing/`）
- Modify: `backend/app/services/workforce/agent_worker.py::_run_subagent_task`（约 :488 `"cost_cents": envelope.get("cost_cents") or 0`）
- Test: `backend/tests/workforce/test_subagent_crash_reports_real_cost.py`（放到该模块既有测试目录）

- [ ] **Step 1: 守卫测试（直接写成会绿的断言，但先用一处故意留下的 `func.sum(AgentRuns.cost_cents)` 验证它会红，再删掉那处）**

```python
import pathlib, re
APP = pathlib.Path(__file__).resolve().parents[2] / "app"

def test_cost_cents_is_never_summed_in_app():
    hits = []
    for p in APP.rglob("*.py"):
        src = p.read_text()
        for pat in (r"func\.sum\(\s*AgentRuns\.cost_cents", r"SUM\(\s*(?:a\.|agent_runs\.)?cost_cents\b", r"sum\(\s*func\.coalesce\(\s*AgentRuns\.cost_cents"):
            if re.search(pat, src, re.I):
                hits.append(str(p.relative_to(APP)))
    assert not hits, f"cost_cents 是展示列，禁止聚合；改读 own_cost_cents：{hits}"

def test_own_media_sum_has_exactly_one_home():
    # own_cents 与 media_cents 相加只许在 tree_charge.spend_of_run 里出现
    hits = []
    for p in APP.rglob("*.py"):
        if p.name == "tree_charge.py": continue
        src = p.read_text()
        if re.search(r"own_cents[^\n]{0,40}\+[^\n]{0,40}media_cents|media_cents[^\n]{0,40}\+[^\n]{0,40}own_cents", src):
            hits.append(str(p.relative_to(APP)))
    assert not hits, f"自身花费公式只许在 spend_of_run：{hits}"
```

⚠️ 第二条会命中 `run_recorder._finish` 现有的 `own_media_cents = round((own_cents or 0.0) + media_cents, 4)`（:861）。它与 `own_spend.total` 是同一个数：把它改成 `own_media_cents = own_spend.total`（`own_spend` 的计算上移到它前面），删掉那份加法。`cost_cents = own + children + media` 那行是老列公式，不含 `own_cents ... media_cents` 相邻模式即可（正则窗口 40 字符内没有 `children_cents` 隔开的话会命中——实测后调正则，别调代码去迎合正则）。

- [ ] **Step 2: 崩溃子 run 失败测试**

```python
async def test_crashed_subagent_reports_child_row_cost_not_zero(worker_env):
    # run_background_task 抛异常；子 run 行（sub_run_id 已知）own_cost_cents = 0.42
    content = await worker_env.run_subagent_and_capture_done()
    assert content["cost_cents"] == 0.42
```

- [ ] **Step 3: 跑红** → FAIL（报 0）。

- [ ] **Step 4: 实现**

崩溃分支的 envelope 没有 `cost_cents`：在 `content` 组装前，若 `envelope.get("cost_cents") is None` 且能拿到子 run id（payload 里的 `sub_run_id` / 已建的 recorder id），用 `get_agent_runs_repository().cost_rows_for_ids([child_id])` 读行上 `cost_cents`（这里是**老列**语义：子树到货总额，与 `subagent_done.cost_cents` 一直的口径一致），读不到才回落 0 并 `logger.warning`。注释写明：这只影响父行的展示列 `cost_cents`，聚合读面早已不读它。

- [ ] **Step 5: 跑绿 + 全量**

Run: `cd backend && uv run pytest tests/guards tests/workforce tests/runner -q` 然后全量 `uv run pytest -q`（本机 URLBlocked 12 条假红忽略）。

- [ ] **Step 6: Commit**

```bash
git commit -am "test(billing): 守卫 cost_cents 不进聚合、自身花费公式唯一；修崩溃子 run 向父级报 0"
```

---

### Task 7: 真库不变量用例 + schema-drift + 交付 PR-B

**Files:**
- Create: `backend/tests/db/test_own_cost_tree_invariants_integration.py`
- Modify: `.github/workflows/schema-drift.yml`（加 step、paths、数词 `thirty-four` → `thirty-five`）

- [ ] **Step 1: 写真库用例**

插一棵 4 层树（root A；A 的进程内子 B；A 的后台子 C；C 的 Delegate 子 D；D 的 Delegate 子 E），每行 `metadata_json.cost` 与 `own_cost_cents` 按定义一致（own+media），`cost_cents` 故意填成「自身+已报到后代」的脏值；A 与 B 同 issue，C/D/E 也带同 issue；agent_id：A、B 是 agent X，C、D、E 是 agent Y。

```python
async def test_tree_issue_agent_sums_agree(conn_with_tree):
    repo = ...  # 用 conn 绑定的 session 构造，或 monkeypatch read_scope 指向本连接
    tree = await repo.tree_cost_cents([A])
    issue = await repo.spent_cents_for_issue(issue_id=ISSUE)
    by_agent = await repo.monthly_usage_by_agent([X, Y], since=EPOCH)
    expected = sum(own for own in OWNS.values())          # 手算：五行自身之和
    assert tree[str(A)] == expected == issue
    assert by_agent[str(X)]["cost_cents"] + by_agent[str(Y)]["cost_cents"] == int(expected)  # 该方法回 int

async def test_tree_total_equals_bucket_tree(conn_with_tree):
    from app.services.ai.billing.tree_charge import bucket_tree
    rows = [cost_view for each of the five rows]
    assert (await repo.tree_cost_cents([A]))[str(A)] == bucket_tree(rows).tree_total

async def test_delegate_child_counts_toward_budget(conn_with_tree):
    # 只删 D、E 两行再算门禁，差值 == own(D)+own(E)
    ...
```

- [ ] **Step 2: 本地真库跑**（无库则 skip，交 CI）。

- [ ] **Step 3: 接 schema-drift**：新 step（形状同 Task 1 Step 5）、paths 加该文件、数词 `thirty-four` → `thirty-five`，`actionlint` 过。

- [ ] **Step 4: 全量 + 格式**

`cd backend && uv run pytest -q`；`uv run ruff check $(git diff --name-only origin/master...HEAD -- 'backend/*.py' | sed 's#^backend/##')`；同一清单 `uv run black --check`。

- [ ] **Step 5: Commit + PR-B**

```bash
git commit -am "test(billing): own_cost_cents 按树/议题/agent 三种分组不重不漏（真库）"
```

PR 标题：`feat(billing): 花费聚合改读 own_cost_cents —— 委派子 run 进预算、去掉月度双计（3d 第 0 票 PR-B）`。正文写明**依赖 PR-A 已上线**，列出 spec §4 的用户可见变化。

**合并后真栈验收（控制方做）**：`deploy-gpu` success + `readyz`；容器内 `python -c "from app.repositories.agent_runs_repository import get_agent_runs_repository as g; import asyncio; print(asyncio.run(g().tree_cost_cents([<含 Delegate 子 run 的 root>])))"` 与 `tree_charge.bucket_tree` 对同一棵树给同一个数；`spent_cents_for_issue(issue_id=<该 root 的议题>)` ≥ 该树总额；跑一个新回合后核 `agent_runs.own_cost_cents` 在 running 期间非 NULL 且随事件增长；前端 `npm run e2e:prod`。

---

## 自审

- **Spec 覆盖**：§2.1 列 → T1；§2.2 两个写方 + 公式唯一 → T2/T6；§2.3 六个 root-only 读面（门禁 / issue_totals / rollup → T3；效率页 / 消耗行 / done 帧 → T5）与六个双计读面（月度 / 异常 / 模型日趋势 / key 日趋势 / telemetry / 7d 卡片 / 详情分组 → T4）；§2.4 迁移三档 + issue_id 补齐 + 部署顺序 → T1 与合并顺序表；§2.5 崩溃子 run → T6；§3 六条不变量 → 1/2/3 在 T7，4 在 T2（镜像语句写列即崩溃行保值，T7 可加一条「删掉终态 UPDATE 后列仍有最后镜像值」的断言），5/6 在 T6；§6 验收 → T1/T7 合并后清单。
- **占位扫描**：T3 Step 1 第三条与 T7 用例体有 `...`，是「按同文件其余用例的 fixture 形状补全」，实施者有 T1/T5 的完整样例可照抄；T5 `efficiency_groups` 的 `...` 是原有列清单原样保留。无 TBD。
- **类型一致**：`tree_cost_cents(root_ids: List[int]) -> Dict[str, float]` 在 T5 定义、T7 消费；`own_cost_cents_for_issue_runs(issue_id: int) -> float` 在 T3 定义与消费；`spend_of_run(...).total` 全程同名。
