# harness 三期 3c「检索 / @引用 / 效率账 / 回合可读性」Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 agent 的工作可以被找到（议题 / run / 产出三组统一检索、产出跨议题 @ 引用与反查）、被算清（三套账对齐后的效率指标）、被读懂（回合里的阶段性叙述与每条回复的消耗行）。

**Architecture:** 一份迁移（mig 472）建 `search_docs` 投影表与 `output_citations` 镜像表、给 `agent_runs` / `ai_usage_hourly` 补计数列；六条对账前置票先把小时表、议题合计、积分、收件箱、issues 检索、run 归属修成同一口径；指标在 `RunRecorder._finish` 由并列的计数 fold 数好落 run 行，读面只扫 `agent_runs`；检索在三个咽喉点（登记口、run 终态、消息落库）写投影与镜像，`/search` 一个端点服务 ⌘K 与 @ 页签；回合可读性靠 `assistant{partial}` 事件进 transcript 后折叠成正文节点。

**Tech Stack:** FastAPI + SQLAlchemy async（ORM，禁裸 SQL）+ Postgres（pg_trgm）+ DBOS；React + Vite + vitest + Playwright（e2e-prod）；pytest + 真 PG 集成用例接 `schema-drift.yml`。

**Spec:** `docs/superpowers/specs/2026-09-15-harness-p4-phase3c-search-efficiency-design.md`（master eabbdde6）。共享接口契约与四份侦察在本地 SDD 工作区 `.superpowers/sdd/2026-09-15-harness-p4-phase3c/`（`plan-contract.md`、`recon-3c-*.md`，不进仓）。

## Global Constraints

- mig 编号 **472**（取号时重扫 `supabase/migrations/`，471 是当前最后一个）；迁移先行，代码侧对新列缺席容错（`SQLSTATE 42703` → 503 typed，不 `return []`）。
- 新 SQL 一律 ORM；`text()` 裸 SQL 只许经 `scoped_sql()` 且显式 `system=True, reason=...`（本计划全部 ORM，无裸 SQL）。
- ORM 索引/约束镜像按 (名字, 轴) 与迁移一致（C1 门禁零容忍）；真 PG 集成用例接 `.github/workflows/schema-drift.yml`，**step 级**加一行、别粘连 `name` 行。
- 每 Task 独立 worktree（`git -C /Volumes/program/project-code/repos/nous-app worktree add -b <branch> .worktrees/<name> origin/master`）+ 独立 PR；所有 git 命令 `git -C <绝对路径>`；主检出只读；同一轮最多一条依赖 cwd 的 Bash。
- TDD：先红后绿，每 Task 末尾突变记录（改一处实现让测试转红再改回）。
- 边界 mock 用真实 wire 形状（Snowflake id 在 `scenes`/`shots` 是 number，`canvases`/`outputs` 是 string）；错误码走 `details.code`（`ErrorResponse` 外壳）。
- UI 文案英文、语义色 token（ok / warn / danger / info / agent）；i18n key camelCase。
- `stream_turn` 缓冲回退分支（adapter 无 `stream`）是生产唯一路径，任何 runner 事件改动必须有该分支的用例。
- ILIKE 模式谁拼谁转义：`escape_like` + SQL `ESCAPE '\'`。
- 积分扣费：修好并真扣、只向前、按 run 自身花费；急停 `AGENT_POINTS_CHARGE_ENABLED`（`config.yml` 大写键，默认 true）。
- 后端测试 `cd <worktree>/backend && uv run pytest <path> -q`；lint `uv run ruff check . && uv run black --check . && uv run isort --check-only .`；前端 `cd <worktree>/frontend && npx vitest run <path>` + `npm run typecheck`。
- 派发词 lint 清单要写全 ruff + black + isort；实施者要跑根级守卫 `backend/tests/test_*.py`（3b 教训）。

## 契约偏离汇总（四段作者 grep 核实，拼装时已对齐；实施者以此为准）

| # | 契约原文 | 实况 | 计划口径 |
|---|---|---|---|
| 1 | 积分流水 `point_consumption_log` | 不存在。真账本 `public.point_transactions`（ORM `PointTransactions`，`app/models/billing.py:167`）：`type='consume'`、`reference_type=<action_type>`、`reference_id=str(run_id)`、`amount` 为负 | Task 4 / 9 / 10 / 21 / 22 全按它；`charged_points = -SUM(amount)`；读法 ORM，不用 `scoped_sql` |
| 2 | `folds/efficiency.py` 用 `@register` | `run_projection.register` 对重复事件类型 raise，四族各有主 fold | Task 8 新增并列计数道 `register_counter`，`apply` 跑两道 |
| 3 | 前端类型 `IssueRollup` | 真名 `IssueProgress`（`issuesService.ts:394`） | Task 9 / 21 |
| 4 | A6 两个 `RunRecorder(...)` 调用点都兜底 | `conversation_agent_turn.py` Gate 0 已保证 `team_id` 非空，兜底是死分支 | Task 6 只改 `ai_library_chat_service.py:1440`，另一侧加回归 fixture |
| 5 | 守卫测试 `test_no_repository_write_path_touches_scope_columns` | 真名 `test_no_write_path_anywhere_touches_scope_columns` | Task 6 |
| 6 | BYOK run 跳过扣分 | `agent_runs` / `generated_media` 无 run 级 BYOK 标记 | Task 4 保留 `byo_key=False`，写成 Stated Limitation + 急停 + 完成账对照 SQL。**本计划唯一刻意未闭合的口子** |
| 7 | `search_docs.entity_id` 拼法未定 | — | run 行 `str(agent_runs.id)`；output 行 `f"{kind}:{ref_id}:{version}"`（Task 1 回填与 Task 13 upsert 同串） |
| 8 | 路由在 `main.py` 注册 | 注册点是 `app/api/__init__.py`（:204-208） | Task 14 |
| 9 | `record_output_citations(session, ...)` 同事务 | `ConversationRepository.send_message` 自开自提交，拿不到 session | `session` 允许 `None`，镜像在消息落库之后，失败记 ERROR 不回滚消息（Task 16） |
| 10 | run 行标题用 `map_identifiers` | 它只给 identifier | Task 13 新增 `IssueRepository.identifier_and_title`；深链只到议题页（`search_docs` 无 `step` 列，不编造 `?step=`） |
| 11 | 阶段性叙述三条路径三处 diff | 缓冲回退（adapter 无 `stream`）已委托 `run_turn`，继承第一处 | Task 19 两处 diff，第三条靠「无 `stream` 属性」用例钉住 |
| 12 | `/runs/costs` 取数方法 | Part B 拆成 `AgentRunsRepository.cost_rows_for_ids` + `PointsRepository.charged_points_for_references` | 路由与 done 帧（Task 21）都组合这两个，不另开读路径 |
| 13 | `ai_usage_logs` 按 `run_id` 配对未扣分行 | 该表无 `run_id` 列 | 存量 = A3 上线时刻之前的全部 `cost_points > 0` 行，交叉确认 `point_transactions` 无 `agent_run` 行 |
| 14 | 议题线程消耗行 hover 显示 tokens | rollup `runs[]` 无 token 列 | 议题侧 hover 只给 ¢ 与积分，tokens 记票（Task 23） |

## 合并顺序（跨段）

1. **Task 1**（mig 472 + ORM + 真 PG）先行：run-migration 与 deploy-gpu 两条链成功、`\d search_docs`。
2. **Task 8**（tool_events + efficiency fold + `_finish` 五列）紧随其后。
3. **Task 2–6**（对账 A1/A2/A3/A4/A6）与 **Task 13**（投影写方）并行；⚠️ Task 2 与 Task 8 都改 `RunRecorder._finish`：后合的一方 rebase 时把 Task 2 内联的 `views.get("efficiency")` 读法换成 Task 8 的 `_efficiency_counts()`，键名一致（`steps / tool_calls / tool_errors / deliverables / turn_end_reason`）。
4. **Task 9 / 10 / 14 / 16 / 19** 并行（各只依赖 1、8、13）。
5. **Task 11 / 15 / 17 / 20 / 21** 前端并行（各依赖对应后端 Task）。
6. **Task 22** 真栈验收 → **Task 23** 完成账。
7. Part 各自的「合并顺序与部署验证」小节（Task 7 / 12 / 18）是上面的细化，冲突时以本节为准。

---

# Part A（数据与对账）· Task 1–7

> `docs/superpowers/plans/2026-09-15-harness-p4-phase3c-search-efficiency.md` 的第一段。契约见同目录 `plan-contract.md`，名字与形状照抄。Task 编号全局唯一：A 段 1–7，B 段 8–12，C 段 13–18，D 段 19–23。

**Goal：** 把 3c 要读的那些数先变成真的。一份迁移（472）建两张投影/镜像表、给 `agent_runs` 与 `ai_usage_hourly` 补计数列、回填已结束 run 的 `team_id` 与两类 `search_docs` 行；再用五个独立 PR 修掉「三套账互不自洽」的五条前置缺陷（A1 小时表双计、A2 议题合计无 root 过滤、A3 积分从未真扣、A4 收件箱 500、A6 run 的 team_id 大量 NULL）。B/C/D 的读面全建立在这七个 Task 之上。

**Part A 自定义、B/C/D 消费的三条约定**

- `search_docs.entity_id` 口径（契约 §1 给了字段没给 key 拼法）：run 行 = `str(agent_runs.id)`；output 行 = `f"{kind}:{ref_id}:{version}"`。Part C 的 `SearchDocsRepository.upsert` 必须拼同一个串，否则回填行与 upsert 行并存成两份。
- 回填顺序是契约的一部分：`agent_runs.team_id` 的 `UPDATE` 必须排在两段 `search_docs` 的 `INSERT` 之前。顺序反了，投影出的 run 行 `team_id` 停在 NULL，而 `ON CONFLICT DO NOTHING` 不会再修。
- `own_media_cents` = `round((own_cents or 0.0) + media_cents, 4)`，在 `RunRecorder._finish` 算一次，同时喂 A1（小时表）与 A3（积分）。`agent_runs.cost_cents` 仍是树总额，不动。

**与契约的出入（已 grep 核实）**

| # | 契约 | 实况 | 处理 |
|---|---|---|---|
| 1 | 「`point_consumption_log` 按 `reference_id` 查」 | 全仓无此表。积分流水是 `public.point_transactions`（`app/models/billing.py:168`），`PointsRepository.create_transaction` 写，字段 `reference_type`（= `action_type`）/ `reference_id` / `amount`（消费为负）。 | Task 4 按 `point_transactions` 写完成账；Part B 读 `charged_points` 同样要改，Task 7 Step 7 交接。 |
| 2 | 「A6：`conversation_agent_turn.py:459` 从议题兜底」 | 该模块零个 issue 引用；Gate 0（`:311-318`）在 `scope_id` 空时直接 return，到 `RunRecorder(...)` 时 `team_id=int(scope_id)` **必然非空**；`project_id` 由 `resolve_dispatch_scope(conversation_id=…)` 给。 | Task 6 只在 `ai_library_chat_service.py:1440` 做真兜底；那一侧加回归 fixture。加兜底就是永不执行的死分支。 |
| 3 | 「`config.yml` 键 `agent_points_charge_enabled`」 | `backend/config.yml` 全篇大写键，小写读不出来。 | 用 `AGENT_POINTS_CHARGE_ENABLED`。 |
| 4 | 「`test_no_repository_write_path_touches_scope_columns`」 | 真名 `test_no_write_path_anywhere_touches_scope_columns`（`tests/test_agent_run_scope.py:326`）。 | Task 6 用真名。 |
| 5 | 「BYOK 跳过沿用 `byo_key`（按 `byok_key_id` 传 True）」 | `byok_key_id` 只在 `agent_run_events`（正退役）与 `services/ai/cost/snapshot.py`；`agent_runs` / `generated_media` 无 run 级 BYOK 标记，`RunRecorder` 无此 kwarg。 | Task 4 保留 `byo_key=False`，写成 Stated Limitation + 急停 + 完成账对照 SQL。**本段唯一刻意未闭合的口子**，B/C/D 不得默认它已闭合。 |

---

### Task 1: mig 472 四段 + ORM 镜像 + 真 PG 集成用例接门禁

> 迁移号 **472**（`ls supabase/migrations | tail -1` 实测 471 最后；动手时重扫）。ORM 镜像同 PR，消费代码分 PR。顺带修 A4 的一半：`inbox_notifications_kind_check` 补第五值、`idx_inbox_notifications_user_unread` 补 partial 谓词 —— 两者都是模型侧改动，且补谓词必须同批删掉 `test_orm_indexes_integration.py` 的棘轮豁免，拆两个 PR 会让门禁在中间态红。

**Files:**
- Create `supabase/migrations/472_harness_3c_search_efficiency.sql`
- Create `backend/app/models/search.py`
- Create `backend/tests/db/test_migration_472_search_efficiency.py`
- Create `backend/tests/db/test_mig_472_integration.py`
- Modify `backend/app/models/__init__.py`（新块按字母序插在 192 行 `from app.models.reviews import (` 之前）
- Modify `backend/app/models/agents.py`（`AgentRuns` 索引块 189-242、列尾 `attribution` 401-409）
- Modify `backend/app/models/usage.py`（`AiUsageHourly` 列尾 118-126）
- Modify `backend/app/models/reviews.py`（CheckConstraint 245-248、Index 266、类 docstring 238-240）
- Modify `backend/tests/db/test_orm_indexes_integration.py`（`ALLOWED_INDEX_DRIFT` 的 `idx_inbox_notifications_user_unread`）
- Modify `.github/workflows/schema-drift.yml`（`pull_request.paths` 一行 + 末尾一个 step）

**Interfaces:**
- Produces（SQL）`public.search_docs` / `public.output_citations` + 7 索引；`agent_runs.steps/tool_calls/tool_errors/deliverables/turn_end_reason` + `idx_agent_runs_user_created`；`ai_usage_hourly.run_count/failed_runs/tool_calls/tool_errors/deliverables`（`INTEGER NOT NULL DEFAULT 0`）。
- Produces（ORM）`app.models.search.SearchDocs` / `OutputCitations`（经 `app/models/__init__.py` 导出）；`AgentRuns` 五列 + 索引；`AiUsageHourly` 五列；`InboxNotifications` 五值 CHECK + partial 索引声明。
- Consumes 无。

- [ ] **Step 1: 写形状测试（会红）**

```python
# backend/tests/db/test_migration_472_search_efficiency.py
"""472 的形状 + 同 PR 的 ORM 镜像。读法照 test_migration_466_revert_cost.py：
只读可执行正文（剥掉整行注释）—— 注释里复述一句 SQL 不算数。"""
from __future__ import annotations

import pathlib
import re

import pytest
from sqlalchemy import CheckConstraint

from app.models import AgentRuns, AiUsageHourly, InboxNotifications
from app.models.search import OutputCitations, SearchDocs

pytestmark = pytest.mark.unit

MIG = pathlib.Path(__file__).resolve().parents[3] / "supabase/migrations"
_RAW = (MIG / "472_harness_3c_search_efficiency.sql").read_text(encoding="utf-8")
BODY = "\n".join(l for l in _RAW.splitlines() if not l.strip().startswith("--"))
_SQ = re.sub(r"[ \t]+", " ", BODY)

RUN_COLS = ("steps", "tool_calls", "tool_errors", "deliverables", "turn_end_reason")
HOURLY_COLS = ("run_count", "failed_runs", "tool_calls", "tool_errors", "deliverables")


def test_both_tables_and_every_index_are_idempotent():
    assert "CREATE TABLE IF NOT EXISTS public.search_docs" in _SQ
    assert "CREATE TABLE IF NOT EXISTS public.output_citations" in _SQ
    assert "CREATE TABLE " not in _SQ.replace("CREATE TABLE IF NOT EXISTS ", "")
    assert set(re.findall(r"CREATE INDEX IF NOT EXISTS (\w+)", _SQ)) == {
        "idx_search_docs_title_trgm", "idx_search_docs_body_trgm",
        "idx_search_docs_team_updated", "idx_search_docs_issue",
        "idx_search_docs_project", "idx_output_citations_ref",
        "idx_output_citations_issue", "idx_agent_runs_user_created",
    }
    assert "CREATE INDEX " not in _SQ.replace("CREATE INDEX IF NOT EXISTS ", "")
    for col in RUN_COLS + HOURLY_COLS:
        assert f"ADD COLUMN IF NOT EXISTS {col}" in _SQ, col
    assert "ADD COLUMN " not in _SQ.replace("ADD COLUMN IF NOT EXISTS ", "")


def test_the_two_counter_families_have_opposite_nullability():
    """小时表五列是 upsert 的加法累加器（`col + EXCLUDED.col`），可空会把整行算
    成 NULL、一整个小时桶静默变未知；agent_runs 五列必须可空 —— 存量行的
    「那时还没在数」不能被 DEFAULT 0 伪装成「一次都没调」。"""
    for col in HOURLY_COLS:
        assert f"ADD COLUMN IF NOT EXISTS {col} INTEGER NOT NULL DEFAULT 0" in _SQ, col
    for col in RUN_COLS:
        assert f"ADD COLUMN IF NOT EXISTS {col} INTEGER NOT NULL" not in _SQ, col
        assert f"ADD COLUMN IF NOT EXISTS {col} TEXT NOT NULL" not in _SQ, col


def test_the_team_backfill_runs_first_and_only_on_finished_runs():
    assert _SQ.index("UPDATE public.agent_runs a") < _SQ.index(
        "INSERT INTO public.search_docs"
    ), "team_id 必须先补：search_docs 的 DO NOTHING 让 run 行只有一次机会"
    assert "a.ended_at IS NOT NULL" in _SQ and "a.team_id IS NULL" in _SQ
    assert _SQ.count("ON CONFLICT (entity_kind, entity_id) DO NOTHING") == 2


def test_new_tables_are_service_role_only_and_the_file_never_sets_roles():
    """anon key 是烤进浏览器包的公开值；多一条给 anon/authenticated 的策略就是
    一个公开读口（CLAUDE.md 2026-09-11）。"""
    for tbl in ("search_docs", "output_citations"):
        assert f"ALTER TABLE public.{tbl} ENABLE ROW LEVEL SECURITY" in _SQ
        assert f"{tbl}_service_role_all ON public.{tbl}" in _SQ
    assert "TO anon" not in _SQ and "TO authenticated" not in _SQ
    assert not any(re.match(r"(?i)^SET\s+ROLE\b", l.strip()) for l in BODY.splitlines())
    assert BODY.count("BEGIN;") == 1 and BODY.count("COMMIT;") == 1


def test_the_orm_mirrors_both_new_tables_and_every_index():
    """C1 门禁按 (名字, 有序列清单) 对账 —— 名字对、列序错照样是两个索引。"""
    assert (SearchDocs.__tablename__, OutputCitations.__tablename__) == (
        "search_docs", "output_citations"
    )
    d = SearchDocs.__table__.columns
    assert not d["entity_kind"].nullable and not d["entity_id"].nullable
    assert not d["title"].nullable and d["body"].nullable
    c = OutputCitations.__table__.columns
    for name in ("kind", "ref_id", "version", "message_id", "cited_by_user_id"):
        assert not c[name].nullable, name
    got = {
        ix.name: [col.name for col in ix.columns]
        for model in (SearchDocs, OutputCitations)
        for ix in model.__table__.indexes
    }
    assert got == {
        "idx_search_docs_title_trgm": ["title"],
        "idx_search_docs_body_trgm": ["body"],
        "idx_search_docs_team_updated": ["team_id", "updated_at"],
        "idx_search_docs_issue": ["issue_id"],
        "idx_search_docs_project": ["project_id"],
        "idx_output_citations_ref": ["kind", "ref_id", "version"],
        "idx_output_citations_issue": ["issue_id"],
    }
    opts = {ix.name: ix.dialect_options["postgresql"] for ix in SearchDocs.__table__.indexes}
    assert opts["idx_search_docs_body_trgm"]["where"] is not None
    assert opts["idx_search_docs_project"]["where"] is not None
    assert opts["idx_search_docs_issue"].get("where") is None


def test_the_orm_mirrors_the_new_agent_run_and_hourly_columns():
    runs = AgentRuns.__table__.columns
    for col in RUN_COLS:
        assert col in runs and runs[col].nullable, col
    assert "idx_agent_runs_user_created" in {ix.name for ix in AgentRuns.__table__.indexes}
    hourly = AiUsageHourly.__table__.columns
    for col in HOURLY_COLS:
        assert col in hourly and not hourly[col].nullable, col


def test_the_inbox_check_and_partial_index_stop_drifting():
    """A4 的一半：ORM kind CHECK 缺 agent_question（第三份口径）；
    idx_inbox_notifications_user_unread 库里是 WHERE read_at IS NULL 的部分索引，
    ORM 声明成全表 —— 名字里的 `unread` 正是那个丢掉的谓词。"""
    check = next(
        c for c in InboxNotifications.__table__.constraints
        if isinstance(c, CheckConstraint) and c.name == "inbox_notifications_kind_check"
    )
    assert "agent_question" in str(check.sqltext)
    ix = next(
        i for i in InboxNotifications.__table__.indexes
        if i.name == "idx_inbox_notifications_user_unread"
    )
    assert ix.dialect_options["postgresql"]["where"] is not None
```

- [ ] **Step 2: 跑它，确认红**

```bash
cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t1/backend && \
  uv run pytest tests/db/test_migration_472_search_efficiency.py -q
```
预期：collection 阶段 `ModuleNotFoundError: No module named 'app.models.search'`（`FileNotFoundError` 要等 import 过了才轮到）。

- [ ] **Step 3: 写迁移**

```sql
-- supabase/migrations/472_harness_3c_search_efficiency.sql
-- 472: harness 三期 3c —— 检索投影表 + 引用镜像表 + 效率计数列 + 三段回填
-- （spec §2.1 / §2.2 / §3.2 / §5）。四段各自幂等，整份可重跑。
--
-- 为什么投影表而不是加 tsvector：唯一的 tsvector 前例（agent_memory.search_tsv）
-- 是 'english' 配置，中文会被切碎；embedding 链当前 503。所以全走 pg_trgm。而
-- 产出正文散在 script_shots 六列 / script_ops 元素数组 / generated_media.prompt
-- 三处，只有投影表能把它们收成一个可索引的面。
-- 为什么 output_citations 是镜像：引用落在 messages.body jsonb，零索引。画布侧
-- 同类反查（canvas_asset_refs）用的就是物化镜像表，这里沿用同一形状。
BEGIN;

-- ── 1. 检索投影表 ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.search_docs (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  entity_kind TEXT NOT NULL CONSTRAINT search_docs_entity_kind_check CHECK (entity_kind IN ('run','output')),
  entity_id TEXT NOT NULL,
  kind TEXT, ref_id TEXT, version INTEGER,
  team_id BIGINT, project_id BIGINT, issue_id BIGINT, run_id BIGINT,
  owner_user_id UUID, agent_id UUID,
  title TEXT NOT NULL, body TEXT, model TEXT, status TEXT, error_code TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT search_docs_entity_key UNIQUE (entity_kind, entity_id)
);
CREATE INDEX IF NOT EXISTS idx_search_docs_title_trgm ON public.search_docs USING gin (title gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_search_docs_body_trgm ON public.search_docs USING gin (body gin_trgm_ops) WHERE body IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_search_docs_team_updated ON public.search_docs (team_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_search_docs_issue ON public.search_docs (issue_id);
CREATE INDEX IF NOT EXISTS idx_search_docs_project ON public.search_docs (project_id) WHERE project_id IS NOT NULL;

ALTER TABLE public.search_docs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS search_docs_service_role_all ON public.search_docs;
CREATE POLICY search_docs_service_role_all ON public.search_docs
  FOR ALL TO service_role USING (true) WITH CHECK (true);

-- ── 2. 引用镜像表 ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.output_citations (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  kind TEXT NOT NULL, ref_id TEXT NOT NULL, version INTEGER NOT NULL,
  issue_id BIGINT, conversation_id BIGINT, message_id BIGINT NOT NULL,
  cited_by_user_id UUID NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT output_citations_message_ref_key UNIQUE (message_id, kind, ref_id, version)
);
CREATE INDEX IF NOT EXISTS idx_output_citations_ref ON public.output_citations (kind, ref_id, version);
CREATE INDEX IF NOT EXISTS idx_output_citations_issue ON public.output_citations (issue_id);

ALTER TABLE public.output_citations ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS output_citations_service_role_all ON public.output_citations;
CREATE POLICY output_citations_service_role_all ON public.output_citations
  FOR ALL TO service_role USING (true) WITH CHECK (true);

-- ── 3. 效率计数列 ───────────────────────────────────────────────────
-- agent_runs 侧存量行留 NULL（UI 显示 '—'）：0 说「一次工具都没调」，NULL 说
-- 「那时还没在数」—— 两件不同的事，不许合并。
ALTER TABLE public.agent_runs
  ADD COLUMN IF NOT EXISTS steps INTEGER,
  ADD COLUMN IF NOT EXISTS tool_calls INTEGER,
  ADD COLUMN IF NOT EXISTS tool_errors INTEGER,
  ADD COLUMN IF NOT EXISTS deliverables INTEGER,
  ADD COLUMN IF NOT EXISTS turn_end_reason TEXT;
CREATE INDEX IF NOT EXISTS idx_agent_runs_user_created ON public.agent_runs (user_id, created_at DESC);

-- ai_usage_hourly 侧相反：这五列是 upsert 的**加法累加器**（col + EXCLUDED.col），
-- 可空会让一次加法把整行算成 NULL，于是一整个小时桶的计数静默变成未知。
ALTER TABLE public.ai_usage_hourly
  ADD COLUMN IF NOT EXISTS run_count INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS failed_runs INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS tool_calls INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS tool_errors INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS deliverables INTEGER NOT NULL DEFAULT 0;

-- ── 4. 回填 ─────────────────────────────────────────────────────────
-- 顺序是契约的一部分：team_id 必须先补，否则下面投影出来的 run 行带着 NULL
-- team_id 落库，而 ON CONFLICT DO NOTHING 让它们再也修不回来。
UPDATE public.agent_runs a
SET team_id = i.team_id,
    project_id = COALESCE(a.project_id, i.project_id)
FROM public.issues i
WHERE a.issue_id = i.id AND a.team_id IS NULL AND a.ended_at IS NOT NULL;

-- run 行：title = "<issue_key> · <issue_title>"，无议题退到 input_summary 前 80
-- 字，再无则字面量 'run'（title 是 NOT NULL，COALESCE 链必须以常量收尾）。
-- body = output_summary。刻意不用 input_summary：三个写方语义各异，列表投影本来
-- 就把它排除了（agent_runs_repository.py:367-370）。
INSERT INTO public.search_docs (
  entity_kind, entity_id, title, body, team_id, project_id, issue_id, run_id,
  owner_user_id, agent_id, model, status, error_code, created_at, updated_at)
SELECT 'run', r.id::text,
       COALESCE(NULLIF(i.identifier || ' · ' || i.title, ''),
                NULLIF(LEFT(r.input_summary, 80), ''), 'run'),
       r.output_summary, r.team_id, r.project_id, r.issue_id, r.id,
       r.user_id, r.agent_id, r.model, r.status, r.error_code,
       r.created_at, COALESCE(r.ended_at, r.created_at)
FROM public.agent_runs r
LEFT JOIN public.issues i ON i.id = r.issue_id
ON CONFLICT (entity_kind, entity_id) DO NOTHING;

-- output 标题行：存量产出只有 ≤120 字的标签，正文要等 Part C 的三个生产者在登记
-- 时交（向前的，不回补）。body 留 NULL —— NULL 说「没交过正文」，空串会说
-- 「正文是空的」。
INSERT INTO public.search_docs (
  entity_kind, entity_id, kind, ref_id, version, title, team_id, project_id,
  issue_id, run_id, owner_user_id, agent_id, model, created_at, updated_at)
SELECT 'output', d.kind || ':' || d.ref_id || ':' || d.version::text,
       d.kind, d.ref_id, d.version,
       COALESCE(NULLIF(d.title, ''), d.kind || ' ' || d.ref_id),
       r.team_id, r.project_id, r.issue_id, d.run_id,
       COALESCE(r.user_id, d.actor_user_id), r.agent_id, d.model,
       d.created_at, d.created_at
FROM public.run_deliverables d
LEFT JOIN public.agent_runs r ON r.id = d.run_id
ON CONFLICT (entity_kind, entity_id) DO NOTHING;

COMMIT;
NOTIFY pgrst, 'reload schema';
```

- [ ] **Step 4: 写 ORM 新文件**

```python
# backend/app/models/search.py
"""检索投影表与引用镜像表（mig 472，3c §2.1 / §2.2）。

REFERENCE METADATA ONLY —— schema 归 supabase/migrations/*.sql。索引的名字与
**有序**列清单逐条镜像迁移：tests/db/test_orm_indexes_integration.py（C1）按
(名字, 轴) 对账，少一列、换个顺序、漏一个 partial 谓词都是一条新漂移。
"""

from __future__ import annotations

import datetime
import uuid
from typing import Optional

from sqlalchemy import (
    BigInteger, CheckConstraint, DateTime, Identity, Index, Integer,
    PrimaryKeyConstraint, Text, UniqueConstraint, Uuid, text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.orm_base import Base


class SearchDocs(Base):
    """统一检索的投影表：run 行 + output 行。

    entity_id 的拼法是与 Part C 的 SearchDocsRepository.upsert 共享的契约：
    run 行 = str(agent_runs.id)，output 行 = f"{kind}:{ref_id}:{version}"。
    两侧拼法不一致 = 同一个对象在表里并存两行，而 UNIQUE 拦不住。
    """

    __tablename__ = "search_docs"
    __table_args__ = (
        CheckConstraint("entity_kind IN ('run','output')", name="search_docs_entity_kind_check"),
        PrimaryKeyConstraint("id", name="search_docs_pkey"),
        UniqueConstraint("entity_kind", "entity_id", name="search_docs_entity_key"),
        Index("idx_search_docs_title_trgm", "title", postgresql_using="gin",
              postgresql_ops={"title": "gin_trgm_ops"}),
        Index("idx_search_docs_body_trgm", "body", postgresql_using="gin",
              postgresql_ops={"body": "gin_trgm_ops"},
              postgresql_where=text("body IS NOT NULL")),
        Index("idx_search_docs_team_updated", "team_id", "updated_at"),
        Index("idx_search_docs_issue", "issue_id"),
        Index("idx_search_docs_project", "project_id",
              postgresql_where=text("project_id IS NOT NULL")),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    entity_kind: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[Optional[str]] = mapped_column(Text)
    ref_id: Mapped[Optional[str]] = mapped_column(Text)
    version: Mapped[Optional[int]] = mapped_column(Integer)
    team_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    project_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    issue_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    run_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    owner_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    #: NULL = 生产者从未交过正文；'' = 正文确实是空的。不许合并这两种。
    body: Mapped[Optional[str]] = mapped_column(Text)
    model: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[Optional[str]] = mapped_column(Text)
    error_code: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()"))


class OutputCitations(Base):
    """「这一版产出被哪条消息引用过」的物化镜像（messages.body jsonb 零索引）。

    UNIQUE (message_id, kind, ref_id, version)：一条消息里同一版被 @ 两次只算
    一次，重发/编辑重投也不会翻倍。
    """

    __tablename__ = "output_citations"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="output_citations_pkey"),
        UniqueConstraint("message_id", "kind", "ref_id", "version",
                         name="output_citations_message_ref_key"),
        Index("idx_output_citations_ref", "kind", "ref_id", "version"),
        Index("idx_output_citations_issue", "issue_id"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    ref_id: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    issue_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    conversation_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cited_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()"))
```

- [ ] **Step 5: 四处 ORM 改动 + 导出**

`app/models/__init__.py` —— 192 行之前插入：

```python
from app.models.search import (  # noqa: F401
    OutputCitations,
    SearchDocs,
)
```

`app/models/agents.py` —— `AgentRuns.__table_args__` 里 `idx_agent_runs_useful_action_scan` 之后、`{"schema": "public"}` 之前加；列尾 `attribution` 之后加：

```python
        # 472: 用户视角的时间窗（/ai-library/usage/efficiency?scope=user）。
        # 既有 11 个索引全是坐标索引，按 user 扫时间段只能全表。
        Index("idx_agent_runs_user_created", "user_id", "created_at"),
...
    # 472 (3c §3.2): 效率指标，_finish 时由 folds/efficiency.py 一次写入。
    # 存量行 NULL ——「那时还没在数」不是「一次都没调」，UI 显示 '—'。
    steps: Mapped[Optional[int]] = mapped_column(Integer)
    tool_calls: Mapped[Optional[int]] = mapped_column(Integer)
    tool_errors: Mapped[Optional[int]] = mapped_column(Integer)
    deliverables: Mapped[Optional[int]] = mapped_column(Integer)
    #: TurnEndReason 的 10 值之一；interrupted 由事后 sweeper 补写。
    turn_end_reason: Mapped[Optional[str]] = mapped_column(Text)
```

`app/models/usage.py` —— `AiUsageHourly` 的 `event_count` 与 `updated_at` 之间：

```python
    # 472 (3c §3.2): 加法累加器 —— record_usage 的 upsert 走 `col + EXCLUDED.col`，
    # 所以 NOT NULL DEFAULT 0 是必需的，不是洁癖。event_count 不动：它是「有
    # token 的完成」数，语义不同。
    run_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    failed_runs: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    tool_calls: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    tool_errors: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    deliverables: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
```

`app/models/reviews.py` —— kind CHECK（245-248）补第五值、索引（266）补谓词：

```python
        CheckConstraint(
            "kind = ANY (ARRAY['generation_result'::text, 'publish_result'::text, "
            "'autopilot_output'::text, 'workflow_stage'::text, "
            "'agent_question'::text])",
            name="inbox_notifications_kind_check",
        ),
...
        # mig 399 起库里是五值；ORM 停在四值，是 /api/v1/inbox 500 的四份口径
        # 之一。谓词同理：库里这个索引是 WHERE read_at IS NULL —— 名字里的
        # `unread` 就是它。
        Index("idx_inbox_notifications_user_unread", "user_id",
              postgresql_where=text("read_at IS NULL")),
```

同文件类 docstring 里「exactly three producer kinds」改成「five producer kinds（373 建三个，387 加 workflow_stage，399 加 agent_question）」。

`tests/db/test_orm_indexes_integration.py` —— 从 `ALLOWED_INDEX_DRIFT` 整条删掉 `"idx_inbox_notifications_user_unread"`：它只在 `AXIS_PARTIAL` 上漂移，谓词补上后 `test_allowlist_entries_are_still_drifting` 会要求删掉那条轴，而那是它唯一的轴。

- [ ] **Step 6: 再跑，确认绿**

```bash
cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t1/backend && \
  uv run pytest tests/db/test_migration_472_search_efficiency.py -q
```
预期：`7 passed`。

- [ ] **Step 7: 写真 PG 集成用例**

```python
# backend/tests/db/test_mig_472_integration.py
"""472 在真 Postgres 上的样子。上一个文件读的是我们写了什么；这里读的是服务器
接受了什么（CLAUDE.md「读正常 ≠ 服务正常」）。

    INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/drift \
      uv run pytest tests/db/test_mig_472_integration.py -v
"""
from __future__ import annotations

import os
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
pytest.importorskip("asyncpg")
_skip = pytest.mark.skipif(
    not _TEST_DSN, reason="INTEGRATION_DATABASE_URL not set — mig 472 needs a DB.")


@pytest.fixture
async def pg():
    conn = await asyncpg.connect(_TEST_DSN)
    try:
        yield conn
    finally:
        await conn.close()


async def _insert_run_doc(pg, entity_id: str) -> int:
    return await pg.fetchval(
        "INSERT INTO public.search_docs (entity_kind, entity_id, title, body)"
        " VALUES ('run', $1, 'MH-1 · demo', 'a shot about lanterns') RETURNING id",
        entity_id)


@_skip
async def test_the_trigram_indexes_exist_with_their_predicates(pg):
    """pg_trgm 没装时这两个索引压根建不出来 —— 而文本测试会照样绿。"""
    defs = {r["indexname"]: r["indexdef"] for r in await pg.fetch(
        "SELECT indexname, indexdef FROM pg_indexes"
        " WHERE schemaname='public' AND tablename='search_docs'")}
    assert "gin_trgm_ops" in defs["idx_search_docs_title_trgm"]
    assert "gin_trgm_ops" in defs["idx_search_docs_body_trgm"]
    assert "WHERE (body IS NOT NULL)" in defs["idx_search_docs_body_trgm"]
    assert "WHERE (project_id IS NOT NULL)" in defs["idx_search_docs_project"]


@_skip
async def test_one_entity_gets_exactly_one_row_and_a_third_kind_is_refused(pg):
    """投影表的全部幂等性靠 UNIQUE —— upsert 与回填拼同一个 entity_id 时第二次
    必须撞上它。而 'issue' 不进投影表（issues 走自己的三个 trgm 索引），CHECK 是
    那条设计决定在库里的唯一表达。"""
    ent = f"mig472-{uuid.uuid4().hex[:12]}"
    row_id = await _insert_run_doc(pg, ent)
    try:
        with pytest.raises(asyncpg.exceptions.UniqueViolationError) as err:
            await _insert_run_doc(pg, ent)
        assert "search_docs_entity_key" in str(err.value)
        with pytest.raises(asyncpg.exceptions.CheckViolationError) as err2:
            await pg.execute(
                "INSERT INTO public.search_docs (entity_kind, entity_id, title)"
                " VALUES ('issue', $1, 'x')", f"bad-{uuid.uuid4().hex[:8]}")
        assert "search_docs_entity_kind_check" in str(err2.value)
    finally:
        await pg.execute("DELETE FROM public.search_docs WHERE id=$1", row_id)


@_skip
async def test_the_same_version_cannot_be_cited_twice_by_one_message(pg):
    ref, msg, user = f"mig472-{uuid.uuid4().hex[:12]}", 472_000_000_000_001, uuid.uuid4()
    ins = ("INSERT INTO public.output_citations (kind, ref_id, version, message_id,"
           " cited_by_user_id) VALUES ('script_shot', $1, 2, $2, $3)")
    row_id = await pg.fetchval(ins + " RETURNING id", ref, msg, user)
    try:
        with pytest.raises(asyncpg.exceptions.UniqueViolationError) as err:
            await pg.execute(ins, ref, msg, user)
        assert "output_citations_message_ref_key" in str(err.value)
    finally:
        await pg.execute("DELETE FROM public.output_citations WHERE id=$1", row_id)


@_skip
async def test_the_counter_columns_have_the_nullability_each_side_needs(pg):
    """小时表五列必须 NOT NULL DEFAULT 0（累加器可空 → 加法把整行算成 NULL）；
    agent_runs 五列必须可空（「没在数」不能被 0 伪装成「没调过」）。"""
    names = ["run_count", "failed_runs", "tool_calls", "tool_errors", "deliverables"]
    hourly = await pg.fetch(
        "SELECT column_name, is_nullable, column_default FROM information_schema.columns"
        " WHERE table_schema='public' AND table_name='ai_usage_hourly'"
        "   AND column_name = ANY($1::text[])", names)
    assert len(hourly) == 5
    for r in hourly:
        assert r["is_nullable"] == "NO" and "0" in (r["column_default"] or ""), r["column_name"]
    runs = await pg.fetch(
        "SELECT column_name, is_nullable FROM information_schema.columns"
        " WHERE table_schema='public' AND table_name='agent_runs'"
        "   AND column_name = ANY($1::text[])",
        ["steps", "tool_calls", "tool_errors", "deliverables", "turn_end_reason"])
    assert len(runs) == 5 and all(r["is_nullable"] == "YES" for r in runs)


@_skip
async def test_both_new_tables_have_rls_on_and_no_browser_policy(pg):
    """anon key 是烤进浏览器包的公开值。多一条给 anon/authenticated 的策略就是
    一个公开读口（CLAUDE.md 2026-09-11）。"""
    for tbl in ("search_docs", "output_citations"):
        assert await pg.fetchval(
            "SELECT relrowsecurity FROM pg_class WHERE relname=$1", tbl) is True, tbl
        joined = " ".join(r["r"] for r in await pg.fetch(
            "SELECT roles::text AS r FROM pg_policies"
            " WHERE schemaname='public' AND tablename=$1", tbl))
        assert "anon" not in joined and "authenticated" not in joined, tbl
```

```bash
cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t1/backend && \
  uv run pytest tests/db/test_mig_472_integration.py -q
```
预期（本机无库）：`5 skipped` —— 正是 `pytest-no-full-skip.sh` 在 CI 里拦的状态；真验证在 Step 8。

- [ ] **Step 8: 接进 schema-drift.yml**

`pull_request.paths` 里，`- 'backend/tests/db/test_migration_470_username_integration.py'` 之后加：

```yaml
      # 3c mig 472：两张新表 + 两组计数列。trgm opclass 在不在、UNIQUE 拦不拦得住
      # 第二行、五个累加器是不是 NOT NULL DEFAULT 0 —— 只有真库能答。
      - 'backend/tests/db/test_mig_472_integration.py'
```

文件**末尾**追加一个独立 step（不要粘进上一个 step 的 `run:`）：

```yaml
      - name: Assert mig 472's projection tables, trgm indexes & counter defaults
        working-directory: backend
        env:
          INTEGRATION_DATABASE_URL: postgresql://postgres:postgres@127.0.0.1:5432/drift
        run: |
          set -euo pipefail
          # 472 建的两张表是 3c 检索与引用反查的全部地基。文本测试读迁移写了什么；
          # 只有服务器能回答：pg_trgm 的 gin_trgm_ops opclass 是否真的可用（没装则
          # CREATE INDEX 直接报错，而文本测试照样绿）、UNIQUE (entity_kind,
          # entity_id) 是否真把同一个对象收成一行、ai_usage_hourly 那五个**加法
          # 累加器**是否真是 NOT NULL DEFAULT 0（可空的话 col + EXCLUDED.col 会把
          # 一整个小时桶算成 NULL），以及两张新表有没有给 anon/authenticated 开策略。
          bash "$GITHUB_WORKSPACE/.github/scripts/pytest-no-full-skip.sh" tests/db/test_mig_472_integration.py -v
```

把 `.github/scripts/pytest-no-full-skip.sh` 那条 paths 注释里的计数**重数**（不要递增），再本机 lint：

```bash
cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t1 && \
  grep -cE '^\s*bash .*pytest-no-full-skip\.sh" tests/' .github/workflows/schema-drift.yml && \
  /tmp/actionlint .github/workflows/schema-drift.yml && echo CLEAN
```
没有 `/tmp/actionlint` 就先取：`curl -sSL -o /tmp/al.tgz https://github.com/rhysd/actionlint/releases/download/v1.7.7/actionlint_1.7.7_darwin_arm64.tar.gz && tar xzf /tmp/al.tgz -C /tmp actionlint`。

- [ ] **Step 9: 突变记录（三处，逐个改→跑→确认红→改回）**

1. 把 team_id 回填那段 `UPDATE` 挪到两条 `INSERT INTO public.search_docs` 之后 → `test_the_team_backfill_runs_first_and_only_on_finished_runs` 转红。
2. `run_count INTEGER NOT NULL DEFAULT 0` 改成 `run_count INTEGER` → `test_the_two_counter_families_have_opposite_nullability` 转红。
3. 删掉 `reviews.py` 的 `postgresql_where=text("read_at IS NULL")` → `test_the_inbox_check_and_partial_index_stop_drifting` 转红。

三处恢复后重跑确认 `7 passed`。

- [ ] **Step 10: lint + 提交 + PR**

```bash
cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t1/backend && \
  uv run ruff check . && uv run black --check . && uv run isort --check-only .
git -C /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t1 add \
  supabase/migrations/472_harness_3c_search_efficiency.sql \
  backend/app/models/search.py backend/app/models/__init__.py \
  backend/app/models/agents.py backend/app/models/usage.py backend/app/models/reviews.py \
  backend/tests/db/test_migration_472_search_efficiency.py \
  backend/tests/db/test_mig_472_integration.py \
  backend/tests/db/test_orm_indexes_integration.py .github/workflows/schema-drift.yml
git -C /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t1 commit -m "feat(db): mig 472 检索投影表 + 引用镜像表 + 效率计数列 + 三段回填，ORM 同 PR 镜像（harness 三期 3c Task 1）"
git -C /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t1 push -u origin 3c-t1-mig-472
gh pr create --base master --title "feat(db): mig 472 —— 3c 检索投影表 / 引用镜像表 / 效率计数列" --body "见 plan Task 1。迁移先行：合并后先跑 run-migration，再合 Task 2–6。PR body 记下回填前 agent_runs.team_id IS NULL 的行数（Task 7 Step 3 的比较对象）。"
```

---

### Task 2（A1）: 小时表推自身花费 + `record_usage` 五个计数关键字

> 缺陷：`_finish` 把**树总额**（own + children + media）推进 `ai_usage_hourly`，而每个子 run 自己也会 `_finish` 写一行 → 跨 run 求和时子 agent 花费双计；该表无 `parent_run_id` 维度，事后剔不掉（recon A3）。

**Files:**
- Modify `backend/app/services/ai_usage.py`（`_hourly_upsert_stmt` 62-82、签名 85-97、docstring 98-106、values 117-131）
- Modify `backend/app/services/ai/runner/run_recorder.py`（`media_cents` 820-822、`record_usage` 调用 895-911）
- Create `backend/tests/test_hourly_usage_is_own_spend.py`

**Interfaces:**
- Produces `record_usage(..., run_count: int = 0, failed_runs: int = 0, tool_calls: int = 0, tool_errors: int = 0, deliverables: int = 0) -> None`，五个关键字在同一次 upsert 里累加。
- Produces `RunRecorder._finish` 的局部量 `own_media_cents`（Task 4 复用）。
- Consumes `self._event_writer.views.get("efficiency")` —— Part B Task 8 才产出，此刻缺省 `{}`、五个计数写 0。刻意如此，两个 Task 可独立合并。
- 不变：`agent_runs.cost_cents` 仍是树总额。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_hourly_usage_is_own_spend.py
"""A1：小时表记的是**自身**花费。父 + 两子 + 一张图的 fixture。

子 run 自己那一行就是它 —— 父行再加一遍，跨 run 求和就双计，而
ai_usage_hourly 没有 parent_run_id 维度，事后剔不掉。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.ai.runner import run_recorder as rr

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class _Writer:
    """EventWriter 替身：只需 views 与 refold_external_slices。"""

    def __init__(self, views: dict[str, Any]) -> None:
        self.views = views

    async def refold_external_slices(self) -> None:
        return None


def _recorder(*, views, prompt=10, completion=20):
    rec = rr.RunRecorder.__new__(rr.RunRecorder)
    rec.run_id = "900000000000001"
    rec.user_id, rec.agent_id = uuid4(), uuid4()
    rec.team_id, rec.project_id, rec.session_id = 42, None, None
    rec.model, rec.trigger, rec.attribution = "doubao-seed-2-0-lite", "chat", "direct_human"
    rec._prompt_tokens, rec._completion_tokens, rec._cached_input_tokens = prompt, completion, 0
    rec._skill_slugs_used, rec._output_summary = [], None
    rec._prompt_rate = rec._completion_rate = None
    rec._event_writer = _Writer(views)
    return rec


@pytest.fixture
def captured(monkeypatch):
    """拦下 record_usage 与 agent_runs 的 UPDATE，两边都记下来。"""
    calls: dict[str, list] = {"usage": [], "update": []}

    import app.db.session as db_session
    import app.services.ai_usage as ai_usage

    @asynccontextmanager
    async def _write_scope():
        class _S:
            async def execute(self, stmt):
                calls["update"].append(stmt)

        yield _S()

    async def _record(**kwargs):
        calls["usage"].append(kwargs)

    monkeypatch.setattr(db_session, "write_scope", _write_scope)
    monkeypatch.setattr(ai_usage, "record_usage", _record)
    monkeypatch.setattr("app.services.ai.billing.token_billing.reconcile_run", AsyncMock())
    return calls


def _values(stmt) -> dict:
    return {k.name: v for k, v in stmt._values.items()}


async def test_the_children_own_rows_sum_to_the_root_column(captured):
    """own 10 + 两子 3+4 + 一张图 5：根 run 的列是 22（树总额），它的小时行必须
    是 15，而三行小时表加起来正好等于那个 22。"""
    for views in (
        {"cost": {"own_cents": 3.0, "by_child": {}, "media_cents": 0.0}},
        {"cost": {"own_cents": 4.0, "by_child": {}, "media_cents": 0.0}},
        {"cost": {"own_cents": 10.0, "by_child": {"c1": 3.0, "c2": 4.0}, "media_cents": 5.0}},
    ):
        await _recorder(views=views)._finish(status="completed")
    root = _values(captured["update"][-1])["cost_cents"]
    assert root == 22.0, "列仍是树总额（预算门禁靠它）"
    assert captured["usage"][-1]["cost_cents"] == 15.0
    assert sum(c["cost_cents"] for c in captured["usage"]) == root


async def test_the_counters_default_to_zero_then_follow_the_fold(captured):
    """efficiency fold（Part B Task 8）还没合时写 0 而不是炸；合了之后跟它走。"""
    await _recorder(views={"cost": {"own_cents": 1.0}})._finish(status="completed")
    kw = captured["usage"][0]
    assert (kw["run_count"], kw["failed_runs"]) == (1, 0)
    assert (kw["tool_calls"], kw["tool_errors"], kw["deliverables"]) == (0, 0, 0)

    await _recorder(views={
        "cost": {"own_cents": 1.0},
        "efficiency": {"tool_calls": 7, "tool_errors": 2, "deliverables": 3},
    })._finish(status="completed")
    kw = captured["usage"][1]
    assert (kw["tool_calls"], kw["tool_errors"], kw["deliverables"]) == (7, 2, 3)


async def test_a_failed_run_counts_as_failed(captured):
    await _recorder(views={"cost": {"own_cents": 1.0}})._finish(
        status="failed", error_code="provider_error")
    assert captured["usage"][0]["failed_runs"] == 1


async def test_a_media_only_run_still_reaches_the_hourly_table(captured):
    """零 token、只生了图的 run：旧的 token>0 守门把它整行丢掉，run_count 从第一
    天起就偏低。"""
    rec = _recorder(views={"cost": {"own_cents": None, "by_child": {}, "media_cents": 6.0}},
                    prompt=0, completion=0)
    await rec._finish(status="completed")
    assert captured["usage"], "media-only run 也要进小时表"
    assert captured["usage"][0]["cost_cents"] == 6.0
```

- [ ] **Step 2: 跑它，确认红**

```bash
cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t2/backend && \
  uv run pytest tests/test_hourly_usage_is_own_spend.py -q
```
预期：第一个用例 `assert 22.0 == 15.0`；计数用例 `KeyError: 'run_count'`；media-only 用例 `assert []`。

- [ ] **Step 3: 改 `record_usage`**

`app/services/ai_usage.py` —— `_hourly_upsert_stmt` 的 `set_` 里，`"event_count"` 之后、`"updated_at"` 之前加；签名 `cost_cents` 之后加；values 字典 `"event_count": 1,` 之后加：

```python
            "run_count": AiUsageHourly.run_count + stmt.excluded.run_count,
            "failed_runs": AiUsageHourly.failed_runs + stmt.excluded.failed_runs,
            "tool_calls": AiUsageHourly.tool_calls + stmt.excluded.tool_calls,
            "tool_errors": AiUsageHourly.tool_errors + stmt.excluded.tool_errors,
            "deliverables": AiUsageHourly.deliverables + stmt.excluded.deliverables,
...
    run_count: int = 0,
    failed_runs: int = 0,
    tool_calls: int = 0,
    tool_errors: int = 0,
    deliverables: int = 0,
...
                "run_count": int(run_count or 0),
                "failed_runs": int(failed_runs or 0),
                "tool_calls": int(tool_calls or 0),
                "tool_errors": int(tool_errors or 0),
                "deliverables": int(deliverables or 0),
```

docstring 追加：

```
    ``cost_cents`` 是这个 run 的**自身**花费（own + media），不是树总额：每个子
    run 自己也会写一行，父行再把子的加进来就是双计，而这张表没有 parent_run_id
    维度，事后剔不掉（3c A1）。五个计数列同理 —— 数的都是自身量，跨 run 求和
    天然正确。``event_count`` 不动：它是「有 token 的完成」数，语义不同。
```

- [ ] **Step 4: 改 `_finish`**

`run_recorder.py:820-822`，`media_cents = ...` 之后插入；`:895` 起的守门与调用整段替换：

```python
        # A1：小时表与积分账都收**自身**花费（own + media）。列里的 cost_cents
        # 仍是树总额（review I3：父 run 完成时花费不能倒退），两个数各有其用。
        own_media_cents = round((own_cents or 0.0) + media_cents, 4)
```

```python
        eff = (
            (self._event_writer.views.get("efficiency") if self._event_writer else None)
            or {}
        )
        # token > 0 **或** 有媒体花费：只生了图、零 token 的 run 也是一次 run，
        # 旧守门把它整行丢掉，run_count 从第一天起就偏低。
        if (self._prompt_tokens + self._completion_tokens) > 0 or own_media_cents > 0:
            try:
                from app.services.ai_usage import record_usage

                await record_usage(
                    module=self.trigger,
                    attribution=effective_attribution,
                    prompt_tokens=self._prompt_tokens,
                    completion_tokens=self._completion_tokens,
                    cached_input_tokens=self._cached_input_tokens,
                    team_id=self.team_id,
                    project_id=self.project_id,
                    agent_id=self.agent_id,
                    model=self.model,
                    cost_cents=own_media_cents,
                    run_count=1,
                    failed_runs=int(status != "completed"),
                    tool_calls=int(eff.get("tool_calls") or 0),
                    tool_errors=int(eff.get("tool_errors") or 0),
                    deliverables=int(eff.get("deliverables") or 0),
                )
            except Exception as exc:  # noqa: BLE001 — defence in depth
                logger.warning(f"[RunRecorder] usage rollup failed (non-fatal): {exc}")
```

- [ ] **Step 5: 跑通过 + 既有回归**

```bash
cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t2/backend && \
  uv run pytest tests/test_hourly_usage_is_own_spend.py tests/test_agent_usage.py \
    tests/test_run_recorder.py tests/test_run_recorder_cached_cost.py \
    tests/repositories/test_usage_repository.py -q
```
预期：`4 passed` + 四个既有文件全绿。任何既有用例断言小时表拿到树总额，就改成 own+media 并在 commit message 里点名 —— 那是缺陷的化石，不是回归。

- [ ] **Step 6: 突变记录**

`cost_cents=own_media_cents` 改回 `cost_cents=cost_cents` → 第一个用例转红；再把 `run_count=1` 改成 `run_count=0` → `test_the_counters_default_to_zero_then_follow_the_fold` 转红。两处恢复并重跑确认 `4 passed`。

- [ ] **Step 7: lint + 提交 + PR**

```bash
cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t2/backend && \
  uv run ruff check . && uv run black --check . && uv run isort --check-only .
git -C /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t2 add \
  backend/app/services/ai_usage.py backend/app/services/ai/runner/run_recorder.py \
  backend/tests/test_hourly_usage_is_own_spend.py
git -C /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t2 commit -m "fix(usage): 小时表记自身花费而非树总额，子 agent 不再双计 + 五个效率计数（harness 三期 3c Task 2 / A1）"
git -C /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t2 push -u origin 3c-t2-hourly-own-spend
gh pr create --base master --title "fix(usage): 小时表推自身花费（A1）" --body "见 plan Task 2。依赖 mig 472 已上线（五个计数列）。"
```

---

### Task 3（A2）: `issue_totals` 加 root 过滤

> 缺陷：`usage_repository.issue_totals`（`GET /api/v1/usage/issues/{id}` 的后端）对整个议题 `SUM(cost_cents)` 不过滤 root，而预算门禁 `spent_cents_for_issue`（`agent_runs_repository.py:843`）与 `issue_rollup` 都有 `parent_run_id IS NULL` → 同一个议题，Usage 面与驾驶舱 Budget 格读出两个数（recon A4）。

**Files:**
- Modify `backend/app/repositories/usage_repository.py`（`issue_totals` 200-232）
- Modify `backend/tests/repositories/test_usage_repository.py`（末尾追加两个用例）

**Interfaces:** Produces `issue_totals(issue_id)` 的 SELECT 多一条 `WHERE agent_runs.parent_run_id IS NULL`；返回键不变（`prompt_tokens / completion_tokens / total_tokens / cost_cents / run_count`）。Consumes `AgentRuns.parent_run_id`（已有列）。

- [ ] **Step 1: 写失败测试（追加到现有文件末尾，复用该文件的 `_RecordingSession` / `_FakeResult` / `_install`）**

```python
# ── A2：议题合计只算 root run ────────────────────────────────────────

_EMPTY_TOTALS = {
    "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
    "cost_cents": Decimal("0"), "run_count": 0,
}


@pytest.mark.asyncio
async def test_issue_totals_filters_to_root_runs(monkeypatch):
    session = _RecordingSession(_FakeResult(_EMPTY_TOTALS))
    _install(monkeypatch, session)
    await usage_repository.issue_totals(900000000000001)
    sql, binds = session.calls[0]
    assert "parent_run_id IS NULL" in sql
    assert 900000000000001 in binds.values()


@pytest.mark.asyncio
async def test_issue_totals_and_the_budget_gate_use_the_same_predicate(monkeypatch):
    """两处「这个议题花了多少」必须同一条谓词。比的是编译出的 SQL 片段，不是
    各自的注释 —— 注释不会在漂移时报错。"""
    from app.repositories.agent_runs_repository import AgentRunsRepository

    session = _RecordingSession(_FakeResult(_EMPTY_TOTALS))
    _install(monkeypatch, session)
    await usage_repository.issue_totals(1)
    totals_sql = session.calls[0][0]

    class _Scalar:
        def scalar_one(self):
            return 0

    gate_session = _RecordingSession(_Scalar())
    _install(monkeypatch, gate_session)
    await AgentRunsRepository().spent_cents_for_issue(issue_id=1)

    assert "parent_run_id IS NULL" in totals_sql
    assert "parent_run_id IS NULL" in gate_session.calls[0][0]
```

- [ ] **Step 2: 跑它，确认红**

```bash
cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t3/backend && \
  uv run pytest tests/repositories/test_usage_repository.py -q
```
预期：两个新用例 `AssertionError: assert 'parent_run_id IS NULL' in "SELECT ..."`。

- [ ] **Step 3: 最小实现**

`app/repositories/usage_repository.py` —— `issue_totals` 的 `.where(...)` 与 docstring：

```python
                    ).where(
                        AgentRuns.issue_id == _coerce_bigint(issue_id),
                        # A2：只算 root run。子 run 的花费已滚进父行的 cost_cents
                        # （_finish 的树总额），再加一遍就是双计 —— 而预算门禁
                        # （spent_cents_for_issue）与 issue_rollup 一直是 root-only，
                        # 于是同一个议题两个面读出两个数。
                        AgentRuns.parent_run_id.is_(None),
                    )
```

```python
    """Per-issue AI spend, summed from agent_runs by the issue_id index.

    Root runs only（A2）—— 与 ``agent_runs_repository.spent_cents_for_issue`` 和
    ``issue_rollup.compute_rollup`` 同一条谓词。子 run 的花费通过父行的树总额
    上滚，这里再加一次就是双计。
    """
```

- [ ] **Step 4: 跑通过**

```bash
cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t3/backend && \
  uv run pytest tests/repositories/test_usage_repository.py tests/test_usage_detail_endpoints.py -q
```
预期：全绿。

- [ ] **Step 5: 突变记录**

删掉 `AgentRuns.parent_run_id.is_(None)` 这一行 → 两个新用例转红；恢复并重跑确认全绿。

- [ ] **Step 6: lint + 提交 + PR**

```bash
cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t3/backend && \
  uv run ruff check . && uv run black --check . && uv run isort --check-only .
git -C /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t3 add \
  backend/app/repositories/usage_repository.py backend/tests/repositories/test_usage_repository.py
git -C /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t3 commit -m "fix(usage): 议题合计只算 root run，与预算门禁口径一致（harness 三期 3c Task 3 / A2）"
git -C /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t3 push -u origin 3c-t3-issue-totals-root
gh pr create --base master --title "fix(usage): /usage/issues/{id} 加 root 过滤（A2）" --body "见 plan Task 3。"
```

---

### Task 4（A3）: 积分真扣

> 缺陷：`token_billing.py:288-297` 调 `ps.check_and_consume(team_id=…, points=…, action=…, metadata=…)`；真签名是 `(team_id, user_id, action_type, reference_id=None, count=1, override_cost=None, description=None)` —— 四个关键字全不匹配、两个必填缺失。每次 completed + cost>0 的 run 都 TypeError，被 `:306-314` 吞成 WARNING；而 `ai_usage_logs` 那一半写成功了 → 库里有一批「有 `cost_points` 记录、从未真扣分」的行（recon A5）。
>
> **返回形态（已读 `points_service.py` 确认）**：返回 `Dict[str, Any]`，键固定为 `success` / `points_cost` / `balance_after` / `reason`（:70-75, :120-135, :155-160）。成功键就是 `success`。余额不足与「RPC 不可用」都返回 `success=False` + `reason`，**不 raise**。流水落 `public.point_transactions`（`reference_type = action_type`，`amount` 为负）。
>
> **Stated Limitation（BYOK 重复计费）**：`agent_runs` / `generated_media` 无 run 级 BYOK 标记，`RunRecorder` 无此 kwarg。故 `byo_key=False` 不动 —— BYOK 的 LLM 模型通常不在 `ai_model_prices` 里（`own_cents` 为 0，天然不扣），受影响的只有管理员配了 `per_call_cents` 的 BYOK 图片模型。闭合它要加列 + 改构造签名，超出 A3 与 mig 472 范围；本 Task 给急停开关 + 完成账对照 SQL。

**Files:**
- Modify `backend/app/core/config.py`（`SCOPE_ENFORCE_RESOURCES` 82-95 之后）
- Modify `backend/config.yml`（末尾追加一节）
- Modify `backend/app/services/ai/billing/token_billing.py`（import 区 + `reconcile_run` 286-314）
- Modify `backend/app/services/ai/runner/run_recorder.py`（守门 913、`cost_points` 935、`byo_key` 936 上方注释）
- Modify `backend/tests/test_token_billing.py`（`test_reconcile_platform_run_calls_points_service` 196-227 改写 + 追加四个）

**Interfaces:**
- Produces `settings.AGENT_POINTS_CHARGE_ENABLED: bool`（默认 `True`；`config.yml` 键 `AGENT_POINTS_CHARGE_ENABLED`）。
- Produces `check_and_consume` 的调用参数集合恰为 `{team_id, user_id, action_type, reference_id, override_cost, description}`。
- Consumes Task 2 的 `own_media_cents`。**本 Task 必须从合完 Task 2 的 `origin/master` 起**（两者都改 `_finish`）。

- [ ] **Step 1: 写失败测试（替换 196-227 那个用例并追加四个；复用该文件的 `_write_scope`）**

```python
_REAL_CONSUME_KWARGS = {
    "team_id", "user_id", "action_type", "reference_id", "override_cost", "description",
}


def _fake_points(result):
    ps = MagicMock()
    ps.check_and_consume = AsyncMock(return_value=result)
    return ps


async def _reconcile(ps, **over):
    kwargs = dict(
        run_id="900000000000007", user_id=uuid4(), team_id=42, project_id=None,
        session_id=None, agent_id=uuid4(), model="nous_qwen-max",
        prompt_tokens=10, completion_tokens=20, cost_points=2.5, byo_key=False,
    )
    kwargs.update(over)
    with (
        patch("app.db.session.write_scope", new=_write_scope(ok=True)),
        patch("app.services.billing.points_service.PointsService", return_value=ps),
    ):
        return await tb.reconcile_run(**kwargs)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reconcile_calls_the_real_signature_exactly():
    """四个关键字全不匹配、两个必填缺失，每轮 TypeError 被吞成 WARNING ——
    参数集合**精确**匹配，多一个少一个都红，防再次静默漂移。"""
    ps = _fake_points({"success": True, "points_cost": 3, "balance_after": 97, "reason": None})
    result = await _reconcile(ps)
    _, kwargs = ps.check_and_consume.call_args
    assert set(kwargs) == _REAL_CONSUME_KWARGS
    assert kwargs["team_id"] == "42" and kwargs["action_type"] == "agent_run"
    assert kwargs["reference_id"] == "900000000000007"
    assert kwargs["override_cost"] == 3  # ceil(2.5)
    assert "nous_qwen-max" in kwargs["description"] and "30 tokens" in kwargs["description"]
    assert result.charged is True and result.charged_points == 2.5


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_non_empty_dict_with_success_false_is_not_a_charge():
    """旧代码 `charged=bool(ok)` 对非空 dict 恒 True —— 余额不足被记成扣过。"""
    ps = _fake_points({"success": False, "points_cost": 3, "balance_after": 0,
                       "reason": "Insufficient balance"})
    result = await _reconcile(ps)
    assert result.charged is False and result.charged_points == 0.0
    assert "Insufficient balance" in (result.note or "")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_rpc_unavailable_shape_is_also_not_a_charge():
    """RPC 不可用时返回 dict（不 raise）—— 必须与「扣成功」分开，否则一次服务
    降级会被记成一次收费。"""
    ps = _fake_points({"success": False, "points_cost": 3, "balance_after": None,
                       "reason": "Points service temporarily unavailable."})
    result = await _reconcile(ps)
    assert result.charged is False and "temporarily unavailable" in (result.note or "")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_kill_switch_skips_the_charge_but_still_logs_usage():
    ps = _fake_points({"success": True, "points_cost": 3, "balance_after": 97, "reason": None})
    with patch.object(tb.settings, "AGENT_POINTS_CHARGE_ENABLED", False):
        result = await _reconcile(ps)
    ps.check_and_consume.assert_not_awaited()
    assert result.charged is False and result.usage_logged is True
    assert result.note == "charging disabled"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fractional_cost_rounds_up_never_to_zero():
    """0.3 分的 run 扣 1 分 —— 向下取整会让一整类小额 run 白跑。"""
    ps = _fake_points({"success": True, "points_cost": 1, "balance_after": 99, "reason": None})
    await _reconcile(ps, cost_points=0.3)
    assert ps.check_and_consume.call_args[1]["override_cost"] == 1
```

- [ ] **Step 2: 跑它，确认红**

```bash
cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t4/backend && \
  uv run pytest tests/test_token_billing.py -q
```
预期：签名用例报 `assert {'team_id','points','action','metadata'} == {...}`；急停用例报 `AttributeError: ... has no attribute 'AGENT_POINTS_CHARGE_ENABLED'`。

- [ ] **Step 3: 加配置**

`app/core/config.py`，`SCOPE_ENFORCE_RESOURCES` 之后；`backend/config.yml` 末尾：

```python
    AGENT_POINTS_CHARGE_ENABLED: bool = Field(
        default=True,
        description="Agent run 结束后是否真扣团队积分（3c A3）。这条链从上线起就"
        "因为四个关键字不匹配而每轮 TypeError，被吞成 WARNING；修好之后它会"
        "**真的**动用户余额，所以需要一个不改代码就能拉闸的开关。关闭时"
        "ai_usage_logs 照写（审计不断），只跳过 PointsService.check_and_consume，"
        "ReconcileResult.note = 'charging disabled'。",
    )
```

```yaml
# ===========================================
# Agent 积分计费（3c A3）
# ===========================================
# true = run 结束按自身花费（own + media）真扣团队积分；false = 只写
# ai_usage_logs 审计行，不动余额。急停：改这里或用同名环境变量，不需要改代码。
AGENT_POINTS_CHARGE_ENABLED: true
```

- [ ] **Step 4: 改 `reconcile_run`**

`token_billing.py` 顶部加 `import math` 与 `from app.core.config import settings`；`# 2. Charge points` 之后的 `try:` 块（286-314）整段替换：

```python
    if not settings.AGENT_POINTS_CHARGE_ENABLED:
        # 急停：审计行上面第 1 步已经写了，这里只是不动余额。
        return ReconcileResult(
            charged=False, charged_points=0.0, byo_key=False,
            usage_logged=usage_logged, note="charging disabled",
        )

    try:
        from app.services.billing.points_service import PointsService

        ps = PointsService()
        # 真签名（points_service.py:34-43）。此前这里传 points= / action= /
        # metadata= —— 三个不存在的关键字，外加缺了必填的 user_id / action_type，
        # 于是每一次 completed + cost>0 的 run 都 TypeError 并被下面的 except 吞成
        # WARNING：ai_usage_logs 有记录、余额一分没动，整整一个上线周期。
        # override_cost 是整数积分，向上取整：0.3 分的 run 扣 1 分而不是 0 ——
        # 向下取整会让一整类小额 run 白跑。
        res = await ps.check_and_consume(
            team_id=str(team_id),
            user_id=str(user_id),
            action_type="agent_run",
            reference_id=str(run_id),
            override_cost=int(math.ceil(cost_points)),
            description=f"{model} · {total_tokens} tokens",
        )
        # 返回 Dict[str, Any]（success / points_cost / balance_after / reason），
        # 不是 bool。余额不足与「RPC 不可用」都走 success=False 而**不 raise** ——
        # 旧代码 `charged=bool(ok)` 对任何非空 dict 恒 True，把这两种拒绝都记成了
        # 一次成功扣费。
        ok = bool(res.get("success"))
        return ReconcileResult(
            charged=ok,
            charged_points=cost_points if ok else 0.0,
            byo_key=False,
            usage_logged=usage_logged,
            note=None if ok else (res.get("reason") or "points consume denied"),
        )
    except Exception as exc:
        logger.warning(f"[token_billing] points consume failed: {exc}")
        return ReconcileResult(
            charged=False, charged_points=0.0, byo_key=False,
            usage_logged=usage_logged, note=f"points consume errored: {exc}",
        )
```

- [ ] **Step 5: 改 `_finish` 的调用**

`run_recorder.py:913` 的守门改为 `if status == "completed" and own_media_cents > 0:`；`cost_points=float(cost_cents),` 换成下面第一段；`byo_key=False` 与它原有注释保持不动，其上补第二段：

```python
                    # A3：按**自身**花费扣，不按树总额 —— 子 run 自己也会走到这里
                    # 扣它那份，父行再扣一遍就是对同一笔钱收两次。
                    cost_points=own_media_cents,
                    # ⚠️ 3c A3 Stated Limitation：agent_runs 没有 run 级 BYOK 标记，
                    # 所以管理员配了 per_call_cents 的 BYOK 图片模型会被按平台价扣
                    # 一次。闭合它要加列 + 改构造签名，另立票；现阶段的兜底是
                    # AGENT_POINTS_CHARGE_ENABLED。
```

- [ ] **Step 6: 跑通过**

```bash
cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t4/backend && \
  uv run pytest tests/test_token_billing.py tests/test_run_recorder.py \
    tests/test_hourly_usage_is_own_spend.py -q
```
预期：全绿（`test_token_billing.py` 共 12 个用例）。

- [ ] **Step 7: 突变记录**

`ok = bool(res.get("success"))` 改成 `ok = bool(res)` → 两个 `not_a_charge` 用例转红；再把 `int(math.ceil(cost_points))` 改成 `int(cost_points)` → `test_fractional_cost_rounds_up_never_to_zero` 转红。两处恢复并重跑确认全绿。

- [ ] **Step 8: 完成账要报的两个数（写进 PR body，不写代码）**

```sql
-- ① 存量「有 ai_usage_logs 记录、从未扣分」的行数（用户裁定：只向前，不追扣）
-- ai_usage_logs 没有 run_id 列，无法按 run 与 point_transactions 配对；修复上线前
-- check_and_consume 一次都没成功过，所以「上线时刻之前的全部行」就是存量。
SELECT count(*) AS logged_never_charged, coalesce(sum(cost_points), 0) AS points
FROM public.ai_usage_logs
WHERE cost_points > 0 AND created_at < '<A3 deploy-gpu 成功时刻>';
-- 交叉确认（应为 0）：
SELECT count(*) FROM public.point_transactions WHERE reference_type = 'agent_run';

-- ② BYOK 风险面：配了每次调用价的图片模型下有多少 run 会被按平台价扣
SELECT p.provider, p.model, count(DISTINCT g.origin_run_id) AS runs_at_risk
FROM public.generated_media g
JOIN public.ai_model_prices p ON p.model = g.model AND p.provider = g.provider
WHERE p.per_call_cents IS NOT NULL AND g.origin_run_id IS NOT NULL
GROUP BY 1, 2 ORDER BY 3 DESC;
```
② 非零时，在 PR body 里写明「建议先以 `AGENT_POINTS_CHARGE_ENABLED=false` 上线，等 BYOK 标记那张票落地再开」，由用户拍板。

- [ ] **Step 9: lint + 提交 + PR**

```bash
cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t4/backend && \
  uv run ruff check . && uv run black --check . && uv run isort --check-only .
git -C /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t4 add \
  backend/app/core/config.py backend/config.yml \
  backend/app/services/ai/billing/token_billing.py \
  backend/app/services/ai/runner/run_recorder.py backend/tests/test_token_billing.py
git -C /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t4 commit -m "fix(billing): 积分按真签名真扣、按自身花费计，加 AGENT_POINTS_CHARGE_ENABLED 急停（harness 三期 3c Task 4 / A3）"
git -C /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t4 push -u origin 3c-t4-points-really-charge
gh pr create --base master --title "fix(billing): 积分真扣（A3）" --body "见 plan Task 4。⚠️ 这条链从此会真的动用户余额。BYOK 限制与两条完成账 SQL 见下。"
```

---

### Task 5（A4）: `InboxKind` 单一来源 + 收件箱逐行容错

> 缺陷：`GET /api/v1/inbox` 每次 500。kind 枚举四处口径：schema 3 个（`schemas/inbox.py:7`）、DB CHECK 5 个（mig 373→387→399）、写入侧 5 个（`services/notifications.py:33-39`）、前端 5 个（`notificationsService.ts:10-15`）。故障点 `inbox_router.py:37` 逐行 `InboxNotificationResponse(**r)`，库里任何 `workflow_stage` / `agent_question` 行 → ValidationError → **整表 500**（生产 14 条 `[Unhandled] ValidationError at GET /api/v1/inbox`）。ORM 那一份已在 Task 1 随迁移修掉。

**Files:**
- Modify `backend/app/services/notifications.py`（`NotificationKind` 33-39）
- Modify `backend/app/schemas/inbox.py`（`InboxKind` 7）
- Modify `backend/app/api/inbox_router.py`（import 区、模块 docstring 1-8、`list_inbox` 30-39）
- Create `backend/tests/api/test_inbox_kind_mirror.py`

**Interfaces:**
- Produces `app.services.notifications.NOTIFICATION_KINDS: tuple[str, ...]`（五值，**唯一来源**）、`NotificationKind = Literal[*NOTIFICATION_KINDS]`。
- Produces `app.schemas.inbox.InboxKind = Literal[*NOTIFICATION_KINDS]`。
- Produces `list_inbox` 逐行 `try/except ValidationError` → `logger.error` + skip；`unread_count` 语义不变，`total` 变成「成功构造的行数」。
- Consumes Task 1 产出的五值 ORM CHECK。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/api/test_inbox_kind_mirror.py
"""A4：kind 枚举的四处口径钉成一处。

四份各写各的、没有任何东西比对它们，于是 mig 387 与 399 各扩了一次 DB CHECK，
schema 停在 373 的三值，ORM 停在四值 —— 库里一行 agent_question 就把整表打成 500。
"""
from __future__ import annotations

import pathlib
import re
from types import SimpleNamespace
from typing import get_args

import pytest
from sqlalchemy import CheckConstraint

from app.models import InboxNotifications
from app.schemas.inbox import InboxKind
from app.services.notifications import NOTIFICATION_KINDS, NotificationKind

pytestmark = pytest.mark.unit

REPO = pathlib.Path(__file__).resolve().parents[3]
MIG = REPO / "supabase/migrations"
FRONTEND = REPO / "frontend/services/notificationsService.ts"


def _latest_migration_kinds() -> set[str]:
    """最后一个动过 inbox_notifications_kind_check 的迁移里那一组值。按文件号排序
    取最大 ——「最新」不能靠印象，也不能硬编码 399（下一个扩它的迁移会让硬编码
    悄悄指着旧的那一份）。"""
    hits = []
    for path in sorted(MIG.glob("*.sql")):
        m = re.search(
            r"inbox_notifications_kind_check\s+CHECK\s*\(\s*kind\s+IN\s*\(([^)]*)\)",
            path.read_text(encoding="utf-8"), re.IGNORECASE)
        if m:
            hits.append(set(re.findall(r"'([a-z_]+)'", m.group(1))))
    assert hits, "没有任何迁移建过 inbox_notifications_kind_check —— 扫描本身坏了"
    return hits[-1]


def _frontend_kinds() -> set[str]:
    m = re.search(r"InboxKind\s*=\s*([^;]+);", FRONTEND.read_text(encoding="utf-8"))
    assert m, "notificationsService.ts 里找不到 InboxKind —— 镜像测试失去了它的对象"
    return set(re.findall(r"'([a-z_]+)'", m.group(1)))


def _orm_kinds() -> set[str]:
    check = next(
        c for c in InboxNotifications.__table__.constraints
        if isinstance(c, CheckConstraint) and c.name == "inbox_notifications_kind_check")
    return set(re.findall(r"'([a-z_]+)'", str(check.sqltext)))


def test_all_four_surfaces_derive_from_the_write_side():
    assert set(get_args(NotificationKind)) == set(NOTIFICATION_KINDS)
    assert set(get_args(InboxKind)) == set(NOTIFICATION_KINDS)
    assert len(NOTIFICATION_KINDS) == len(set(NOTIFICATION_KINDS))
    expected = set(NOTIFICATION_KINDS)
    assert _orm_kinds() == expected, "ORM CheckConstraint"
    assert _latest_migration_kinds() == expected, "最新 migration 的 CHECK"
    assert _frontend_kinds() == expected, "frontend/services/notificationsService.ts"


def test_the_two_kinds_that_caused_the_500_are_actually_in_there():
    """下限断言：上面那条在四处**同时**丢掉这两个值时也会绿 —— 那正是缺陷第一天
    的样子。钉住具体的值，让扫描本身可证伪。"""
    assert {"agent_question", "workflow_stage"} <= set(NOTIFICATION_KINDS)


@pytest.mark.asyncio
async def test_one_unparseable_row_does_not_take_the_whole_list_down(monkeypatch):
    """一行坏数据（未来又多一个 kind、或某行被手工改坏）只该少一行，不该让整个
    收件箱 500 —— 与「分发器要容纳回调异常」同族。"""
    from app.api import inbox_router

    good = {"id": "1", "kind": "agent_question", "title": "Agent needs input",
            "severity": "info"}
    bad = {"id": "2", "kind": "not_a_kind", "title": "boom", "severity": "info"}

    class _Repo:
        async def list_notifications(self, *_a, **_kw):
            return [good, bad]

        async def unread_count(self, *_a, **_kw):
            return 1

    monkeypatch.setattr(inbox_router, "get_inbox_repository", lambda: _Repo())
    res = await inbox_router.list_inbox(
        auth=SimpleNamespace(user_id="u1"), unread_only=False, limit=50, offset=0)
    assert [n.id for n in res.notifications] == ["1"]
    assert res.total == 1 and res.unread_count == 1
```

- [ ] **Step 2: 跑它，确认红**

```bash
cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t5/backend && \
  uv run pytest tests/api/test_inbox_kind_mirror.py -q
```
预期：collection 阶段 `ImportError: cannot import name 'NOTIFICATION_KINDS'`。

- [ ] **Step 3: 单一来源**

`app/services/notifications.py` —— `NotificationKind = Literal[...]`（33-39）替换；`app/schemas/inbox.py` —— `InboxKind`（7 行）替换：

```python
#: kind 的**唯一来源**。DB CHECK（最新 migration）、ORM CheckConstraint、
#: schemas.inbox.InboxKind、前端 notificationsService.ts 四处都对着它，
#: tests/api/test_inbox_kind_mirror.py 逐处比对。加一个 kind = 改这里 + 写迁移
#: + 改 ORM + 改前端，四处缺一个测试就红。
NOTIFICATION_KINDS: tuple[str, ...] = (
    "generation_result",
    "publish_result",
    "autopilot_output",
    "workflow_stage",
    "agent_question",
)
NotificationKind = Literal[*NOTIFICATION_KINDS]
```

```python
from app.services.notifications import NOTIFICATION_KINDS

#: 派生自写入侧的单一来源。此前这里手写三个值，而 mig 387 / 399 各扩过一次 DB
#: CHECK —— 库里一行 agent_question 就让 /api/v1/inbox 整表 500。
InboxKind = Literal[*NOTIFICATION_KINDS]
```

`schemas/inbox.py` 与 `inbox_router.py` 的 docstring 里，凡「exactly three producer kinds」一律改成「the producer kinds listed in `NOTIFICATION_KINDS`」。

- [ ] **Step 4: 路由逐行容错**

`app/api/inbox_router.py` —— 顶部加 `from loguru import logger` 与 `from pydantic import ValidationError`；`list_inbox` 的返回体替换：

```python
    items: list[InboxNotificationResponse] = []
    for row in rows:
        try:
            items.append(InboxNotificationResponse(**row))
        except ValidationError as exc:
            # 一行坏数据不该让整个收件箱 500。历史上正是这里：schema 停在三个
            # kind，库里有 workflow_stage / agent_question 行，于是每一次列表请求
            # 都 ValidationError。容纳并**记 ERROR**（不是 except: pass）——
            # 与「分发器要容纳回调异常」同一条纪律。
            logger.error(
                f"[inbox] skipping unparseable row id={row.get('id')} "
                f"kind={row.get('kind')} err={exc}"
            )
    return InboxListResponse(
        notifications=items,
        total=len(items),
        unread_count=unread_count,
    )
```

- [ ] **Step 5: 跑通过**

```bash
cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t5/backend && \
  uv run pytest tests/api/test_inbox_kind_mirror.py -q && uv run pytest tests/ -q -k inbox
```
预期：`3 passed` + 既有 inbox 相关用例全绿。

- [ ] **Step 6: 突变记录**

从 `NOTIFICATION_KINDS` 删掉 `"agent_question"` → 前两个用例同时转红；恢复。再删掉路由里整个 `except ValidationError` 分支 → 第三个用例转红（ValidationError 抛出）；恢复并重跑确认 `3 passed`。

- [ ] **Step 7: lint + 提交 + PR**

```bash
cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t5/backend && \
  uv run ruff check . && uv run black --check . && uv run isort --check-only .
git -C /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t5 add \
  backend/app/services/notifications.py backend/app/schemas/inbox.py \
  backend/app/api/inbox_router.py backend/tests/api/test_inbox_kind_mirror.py
git -C /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t5 commit -m "fix(inbox): kind 枚举收成单一来源 + 列表逐行容错，/api/v1/inbox 不再整表 500（harness 三期 3c Task 5 / A4）"
git -C /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t5 push -u origin 3c-t5-inbox-kind-single-source
gh pr create --base master --title "fix(inbox): InboxKind 单一来源 + 逐行容错（A4）" --body "见 plan Task 5。ORM 侧那一份已随 Task 1（mig 472）修掉。"
```

---

### Task 6（A6）: run 的 `team_id` / `project_id` 从议题兜底

> 缺陷：`agent_runs.team_id` 大量 NULL（`UsagePage.tsx:14-16` 自认「团队 scope 只给月度」），而 `idx_agent_runs_billing` 是 `WHERE team_id IS NOT NULL` 的 partial 索引 —— NULL 的行对任何按团队的效率账等于不存在。已结束 run 的一次性回填在 mig 472 第 4 段；本 Task 修**向前**的派发链。
> ⚠️ 与契约的出入见开头第 2 条：`conversation_agent_turn.py` 没有议题概念、`team_id` 必然非空，那一侧只加回归 fixture —— 加兜底就是永不执行的死分支。

**Files:**
- Modify `backend/app/services/ai/chat/ai_library_chat_service.py`（`_dispatch_scope = ...` 1433-1437 之后插入；`RunRecorder(` 的 `team_id` 1446 与 `**_dispatch_scope.as_recorder_kwargs(),` 1447）
- Create `backend/tests/test_run_recorder_scope_fallback.py`

**Interfaces:**
- Produces 局部量 `_team_id = session.get("team_id") or (_issue_row or {}).get("team_id")`、`_project_id = _dispatch_scope.project_id or (_issue_row or {}).get("project_id")`；`_issue_row` 只在 `issue_id` 非空且 `session.get("team_id")` 为空时查一次。
- Consumes `IssueRepository.get_by_id`（`app/repositories/issue_repository.py:209`）、`DispatchScope.project_id` / `.episode_id`（`app/services/ai/scope/scope_binding.py:60-61`）。
- 不变：`tests/test_agent_run_scope.py::test_no_write_path_anywhere_touches_scope_columns` 保持绿 —— 兜底在**构造时**（INSERT），不是 UPDATE，那条正则扫的窗口不受影响。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_run_recorder_scope_fallback.py
"""A6：派发链的 run 必须带上 team_id / project_id。

NULL 的行落在 idx_agent_runs_billing（WHERE team_id IS NOT NULL）之外 ——
对任何按团队的效率账，它们等于不存在。
"""
from __future__ import annotations

import ast
import pathlib

import pytest

pytestmark = pytest.mark.unit

BACKEND = pathlib.Path(__file__).resolve().parents[1]
CHAT = BACKEND / "app/services/ai/chat/ai_library_chat_service.py"
CONV = BACKEND / "app/services/chat/conversation_agent_turn.py"


def _recorder_kwargs(path: pathlib.Path) -> dict[str, str]:
    """取出文件里唯一一处 RunRecorder(...) 的关键字（源码文本形式）。比 grep 稳：
    参数换行、夹注释都不影响。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "RunRecorder"]
    assert len(calls) == 1, f"{path.name} 里 RunRecorder 调用点不是一个"
    return {kw.arg: ast.unparse(kw.value) for kw in calls[0].keywords if kw.arg}


def test_the_chat_dispatch_site_takes_both_columns_from_the_fallback():
    kwargs = _recorder_kwargs(CHAT)
    assert kwargs["team_id"] == "_team_id"
    assert kwargs["project_id"] == "_project_id"
    assert kwargs["episode_id"] == "_dispatch_scope.episode_id"


def test_the_chat_fallback_reads_both_columns_off_the_issue_row():
    src = CHAT.read_text(encoding="utf-8")
    assert "_issue_row = await get_issue_repository().get_by_id(int(issue_id))" in src
    assert '_team_id = session.get("team_id") or (_issue_row or {}).get("team_id")' in src
    assert ('_project_id = _dispatch_scope.project_id or '
            '(_issue_row or {}).get("project_id")') in src


def test_the_conversation_summon_site_still_binds_the_team():
    """这一侧没有议题（模块里零个 issue 引用），team_id 来自 conversation 的
    scope_id，而 Gate 0 已保证它非空。这条守的是「未来有人把它拿掉」—— 不是
    兜底，是回归钉子。"""
    kwargs = _recorder_kwargs(CONV)
    assert kwargs["team_id"] == "int(scope_id) if scope_id is not None else None"
    assert "issue" not in CONV.read_text(encoding="utf-8").lower(), (
        "这个模块一旦引入议题概念，本 Task 的裁定（那一侧不加兜底）要重做")


def test_the_scope_columns_are_still_insert_only():
    """兜底在构造时（INSERT），不是 UPDATE —— agent_run_scope.py 的不可变承诺
    不受影响。直接跑那条既有守卫，而不是复述它。"""
    from tests.test_agent_run_scope import (
        test_no_write_path_anywhere_touches_scope_columns,
    )

    test_no_write_path_anywhere_touches_scope_columns()
```

- [ ] **Step 2: 跑它，确认红**

```bash
cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t6/backend && \
  uv run pytest tests/test_run_recorder_scope_fallback.py -q
```
预期：前两个用例红（`KeyError: 'project_id'` —— 当前是 `**as_recorder_kwargs()` splat；以及 `assert ... in src`），后两个绿。

- [ ] **Step 3: 最小实现**

`ai_library_chat_service.py` —— `_dispatch_scope = await resolve_dispatch_scope(...)` 之后插入：

```python
        # A6：议题派发链的 session 常常不带 team_id（UsagePage 自认「团队 scope
        # 只给月度」正是这个后果），而 idx_agent_runs_billing 是
        # WHERE team_id IS NOT NULL 的 partial 索引 —— NULL 的 run 对任何按团队的
        # 效率账等于不存在。只在真缺时查一次议题，不给正常路径加往返。
        _issue_row = None
        if issue_id and not session.get("team_id"):
            from app.repositories.issue_repository import get_issue_repository

            try:
                _issue_row = await get_issue_repository().get_by_id(int(issue_id))
            except Exception as exc:  # noqa: BLE001 — 兜底失败不该挡住回合
                logger.warning(f"[chat] issue scope fallback failed: {exc}")
        _team_id = session.get("team_id") or (_issue_row or {}).get("team_id")
        _project_id = _dispatch_scope.project_id or (_issue_row or {}).get("project_id")
```

`RunRecorder(` 的 `team_id=session.get("team_id"),` 与 `**_dispatch_scope.as_recorder_kwargs(),` 两行替换成三行 —— splat 必须拆开写明，否则 `project_id` 会被它覆盖，而显式关键字与 splat 并存是重复关键字、调用时 TypeError（同 CLAUDE.md `safe_popen_kwargs` 那条）：

```python
                team_id=_team_id,
                project_id=_project_id,
                episode_id=_dispatch_scope.episode_id,
```

- [ ] **Step 4: 跑通过**

```bash
cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t6/backend && \
  uv run pytest tests/test_run_recorder_scope_fallback.py tests/test_agent_run_scope.py \
    tests/test_scope_binding.py tests/test_issue_dispatch_usage.py -q
```
预期：全绿。若 `test_scope_binding.py` 有用例断言该调用点用的是 `as_recorder_kwargs()` splat，改成断言两个关键字显式传入，并在 commit message 里说明原因。

- [ ] **Step 5: 突变记录**

`team_id=_team_id` 改回 `team_id=session.get("team_id")` → `test_the_chat_dispatch_site_takes_both_columns_from_the_fallback` 转红；再把 `conversation_agent_turn.py` 的 `team_id=int(scope_id) if scope_id is not None else None` 改成 `team_id=None` → `test_the_conversation_summon_site_still_binds_the_team` 转红。两处恢复并重跑确认全绿。

- [ ] **Step 6: lint + 提交 + PR**

```bash
cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t6/backend && \
  uv run ruff check . && uv run black --check . && uv run isort --check-only .
git -C /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t6 add \
  backend/app/services/ai/chat/ai_library_chat_service.py \
  backend/tests/test_run_recorder_scope_fallback.py
git -C /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t6 commit -m "fix(runs): 议题派发链的 run 从议题兜底 team_id/project_id（harness 三期 3c Task 6 / A6）"
git -C /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t6 push -u origin 3c-t6-run-scope-fallback
gh pr create --base master --title "fix(runs): run 的 team_id 从议题兜底（A6）" --body "见 plan Task 6。已结束 run 的一次性回填在 mig 472 第 4 段。⚠️ conversation_agent_turn 那一侧不加兜底，理由见正文。"
```

---

### Task 7: Part A 的合并顺序与部署验证

> 本 Task 没有代码，只有一张按序执行的清单。Part B/C/D 的任何 Task 都不得在 Task 1 的迁移真的跑到生产之前合并。

**Files:** 无（完成账写进 Part D 的收尾 Task）。

- [ ] **Step 1: Task 1 先行，两条链都盯**

Task 1 同时碰 `supabase/migrations/**`（`run-migration.yml`）与 `backend/app/models/**`（`schema-drift.yml`，PR 阶段）。合并后：

```bash
gh run list --branch master --limit 10 \
  --json name,status,conclusion --jq '.[] | "\(.name) \(.status) \(.conclusion)"'
```
`Run Migration` 与 `Schema Drift` 都要 `success`。

- [ ] **Step 2: 到生产库确认迁移真的落了**

```bash
ssh gpupc "docker exec nous-db psql -U postgres -p 55434 -d postgres -c '\d search_docs'"
ssh gpupc "docker exec nous-db psql -U postgres -p 55434 -d postgres -c '\d output_citations'"
```
预期：`search_docs` 列出 5 个索引（两个 gin、三个 btree）+ `search_docs_entity_key`。⚠️ 端口是 **55434** 不是 5432（容器内 `PGPORT` 被 `POSTGRES_PORT` 覆盖）。

- [ ] **Step 3: 确认三段回填的行数**

```bash
ssh gpupc "docker exec nous-db psql -U postgres -p 55434 -d postgres -c \
  \"SELECT entity_kind, count(*) FROM public.search_docs GROUP BY 1\""
ssh gpupc "docker exec nous-db psql -U postgres -p 55434 -d postgres -c \
  \"SELECT count(*) FILTER (WHERE team_id IS NULL) AS still_null,
            count(*) FILTER (WHERE team_id IS NOT NULL) AS stamped
     FROM public.agent_runs WHERE ended_at IS NOT NULL\""
```
`still_null` 非零是**预期的**（`issue_id` 为空的 run 无处可取），但必须显著小于回填前 —— 回填前的数在 Task 1 的 PR body 里，否则这一步没有比较对象（「拿不到值」与「值没变」是两件事）。

- [ ] **Step 4: 2–6 的 worktree 与顺序**

```bash
for n in t2 t3 t5 t6; do
  git -C /Volumes/program/project-code/repos/nous-app worktree add \
    -b 3c-$n .worktrees/3c-$n origin/master
done
```
Task 2 / 3 / 5 / 6 互不相交，可同时开工、任意顺序合并。**Task 4 必须等 Task 2 合并后再建 worktree**（两者都改 `_finish`，Task 4 消费 `own_media_cents`）：

```bash
git -C /Volumes/program/project-code/repos/nous-app fetch origin && \
git -C /Volumes/program/project-code/repos/nous-app worktree add \
  -b 3c-t4 .worktrees/3c-t4 origin/master
```
从 `origin/master` 起，不从本机 master 起 —— 本机 master 会带 squash 前的提交。

- [ ] **Step 5: 每个后端 PR 合并后的部署验收**

```bash
gh run watch                                   # deploy-gpu 的 smoke
ssh gpupc "docker exec nous-worker curl -sS http://localhost:8080/api/v1/readyz"
```
`readyz` 的 `dbos` 字段必须是启用态；`configured_but_disabled` 即故障。**不要**用 `docker ps` 的 `healthy` 当依据。

- [ ] **Step 6: Task 4 上线后的真栈探针（唯一能证明积分真扣的那条）**

在 cn.nous.ink 跑一次会花钱的完成回合，然后：

```bash
ssh gpupc "docker exec nous-db psql -U postgres -p 55434 -d postgres -c \
  \"SELECT reference_id, amount, balance_after, description
     FROM public.point_transactions
     WHERE reference_type='agent_run' ORDER BY created_at DESC LIMIT 3\""
```
预期：多一行 `reference_id = <run_id>`、`amount = -ceil(自身花费)`。⚠️ 表名是 `point_transactions`（契约写的 `point_consumption_log` 不存在）。

再验急停：把 `AGENT_POINTS_CHARGE_ENABLED=false` 写进 `secrets/backend.env`、`docker compose up -d backend worker`（**不是** `restart` —— env_file 改动 restart 不重读），跑第二回合，确认 `point_transactions` 不增、`ai_usage_logs` 照增。验完删掉那一行，让 `config.yml` 重新成为唯一来源。

- [ ] **Step 7: 交接给 Part B（两条事实，不是建议）**

1. `RunRecorder._finish` 已在读 `views.get("efficiency")` 的 `tool_calls` / `tool_errors` / `deliverables` 三个键 —— Part B Task 8 的 fold 输出必须用这三个名字，它的消费方已经存在。
2. 契约里「读 `point_consumption_log` 拿 `charged_points`」要改成 `public.point_transactions`，谓词 `reference_type = 'agent_run' AND type = 'consume' AND reference_id = str(run_id)`，取值 `abs(amount)`。

---

# Part B — 指标与效率读面（Task 8–12）

把「一次 run 干了多少活」变成 `agent_runs` 上的五列，再接进三个读面（议题驾驶舱 / 团队用量 /
个人用量）。**前置**：Task 8 起步前 Part A Task 1（mig 472 + ORM）必须已 merge——`AgentRuns.steps /
tool_calls / tool_errors / deliverables / turn_end_reason` 与 `AiUsageHourly` 五个计数列的 ORM 属性
来自那里。Task 9–11 只依赖 Task 8。worktree 一律从 `origin/master` 建，git 命令一律 `git -C <绝对路径>`。

## 与契约的三处偏离（Task 里按这里的口径写）

1. **`point_consumption_log` 这张表不存在。** 真账本是 `public.point_transactions`（ORM
   `PointTransactions`，`app/models/billing.py:167`）：`check_and_consume` 经 `create_transaction`
   落一行 `type='consume'`、`reference_type=<action_type>`、`reference_id=str(run_id)`、`amount`
   为负 Integer。本段按这张表写：`WHERE type='consume' AND reference_type='agent_run' AND
   reference_id IN (...)`，`charged_points = -SUM(amount)`。
   ORM 模型已存在并从 `app.models` 导出（`app/models/__init__.py:80`），所以两处读法都是 ORM，
   **不需要** `scoped_sql(system=True, ...)`。
2. **`folds/efficiency.py` 不能用 `@register`。** `run_projection.register`（:125-133）对重复
   event_type **raise**，而 `tool_call` / `step_end` / `deliverable` / `turn_end` 四族各自已有主
   fold。Task 8 新增并列的计数注册道 `register_counter`，「一族一主 fold」不变量保留。
3. **前端类型叫 `IssueProgress`**（`frontend/services/issuesService.ts:394`），不叫 `IssueRollup`。

---

### Task 8: `tool_call` 补耗时与错误码 + efficiency 计数道 + run 行五列

三个 `tool_call` 发射点（`agent_runner.py:1048 / 1275 / 2160`）各自手搓同形 payload，都没有耗时与
类型化错误码，于是工作量只能去读那张无人读的 `agent_run_events`（recon A6）。本 Task 收成一个发射
器、补两字段、折出五个计数、写进 run 行，并摘掉 `CostAuditorHook`（那张孤儿表的唯一写方，delta 靠
进程内 LRU 算，多 worker 下本就是错的）。

**Files:**
- Create `backend/app/services/ai/runner/tool_events.py`、`backend/app/services/ai/runner/folds/efficiency.py`
- Modify `backend/app/services/ai/runner/run_projection.py`（`empty_views` :36-89、`_REGISTRY` :31、`register` :125-133、`apply` :141-155、fold 导入表 :167-182）
- Modify `backend/app/services/ai/runner/agent_runner.py`（:887 / :1048-1057 / :1275-1279 / :1988 / :2160-2169）
- Modify `backend/app/services/ai/runner/run_recorder.py`（`_finish` 的 `updates` :846-854）、`interrupted_turn.py`（`close_interrupted_run` :40-54）
- Modify `backend/app/services/ai/chat/ai_library_chat_wiring.py`（:11 docstring、:49 import、:262-266）、`backend/app/startup/agent_framework_init.py`（:85-92）
- Delete `backend/app/services/infra/hooks/cost_auditor.py`、`backend/tests/test_cost_auditor.py`
- Test: Create `backend/tests/runner/test_tool_call_single_emitter.py`、`test_fold_efficiency.py`、`test_run_recorder_efficiency_columns.py`；Modify `backend/tests/runner/test_interrupted_turn.py`、`backend/tests/test_ai_library_chat_wiring.py`（:115-129）

**Interfaces:**
- Produces `tool_events.emit_tool_call(recorder, *, tool: str, args: Any, result: Any, iteration: int, duration_ms: int, error_code: str | None) -> None`、`tool_events.tool_error_code(result: Any) -> str | None`
- Produces wire `tool_call` payload `{tool, args, result, iteration, duration_ms: int, error_code: str | None}`
- Produces `run_projection.register_counter(event_type: str) -> Callable[[Fold], Fold]`
- Produces `views["efficiency"] = {"steps": int, "tool_calls": int, "tool_errors": int, "deliverables": int, "turn_end_reason": str | None}`
- Produces `RunRecorder._efficiency_counts() -> dict[str, Any]`（Part A 的 A1 票用它给 `record_usage` 传四个计数；无 event writer 时返回全零，A1 永远拿得到可加的 dict）
- Consumes `AgentRuns` 五列（Part A Task 1）、`folds/deliverables.py` 的 `seen` 去重簿记（不改那个模块）

- [ ] **Step 1: 建 worktree**
  ```bash
  git -C /Volumes/program/project-code/repos/nous-app worktree add -b feature/3c-t8-run-efficiency-metrics \
    /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t8 origin/master
  ```
- [ ] **Step 2: 失败测试 — 发射器与错误码判据**（`backend/tests/runner/test_tool_call_single_emitter.py`）
  ```python
  """一个 ``tool_call`` 发射器（3c §3.2）。三处手搓同形 payload 的代价已经见过一次：
  ``stream_turn`` 的 trace 曾整个缺席，生产唯一路径上的 FinishIssue 声明静默消失。"""
  import ast
  from pathlib import Path

  import pytest

  from app.services.ai.runner.tool_events import emit_tool_call, tool_error_code

  pytestmark = pytest.mark.unit
  RUNNER = Path(__file__).resolve().parents[2] / "app" / "services" / "ai" / "runner"

  def _literal_call_lines(path, literal):
      """调用的第二个位置参数是该字面量的行号。ast 而不是正则——注释里的字面量、
      跨行调用、以及它作为 dict 键出现都不该误判。"""
      tree = ast.parse(path.read_text())
      return [n.lineno for n in ast.walk(tree)
              if isinstance(n, ast.Call) and len(n.args) >= 2
              and isinstance(n.args[1], ast.Constant) and n.args[1].value == literal]

  def test_no_runner_module_but_the_emitter_passes_the_literal_to_emit():
      """守卫：第四处裸发发不出去。folds/ 里出现该字面量是读方，所以只扫顶层模块。"""
      offenders = [f"{p.name}:{ln}" for p in sorted(RUNNER.glob("*.py"))
                   if p.name != "tool_events.py" for ln in _literal_call_lines(p, "tool_call")]
      assert offenders == [], f"route these through tool_events.emit_tool_call: {offenders}"

  @pytest.mark.parametrize("result,expected", [
      ({"error_code": "tool_timeout"}, "tool_timeout"),
      ({"outcome": "denied"}, "denied"),
      ({"outcome": "ok"}, None),
      ({"error": "boom"}, "tool_error"),
      ({"ok": True}, None),
      ("a string result", None),
      # 「没有 error 键」是成功；「error 键是 None」是调用方在说这里该有个错。
      ({"error_code": None, "error": None}, "tool_error"),
  ])
  def test_tool_error_code_reads_the_three_shapes(result, expected):
      assert tool_error_code(result) == expected

  class _Rec:
      def __init__(self):
          self.events = []

      async def record_event(self, event_type, payload, *, turn=None, step=None):
          self.events.append((event_type, payload, turn, step))

  async def test_emit_tool_call_writes_the_full_payload():
      rec = _Rec()
      await emit_tool_call(rec, tool="GenerateShotImage", args={"shot_id": 9},
                           result={"outcome": "ok"}, iteration=3, duration_ms=812,
                           error_code=None)
      assert rec.events == [("tool_call", {
          "tool": "GenerateShotImage", "args": {"shot_id": 9}, "result": {"outcome": "ok"},
          "iteration": 3, "duration_ms": 812, "error_code": None}, None, None)]
      # 遥测永不打断回合（events.emit 的既有契约，这里只是不绕过它）。
      await emit_tool_call(None, tool="X", args={}, result={}, iteration=1,
                           duration_ms=0, error_code=None)
  ```
- [ ] **Step 3: 跑，看红**
  ```bash
  cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t8/backend && uv run pytest tests/runner/test_tool_call_single_emitter.py -q
  ```
  期望 collection error：`ModuleNotFoundError: No module named 'app.services.ai.runner.tool_events'`。
- [ ] **Step 4: 写发射器**（`backend/app/services/ai/runner/tool_events.py`）
  ```python
  """``tool_call`` 的唯一发射点（三期 3c §3.2）。

  两个新字段：``duration_ms``（单调钟包住**工具执行本身**，不含 PreToolUse 钩子与结果
  裁剪）、``error_code``（类型化失败码，成功是 None）。

  ⚠️ import 写成 ``from ...events import emit``（不是 ``emit as emit_event``）：
  ``tests/runner/test_events_single_entry.py::test_no_second_best_effort_emit_helper_is_defined``
  扫所有 ``emit_*`` 定义，要求函数体里出现字面量 ``emit(``；``emit_event(`` 不含这个子
  串，改名即触发守卫。
  """
  from __future__ import annotations

  from typing import Any, Optional

  from app.services.ai.runner.events import emit

  TOOL_CALL_EVENT_TYPE = "tool_call"

  def tool_error_code(result: Any) -> Optional[str]:
      """优先级：处理方给的 ``error_code`` > 非 ``ok`` 的 ``outcome`` > 存在 ``error``
      键（退化成通用 ``tool_error``）。非 dict 一律 None——「读不懂」不等于「失败」，
      算成错误会让工具错误率虚高。"""
      if not isinstance(result, dict):
          return None
      code = result.get("error_code")
      if isinstance(code, str) and code:
          return code
      outcome = result.get("outcome")
      if isinstance(outcome, str) and outcome and outcome != "ok":
          return outcome
      return "tool_error" if "error" in result else None

  async def emit_tool_call(recorder: Any, *, tool: str, args: Any, result: Any,
                           iteration: int, duration_ms: int,
                           error_code: Optional[str]) -> None:
      """把一次已执行（或已判定为未执行）的工具调用写上 transcript。"""
      await emit(recorder, TOOL_CALL_EVENT_TYPE, {
          "tool": tool, "args": args, "result": result, "iteration": iteration,
          "duration_ms": duration_ms, "error_code": error_code})

  __all__ = ["TOOL_CALL_EVENT_TYPE", "emit_tool_call", "tool_error_code"]
  ```
- [ ] **Step 5: 改三处发射点**（`agent_runner.py`）
  :38 之后加 `from app.services.ai.runner.tool_events import emit_tool_call, tool_error_code`。
  **① stream_turn**：:885 `inc_metric("loop_guard_observed")` 与 :887 `if tool_name == "Skill":`
  之间插 `                _tool_t0 = _time.monotonic()`（三个 dispatch 分支都从这里开始）；
  :1048-1057（含上方 `# P3 transcript (mig 285): mirror of run_turn's tool event.` 注释）换成：
  ```python
                  # P3 transcript (mig 285) — 3c §3.2 起走 tool_events 的唯一发射点；
                  # 耗时只包工具本身（钩子与图片裁剪在此之外）。
                  await emit_tool_call(
                      recorder, tool=tool_name, args=args, result=result,
                      iteration=iteration,
                      duration_ms=int((_time.monotonic() - _tool_t0) * 1000),
                      error_code=tool_error_code(result))
  ```
  **② AskUser 泊车后的未执行分支**：:1275-1279 换成：
  ```python
              await emit_tool_call(
                  recorder, tool=name, args={}, result=result, iteration=iteration,
                  # 没执行过所以没有耗时；``skipped`` 结果自带 ``error`` 键，会被算进
                  # 工具错误——这是想要的：被腰斩的回合里那几个调用确实没成。
                  duration_ms=0, error_code=tool_error_code(result))
  ```
  **③ run_turn**：:1988 `# ── Tool dispatch ───` 注释行**下面**插
  `                _tool_t0 = _time.monotonic()`；:2160-2169（含上方三行注释）换成：
  ```python
                  # P3 transcript (mig 285) — 3c §3.2：与 stream_turn 同一发射点。
                  # record_event 仍会截断过长的 args/result。
                  await emit_tool_call(
                      recorder, tool=tool_name, args=args, result=result,
                      iteration=iteration,
                      duration_ms=int((_time.monotonic() - _tool_t0) * 1000),
                      error_code=tool_error_code(result))
  ```
- [ ] **Step 6: 跑绿**
  ```bash
  cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t8/backend && uv run pytest \
    tests/runner/test_tool_call_single_emitter.py tests/runner/test_fold_tools.py \
    tests/runner/test_tool_exec.py tests/runner/test_events_single_entry.py -q
  ```
  全绿。`test_events_single_entry` 必须同跑——它是 Step 4 那条 import 纪律的执行者。
- [ ] **Step 7: 失败测试 — efficiency 计数道**（`backend/tests/runner/test_fold_efficiency.py`）
  ```python
  """``views["efficiency"]``（3c §3.2）：一次 run 干了多少活。四族各自已有主 fold
  （``register`` 对重复注册 raise），所以计数走 ``register_counter`` 这条并列道。"""
  import pytest

  from app.services.ai.runner.run_projection import empty_views, replay

  pytestmark = pytest.mark.unit

  def test_an_empty_run_counts_nothing_and_has_no_reason():
      assert empty_views()["efficiency"] == {
          "steps": 0, "tool_calls": 0, "tool_errors": 0, "deliverables": 0,
          "turn_end_reason": None}

  def test_steps_tools_errors_and_outputs_all_count():
      views = replay([
          ("step_start", {"turn": 1, "step": 1, "model": "m"}),
          ("step_end", {"turn": 1, "step": 1, "cost_cents": 0.5, "model": "m"}),
          ("tool_call", {"tool": "ListShots", "error_code": None}),
          ("tool_call", {"tool": "UpdateShot", "error_code": "tool_error"}),
          ("deliverable", {"kind": "script_shot", "ref_id": "9", "version": 1}),
          ("turn_end", {"reason": "completed"})])
      assert views["efficiency"] == {
          "steps": 1, "tool_calls": 2, "tool_errors": 1, "deliverables": 1,
          "turn_end_reason": "completed"}
      # 主 fold 的既有语义一个字不改：计数道与它并存，不替换它。
      assert views["view"]["tools"] == {"timed_out": 0, "last_timed_out": None}

  def test_only_the_error_code_field_counts_as_an_error():
      """发射器已经判过一次，折叠不再重判——两处判据会各自漂移。"""
      views = replay([("tool_call", {"tool": "A", "error_code": "tool_timeout"}),
                      ("tool_call", {"tool": "B", "error_code": None}),
                      ("tool_call", {"tool": "C", "result": {"error": "boom"}})])
      assert (views["efficiency"]["tool_calls"], views["efficiency"]["tool_errors"]) == (3, 1)

  def test_a_repeated_or_malformed_deliverable_does_not_count():
      """去重键就是 ``folds/deliverables.py`` 判过的那一个——与花费同口径：重复到达
      （DBOS 重放、迟到事件）既不多计一件产出，也不多计一次工作量。"""
      ok = ("deliverable", {"kind": "generated_media", "ref_id": "1", "version": 1})
      bad = ("deliverable", {"kind": "script_shot", "ref_id": "9", "version": True})
      assert replay([ok, ok, bad])["efficiency"]["deliverables"] == 1

  def test_the_last_turn_end_with_a_reason_wins():
      """sweeper 补的 ``interrupted`` 到得最晚，它就是最终结论；而一个没有 reason 的
      turn_end 不该把已知结论抹掉。"""
      views = replay([("turn_end", {"reason": "completed"}),
                      ("turn_end", {"reason": "interrupted"}), ("turn_end", {})])
      assert views["efficiency"]["turn_end_reason"] == "interrupted"
  ```
- [ ] **Step 8: 跑，看红**
  ```bash
  cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t8/backend && uv run pytest tests/runner/test_fold_efficiency.py -q
  ```
  期望每条 `KeyError: 'efficiency'`。
- [ ] **Step 9: 加计数注册道**（`run_projection.py`）
  `empty_views()` 的 `"cost"` 之后加第三个视图：
  ```python
          # 3c §3.2：与 ``cost`` 并列的计数道。花费回答「花了多少钱」，这里回答「干了
          # 多少活」——两者分母不同（花费是树总额、计数是自身量），合进一个字典必然
          # 有人取错分母。``_finish`` 把这五个值落进 agent_runs 同名列。
          "efficiency": {"steps": 0, "tool_calls": 0, "tool_errors": 0,
                         "deliverables": 0, "turn_end_reason": None},
  ```
  `_REGISTRY` 声明（:31）下面加：
  ```python
  # 计数道：与主 fold 并列，同一事件可以有多个。主 fold 负责「这个族的整值视图」，一
  # 族只许一个（``register`` 对重复 raise）；计数只是在同一批事件上加法，没有整值语
  # 义，所以允许多个并存且**不**替换主 fold。
  _COUNTERS: dict[str, list[Fold]] = {}
  ```
  `register` 之后加（`registered_types` 必须并起两道：`test_fold_fork.py` 用它比对 ORM CHECK
  白名单，漏并会在将来订到新类型时让守卫失明）：
  ```python
  def register_counter(event_type: str) -> Callable[[Fold], Fold]:
      """注册一个计数道折叠。主 fold 之后运行，拿到同一个可变副本。"""

      def deco(fn: Fold) -> Fold:
          _COUNTERS.setdefault(event_type, []).append(fn)
          return fn

      return deco

  def registered_types() -> tuple[str, ...]:
      return tuple(sorted(set(_REGISTRY) | set(_COUNTERS)))
  ```
  `apply` 整个替换：
  ```python
  def apply(views: Views, event_type: str, payload: dict[str, Any],
            *, seq: int | None = None) -> Views:
      """Pure: ``views`` is never mutated. Unknown event → same object.

      主 fold 与计数道都跑：主 fold 说「没什么好说的」（返回 None）时计数道仍要计——
      一次没超时的 ``tool_call`` 对 ``view.tools`` 无话可说，对工作量却是实打实的一
      次。两道都没改动才返回原对象（dsh「同一引用 = 零下游工作」）。
      """
      fold = _REGISTRY.get(event_type)
      counters = _COUNTERS.get(event_type) or ()
      if fold is None and not counters:
          return views
      nxt = copy.deepcopy(views)
      changed = False
      if fold is not None:
          folded = fold(nxt, payload or {})
          if folded is not None:
              nxt, changed = folded, True
      for counter in counters:
          counted = counter(nxt, payload or {})
          if counted is not None:
              nxt, changed = counted, True
      if not changed:
          return views
      if seq is not None:
          nxt["view"]["revision"] = seq
      return nxt
  ```
  文件底部 fold 导入表 `deliverables,` 之后加 `efficiency,`；`__all__` 加 `"register_counter"`。
- [ ] **Step 10: 写 efficiency 计数道**（`backend/app/services/ai/runner/folds/efficiency.py`）
  ```python
  """四个事件族 → ``views["efficiency"]``（三期 3c §3.2）。

  这是**计数道**不是主 fold：四族各自已有主 fold，而 ``register`` 对重复注册 raise。

  五个值：``steps``（``step_end`` 计数——开了没结的步不是干完的活）、``tool_calls`` /
  ``tool_errors``（错误码由发射点判定，这里只加法，两处判据必然漂移）、``deliverables``
  （与花费同一个去重键）、``turn_end_reason``（最后一个说得出理由的 turn_end 胜出）。
  存量行 NULL，UI 显示 ``—``——**NULL 不是 0**：没有这五列的旧 run 不等于一次没调过工具。
  """
  from app.services.ai.runner.run_projection import register_counter

  _EMPTY = {"steps": 0, "tool_calls": 0, "tool_errors": 0, "deliverables": 0,
            "turn_end_reason": None}

  def _bump(views, field):
      eff = {**_EMPTY, **(views.get("efficiency") or {})}
      eff[field] = int(eff.get(field) or 0) + 1
      views["efficiency"] = eff
      return views

  @register_counter("step_end")
  def count_step(views, payload):
      return _bump(views, "steps")

  @register_counter("tool_call")
  def count_tool_call(views, payload):
      views = _bump(views, "tool_calls")
      code = payload.get("error_code")
      return _bump(views, "tool_errors") if isinstance(code, str) and code else views

  @register_counter("deliverable")
  def count_deliverable(views, payload):
      """只在主 fold 认账的那一次计数。

      ``fold_deliverable`` 在同一个副本上原地更新 ``view.outputs``（计数道在它之后运
      行），所以「这次算不算新的」直接读它的簿记，不重判一遍。重判等于第二套去重逻
      辑，而 3b 已经为「前端按去重卡数、后端按事件数」记过一张票。
      """
      total = int((views["view"].get("outputs") or {}).get("total") or 0)
      counted = int((views.get("efficiency") or {}).get("deliverables") or 0)
      return _bump(views, "deliverables") if total > counted else None

  @register_counter("turn_end")
  def count_turn_end(views, payload):
      reason = payload.get("reason")
      if not isinstance(reason, str) or not reason:
          return None
      views["efficiency"] = {**_EMPTY, **(views.get("efficiency") or {}),
                             "turn_end_reason": reason}
      return views
  ```
- [ ] **Step 11: 跑绿** — `cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t8/backend && uv run pytest tests/runner/ -q`
  整个 `tests/runner/` 都要跑：`apply` 的返回契约变了，`test_run_projection.py` 与
  `test_outputs_refold_from_transcript.py` 是那条契约的现有消费方。
- [ ] **Step 12: 失败测试 — 五列落 run 行**（`backend/tests/runner/test_run_recorder_efficiency_columns.py`）
  ```python
  """``_finish`` 把 efficiency 视图写进 agent_runs 五列（3c §3.2）。与 ``cost_cents``
  同一次 UPDATE：两者来自同一份折叠，分两次写就有一半落空的窗口。没有折叠数据时**不
  写**这五列而不是写 0——「一次工具都没调」与「早于本期」必须分得开。"""
  import contextlib
  from uuid import uuid4

  import pytest

  from app.services.ai.runner.run_recorder import RunRecorder

  pytestmark = pytest.mark.unit
  COST = {"own_cents": 1.0, "by_child": {}, "media_cents": 0.0}

  class _Writer:
      def __init__(self, views):
          self.views = views

      async def refold_external_slices(self):
          return None

  def _recorder(monkeypatch, captured, views):
      class _S:
          async def execute(self, stmt):
              captured["params"] = dict(stmt.compile().params)
              return None

      @contextlib.asynccontextmanager
      async def _ws():
          yield _S()

      monkeypatch.setattr("app.db.session.write_scope", _ws)
      rec = RunRecorder(agent_id=uuid4(), user_id=uuid4(), trigger="chat")
      rec.run_id = "777"
      if views is not None:
          rec._event_writer = _Writer(views)
      return rec

  async def test_finish_writes_all_five_counters(monkeypatch):
      captured: dict = {}
      rec = _recorder(monkeypatch, captured, {"cost": COST, "efficiency": {
          "steps": 3, "tool_calls": 7, "tool_errors": 2, "deliverables": 4,
          "turn_end_reason": "completed"}})
      await rec._finish(status="completed")
      p = captured["params"]
      assert (p["steps"], p["tool_calls"], p["tool_errors"], p["deliverables"]) == (3, 7, 2, 4)
      assert p["turn_end_reason"] == "completed"

  async def test_a_reasonless_run_writes_counters_but_no_reason(monkeypatch):
      """被 kill 的 run 没有 turn_end——四个计数照写，reason 留给 sweeper 补。"""
      captured: dict = {}
      rec = _recorder(monkeypatch, captured, {"cost": COST, "efficiency": {
          "steps": 2, "tool_calls": 1, "tool_errors": 0, "deliverables": 0,
          "turn_end_reason": None}})
      await rec._finish(status="failed")
      assert captured["params"]["steps"] == 2
      assert "turn_end_reason" not in captured["params"]

  async def test_a_run_without_folded_views_leaves_the_columns_alone(monkeypatch):
      """NULL ≠ 0：这五列根本不在 SET 子句里。而 A1 票要把计数当整数相加，所以
      ``_efficiency_counts`` 在同一情况下必须给出可加的零，不该让它先判空。"""
      captured: dict = {}
      rec = _recorder(monkeypatch, captured, None)
      await rec._finish(status="failed", error_code="boom")
      assert "steps" not in captured["params"]
      assert rec._efficiency_counts() == {
          "steps": 0, "tool_calls": 0, "tool_errors": 0, "deliverables": 0,
          "turn_end_reason": None}
  ```
- [ ] **Step 13: 跑，看红** — `cd .../3c-t8/backend && uv run pytest tests/runner/test_run_recorder_efficiency_columns.py -q`
  期望 `KeyError: 'steps'` 与 `AttributeError: 'RunRecorder' object has no attribute '_efficiency_counts'`。
- [ ] **Step 14: 实现五列写入**（`run_recorder.py`）
  `async def _finish` 定义上方加方法：
  ```python
      def _efficiency_counts(self) -> dict[str, Any]:
          """折叠出来的工作量，永远是一个可加的 dict。没有 event writer（预检即拒的
          run、测试替身）时返回全零——A1 票直接相加，不该先判空。``_finish`` 写列时
          另判一次「有没有折叠数据」，那里 NULL 与 0 必须分得开。"""
          empty = {"steps": 0, "tool_calls": 0, "tool_errors": 0,
                   "deliverables": 0, "turn_end_reason": None}
          if self._event_writer is None:
              return empty
          return {**empty, **(self._event_writer.views.get("efficiency") or {})}
  ```
  `_finish` 里 `updates` dict 之后、`if status in ("completed", "failed"):` 之前插：
  ```python
          # 3c §3.2：与 cost_cents 同一次 UPDATE。存量行留 NULL——一个没有折叠数据的
          # run「不知道干了多少活」，写 0 会把它伪装成「什么都没干」。
          if self._event_writer is not None:
              eff = self._efficiency_counts()
              for column in ("steps", "tool_calls", "tool_errors", "deliverables"):
                  updates[column] = int(eff[column])
              if eff["turn_end_reason"] is not None:
                  updates["turn_end_reason"] = str(eff["turn_end_reason"])
  ```
- [ ] **Step 15: 跑绿** — `cd .../3c-t8/backend && uv run pytest tests/runner/ -q`
- [ ] **Step 16: 失败测试 — sweeper 补 reason**（追加到 `backend/tests/runner/test_interrupted_turn.py`）
  ```python
  def _fake_state(seq, has_end):
      async def _impl(run_id):
          return seq, has_end

      return _impl

  def _fake_writer(captured):
      class _W:
          def __init__(self, run_id, *, seq_start):
              captured["writer"] = (run_id, seq_start)

          async def append(self, event_type, payload, *, turn=None):
              captured["event"] = (event_type, payload, turn)

      return lambda run_id, *, seq_start: _W(run_id, seq_start=seq_start)

  async def test_closing_an_interrupted_run_also_stamps_the_column(monkeypatch):
      """崩溃的 run 不会再跑 ``_finish``，那一列不补就永远空着——而空着在读面上是
      「不知道」，一个被腰斩的回合是知道的。"""
      import app.services.ai.runner.interrupted_turn as it

      captured: dict = {}

      async def _probe(run_id, reason):
          captured["stamp"] = (run_id, reason)

      monkeypatch.setattr(it, "_last_seq_and_has_turn_end", _fake_state(7, False))
      monkeypatch.setattr(it, "_writer_factory", _fake_writer(captured))
      monkeypatch.setattr(it, "_stamp_turn_end_reason", _probe)
      assert await it.close_interrupted_run(777, detail="heartbeat_lost") is True
      assert captured["stamp"] == (777, "interrupted")

  async def test_a_run_that_already_ended_is_not_restamped(monkeypatch):
      import app.services.ai.runner.interrupted_turn as it

      captured: dict = {}

      async def _probe(run_id, reason):
          captured["stamp"] = (run_id, reason)

      monkeypatch.setattr(it, "_last_seq_and_has_turn_end", _fake_state(9, True))
      monkeypatch.setattr(it, "_stamp_turn_end_reason", _probe)
      assert await it.close_interrupted_run(777) is False
      assert "stamp" not in captured
  ```
- [ ] **Step 17: 跑，看红** — `cd .../3c-t8/backend && uv run pytest tests/runner/test_interrupted_turn.py -q`
  期望 `AttributeError: module ... has no attribute '_writer_factory'`。
- [ ] **Step 18: 实现 sweeper 补写**（`interrupted_turn.py`，`_last_seq_and_has_turn_end` 之后）
  ```python
  def _writer_factory(run_id, *, seq_start):
      from app.services.ai.runner.run_recorder import RunEventWriter

      return RunEventWriter(run_id, seq_start=seq_start)

  async def _stamp_turn_end_reason(run_id: int, reason: str) -> None:
      """仅当 ``turn_end_reason`` 还空着时补写。``IS NULL`` 守卫有两个作用：sweeper 重
      放（DBOS 重试）不改写，一个真跑完并自己写了结论的 run 也绝不会被事后改成
      ``interrupted``。"""
      from sqlalchemy import update as sa_update

      from app.db.session import write_scope
      from app.models import AgentRuns

      async with write_scope() as session:
          await session.execute(
              sa_update(AgentRuns).where(AgentRuns.id == int(run_id))
              .where(AgentRuns.turn_end_reason.is_(None))
              .values(turn_end_reason=reason))
  ```
  `close_interrupted_run` 体内 `from ... import RunEventWriter` 与 `writer = RunEventWriter(...)`
  换成 `writer = _writer_factory(run_id, seq_start=last_seq)`；`await writer.append(...)` 之后、
  `return True` 之前加：
  ```python
      # 3c §3.2：事件与列一起补，否则崩溃的 run 那一列永远空着。
      await _stamp_turn_end_reason(int(run_id), TurnEndReason.INTERRUPTED.value)
  ```
- [ ] **Step 19: 跑绿** — `cd .../3c-t8/backend && uv run pytest tests/runner/test_interrupted_turn.py -q`
- [ ] **Step 20: 摘除 `CostAuditorHook`**
  ```bash
  git -C /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t8 rm \
    backend/app/services/infra/hooks/cost_auditor.py backend/tests/test_cost_auditor.py
  ```
  `ai_library_chat_wiring.py`：删 :49 的 import 与 :262-266 的 `registry.register_post(CostAuditorHook(), …)`
  整块，docstring :11 提到 CostAuditor 的那句改成「每个工具调用一行审计的 CostAuditor 已于 3c §3.2
  摘除——它写的 agent_run_events 零个读方，delta 还靠进程内 LRU 算（多 worker 下必然偏大）。同样的
  原料现在走 tool_call 事件的 duration_ms / error_code，折进 agent_runs 的五列。表本身不动（DROP 走
  单独迁移，等一个发布周期确认无人读）。」
  `startup/agent_framework_init.py`：删 :85 的 import 与 :89-92 的 register 块（保留 `MemoryHarvesterHook`
  那一支与外层 try）。`tests/test_ai_library_chat_wiring.py` :115-129 的用例改名为
  `test_post_hooks_include_memory_harvester_and_no_cost_auditor`，断言改为：
  ```python
      post_names = [e.name for e in stack.runner.hooks.get_post_hooks()]
      assert "memory_harvester" in post_names
      # 3c §3.2：摘除后不许悄悄回来——它是 agent_run_events 的唯一写方。
      assert "cost_auditor" not in post_names
  ```
- [ ] **Step 21: 全量跑 + lint**
  ```bash
  cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t8/backend && uv run pytest \
    tests/runner tests/test_ai_library_chat_wiring.py tests/test_streaming.py -q \
    && uv run ruff check . && uv run black --check . && uv run isort --check-only .
  ```
  `test_streaming.py:397`（PostToolUse 在流式路径上会 fire）仍应绿——它验的是链在跑，不是链里有谁。
- [ ] **Step 22: 突变记录**
  ① `count_tool_call` 的 `tool_errors` 那一支改成恒 `return views`，跑
  `uv run pytest tests/runner/test_fold_efficiency.py -q` → 期望
  `test_steps_tools_errors_and_outputs_all_count` 与 `test_only_the_error_code_field_counts_as_an_error` 红。
  ② `apply` 里 `for counter in counters:` 循环整个删掉，跑同一文件 → 期望 `KeyError` / 计数恒零。
  两次都恢复，记进 PR 描述。
- [ ] **Step 23: commit + PR**
  ```bash
  git -C /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t8 add -A
  git -C /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t8 commit -m "feat(runner): tool_call 补耗时与错误码，efficiency 计数道落 run 行五列

三处手搓的 tool_call payload 收成 tool_events.emit_tool_call（ast 守卫钉住第四处发不出
去），补 duration_ms 与 error_code；新增与主 fold 并列的计数注册道，四族折出
steps/tool_calls/tool_errors/deliverables/turn_end_reason，_finish 与 cost_cents 同一次
UPDATE 写进 agent_runs；sweeper 关闭被腰斩的 run 时同步补 turn_end_reason。
CostAuditorHook 摘除（agent_run_events 零读方，delta 靠进程内 LRU）。"
  git -C /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t8 push -u origin feature/3c-t8-run-efficiency-metrics
  cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t8 && gh pr create --base master \
    --title "feat(runner): tool_call 耗时/错误码 + efficiency 五列落 run 行（3c §3.2）" --fill
  ```

---

### Task 9: `issue.rollup.efficiency` + `runs[].charged_points` + 驾驶舱两格

驾驶舱有 `started_at`+`ended_at` 却从不算耗时，有 Outputs 计数却不知道每件产出多少钱（recon C14）。
⚠️ 积分表是 `point_transactions`（见开头偏离 1）。

**Files:**
- Modify `backend/app/repositories/agent_runs_repository.py`（`spent_cents_for_issue` :822-855 之后新增；:38 的 sqlalchemy import 加 `and_`）
- Modify `backend/app/repositories/points_repository.py`（`get_transactions` :770 之前新增；:191 import 加 `func`）
- Modify `backend/app/services/issues/issue_rollup.py`（`compute_rollup` :79-165、`load_rollup` :173-197）
- Modify `frontend/services/issuesService.ts`（`IssueProgressRun` :382-393、`IssueProgress` :395-421）、`frontend/components/Todolist/blocks/CockpitBlock.tsx`（Tools 格 :315-320、Outputs 格 :331-343）
- Test: Create `backend/tests/services/issues/test_rollup_efficiency.py`；Modify `frontend/components/Todolist/blocks/CockpitBlock.test.tsx`

**Interfaces:**
- Produces `AgentRunsRepository.efficiency_for_issue(issue_id: int) -> dict`（`{runs, steps, tool_calls, tool_errors, deliverables, avg_run_ms, turn_end_reasons}`）
- Produces `PointsRepository.charged_points_for_references(*, reference_type: str, reference_ids: list[str]) -> dict[str, float]`（键 `reference_id`，值正数积分；没扣过的 id 不出现）
- Produces `compute_rollup(..., efficiency: dict | None = None, charged_points: dict[str, float] | None = None)`
- Produces wire `rollup.efficiency`（上述七键 + `cost_per_deliverable_cents`）、`rollup.runs[i].charged_points: float | null`
- Consumes `AgentRuns` 五列（Task 8）、`PointTransactions`（既有）

- [ ] **Step 1: 建 worktree**
  ```bash
  git -C /Volumes/program/project-code/repos/nous-app worktree add -b feature/3c-t9-rollup-efficiency \
    /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t9 origin/master
  ```
- [ ] **Step 2: 失败测试 — rollup**（`backend/tests/services/issues/test_rollup_efficiency.py`）
  ```python
  """``issue.rollup.efficiency``（3c §3.3）。计数按**全体** run 求和（root + children：
  计数是自身量，不像花费那样父行已含子行）；``cost_per_deliverable_cents`` 的分子沿用
  rollup 已有的 root-only 树总额。两个口径不同是故意的。"""
  import pytest

  from app.services.issues.issue_rollup import compute_rollup

  pytestmark = pytest.mark.unit
  ISSUE = {"id": 5, "status": "in_progress", "budget_cents": None}
  EFF = {"runs": 3, "steps": 12, "tool_calls": 20, "tool_errors": 3, "deliverables": 4,
         "avg_run_ms": 41000, "turn_end_reasons": {"completed": 2, "interrupted": 1}}

  def _run(run_id, cents):
      return {"id": run_id, "status": "completed", "started_at": None, "ended_at": None,
              "model": "doubao", "cost_cents": cents, "metadata_json": {}}

  def test_efficiency_rides_through_with_cost_per_deliverable():
      out = compute_rollup(ISSUE, [_run(1, 20.0)], [], 0, {"kind": "manual"}, efficiency=EFF)
      assert out["efficiency"] == {**EFF, "cost_per_deliverable_cents": 5.0}

  def test_an_issue_with_no_runs_yet_still_has_the_full_shape():
      """前端无条件读这八个键——缺席的字段会渲成空白格。0 件产出时「每件多少钱」是
      不知道，不是 0：0 会被读成「很便宜」。"""
      out = compute_rollup(ISSUE, [], [], 0, {"kind": "manual"})
      assert out["efficiency"] == {
          "runs": 0, "steps": 0, "tool_calls": 0, "tool_errors": 0, "deliverables": 0,
          "avg_run_ms": None, "cost_per_deliverable_cents": None, "turn_end_reasons": {}}
      spent = compute_rollup(ISSUE, [_run(1, 20.0)], [], 0, {"kind": "manual"},
                             efficiency={**EFF, "deliverables": 0})
      assert spent["efficiency"]["cost_per_deliverable_cents"] is None

  def test_charged_points_land_on_the_matching_run_only():
      """键是字符串 run id（wire 上每个 snowflake 都是 string）。没扣过的是 None——
      「这次没扣」（BYOK / 急停关闭 / 零花费）与「扣了 0」是两回事。"""
      out = compute_rollup(ISSUE, [_run(1, 20.0), _run(2, 5.0)], [], 0,
                           {"kind": "manual"}, charged_points={"1": 21.0})
      assert [(r["id"], r["charged_points"]) for r in out["runs"]] == [("1", 21.0), ("2", None)]
  ```
- [ ] **Step 3: 跑，看红** — `cd .../3c-t9/backend && uv run pytest tests/services/issues/test_rollup_efficiency.py -q`
  期望 `TypeError: compute_rollup() got an unexpected keyword argument 'efficiency'`。
- [ ] **Step 4: 实现 rollup 侧**（`issue_rollup.py`）
  `compute_rollup` 之前加模块常量：
  ```python
  #: 一个还没跑过 run 的议题也得有完整形状——前端无条件读这八个键。
  EMPTY_EFFICIENCY: dict[str, Any] = {
      "runs": 0, "steps": 0, "tool_calls": 0, "tool_errors": 0,
      "deliverables": 0, "avg_run_ms": None, "turn_end_reasons": {}}
  ```
  签名加两个 keyword-only 参数 `efficiency: Optional[dict[str, Any]] = None,` 与
  `charged_points: Optional[dict[str, float]] = None,`；`done_children = ...` 之后加：
  ```python
      # 3c §3.3：计数按全体 run 求和（root + children），因为计数是每个 run 的自身量，
      # 不像 cost_cents 那样父行已含子行。分子 ``spent`` 却是 root-only 的树总额——换
      # 成 root-only 的计数会漏掉子 agent 干的活，换成全体求和的花费会把子 agent 的钱
      # 数两遍。
      eff = {**EMPTY_EFFICIENCY, **(efficiency or {})}
      delivered = int(eff.get("deliverables") or 0)
      eff["cost_per_deliverable_cents"] = round(spent / delivered, 4) if delivered > 0 else None
      points = charged_points or {}
  ```
  返回字典的 `runs` 推导式加一行 `"charged_points": points.get(str(r["id"])),`，并在 `"budget"`
  之前加 `"efficiency": eff,`。
- [ ] **Step 5: 跑绿** — `cd .../3c-t9/backend && uv run pytest tests/services/issues/ -q`
- [ ] **Step 6: 聚合查询**（`agent_runs_repository.py`，`spent_cents_for_issue` 之后）
  ```python
      async def efficiency_for_issue(self, issue_id: int) -> Dict[str, Any]:
          """这个议题上所有 run 的工作量（3c §3.3）。

          **不**加 root 过滤：五个计数是每个 run 的自身量，父行不含子行，全体求和才是
          真数（``spent_cents_for_issue`` 反过来，那里必须 root-only）。一条 SQL 按
          ``turn_end_reason`` 分组，总量在 Python 侧加起来——分布与总量同源。

          ``avg_run_ms`` 的分母只数两端时间戳都有的 run；一个都没有 → None（不知道，
          不是 0 毫秒）。读失败返回 {}：驾驶舱少两个格子，不该把整个议题页拖垮。
          """
          try:
              timed = and_(AgentRuns.started_at.isnot(None), AgentRuns.ended_at.isnot(None))
              stmt = (select(
                  AgentRuns.turn_end_reason.label("reason"),
                  func.count().label("runs"),
                  func.coalesce(func.sum(AgentRuns.steps), 0).label("steps"),
                  func.coalesce(func.sum(AgentRuns.tool_calls), 0).label("tool_calls"),
                  func.coalesce(func.sum(AgentRuns.tool_errors), 0).label("tool_errors"),
                  func.coalesce(func.sum(AgentRuns.deliverables), 0).label("deliverables"),
                  func.count().filter(timed).label("timed_runs"),
                  func.coalesce(func.sum(func.extract(
                      "epoch", AgentRuns.ended_at - AgentRuns.started_at).filter(timed)), 0
                  ).label("total_seconds"),
              ).where(AgentRuns.issue_id == int(issue_id))
                  .group_by(AgentRuns.turn_end_reason))
              async with read_scope() as session:
                  rows = (await session.execute(stmt)).mappings().all()
          except Exception as e:
              logger.error(f"[agent_runs] efficiency_for_issue({issue_id}) failed: {e}")
              return {}
          out: Dict[str, Any] = {"runs": 0, "steps": 0, "tool_calls": 0, "tool_errors": 0,
                                 "deliverables": 0, "turn_end_reasons": {}}
          timed_runs, total_seconds = 0, 0.0
          for row in rows:
              out["runs"] += int(row["runs"])
              for key in ("steps", "tool_calls", "tool_errors", "deliverables"):
                  out[key] += int(row[key] or 0)
              timed_runs += int(row["timed_runs"] or 0)
              total_seconds += float(row["total_seconds"] or 0.0)
              if row["reason"]:
                  out["turn_end_reasons"][str(row["reason"])] = int(row["runs"])
          out["avg_run_ms"] = int(total_seconds * 1000 / timed_runs) if timed_runs else None
          return out
  ```
- [ ] **Step 7: 积分批查**（`points_repository.py`，`get_transactions` 之前）
  ```python
      async def charged_points_for_references(
          self, *, reference_type: str, reference_ids: List[str]
      ) -> Dict[str, float]:
          """这些引用各自真扣掉的积分（正数）。

          真相在 ``point_transactions``（``type='consume'``，``amount`` 为负），不在任
          何效率表里——效率账引用积分账，不复制它。同一引用可能有多行（重试、补扣），
          所以求和。**没扣过的 id 不出现**：调用方读到 None 才能把「没扣」和「扣了 0」
          分开。"""
          wanted = [str(r) for r in reference_ids if r is not None]
          if not wanted:
              return {}
          try:
              stmt = (select(PointTransactions.reference_id,
                             func.sum(PointTransactions.amount).label("amount"))
                      .where(PointTransactions.type == "consume")
                      .where(PointTransactions.reference_type == reference_type)
                      .where(PointTransactions.reference_id.in_(wanted))
                      .group_by(PointTransactions.reference_id))
              async with read_scope() as session:
                  rows = (await session.execute(stmt)).all()
              # 取负而不是 abs()：``type='consume'`` 的行一律是负数，取负正好还原
              # 扣了多少。abs() 会把一个本不该出现的正数悄悄读成扣分，掩盖数据异常。
              return {str(ref): -float(amount or 0) for ref, amount in rows}
          except Exception as e:
              logger.error(f"Failed to read charged points for {reference_type}: {e}")
              return {}
  ```
- [ ] **Step 8: 接进 `load_rollup`**（`issue_rollup.py`，`origin = await resolve_origin(issue)` 之后）
  ```python
      from app.repositories.points_repository import get_points_repository

      efficiency = await get_agent_runs_repository().efficiency_for_issue(issue_id)
      # ``agent_run`` 是 A3 票定的 reference_type。runs 已是 root-only，就是 UI 要显示
      # 的那几行，不必为子 run 多查。
      charged = await get_points_repository().charged_points_for_references(
          reference_type="agent_run", reference_ids=[str(r["id"]) for r in runs])
  ```
  返回行改成 `return compute_rollup(issue, runs, children, pending, origin, last_seq=last_seq,
  efficiency=efficiency, charged_points=charged)`。
- [ ] **Step 9: 跑绿 + lint**
  ```bash
  cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t9/backend && uv run pytest \
    tests/services/issues tests/api/test_issue_progress_router.py -q \
    && uv run ruff check . && uv run black --check . && uv run isort --check-only .
  ```
- [ ] **Step 10: 失败测试 — 驾驶舱两格**（追加到 `frontend/components/Todolist/blocks/CockpitBlock.test.tsx`）
  先给文件里的 `rollup()` 工厂在 `budget` 之后补一行（否则 TS 报缺字段）：
  ```ts
      efficiency: { runs: 0, steps: 0, tool_calls: 0, tool_errors: 0, deliverables: 0, avg_run_ms: null, cost_per_deliverable_cents: null, turn_end_reasons: {} },
  ```
  再追加：
  ```tsx
  describe('CockpitBlockView — efficiency cells (3c §3.3)', () => {
    function withEfficiency(eff: Partial<IssueProgress['efficiency']>): IssueBlockContext {
      const base = ctx('running');
      const r = base.rollup as IssueProgress;
      return { ...base, rollup: { ...r, efficiency: { ...r.efficiency, ...eff },
        current_run: r.current_run
          ? { ...r.current_run, view: { outputs: { total: 4, revised: 1, last: null } } }
          : null } };
    }

    it('shows cost per output next to the outputs count', () => {
      render(<CockpitBlockView ctx={withEfficiency({ deliverables: 4, cost_per_deliverable_cents: 5 })} />);
      expect(screen.getByTestId('cockpit-outputs')).toHaveTextContent('/ output');
    });

    it('shows nothing rather than ¢0.00 when no output has been priced', () => {
      render(<CockpitBlockView ctx={withEfficiency({ deliverables: 0, cost_per_deliverable_cents: null })} />);
      expect(screen.getByTestId('cockpit-outputs')).not.toHaveTextContent('/ output');
    });

    it('surfaces tool errors as their own cell, and hides it when nothing failed', () => {
      const { unmount } = render(<CockpitBlockView ctx={withEfficiency({ tool_calls: 20, tool_errors: 3 })} />);
      expect(screen.getByTestId('cockpit-tool-errors')).toHaveTextContent('3 errors');
      expect(screen.getByTestId('cockpit-tool-errors')).toHaveTextContent('20 calls');
      unmount();
      render(<CockpitBlockView ctx={withEfficiency({ tool_calls: 20, tool_errors: 0 })} />);
      expect(screen.queryByTestId('cockpit-tool-errors')).toBeNull();
    });
  });
  ```
- [ ] **Step 11: 跑，看红** — `cd .../3c-t9/frontend && npx vitest run components/Todolist/blocks/CockpitBlock.test.tsx`
  期望 TS 报 `efficiency` 不在 `IssueProgress` 上 + 三条断言红。
- [ ] **Step 12: 前端类型 + 两格**
  `issuesService.ts`：`IssueProgressRun` 加
  ```ts
    /** 真扣掉的积分（point_transactions 的 consume 流水求和）。null = 没扣过
     *  （BYOK / 急停关闭 / 零花费），不是扣了 0。 */
    charged_points: number | null;
  ```
  `IssueProgress` 在 `budget` 之后加
  ```ts
    /** 3c §3.3：计数是全体 run 求和，cost_per_deliverable_cents 的分子却是 root-only
     *  的树总额——口径差异是后端故意的，前端只显示不重算。 */
    efficiency: {
      runs: number; steps: number; tool_calls: number; tool_errors: number;
      deliverables: number; avg_run_ms: number | null;
      cost_per_deliverable_cents: number | null;
      turn_end_reasons: Record<string, number>;
    };
  ```
  `CockpitBlock.tsx`：与既有 `tools` 等取值同处加 `const eff = rollup.efficiency;`；Outputs 格在
  `{outputs.last?.title && …}` 之前插：
  ```tsx
            {eff?.cost_per_deliverable_cents != null && (
              <span className="text-ink-500 text-[12px]">
                {' · '}
                {t('issueDetail.costPerOutput', '{{c}} / output', { c: formatCents(eff.cost_per_deliverable_cents) })}
              </span>
            )}
  ```
  既有 Tools 格之后加新格（`GRID_COLS` 已有 4–7 档，无需改表）：
  ```tsx
          {eff && eff.tool_errors > 0 && (
            <Cell label={t('issueDetail.toolErrors', 'Tool errors')} testId="cockpit-tool-errors">
              <span className="text-danger">{t('issueDetail.toolErrorCount', '{{n}} errors', { n: eff.tool_errors })}</span>
              <div className="text-[11px] text-ink-500 truncate">
                {t('issueDetail.toolCallCount', '{{n}} calls', { n: eff.tool_calls })}
              </div>
            </Cell>
          )}
  ```
- [ ] **Step 13: 跑绿 + typecheck** — `cd .../3c-t9/frontend && npx vitest run components/Todolist/ && npm run typecheck`
  typecheck 有约 61 条既有错误；触碰的文件零新增即可。
- [ ] **Step 14: 突变记录**
  把 `compute_rollup` 里 `if delivered > 0 else None` 改成 `else 0.0`，跑
  `uv run pytest tests/services/issues/test_rollup_efficiency.py -q` → 期望
  `test_an_issue_with_no_runs_yet_still_has_the_full_shape` 红（`0.0 is not None`）。改回。
- [ ] **Step 15: commit + PR**
  ```bash
  git -C /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t9 add -A
  git -C /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t9 commit -m "feat(issues): 驾驶舱补效率账——每件产出多少钱、工具错了几次、每次 run 扣了多少分

rollup 加 efficiency 聚合（计数全体 run 求和、分子沿用 root-only 树总额，口径差异写在
实现里）与 runs[].charged_points（读 point_transactions 的 consume 流水，不复制一份）；
驾驶舱 Outputs 格加 ¢x / output，新增 Tool errors 格。"
  git -C /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t9 push -u origin feature/3c-t9-rollup-efficiency
  cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t9 && gh pr create --base master \
    --title "feat(issues): rollup.efficiency + charged_points + 驾驶舱两格（3c §3.3）" --fill
  ```

---

### Task 10: `/usage/summary` 新列 + `/ai-library/usage/efficiency` + `/ai-library/runs/costs`

⚠️ **新端点必须自己写边界门**：`agent_runs` 不在 `SCOPE_ENFORCE_*` 强制表清单里（recon F20），SQL
层没有兜底。`/ai-library/usage` 那句「RLS-enforced」注释是错的（它先取全量再在 Python 里 filter，
team scope 无成员校验）；本 Task 不修那个既有端点（范围外，Step 14 记票），新端点一律走
`usage_router` 的真成员门。

**Files:**
- Modify `backend/app/repositories/usage_repository.py`（`summarize` :71-186）、`backend/app/schemas/usage.py`（:10-36）、`backend/app/api/usage_router.py`（`usage_summary` :61-136）
- Create `backend/app/schemas/efficiency.py`
- Modify `backend/app/repositories/agent_runs_repository.py`（Task 9 的 `efficiency_for_issue` 之后）
- Modify `backend/app/api/ai_library_router.py`（`get_usage_daily` :2838-2856 之后加两个端点 + 顶部 import）
- Test: Create `backend/tests/api/test_usage_efficiency_router.py`、`test_run_costs_router.py`；Modify `backend/tests/api/test_usage_router.py`

**Interfaces:**
- Produces wire `/usage/summary` 的 `total` / `groups[i]` / `daily[i]` 多五个计数列，前两者再多 `cost_per_deliverable_cents: float | null`
- Produces `AgentRunsRepository.efficiency_groups(*, frm, to, user_id=None, team_id=None, project_id=None, group_by) -> tuple[list[dict], dict[str, int]]`、`cost_rows_for_ids(ids: list[int]) -> list[dict]`
- Produces `GET /api/v1/ai-library/usage/efficiency?scope=user|team|project&id=&from=&to=&group_by=agent|model` → `EfficiencyResponse`
- Produces `GET /api/v1/ai-library/runs/costs?ids=<csv ≤50>` → `{"items": {"<run_id>": {cost_cents, charged_points, model, status, prompt_tokens, completion_tokens}}}`
- Consumes `AiUsageHourly` 五个计数列（Part A Task 1）、`AgentRuns` 五列（Task 8）、`PointsRepository.charged_points_for_references`（Task 9）、`visible_issue_ids`（既有）

- [ ] **Step 1: 建 worktree**
  ```bash
  git -C /Volumes/program/project-code/repos/nous-app worktree add -b feature/3c-t10-efficiency-endpoints \
    /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t10 origin/master
  ```
- [ ] **Step 2: 失败测试 — 效率端点**（`backend/tests/api/test_usage_efficiency_router.py`）
  ```python
  """``GET /ai-library/usage/efficiency``（3c §3.3）。跨团队汇总在 SQL 层没有兜底
  （agent_runs 不在 SCOPE_ENFORCE 清单里），边界完全由端点自己负责——所以「非成员拿
  404」是本文件的主角，不是附赠。"""
  import pytest
  from fastapi import HTTPException

  from app.api import ai_library_router as R

  pytestmark = pytest.mark.unit
  GROUPS = [{"key": "doubao", "run_count": 10, "failed_runs": 1, "total_ms": 410000,
             "timed_runs": 10, "tool_calls": 40, "tool_errors": 4,
             "deliverables": 8, "cost_cents": 32.0}]
  REASONS = {"completed": 8, "awaiting_input": 1, "error": 1}

  class _Auth:
      user_id = "11111111-1111-1111-1111-111111111111"

  def _stub(monkeypatch, rows=GROUPS, captured=None):
      class _Repo:
          async def efficiency_groups(self, **kw):
              if captured is not None:
                  captured.update(kw)
              return rows, REASONS

      monkeypatch.setattr(R, "get_agent_runs_repository", lambda: _Repo())

  async def test_user_scope_is_hard_locked_and_the_row_carries_rate_and_ratio(monkeypatch):
      captured: dict = {}
      _stub(monkeypatch, captured=captured)
      out = await R.get_usage_efficiency(_Auth(), scope="user", group_by="model")
      assert str(captured["user_id"]) == _Auth.user_id
      assert captured["team_id"] is None and captured["project_id"] is None
      g = out.groups[0]
      assert (g.run_count, g.failed_runs, g.avg_run_ms) == (10, 1, 41000)
      assert (g.tool_error_rate, g.cost_per_deliverable_cents) == (0.1, 4.0)
      assert out.turn_end_reasons == REASONS

  async def test_zero_denominators_read_differently(monkeypatch):
      """0 次调用 → 错误率 0（确定没错过）；0 件产出 → 单价 null（不知道，不是免费）。"""
      _stub(monkeypatch, rows=[{**GROUPS[0], "tool_calls": 0, "tool_errors": 0,
                                "deliverables": 0}])
      g = (await R.get_usage_efficiency(_Auth(), scope="user")).groups[0]
      assert g.tool_error_rate == 0.0 and g.cost_per_deliverable_cents is None

  async def test_team_scope_404s_for_a_non_member(monkeypatch):
      class _Teams:
          async def get_team_by_id(self, team_id, user_id):
              return None

      monkeypatch.setattr(R, "get_team_repository", lambda: _Teams())
      with pytest.raises(HTTPException) as e:
          await R.get_usage_efficiency(_Auth(), scope="team", id=42)
      assert e.value.status_code == 404

  @pytest.mark.parametrize("kwargs,code", [
      ({"scope": "team"}, "scope_requires_id"),
      ({"scope": "user", "group_by": "module"}, "invalid_group_by"),
      ({"scope": "nope"}, "invalid_scope"),
  ])
  async def test_bad_input_is_a_typed_400(kwargs, code):
      with pytest.raises(HTTPException) as e:
          await R.get_usage_efficiency(_Auth(), **kwargs)
      assert e.value.status_code == 400 and e.value.detail["code"] == code
  ```
- [ ] **Step 3: 失败测试 — 批量花费端点**（`backend/tests/api/test_run_costs_router.py`）
  ```python
  """``GET /ai-library/runs/costs``（3c §4.2 的数据源）。可见性 = 自己的 run，或一个自己
  看得见的议题上的 run。**看不见的 id 是键省略，不是 404**——一次批量里混进一个别人的
  run，不该把另外 49 个也打掉。"""
  import pytest
  from fastapi import HTTPException

  from app.api import ai_library_router as R

  pytestmark = pytest.mark.unit
  ME = "11111111-1111-1111-1111-111111111111"
  SOMEONE = "22222222-2222-2222-2222-222222222222"
  ROWS = [
      {"id": 1, "user_id": ME, "issue_id": None, "cost_cents": 12.5, "model": "doubao",
       "status": "completed", "prompt_tokens": 900, "completion_tokens": 120},
      {"id": 2, "user_id": SOMEONE, "issue_id": 77, "cost_cents": 3.0, "model": "qwen",
       "status": "failed", "prompt_tokens": 10, "completion_tokens": 0},
      {"id": 3, "user_id": SOMEONE, "issue_id": 99, "cost_cents": 1.0, "model": "qwen",
       "status": "completed", "prompt_tokens": 5, "completion_tokens": 5},
  ]

  class _Auth:
      user_id = ME

  def _stub(monkeypatch, visible):
      class _Repo:
          async def cost_rows_for_ids(self, ids):
              return [r for r in ROWS if r["id"] in ids]

      class _Points:
          async def charged_points_for_references(self, *, reference_type, reference_ids):
              return {"1": 13.0}

      async def _visible(issue_ids, auth):
          return visible

      monkeypatch.setattr(R, "get_agent_runs_repository", lambda: _Repo())
      monkeypatch.setattr(R, "get_points_repository", lambda: _Points())
      monkeypatch.setattr(R, "visible_issue_ids", _visible)

  async def test_owner_and_visible_issue_rows_come_back(monkeypatch):
      _stub(monkeypatch, {"77"})
      out = await R.get_run_costs(_Auth(), ids="1,2,3")
      assert sorted(out["items"]) == ["1", "2"]
      assert out["items"]["1"] == {"cost_cents": 12.5, "charged_points": 13.0,
                                   "model": "doubao", "status": "completed",
                                   "prompt_tokens": 900, "completion_tokens": 120}
      # 没扣过就是 None，不是 0——BYOK / 急停关闭都长这样。
      assert out["items"]["2"]["charged_points"] is None

  async def test_invisible_and_garbage_ids_are_dropped_not_fatal(monkeypatch):
      _stub(monkeypatch, set())
      assert list((await R.get_run_costs(_Auth(), ids="1,3"))["items"]) == ["1"]
      assert list((await R.get_run_costs(_Auth(), ids="1,,abc, 3 "))["items"]) == ["1"]

  async def test_more_than_fifty_ids_is_a_typed_400():
      with pytest.raises(HTTPException) as e:
          await R.get_run_costs(_Auth(), ids=",".join(str(i) for i in range(51)))
      assert e.value.status_code == 400 and e.value.detail["code"] == "too_many_ids"
  ```
- [ ] **Step 4: 跑，看红**
  ```bash
  cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t10/backend && uv run pytest \
    tests/api/test_usage_efficiency_router.py tests/api/test_run_costs_router.py -q
  ```
  期望 `AttributeError: module 'app.api.ai_library_router' has no attribute 'get_usage_efficiency'`。
- [ ] **Step 5: 效率 schema**（`backend/app/schemas/efficiency.py`）
  ```python
  """``GET /ai-library/usage/efficiency`` 的响应（三期 3c §3.3）。

  比率在服务端算一次：``tool_error_rate`` 与 ``cost_per_deliverable_cents`` 的分母为 0 时
  含义不同——前者「没调过工具」的错误率是 0（确定没错），后者「没产出」的单价是 **null**
  （不知道，不是免费）。放前端各算各的必然有一处写成 0。"""
  from __future__ import annotations

  import datetime
  from typing import Dict, List, Optional

  from pydantic import BaseModel, Field

  class EfficiencyGroup(BaseModel):
      key: str
      label: str
      run_count: int = 0
      failed_runs: int = 0
      avg_run_ms: Optional[int] = None
      tool_calls: int = 0
      tool_errors: int = 0
      tool_error_rate: float = 0.0
      deliverables: int = 0
      cost_cents: float = 0.0
      cost_per_deliverable_cents: Optional[float] = None

  class EfficiencyResponse(BaseModel):
      scope: str
      from_: datetime.datetime = Field(..., alias="from")
      to: datetime.datetime
      groups: List[EfficiencyGroup]
      turn_end_reasons: Dict[str, int]

      model_config = {"populate_by_name": True}
  ```
- [ ] **Step 6: 仓库层两个方法**（`agent_runs_repository.py`，`efficiency_for_issue` 之后）
  ```python
      async def efficiency_groups(
          self, *, frm: datetime, to: datetime, user_id: Optional[UUID] = None,
          team_id: Optional[int] = None, project_id: Optional[int] = None,
          group_by: str = "model",
      ) -> tuple[List[Dict[str, Any]], Dict[str, int]]:
          """按 model 或 agent 分组的效率原料 + 全窗口的 turn_end 分布。

          三个 scope 参数至少一个非空由调用方保证（user 锁调用者，team/project 过成员
          门）。这里**不兜底**——一个没有任何 where 的跨团队全表聚合，是这条链上最贵也
          最危险的查询。返回原始计数而不是比率：分母语义（0 次调用 vs 0 件产出）在路由
          层统一处理一次，两处各算一遍必然漂移。读失败一律 raise（路由转 503）——空结果
          会被读成「这段时间没花钱」。
          """
          key_col = AgentRuns.agent_id if group_by == "agent" else AgentRuns.model
          scope = [AgentRuns.created_at >= frm, AgentRuns.created_at < to]
          if user_id is not None:
              scope.append(AgentRuns.user_id == user_id)
          if team_id is not None:
              scope.append(AgentRuns.team_id == int(team_id))
          if project_id is not None:
              scope.append(AgentRuns.project_id == int(project_id))
          timed = and_(AgentRuns.started_at.isnot(None), AgentRuns.ended_at.isnot(None))
          try:
              rows_stmt = (select(
                  key_col.label("key"),
                  func.count().label("run_count"),
                  func.sum(case((AgentRuns.status.in_(("failed", "heartbeat_lost")), 1),
                                else_=0)).label("failed_runs"),
                  func.count().filter(timed).label("timed_runs"),
                  func.coalesce(func.sum(func.extract(
                      "epoch", AgentRuns.ended_at - AgentRuns.started_at).filter(timed)), 0
                  ).label("total_seconds"),
                  func.coalesce(func.sum(AgentRuns.tool_calls), 0).label("tool_calls"),
                  func.coalesce(func.sum(AgentRuns.tool_errors), 0).label("tool_errors"),
                  func.coalesce(func.sum(AgentRuns.deliverables), 0).label("deliverables"),
                  func.coalesce(func.sum(AgentRuns.cost_cents), 0).label("cost_cents"),
              ).where(*scope).group_by(key_col))
              reasons_stmt = (select(AgentRuns.turn_end_reason, func.count())
                              .where(*scope).where(AgentRuns.turn_end_reason.isnot(None))
                              .group_by(AgentRuns.turn_end_reason))
              async with read_scope() as session:
                  rows = (await session.execute(rows_stmt)).mappings().all()
                  reasons = (await session.execute(reasons_stmt)).all()
          except Exception as e:
              logger.error(f"[agent_runs] efficiency_groups failed: {e}")
              raise
          out = []
          for r in rows:
              d = dict(r)
              d["key"] = "" if d["key"] is None else str(d["key"])
              d["total_ms"] = int(float(d.pop("total_seconds") or 0.0) * 1000)
              out.append(d)
          return out, {str(k): int(v) for k, v in reasons}

      async def cost_rows_for_ids(self, ids: List[int]) -> List[Dict[str, Any]]:
          """这批 run 的花费与归属列。可见性判定在路由层——仓库不认识调用者。"""
          if not ids:
              return []
          stmt = select(AgentRuns.id, AgentRuns.user_id, AgentRuns.issue_id,
                        AgentRuns.cost_cents, AgentRuns.model, AgentRuns.status,
                        AgentRuns.prompt_tokens, AgentRuns.completion_tokens
                        ).where(AgentRuns.id.in_([int(i) for i in ids]))
          try:
              async with read_scope() as session:
                  return [dict(r) for r in (await session.execute(stmt)).mappings().all()]
          except Exception as e:
              logger.error(f"[agent_runs] cost_rows_for_ids failed: {e}")
              raise
  ```
- [ ] **Step 7: 两个端点**（`ai_library_router.py`，`get_usage_daily` 之后；顶部 import 加
  `get_points_repository` / `get_team_repository` / `EfficiencyGroup, EfficiencyResponse` / `visible_issue_ids`）
  ```python
  @router.get("/usage/efficiency", response_model=EfficiencyResponse,
              summary="Run efficiency (turn-end mix, tool error rate, cost per output)")
  async def get_usage_efficiency(
      auth: AuthDep, scope: str = "user", id: int | None = None,
      frm: str | None = Query(None, alias="from"), to: str | None = None,
      group_by: str = "model",
  ) -> EfficiencyResponse:
      """从 ``agent_runs`` 的效率五列出一张按 model / agent 的分组表。

      user scope 硬锁调用者；team scope 过 ``get_team_by_id``（非成员 None → 404，不泄露
      存在性）；project scope 先解析出它的 team 再过同一道门。``agent_runs`` 不在
      SCOPE_ENFORCE 清单里，SQL 层不会兜底——这道门是唯一的一道。
      """
      if scope not in ("user", "team", "project"):
          raise HTTPException(400, detail={"code": "invalid_scope"})
      if group_by not in ("model", "agent"):
          raise HTTPException(400, detail={"code": "invalid_group_by"})
      if scope != "user" and id is None:
          raise HTTPException(400, detail={"code": "scope_requires_id"})
      now = datetime.now(timezone.utc)
      to_dt = _parse_iso(to, default=now)
      frm_dt = _parse_iso(frm, default=to_dt - timedelta(days=30))
      if frm_dt >= to_dt:
          raise HTTPException(400, detail={"code": "invalid_range"})

      user_uuid = team_id = project_id = None
      if scope == "user":
          user_uuid = _coerce_user_uuid(auth.user_id)
      else:
          if scope == "project":
              project = await get_project_repository().get_by_id(int(id))
              gate_team, project_id = (project or {}).get("team_id"), int(id)
          else:
              gate_team, team_id = int(id), int(id)
          if gate_team is None or not await get_team_repository().get_team_by_id(
                  str(gate_team), auth.user_id):
              raise HTTPException(404, detail={"code": "not_found"})
      try:
          rows, reasons = await get_agent_runs_repository().efficiency_groups(
              frm=frm_dt, to=to_dt, user_id=user_uuid, team_id=team_id,
              project_id=project_id, group_by=group_by)
      except Exception as exc:  # noqa: BLE001
          logger.error(f"[usage/efficiency] read failed: {exc}")
          raise HTTPException(503, detail={"code": "efficiency_unavailable"})

      labels = await _agent_labels([r["key"] for r in rows]) if group_by == "agent" else {}
      groups = []
      for r in rows:
          calls, delivered = int(r["tool_calls"] or 0), int(r["deliverables"] or 0)
          timed, cost = int(r["timed_runs"] or 0), float(r["cost_cents"] or 0.0)
          errors = int(r["tool_errors"] or 0)
          groups.append(EfficiencyGroup(
              key=r["key"], label=labels.get(r["key"], r["key"] or "unknown"),
              run_count=int(r["run_count"] or 0), failed_runs=int(r["failed_runs"] or 0),
              avg_run_ms=(int(r["total_ms"] / timed) if timed else None),
              tool_calls=calls, tool_errors=errors,
              # 0 次调用 → 0 的错误率（确定没错过），不是 null。
              tool_error_rate=(round(errors / calls, 4) if calls else 0.0),
              deliverables=delivered, cost_cents=round(cost, 4),
              # 0 件产出 → null（不知道单价），绝不是 0。
              cost_per_deliverable_cents=(round(cost / delivered, 4) if delivered else None)))
      return EfficiencyResponse(scope=scope, **{"from": frm_dt}, to=to_dt,
                                groups=groups, turn_end_reasons=reasons)

  @router.get("/runs/costs", summary="Batch cost + charged points for up to 50 runs")
  async def get_run_costs(auth: AuthDep, ids: str = "") -> Dict[str, Any]:
      """一次拿一屏气泡的花费（3c §4.2）。可见性复用 ``visible_issue_ids``（与血缘同一
      把尺）；**看不见的 id 是键省略**，不是 404。"""
      wanted = [int(t) for t in (s.strip() for s in ids.split(",")) if t.isdigit()]
      if len(wanted) > 50:
          raise HTTPException(400, detail={"code": "too_many_ids"})
      if not wanted:
          return {"items": {}}
      try:
          rows = await get_agent_runs_repository().cost_rows_for_ids(wanted)
      except Exception as exc:  # noqa: BLE001
          logger.error(f"[runs/costs] read failed: {exc}")
          raise HTTPException(503, detail={"code": "run_costs_unavailable"})
      me = str(auth.user_id)
      visible = await visible_issue_ids({r.get("issue_id") for r in rows}, auth)
      allowed = [r for r in rows
                 if str(r.get("user_id")) == me or str(r.get("issue_id")) in visible]
      charged = await get_points_repository().charged_points_for_references(
          reference_type="agent_run", reference_ids=[str(r["id"]) for r in allowed])
      return {"items": {str(r["id"]): {
          "cost_cents": (float(r["cost_cents"]) if r["cost_cents"] is not None else None),
          "charged_points": charged.get(str(r["id"])),
          "model": r["model"], "status": r["status"],
          "prompt_tokens": int(r["prompt_tokens"] or 0),
          "completion_tokens": int(r["completion_tokens"] or 0)} for r in allowed}}
  ```
  `_parse_iso` / `_agent_labels` 若文件里尚无同名 helper，从 `usage_router._parse_dt` 与
  `get_usage_daily` 的 agent 标签富化块各抽一个私有函数复用（两处都已有实现，不新写解析逻辑）。
- [ ] **Step 8: 跑绿**
  ```bash
  cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t10/backend && uv run pytest \
    tests/api/test_usage_efficiency_router.py tests/api/test_run_costs_router.py -q
  ```
- [ ] **Step 9: 失败测试 — `/usage/summary` 新列**（追加到 `backend/tests/api/test_usage_router.py`；
  文件若无这两个替身先在顶部加 `class _Auth: user_id = "1111…"` 与
  `class _TeamOk:` + `async def get_team_by_id(self, team_id, user_id): return {"id": team_id}`）
  ```python
  async def test_summary_carries_the_efficiency_counters(monkeypatch):
      """小时表的五个计数列必须一路到 wire——UI 的六枚 tile 全靠它们。"""
      import app.api.usage_router as R

      counters = {"run_count": 5, "failed_runs": 1, "tool_calls": 20,
                  "tool_errors": 2, "deliverables": 8}

      async def _summarize(**kw):
          return {
              "total": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12,
                        "cached_input_tokens": 0, "cost_cents": 40.0, "event_count": 3,
                        **counters},
              "groups": [{"grp": "doubao", "prompt_tokens": 10, "completion_tokens": 2,
                          "total_tokens": 12, "cost_cents": 40.0, "event_count": 3,
                          **counters}],
              "daily": [{"day": "2026-09-15", "grp": "doubao", "total_tokens": 12,
                         "cost_cents": 40.0, **counters}]}

      monkeypatch.setattr(R.usage_repository, "summarize", _summarize)
      monkeypatch.setattr(R, "get_team_repository", lambda: _TeamOk())
      out = await R.usage_summary(_Auth(), team_id="1")
      assert (out.total.run_count, out.total.tool_errors) == (5, 2)
      assert out.total.cost_per_deliverable_cents == 5.0
      assert out.groups[0].deliverables == 8
      assert out.daily[0].run_count == 5

  async def test_a_window_with_no_outputs_has_a_null_ratio(monkeypatch):
      import app.api.usage_router as R

      async def _summarize(**kw):
          return {"total": {"cost_cents": 40.0, "deliverables": 0}, "groups": [], "daily": []}

      monkeypatch.setattr(R.usage_repository, "summarize", _summarize)
      monkeypatch.setattr(R, "get_team_repository", lambda: _TeamOk())
      assert (await R.usage_summary(_Auth(), team_id="1")).total.cost_per_deliverable_cents is None
  ```
- [ ] **Step 10: 跑，看红** — `cd .../3c-t10/backend && uv run pytest tests/api/test_usage_router.py -q`
  期望 `AttributeError: 'UsageTotals' object has no attribute 'run_count'`。
- [ ] **Step 11: 实现小时表新列**
  `usage_repository.py` 加模块级 helper（避免三个 SELECT 各抄一遍）：
  ```python
  def _counter_cols():
      """五个计数列的 SUM。每个 SELECT 各调一次——Label 实例不可跨 SELECT 复用，理由
      同本模块顶部的 ``_GROUP_KEY_FACTORY``。"""
      from sqlalchemy import func

      from app.models import AiUsageHourly

      return [func.coalesce(func.sum(getattr(AiUsageHourly, name)), 0).label(name)
              for name in ("run_count", "failed_runs", "tool_calls", "tool_errors",
                           "deliverables")]
  ```
  `total` / `groups` / `daily` 三个 `select(...)` 的列表尾部各加 `*_counter_cols()`。
  `schemas/usage.py`：三个模型各加 `run_count / failed_runs / tool_calls / tool_errors /
  deliverables: int = 0`；`UsageTotals` 与 `UsageGroupRow` 再加
  ```python
      #: 0 件产出 → None（不知道单价），不是 0.0。
      cost_per_deliverable_cents: Optional[float] = None
  ```
  `usage_router.py` 加本地 helper 并在三处构造透传：
  ```python
  def _per_output(cost: Any, delivered: Any) -> Optional[float]:
      n = _int(delivered)
      return round(_num(cost) / n, 4) if n > 0 else None
  ```
  `UsageTotals(...)` / `UsageGroupRow(...)` 各加五个 `_int(...)` 计数 +
  `cost_per_deliverable_cents=_per_output(<cost 源>, <deliverables 源>)`；`UsageDailyRow(...)`
  只加五个计数（日行不算单价，图上没有它的位置）。
- [ ] **Step 12: 跑绿 + lint**
  ```bash
  cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t10/backend && uv run pytest \
    tests/api/test_usage_router.py tests/api/test_usage_efficiency_router.py \
    tests/api/test_run_costs_router.py tests/repositories -q \
    && uv run ruff check . && uv run black --check . && uv run isort --check-only .
  ```
- [ ] **Step 13: 突变记录**
  ① `get_usage_efficiency` 的 404 分支改成 `pass`（放行），跑
  `uv run pytest tests/api/test_usage_efficiency_router.py -q` → 期望
  `test_team_scope_404s_for_a_non_member` 红。改回。
  ② `_per_output` 的 `else None` 改成 `else 0.0` → 期望 `test_a_window_with_no_outputs_has_a_null_ratio` 红。改回。
- [ ] **Step 14: commit + PR**
  ```bash
  git -C /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t10 add -A
  git -C /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t10 commit -m "feat(usage): 效率读面三端点——小时表计数列、/usage/efficiency、/runs/costs

/usage/summary 的 total/groups/daily 三处透传小时表五个计数列并给出每件产出单价（0 件 →
null 不是 0）；新增 /ai-library/usage/efficiency（turn_end 分布、平均耗时、工具错误率，
团队与项目 scope 各自过真成员门——agent_runs 不在 SCOPE_ENFORCE 清单里，SQL 层没有兜
底）；新增 /ai-library/runs/costs 批量端点，看不见的 run 是键省略而不是整批 404。仓库层
读失败一律 raise，路由转 503，不伪装成空结果。"
  git -C /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t10 push -u origin feature/3c-t10-efficiency-endpoints
  cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t10 && gh pr create --base master \
    --title "feat(usage): 效率读面三端点（3c §3.3）" --fill
  ```

---

### Task 11: 前端用量页六 tile + turn_end 分布条 + 团队页两 tile

`UsagePage` 的 user scope 今天四枚 tile，`success rate` 读 `status`（于是 `awaiting_input` 被算成失
败，recon C12）。改读 turn_end 分布，补两枚 tile 与一条分布条；团队页同补两枚。

**Files:**
- Modify `frontend/services/usageService.ts`（类型区 :26-79、`usageService` 对象 :81-119）、`frontend/services/aiLibraryService.ts`（`getUsageDaily` :685-700 之后）、`frontend/types.ts`（:2243-2253 之后）
- Modify `frontend/pages/UsagePage.tsx`（state :80-85、`fetchUsage` :97-126、渲染 :234-247、`StatTiles` :489-526）、`frontend/pages/TeamAiUsagePage.tsx`（Totals 网格 :298-304）
- Create `frontend/components/usage/TurnEndBreakdown.tsx`
- Test: Create `frontend/components/usage/TurnEndBreakdown.test.tsx`、`frontend/pages/UsagePage.tiles.test.tsx`

**Interfaces:**
- Produces `usageService.getEfficiency(params: {scope: 'user'|'team'|'project'; id?: number; from?: string; to?: string; groupBy?: 'model'|'agent'}) -> Promise<EfficiencySummary>`
- Produces `aiLibraryService.getRunCosts(ids: string[]) -> Promise<Record<string, RunCost>>`（Part D 的 `RunCostTail` 消费）
- Produces `TurnEndBreakdown: React.FC<{ reasons: Record<string, number> }>`、导出的 `StatTiles`
- Consumes wire `EfficiencyResponse` / `/runs/costs` / `UsageSummary` 新列（Task 10）

- [ ] **Step 1: 建 worktree**
  ```bash
  git -C /Volumes/program/project-code/repos/nous-app worktree add -b feature/3c-t11-usage-tiles \
    /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t11 origin/master
  ```
- [ ] **Step 2: 失败测试 — 分布条**（`frontend/components/usage/TurnEndBreakdown.test.tsx`）
  ```tsx
  /** turn_end 分布条（3c §6 稿三）。十个 reason 里只有五个值得一眼分辨，其余归 Other。 */
  import { render, screen, cleanup } from '@testing-library/react';
  import { afterEach, describe, expect, it, vi } from 'vitest';

  import { TurnEndBreakdown } from './TurnEndBreakdown';

  vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string, f?: string) => f ?? k }) }));
  afterEach(cleanup);

  describe('TurnEndBreakdown', () => {
    it('renders one segment per reason sized by its share', () => {
      render(<TurnEndBreakdown reasons={{ completed: 8, error: 1, interrupted: 1 }} />);
      expect(screen.getByTestId('turn-end-completed')).toHaveStyle({ width: '80%' });
      expect(screen.getByTestId('turn-end-error')).toHaveStyle({ width: '10%' });
    });

    it('folds unknown reasons into one Other segment', () => {
      render(<TurnEndBreakdown reasons={{ completed: 2, max_iterations: 1, paused: 1 }} />);
      expect(screen.getByTestId('turn-end-other')).toHaveStyle({ width: '50%' });
    });

    it('says so when nothing has ended yet instead of drawing an empty bar', () => {
      // 空条与「全是 completed 的条」是两回事，不能长得一样。
      render(<TurnEndBreakdown reasons={{}} />);
      expect(screen.getByTestId('turn-end-empty')).toHaveTextContent('No finished runs yet');
      expect(screen.queryByTestId('turn-end-completed')).toBeNull();
    });
  });
  ```
- [ ] **Step 3: 失败测试 — 六枚 tile**（`frontend/pages/UsagePage.tiles.test.tsx`）
  ```tsx
  /** 用量页 stat tiles（3c §3.3 / §6 稿三）。Success 改读 turn_end 分布：
   *  ``awaiting_input`` 是一个在等人的回合，算成失败等于告诉用户「越问越糟」。 */
  import { render, screen, cleanup } from '@testing-library/react';
  import { afterEach, describe, expect, it, vi } from 'vitest';

  import { StatTiles } from './UsagePage';

  vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string, f?: string) => f ?? k }) }));
  afterEach(cleanup);

  const SUMMARY = { days: 30, month: null, group_by: 'model' as const, total_requests: 10,
    total_failed: 1, total_tokens: 1200, total_cost_cents: 40, daily: [] };
  const EFFICIENCY = {
    scope: 'user', from: '2026-08-16T00:00:00Z', to: '2026-09-15T00:00:00Z',
    groups: [{ key: 'doubao', label: 'doubao', run_count: 10, failed_runs: 1,
               avg_run_ms: 41000, tool_calls: 40, tool_errors: 4, tool_error_rate: 0.1,
               deliverables: 8, cost_cents: 40, cost_per_deliverable_cents: 5 }],
    turn_end_reasons: { completed: 8, awaiting_input: 1, error: 1 } };

  describe('StatTiles', () => {
    it('shows cost per output and tool error rate', () => {
      render(<StatTiles summary={SUMMARY} efficiency={EFFICIENCY} />);
      expect(screen.getByTestId('tile-cost-per-output')).toHaveTextContent('¢5.00');
      expect(screen.getByTestId('tile-tool-errors')).toHaveTextContent('10.0%');
    });

    it('reads success from the turn-end mix, not from run status', () => {
      // 10 个 run 里 8 个 completed → 80%。按 status 算会是 90%（只有 1 个 failed）。
      render(<StatTiles summary={SUMMARY} efficiency={EFFICIENCY} />);
      expect(screen.getByTestId('tile-success')).toHaveTextContent('80%');
    });

    it('falls back to an em dash while efficiency is absent or produced nothing', () => {
      // 效率是第二个请求；它还没回来时这两格必须显式「不知道」，不是 100%。
      const { unmount } = render(<StatTiles summary={SUMMARY} efficiency={null} />);
      expect(screen.getByTestId('tile-cost-per-output')).toHaveTextContent('—');
      expect(screen.getByTestId('tile-success')).toHaveTextContent('—');
      unmount();
      const groups = [{ ...EFFICIENCY.groups[0], deliverables: 0, cost_per_deliverable_cents: null }];
      render(<StatTiles summary={SUMMARY} efficiency={{ ...EFFICIENCY, groups }} />);
      expect(screen.getByTestId('tile-cost-per-output')).toHaveTextContent('—');
    });
  });
  ```
- [ ] **Step 4: 跑，看红**
  ```bash
  cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t11/frontend && npx vitest run \
    components/usage/TurnEndBreakdown.test.tsx pages/UsagePage.tiles.test.tsx
  ```
  期望 `Failed to resolve import './TurnEndBreakdown'` 与 `StatTiles is not exported`。
- [ ] **Step 5: 服务层两个方法 + 类型**
  `usageService.ts` 类型区加：
  ```ts
  export interface EfficiencyGroup {
    key: string; label: string; run_count: number; failed_runs: number;
    avg_run_ms: number | null; tool_calls: number; tool_errors: number;
    tool_error_rate: number; deliverables: number; cost_cents: number;
    /** 0 件产出时是 null（不知道单价），绝不是 0。 */
    cost_per_deliverable_cents: number | null;
  }

  export interface EfficiencySummary {
    scope: string; from: string; to: string;
    groups: EfficiencyGroup[]; turn_end_reasons: Record<string, number>;
  }
  ```
  `UsageTotals` 与 `UsageGroupRow` 各加 `run_count / failed_runs / tool_calls / tool_errors /
  deliverables: number` 与 `cost_per_deliverable_cents: number | null`，`UsageDailyRow` 只加前五个。
  `usageService` 对象加：
  ```ts
    async getEfficiency(params: {
      scope: 'user' | 'team' | 'project'; id?: number;
      from?: string; to?: string; groupBy?: 'model' | 'agent';
    }): Promise<EfficiencySummary> {
      const qs = new URLSearchParams({ scope: params.scope });
      if (params.id != null) qs.set('id', String(params.id));
      if (params.from) qs.set('from', params.from);
      if (params.to) qs.set('to', params.to);
      qs.set('group_by', params.groupBy || 'model');
      const resp = await fetch(`${base()}/ai-library/usage/efficiency?${qs.toString()}`, {
        headers: await getAuthHeaders(),
      });
      return handle<EfficiencySummary>(resp);
    },
  ```
  `types.ts` 加：
  ```ts
  export interface RunCost {
    cost_cents: number | null;
    /** 真扣掉的积分；null = 没扣过（BYOK / 急停关闭 / 零花费）。 */
    charged_points: number | null;
    model: string | null;
    status: string;
    prompt_tokens: number;
    completion_tokens: number;
  }
  ```
  `aiLibraryService.ts` 的 `getUsageDaily` 之后加：
  ```ts
    /** 一屏气泡的花费（3c §4.2）。看不见的 run 不在返回里——键缺席即不可见。 */
    async getRunCosts(ids: string[]): Promise<Record<string, RunCost>> {
      const wanted = ids.filter(Boolean).slice(0, 50);
      if (wanted.length === 0) return {};
      const resp = await fetch(`${base()}/runs/costs?ids=${encodeURIComponent(wanted.join(','))}`, {
        headers: await getAuthHeaders(),
      });
      return (await handle<{ items: Record<string, RunCost> }>(resp)).items;
    },
  ```
- [ ] **Step 6: 分布条组件**（`frontend/components/usage/TurnEndBreakdown.tsx`）
  ```tsx
  /**
   * 一次窗口里回合都是怎么结束的（3c §6 稿三）。十个 TurnEndReason 里只有五个值得一眼
   * 分辨，其余归 Other。全用语义色 token（K1 之后色相名不再表示状态）。
   */
  import React from 'react';
  import { useTranslation } from 'react-i18next';

  const KNOWN = [
    { key: 'completed', className: 'bg-ok', label: 'Completed' },
    { key: 'awaiting_input', className: 'bg-info', label: 'Awaiting input' },
    { key: 'error', className: 'bg-danger', label: 'Error' },
    { key: 'interrupted', className: 'bg-warn', label: 'Interrupted' },
    { key: 'cancelled', className: 'bg-ink-400', label: 'Cancelled' },
  ];

  export const TurnEndBreakdown: React.FC<{ reasons: Record<string, number> }> = ({ reasons }) => {
    const { t } = useTranslation();
    const total = Object.values(reasons).reduce((a, b) => a + b, 0);
    if (total === 0) {
      // 空条与「全是 completed」在视觉上必须不同：一个是没数据，一个是好消息。
      return (
        <p className="text-xs text-ink-500" data-testid="turn-end-empty">
          {t('aiUsage.turnEndEmpty', 'No finished runs yet')}
        </p>
      );
    }
    const other = total - KNOWN.reduce((a, k) => a + (reasons[k.key] || 0), 0);
    const segments = [
      ...KNOWN.map((k) => ({ ...k, count: reasons[k.key] || 0 })),
      { key: 'other', className: 'bg-ink-600', label: 'Other', count: other },
    ].filter((s) => s.count > 0);
    return (
      <div className="space-y-2">
        <div className="flex h-2 w-full overflow-hidden rounded-full bg-ink-800">
          {segments.map((s) => (
            <div key={s.key} data-testid={`turn-end-${s.key}`} className={s.className}
                 style={{ width: `${(s.count / total) * 100}%` }}
                 title={`${s.label}: ${s.count}`} />
          ))}
        </div>
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-ink-500">
          {segments.map((s) => (
            <span key={s.key} className="inline-flex items-center gap-1.5">
              <span className={`h-2 w-2 rounded-full ${s.className}`} />
              {t(`aiUsage.turnEnd.${s.key}`, s.label)} · {s.count}
            </span>
          ))}
        </div>
      </div>
    );
  };

  export default TurnEndBreakdown;
  ```
- [ ] **Step 7: 六枚 tile**（`UsagePage.tsx`）
  `SummaryCard` 加可选 `testId?: string` 透传到根 div 的 `data-testid`。`StatTiles` 改成导出并收第
  二个 prop（既有四枚保持不变，Success 那枚加 `testId="tile-success"` 并改用 `successRate`；网格
  `lg:grid-cols-4` 改 `lg:grid-cols-6`）：
  ```tsx
  export const StatTiles: React.FC<{
    summary: UsageDailySummary;
    efficiency: EfficiencySummary | null;
  }> = ({ summary, efficiency }) => {
    const { t } = useTranslation();
    // 3c §3.3：成功率改读 turn_end 分布。按 status 算会把 awaiting_input（一个在等人
    // 的回合）记成失败。效率端点还没回来时是「不知道」，不是 100%。
    const sum = (f: (g: EfficiencyGroup) => number) =>
      efficiency ? efficiency.groups.reduce((a, g) => a + f(g), 0) : 0;
    const runs = sum((g) => g.run_count);
    const successRate = efficiency && runs > 0
      ? `${(((efficiency.turn_end_reasons.completed ?? 0) / runs) * 100).toFixed(0)}%` : '—';
    const delivered = sum((g) => g.deliverables);
    const costPerOutput = efficiency && delivered > 0
      ? `¢${(sum((g) => g.cost_cents) / delivered).toFixed(2)}` : '—';
    const calls = sum((g) => g.tool_calls);
    const toolErrorRate = efficiency && calls > 0
      ? `${((sum((g) => g.tool_errors) / calls) * 100).toFixed(1)}%` : '—';
    return (
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-6">
        {/* … 既有 Spend / Tokens / Requests / Success 四枚 … */}
        <SummaryCard testId="tile-cost-per-output" icon={<Package size={18} />}
          tint="text-ok bg-ok-soft"
          label={t('aiUsage.statCostPerOutput', 'Cost / output')} value={costPerOutput} />
        <SummaryCard testId="tile-tool-errors" icon={<AlertTriangle size={18} />}
          tint="text-danger bg-danger-soft"
          label={t('aiUsage.statToolErrors', 'Tool error rate')} value={toolErrorRate} />
      </div>
    );
  };
  ```
  `Package` / `AlertTriangle` 从 `lucide-react` 导入。页面 state 加
  `const [efficiency, setEfficiency] = useState<EfficiencySummary | null>(null);`；`fetchUsage`
  的 user 分支在 `setSummary(resp)` 之后加：
  ```ts
          // 效率是第二个请求：它失败不该把整页打掉，tiles 自己退回「—」。
          try {
            setEfficiency(await usageService.getEfficiency({ scope: 'user', groupBy }));
          } catch (effErr) {
            console.error('[UsagePage] efficiency fetch failed:', effErr);
            setEfficiency(null);
          }
  ```
  渲染段 `<StatTiles summary={summary} />` 改成 `<StatTiles summary={summary} efficiency={efficiency} />`，
  并在 `<HeroChart .../>` 之后插：
  ```tsx
            {efficiency && (
              <section className="rounded-xl border border-ink-800 bg-ink-900/40 p-4">
                <div className="mb-3 text-xs text-ink-500">
                  {t('aiUsage.turnEndTitle', 'How turns ended')}
                </div>
                <TurnEndBreakdown reasons={efficiency.turn_end_reasons} />
              </section>
            )}
  ```
- [ ] **Step 8: 团队页两 tile**（`TeamAiUsagePage.tsx`）
  Totals 网格 `sm:grid-cols-4` 改 `sm:grid-cols-6`，末尾加两格（两个值都从
  `usageService.getSummary` 的 `total` 直接来，Task 10 已让小时表带上它们，不再第二次请求）：
  ```tsx
          <Stat label="Cost / output"
            value={total?.cost_per_deliverable_cents != null ? `¢${total.cost_per_deliverable_cents.toFixed(2)}` : '—'} />
          <Stat label="Tool errors"
            value={total && total.tool_calls > 0 ? `${((total.tool_errors / total.tool_calls) * 100).toFixed(1)}%` : '—'} />
  ```
- [ ] **Step 9: 跑绿 + typecheck**
  ```bash
  cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t11/frontend \
    && npx vitest run components/usage pages/UsagePage.tiles.test.tsx && npm run typecheck
  ```
- [ ] **Step 10: 突变记录**
  把 `successRate` 的分子从 `turn_end_reasons.completed` 改成
  `runs - (efficiency.turn_end_reasons.error ?? 0)`，跑 `npx vitest run pages/UsagePage.tiles.test.tsx`
  → 期望 `reads success from the turn-end mix` 红（90% ≠ 80%）。改回。
- [ ] **Step 11: commit + PR**
  ```bash
  git -C /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t11 add -A
  git -C /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t11 commit -m "feat(usage): 用量页六 tile + turn_end 分布条，团队页补两 tile

Success tile 改读 turn_end 分布（awaiting_input 是在等人，不是失败）；新增 Cost / output
与 Tool error rate 两枚，效率端点未回来时显式显示「—」而不是 100%；新增 TurnEndBreakdown
分布条（五个语义色 + Other，零完成回合时说「还没有」而不是画一条空条）；
usageService.getEfficiency 与 aiLibraryService.getRunCosts 两个客户端。"
  git -C /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t11 push -u origin feature/3c-t11-usage-tiles
  cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t11 && gh pr create --base master \
    --title "feat(usage): 用量页效率 tiles + turn_end 分布条（3c §3.3 / §6 稿三）" --fill
  ```

---

### Task 12: Part B 的合并顺序与部署验证

**Files:** 无（只跑命令与记账）   **Interfaces:** Consumes Task 8–11 的四个 PR

- [ ] **Step 1: 合并顺序**（每条等前一条的 `deploy-gpu` 绿再动下一条）
  ① **Part A Task 1（mig 472 + ORM）** — Task 8 的五列写入依赖它。② **Task 8**。
  ③ **Part A 的 A1 票** — 它消费 Task 8 的 `RunRecorder._efficiency_counts()`。
  ⚠️ 契约把 `record_usage(..., tool_calls=eff[...])` 归在 A1，而 `eff` 的产地是 Task 8——**A1 必须
  排在 Task 8 之后**，不能并行。`_efficiency_counts()` 无 event writer 时返回可加的零，所以顺序反
  了不会炸，但那段时间小时表计数全是 0，A1 的对账 fixture 会红。
  ④ **Task 9 / Task 10** — 互不相交，可并行。⑤ **Task 11** — 依赖 Task 10 的两个端点已上线。
- [ ] **Step 2: 每个后端 PR 合并后盯部署链**
  ```bash
  gh run watch --exit-status
  ssh gpupc 'docker exec nous-backend curl -sS http://localhost:8080/api/v1/readyz'
  ```
  `dbos` 字段三态，`configured_but_disabled` 即故障。
- [ ] **Step 3: Task 8 合并后的容器内符号探针**（一个 import 不进来的 fold 不报错，只会永远计 0）
  ```bash
  ssh gpupc 'docker exec nous-worker /app/.venv/bin/python -c "
  import app.services.ai.runner.tool_events as te
  from app.services.ai.runner.run_projection import empty_views, registered_types
  assert callable(te.emit_tool_call) and callable(te.tool_error_code)
  eff = empty_views()[\"efficiency\"]
  assert set(eff) == {\"steps\",\"tool_calls\",\"tool_errors\",\"deliverables\",\"turn_end_reason\"}, eff
  for t in (\"step_end\",\"tool_call\",\"deliverable\",\"turn_end\"):
      assert t in registered_types(), t
  print(\"OK\", eff)
  "'
  ssh gpupc 'docker exec nous-backend /app/.venv/bin/python -c "
  import importlib.util as u
  assert u.find_spec(\"app.services.infra.hooks.cost_auditor\") is None, \"CostAuditor 还在镜像里\"
  print(\"OK removed\")
  "'
  ```
- [ ] **Step 4: Task 8 合并后的真栈数据探针**（先在 cn.nous.ink 跑一个带工具调用的回合）
  ```bash
  ssh gpupc 'docker exec nous-db psql -U postgres -p 55434 -d postgres -c "
  SELECT id, steps, tool_calls, tool_errors, deliverables, turn_end_reason
  FROM agent_runs WHERE ended_at > now() - interval \"10 minutes\" ORDER BY ended_at DESC LIMIT 5"'
  ssh gpupc 'docker exec nous-db psql -U postgres -p 55434 -d postgres -c "
  SELECT payload->>\"duration_ms\" AS ms, payload ? \"error_code\" AS has_code
  FROM agent_run_transcript_events WHERE event_type = \"tool_call\"
    AND created_at > now() - interval \"10 minutes\" LIMIT 5"'
  ```
  口径：五列非空（`turn_end_reason` 至少是 `completed`）；`duration_ms` 是正整数；`has_code` 恒为
  t（成功时值是 null，但**键必须在**——缺键会被读成「没这回事」）。
- [ ] **Step 5: Task 10 合并后探三个端点**
  ```bash
  curl -sS "https://cn.nous.ink/api/v1/ai-library/usage/efficiency?scope=user&group_by=model" \
    -H "Authorization: Bearer $TOKEN" | jq '{groups: (.groups|length), turn_end_reasons}'
  curl -sS "https://cn.nous.ink/api/v1/ai-library/runs/costs?ids=$RUN_ID" \
    -H "Authorization: Bearer $TOKEN" | jq '.items'
  curl -sS "https://cn.nous.ink/api/v1/usage/summary?team_id=$TEAM_ID" \
    -H "Authorization: Bearer $TOKEN" | jq '.total | {run_count, tool_errors, cost_per_deliverable_cents}'
  ```
  **负向对照（必做）**：拿一个自己不是成员的 `team_id` 调 `/usage/efficiency?scope=team&id=`，必须
  404 且响应体是 `{"success": false, "code": "http_404", "details": {"code": "not_found"}}` 的外壳
  形状。这是 Task 10 唯一一道边界门的可证伪证据——功能测试永远绿，泄露只存在于这条没人走的路径上。
- [ ] **Step 6: Task 11 合并后前端验收**
  ```bash
  curl -sS https://app.nous.ink/version.json | jq -r .commitSha
  cd /Volumes/program/project-code/repos/nous-app/frontend && npm run e2e:prod
  ```
  `commitSha` 与 push 的 SHA 前 7 位一致后，打开 `/usage`：六枚 tile 齐、Success 显示的是 turn_end
  口径、分布条有色段；再开 `/team/<id>/ai-cost` 看两枚新 tile。
- [ ] **Step 7: 记账**
  在 `.superpowers/sdd/2026-09-15-harness-p4-phase3c/progress.md` 追加 Part B 段：四个 PR 号、开头
  「与契约的三处偏离」的最终裁定，以及两张新票：（a）`/ai-library/usage` 的 team scope 无成员校验
  （注释写着 RLS-enforced，实际是取全量再在 Python 里 filter，`agent_runs` 不在 SCOPE_ENFORCE 清单
  里）——范围外，单开；（b）`agent_run_events` 与 `provider_monthly_spend` 的 DROP 迁移，等一个发
  布周期后再排。

---

# Part C（检索与引用）· Task 13–18

spec `docs/superpowers/specs/2026-09-15-harness-p4-phase3c-search-efficiency-design.md` §2 / §6 稿一稿四 / §10；契约 `plan-contract.md` §0 §1 §4；侦察 `recon-3c-search-mentions.md`。
mig 472 与 `app/models/search.py`（`SearchDocs` / `OutputCitations`）由 **Part A Task 1** 提供；本段只消费第 1、2 段。

**与契约的三处偏离（四位作者需知，Task 18 Step 7 记票）**

1. 路由注册点是 `backend/app/api/__init__.py`（import :204-205、include :207-208），不是 `main.py`——后者 :301 只 include 聚合器一次。
2. `record_output_citations` 的第一个位置参数 `session` 允许 `None`。`ConversationRepository.send_message`（`conversation_repository.py:455`）自己开 `write_scope()` 并在返回前提交，调用方拿不到那个 session；真同事务要改消息主链路的签名，不在本期范围。镜像发生在消息**已落库之后**，失败记 ERROR 不回滚消息。`insert_many(rows, *, session)` 形状保留，将来 `send_message` 交出 session 时零改动接上。
3. run 行标题要议题 **identifier + title**，而 `map_identifiers`（`issue_repository.py:489`）只给 identifier。Task 13 新增 `IssueRepository.identifier_and_title`。**深链只到议题页**：`search_docs` 按契约 §0 没有 `step` / `turn` 列，spec §2.3 写的 `?step=` 与 `?output=kind/ref_id` 两个锚点都缺输入，本期不编造。

---

### Task 13: `search_docs` 投影表与四个写方

**Files:**
- Create `backend/app/services/search/__init__.py`（空，包标记）、`types.py`、`projection.py`
- Create `backend/app/repositories/search_docs_repository.py`
- Modify `backend/app/services/deliverables/registry.py`（签名 66-80、返回点 128-132；`register_deliverable_best_effort` 234-260 用 `**kwargs` 自动透传）
- Modify `backend/app/services/ai/tools/screenwriting_tools.py`（`_register_write` 251-275、调用点 373 / 566 / 777）
- Modify `backend/app/services/library/generated_media_service.py`（登记调用 413-427）
- Modify `backend/app/services/deliverables/revert.py`（`_register` 457-470 与 `_revert_shot` / `_revert_scene` 两个调用点）
- Modify `backend/app/services/ai/runner/run_recorder.py`（`_finish` 的 UPDATE 之后、`record_usage` 之前，约 885）
- Modify `backend/app/repositories/issue_repository.py`（`map_identifiers` 489-504 之后）
- Create `backend/tests/repositories/test_search_docs_repository.py`、`backend/tests/services/search/test_projection.py`、`backend/tests/services/search/test_search_text_wiring.py`

**Interfaces:**
- Produces `SearchDoc`（契约 §1 逐字段照抄）、`BODY_MAX_BYTES = 8192`
- Produces `SearchDocsRepository.upsert(doc: SearchDoc) -> None`
- Produces `SearchDocsRepository.search(*, q: str, kinds: set[str], team_ids: list[int], user_id: str, project_id: int | None, issue_id: int | None, limit: int) -> list[dict]`
- Produces `get_search_docs_repository() -> SearchDocsRepository`
- Produces `project_run_best_effort(run_row: dict) -> None`、`project_output_best_effort(row: DeliverableRow, *, search_text: str | None) -> None`
- Produces `output_entity_id(kind, ref_id, version) -> str`（`search/projection.py`）——产出行 `entity_id` 的**唯一**拼法 `f"{kind}:{ref_id}:{version}"`，与 mig 472 回填段同一口径；run 行是 `str(agent_runs.id)`，两者刻意不统一
- Produces `register_deliverable(..., search_text: str | None = None)`
- Produces `IssueRepository.identifier_and_title(issue_id: int) -> tuple[str | None, str | None]`
- Consumes Part A Task 1 的 `SearchDocs` ORM；既有 `diff.py::render_shot`(:81) / `render_elements`(:92)、`library/like_escape.py::escape_like` 与 `LIKE_ESCAPE_CHAR`

> `team_ids` 是 **AND 过滤**不是授权输入——授权由 `team_members` 子查询独立完成（同 `list_for_user` 的 `team_id`，`issue_repository.py:316-323` 有这条注释）。

- [ ] **Step 1: 建 worktree**

```bash
git -C /Volumes/program/project-code/repos/nous-app worktree add -b feat/3c-search-projection \
  /Volumes/program/project-code/repos/nous-app/.worktrees/3c-search-projection origin/master
```

- [ ] **Step 2: 写 repository 的失败测试**

```python
# backend/tests/repositories/test_search_docs_repository.py
"""search_docs 的读写口（3c §2.1）。三条：upsert 按 (entity_kind, entity_id)
幂等（投影可重建是设计的一部分）；body 超 8 KB 截断而不是拒绝；授权谓词与
team 过滤是**两个**谓词——合成一个会让「按别人的 team 过滤」变成一次越权读取
（`rpc_user_media_text_search` 那类洞的形状）。"""
import pytest

from app.repositories.search_docs_repository import SearchDocsRepository
from app.services.search.types import BODY_MAX_BYTES, SearchDoc

pytestmark = pytest.mark.unit
ME = "11111111-1111-1111-1111-111111111111"


def _sql(stmt) -> str:
    from sqlalchemy.dialects import postgresql

    return str(stmt.compile(dialect=postgresql.dialect()))


def _stmt(**over):
    kw = {"q": "rain", "kinds": {"run"}, "team_ids": [], "user_id": ME,
          "project_id": None, "issue_id": None, "limit": 10}
    kw.update(over)
    return SearchDocsRepository()._search_stmt(**kw)


def test_an_upsert_targets_the_entity_key_and_bumps_updated_at():
    sql = _sql(SearchDocsRepository()._upsert_stmt(
        SearchDoc(entity_kind="run", entity_id="913", title="MH-96 · Alpha")))
    assert "ON CONFLICT (entity_kind, entity_id) DO UPDATE" in sql and "updated_at" in sql


def test_a_long_multibyte_body_is_clipped_on_a_codepoint_boundary():
    # 一个汉字 3 字节：按字节硬切会切出半个字符，asyncpg 绑定时当场报错。
    # 本仓产出正文含中文（剧本行），所以这不是理论情形。
    doc = SearchDoc(entity_kind="output", entity_id="script_shot:9:4", title="t",
                    body="中" * BODY_MAX_BYTES)
    body = SearchDocsRepository()._values(doc)["body"]
    assert len(body.encode()) <= BODY_MAX_BYTES and body.encode().decode() == body


def test_the_visibility_predicate_is_always_there_and_team_filter_is_extra():
    wide, narrow = _sql(_stmt()), _sql(_stmt(team_ids=[7]))
    assert "team_members" in wide and "owner_user_id" in wide and "team_members" in narrow
    # team 过滤是**追加**的一个 IN，不是替换授权谓词。
    assert narrow.count("team_id IN") > wide.count("team_id IN")


def test_the_pattern_is_escaped_and_declares_its_escape_char():
    # CLAUDE.md「ILIKE 模式的转义责任要跟着模式走」：Python 侧转义 + SQL 侧
    # ESCAPE 缺一不可——Postgres 的 LIKE 没有默认转义符。
    sql = _sql(_stmt(q="100%", kinds={"run", "output"}))
    assert "ESCAPE" in sql and "ILIKE" in sql.upper()


def test_the_order_is_similarity_then_recency_and_scopes_are_additive():
    sql = _sql(_stmt(kinds={"output"}))
    assert "greatest(similarity" in sql.lower() and "updated_at DESC" in sql
    assert sql.lower().index("order by") < sql.lower().index("limit")
    assert "project_id" not in sql
    both = _sql(_stmt(project_id=3, issue_id=96))
    assert "project_id" in both and "issue_id" in both
```

- [ ] **Step 3: 跑，确认红**

```bash
cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-search-projection/backend && \
  uv run pytest tests/repositories/test_search_docs_repository.py -q
```
预期：`ModuleNotFoundError: No module named 'app.services.search'`（collection error，5 个用例一个都没跑）。

- [ ] **Step 4: 写 `types.py`**

`@dataclass(frozen=True) class SearchDoc` 的十六个字段**逐字照抄契约 §1**
（`entity_kind` / `entity_id` / `title` 必填，其余 `= None`），加
`BODY_MAX_BYTES = 8192`。模块 docstring：

```
"""检索投影的值对象（3c §2.1）。``search_docs`` 是**投影**不是真相：丢了可以从
``agent_runs`` / ``run_deliverables`` 重建。所以每个坐标都可空——写不全某个坐标
时该写进去，不该整条不写。必填只有身份两列与 title（「搜得到」的下限）。

BODY_MAX_BYTES 是**字节**上限。截断而不是拒绝：搜不到长正文的尾巴，比这条投影
整个缺席好——后者会让这件产出在检索里不存在。"""
```

- [ ] **Step 5: 写 repository**

`search_docs_repository.py` 的 docstring 写两件事：授权在 SQL 里做一半（team/owner
谓词）、在 Python 里做另一半（`visible_issue_ids`，由 `search/service.py` 负责）；
`team_ids` 是过滤不是授权。`upsert` 包 `write_scope()` 执行 `_upsert_stmt`；
`search` 包 `read_scope()` 执行 `_search_stmt`，把每行按列名摊成 dict，
`id / team_id / project_id / issue_id / run_id / owner_user_id / agent_id` 一律
`str()`（Snowflake 精度纪律），并附 `score: float`。

⚠️ **`entity_id` 原样出口，谁都不许在读侧切它。** 产出行的拼法是
`kind:ref_id:version`（契约补充，`projection.output_entity_id` 是唯一拼它的地方），
但三段坐标在表上**各有一列**——读者要 kind / ref_id / version 就读列，切字符串等于
把拼法复制成第二份，而第二份不会跟着 `output_entity_id` 一起改。`entity_id` 在读侧
只有一个用途：`SearchHit.id`，也就是「这条命中是哪一版」的不透明身份键。

三个 `_` 前缀方法：

```python
def _clip(body):
    """按**字节**截，切在码点边界上。``encode()[:n].decode()`` 会在多字节字符
    中间切断并抛 UnicodeDecodeError；``errors="ignore"`` 丢的是尾巴上的一个字，
    不是这条投影。"""
    if body is None:
        return None
    raw = body.encode("utf-8")
    return body if len(raw) <= BODY_MAX_BYTES else raw[:BODY_MAX_BYTES].decode(
        "utf-8", errors="ignore")


class SearchDocsRepository:
    def _values(self, doc):
        out = {f: getattr(doc, f) for f in SearchDoc.__dataclass_fields__}
        out["entity_id"], out["body"] = str(doc.entity_id), _clip(doc.body)
        return out

    def _upsert_stmt(self, doc):
        values = self._values(doc)
        updatable = {k: v for k, v in values.items()
                     if k not in ("entity_kind", "entity_id")}
        return pg_insert(SearchDocs).values(**values).on_conflict_do_update(
            index_elements=[SearchDocs.entity_kind, SearchDocs.entity_id],
            set_={**updatable, "updated_at": func.now()})

    def _search_stmt(self, *, q, kinds, team_ids, user_id, project_id, issue_id, limit):
        # 拼模式者负责转义（CLAUDE.md）。SQL 侧再声明 ESCAPE——只做一侧等于没做。
        pattern = f"%{escape_like(q)}%"
        body = func.coalesce(SearchDocs.body, literal(""))
        score = func.greatest(func.similarity(SearchDocs.title, q),
                              func.similarity(body, q)).label("score")
        me = cast(literal(user_id), String)
        mine = select(TeamMembers.team_id).where(TeamMembers.user_id == me)
        stmt = (select(SearchDocs, score)
                .where(SearchDocs.entity_kind.in_(sorted(kinds)))
                .where(or_(SearchDocs.title.ilike(pattern, escape=LIKE_ESCAPE_CHAR),
                           body.ilike(pattern, escape=LIKE_ESCAPE_CHAR)))
                # 授权谓词：恒在。没有它这张表就是一张对所有人开放的全量索引。
                .where(or_(SearchDocs.team_id.in_(mine), SearchDocs.owner_user_id == me)))
        if team_ids:
            stmt = stmt.where(SearchDocs.team_id.in_([int(t) for t in team_ids]))
        if project_id is not None:
            stmt = stmt.where(SearchDocs.project_id == int(project_id))
        if issue_id is not None:
            stmt = stmt.where(SearchDocs.issue_id == int(issue_id))
        return stmt.order_by(score.desc(), SearchDocs.updated_at.desc()).limit(int(limit))
```

`cast(literal(user_id), String)` 不能省：两列在 ORM 上是 `Uuid`，绑一个 Python
`str` 时 asyncpg 按 text 传参并在 `= uuid` 上报 `operator does not exist`。
模块尾部 `_repo = SearchDocsRepository()` + `get_search_docs_repository()`。

- [ ] **Step 6: 跑，确认绿**

```bash
cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-search-projection/backend && \
  uv run pytest tests/repositories/test_search_docs_repository.py -q
```
预期：`5 passed`。

- [ ] **Step 7: 写投影与接线的失败测试**

```python
# backend/tests/services/search/test_projection.py
"""两个 best_effort 写方（3c §2.1）。纪律同 register_deliverable_best_effort：
产物已经存在，投影失败不该把它判成失败。**不是静默吞错**——失败记 ERROR。"""
import pytest

from app.services.deliverables.registry import DeliverableRow
from app.services.search import projection as mod

pytestmark = pytest.mark.unit
ME = "11111111-1111-1111-1111-111111111111"


class _RepoSpy:
    def __init__(self, boom=False):
        self.docs, self._boom = [], boom

    async def upsert(self, doc):
        if self._boom:
            raise RuntimeError("relation search_docs does not exist")
        self.docs.append(doc)


@pytest.fixture
def spy(monkeypatch):
    s = _RepoSpy()
    monkeypatch.setattr(mod, "get_search_docs_repository", lambda: s)
    return s


async def test_a_run_projects_with_its_issue_key_and_title(spy, monkeypatch):
    async def _ident(issue_id):
        assert issue_id == 96
        return ("MH-96", "Alpha rain on glass")

    monkeypatch.setattr(mod, "_issue_identity", _ident)
    await mod.project_run_best_effort(
        {"id": 913, "issue_id": 96, "team_id": 7, "project_id": 3, "agent_id": "a-1",
         "user_id": ME, "model": "doubao", "status": "completed", "error_code": None,
         "input_summary": "ignored", "output_summary": "6403 images"})
    doc = spy.docs[-1]
    assert (doc.entity_kind, doc.entity_id) == ("run", "913")
    assert doc.title == "MH-96 · Alpha rain on glass" and doc.body == "6403 images"
    assert (doc.team_id, doc.issue_id, doc.run_id, doc.owner_user_id) == (7, 96, 913, ME)


async def test_a_run_without_an_issue_falls_back_to_a_clipped_input_summary(spy):
    await mod.project_run_best_effort(
        {"id": 914, "issue_id": None, "user_id": ME, "status": "failed",
         "error_code": "budget_exhausted", "input_summary": "x" * 200})
    doc = spy.docs[-1]
    assert doc.title == "x" * 80  # 首 80 字，不是整段
    # 无议题的 run 靠 owner 做可见性——SQL 层唯一能证明归属的列。
    assert doc.owner_user_id == ME and doc.error_code == "budget_exhausted"


async def test_a_run_with_neither_issue_nor_summary_still_gets_a_title(spy):
    await mod.project_run_best_effort({"id": 915, "issue_id": None, "user_id": ME})
    # title NOT NULL —— 编不出名字也必须写一条，否则这次运行在检索里不存在。
    assert spy.docs[-1].title == "run"


async def test_an_output_carries_the_search_text_as_its_body(spy, monkeypatch):
    async def _coords(run_id):
        assert run_id == "913"
        return {"team_id": 7, "project_id": 3, "issue_id": 96, "agent_id": "a-1",
                "user_id": ME, "model": "doubao"}

    monkeypatch.setattr(mod, "_run_coords", _coords)
    row = DeliverableRow(id="42", run_id="913", kind="script_shot", ref_id="9",
                         version=4, parent_version=3, title="S3 · Shot 1 · MS")
    await mod.project_output_best_effort(row, search_text="shot_type: MS\ndesc: rain")
    doc = spy.docs[-1]
    # 身份键是坐标拼出来的，**不是** run_deliverables.id（契约补充）：mig 472 的
    # 回填段按同一个拼法写存量行，两边拼法不一致会让回填行与新写行互不覆盖，
    # 于是同一版在表里有两行而 UNIQUE 拦不住——它比的是这个字符串。
    assert (doc.entity_kind, doc.entity_id) == ("output", "script_shot:9:4")
    assert (doc.kind, doc.ref_id, doc.version, doc.issue_id) == ("script_shot", "9", 4, 96)
    assert doc.body == "shot_type: MS\ndesc: rain"


async def test_a_human_version_owns_itself_and_needs_no_search_text(spy, monkeypatch):
    async def _coords(run_id):
        assert run_id is None
        return None

    monkeypatch.setattr(mod, "_run_coords", _coords)
    row = DeliverableRow(id="43", run_id=None, kind="script_scene", ref_id="7",
                         version=2, parent_version=1, title=None, actor_user_id=ME)
    await mod.project_output_best_effort(row, search_text=None)
    doc = spy.docs[-1]
    # 新 kind 的生产者忘传正文只是搜不到正文，不是接线 bug（spec §2.1）；
    # 人手版（回退）没有 run，归属只能由署名人给出。
    assert doc.body is None and doc.title == "script_scene #7" and doc.owner_user_id == ME
    # 人手版的身份键与 agent 版同一个拼法——它写在同一条链上，第二种拼法会让
    # 回退产生的那一版在检索里变成一个不同的东西。
    assert doc.entity_id == "script_scene:7:2"


async def test_a_failing_projection_is_logged_and_swallowed(monkeypatch, caplog):
    monkeypatch.setattr(mod, "get_search_docs_repository", lambda: _RepoSpy(boom=True))
    await mod.project_run_best_effort({"id": 916, "issue_id": None, "user_id": ME})
    assert any("search_docs" in r.message for r in caplog.records)
```

```python
# backend/tests/services/search/test_search_text_wiring.py
"""四个咽喉点各自交了正文（3c §2.1）。单独存在的理由：``search_text`` 缺省时
**不报错**，所以一个生产者忘了传，测试和生产都不会红——只会静默地搜不到正文。
这几条就是那个缺席的哨兵。"""
import ast
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
_APP = Path(__file__).resolve().parents[3] / "app"


def _kwargs_at(path: Path, func_name: str) -> set:
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call) and (
            getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        ) == func_name:
            found |= {kw.arg for kw in node.keywords if kw.arg}
    return found


def test_the_registry_accepts_search_text():
    from app.services.deliverables.registry import register_deliverable

    assert "search_text" in inspect.signature(register_deliverable).parameters


def test_screenwriting_hands_its_rendered_body_to_the_registry():
    src = _APP / "services" / "ai" / "tools" / "screenwriting_tools.py"
    assert "search_text" in _kwargs_at(src, "register_deliverable_best_effort")
    text = src.read_text(encoding="utf-8")
    # 渲染复用 diff 的两个函数——第二个渲染器意味着「搜到的文本」与
    # 「diff 里看到的文本」会分叉。
    assert "render_shot" in text and "render_elements" in text


def test_generated_media_hands_the_whole_prompt_not_its_first_line():
    src = _APP / "services" / "library" / "generated_media_service.py"
    assert "search_text" in _kwargs_at(src, "register_deliverable_best_effort")
    # 标题取首行（_first_line），正文取全文——两者刻意不是同一个值。
    assert "search_text=origin.prompt" in src.read_text(encoding="utf-8")


def test_the_revert_and_the_recorder_are_wired_too():
    assert "search_text" in _kwargs_at(
        _APP / "services" / "deliverables" / "revert.py", "register_deliverable")
    assert "project_run_best_effort" in (
        _APP / "services" / "ai" / "runner" / "run_recorder.py").read_text(encoding="utf-8")
```

- [ ] **Step 8: 跑，确认红**

```bash
cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-search-projection/backend && \
  uv run pytest tests/services/search/ -q
```
预期：`test_projection.py` collection error（模块不存在）；`test_search_text_wiring.py` 4 failed。

- [ ] **Step 9: 写 `projection.py`**

模块 docstring 两条：失败永不连坐（run 已结束、产出已付过钱，判成失败会让 agent
重试、同一件东西产两遍）；这里不做可见性判断（读的那一侧才决定谁看得见，两处都判
会出现两套口径）。

```python
#: 无议题的 run 用输入摘要当标题时取的长度。整段 500 字的 input_summary 会把
#: 命中列表撑成一堵墙。
_TITLE_FALLBACK_CHARS = 80


async def _issue_identity(issue_id):
    from app.repositories.issue_repository import issue_repository
    return await issue_repository.identifier_and_title(int(issue_id))


async def _run_coords(run_id):
    """一条产出的坐标来自它的 run —— run_deliverables 没有 issue_id 列，
    agent_runs.issue_id 是唯一真相（output_ref_resolver docstring 第二条）。
    人手版没有 run，返回 None。一条 SELECT 取 team/project/issue/agent/user/model。"""


def _run_title(run_row, key, title) -> str:
    if key and title:
        return f"{key} · {title}"
    if key:
        return key
    # title 是 NOT NULL：编不出名字也要写一条，否则这次运行在检索里不存在。
    return (run_row.get("input_summary") or "").strip()[:_TITLE_FALLBACK_CHARS] or "run"


async def project_run_best_effort(run_row: dict) -> None:
    try:
        issue_id = run_row.get("issue_id")
        key, title = await _issue_identity(issue_id) if issue_id is not None else (None, None)
        await get_search_docs_repository().upsert(SearchDoc(
            # run 行的身份键就是 str(agent_runs.id)——**和产出行的拼法不同**，
            # 两者刻意不统一（契约补充）：run 本来就有一个全局唯一 id，产出的
            # 「一版」没有可用的单列 id，只能由坐标拼。
            entity_kind="run", entity_id=str(run_row.get("id")),
            title=_run_title(run_row, key, title), body=run_row.get("output_summary"),
            team_id=_int(run_row.get("team_id")), project_id=_int(run_row.get("project_id")),
            issue_id=_int(issue_id), run_id=_int(run_row.get("id")),
            owner_user_id=_str(run_row.get("user_id")), agent_id=_str(run_row.get("agent_id")),
            model=run_row.get("model"), status=run_row.get("status"),
            error_code=run_row.get("error_code")))
    except Exception as exc:  # noqa: BLE001 — 见模块 docstring
        logger.opt(exception=True).error(
            f"[search] run {run_row.get('id')} did not reach search_docs: {exc!r}")


def output_entity_id(kind, ref_id, version) -> str:
    """一件产出**某一版**的身份键（契约补充）：``kind:ref_id:version``。

    不是 ``run_deliverables.id``。mig 472 的回填段按同一个拼法写存量行，所以拼法
    必须只有这**一个**函数说了算——两处各拼各的，回填行与新写行就互不覆盖，
    UNIQUE (entity_kind, entity_id) 比的正是这个字符串，拦不住的那一版会在表里
    留两行、在检索里出现两次。

    三段都不含 ``:``：kind 是四值枚举，ref_id 是 Snowflake 十进制，version 是小
    整数——所以这个串可以反向切开，虽然读者不该切（坐标另有三列，见下）。"""
    return f"{kind}:{ref_id}:{version}"


async def project_output_best_effort(row, *, search_text) -> None:
    try:
        coords = await _run_coords(row.run_id) or {}
        await get_search_docs_repository().upsert(SearchDoc(
            entity_kind="output",
            entity_id=output_entity_id(row.kind, row.ref_id, row.version),
            title=row.title or f"{row.kind} #{row.ref_id}", body=search_text,
            kind=row.kind, ref_id=str(row.ref_id), version=row.version,
            team_id=_int(coords.get("team_id")), project_id=_int(coords.get("project_id")),
            issue_id=_int(coords.get("issue_id")), run_id=_int(row.run_id),
            # 人手版（回退）没有 run，署名人就是它的 owner。
            owner_user_id=_str(coords.get("user_id") or row.actor_user_id),
            agent_id=_str(coords.get("agent_id")), model=coords.get("model")))
    except Exception as exc:  # noqa: BLE001
        logger.opt(exception=True).error(
            f"[search] {row.kind}/{row.ref_id} v{row.version} did not reach "
            f"search_docs: {exc!r}")
```

`_int` / `_str` 是两个 `None if v is None else int(v) / str(v)` 的模块级 helper。

- [ ] **Step 10: 接四个咽喉点**

**`issue_repository.py`**（`map_identifiers` 之后）：

```python
    async def identifier_and_title(self, issue_id: int) -> tuple[Optional[str], Optional[str]]:
        """一件议题的 (identifier, title)，给 run 投影的标题用。不复用
        ``map_identifiers``：那个是批量的且只给 identifier，改成也返回标题会让
        既有三个调用方多拖一列。"""
        async with read_scope() as session:
            row = (await session.execute(select(Issues.identifier, Issues.title)
                                         .where(Issues.id == int(issue_id)))).first()
        return (row[0], row[1]) if row else (None, None)
```

**`registry.py`**：签名追加 `search_text: Optional[str] = None`；在
`row = await _insert_next_version(...)` 之后、`if rid is None:`（128 行）**之前**：

```python
    # 投影在行落库之后，失败不连坐（见 projection 模块 docstring）。放在 rid
    # 分支之前：人手版（回退）同样要能被搜到——它是这条链当前的内容。
    from app.services.search.projection import project_output_best_effort

    await project_output_best_effort(row, search_text=search_text)
```

**`screenwriting_tools.py`**：顶部 import `render_elements, render_shot`；
`_register_write` 加 `search_text: Optional[str] = None` 并透传。三个调用点：
`create_shot`(373) `search_text=render_shot(shot)`、`update_shot`(566)
`search_text=render_shot(updated)`（两者都在 `_take_ledger_ref` 之后——那一步已把
宿主字段取走）、`apply_edit`(777) `search_text=render_elements(outcome.elements)`。
两个渲染器就是 diff 两侧用的那一对，「搜到的文本」与「diff 里看到的文本」按构造相同。

**`generated_media_service.py`**（413-427）在 `title=_first_line(origin.prompt)` 旁加
`search_text=origin.prompt`。

**`revert.py`**：`_register` 加 `search_text` 并透传；`_revert_shot` 传
`render_shot(fields)`、`_revert_scene` 传 `render_elements(...)`（两者的目标版内容
revert 已经算出来了）。

**`run_recorder.py::_finish`**，在 `async with write_scope()` 的 UPDATE **之后**、
`record_usage` 之前：

```python
        # 只在终态写一次。放在 UPDATE 之后，投影读到的就是刚落库的那份
        # status / error_code / output_summary。
        from app.services.search.projection import project_run_best_effort

        await project_run_best_effort({
            "id": self.run_id, "issue_id": self.issue_id, "team_id": self.team_id,
            "project_id": self.project_id, "agent_id": self.agent_id,
            "user_id": self.user_id, "model": self.model, "status": status,
            "error_code": error_code, "input_summary": self.input_summary,
            "output_summary": self._output_summary})
```

- [ ] **Step 11: 跑，确认绿**

```bash
cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-search-projection/backend && \
  uv run pytest tests/services/search/ tests/services/deliverables/ \
    tests/repositories/test_search_docs_repository.py -q && \
  uv run ruff check . && uv run black --check . && uv run isort --check-only .
```
预期：全绿（既有 deliverables 套件不受影响——`search_text` 有缺省值）。

- [ ] **Step 12: 突变验证**

① `_clip` 改成无条件 `return body`，跑 `uv run pytest tests/repositories/test_search_docs_repository.py -q`，
确认 `test_a_long_multibyte_body_is_clipped_on_a_codepoint_boundary` 转红；恢复。
② `_search_stmt` 的授权 `.where(or_(SearchDocs.team_id.in_(mine), ...))` 注释掉，确认
`test_the_visibility_predicate_is_always_there_and_team_filter_is_extra` 转红；恢复。
③ 删掉 `generated_media_service.py` 的 `search_text=origin.prompt`，确认
`test_generated_media_hands_the_whole_prompt_not_its_first_line` 转红；恢复。
④ `output_entity_id` 改回 `str(row.id)`，确认 `test_an_output_carries_the_search_text_as_its_body`
与 `test_a_human_version_owns_itself_and_needs_no_search_text` 两条转红——这条突变正是
「回填行与新写行互不覆盖、同一版两行」的形状；恢复并重跑全绿。

- [ ] **Step 13: 提交与 PR**

```bash
W=/Volumes/program/project-code/repos/nous-app/.worktrees/3c-search-projection
git -C "$W" add -A && \
git -C "$W" commit -m "feat(search): search_docs 投影表与四个写方——run 终态、分镜/场次/生成媒体正文、回退重建文本（harness 三期 3c Task 13）" && \
git -C "$W" push -u origin feat/3c-search-projection && \
gh pr create --repo iocrazy/nous-app --base master --head feat/3c-search-projection \
  --title "feat(search): search_docs 投影表与四个写方（3c Task 13）" \
  --body "3c §2.1。投影是注册表：真相仍在 agent_runs / run_deliverables，search_docs 丢了可以重建。四个写方全部 best-effort（失败记 ERROR 不连坐主写入）。正文渲染复用 diff.py 的 render_shot / render_elements，与 diff 面板看到的文本按构造相同。"
```

---

### Task 14: `GET /api/v1/search` 统一检索端点

**Files:**
- Create `backend/app/services/search/service.py`、`backend/app/schemas/search.py`、`backend/app/api/search_router.py`
- Modify `backend/app/api/__init__.py`（照 :204-205 与 :207-208 的形状加两行）
- Modify `backend/app/repositories/issue_repository.py`（`list_for_user` 305-355）
- Create `backend/tests/repositories/test_issue_repository_query.py`、`backend/tests/services/search/test_unified_search.py`、`backend/tests/api/test_search_router.py`

**Interfaces:**
- Produces `unified_search(*, auth, q: str, kinds: set[str], team_id: int | None, project_id: int | None, issue_id: int | None, limit_per_group: int = 10) -> SearchResponse`
- Produces `snippet_for(text: str | None, q: str) -> str | None`、`ALL_SEARCH_KINDS: frozenset`
- Produces `SearchHit` / `SearchGroups` / `SearchTotals` / `SearchResponse`（契约 §1 逐字段照抄）
- Produces `GET /api/v1/search`（`q` < 2 → 400 `query_too_short`；kinds 全不认识 → 400 `unknown_kinds`）
- Produces `IssueRepository.list_for_user(..., q: str | None = None)` 与 `_q_predicate(q)`
- Consumes Task 13 的 `SearchDocsRepository.search`；既有 `issue_visibility.visible_issue_ids`、`issue_links.issue_deep_link`

- [ ] **Step 1: 建 worktree（Task 13 合并后建，或建后 `rebase origin/master`）**

```bash
git -C /Volumes/program/project-code/repos/nous-app worktree add -b feat/3c-search-endpoint \
  /Volumes/program/project-code/repos/nous-app/.worktrees/3c-search-endpoint origin/master
```

- [ ] **Step 2: 写议题组查询的失败测试**

```python
# backend/tests/repositories/test_issue_repository_query.py
"""``list_for_user(q=)``（3c §2.3）。mig 166 的三个 trgm GIN（identifier /
title / description）从建好那天起没有任何谓词碰过（侦察 A3）。这条参数是它们的
第一个消费方，缺一列等于那个索引继续闲置而用户以为搜过了。"""
import pytest

from app.repositories.issue_repository import issue_repository

pytestmark = pytest.mark.unit


def _sql(stmt) -> str:
    from sqlalchemy.dialects import postgresql

    return str(stmt.compile(dialect=postgresql.dialect()))


def test_a_query_matches_identifier_title_and_description():
    sql = _sql(issue_repository._q_predicate_stmt("rain"))
    assert sql.count("ILIKE") == 3
    for col in ("identifier", "title", "description"):
        assert col in sql


def test_a_query_declares_its_escape_char():
    assert "ESCAPE" in _sql(issue_repository._q_predicate_stmt("100%"))


def test_a_blank_query_adds_no_predicate():
    # 空串与只有空白 = 没搜。加一个 ``%%`` 谓词会让 GIN 失效并全表扫。
    assert issue_repository._q_predicate("  ") is None
    assert issue_repository._q_predicate(None) is None
```

- [ ] **Step 3: 跑，确认红**

```bash
cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-search-endpoint/backend && \
  uv run pytest tests/repositories/test_issue_repository_query.py -q
```
预期：`AttributeError: 'IssueRepository' object has no attribute '_q_predicate_stmt'`（3 failed）。

- [ ] **Step 4: 实现 `list_for_user(q=)` 并确认绿**

`issue_repository.py` 顶部加 `from app.services.library.like_escape import
LIKE_ESCAPE_CHAR, escape_like`，在 `list_for_user` 之前加：

```python
    def _q_predicate(self, q):
        """``identifier ILIKE OR title ILIKE OR description ILIKE``，或 None。
        转义在这里做（拼模式者负责转义），ESCAPE 由 ``.ilike(escape=…)`` 声明
        ——Postgres 的 LIKE 没有默认转义符，只做 Python 侧等于没做。"""
        term = (q or "").strip()
        if not term:
            return None
        pattern = f"%{escape_like(term)}%"
        return or_(Issues.identifier.ilike(pattern, escape=LIKE_ESCAPE_CHAR),
                   Issues.title.ilike(pattern, escape=LIKE_ESCAPE_CHAR),
                   Issues.description.ilike(pattern, escape=LIKE_ESCAPE_CHAR))

    def _q_predicate_stmt(self, q):
        """测试用：把谓词包成一条可编译的 SELECT。"""
        return select(Issues.id).where(self._q_predicate(q))
```

`list_for_user` 签名追加 `q: Optional[str] = None`，在 `include_hidden` 那一支之后
`predicate = self._q_predicate(q)`，非 None 则 `base = base.where(predicate)`。
docstring 追加：`q` 是**过滤**，叠在 `visibility_predicate` 之上，永不放宽可见性；
走 mig 166 的三个 trgm GIN；`total` 因此是「匹配且可见」的条数，UI 据它显示
「N of M issues」而不是静默截断到 limit。

```bash
cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-search-endpoint/backend && \
  uv run pytest tests/repositories/ -q -k issue
```
预期：新 3 passed，既有 issue repository 套件全绿。

- [ ] **Step 5: 写服务层与路由的失败测试**

```python
# backend/tests/services/search/test_unified_search.py
"""``unified_search``——三组、一把可见性尺子（3c §2.3）。最重要的是**负例**：
别团队成员搜同一个词，三组必须全空且 HTTP 200。404 会把「不许你看」和「不存在」
混成一个答案，而这个端点是跨团队的。"""
import pytest

from app.services.search import service as mod

pytestmark = pytest.mark.unit
ME = "11111111-1111-1111-1111-111111111111"


class _Auth:
    user_id = ME


def _doc(**over):
    # entity_id 是 repository 真实返回的形状（契约补充的 kind:ref_id:version），
    # 不是登记行 id——fixture 写成 "42" 会让「命中 id 是一版的身份键」这件事
    # 在测试里成立而在真栈上不成立（「边界 mock 必须用真实 JSON 形状」）。
    base = {"entity_kind": "output", "entity_id": "script_shot:9:4",
            "title": "S3 · Shot 1 · MS",
            "body": "description: rain on glass", "kind": "script_shot", "ref_id": "9",
            "version": 4, "team_id": "7", "project_id": "3", "issue_id": "96",
            "run_id": "913", "owner_user_id": ME, "agent_id": "a-1", "model": "doubao",
            "status": None, "error_code": None, "score": 0.42}
    base.update(over)
    return base


async def _run(wired, **over):
    kw = {"auth": _Auth(), "q": "rain", "kinds": {"output"}, "team_id": None,
          "project_id": None, "issue_id": None}
    kw.update(over)
    return await mod.unified_search(**kw)


@pytest.fixture
def wired(monkeypatch):
    state = {"docs": [], "issues": ([], 0), "visible": {"96"},
             "identity": {"96": ("MH-96", 7)}}

    class _Docs:
        async def search(self, **kw):
            state["last_search"] = kw
            return [d for d in state["docs"] if d["entity_kind"] in kw["kinds"]]

    async def _issues(**kw):
        state["last_issues"] = kw
        return state["issues"]

    async def _visible(ids, auth):
        return {str(i) for i in ids if i is not None and str(i) in state["visible"]}

    async def _coords(ids):
        wanted = {str(i) for i in ids}
        return {k: v for k, v in state["identity"].items() if k in wanted}

    monkeypatch.setattr(mod, "get_search_docs_repository", lambda: _Docs())
    monkeypatch.setattr(mod, "_list_issues", _issues)
    monkeypatch.setattr(mod, "visible_issue_ids", _visible)
    monkeypatch.setattr(mod, "_issue_coordinates", _coords)
    return state


async def test_an_output_hit_carries_its_issue_key_and_a_deep_link(wired):
    wired["docs"] = [_doc()]
    hit = (await _run(wired)).groups.outputs[0]
    assert (hit.kind, hit.id, hit.issue_key) == ("output", "script_shot:9:4", "MH-96")
    assert hit.deep_link == "/team/7/todolist/MH-96"
    # 坐标从**列**来，不是把 id 切开来的——切它等于把拼法复制成第二份。
    assert hit.meta["version"] == 4 and hit.meta["ref_id"] == "9"
    assert "rain" in (hit.snippet or "")


async def test_a_hit_on_an_invisible_issue_is_dropped_not_refused(wired):
    wired["docs"], wired["visible"] = [_doc(issue_id="999")], set()
    res = await _run(wired)
    # 200 + 空组。别团队成员得到的是「没有匹配」，不是「有但不给你」。
    assert res.groups.outputs == [] and res.totals.outputs == 0


async def test_a_run_without_an_issue_survives_the_visibility_pass(wired):
    # 无议题的 run 已在 SQL 层按 owner 过滤过。Python 这一道再裁一次，会让
    # 每个人自己的画布道 run 永远搜不到。
    wired["docs"] = [_doc(entity_kind="run", entity_id="913", issue_id=None, kind=None,
                          ref_id=None, version=None, title="run", body="6403 images",
                          status="completed")]
    res = await _run(wired, q="images", kinds={"run"})
    assert [h.id for h in res.groups.runs] == ["913"]
    assert res.groups.runs[0].deep_link == ""


async def test_the_sql_layer_is_asked_for_three_times_the_page(wired):
    await _run(wired, kinds={"run", "output"}, limit_per_group=10)
    # 裁剪发生在 Python 里，所以 SQL 要多取——否则一页里有几条不可见，
    # 用户看到的就是一页残缺的结果而不是十条。
    assert wired["last_search"]["limit"] == 30


async def test_the_issue_group_goes_through_the_repository_not_the_projection(wired):
    wired["issues"] = ([{"id": 96, "identifier": "MH-96", "title": "Alpha rain",
                         "description": None, "team_id": 7, "status": "in_progress",
                         "assignee_user_label": "Alice"}], 37)
    res = await _run(wired, kinds={"issue"})
    assert wired["last_issues"]["q"] == "rain"
    hit = res.groups.issues[0]
    assert (hit.kind, hit.id, hit.issue_key) == ("issue", "96", "MH-96")
    assert hit.meta["status"] == "in_progress"
    # totals 是服务端的 total，不是这一页的长度——UI 靠它说「N of M」。
    assert res.totals.issues == 37


async def test_an_unrequested_group_is_empty_and_costs_nothing(wired):
    wired["docs"] = [_doc()]
    res = await _run(wired, kinds={"issue"})
    assert res.groups.outputs == [] and res.groups.runs == []
    assert "last_search" not in wired


def test_a_snippet_is_centred_on_the_match_and_short_bodies_pass_through():
    out = mod.snippet_for("a" * 200 + "rain" + "b" * 200, "rain")
    assert "rain" in out and out.startswith("…") and out.endswith("…")
    assert mod.snippet_for("rain on glass", "rain") == "rain on glass"


def test_a_body_that_does_not_contain_the_term_still_yields_a_head():
    # 命中可能来自 title。正文没有那个词时给开头而不是 None——空 snippet 会让
    # 那一行看起来像一条坏数据。
    assert mod.snippet_for("nothing relevant here", "rain").startswith("nothing relevant")
```

```python
# backend/tests/api/test_search_router.py
"""``GET /api/v1/search`` 的参数契约（3c §2.3）。"""
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.unit


@pytest.fixture
def client(monkeypatch):
    from app.api import search_router as mod
    from app.core.auth import get_current_user
    from app.schemas.search import SearchGroups, SearchResponse, SearchTotals

    app = FastAPI()
    app.include_router(mod.router, prefix="/api/v1")

    class _Auth:
        user_id = "11111111-1111-1111-1111-111111111111"

    app.dependency_overrides[get_current_user] = lambda: _Auth()
    calls: list = []

    async def _search(**kw):
        calls.append(kw)
        return SearchResponse(groups=SearchGroups(), totals=SearchTotals(), took_ms=1)

    monkeypatch.setattr(mod, "unified_search", _search)
    return app, calls


async def _get(app, **params):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.get("/api/v1/search", params=params)


async def test_a_short_query_is_a_typed_400_after_stripping(client):
    res = await _get(client[0], q="  r  ")
    assert res.status_code == 400
    # detail 必须是 dict——生产的 ErrorResponse 外壳只让 dict 活到 details；
    # 字符串会塌成 http_400 而前端读的是 details.code（CLAUDE.md 2026-09-09）。
    assert res.json()["detail"]["code"] == "query_too_short"


async def test_kinds_default_to_all_three(client):
    app, calls = client
    await _get(app, q="rain")
    assert calls[-1]["kinds"] == {"issue", "run", "output"}


async def test_an_unknown_kind_is_dropped_but_all_unknown_is_a_typed_400(client):
    app, calls = client
    res = await _get(app, q="rain", kinds="issue,bogus")
    assert res.status_code == 200 and calls[-1]["kinds"] == {"issue"}
    assert (await _get(app, q="rain", kinds="bogus")).json()["detail"]["code"] == "unknown_kinds"
```

- [ ] **Step 6: 跑，确认红**

```bash
cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-search-endpoint/backend && \
  uv run pytest tests/services/search/test_unified_search.py tests/api/test_search_router.py -q
```
预期：两个文件都 collection error（`app.services.search.service` / `app.api.search_router` 不存在）。

- [ ] **Step 7: 写 `schemas/search.py`**

四个模型的字段**逐字照抄契约 §1**（`SearchGroups` 三个列表与 `SearchTotals` 三个
计数都给缺省值，这样路由测试可以 `SearchGroups()` 构造空响应）。两段必须写上的
docstring：

```
SearchHit: """一条命中。``id`` 是**不透明身份键**，一律字符串：issue / run 是
Snowflake BIGINT（落进 JSON number 会在浏览器里丢精度），output 是
``kind:ref_id:version``（契约补充——一版一行，而「一版」没有可用的单列 id）。
客户端只拿它做 React key 与去重，**不解析**：产出的三个坐标在 ``meta`` 里各占
一个键，切 id 等于把拼法复制成第二份。``deep_link`` 是**空串**而不是 null 时
表示「这条命中没有可跳转的页面」（无议题的个人 run），前端渲染不可点的行。
``meta`` —— run: {status, model, error_code}；output: {kind, ref_id, version}；
issue: {status, assignee_name}。"""

SearchTotals: """每组服务端一共有多少条匹配。议题组是 repository 的 total
（精确）。run / 产出两组是**裁剪后这一页**的条数——投影表上没有便宜的 count，
而一个会骗人的总数比没有更糟。UI 只对议题组显示「N of M」。"""
```

- [ ] **Step 8: 写 `search/service.py`**

模块 docstring：跨团队边界完全由本模块负责（`scoped_sql` 不管 `agent_runs`，侦察
F20），两道门——SQL 层 team/owner 谓词 + Python 层 `visible_issue_ids`；无议题的
run **不进**第二道。

```python
#: snippet 在命中词两侧各留多少字符。
_SNIPPET_PAD = 60
ALL_SEARCH_KINDS = frozenset({"issue", "run", "output"})


def snippet_for(text, q):
    """命中片段：Python 截，不用 ``ts_headline``。后者要一个 tsvector 配置，而
    本仓正文含中文且没有分词（侦察 D2）——英文配置切中文等于按空格切。"""
    if not text:
        return None
    body = text.replace("\n", " ")
    idx = body.lower().find(q.lower())
    if idx < 0:
        # 命中来自标题。给正文开头而不是 None——空 snippet 看起来像坏数据。
        head = body[: _SNIPPET_PAD * 2]
        return head + ("…" if len(body) > len(head) else "")
    start, end = max(0, idx - _SNIPPET_PAD), min(len(body), idx + len(q) + _SNIPPET_PAD)
    return ("…" if start else "") + body[start:end] + ("…" if end < len(body) else "")
```

`_list_issues(**kw)` 延迟 import `issue_repository.list_for_user`；
`_issue_coordinates(ids)` 一条 IN 查 `{str(issue_id): (identifier, team_id)}`
（深链要这两样）。`_issue_hit(row, q)` / `_doc_hit(doc, q, coords)` 各把一行摊成
`SearchHit`，`deep_link=issue_deep_link(team_id=…, issue_key=…) or ""`，`_doc_hit`
的 meta 按 `entity_kind` 二选一；`_doc_hit` 处写注释：

```python
        # 深链只到议题页：search_docs 没有 step/turn 列（契约 §0）。造一条
        # ``?step=`` 等于声称这条命中出自某一步，而我们并不知道是哪一步。
```

```python
async def unified_search(*, auth, q, kinds, team_id=None, project_id=None,
                         issue_id=None, limit_per_group=10) -> SearchResponse:
    started = time.monotonic()
    issues, issue_total = [], 0
    if "issue" in kinds:
        rows, issue_total = await _list_issues(
            user_id=str(auth.user_id), q=q, project_id=project_id, team_id=team_id,
            limit=limit_per_group, offset=0)
        issues = [_issue_hit(row, q) for row in rows]

    doc_kinds = {k for k in kinds if k in ("run", "output")}
    runs, outputs = [], []
    if doc_kinds:
        docs = await get_search_docs_repository().search(
            q=q, kinds=doc_kinds,
            team_ids=[int(team_id)] if team_id is not None else [],
            user_id=str(auth.user_id), project_id=project_id, issue_id=issue_id,
            # 裁剪在 Python 里做，所以 SQL 要多取——否则一页里有几条不可见，
            # 读者看到的是一页残缺的结果而不是十条（spec §2.3）。
            limit=limit_per_group * 3)
        with_issue = {d.get("issue_id") for d in docs if d.get("issue_id") is not None}
        visible = await visible_issue_ids(with_issue, auth)
        coords = await _issue_coordinates(visible)
        for doc in docs:
            iid = doc.get("issue_id")
            # 无议题的 run 在 SQL 层已按 owner 过滤，第二道不再碰它。
            if iid is not None and str(iid) not in visible:
                continue
            bucket = runs if doc["entity_kind"] == "run" else outputs
            if len(bucket) < limit_per_group:
                bucket.append(_doc_hit(doc, q, coords))

    return SearchResponse(
        groups=SearchGroups(issues=issues, runs=runs, outputs=outputs),
        totals=SearchTotals(issues=issue_total, runs=len(runs), outputs=len(outputs)),
        took_ms=int((time.monotonic() - started) * 1000))
```

- [ ] **Step 9: 写路由并注册**

```python
# backend/app/api/search_router.py
router = APIRouter(prefix="/search", tags=["Search"])

#: 一个字的查询在 trgm 上退化成全表匹配，而它几乎一定是「还在打字」。
MIN_QUERY_CHARS = 2


def _reject(code: str, message: str) -> HTTPException:
    # detail 必须是 dict：生产的 ErrorResponse 外壳只让 dict 落到 details，
    # 字符串会塌成 http_400 + "400 Bad Request"（CLAUDE.md 2026-09-09）。
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                         detail={"code": code, "message": message})


@router.get("", response_model=SearchResponse)
async def search(
    auth: AuthDep,
    q: str = Query(..., max_length=200),
    kinds: Optional[str] = Query(None),
    team_id: Optional[int] = Query(None),
    project_id: Optional[int] = Query(None),
    issue_id: Optional[int] = Query(None),
    limit_per_group: int = Query(10, ge=1, le=50),
) -> SearchResponse:
    term = q.strip()
    if len(term) < MIN_QUERY_CHARS:
        raise _reject("query_too_short",
                      f"a search needs at least {MIN_QUERY_CHARS} characters")
    if kinds is None:
        wanted = set(ALL_SEARCH_KINDS)
    else:
        asked = {k.strip() for k in kinds.split(",") if k.strip()}
        # 认得的留下，认不得的丢掉——新版本前端多送一个 kind 不该让整次搜索
        # 失败。全都认不得才是拒绝：那时调用方要的东西一件没给。
        wanted = asked & ALL_SEARCH_KINDS
        if not wanted:
            raise _reject("unknown_kinds", f"none of {sorted(asked)} is a searchable kind")
    return await unified_search(auth=auth, q=term, kinds=wanted, team_id=team_id,
                                project_id=project_id, issue_id=issue_id,
                                limit_per_group=limit_per_group)
```

`app/api/__init__.py` 加 `from app.api.search_router import router as search_router  # noqa: E402`
与 `api_router.include_router(router=search_router, tags=["Search"])`。

- [ ] **Step 10: 跑，确认绿**

```bash
cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-search-endpoint/backend && \
  uv run pytest tests/services/search/ tests/api/test_search_router.py \
    tests/repositories/test_issue_repository_query.py -q && \
  uv run ruff check . && uv run black --check . && uv run isort --check-only .
```
预期：服务层 8 passed、路由 3 passed、repository 3 passed。

- [ ] **Step 11: 突变验证**

① 删掉 `unified_search` 的 `if iid is not None and str(iid) not in visible: continue`，
确认 `test_a_hit_on_an_invisible_issue_is_dropped_not_refused` 转红；恢复。
② 同一行去掉 `iid is not None` 守卫，确认
`test_a_run_without_an_issue_survives_the_visibility_pass` 转红——这条突变正是
「个人 run 永远搜不到」那个缺陷的形状；恢复。
③ `limit=limit_per_group * 3` 改成 `limit=limit_per_group`，确认
`test_the_sql_layer_is_asked_for_three_times_the_page` 转红；恢复并重跑全绿。

- [ ] **Step 12: 提交与 PR**

```bash
W=/Volumes/program/project-code/repos/nous-app/.worktrees/3c-search-endpoint
git -C "$W" add -A && \
git -C "$W" commit -m "feat(search): GET /api/v1/search 三组统一检索——议题走 mig 166 的 trgm，run/产出走 search_docs，可见性两道门（harness 三期 3c Task 14）" && \
git -C "$W" push -u origin feat/3c-search-endpoint && \
gh pr create --repo iocrazy/nous-app --base master --head feat/3c-search-endpoint \
  --title "feat(search): GET /api/v1/search 三组统一检索（3c Task 14）" \
  --body "3c §2.3。跨团队边界完全由本端点负责（scoped_sql 不管 agent_runs）：SQL 层 team/owner 谓词 + Python 层 visible_issue_ids 批量裁剪。别团队成员拿 200 + 三组全空，不是 404。mig 166 那三个 trgm GIN 第一次有谓词碰它们。"
```

---

### Task 15: `GET /issues?q=` 与 Issues 页搜索改服务端

**Files:**
- Modify `backend/app/api/issues_router.py`（`list_issues` 152-182）
- Modify `frontend/services/issuesService.ts`（`IssueListFilters` 与 `listIssues` 181-196）
- Modify `frontend/pages/TodolistPage.tsx`（`refreshIssues` 174-188 与传给 `IssueListView` 的 props）
- Modify `frontend/components/Todolist/IssueListView.tsx`（props 448、`filtered` 621-630、搜索框 767-777）
- Modify `frontend/public/locales/en.json` 与 `zh.json`
- Create `backend/tests/api/test_issues_query_param.py`
- Create `frontend/components/Todolist/issueListTestFactories.ts`、`issueSearchServerSide.test.tsx`

**Interfaces:**
- Produces `GET /api/v1/issues/?q=<term>`（透传 `list_for_user(q=)`）；`listIssues({ q })`
- Produces `IssueListView` 新 props `onSearchChange?: (q: string) => void`、`serverQuery?: string`、`totalCount?: number | null`
- Consumes Task 14 的 `IssueRepository.list_for_user(q=)`

- [ ] **Step 1: 建 worktree（Task 14 合并后）**

```bash
git -C /Volumes/program/project-code/repos/nous-app worktree add -b feat/3c-issues-server-search \
  /Volumes/program/project-code/repos/nous-app/.worktrees/3c-issues-server-search origin/master
```

- [ ] **Step 2: 写后端透传的失败测试**

```python
# backend/tests/api/test_issues_query_param.py
"""``GET /issues?q=``（3c §2.3）。在此之前 Issues 页的搜索是对**最新 200 行**的
内存子串匹配（侦察 A3）：第 201 件议题在 UI 上不可检索，而这一点不报错，只会
安静地搜不到。"""
import pytest

pytestmark = pytest.mark.unit


class _Auth:
    user_id = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def seen(monkeypatch):
    from app.api import issues_router as mod

    captured: dict = {}

    async def _list(**kw):
        captured.update(kw)
        return ([], 0)

    monkeypatch.setattr(mod.issue_repository, "list_for_user", _list)
    return captured


async def _call(**over):
    from app.api import issues_router as mod

    kw = {"auth": _Auth(), "status_filter": None, "project_id": None, "team_id": None,
          "include_hidden": False, "limit": 50, "offset": 0, "q": None}
    kw.update(over)
    return await mod.list_issues(**kw)


async def test_the_router_hands_q_to_the_repository(seen):
    res = await _call(q="rain")
    assert seen["q"] == "rain"
    # total 是服务端的匹配总数——UI 显示「N of M issues」靠它，而不是靠页面
    # 长度（那个只会说 50）。
    assert res.total == 0


async def test_no_q_is_none_not_an_empty_string(seen):
    await _call()
    assert seen["q"] is None
```

- [ ] **Step 3: 跑，确认红**

```bash
cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-issues-server-search/backend && \
  uv run pytest tests/api/test_issues_query_param.py -q
```
预期：`TypeError: list_issues() got an unexpected keyword argument 'q'`（2 failed）。

- [ ] **Step 4: 实现后端并确认绿**

`issues_router.py::list_issues` 签名追加 `q: Optional[str] = Query(None, max_length=200)`，
透传 `q=q`；docstring 追加：`q` 走 `identifier / title / description` 三列的 trgm
（mig 166），是过滤、叠在可见性之上，`total` 是「匹配且可见」的条数。

```bash
cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-issues-server-search/backend && \
  uv run pytest tests/api/test_issues_query_param.py -q && uv run pytest tests/api/ -q -k issue
```
预期：新 2 passed，既有 issues 路由套件全绿。

- [ ] **Step 5: 写前端的失败测试**

```tsx
// frontend/components/Todolist/issueSearchServerSide.test.tsx
/**
 * Issues 页的搜索改服务端（3c §2.3）。三件事：防抖 250 ms 后只发一次；本地
 * `includes` 已删（服务端已筛过，再筛一次会二次裁剪服务端的命中，出现
 * 「搜到了却不显示」，而两处口径的差异不会有任何地方报错）；总数按
 * 「N of M issues」显示。
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import { IssueListView } from './IssueListView';
import { baseProps, uiIssue } from './issueListTestFactories';

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

const mount = (props: Record<string, unknown>) =>
  render(<MemoryRouter><IssueListView {...baseProps()} {...props} /></MemoryRouter>);

describe('Issues 页服务端搜索', () => {
  it('debounces to one call 250 ms after the last keystroke', () => {
    const onSearchChange = vi.fn();
    mount({ onSearchChange });
    const box = screen.getByPlaceholderText('Search issues…');
    for (const v of ['ra', 'rai', 'rain']) fireEvent.change(box, { target: { value: v } });
    act(() => { vi.advanceTimersByTime(249); });
    expect(onSearchChange).not.toHaveBeenCalled();
    act(() => { vi.advanceTimersByTime(1); });
    expect(onSearchChange).toHaveBeenCalledTimes(1);
    expect(onSearchChange).toHaveBeenCalledWith('rain');
  });

  it('renders every row the server returned, without a second local filter', () => {
    // 服务端按 description 命中的一行，前端 haystack 里没有那个词。本地
    // includes 还在的话这一行会被二次裁掉。
    mount({ issues: [uiIssue({ identifier: 'MH-96', title: 'Alpha', description: null })],
            onSearchChange: vi.fn(), serverQuery: 'rain' });
    expect(screen.getByText(/MH-96/)).toBeTruthy();
  });

  it('says N of M while searching and a plain count otherwise', () => {
    const { unmount } = mount({ issues: [uiIssue({ identifier: 'MH-96' })],
                                onSearchChange: vi.fn(), serverQuery: 'rain', totalCount: 37 });
    expect(screen.getByTestId('issue-list-count').textContent).toContain('1 of 37');
    unmount();
    mount({ issues: [uiIssue({ identifier: 'MH-96' })], onSearchChange: vi.fn() });
    const text = screen.getByTestId('issue-list-count').textContent ?? '';
    expect(text).toContain('1 issues');
    expect(text).not.toContain(' of ');
  });
});
```

同批新建 `issueListTestFactories.ts`：导出 `baseProps()`（按 `IssueListViewProps` 的
真实必填字段构造，`scope` 用 `{ type: 'team', id: '7' }`）与 `uiIssue(over)`
（**前端 `UiIssue` 形状**，即 `toUiIssue` 的产物，所以 id 是 string，不是 wire 形状）。

- [ ] **Step 6: 跑，确认红**

```bash
cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-issues-server-search/frontend && \
  npx vitest run components/Todolist/issueSearchServerSide.test.tsx
```
预期：3 failed（`onSearchChange` 不是 prop、`issue-list-count` 不存在）。

- [ ] **Step 7: 实现前端**

`issuesService.ts`：`IssueListFilters` 加 `q?: string`；`listIssues` 加
`if (filters.q) params.set('q', filters.q);`。

`IssueListView.tsx`：props 加 `onSearchChange?` / `serverQuery?` / `totalCount?`；
在 `/` 快捷键 effect 之后新增防抖 effect（保持 effect 声明顺序稳定）：

```tsx
  // 服务端搜索（3c §2.3）。250 ms——比资产页签的 200 ms 长，因为这一次击键
  // 换的是一整页议题，而不是一个弹层的行。
  useEffect(() => {
    if (!onSearchChange) return;
    const id = setTimeout(() => onSearchChange(search.trim()), 250);
    return () => clearTimeout(id);
  }, [search, onSearchChange]);
```

`filtered`（621-630）删掉 `hay` / `includes` 那一段，只留 phase / wakeup：

```tsx
  const filtered = useMemo(() => {
    // 文本匹配已经在服务端做完（3c）。这里再筛一次会按前端自己的 haystack
    // 二次裁剪服务端的命中——用户会看到「搜到了却不显示」，而两处口径的差异
    // 不会有任何地方报错。
    const byPhase = phaseFilter ? filteredByPanel.filter((i) => issuePhase(i) === phaseFilter) : filteredByPanel;
    return scheduledOnly ? byPhase.filter(hasWakeup) : byPhase;
  }, [filteredByPanel, phaseFilter, scheduledOnly]);
```

搜索框那个 `<div className="relative flex-1 max-w-md">` 之后加计数：

```tsx
        <span data-testid="issue-list-count" className="shrink-0 text-[12px] text-ink-500 tabular-nums">
          {serverQuery
            ? t('issues.countOfTotal', '{{n}} of {{total}} issues',
                { n: filtered.length, total: totalCount ?? filtered.length })
            : t('issues.count', '{{n}} issues', { n: filtered.length })}
        </span>
```

`TodolistPage.tsx`：`refreshIssues` 多接一个 `q` 并塞进 `filters`；新增
`query` / `issuesTotal` 两个 state，在 `setIssues(...)` 旁 `setIssuesTotal(resp.total)`，
`query` 变化时重拉；把 `onSearchChange={setQuery}` / `serverQuery={query}` /
`totalCount={issuesTotal}` 传下去。i18n：`en.json` 的 `issues` 段加 `count` /
`countOfTotal`（英文），`zh.json` 同键中文值。

- [ ] **Step 8: 跑，确认绿**

```bash
cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-issues-server-search/frontend && \
  npx vitest run components/Todolist/ && npm run typecheck
```
预期：新 3 passed，既有 Todolist 套件全绿，typecheck 零错。

- [ ] **Step 9: 突变验证**

① 防抖 `250` 改成 `0`，确认第一条转红（会看到 3 次调用）；恢复。
② 把删掉的本地 `includes` 加回 `filtered`，确认第二条转红；恢复。
③ 后端 `q=q` 改成 `q=None`，确认 `test_the_router_hands_q_to_the_repository` 转红；恢复并重跑。

- [ ] **Step 10: 提交与 PR**

```bash
W=/Volumes/program/project-code/repos/nous-app/.worktrees/3c-issues-server-search
git -C "$W" add -A && \
git -C "$W" commit -m "feat(issues): 搜索移到服务端——GET /issues?q= 走 mig 166 的 trgm，删掉最新 200 行的内存子串匹配（harness 三期 3c Task 15）" && \
git -C "$W" push -u origin feat/3c-issues-server-search && \
gh pr create --repo iocrazy/nous-app --base master --head feat/3c-issues-server-search \
  --title "feat(issues): Issues 页搜索改服务端（3c Task 15）" \
  --body "3c §2.3。此前搜索是对最新 200 行的内存子串匹配，第 201 件议题在 UI 上不可检索且不报错。改成服务端 q 参数后走 mig 166 的三个 trgm GIN；总数显示「N of M issues」，不再静默截断。"
```

---

### Task 16: 引用镜像表、血缘 `cited_in`、引用归属放宽为「链可见」

**Files:**
- Create `backend/app/repositories/output_citations_repository.py`、`backend/app/services/deliverables/citations.py`、`backend/app/services/deliverables/visibility.py`
- Modify `backend/app/services/ai/chat/conversations_ai_store.py`（`_DISPLAY_ATTACHMENT_KEYS` 111-125、`append_user_message` 638-694）
- Modify `backend/app/services/ai/chat/output_ref_resolver.py`（模块 docstring 第二条、`ChatOutputRef`、`_verified`、`_stamped`、`resolve_output_refs`、`citations_for_transcript`）
- Modify `backend/app/api/issue_messages_router.py`（调用点 680-695 传 `auth`）
- Modify `backend/app/api/outputs_router.py`（`get_output_lineage` 145-200）
- Modify `backend/app/schemas/outputs.py`（新增 `CitedIn`、`OutputVersion` 加两字段）
- Create `backend/tests/repositories/test_output_citations_repository.py`、`backend/tests/services/deliverables/test_citations.py`、`backend/tests/services/ai/chat/test_output_ref_chain_visible.py`
- Modify `backend/tests/api/test_outputs_router.py`（血缘三条；不存在则新建）

**Interfaces:**
- Produces `OutputCitationsRepository.insert_many(rows: list[dict], *, session) -> None` / `.list_for_ref(kind, ref_id, version) -> list[dict]` / `.counts_for_chain(kind, ref_id) -> dict[int, int]`、`get_output_citations_repository()`
- Produces `record_output_citations(session, *, message_row: dict, attachments, user_id: str, issue_id, conversation_id) -> None`
- Produces `assert_chain_visible(kind: str, ref_id: str, auth) -> list[dict]`
- Produces `resolve_output_refs(attachments, *, issue_id, auth)`（新增 `auth`）
- Produces wire `OutputVersion.cited_count: int` / `.cited_in: list[CitedIn]`；附件与 transcript 投影多带 `issue_key`
- Consumes Part A Task 1 的 `OutputCitations` ORM

- [ ] **Step 1: 建 worktree**

```bash
git -C /Volumes/program/project-code/repos/nous-app worktree add -b feat/3c-output-citations \
  /Volumes/program/project-code/repos/nous-app/.worktrees/3c-output-citations origin/master
```

- [ ] **Step 2: 写镜像写点与 repository 的失败测试**

```python
# backend/tests/services/deliverables/test_citations.py
"""引用镜像（3c §2.2）。「这一版产出被谁引用过」今天无法回答——引用只活在
``messages.body`` 的 jsonb 里，那一列零索引（侦察 C4）。对照组是画布侧的
``canvas_asset_refs``：反查靠物化镜像表解决。"""
import pytest

from app.services.deliverables import citations as mod

pytestmark = pytest.mark.unit
ME = "11111111-1111-1111-1111-111111111111"
REF = {"kind": "output_ref", "ref_kind": "script_shot", "ref_id": "9", "version": 4}


class _RepoSpy:
    def __init__(self, boom=False):
        self.batches, self._boom = [], boom

    async def insert_many(self, rows, *, session=None):
        if self._boom:
            raise RuntimeError("relation output_citations does not exist")
        self.batches.append(rows)


@pytest.fixture
def spy(monkeypatch):
    s = _RepoSpy()
    monkeypatch.setattr(mod, "get_output_citations_repository", lambda: s)
    return s


async def _record(attachments):
    await mod.record_output_citations(
        None, message_row={"id": "5001", "conversation_id": "700"},
        attachments=attachments, user_id=ME, issue_id=96, conversation_id=700)


async def test_only_output_refs_are_mirrored(spy):
    await _record([{"kind": "resource_ref", "resource_id": "1"}, dict(REF),
                   {"kind": "asset_ref", "asset_id": "2"}])
    assert spy.batches[-1] == [{
        "kind": "script_shot", "ref_id": "9", "version": 4, "issue_id": 96,
        "conversation_id": 700, "message_id": 5001, "cited_by_user_id": ME}]


async def test_no_citations_touches_the_table_at_all(spy):
    await _record([{"kind": "resource_ref"}])
    await _record(None)
    assert spy.batches == []


async def test_the_same_version_cited_twice_in_one_message_is_one_row(spy):
    await _record([dict(REF), dict(REF)])
    # UNIQUE (message_id, kind, ref_id, version) 会拦，但让它走到数据库再靠
    # 约束兜底，等于每次都赌 INSERT 的失败模式。在这里去重。
    assert len(spy.batches[-1]) == 1


async def test_a_malformed_citation_is_skipped_and_logged(spy, caplog):
    await _record([{"kind": "output_ref", "ref_kind": "script_shot", "ref_id": "9"}])
    # 没有 version 的引用只能来自「发帖口没校验就落库」的接线漂移。静默跳过
    # 会让那种漂移永远不被发现。
    assert spy.batches == []
    assert any("citation" in r.message for r in caplog.records)


async def test_a_failing_mirror_never_unposts_the_message(monkeypatch, caplog):
    monkeypatch.setattr(mod, "get_output_citations_repository", lambda: _RepoSpy(boom=True))
    await _record([dict(REF)])
    assert any("output_citations" in r.message for r in caplog.records)
```

```python
# backend/tests/repositories/test_output_citations_repository.py
"""反查的两个读法（3c §2.2）。"""
import pytest

from app.repositories.output_citations_repository import OutputCitationsRepository

pytestmark = pytest.mark.unit


def _sql(stmt) -> str:
    from sqlalchemy.dialects import postgresql

    return str(stmt.compile(dialect=postgresql.dialect()))


def test_list_for_ref_pins_all_three_coordinates():
    sql = _sql(OutputCitationsRepository()._list_stmt("script_shot", "9", 4))
    for col in ("kind", "ref_id", "version"):
        assert col in sql
    assert "ORDER BY" in sql.upper()


def test_counts_for_chain_groups_by_version_and_does_not_filter_on_it():
    sql = _sql(OutputCitationsRepository()._counts_stmt("script_shot", "9"))
    # 整条链一次查完——每版一次查询会让二十版的链发二十次往返。
    assert "GROUP BY" in sql.upper() and "version =" not in sql
```

- [ ] **Step 3: 跑，确认红**

```bash
cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-output-citations/backend && \
  uv run pytest tests/services/deliverables/test_citations.py \
    tests/repositories/test_output_citations_repository.py -q
```
预期：两个文件 collection error（两个模块都不存在）。

- [ ] **Step 4: 写 repository 与 `citations.py`，接写点**

`output_citations_repository.py` docstring 写明「删消息**不级联**：引用是历史事实」。
`insert_many` 在 `session` 非空时直接 `session.execute`，否则自开 `write_scope()`；
`_list_stmt(kind, ref_id, version)` 按三坐标过滤并 `ORDER BY created_at DESC`，
`list_for_ref` 把行摊成 `{issue_id, message_id, user_id, at}`（id 一律 str）；
`_counts_stmt(kind, ref_id)` 是 `select(version, count()).group_by(version)`，
`counts_for_chain` 返回 `{int: int}`。模块尾部 `get_output_citations_repository()`。

`citations.py` docstring 两条：写点唯一（注释 / 唤醒 / 聊天面板守卫三条入口都经
`append_user_message` 落库）；`session` 允许 `None` 的理由（本文件顶部偏离 2）。

```python
def _rows(attachments, *, message_id, user_id, issue_id, conversation_id) -> list[dict]:
    out, seen = [], set()
    for att in attachments or []:
        if not isinstance(att, dict) or att.get("kind") != ATTACHMENT_KIND:
            continue
        try:
            key = (str(att["ref_kind"]), str(att["ref_id"]), int(att["version"]))
        except (KeyError, TypeError, ValueError):
            # 只可能来自「发帖口没校验就落库」的接线漂移。静默跳过会让那种漂移
            # 永远不被发现（与 output_refs_from_attachments 同一口径）。
            logger.warning(f"[citations] dropping an unusable citation: {att!r}")
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append({"kind": key[0], "ref_id": key[1], "version": key[2],
                    "issue_id": int(issue_id) if issue_id is not None else None,
                    "conversation_id": int(conversation_id) if conversation_id is not None else None,
                    "message_id": int(message_id), "cited_by_user_id": str(user_id)})
    return out


async def record_output_citations(session, *, message_row, attachments, user_id,
                                  issue_id, conversation_id) -> None:
    try:
        rows = _rows(attachments, message_id=message_row.get("id"), user_id=user_id,
                     issue_id=issue_id, conversation_id=conversation_id)
        if not rows:
            return
        await get_output_citations_repository().insert_many(rows, session=session)
    except Exception as exc:  # noqa: BLE001 — 见模块 docstring
        logger.opt(exception=True).error(
            f"[citations] output_citations mirror FAILED for message "
            f"{message_row.get('id')}: {exc!r} — the comment posted, the "
            "back-reference did not")
```

`conversations_ai_store.py::append_user_message`，在 `row = await ...send_message(...)`
之后、`return` 之前：

```python
        # 3c §2.2：引用镜像。写点唯一。议题只在真有引用时才查，普通消息零额外往返。
        if any(isinstance(a, dict) and a.get("kind") == "output_ref"
               for a in (attachments or [])):
            from app.repositories.issue_repository import issue_repository
            from app.services.deliverables.citations import record_output_citations

            issue = await issue_repository.get_by_session(_bigint(session_id))
            await record_output_citations(
                None, message_row=row, attachments=attachments, user_id=user_id,
                issue_id=(issue or {}).get("id"),
                conversation_id=row.get("conversation_id"))
```

`_DISPLAY_ATTACHMENT_KEYS`（111-125）追加 `"issue_key"`——引用可以跨议题之后，
线程里的引用卡要说得出来源。

```bash
cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-output-citations/backend && \
  uv run pytest tests/services/deliverables/test_citations.py \
    tests/repositories/test_output_citations_repository.py -q
```
预期：7 passed。

- [ ] **Step 5: 写「链可见」与血缘 `cited_in` 的失败测试**

```python
# backend/tests/services/ai/chat/test_output_ref_chain_visible.py
"""引用归属从「属于本议题」放宽为「链对调用方可见」（3c §2.4）。放宽之后 @ 页签
能跨议题找产出，而**尺子只有一把**：``outputs_router.visible_chain``。第二把尺子
意味着「能引的」和「能看的」两个集合会分叉，而分叉的那一侧不会报错。"""
import pytest

from app.services.ai.chat import output_ref_resolver as mod

pytestmark = pytest.mark.unit
ME = "11111111-1111-1111-1111-111111111111"


class _Auth:
    user_id = ME


def _att(version=4):
    return {"kind": "output_ref", "ref_kind": "script_shot", "ref_id": "9",
            "version": version}


@pytest.fixture
def chain(monkeypatch):
    state = {"rows": [{"version": 4, "title": "S3 · Shot 1 · MS", "issue_id": "98",
                       "issue_key": "MH-98", "run_id": "913"}], "visible": True}

    async def _assert(kind, ref_id, auth):
        if not state["visible"]:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="not found")
        return state["rows"]

    monkeypatch.setattr(mod, "assert_chain_visible", _assert)
    return state


async def test_a_version_from_another_issue_resolves_when_the_chain_is_visible(chain):
    res = await mod.resolve_output_refs([_att()], issue_id=96, auth=_Auth())
    assert (res.refs[0].ref_id, res.refs[0].version) == ("9", 4)
    # 附件多带来源议题，线程里的引用卡据它显示 MH-98 chip。
    assert res.attachments[0]["issue_key"] == "MH-98"
    # 同一批坐标的第二个读者（线程「引用 N 件」那一行）也要说得出来源。
    assert mod.citations_for_transcript(res.refs)[0]["issue_key"] == "MH-98"


async def test_an_invisible_chain_is_the_same_refusal_as_a_missing_version(chain):
    chain["visible"] = False
    with pytest.raises(mod.OutputRefRefused) as exc:
        await mod.resolve_output_refs([_att()], issue_id=96, auth=_Auth())
    # 同一个 code：分出「存在但你不能引」等于确认那个对象存在。
    assert exc.value.code == mod.UNRESOLVABLE
    chain["visible"] = True
    with pytest.raises(mod.OutputRefRefused):
        await mod.resolve_output_refs([_att(version=9)], issue_id=96, auth=_Auth())


def test_the_chat_entry_point_still_refuses_outright():
    # 聊天面板没有议题可作用域，引用一律拒绝（spec §2.4 明写仍然拒绝）。
    with pytest.raises(mod.OutputRefRefused):
        mod.refuse_citations_without_issue([_att()])
```

`backend/tests/api/test_outputs_router.py` 追加三条（fixture `lineage_wired` 桩掉
`visible_chain` / `counts_for_chain` / `list_for_ref` / `visible_issue_ids` /
`map_identifiers`，并记 `count_calls`）：

```python
async def test_a_version_reports_its_citation_count_in_full(lineage_wired):
    # 被引 3 次、你只看得见 1 条，是允许的诚实答案（spec §2.2）。
    lineage_wired["counts"] = {4: 3}
    lineage_wired["cited"] = {4: [
        {"issue_id": "96", "message_id": "5001", "user_id": ME, "at": _AT},
        {"issue_id": "999", "message_id": "5002", "user_id": OTHER, "at": _AT}]}
    lineage_wired["visible"] = {"96"}
    res = await get_output_lineage("script_shot", "9", _Auth())
    v4 = next(v for v in res.versions if v.version == 4)
    assert v4.cited_count == 3
    assert [c.issue_key for c in v4.cited_in] == ["MH-96"]


async def test_an_uncited_version_says_zero_not_null(lineage_wired):
    res = await get_output_lineage("script_shot", "9", _Auth())
    assert all(v.cited_count == 0 and v.cited_in == [] for v in res.versions)


async def test_the_chain_costs_one_count_query_not_one_per_version(lineage_wired):
    await get_output_lineage("script_shot", "9", _Auth())
    assert lineage_wired["count_calls"] == 1
```

- [ ] **Step 6: 跑，确认红**

```bash
cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-output-citations/backend && \
  uv run pytest tests/services/ai/chat/test_output_ref_chain_visible.py \
    tests/api/test_outputs_router.py -q
```
预期：`AttributeError: module 'app.services.ai.chat.output_ref_resolver' has no attribute
'assert_chain_visible'`、`TypeError: resolve_output_refs() got an unexpected keyword
argument 'auth'`；血缘三条 `AttributeError: 'OutputVersion' object has no attribute 'cited_count'`。

- [ ] **Step 7: 实现「链可见」**

```python
# backend/app/services/deliverables/visibility.py
"""产出链可见性的**唯一**入口（3c §2.4）。

3a 起这把尺子长在 ``outputs_router.visible_chain`` 上，消费方已经三个（血缘端点、
回退服务、现在还有引用解析）。抽到服务层不是为了整洁，是为了「能引的集合」与
「能看的集合」在定义上就无法分叉。延迟 import 打断 router ↔ service 的环——
``revert.py::visible_chain`` 与 ``registry.py`` 的 ``in_unit_of_work`` 是同一手法。"""


async def assert_chain_visible(kind: str, ref_id: str, auth) -> list[dict]:
    """版本链（新到旧），或 404。**每一次拒绝都是 404**——调用方看不见的对象
    必须与从未登记过的对象无从区分。"""
    from app.api.outputs_router import visible_chain

    return await visible_chain(kind, str(ref_id), auth)
```

`output_ref_resolver.py`：顶部 import `assert_chain_visible`；模块 docstring 第二条
改写为——

```
**二、归属是「这条链你看得见」，不是「它属于本议题」。**
3c §2.4 放宽了这一条：同项目另一件议题的产出可以被引用，因为读者要说的就是
「你在 MH-B 上产的那一版」。尺子只有一把——``assert_chain_visible``，血缘端点与
回退端点用的同一个。放宽的是归属，**不是**可见性：看不见的链仍然拿同一个
``output_ref_unresolvable``。
```

`ChatOutputRef` 加 `issue_key: Optional[str] = None`；`_verified` 删掉 `row_issue`
那一段（不再比 issue_id），改为从链里 `newest_with_a_run(chain)` 取 `issue_key`
填进去；`_stamped` 与 `citations_for_transcript` 按 `title` 同样的「为空则省略键」
口径多输出 `issue_key`。`resolve_output_refs` 签名追加 `auth`，装载链那一段改为：

```python
    for ref_kind, ref_id, _version in coords:
        key = (ref_kind, ref_id)
        if key in chains:
            continue
        try:
            chains[key] = list(await assert_chain_visible(ref_kind, ref_id, auth))
        except HTTPException:
            # 404（看不见 / 没登记）与「链上没有这一版」是同一个回答：分出一个
            # 「存在但你不能引」的 code 等于确认那个对象存在。
            raise OutputRefRefused(
                UNRESOLVABLE, f"{ref_kind}/{ref_id} cannot be cited here") from None
```

`issue_messages_router.py` 调用点（680-695）传 `auth=auth`。既有
`test_output_ref_resolver.py` 里「别的 issue 的产出被拒」那条旧用例按新契约改写成
「链不可见时被拒」，同一 Step 改掉并在 PR 描述里点名。

- [ ] **Step 8: 实现血缘 `cited_count` / `cited_in`**

`schemas/outputs.py` 新增：

```python
class CitedIn(BaseModel):
    """一次引用发生在哪里。``issue_id`` / ``issue_key`` 为空 = 那件议题调用方
    看不见（或那条引用不在任何议题上）——计数仍然算它。"""

    issue_id: Optional[str] = None
    issue_key: Optional[str] = None
    message_id: str
    user_id: str
    at: datetime
```

`OutputVersion` 追加：

```python
    #: 这一版被引用过几次，**全量**。cited_in 只列调用方看得见的那几条，所以
    #: 「被引 3 次、你能看 1 条」是允许且正确的答案（spec §2.2）。
    cited_count: int = 0
    cited_in: List[CitedIn] = []
```

`lineage_view._VERSION_KEYS` **不动**——这两个不是行上的列，由端点合成。
`outputs_router.get_output_lineage` 在 `versions = redact_foreign_issue_links(...)` 之后：

```python
    # 整条链一次 GROUP BY（每版一次查询会让二十版的链发二十次往返）。
    counts = await get_output_citations_repository().counts_for_chain(kind, str(ref_id))
    for item in versions:
        n = counts.get(int(item["version"]), 0)
        item["cited_count"] = n
        item["cited_in"] = await _cited_in(kind, ref_id, int(item["version"]), auth) if n else []
```

`_cited_in` 是本文件的模块级 helper：`list_for_ref` → 同一个 `visible_issue_ids`
批量裁剪 → `issue_repository.map_identifiers` 补 `issue_key`。

- [ ] **Step 9: 跑，确认绿**

```bash
cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-output-citations/backend && \
  uv run pytest tests/services/ai/chat/ tests/services/deliverables/ \
    tests/api/test_outputs_router.py tests/repositories/test_output_citations_repository.py -q && \
  uv run ruff check . && uv run black --check . && uv run isort --check-only .
```
预期：全绿。

- [ ] **Step 10: 突变验证**

① 删掉 `_rows` 的 `if key in seen: continue`，确认
`test_the_same_version_cited_twice_in_one_message_is_one_row` 转红；恢复。
② 把 `resolve_output_refs` 的 `except HTTPException` 分支改成 `pass`（吞掉），确认
`test_an_invisible_chain_is_the_same_refusal_as_a_missing_version` 转红；恢复。
③ 去掉 `_cited_in` 的 `visible_issue_ids` 裁剪，确认
`test_a_version_reports_its_citation_count_in_full` 转红（会列出两条）；恢复并重跑全绿。

- [ ] **Step 11: 提交与 PR**

```bash
W=/Volumes/program/project-code/repos/nous-app/.worktrees/3c-output-citations
git -C "$W" add -A && \
git -C "$W" commit -m "feat(outputs): output_citations 反查镜像、血缘 cited_count/cited_in、引用归属放宽为链可见（harness 三期 3c Task 16）" && \
git -C "$W" push -u origin feat/3c-output-citations && \
gh pr create --repo iocrazy/nous-app --base master --head feat/3c-output-citations \
  --title "feat(outputs): 引用反查镜像 + 归属放宽为链可见（3c Task 16）" \
  --body "3c §2.2 / §2.4。『这一版被谁引用过』此前无法回答——引用只在 messages.body 的 jsonb 里，那列零索引。对照组是画布侧的 canvas_asset_refs：反查靠物化镜像表。归属从『属于本议题』放宽为『链对调用方可见』，尺子抽成 deliverables/visibility.py::assert_chain_visible，血缘 / 回退 / 引用三个消费方共用。既有一条『别的 issue 的产出被拒』用例按新契约改写。"
```

---

### Task 17: 前端——⌘K 面、@ 页签跨议题、引用卡与血缘的被引显示

**Files:**
- Create `frontend/services/searchService.ts`、`frontend/stores/commandPaletteStore.ts`、`frontend/components/search/CommandPalette.tsx`
- Modify `frontend/components/TopBar.tsx`（放大镜占位 593-600）、`frontend/App.tsx`（挂 `<CommandPalette />`）
- Modify `frontend/components/chat/useMentionOutputsTab.ts`（模块 docstring 第 2 条、options、fetch effect、`rows`）
- Modify `frontend/components/chat/outputMentionRows.ts`、`OutputMentionList.tsx`（行渲染 152-178）
- Modify `frontend/components/agentActivity/TrajectoryRenderer/nodes/builtins.tsx`（`OutputCitations` 370-399）与 `foldEvents.ts`（citations 条目带 `issueKey`）
- Modify `frontend/components/Todolist/OutputDiffDialog.tsx`（版本 chip 436-458）、`frontend/services/outputsService.ts`（`OutputVersion` 38-62）
- Modify `frontend/public/locales/en.json` 与 `zh.json`
- Create `frontend/services/searchService.test.ts`、`frontend/components/chat/mentionOutputsCrossIssue.test.ts`、`frontend/components/search/CommandPalette.test.tsx`

**Interfaces:**
- Produces `search(params: SearchParams) -> Promise<SearchResponse>`、`SearchError`
- Produces `useCommandPalette()`：`{ open: boolean; setOpen: (v: boolean) => void }`
- Produces `searchHitsToMentionRows(hits: SearchHit[]) -> OutputMentionRow[]`、`CommandPalette`
- Consumes Task 14 的 `GET /api/v1/search`、Task 16 的 `cited_count` 与附件 `issue_key`

- [ ] **Step 1: 建 worktree（Task 14 与 16 合并后）**

```bash
git -C /Volumes/program/project-code/repos/nous-app worktree add -b feat/3c-search-ui \
  /Volumes/program/project-code/repos/nous-app/.worktrees/3c-search-ui origin/master
```

- [ ] **Step 2: 写 service 与跨议题行的失败测试**

```ts
// frontend/services/searchService.test.ts
/**
 * `/api/v1/search` 的 client。fixture 用**真实 wire 形状**：命中的 `id` /
 * `issue_id` 是 string（后端刻意 stringify 的 Snowflake），`version` 是 number；
 * 拒绝体是生产的 ErrorResponse 外壳，类型化码在 `details.code` 不在 `detail`。
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';

import { search } from './searchService';

const fetchMock = vi.fn();
vi.stubGlobal('fetch', fetchMock);
vi.mock('./parserService', () => ({ getAuthHeaders: async () => ({}) }));

const WIRE = {
  groups: {
    issues: [{ kind: 'issue', id: '96', title: 'MH-96 · Alpha rain on glass',
               snippet: null, deep_link: '/team/7/todolist/MH-96',
               issue_key: 'MH-96', issue_id: '96',
               meta: { status: 'in_progress', assignee_name: 'Alice' } }],
    runs: [], outputs: [],
  },
  totals: { issues: 1, runs: 0, outputs: 0 }, took_ms: 12,
};

beforeEach(() => fetchMock.mockReset());

describe('searchService', () => {
  it('sends q, kinds and the scope as query params, keeping ids strings', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => WIRE });
    const res = await search({ q: 'rain', kinds: ['output'], projectId: '3' });
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain('q=rain');
    expect(url).toContain('kinds=output');
    expect(url).toContain('project_id=3');
    expect(typeof res.groups.issues[0].id).toBe('string');
    expect(res.totals.issues).toBe(1);
  });

  it('reads a typed refusal from details.code', async () => {
    fetchMock.mockResolvedValue({
      ok: false, status: 400, statusText: 'Bad Request',
      json: async () => ({ success: false, error: 'a search needs at least 2 characters',
        code: 'http_400', request_id: 'r-1',
        details: { code: 'query_too_short', message: 'a search needs at least 2 characters' } }),
    });
    await expect(search({ q: 'r' })).rejects.toMatchObject({ code: 'query_too_short' });
  });
});
```

```ts
// frontend/components/chat/mentionOutputsCrossIssue.test.ts
/**
 * @ 页签 Outputs 跨议题（3c §2.4）。契约从「读一次 + 内存过滤」翻成 assets
 * 页签那一侧：空查询 = 本议题产出，有查询 = 一次 `/search`。
 */
import { describe, expect, it } from 'vitest';

import { searchHitsToMentionRows } from './outputMentionRows';

// `id` 是后端的不透明身份键 `kind:ref_id:version`（契约补充），不是登记行 id。
// 行的坐标从 `meta` 来——切 id 等于把后端的拼法复制到前端。
const hit = (meta: Record<string, unknown>) => ({
  kind: 'output' as const, id: 'script_shot:9:4', title: 'S3 · Shot 1 · MS',
  snippet: null, deep_link: '/team/7/todolist/MH-98', issue_key: 'MH-98',
  issue_id: '98', meta,
});

describe('跨议题命中 → mention 行', () => {
  it('carries the issue key so the row can say where it came from', () => {
    const rows = searchHitsToMentionRows([
      hit({ kind: 'script_shot', ref_id: '9', version: 4 })]);
    expect(rows[0]).toMatchObject({
      ref_kind: 'script_shot', ref_id: '9', version: 4, issue_key: 'MH-98' });
    // 检索给的是一版，不是一条链——标成「最新」等于对一个我们没读过链的对象
    // 下结论。
    expect(rows[0].latest).toBe(false);
    expect(rows[0].startsOlderGroup).toBe(false);
  });

  it('drops a hit whose meta lacks the three coordinates', () => {
    // 引用是三坐标的。缺一个就拼不出 chip，塞进去只会在发帖时 400。
    expect(searchHitsToMentionRows([hit({ kind: 'script_shot', ref_id: '9' })])).toEqual([]);
  });
});
```

- [ ] **Step 3: 跑，确认红**

```bash
cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-search-ui/frontend && \
  npx vitest run services/searchService.test.ts components/chat/mentionOutputsCrossIssue.test.ts
```
预期：两个文件都 `Failed to resolve import`（`./searchService` 不存在、
`searchHitsToMentionRows` 未导出）。

- [ ] **Step 4: 写 `searchService.ts` 与 `searchHitsToMentionRows`**

`searchService.ts` 照 `outputsService.ts:151-195` 的三件套写：`SearchError`（带
`code` / `status` / `details`）、`reject(res)` 用 `decodeErrorEnvelope` 读
`details.code`、`get<T>(path)` 用 `getApiUrl()` + `getAuthHeaders()`。
`SearchHit` / `SearchGroups` / `SearchTotals` / `SearchResponse` 逐字段镜像
`backend/app/schemas/search.py`，**所有 id 是 string**。导出：

```ts
export interface SearchParams {
  q: string;
  kinds?: Array<'issue' | 'run' | 'output'>;
  teamId?: string | number;
  projectId?: string | number;
  issueId?: string | number;
  limitPerGroup?: number;
}

export async function search(params: SearchParams): Promise<SearchResponse>;
```

`outputMentionRows.ts`：`OutputMentionRow` 加 `issue_key: string | null`
（`toMentionRows` 里补 `null`——本议题的行不需要 chip），并追加：

```ts
/** 一条检索命中 → 一行可引用的版本（3c §2.4）。与 `toMentionRows` 的区别：
 *  检索给的是**一版**，不是一条链。所以 `latest` 恒 false——标成「最新」等于
 *  对一个我们没读过链的对象下结论。 */
export function searchHitsToMentionRows(hits: SearchHit[]): OutputMentionRow[] {
  const out: OutputMentionRow[] = [];
  for (const h of hits) {
    const kind = h.meta?.kind, refId = h.meta?.ref_id, version = h.meta?.version;
    if (typeof kind !== 'string' || typeof refId !== 'string' || typeof version !== 'number') {
      // 引用是三坐标的。缺一个就拼不出 chip，塞进去只会在发帖时 400。
      continue;
    }
    // key 由**这三个坐标**拼，不读 `h.id`。两者今天字面相同（后端的身份键是
    // 同一个拼法），但这一行的键要和 `toMentionRows` 产的行可比——那一条路
    // 没有 `h.id` 可读，两种拼法会让同一版在两条路上得到两个 key。
    out.push({ key: `${kind}:${refId}:${version}`, ref_kind: kind, ref_id: refId,
               version, title: h.title || null, issue_key: h.issue_key ?? null,
               latest: false, startsOlderGroup: false });
  }
  return out;
}
```

```bash
cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-search-ui/frontend && \
  npx vitest run services/searchService.test.ts components/chat/mentionOutputsCrossIssue.test.ts
```
预期：4 passed。

- [ ] **Step 5: 写 ⌘K 面的失败测试**

```tsx
// frontend/components/search/CommandPalette.test.tsx
/**
 * ⌘K 统一面（3c §2.5 / spec §6 稿一）。四条：⌘K 打开；输入防抖 200 ms 后一次
 * 请求；↑↓ 在**组间连续**移动（三组是一个列表，不是三个各自循环的列表——否则
 * Enter 会打开你没看着的那行）；空态 / 无命中 / 失败三种文案互不相同。
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import { CommandPalette } from './CommandPalette';

const searchMock = vi.fn();
vi.mock('../../services/searchService', () => ({
  search: (...a: unknown[]) => searchMock(...a),
  SearchError: class extends Error { code = 'x'; },
}));

const HIT = (kind: string, id: string, title: string) => ({
  kind, id, title, snippet: null, deep_link: '/team/7/todolist/MH-96',
  issue_key: 'MH-96', issue_id: '96', meta: {},
});
const RESULT = {
  groups: { issues: [HIT('issue', '96', 'MH-96 · Alpha rain on glass')],
            runs: [HIT('run', '913', 'MH-96 · Alpha')],
            // 产出命中的 id 是 `kind:ref_id:version`（契约补充），面板只拿它
            // 当 React key。
            outputs: [HIT('output', 'script_shot:9:4', 'S3 · Shot 1 · MS')] },
  totals: { issues: 1, runs: 1, outputs: 1 }, took_ms: 9,
};

beforeEach(() => { vi.useFakeTimers(); searchMock.mockReset(); });
afterEach(() => vi.useRealTimers());

const mount = () => render(<MemoryRouter><CommandPalette /></MemoryRouter>);
const open = () => act(() => { fireEvent.keyDown(window, { key: 'k', metaKey: true }); });
const type = (v: string) =>
  fireEvent.change(screen.getByTestId('command-palette-input'), { target: { value: v } });

describe('CommandPalette', () => {
  it('opens on Cmd+K and closes on Escape', () => {
    mount();
    expect(screen.queryByTestId('command-palette')).toBeNull();
    open();
    expect(screen.getByTestId('command-palette')).toBeTruthy();
    act(() => { fireEvent.keyDown(window, { key: 'Escape' }); });
    expect(screen.queryByTestId('command-palette')).toBeNull();
  });

  it('debounces to one request 200 ms after the last keystroke', () => {
    searchMock.mockResolvedValue(RESULT);
    mount(); open(); type('ra'); type('rain');
    act(() => { vi.advanceTimersByTime(199); });
    expect(searchMock).not.toHaveBeenCalled();
    act(() => { vi.advanceTimersByTime(1); });
    expect(searchMock).toHaveBeenCalledTimes(1);
  });

  it('moves the highlight across group boundaries', async () => {
    searchMock.mockResolvedValue(RESULT);
    mount(); open(); type('rain');
    await act(async () => { vi.advanceTimersByTime(200); });
    const rows = () => screen.getAllByTestId('command-palette-row');
    expect(rows()[0].getAttribute('data-active')).toBe('true');
    act(() => { fireEvent.keyDown(window, { key: 'ArrowDown' }); });
    // 第二行属于**下一组**——三组是一个列表。
    expect(rows()[1].getAttribute('data-active')).toBe('true');
    expect(rows()[1].getAttribute('data-kind')).toBe('run');
  });

  it('tells an empty box, an empty result and a failure apart', async () => {
    mount(); open();
    expect(screen.getByTestId('command-palette-hint')).toBeTruthy();

    searchMock.mockResolvedValue({ groups: { issues: [], runs: [], outputs: [] },
                                   totals: { issues: 0, runs: 0, outputs: 0 }, took_ms: 3 });
    type('zzz');
    await act(async () => { vi.advanceTimersByTime(200); });
    expect(screen.getByTestId('command-palette-empty').textContent).toContain('zzz');

    searchMock.mockRejectedValue(Object.assign(new Error('nope'), { code: 'query_too_short' }));
    type('qq');
    await act(async () => { vi.advanceTimersByTime(200); });
    // 「搜不到」与「搜不成」是读者会采取不同行动的两个答案。
    expect(screen.getByTestId('command-palette-error').textContent).toContain('query_too_short');
  });
});
```

- [ ] **Step 6: 跑，确认红**

```bash
cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-search-ui/frontend && \
  npx vitest run components/search/CommandPalette.test.tsx
```
预期：`Failed to resolve import "./CommandPalette"`（4 个用例一个都没跑）。

- [ ] **Step 7: 写 store 与 `CommandPalette`，接顶栏**

`stores/commandPaletteStore.ts` 照 `settingsStore.ts` 的 zustand 写法，**不**加
`persist`——一个搜索面板的开合不该跨会话留存：

```ts
export const useCommandPalette = create<{ open: boolean; setOpen: (o: boolean) => void }>(
  (set) => ({ open: false, setOpen: (open) => set({ open }) }));
```

`components/search/CommandPalette.tsx` 要点：

- 全局 keydown 守卫**照抄** `IssueListView.tsx:512-518` 那段的形状（`defaultPrevented` /
  `isComposing` / 输入上下文），但**不排除** `metaKey`——⌘K 本身就是组合键，只排
  `altKey` 与 `shiftKey`。
- 输入防抖 200 ms（spec §2.5），一次 `search({ q, limitPerGroup: 10 })`，带 `live` 守卫防竞态。
- 三组按 issues → runs → outputs 平铺成**一个** `flat` 数组，`activeIndex` 在它上面
  移动并 wrap；每组前画组头。行带 `data-testid="command-palette-row"`、`data-kind`、`data-active`。
- Enter：`navigate(hit.deep_link)` 后关闭；`deep_link` 为空串的行渲染成灰行、Enter 跳过
  （无议题的个人 run 没有可跳转的页面）。
- 三种状态各有 testid：`command-palette-hint`（没输入）、`command-palette-empty`
  （搜了没命中，文案含查询词）、`command-palette-error`（失败，文案含 `details.code`，
  走 `errorEnvelope.ts` 的解码结果）。底部提示条 `↑↓ navigate · ↵ open · esc close`。

`TopBar.tsx`（593-600）把 `onClick={() => { /* Cmd+K search — Phase 2+ */ }}` 换成
`onClick={() => setPaletteOpen(true)}`（`const setPaletteOpen = useCommandPalette((s) => s.setOpen);`）。
`App.tsx` 在既有全局弹层旁挂 `<CommandPalette />`（未开时返回 null）。
i18n：`en.json` 新增 `search.paletteHint` / `paletteEmpty` / `paletteError` /
`groupIssues` / `groupRuns` / `groupOutputs` / `footerHint`，`zh.json` 同键中文值。
`topbar.search` 已是 `"Search (⌘K)"`，不动。

- [ ] **Step 8: 接 @ 页签跨议题与两处被引显示**

`useMentionOutputsTab.ts`：options 加 `projectId?: string | number | null`；fetch
effect 拆两条路——`query.trim()` 为空走既有 `listIssueOutputs`（一次读 + 内存过滤），
非空且有 `projectId` 走 `search({ q, kinds: ['output'], projectId, limitPerGroup: 12 })`，
每次击键重查并带 `live` 守卫；无 project 的议题退回本议题。`rows` 走检索时用
`searchHitsToMentionRows(hits)`，否则 `toMentionRows(objects, query)`。模块 docstring
第 2 条改写为：

```
 *  2. **两套契约，按有没有查询切换。** 空查询仍是「读一次本议题的产出 + 内存
 *     过滤」——那是几行数据，往返买不到什么。有查询时范围扩到整个项目（3c
 *     §2.4），行数不再有界，于是翻成 assets 页签那一侧：每次击键重查。无
 *     project 的议题退回本议题——一个范围为空的检索是一次范围为全部的检索。
```

`OutputMentionList.tsx` 行渲染（167-175 的 kindWord span 之后）：

```tsx
                    {row.issue_key && (
                      <span data-testid="output-picker-issue"
                            className="ml-1.5 rounded border border-ink-700 px-1 text-ink-400">
                        {row.issue_key}
                      </span>
                    )}
```

`builtins.tsx::OutputCitations` chip 内追加 `{c.issueKey && <span
data-testid="output-citation-issue" className="ml-1 text-ink-500">{c.issueKey}</span>}`；
`foldEvents.ts` 的 citations 条目从 `user` 事件 payload 读 `issue_key` 填 `issueKey`
（Task 16 的 `citations_for_transcript` 已把它放进那份投影）。

`OutputDiffDialog.tsx` 版本 chip（`{t('outputs.version', …)}` 之后）：

```tsx
                {v.cited_count > 0 && (
                  <span data-testid="output-version-cited" className="ml-1 text-ink-500">
                    {t('outputs.citedTimes', 'Cited ×{{n}}', { n: v.cited_count })}
                  </span>
                )}
```

`outputsService.ts` 的 `OutputVersion` 追加 `cited_count: number` 与
`cited_in: Array<{ issue_id: string | null; issue_key: string | null; message_id: string;
user_id: string; at: string }>`，注释写明「`cited_count` 是全量，`cited_in` 只列你
看得见的那几条」。

- [ ] **Step 9: 跑，确认绿**

```bash
cd /Volumes/program/project-code/repos/nous-app/.worktrees/3c-search-ui/frontend && \
  npx vitest run services/searchService.test.ts components/search/ components/chat/ \
    components/agentActivity/ components/Todolist/OutputDiffDialog && \
  npm run typecheck
```
预期：新 8 passed，既有 chat / agentActivity / Todolist 套件全绿，typecheck 零错。

- [ ] **Step 10: 突变验证**

① `CommandPalette` 的 `flat` 改成三组各自独立的 index，确认
`moves the highlight across group boundaries` 转红；恢复。
② 防抖 `200` 改成 `0`，确认 `debounces to one request…` 转红；恢复。
③ 删掉 `searchHitsToMentionRows` 的三坐标守卫，确认
`drops a hit whose meta lacks the three coordinates` 转红；恢复。
④ `searchService` 的 `reject` 改成只读 `detail`（不读 `details.code`），确认
`reads a typed refusal from details.code` 转红——这正是 CLAUDE.md 2026-09-09 记的
那类缺陷；恢复并重跑全绿。

- [ ] **Step 11: 提交与 PR**

```bash
W=/Volumes/program/project-code/repos/nous-app/.worktrees/3c-search-ui
git -C "$W" add -A && \
git -C "$W" commit -m "feat(search): ⌘K 统一检索面、@ 页签 Outputs 跨议题、引用卡来源议题 chip 与血缘 Cited ×n（harness 三期 3c Task 17）" && \
git -C "$W" push -u origin feat/3c-search-ui && \
gh pr create --repo iocrazy/nous-app --base master --head feat/3c-search-ui \
  --title "feat(search): ⌘K 面 + @ 页签跨议题 + 被引显示（3c Task 17）" \
  --body "3c §2.4 / §2.5，spec §6 稿一稿四。顶栏放大镜从 Phase 2+ 占位变成真入口。@ 页签按有没有查询切两套契约：空查询读一次本议题（几行数据），有查询走 /search 跨项目（行数无界，翻成 assets 页签那一侧的每击键重查）。所有 fixture 用真实 wire 形状，拒绝从 details.code 读。"
```

---

### Task 18: Part C 的合并顺序与部署验证

**Files:** 无代码改动——四个 PR 落地后的验证动作，结论写进 Part D 的完成账。

**Interfaces:** Consumes Task 13–17 的全部产物；Consumes Part A Task 1 的 mig 472（**必须先合并并跑完**）。

- [ ] **Step 1: 确认迁移先行**

```bash
gh run list --repo iocrazy/nous-app --workflow run-migration.yml --limit 3
ssh ubuntu "docker exec nous-db psql -U postgres -p 55434 -d postgres -c \
  \"SELECT to_regclass('public.search_docs'), to_regclass('public.output_citations')\""
```
预期：两列都非 NULL。任一为 NULL 就**停在这里**——缺表时检索是 500，而
`search_docs` 的写方是 best-effort，只会静默刷 ERROR。

- [ ] **Step 2: 按依赖顺序合并**

| 顺序 | Task | 依赖 | 并行 |
|---|---|---|---|
| 1 | 13 投影四个写方 | mig 472 第 1 段 | — |
| 2 | 14 `/search`、16 引用镜像 | 14 依赖 13 的 repository；16 依赖 mig 472 第 2 段 | 互不相交，**并行** |
| 3 | 15 `/issues?q=`、17 前端 | 15 依赖 14 的 `list_for_user(q=)`；17 依赖 14 与 16 的 wire | 互不相交，**并行** |

每个 PR 合并前 `git -C <worktree> fetch origin && git -C <worktree> rebase origin/master`
（分支寿命 ≤3 天；落后 ≥30 commits 会被 `pr-behind-check.yml` 硬拒）。

- [ ] **Step 3: 后端链部署后，先确认模块真的装进容器**

```bash
ssh ubuntu "docker exec nous-backend /app/.venv/bin/python -c \
  'import app.services.search.service as s; print(sorted(s.ALL_SEARCH_KINDS))'"
ssh ubuntu "docker exec nous-backend /app/.venv/bin/python -c \
  'from app.repositories.output_citations_repository import get_output_citations_repository as g; print(g())'"
```
预期：`['issue', 'output', 'run']` 与一个 repository 实例。

这一步不能用 `/api/v1/readyz` 替代：那个探针不 import 本期任何新模块，一个 import
期就炸的模块会让 readyz 照样绿而每次搜索 500（CLAUDE.md 验收纪律同族——探针要探
客户端真实走的那条路径）。

- [ ] **Step 4: 真栈 curl，owner 与别团队各一次**

```bash
OWNER=<owner 的 JWT>; OTHER=<别团队成员的 JWT>
curl -sS -H "Authorization: Bearer $OWNER" \
  'https://cn.nous.ink/api/v1/search?q=rain&kinds=issue,run,output' \
  | jq '{totals, n: (.groups | map_values(length))}'
curl -sS -o /tmp/other.json -w '%{http_code}\n' -H "Authorization: Bearer $OTHER" \
  'https://cn.nous.ink/api/v1/search?q=rain&kinds=issue,run,output'
jq '.groups | map_values(length)' /tmp/other.json
```
预期：owner 三组有命中；别团队 **HTTP 200** 且三组长度全 0（spec §9 验收 ④ 原文：
`curl 断言 groups 全空，不是 404`）。拿到 404 就是把「不许你看」和「不存在」混成了
一个答案，属于本期缺陷。

- [ ] **Step 5: 走完 spec §9 里属于 Part C 的五条**

| # | 动作 | 通过判据 |
|---|---|---|
| ① | ⌘K 搜一个只在第 201+ 条议题标题里出现的词 | Issues 组命中（此前的内存搜索必然搜不到） |
| ② | 搜一个只在分镜正文、不在标题里的词 | Outputs 组命中，行上有 `v n` 与来源议题 |
| ③ | 搜一个只在 agent 结论摘要里的词 | Runs 组命中 |
| ④ | 别团队成员搜同一词 | 三组零命中（Step 4 已 curl 断言，UI 再看一次） |
| ⑤ | 在 MH-A 的回复框 @ 引用 MH-B（同项目）的产出 | 发帖成功；引用卡带 `MH-B` chip；`output_citations` 多一行；MH-B 那版血缘 `cited_in` 含 MH-A |

⑤ 的库侧对账：

```bash
ssh ubuntu "docker exec nous-db psql -U postgres -p 55434 -d postgres -c \
  'SELECT kind, ref_id, version, issue_id, message_id FROM public.output_citations \
   ORDER BY created_at DESC LIMIT 3'"
```

- [ ] **Step 6: 前端链验收**

```bash
cd /Volumes/program/project-code/repos/nous-app/frontend && npm run e2e:prod
```
预期：`walkthrough.spec.ts` 全绿。⌘K 面是新挂的全局组件，挂错位置会让整页白屏而
单测照样通过——这条走查是它唯一的真栈证明（CLAUDE.md「前端上线验收 = version.json
SHA + 真栈走查，二者缺一不可」）。

- [ ] **Step 7: 记票（写进 Part D 的完成账）**

1. **`search_docs` 没有 `step` / `turn` 列**，检索命中的深链只到议题页；spec §2.3 的
   `?step=` 与 `?output=kind/ref_id` 两个锚点缺输入。加列 + 让议题页消费 `?output=`
   打开产出弹层，是一张独立的票。
2. **存量产出正文未回填**（spec §2.1 明写只回填标题）：mig 472 之前登记的产出按内容
   搜不到。回填要跑账本重放，量大。
3. **`totals.runs` / `totals.outputs` 是这一页的条数，不是服务端总数**（投影表上没有
   便宜的 count）。UI 只对议题组显示「N of M」。
4. **`task_manager_router` 的 `_sanitize_search` 仍漏 `%` 与 `_`**（侦察 A4）：那条路
   没用 `escape_like`，用户搜一个 `%` 就是一次全表 ILIKE。同根但不在范围内。
5. **`agent_runs.input_summary` 三个写方语义不同**（侦察 A2）：会话链写的是合成串
   `conversation=… summoner=…` 而非用户文本，所以无议题 run 的标题回退在那条链上
   可读性差。统一写方是一张独立的票。
6. **路由注册点、`record_output_citations` 的 `session=None`、`identifier_and_title`**
   三处与契约的偏离（本文件顶部），拼装时四位作者对齐一次。
7. **`run_deliverables.id` 不在投影里**（契约补充把产出的 `entity_id` 定为
   `kind:ref_id:version`）。今天没有消费方需要它——命中带三个坐标，引用与深链都够
   ——但「从一条检索命中直接拿登记行」要多一次按坐标的查询。真需要时加一列
   `deliverable_id`，不要改 `entity_id` 的拼法（那会让 mig 472 的回填行全部对不上）。

---

## Part C 小结（给拼装者）

- **13** 建投影（types + repository + projection + 四个写方接线）→ **14** 建读面
  （`/search` 三组 + `list_for_user(q=)`）→ **15** Issues 页搜索改服务端 → **16** 引用
  镜像 + 「链可见」+ 血缘 `cited_in` → **17** 前端三处 → **18** 合并顺序与真栈验收。
- 合并序：13 → 14/16 并行 → 15/17 并行。
- Part C 只消费 mig 472 的第 1、2 段；第 3、4 段属于 Part A / B，本段不读。

---

# Part D — 回合可读性 + 真栈验收 + 完成账（Task 19–23）

> 拼进 `docs/superpowers/plans/2026-09-15-harness-p4-phase3c-search-efficiency.md` 的第四段。
> 契约 `.superpowers/sdd/2026-09-15-harness-p4-phase3c/plan-contract.md` §3 / §4；
> spec `docs/superpowers/specs/2026-09-15-harness-p4-phase3c-search-efficiency-design.md` §4 / §6 稿二 / §9 / §10。
>
> **三条路径只有两处 diff。** spec §4.1 的第三条「`StreamingNotSupported` 缓冲回退」在
> `agent_runner.py:521-540`（`stream_method is None`）已经**委托 `run_turn`**，继承第一处 diff、本身
> 一行不改；`:756-766` 那个 `except StreamingNotSupported`（adapter 有 `stream` 但流里抛）`return`
> 前不跑工具，没有叙述可写。第三条路径的保证 100% 落在测试上——`test_partial_narration.py` 必须有
> 「adapter 无 `stream` 属性」的用例，照 `test_turn_end_reasons.py::test_stream_turn_buffered_fallback_carries_the_hook_stop_reason`
> 的 `_NoStreamAdapter` 写法（CLAUDE.md「生产的唯一路径」血泪）。
>
> 下面所有代码片段都压掉了空行（篇幅）；照抄后 `black` / 项目 lint 会把空行补回原样。

---

### Task 19: 后端——带工具调用的步也写 `assistant{partial:true}`（spec §4.1 前半）

**Files:**
- Modify: `backend/app/services/ai/runner/agent_runner.py`
  - `_stream_turn_inner` per-iteration 缓冲声明（:690-693）
  - `_stream_turn_inner` delta 转发处（:712-718）
  - `_stream_turn_inner` tool_calls 分支入口（:783-789，`if not tool_calls_to_run: … return` 与 `# Append assistant tool-use message` 之间）
  - `_run_turn_inner` tool_calls 分支入口（:1924-1926，`assistant_msg_index = len(messages) - 1` 与 `# Resolve each tool call` 之间）
- Test: `backend/tests/runner/test_partial_narration.py`（新建）

**Interfaces:**
- Consumes：`app.services.ai.runner.events.emit`（模块内别名 `emit_event`，:38 已 import），签名
  `async emit(recorder, event_type: str, payload: dict[str, Any], *, turn: int | None = None, step: int | None = None) -> bool`；
  `strip_reasoning`（同模块已 import，:1893 在用）。
- Produces：transcript 事件 `assistant`，payload `{"content": str, "partial": True, "step": int}`，
  坐标列 `turn=1, step=iteration`（与 `_step_started` / `_step_ended` 同口径）。**契约**：`partial` 键只在
  带工具调用的步出现；最终回答那条 `assistant` 不带 `partial`（Task 20 的折叠器按这个键分流）。

- [ ] **Step 1: 三条路径的失败测试（红）**

```python
# backend/tests/runner/test_partial_narration.py
"""spec 3c §4.1：带工具调用的那一步，模型先说的话也要进 transcript（在这之前
``assistant`` 只在「这一步没有工具调用」时写，:1893）。第三条路径（adapter 无
``stream`` → 委托 run_turn）是生产上 chunk_callback 回合的唯一路径，缺它就会「前两条
全绿而生产一条没写出来」（2026-09-08 stop_reason 事故同形）。"""
import pytest
from app.services.ai.adapters.base import StreamChunk
pytestmark = pytest.mark.unit
class _Rec:
    """`test_turn_end_reasons.py:96-127` 的 `_Rec` 照抄，只改两处：`record_event` 多记
    自增 seq 与 turn/step 关键字；多一个 `of()`。其余方法原样。"""
    async def record_event(self, event_type, payload, *, turn=None, step=None):
        self._seq += 1
        self.events.append((self._seq, event_type, {**payload, "_turn": turn, "_step": step}))
    def of(self, event_type):
        return [(seq, p) for seq, t, p in self.events if t == event_type]
# `_composed()` 照抄 test_turn_end_reasons.py:141-156（逐字）。`_runner(adapter)` 照抄
# :159-172，去掉 `step_hooks=StepHookChain([_StopHook(...)])`——这里要模型真的被调到。
NARRATION = "Here is where things stand: 6403 unique frames.\n\nNow I will list them."
_TOOL_CALLS = [{"id": "call_1", "type": "function",
                "function": {"name": "Skill", "arguments": '{"skill":"script-outline"}'}}]
def _resp(content, tool_calls=None, finish="stop"):
    """真实 wire 形状：provider 一次返回 content + tool_calls（OpenAI / Anthropic 都这么发）。"""
    msg = {"role": "assistant", "content": content}
    if tool_calls: msg["tool_calls"] = tool_calls
    return {"choices": [{"message": msg, "finish_reason": finish}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 4}}
def _narrations(rec):
    return [(seq, p) for seq, p in rec.of("assistant") if p.get("partial") is True]
async def _drain(adapter, rec):
    """走 stream_turn（两个流式用例共用）。"""
    async for _ch in _runner(adapter).stream_turn(
        _composed(), [{"role": "user", "content": "go"}], recorder=rec, auto_recorder=False):
        pass
async def _buffered(adapter, rec):
    """走 run_turn（两个非流式用例共用）。"""
    await _runner(adapter).run_turn(_composed(), [{"role": "user", "content": "go"}], recorder=rec)
async def test_run_turn_writes_the_narration_before_the_tool_call():
    from unittest.mock import AsyncMock
    adapter = AsyncMock()
    adapter.call = AsyncMock(side_effect=[_resp(NARRATION, _TOOL_CALLS, "tool_calls"), _resp("Done.")])
    rec = _Rec()
    await _buffered(adapter, rec)
    rows = _narrations(rec)
    assert len(rows) == 1, rec.events
    seq, payload = rows[0]
    assert payload["content"] == NARRATION
    assert payload["step"] == 1 and payload["_step"] == 1 and payload["_turn"] == 1
    assert seq < rec.of("tool_call")[0][0], rec.events   # 顺序就是语义：叙述在动作之前
    # 最终回答那条不带 partial——折叠器靠这个键分流。
    finals = [p for _s, p in rec.of("assistant") if "partial" not in p]
    assert len(finals) == 1 and finals[0]["content"] == "Done."
async def test_run_turn_writes_nothing_when_the_step_had_no_text():
    """Claude 在纯工具轮把 content 发成 null；空事件在时间线上是个不说话的气泡。"""
    from unittest.mock import AsyncMock
    adapter = AsyncMock()
    adapter.call = AsyncMock(side_effect=[_resp(None, _TOOL_CALLS, "tool_calls"), _resp("Done.")])
    rec = _Rec()
    await _buffered(adapter, rec)
    assert _narrations(rec) == []
class _StreamAdapter:
    def __init__(self): self.rounds = 0
    async def call(self, *a, **k): raise AssertionError("must not fall back to call()")
    async def stream(self, composed, messages, **kw):
        self.rounds += 1
        if self.rounds == 1:
            yield StreamChunk(delta_text="Here is where things stand: 6403 unique frames.")
            yield StreamChunk(delta_text="\n\nNow I will list them.")
            yield StreamChunk(tool_call_delta={"tool_calls": [{"index": 0, **_TOOL_CALLS[0]}]})
            yield StreamChunk(finish_reason="tool_calls", usage={"prompt_tokens": 10, "completion_tokens": 4})
        else:
            yield StreamChunk(delta_text="Done.")
            yield StreamChunk(finish_reason="stop", usage={"prompt_tokens": 12, "completion_tokens": 2})
class _NoStreamAdapter:
    """没有 ``stream`` 属性——生产上 chunk_callback 回合走的就是它，stream_turn 委托 run_turn。"""
    def __init__(self): self._resps = [_resp(NARRATION, _TOOL_CALLS, "tool_calls"), _resp("Done.")]
    async def call(self, *a, **k): return self._resps.pop(0)
async def test_stream_turn_writes_the_narration_accumulated_from_deltas():
    rec = _Rec()
    await _drain(_StreamAdapter(), rec)
    rows = _narrations(rec)
    assert len(rows) == 1, rec.events
    assert rows[0][1]["content"] == NARRATION        # 两段 delta 拼回一整段
    assert rows[0][1]["step"] == 1 and rows[0][1]["_step"] == 1 and rows[0][0] < rec.of("tool_call")[0][0]
async def test_buffered_fallback_carries_the_narration_through_run_turn():
    rec = _Rec()
    await _drain(_NoStreamAdapter(), rec)
    rows = _narrations(rec)
    assert len(rows) == 1 and rows[0][1]["content"] == NARRATION, rec.events
    assert rows[0][0] < rec.of("tool_call")[0][0], rec.events
```

```bash
cd <worktree>/backend && uv run pytest tests/runner/test_partial_narration.py -q
# 红 3（rows == [] / rows[0] IndexError）；test_run_turn_writes_nothing… 先天绿。
```

- [ ] **Step 2: 非流式路径落叙述（绿其一）**

`_run_turn_inner`，在 :1924-1926 之后插入：

```python
            messages.append(msg)
            assistant_msg_index = len(messages) - 1
            # 3c §4.1：这一步模型先说的话。在这之前 ``assistant`` 只在「这一步没有
            # 工具调用」时写（:1893），所以「先交代现状、再动手」的文本从没进过
            # transcript。``partial`` 把它与最终回答分开，折叠器按这个键分流。
            narration = strip_reasoning(msg.get("content") or "")
            if narration:
                await emit_event(recorder, "assistant",
                                 {"content": narration, "partial": True, "step": iteration},
                                 turn=1, step=iteration)
```

```bash
cd <worktree>/backend && uv run pytest tests/runner/test_partial_narration.py -q   # 3 绿 1 红（真流式）
```

- [ ] **Step 3: 真流式路径累计 delta 并落叙述（全绿）**

① 本步缓冲声明（:690-693）加累加器：

```python
            tool_call_buf: dict[int, dict] = {}
            # 3c §4.1：本步已发出的正文（过滤后，与用户逐字相同）。只在这一步真调了
            # 工具时用得上——没调工具的那一步，文本调用方自己已收全。
            step_text: list[str] = []
            final_finish: Optional[str] = None
```

② delta 转发处（:712-713）在 yield 之前累加：

```python
                    emit_text = reason_filter.feed(chunk.delta_text)
                    if emit_text:
                        step_text.append(emit_text)
                    if emit_text or chunk.tool_call_delta:
```

③ tool_calls 分支入口（:783-789，`return` 之后、`# Append assistant tool-use message` 之前）：

```python
            # 3c §4.1：与非流式同一条事件。累的是**过滤后**的文本，所以 transcript 里
            # 读到的与气泡里读到的逐字相同（reason_filter 吃掉的 <think> 不该上时间线）。
            narration = "".join(step_text).strip()
            if narration:
                await emit_event(recorder, "assistant",
                                 {"content": narration, "partial": True, "step": iteration},
                                 turn=1, step=iteration)
```

⚠️ `step_text` 的声明留在 per-iteration 块里（每轮重置）。提到循环外会让第二步的叙述带上第一步的文本。

```bash
cd <worktree>/backend && uv run pytest tests/runner/test_partial_narration.py tests/runner/test_turn_end_reasons.py -q   # 全绿
cd <worktree>/backend && uv run ruff check . && uv run black --check . && uv run isort --check-only .
```

- [ ] **Step 4: 突变记录**（每条改完跑一遍看红，再改回看绿）
  - ③ 的 `narration = "".join(step_text).strip()` → `narration = ""`：只有真流式用例红（证明流式那处 diff 是它唯一来源）。
  - Step 2 的 `turn=1, step=iteration` 两个关键字删掉：两个 `payload["_step"] == 1` 断言红（坐标列真的被写）。
  - `_NoStreamAdapter` 加一个抛 `StreamingNotSupported` 的 `stream` 方法：缓冲回退用例红（证明它走的是 `stream_method is None` 那条生产路径，不是 `except StreamingNotSupported` 那条不跑工具的分支）。

- [ ] **Step 5: commit + PR**

```bash
git -C /Volumes/program/project-code/repos/nous-app worktree add -b feat/3c-t19-partial-narration .worktrees/3c-t19 origin/master
git -C /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t19 add backend/app/services/ai/runner/agent_runner.py backend/tests/runner/test_partial_narration.py
git -C /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t19 commit -m "feat(runner): 带工具调用的步也写 assistant{partial} —— 先说的话不再丢在 transcript 之外"
git -C /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t19 push -u origin feat/3c-t19-partial-narration
gh pr create --base master --title "feat(runner): 带工具调用的步也写 assistant{partial}" --body "spec 3c §4.1。两处 diff（run_turn / stream_turn），缓冲回退继承 run_turn；测试含「adapter 无 stream」用例钉住生产路径。"
```

---

### Task 20: 前端——叙述节点、动作动词、运行中状态行（spec §4.1 后半 / §6 稿二）

**Files:**
- Modify: `frontend/components/agentActivity/TrajectoryRenderer/foldEvents.ts`（`TrajectoryNode` 联合 :202-210；`assistant` case :539-549；`tool_call` case 的 `detail` :479-486）
- Modify: `frontend/components/agentActivity/TrajectoryRenderer/index.ts`（:3 类型导出）
- Modify: `frontend/components/agentActivity/TrajectoryRenderer/TrajectoryRenderer.tsx`（:12 `import './nodes/builtins';` 之下）
- Modify: `frontend/components/agentActivity/TrajectoryRenderer/nodes/builtins.tsx`（:29 加 import；`lineLabel` 的 `case 'tool'` :78-79）
- Create: `frontend/components/agentActivity/TrajectoryRenderer/nodes/NarrationNode.tsx`
- Create: `frontend/components/agentActivity/TrajectoryRenderer/nodes/actionVerbs.ts`
- Create: `frontend/components/agentActivity/RunStatusLine.tsx`
- Modify: `frontend/components/AIChatPanel.tsx`（:47 之后加 import；气泡 map 结束 :1414 之后挂状态行）
- Modify: `frontend/public/locales/en.json` / `zh.json`（`trajectory` 块 :5743-5766）
- Test: `foldEvents.test.ts`（追加 describe）、`nodes/actionVerbs.test.ts`、`nodes/narrationNode.test.tsx`、`RunStatusLine.test.tsx`（后三个新建）

**Interfaces:**
- Consumes（Task 19）：`assistant` 事件 payload `{content, partial: true, step}`，行上带 `turn`/`step` 坐标列。
- Consumes（既有）：`foldEvents(events, {isRunning}) -> TrajectoryNode[]`；`liveStep(nodes) -> StepNode | null`；
  `useRunToolActivity(runId, isRunning) -> {events, denials, activities, nodes, loaded}`（**只读 `nodes`**——
  该 hook 头注释禁止面板消费 `activities`）；`MarkdownBody`（`components/AILibrary/MarkdownBody.tsx`，props `{source: string; className?: string}`）；
  `useElapsedSeconds(active: boolean) -> number`（`frontend/hooks/useElapsedSeconds.ts`）。
- Produces：
  - `NarrationNode = { kind: 'narration'; key: string; text: string; step: number | null; at: string | null }`，进 `TrajectoryNode` 联合并从 `index.ts` 导出类型
  - `actionLabel(tool: string, args: unknown, ok: boolean): string`
  - `RunStatusLineProps = { current: { step: number; tool: string | null } | null; elapsedMs: number }`

- [ ] **Step 1: 折叠器的失败测试（红）**

```ts
// 追加进 frontend/components/agentActivity/TrajectoryRenderer/foldEvents.test.ts（`ev()` 工厂在 :6-10）
describe('foldEvents — 阶段性叙述（3c §4.1）', () => {
  it('partial 的 assistant 成为独立正文节点，排在它所属 step 节点之前', () => {
    seq = 0;
    const nodes = foldEvents([
      ev('user', { content: 'go' }, { turn: 1 }),
      ev('step_start', { turn: 1, step: 1, model: 'm' }, { turn: 1, step: 1 }),
      ev('assistant', { content: 'Here is where things stand.', partial: true, step: 1 }, { turn: 1, step: 1 }),
      ev('tool_call', { tool: 'ListShots', args: { scene_id: 7 }, iteration: 1, result: { ok: true } }, { turn: 1, step: 1 }),
      ev('step_end', { turn: 1, step: 1, duration_ms: 800 }, { turn: 1, step: 1 }),
      ev('assistant', { content: 'All done.' }, { turn: 1, step: 1 }),
    ], { isRunning: false });
    // 叙述在动作之前，与模型输出顺序一致。
    expect(nodes.map((n) => n.kind)).toEqual(['user', 'narration', 'step']);
    const narration = nodes[1];
    if (narration.kind !== 'narration') throw new Error();
    expect(narration.text).toBe('Here is where things stand.');
    expect(narration.step).toBe(1);
    expect(narration.key).toBe('narration:3');
    // 最终回答（无 partial）仍是 step 里的一行 output——现状不变。
    const step = nodes[2];
    if (step.kind !== 'step') throw new Error();
    expect(step.lines.filter((l) => l.type === 'output')).toHaveLength(1);
    expect(step.summary.outputs).toBe(1);
  });
  it('同一步两段叙述各成一个节点，都排在 step 之前', () => {
    seq = 0;
    expect(foldEvents([
      ev('step_start', { turn: 1, step: 1 }, { turn: 1, step: 1 }),
      ev('assistant', { content: 'First.', partial: true, step: 1 }, { turn: 1, step: 1 }),
      ev('assistant', { content: 'Second.', partial: true, step: 1 }, { turn: 1, step: 1 }),
      ev('tool_call', { tool: 'Skill', args: {}, iteration: 1, result: { ok: true } }, { turn: 1, step: 1 }),
    ], { isRunning: false }).map((n) => n.kind)).toEqual(['narration', 'narration', 'step']);
  });
  it('空正文的 partial 事件不画节点', () => {
    seq = 0;
    expect(foldEvents([ev('assistant', { content: '   ', partial: true, step: 1 })], { isRunning: false })
      .filter((n) => n.kind === 'narration')).toHaveLength(0);
  });
  it('tool 行把 args 带进 detail —— 动作动词要拿它拼对象', () => {
    seq = 0;
    const step = foldEvents([ev('tool_call', { tool: 'UpdateShot', args: { shot_id: '42' }, iteration: 1, result: { ok: true } })], { isRunning: false })[0];
    if (step.kind !== 'step') throw new Error();
    expect(step.lines[0].detail?.args).toEqual({ shot_id: '42' });
  });
});
```

```bash
cd frontend && npx vitest run components/agentActivity/TrajectoryRenderer/foldEvents.test.ts
# 红 4：前三个拿到 ['user','step'] / ['step'] 等（partial 被当成 output 行折进 step），第四个 detail.args undefined
```

- [ ] **Step 2: 折叠器实现（绿）**

① 类型（`ErrorNode` 之后、`TrajectoryNode` 联合之前）：

```ts
/** 一段阶段性叙述（3c §4.1）：模型在这一步动手**之前**说的话。不是 step 里的一行，
 *  而是 step 之间的一段正文。最终回答（无 `partial`）不走这里，仍折进它的 step。 */
export interface NarrationNode {
  kind: 'narration';
  key: string;
  text: string;
  /** 它属于哪一步；坐标缺席的老行是 null。 */
  step: number | null;
  at: string | null;
}
```

联合（:202-210）在 `| UserNode` 之后加 `| NarrationNode`。

② `assistant` case（:539-549）改成分流：

```ts
      case 'assistant': {
        // 3c §4.1：带 partial 的是阶段性叙述——独立正文节点，推在 `ensureStep`
        // **之前**，所以它排在它所属 step 节点前面（叙述先于动作）。
        if (p.partial === true) {
          const text = str(p.content);
          if (text) {
            nodes.push({ kind: 'narration', key: `narration:${ev.seq}`, text,
                         step: num(ev.step) ?? num(p.step), at: ev.created_at ?? null });
          }
          break;
        }
        const node = ensureStep(ev, null);
        const content = str(p.content) ?? '';
        node.summary.outputs += 1;
        upsertLine(node, `output:${ev.seq}`, () => ({
          type: 'output', label: content, ok: true, durationMs: null, detail: { chars: content.length },
        }));
        break;
      }
```

⚠️ `break` 必须在 `if` 里就返回。落到下面会既画节点又开 step，一段叙述就把还没开始的 step 提前开出来。

③ `tool_call` 的 `detail`（:479-486）加一项 `args: obj(p.args),`（紧跟 `tool,`）。
`obj()` 会把真 wire 上被 `_truncate_payload` 字符串化的嵌套字典解回来。

④ `index.ts` :3 的类型导出加 `NarrationNode`。

```bash
cd frontend && npx vitest run components/agentActivity/TrajectoryRenderer/foldEvents.test.ts   # 绿
```

- [ ] **Step 3: 动作动词表（红）**

```ts
// frontend/components/agentActivity/TrajectoryRenderer/nodes/actionVerbs.test.ts
import { describe, expect, it } from 'vitest';
import { actionLabel } from './actionVerbs';
describe('actionLabel', () => {
  it('把已知工具译成动词 + 对象', () => {
    expect(actionLabel('ReadScene', { scene_id: 7 }, true)).toBe('Read scene 7');
    expect(actionLabel('ListShots', { scene_id: 7 }, true)).toBe('Listed shots');
    expect(actionLabel('UpdateShot', { shot_id: '42' }, true)).toBe('Edited shot 42');
    expect(actionLabel('GenerateShotImage', { shot_id: 42 }, true)).toBe('Generated image 42');
    expect(actionLabel('RunCommand', { command: 'ls -la' }, true)).toBe('Ran ls -la');
  });
  it('失败的动作在末尾说清它失败了——只说做了什么会读成成功', () => {
    expect(actionLabel('GenerateShotImage', { shot_id: 42 }, false)).toBe('Generated image 42 failed');
    expect(actionLabel('ListShots', {}, false)).toBe('Listed shots failed');
  });
  it('没映射的工具原样用工具名 + 主参数，不猜动词', () => {
    expect(actionLabel('Skill', { skill: 'script-outline' }, true)).toBe('Skill script-outline');
    expect(actionLabel('Delegate', { description: 'research the venue' }, true)).toBe('Delegate research the venue');
    expect(actionLabel('Mystery', {}, true)).toBe('Mystery');
  });
  it('args 不是对象、或主参数缺席时只留动词', () => {
    expect(actionLabel('UpdateShot', null, true)).toBe('Edited shot');
    expect(actionLabel('UpdateShot', 'not-an-object', true)).toBe('Edited shot');
    expect(actionLabel('ReadScene', { scene_id: null }, true)).toBe('Read scene');
  });
  it('长参数截断', () => { expect(actionLabel('RunCommand', { command: 'x'.repeat(80) }, true)).toBe(`Ran ${'x'.repeat(40)}…`); });
});
```

```bash
cd frontend && npx vitest run components/agentActivity/TrajectoryRenderer/nodes/actionVerbs.test.ts
# 红：Failed to resolve import './actionVerbs'
```

- [ ] **Step 4: 动作动词表实现（绿）**

```ts
// frontend/components/agentActivity/TrajectoryRenderer/nodes/actionVerbs.ts
/**
 * 一行动作该读成「做了什么」，不是「调了哪个函数」（3c §4.1）。原先只写工具名，一次
 * 回合读成 `ListShots · UpdateShot`——对着作品的人看不懂。未映射的工具**不猜动词**：
 * 原样用工具名 + 主参数，猜错的动词比没有动词更坏。
 */
const MAX_OBJ = 40;
/** 工具 → [动词, 主参数键（按序取第一个有值的；空数组 = 这个动作没有对象）]。 */
const VERBS: Record<string, [string, string[]]> = {
  ReadScene: ['Read scene', ['scene_id']],
  ListShots: ['Listed shots', []],
  UpdateShot: ['Edited shot', ['shot_id']],
  CreateShot: ['Added shot', ['shot_id']],
  GenerateShotImage: ['Generated image', ['shot_id']],
  GenerateImage: ['Generated image', ['shot_id', 'prompt']],
  RunCommand: ['Ran', ['command']],
};
/** 未映射工具的主参数候选，按优先级；都没有就只留工具名。 */
const FALLBACK_KEYS = ['skill', 'description', 'name', 'command', 'prompt', 'id'];
const trim = (s: string): string => (s.length > MAX_OBJ ? `${s.slice(0, MAX_OBJ)}…` : s);
function pick(args: unknown, keys: string[]): string | null {
  if (!args || typeof args !== 'object' || Array.isArray(args)) return null;
  const bag = args as Record<string, unknown>;
  for (const k of keys) {
    const v = bag[k];
    if (typeof v === 'string' && v.trim()) return trim(v.trim());
    if (typeof v === 'number' && Number.isFinite(v)) return String(v);
  }
  return null;
}
export function actionLabel(tool: string, args: unknown, ok: boolean): string {
  const [verb, keys] = VERBS[tool] ?? [tool, FALLBACK_KEYS];
  const head = [verb, pick(args, keys)].filter(Boolean).join(' ');
  return ok ? head : `${head} failed`;
}
```

```bash
cd frontend && npx vitest run components/agentActivity/TrajectoryRenderer/nodes/actionVerbs.test.ts   # 绿
```

- [ ] **Step 5: 叙述节点 + 动作行接线的失败测试（红）**

```tsx
// frontend/components/agentActivity/TrajectoryRenderer/nodes/narrationNode.test.tsx
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { NarrationNode, StepNode } from '../foldEvents';
import { NarrationNodeView } from './NarrationNode';
import { StepNodeView } from './builtins';
// i18n mock：与 nodes/outputCards.test.tsx:18-26 逐字同一段（模板 + {{var}} 插值）。
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: unknown, opts?: unknown) => {
      const template = typeof fallback === 'string' ? fallback : key;
      const vars = (typeof fallback === 'object' ? fallback : opts) as Record<string, unknown> | undefined;
      return vars ? template.replace(/\{\{(\w+)\}\}/g, (_m, k: string) => String(vars[k] ?? `{{${k}}}`)) : template;
    },
  }),
}));
afterEach(cleanup);
const narration: NarrationNode = {
  kind: 'narration', key: 'narration:3', text: 'Here is **where** things stand.',
  step: 1, at: '2026-09-15T00:00:00Z',
};
const step = (tool: string, args: unknown, ok: boolean): StepNode => ({
  kind: 'step', key: 'step:1:1', turn: 1, step: 1, live: false, model: 'm', startedAt: null,
  lines: [{ key: 'tool:4', type: 'tool', label: tool, ok, count: 1, durationMs: 1200,
            detail: { tool, args, iteration: 1, timedOut: false, timeoutS: null, elapsedS: null } }],
  summary: { tools: 1, retries: 0, compactions: 0, outputs: 0, todo: null, durationMs: 1200, costCents: null, finishReason: null },
  children: [], outputs: [],
});
describe('NarrationNode', () => {
  it('渲成正文，markdown 生效（与最终回答同一个渲染器）', () => {
    render(<NarrationNodeView node={narration} expanded={false} />);
    const body = screen.getByTestId('traj-narration');
    expect(body.textContent).toContain('Here is where things stand.');
    expect(body.querySelector('strong')?.textContent).toBe('where');
  });
});
describe('StepNodeView 的工具行读动作动词', () => {
  it('成功的一行说做了什么，而不是调了谁', () => {
    render(<StepNodeView node={step('UpdateShot', { shot_id: '42' }, true)} expanded />);
    expect(document.body.textContent).toContain('Edited shot 42');
    expect(document.body.textContent).not.toContain('UpdateShot');
  });
  it('失败的一行把 failed 写进标签', () => {
    render(<StepNodeView node={step('GenerateShotImage', { shot_id: 42 }, false)} expanded />);
    expect(document.body.textContent).toContain('Generated image 42 failed');
  });
});
// NarrationNodeView 的 `expanded` / `onToggle` 由 NodeProps 要求，此处传死值即可。
```

```bash
cd frontend && npx vitest run components/agentActivity/TrajectoryRenderer/nodes/narrationNode.test.tsx
# 红 3：Failed to resolve import './NarrationNode'；两个 step 用例拿到 'UpdateShot' / 'GenerateShotImage'
```

- [ ] **Step 6: 组件实现 + `lineLabel` 换动词（绿）**

```tsx
// frontend/components/agentActivity/TrajectoryRenderer/nodes/NarrationNode.tsx
/** 一段阶段性叙述（3c §4.1）。与最终回答共用 `MarkdownBody`——同一个说话人在同一个
 *  回合里说的话，用两套排版会读成两个不同的东西。 */
import React from 'react';
import { MarkdownBody } from '../../../AILibrary/MarkdownBody';
import type { NarrationNode } from '../foldEvents';
import { registerTrajectoryNode, type NodeProps } from './registry';
export const NarrationNodeView: React.FC<NodeProps<NarrationNode>> = ({ node }) => (
  <div className="px-2.5 py-1" data-testid="traj-narration" data-step={node.step ?? undefined}>
    <MarkdownBody source={node.text} className="text-[13px]" />
  </div>
);
registerTrajectoryNode('narration', NarrationNodeView);
export default NarrationNodeView;
```

`builtins.tsx`：:29 之后加 `import { actionLabel } from './actionVerbs';`；`lineLabel` 的
`case 'tool'`（:78-79）改成

```ts
    case 'tool':
      // 3c §4.1：工具名 → 动作动词 + 对象。`detail.args` 由 foldEvents 带上来。
      return actionLabel(line.label, line.detail?.args, line.ok);
```

`TrajectoryRenderer.tsx` :12 的 `import './nodes/builtins';` 下面加 `import './nodes/NarrationNode';`
（注册靠 import 副作用，与 builtins 同一机制）。

```bash
cd frontend && npx vitest run components/agentActivity/TrajectoryRenderer/ && npm run typecheck   # 全绿
```

- [ ] **Step 7: 运行中状态行（红 → 绿）**

```tsx
// frontend/components/agentActivity/RunStatusLine.test.tsx
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { RunStatusLine } from './RunStatusLine';
// i18n mock：与 nodes/outputCards.test.tsx:18-26 逐字同一段（模板 + {{var}} 插值）。
vi.mock('react-i18next', () => ({ /* 同上，逐字 */ }));
afterEach(cleanup);
const line = () => screen.getByTestId('run-status-line').textContent;
describe('RunStatusLine', () => {
  it('说清在第几步、在跑什么、跑了多久', () => {
    render(<RunStatusLine current={{ step: 3, tool: 'GenerateImage' }} elapsedMs={4200} />);
    expect(line()).toBe('Step 3 · Running GenerateImage… · 4.2s');
  });
  it('还没进工具时只说步号', () => {
    render(<RunStatusLine current={{ step: 1, tool: null }} elapsedMs={800} />);
    expect(line()).toBe('Step 1 · 0.8s');
  });
  it('回合结束（current 为 null）什么都不画——留在屏幕上的状态行会读成还在跑', () => {
    render(<RunStatusLine current={null} elapsedMs={9999} />);
    expect(screen.queryByTestId('run-status-line')).toBeNull();
  });
});
```

```bash
cd frontend && npx vitest run components/agentActivity/RunStatusLine.test.tsx   # 红：无此模块
```

```tsx
// frontend/components/agentActivity/RunStatusLine.tsx
/**
 * 「现在在干什么」的一行（3c §4.1）。驾驶舱早有 "Now:"，聊天面板一直没有——正在跑的
 * 回合只有一个转圈。纯展示：`current` 由宿主算出，`null` = 回合结束，此时不画。
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2 } from 'lucide-react';
export interface RunStatusLineProps {
  current: { step: number; tool: string | null } | null;
  elapsedMs: number;
}
export const RunStatusLine: React.FC<RunStatusLineProps> = ({ current, elapsedMs }) => {
  const { t } = useTranslation();
  if (!current) return null;
  const parts = [t('trajectory.step', 'Step {{n}}', { n: current.step })];
  if (current.tool) parts.push(t('trajectory.running', 'Running {{tool}}…', { tool: current.tool }));
  parts.push(`${(elapsedMs / 1000).toFixed(1)}s`);
  return (
    <div className="ml-1 flex items-center gap-1.5 px-3 py-1 text-[11px] text-ink-500" data-testid="run-status-line">
      <Loader2 size={10} className="animate-spin shrink-0" />
      {parts.join(' · ')}
    </div>
  );
};
export default RunStatusLine;
```

i18n：`en.json` / `zh.json` 的 `trajectory` 块（:5743-5766）各加一行
`"running": "Running {{tool}}…",`（UI 一律英文，两份值相同）。

```bash
cd frontend && npx vitest run components/agentActivity/RunStatusLine.test.tsx   # 绿
```

- [ ] **Step 8: 挂进 `AIChatPanel`**

:47 之后加：

```ts
import { RunStatusLine } from './agentActivity/RunStatusLine';
import { useRunToolActivity } from './agentActivity/useRunToolActivity';
import { liveStep } from './agentActivity/TrajectoryRenderer/foldEvents';
import { useElapsedSeconds } from '../hooks/useElapsedSeconds';
```

在 `AIChatPanel` 组件之前定义：

```tsx
/**
 * 面板最后一条气泡下的运行中状态行（3c §4.1）。只读 `useRunToolActivity` 的 `nodes`——
 * 该 hook 的头注释禁止面板消费 `activities`（会把每次调用画两遍）；`nodes` 是折叠结果，
 * 不进气泡。回合结束时传 `runId=null`：这一行本来就不画，拉一次只是白费请求。
 */
const ChatRunStatus: React.FC<{ runId: string | null; isRunning: boolean }> = ({ runId, isRunning }) => {
  const { nodes } = useRunToolActivity(isRunning ? runId : null, isRunning);
  const elapsed = useElapsedSeconds(isRunning);
  const live = isRunning ? liveStep(nodes) : null;
  if (!live) return null;
  const openTool = [...live.lines].reverse().find((l) => l.type === 'tool' && l.durationMs === null);
  return <RunStatusLine current={{ step: live.step, tool: (openTool?.detail?.tool as string) ?? null }} elapsedMs={elapsed * 1000} />;
};
```

气泡 `map` 结束（:1414 的 `))}`）与 `<AttachmentFailureBanner` 之间插入：

```tsx
            <ChatRunStatus runId={messages.length ? chatRunId(messages[messages.length - 1]) : null} isRunning={sending} />
```

```bash
cd frontend && npx vitest run components/agentActivity components/chat && npm run typecheck
```

- [ ] **Step 9: 突变记录**
  - `foldEvents.ts` 的 `if (p.partial === true)` → `if (false)`：三个 fold 用例红。
  - 把 `nodes.push({kind:'narration'…})` 挪到 `ensureStep(ev, null)` **之后**：第一个用例的 `kinds` 变 `['user','step','narration']`，红（证明「叙述先于 step」靠的是推节点的位置）。
  - `actionVerbs.ts` 的 `ok ? head : \`${head} failed\`` → 恒返回 `head`：两个失败用例红。
  - `RunStatusLine.tsx` 删掉 `if (!current) return null`：第三个用例红。

- [ ] **Step 10: commit + PR**

```bash
git -C /Volumes/program/project-code/repos/nous-app worktree add -b feat/3c-t20-narration-ui .worktrees/3c-t20 origin/master
git -C /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t20 add frontend/
git -C /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t20 commit -m "feat(trajectory): 叙述与动作交错 —— narration 节点、动作动词表、聊天面板运行中状态行"
git -C /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t20 push -u origin feat/3c-t20-narration-ui
gh pr create --base master --title "feat(trajectory): 叙述与动作交错 + 动作动词 + 运行中状态行" --body "spec 3c §4.1 后半 / §6 稿二。消费 Task 19 的 assistant{partial}。"
```

---

### Task 21: 前端 + done 帧——每条回复尾部的消耗行（spec §4.2 / §6 稿二）

**Files:**
- Create: `frontend/components/agentActivity/RunCostTail.tsx` + `RunCostTail.test.tsx`
- Create: `frontend/components/Todolist/runCostContext.ts`
- Modify: `frontend/components/Todolist/IssueChatThread.tsx`（`RunTrajectory` 体 :285；chips 条件 :288、内容 :313-321）
- Modify: `frontend/components/Todolist/IssueChatThread.runHeaderChips.test.tsx`
- Modify: `frontend/components/Todolist/IssueDetailView.tsx`（`progress` :137；WS `status/done` :403-416；`IssueChatThread` 挂点 :772）
- Modify: `frontend/components/chat/AIChatBubble.tsx`（props :59-61、解构 :333-343、尾栏 :446-450）+ `AIChatBubble.test.tsx`
- Modify: `frontend/components/AIChatPanel.tsx`（气泡 map :1407；SSE `done` :945-954）
- Modify: `frontend/services/issuesService.ts`（`IssueProgressRun` :384-394）
- Modify: `backend/app/services/issues/issue_chat_stream.py`（`_run_output_keys` 之后；`publish_status` :73-91）
- Modify: `backend/app/services/ai/chat/ai_library_chat_service.py`（`done` 帧 :355-367）
- Test: `backend/tests/services/issues/test_done_frame_cost.py`（新建）
- Modify: `frontend/public/locales/en.json` / `zh.json`

**Interfaces:**
- Consumes（契约 §2，Part B Task 10/11）：
  - `GET /api/v1/ai-library/runs/costs?ids=<csv ≤50>` → `{"items": {"<run_id>": RunCostItem}}`
  - `aiLibraryService.getRunCosts(ids: string[]): Promise<Record<string, RunCost>>`，
    `RunCost = { cost_cents: number | null; charged_points: number | null; model: string | null; status: string; prompt_tokens: number; completion_tokens: number }`
  - `AgentRunsRepository.cost_rows_for_ids(ids: list[int]) -> list[dict]`（列 `id, user_id, issue_id, cost_cents, model, status, prompt_tokens, completion_tokens`）与 `PointsRepository.charged_points_for_references(*, reference_type: str, reference_ids: list[str]) -> dict[str, float]`（没扣过的 id 不出现）——`/runs/costs` 就是这两个方法的组合（Part B Task 10），done 帧同样组合它们，**不另开一条读路径**
  - `issue.rollup` 的 `runs[i]` 多出 `charged_points: float | None`
- Produces：
  - `RunCostTailProps = { costCents: number | null; chargedPoints: number | null; model: string | null; status: string; promptTokens?: number; completionTokens?: number; live?: boolean }`
  - `runCostContext.ts`：`RunCostContext: React.Context<Record<string, RunCost>>`、`useRunCost(runId: string | null): RunCost | null`
  - WS `status` 帧与 SSE `done` 帧各多两个**恒定存在**的键：`cost_cents: float | null`、`charged_points: float | null`

- [ ] **Step 1: 组件的失败测试（红）**

```tsx
// frontend/components/agentActivity/RunCostTail.test.tsx
/** 3c §4.2：尾部「共消耗」一行。积分是**真的扣了的那个数**，¢ 只是没扣成时的兜底；
 *  两个都没有读作 `—`，编出来的 0 会把「没算出价」说成「免费」（同 `fmtChildCents`）。 */
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { RunCostTail } from './RunCostTail';
// i18n mock：与 nodes/outputCards.test.tsx:18-26 逐字同一段。
vi.mock('react-i18next', () => ({ /* 同上，逐字 */ }));
afterEach(cleanup);
const base = { costCents: 0.82, chargedPoints: null, model: 'doubao-seed-2-0-lite', status: 'completed' };
const text = () => screen.getByTestId('run-cost-tail').textContent;
const title = () => screen.getByTestId('run-cost-tail').getAttribute('title');
describe('RunCostTail', () => {
  it('扣过积分就显示积分', () => { render(<RunCostTail {...base} chargedPoints={0.82} />); expect(text()).toBe('◇ 0.82 · doubao-seed-2-0-lite'); });
  it('没扣成（BYOK / 急停 / 零花费）退回 ¢', () => { render(<RunCostTail {...base} />); expect(text()).toBe('¢0.82 · doubao-seed-2-0-lite'); });
  it('两个都没有读作 —，不是 ¢0.00', () => { render(<RunCostTail {...base} costCents={null} />); expect(text()).toBe('— · doubao-seed-2-0-lite'); });
  it('模型未知时只留数字，不拼一个空的 ·', () => { render(<RunCostTail {...base} model={null} />); expect(text()).toBe('¢0.82'); });
  it('运行中的值加 … ——读者要知道它还会变', () => { render(<RunCostTail {...base} chargedPoints={0.4} live />); expect(text()).toBe('◇ 0.40… · doubao-seed-2-0-lite'); });
  it('hover 浮层三行：tokens · ¢ · 扣没扣', () => {
    render(<RunCostTail {...base} chargedPoints={0.82} promptTokens={1200} completionTokens={340} />);
    expect(title()).toBe('1200 prompt · 340 completion tokens\n¢0.82\nCharged ◇ 0.82');
  });
  it('没扣的浮层说清为什么——一句 not charged 不说原因等于没说', () => {
    render(<RunCostTail {...base} promptTokens={10} completionTokens={2} status="failed" />);
    expect(title()).toBe('10 prompt · 2 completion tokens\n¢0.82\nNot charged (failed)');
  });
});
```

```bash
cd frontend && npx vitest run components/agentActivity/RunCostTail.test.tsx   # 红：无此模块
```

- [ ] **Step 2: 组件实现（绿）**

```tsx
// frontend/components/agentActivity/RunCostTail.tsx
/**
 * 一次回合「共消耗」多少（3c §4.2）。`chargedPoints` 是积分账里**真的扣掉的那一行**
 * （`point_transactions` 的 consume 行），`costCents` 是效率账算出来的花费。有前者就显示前者——
 * 读者问的是「我被扣了多少」。BYOK / 急停 / 零花费没有积分行，才退回 ¢。
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
export interface RunCostTailProps {
  costCents: number | null;
  chargedPoints: number | null;
  model: string | null;
  status: string;
  promptTokens?: number;
  completionTokens?: number;
  live?: boolean;   // 回合还没结束——值会变，加 `…` 说明它不是终值
}
export const RunCostTail: React.FC<RunCostTailProps> = ({
  costCents, chargedPoints, model, status, promptTokens, completionTokens, live = false,
}) => {
  const { t } = useTranslation();
  const amount = chargedPoints !== null ? `◇ ${chargedPoints.toFixed(2)}`
    : costCents !== null ? `¢${costCents.toFixed(2)}` : '—';
  const head = live && amount !== '—' ? `${amount}…` : amount;
  const title = [
    promptTokens !== undefined || completionTokens !== undefined
      ? t('cost.tailTokens', '{{p}} prompt · {{c}} completion tokens', { p: promptTokens ?? 0, c: completionTokens ?? 0 })
      : null,
    costCents !== null ? `¢${costCents.toFixed(2)}` : null,
    chargedPoints !== null
      ? t('cost.charged', 'Charged ◇ {{n}}', { n: chargedPoints.toFixed(2) })
      : t('cost.notCharged', 'Not charged ({{why}})', { why: status }),
  ].filter(Boolean).join('\n');
  return (
    <span className="text-[11px] text-ink-500 tabular-nums" data-testid="run-cost-tail" title={title}>
      {[head, model].filter(Boolean).join(' · ')}
    </span>
  );
};
export default RunCostTail;
```

i18n：`en.json` / `zh.json` 顶层在 `"trajectory"` 块之后新建

```json
  "cost": {
    "tailTokens": "{{p}} prompt · {{c}} completion tokens",
    "charged": "Charged ◇ {{n}}",
    "notCharged": "Not charged ({{why}})"
  },
```

```bash
cd frontend && npx vitest run components/agentActivity/RunCostTail.test.tsx   # 绿
```

- [ ] **Step 3: 议题线程宿主（红）**

先建 context（测试要 import 它的 Provider）：

```ts
// frontend/components/Todolist/runCostContext.ts
/**
 * 一个议题里每个 run 的消耗，来自 rollup 的 `runs[]`（3c §4.2）。走 context 而不是
 * props：`RunTrajectory` 隔着 `IssueChatThread` → `AgentRunRow` 两层，而这两层与花费
 * 无关——让它们透传一个自己不用的对象，下一个改签名的人就会漏掉。同 `trajectoryRunContext`。
 */
import { createContext, useContext } from 'react';
import type { RunCost } from '../../services/aiLibraryService';
export const RunCostContext = createContext<Record<string, RunCost>>({});
export function useRunCost(runId: string | null): RunCost | null {
  const map = useContext(RunCostContext);
  return runId ? map[runId] ?? null : null;
}
```

追加进 `IssueChatThread.runHeaderChips.test.tsx`（顶部补 `import { RunCostContext } from './runCostContext';`）：

```tsx
describe('RunTrajectory 的消耗行', () => {
  const costs = { r2: { cost_cents: 0.82, charged_points: 0.82, model: 'doubao-seed-2-0-lite',
                        status: 'completed', prompt_tokens: 1200, completion_tokens: 340 } };
  const mountWithCost = (isRunning = false) => render(
    <MemoryRouter>
      <RunCostContext.Provider value={costs}>
        <ReplayContext.Provider value={replayOf()}>
          <RunTrajectory runId="r2" isRunning={isRunning} />
        </ReplayContext.Provider>
      </RunCostContext.Provider>
    </MemoryRouter>,
  );
  const tail = () => screen.getByTestId('run-cost-tail').textContent;
  it('run 头部带上「共消耗」', () => { EVENTS = [USER]; mountWithCost(); expect(tail()).toBe('◇ 0.82 · doubao-seed-2-0-lite'); });
  it('运行中的 run 加 …', () => { EVENTS = [USER]; mountWithCost(true); expect(tail()).toBe('◇ 0.82… · doubao-seed-2-0-lite'); });
  it('rollup 还没带这个 run 的账时不画消耗行——空位好过一个编出来的 0', () => {
    EVENTS = [USER];
    render(<MemoryRouter><ReplayContext.Provider value={replayOf()}>
      <RunTrajectory runId="r9" isRunning={false} /></ReplayContext.Provider></MemoryRouter>);
    expect(screen.queryByTestId('run-cost-tail')).toBeNull();
  });
});
```

```bash
cd frontend && npx vitest run components/Todolist/IssueChatThread.runHeaderChips.test.tsx   # 红 2（第三个先天绿）
```

- [ ] **Step 4: 议题线程实现（绿）**

`IssueChatThread.tsx`：顶部 import `RunCostTail` 与 `useRunCost`；`RunTrajectory` 体内
`const timedOut = …`（:285）之后加 `const cost = useRunCost(runId);`；chips 的条件（:288）
改成 `(origin || timedOut.count > 0 || fromWakeup || cost)`；在 `timedOut` 那个 `<span>` 之后、
该 `</div>` 之前插入：

```tsx
          {cost && (
            <span className="ml-auto">
              <RunCostTail costCents={cost.cost_cents} chargedPoints={cost.charged_points}
                model={cost.model} status={cost.status} promptTokens={cost.prompt_tokens}
                completionTokens={cost.completion_tokens} live={isRunning} />
            </span>
          )}
```

`issuesService.ts` 的 `IssueProgressRun`（:384-394）加
`/** A3 之后真扣掉的积分；没扣（BYOK / 急停 / 零花费）为 null。 */ charged_points: number | null;`

`IssueDetailView.tsx`：加
`const [liveRunCost, setLiveRunCost] = useState<{ runId: string; cost_cents: number | null; charged_points: number | null } | null>(null);`，
在 WS `phase === 'done'` 分支（:403-416）的 `notifyTurn` 之前加

```tsx
          if (event.run_id) {
            setLiveRunCost({ runId: String(event.run_id),
              cost_cents: event.cost_cents ?? null, charged_points: event.charged_points ?? null });
          }
```

并在 :772 的 `<IssueChatThread` 外包 Provider，值来自 rollup（done 帧覆盖同键，下一次轮询
回来的 rollup 再覆盖回去）：

```tsx
const runCosts = useMemo(() => {
  const out: Record<string, RunCost> = {};
  for (const r of progress?.runs ?? []) {
    out[String(r.id)] = { cost_cents: r.cost_cents, charged_points: r.charged_points ?? null,
      model: r.model, status: r.status, prompt_tokens: 0, completion_tokens: 0 };
  }
  if (liveRunCost) {
    const prev = out[liveRunCost.runId];
    out[liveRunCost.runId] = { ...prev, model: prev?.model ?? null, status: prev?.status ?? 'completed',
      prompt_tokens: prev?.prompt_tokens ?? 0, completion_tokens: prev?.completion_tokens ?? 0,
      cost_cents: liveRunCost.cost_cents, charged_points: liveRunCost.charged_points };
  }
  return out;
}, [progress?.runs, liveRunCost]);
```

⚠️ rollup 的 `runs[]` 不带 token 列，所以议题线程这侧浮层第一行恒为 `0 prompt · 0 completion`。
这是**取舍不是缺陷**：补它要改 Part B 的聚合 SQL，超出本 Task 范围——记票（Task 23 已列）。

```bash
cd frontend && npx vitest run components/Todolist/IssueChatThread.runHeaderChips.test.tsx   # 绿
```

- [ ] **Step 5: 聊天面板宿主 —— 一次批量取数（红 → 绿）**

```tsx
// 追加进 frontend/components/chat/AIChatBubble.test.tsx
describe('AIChatBubble — 消耗行（3c §4.2）', () => {
  const cost = { cost_cents: 0.82, charged_points: 0.82, model: 'doubao-seed-2-0-lite',
                 status: 'completed', prompt_tokens: 1200, completion_tokens: 340 };
  it('assistant 气泡尾部画消耗行，tokens 从正文移进浮层', () => {
    render(<MessageBubble role="assistant" content="ok" tokens={1540} cost={cost} />);
    expect(screen.getByTestId('run-cost-tail').textContent).toBe('◇ 0.82 · doubao-seed-2-0-lite');
    expect(screen.queryByText('1540 tokens')).toBeNull();   // tokens 只在浮层里
  });
  it('没有 cost 的气泡维持原样显示 tokens——老会话不该突然少一行', () => {
    render(<MessageBubble role="assistant" content="ok" tokens={1540} />);
    expect(screen.queryByTestId('run-cost-tail')).toBeNull();
    expect(screen.getByText('1540 tokens')).toBeTruthy();
  });
});
```

```bash
cd frontend && npx vitest run components/chat/AIChatBubble.test.tsx   # 红 2
```

`AIChatBubble.tsx`：props（:61 的 `runId` 之后）加

```ts
  /** 这一轮的消耗（3c §4.2）。由面板批量取回后按 run 分发；老会话没有，
   *  此时尾栏维持原来的 `N tokens`。 */
  cost?: RunCost | null;
```

同时把 `cost` 加进 :333-343 的解构参数表。尾栏（:446-450）改成：

```tsx
          {cost ? (
            <RunCostTail costCents={cost.cost_cents} chargedPoints={cost.charged_points}
              model={cost.model} status={cost.status}
              promptTokens={cost.prompt_tokens} completionTokens={cost.completion_tokens} />
          ) : (
            tokens !== undefined && <span className="text-[10px] text-ink-600">{tokens} tokens</span>
          )}
```

`AIChatPanel.tsx` 在 `messages` 声明之后加批量取数：

```tsx
  // 3c §4.2：一次把这屏所有 run 的账取回来。依赖用**字符串**——依赖数组里放数组
  // 会每次渲染都判定「变了」，那就是每渲染一次发一个请求。
  const [runCosts, setRunCosts] = useState<Record<string, RunCost>>({});
  const runIdsKey = useMemo(
    () => Array.from(new Set(messages.map(chatRunId).filter((x): x is string => !!x))).slice(-50).join(','),
    [messages],
  );
  useEffect(() => {
    if (!runIdsKey) return;
    let cancelled = false;
    aiLibraryService.getRunCosts(runIdsKey.split(','))
      .then((items) => { if (!cancelled) setRunCosts((prev) => ({ ...prev, ...items })); })
      .catch((err) => console.error('[AIChatPanel] run costs failed', err));
    return () => { cancelled = true; };
  }, [runIdsKey]);
```

气泡 map（:1407 的 `runId=` 之后）加
`cost={msg.role === 'assistant' ? runCosts[chatRunId(msg) ?? ''] ?? null : null}`。

SSE `done` 分支（:945-954）补：

```tsx
            const rid = typeof evt.data?.run_id === 'string' ? evt.data.run_id : null;
            if (rid && (evt.data.cost_cents != null || evt.data.charged_points != null)) {
              setRunCosts((prev) => ({ ...prev, [rid]: {
                cost_cents: (evt.data.cost_cents as number | null) ?? null,
                charged_points: (evt.data.charged_points as number | null) ?? null,
                model: prev[rid]?.model ?? null, status: prev[rid]?.status ?? 'completed',
                prompt_tokens: prev[rid]?.prompt_tokens ?? 0, completion_tokens: prev[rid]?.completion_tokens ?? 0,
              } }));
            }
```

```bash
cd frontend && npx vitest run components/chat components/AIChatPanel.test.tsx && npm run typecheck   # 绿
```

- [ ] **Step 6: 后端两个 done 帧带上花费（红）**

```python
# backend/tests/services/issues/test_done_frame_cost.py
"""3c §4.2：done 帧带上这次回合的花费，刚结束的那条气泡不必再发一次 /runs/costs。
两个键**恒定存在**、读不到为 null——同 3b 的 `seq` / `outputs`：有时缺席的字段会被
消费方读成 0，而 0 和「不知道」在钱上是两个答案。"""
import pytest
pytestmark = pytest.mark.unit
async def _async(v):
    return v
def _patch(monkeypatch, repo, published):
    from app.services.issues import issue_chat_stream as ics
    async def _fake_publish(issue_id, payload):
        published.append(payload)
    monkeypatch.setattr(ics, "_publish", _fake_publish)
    monkeypatch.setattr(ics, "_last_transcript_seq", lambda rid: _async(9))
    monkeypatch.setattr(ics, "_run_output_keys", lambda rid: _async([]))
    if repo is not None:
        monkeypatch.setattr(
            "app.repositories.agent_runs_repository.get_agent_runs_repository", lambda: repo)
        monkeypatch.setattr(
            "app.repositories.points_repository.get_points_repository", lambda: _Points())
    return ics
class _Points:
    async def charged_points_for_references(self, *, reference_type, reference_ids):
        assert reference_type == "agent_run" and reference_ids == ["77"]
        return {"77": 0.82}
class _Repo:
    async def cost_rows_for_ids(self, ids):
        assert ids == [77]
        return [{"id": 77, "user_id": "u", "issue_id": 5, "cost_cents": 0.82, "model": "m",
                 "status": "completed", "prompt_tokens": 1, "completion_tokens": 2}]
class _Boom:
    async def cost_rows_for_ids(self, ids):
        raise RuntimeError("db down")
async def test_done_frame_carries_cost_and_points(monkeypatch):
    published: list[dict] = []
    ics = _patch(monkeypatch, _Repo(), published)
    await ics.publish_status(5, "done", run_id=77)
    assert published[-1]["cost_cents"] == 0.82 and published[-1]["charged_points"] == 0.82
async def test_done_frame_still_goes_out_when_the_cost_read_fails(monkeypatch):
    """一次遥测失败绝不该让状态帧发不出去（同 `_last_transcript_seq` 的规矩）。"""
    published: list[dict] = []
    ics = _patch(monkeypatch, _Boom(), published)
    await ics.publish_status(5, "done", run_id=77)
    assert published[-1]["phase"] == "done"
    assert published[-1]["cost_cents"] is None and published[-1]["charged_points"] is None
async def test_running_frame_has_the_keys_as_null(monkeypatch):
    """`running` 帧也带这两个键——消费方不必分两种形状去读。"""
    published: list[dict] = []
    ics = _patch(monkeypatch, None, published)
    await ics.publish_status(5, "running", run_id=None)
    assert published[-1]["cost_cents"] is None and published[-1]["charged_points"] is None
```

```bash
cd <worktree>/backend && uv run pytest tests/services/issues/test_done_frame_cost.py -q   # 红 3：KeyError 'cost_cents'
```

- [ ] **Step 7: 后端实现（绿）**

`issue_chat_stream.py`，`_run_output_keys` 之后加：

```python
async def _run_cost(run_id: Any) -> dict[str, Optional[float]]:
    """这次回合的花费与已扣积分。读失败回两个 None——两个键**恒定存在**，`null` 说的是
    「不知道」，缺席会被读成 0（而 0 在钱上是另一个答案）。与 `/ai-library/runs/costs`
    同两个取数方法（run 行 + 积分行），不另开一条读路径。"""
    rid = _as_run_int(run_id)
    if rid is None:
        return {"cost_cents": None, "charged_points": None}
    try:
        from app.repositories.agent_runs_repository import get_agent_runs_repository
        from app.repositories.points_repository import get_points_repository
        rows = await get_agent_runs_repository().cost_rows_for_ids([rid])
        row = next((r for r in rows if int(r["id"]) == rid), None) or {}
        charged = await get_points_repository().charged_points_for_references(
            reference_type="agent_run", reference_ids=[str(rid)])
        return {"cost_cents": row.get("cost_cents"), "charged_points": charged.get(str(rid))}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[issue_chat_stream] cost read failed (run={run_id}): {e}")
        return {"cost_cents": None, "charged_points": None}
```

`publish_status` 体内 `outputs = …` 之后加 `cost = await _run_cost(run_id)`，把 `**cost`
splat 进 payload（`"outputs": outputs,` 之后）；docstring 末尾补一句
「`cost_cents` / `charged_points` 同样恒定存在，未知为 null（3c §4.2）」。

`ai_library_chat_service.py` 顶部 import `from app.services.issues.issue_chat_stream import _run_cost`；
SSE `done` 帧（:355-367）在 `"run_id": result.get("run_id"),` 之后加：

```python
                # 3c §4.2：刚结束的这一轮，气泡不必再发一次 /runs/costs。
                # 与 WS done 帧同形、同一个取数方法：两个键恒定存在，读不到为 null。
                **(await _run_cost(result.get("run_id"))),
```

```bash
cd <worktree>/backend && uv run pytest tests/services/issues/test_done_frame_cost.py -q
cd <worktree>/backend && uv run ruff check . && uv run black --check . && uv run isort --check-only .
```

- [ ] **Step 8: 突变记录**
  - `RunCostTail.tsx` 把优先级倒过来（先看 `costCents`）：第一个用例红。
  - 把 `'—'` 改成 `'¢0.00'`：第三个用例红。
  - `_run_cost` 的 `except` 改成 `raise`：第二个后端用例红（「一次遥测失败不拖垮状态帧」真的被测到）。
  - `publish_status` 的 `**cost` 改成只在 `phase == "done"` 时 splat：第三个后端用例红。
  - `AIChatPanel` 的 `runIdsKey` 依赖换成数组本身：这一条没有单测钉得住，由 Task 22 的 ⑩ 在真栈上证（`/runs/costs` 恰 1 次）。

- [ ] **Step 9: commit + PR**

```bash
git -C /Volumes/program/project-code/repos/nous-app worktree add -b feat/3c-t21-run-cost-tail .worktrees/3c-t21 origin/master
git -C /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t21 add backend/ frontend/
git -C /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t21 status --porcelain     # 确认只有本 Task 的文件
git -C /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t21 commit -m "feat(cost): 每条回复尾部的消耗行 —— 议题线程 + 聊天面板两处宿主，done 帧带花费与积分"
git -C /Volumes/program/project-code/repos/nous-app/.worktrees/3c-t21 push -u origin feat/3c-t21-run-cost-tail
gh pr create --base master --title "feat(cost): 每条回复尾部的消耗行 + done 帧带花费" --body "spec 3c §4.2。消费 Part B 的 /runs/costs（含 cost_rows_for_ids + charged_points_for_references）与 rollup charged_points。"
```

---

### Task 22: 真栈验收（spec §9 十二条，cn.nous.ink）

**Files:**
- 临时（**永不提交**）：`frontend/e2e-prod/tmp-3c-narration.spec.ts`、`frontend/e2e-prod/tmp-3c-costs.spec.ts`

**固定 fixture**（沿用 3b）：team `331438215859255`、project `337650825568029`、分镜
`337650953731886`、议题 MH-95 `348431024148383`；出图走 owner 私有 Codex 目录行 `codex-image`
（`349722376420455`，`codex` / `gpt-6-astra`）+ 价目行 `(gpt-6-astra, codex, per_call_cents 12)`。

**token**：`TOK=$(cat /tmp/nous_token.txt)`（owner）。**别团队 token `TOK2`** 按
`frontend/e2e-prod/README.md`「Minting an access token」铸一枚 debug 账号
（`claude.debug@nous.test`，口令从 `~/.nous/claude-debug.env` 读，绝不进对话或提交）的：

```bash
cd frontend && E2E_MINT_TOKEN=1 E2E_TOKEN_OUT=/tmp/nous_token_other.token \
  npx playwright test --config e2e-prod/playwright.config.ts get-token
TOK2=$(cat /tmp/nous_token_other.token)
# 先证伪前提再用它（探针够不着 ≠ 被测对象是坏的）：
curl -sS https://cn.nous.ink/api/v1/teams -H "Authorization: Bearer $TOK2" | jq -r '.[].id' | grep -c 331438215859255
# 必须输出 0。输出 1 = 这个账号是该团队成员，第 ④ 条此时**无效**，不许读成 PASS；
# 改用一个新注册账号重来。
```

DB 证据一律 `ssh ubuntu 'docker exec nous-db psql -U postgres -p 55434 -d postgres -Atc "<SQL>"'`。

- [ ] **Step 1: 盯部署链（三条都绿才开始）**
  - 迁移：`gh run list -w run-migration.yml -L 3` 最新 `success`；
    `ssh ubuntu 'docker exec nous-db psql -U postgres -p 55434 -d postgres -Atc "\dt public.search_docs public.output_citations"'` 两行都在。
  - 后端：`gh run list -w deploy-gpu.yml -L 5` 最新 `success`；容器内 `grep -c '"partial": True' /app/app/services/ai/runner/agent_runner.py` ≥2、`grep -c _run_cost /app/app/services/issues/issue_chat_stream.py` ≥2、`ls /app/app/api/search_router.py` 有输出；`docker exec nous-worker curl -sS http://localhost:8080/api/v1/readyz` 的 `dbos` 不是 `configured_but_disabled`。
  - 前端：`curl -s https://app.nous.ink/version.json | jq -r .commitSha | cut -c1-7` == 本轮最后一个前端 PR 的合并 SHA 前 7 位。

- [ ] **Step 2: 逐条跑 spec §9 的十二条**

| # | 判据 | 操作 | 期望观察 |
|---|---|---|---|
| ① | 第 201+ 条议题也能搜到 | `ssh ubuntu '… -Atc "SELECT title FROM issues WHERE team_id=331438215859255 ORDER BY created_at DESC OFFSET 240 LIMIT 1"'` 取一个不常见词 `w`；`curl -sS "https://cn.nous.ink/api/v1/search?q=$w&kinds=issue&team_id=331438215859255" -H "Authorization: Bearer $TOK" \| jq '.groups.issues \| length, .[0].issue_key'` | `length ≥ 1` 且 `issue_key` 正是那条深处议题；Issues 页搜索框输 `w` 也能看到该行 |
| ② | 分镜正文里的词能搜到并深链到 step | `ssh ubuntu '… -Atc "SELECT left(body,200) FROM search_docs WHERE entity_kind=''output'' AND ref_id=''337650953731886'' ORDER BY updated_at DESC LIMIT 1"'` 取一个**不在 title 里**的词 `w2`；`curl -sS "https://cn.nous.ink/api/v1/search?q=$w2&kinds=output&project_id=337650825568029" -H "Authorization: Bearer $TOK" \| jq '.groups.outputs[0] \| {id, deep_link}'` | 命中行 `id` 含 `337650953731886`；`deep_link` 形如 `…/issues/MH-…?step=<n>&output=script_shot/337650953731886`；浏览器打开该链接后右栏对应产出卡高亮 |
| ③ | agent 结论摘要里的词能搜到 | `ssh ubuntu '… -Atc "SELECT left(body,200) FROM search_docs WHERE entity_kind=''run'' ORDER BY updated_at DESC LIMIT 1"'` 取词 `w3`；`curl -sS "https://cn.nous.ink/api/v1/search?q=$w3&kinds=run" -H "Authorization: Bearer $TOK" \| jq '.groups.runs[0] \| {id, meta}'` | 命中；`meta` 含 `status` / `model` / `cost_cents` / `agent_name` 四键 |
| ④ | 跨团队零命中（不是 404） | `curl -sS -w '\n%{http_code}\n' "https://cn.nous.ink/api/v1/search?q=$w2&kinds=issue,run,output" -H "Authorization: Bearer $TOK2" \| jq -c '.groups \| {i:(.issues\|length), r:(.runs\|length), o:(.outputs\|length)}'` | HTTP `200`，`{"i":0,"r":0,"o":0}`。**正对照**：同一条 URL 换 `$TOK` 至少一组非空（否则证明的只是「这个词谁都搜不到」） |
| ⑤ | 跨议题 @ 引用 | 在 MH-95 的回复框 @ 页签输 `w2` 命中同项目**另一**议题的产出（行上带来源 chip）→ 发帖；`ssh ubuntu '… -Atc "SELECT kind,ref_id,version,issue_id FROM output_citations ORDER BY id DESC LIMIT 1"'`；`curl -sS "https://cn.nous.ink/api/v1/outputs/script_shot/<ref>" -H "Authorization: Bearer $TOK" \| jq '.versions[0] \| {cited_count, cited_in}'` | 发帖 201；线程里引用卡带来源议题 chip；SQL 末行 `issue_id == 348431024148383`；`cited_count ≥ 1` 且 `cited_in[]` 含 `issue_key == "MH-95"` |
| ⑥ | 小时表与根 run 对账 | 在 MH-95 跑一次会派子 agent 的回合；`ssh ubuntu '… -Atc "SELECT round(sum(cost_cents)::numeric,4) FROM ai_usage_hourly WHERE bucket_hour >= now() - interval ''2 hours'''"` 与 `"SELECT round(sum(cost_cents)::numeric,4) FROM agent_runs WHERE parent_run_id IS NULL AND created_at >= now() - interval ''2 hours'''"`；`curl -sS "https://cn.nous.ink/api/v1/usage/issues/348431024148383" -H "Authorization: Bearer $TOK" \| jq .cost_cents` | 前两个数相等；第三个 == 驾驶舱 Budget 的 `spent_cents` |
| ⑦ | 积分真扣 + 急停 | 上面那次回合后 `ssh ubuntu '… -Atc "SELECT reference_id, -amount AS points FROM point_transactions WHERE type='consume' AND reference_type='agent_run' ORDER BY id DESC LIMIT 1"'`，并记团队余额前后；再 `ssh ubuntu 'printf "AGENT_POINTS_CHARGE_ENABLED=false\n" >> /media/heygo/program/datahub/nous/secrets/backend.env'` + `ssh ubuntu 'cd /media/heygo/program/projects-code/repos/nous-app/deploy/gpu-server && ./up.sh backend worker'`，跑第二回合 | 第一次：`reference_id` == 该 run id，`points == ceil(该 run 自身花费)`，余额减少同值；急停后第二回合 `point_transactions` 的 `agent_run` consume 行 **不增行**。⚠️ 验完把那行从 `backend.env` 删掉并再跑一次 `./up.sh backend worker`（`docker restart` 不重读 env_file） |
| ⑧ | 收件箱不再 500 | `curl -sS -w '\n%{http_code}\n' "https://cn.nous.ink/api/v1/inbox" -H "Authorization: Bearer $TOK" \| jq -c '[.items[].kind] \| unique'` | `200`；`unique` 含 `"agent_question"`（本账号若无该类行，先在 MH-95 触发一次 AskUser 再查） |
| ⑨ | 叙述与动作交错 | `frontend/e2e-prod/tmp-3c-narration.spec.ts`：登录 → MH-95 → 发一条会让 agent 先说明再动手的指令 → 等回合结束 → 读 `[data-testid="agent-run-row"]` 内 `traj-narration` 与 step 行的 DOM 顺序；同时 `ssh ubuntu '… -Atc "SELECT seq, event_type, payload->>''partial'' FROM agent_run_transcript_events WHERE run_id=<run> ORDER BY seq"'` | 页面上正文段与动作行**交错**出现，DOM 顺序 == transcript 里 `assistant{partial}` 与 `tool_call` 的 seq 顺序；SQL 至少一行 `assistant` 且 `partial` 列为 `true`；动作行读作 `Edited shot …` 一类而非工具名。断言一律 `toBeVisible()`，不用 `toHaveCount`（2026-08-12 教训）。跑完 `rm frontend/e2e-prod/tmp-3c-narration.spec.ts` |
| ⑩ | 消耗行 + 只发一次 `/runs/costs` | `frontend/e2e-prod/tmp-3c-costs.spec.ts`：打开聊天面板某会话，`page.on('request')` 统计 `\/ai-library\/runs\/costs` 命中数；hover 最后一条气泡的 `run-cost-tail` 读 `title` | 尾部读作 `◇ n · <model>`（或 `¢n · <model>`）；`title` 三行含 tokens 与 `¢`；整个会话加载期间 `/runs/costs` 命中数 **== 1**。跑完 `rm frontend/e2e-prod/tmp-3c-costs.spec.ts` |
| ⑪ | run 行五列 + `tool_call` 两字段 + 读面 | `ssh ubuntu '… -Atc "SELECT steps,tool_calls,tool_errors,deliverables,turn_end_reason FROM agent_runs WHERE id=<run>"'`；`ssh ubuntu '… -Atc "SELECT payload->>''duration_ms'', payload->>''error_code'' FROM agent_run_transcript_events WHERE run_id=<run> AND event_type=''tool_call'' LIMIT 3"'`；浏览器看 MH-95 驾驶舱与 `/usage` 页 | 五列全非空；`duration_ms` 非空（成功时 `error_code` 为 null）；驾驶舱 Outputs 格带 `¢x / output`、Tools 格带 `n errors`（`data-testid="cockpit-tool-errors"`）；`/usage` 页六枚 tile（含 `tile-cost-per-output` / `tile-tool-errors`）与 turn_end 分布条可见 |
| ⑫ | 常规走查不回归 | `cd frontend && npm run e2e:prod` | 全绿 |

- [ ] **Step 3: 清理临时件** — `git -C <worktree> status --porcelain frontend/e2e-prod/` 必须**零输出**（两个 tmp spec 已删）；
  `rm -f /tmp/nous_token_other.token`（真栈 bearer，用完即删，README 的规矩）。

---

### Task 23: 完成账 + 文档 PR

**Files:**
- Modify: `docs/superpowers/plans/2026-09-15-harness-p4-phase3c-search-efficiency.md`（末尾追加「完成账」节）

**Interfaces:** 无代码接口。

- [ ] **Step 1: 填表**（逐行填实测值；**不许留空位**，某项没跑就写「未跑，原因 X」）

```markdown
**完成账（2026-09-15）**

21 个代码 Task 各一个 worktree（从 `origin/master` 建）+ 独立 PR + TDD + 突变记录 + opus 对抗评审；
合并后逐条盯部署链（后端：deploy-gpu + 容器符号 + readyz；迁移：run-migration + `\dt`；
前端：version.json 7 位 SHA）。SDD 工作区 `.superpowers/sdd/2026-09-15-harness-p4-phase3c/`
（四份侦察报告 + 契约 + 四段计划 + briefs / reports / 评审包 / Task 22 证据表）。

每个 Task 一行。「部署验证」列：后端写 `deploy-gpu success + 容器内新符号 + readyz`，迁移写
`run-migration success + \dt / information_schema 实证`，前端写 `version.json 前 7 位 SHA`。

| Task | PR | 合并 SHA | 评审轮次 | 部署验证 |
|---|---|---|---|---|
| 1 mig 472 四段 + ORM 镜像 | #… | … | … | run-migration；`\dt` 两张新表在；`\d agent_runs` 见 `turn_end_reason` |
| 2–7 对账前置票 A1 / A2 / A3 / A4 / A6 + 回填校验（各一行） | #… | … | … | … |
| 8–9 `emit_tool_call` 咽喉点 + `folds/efficiency.py` + `_finish` 落五列（各一行） | #… | … | … | … |
| 10–12 `/runs/costs` + rollup `efficiency` + `/usage/*` 新列 + UsagePage 六 tile（各一行） | #… | … | … | … |
| 13–15 `search_docs` 投影写方 + `/api/v1/search` + `/issues?q=`（各一行） | #… | … | … | … |
| 16–18 `output_citations` + 血缘 `cited_in` + @ 页签跨议题 + ⌘K（各一行） | #… | … | … | … |
| 19 `assistant{partial:true}` 两处 diff + 三路径用例 | #… | … | … | deploy-gpu；容器内 `agent_runner.py` 有两处 `"partial": True` |
| 20 narration 节点 + 动作动词 + `RunStatusLine` | #… | … | … | deploy-pages；version.json … |
| 21 `RunCostTail` 两处宿主 + done 帧带花费 | #… | … | … | deploy-gpu + deploy-pages |

**真栈验收（spec §9 十二条，Task 22 报告 `task-22-report.md`）**：照 Task 22 Step 2
的十二行原样抄下来，每行改成三列 `| # 判据 | 结论 | 证据 |`，结论取
`PASS / FAIL / BLOCKED / UNVERIFIED`（后两者必须写原因），证据一行给命令输出或 SQL 结果。
十二行一条不许合并、不许省略。

**A3 的「有记录未扣分」存量**（只向前、不追扣，这里只报数）：
`SELECT count(*) AS logged_never_charged, coalesce(sum(cost_points),0) AS points FROM public.ai_usage_logs WHERE cost_points > 0 AND created_at < '<A3 上线时刻，取 deploy-gpu 成功时间>';`（`ai_usage_logs` 没有 `run_id` 列，无法按 run 配对；修复上线前 `point_transactions` 里 `reference_type='agent_run'` 应为 0 行，先用 `SELECT count(*) FROM point_transactions WHERE reference_type='agent_run'` 交叉确认）
→ **N 行**。

**裁定**（全文在 SDD ledger，每条带代价）：…

**本轮新记的票**：…（进 3d 或下一轮 followups）。已知待记三条：
议题线程消耗行的 hover tokens 恒为 `0 prompt · 0 completion`（rollup `runs[]` 无 token 列，补它要改
Part B 的聚合 SQL）；`agent_run_events` / `provider_monthly_spend` 停写后的 DROP 迁移（等一个发布周期）；
`turn` 仍硬编码 1（改它牵动 3a 坐标契约，本期明确不做）。
```

- [ ] **Step 2: 文档 PR**

```bash
git -C /Volumes/program/project-code/repos/nous-app worktree add -b docs/3c-completion-ledger .worktrees/3c-ledger origin/master
git -C /Volumes/program/project-code/repos/nous-app/.worktrees/3c-ledger add docs/superpowers/plans/2026-09-15-harness-p4-phase3c-search-efficiency.md
git -C /Volumes/program/project-code/repos/nous-app/.worktrees/3c-ledger commit -m "docs(plan): 三期 3c 完成账 —— 21 个 Task 的 PR / SHA / 评审轮次 / 部署证据 + 十二条验收结论"
git -C /Volumes/program/project-code/repos/nous-app/.worktrees/3c-ledger push -u origin docs/3c-completion-ledger
gh pr create --base master --title "docs(plan): 三期 3c 完成账" --body "Task 22 的十二条逐条结论 + A3 存量报数 + 本轮记票。"
```

- [ ] **Step 3: 合并后确认按区域跑的 CI** — `gh run list -w ci.yml -L 3`：纯文档 PR 应只展开
  `scripts/ci-changed-areas.sh` 映射出的最小集（兜底只许退化成全跑，不许反过来）。

- [ ] **Step 4: 清理 worktree**

```bash
for w in 3c-t19 3c-t20 3c-t21 3c-ledger; do
  git -C /Volumes/program/project-code/repos/nous-app worktree remove ".worktrees/$w"
done
```

---

## 完成账（2026-09-16）

18 个代码 Task 各一个 worktree（从 `origin/master` 建）+ 独立 PR + TDD + 突变记录 + opus 对抗评审；
合并后逐条盯部署链（后端：`deploy-gpu` success + 容器内新符号 + `readyz`；迁移：`run-migration`
success + `\dt` / information_schema 实证；前端：`version.json` 前 7 位 SHA）。
Task 7 / 12 / 18 是三段的「合并顺序与部署验证」节，**无代码产出**，其部署证据分摊在各段
最后一个 Task 的行里。收尾另有两个 PR：全分支终审的最终修复批（#2347）与补充票（#2346）。

SDD 工作区 `.superpowers/sdd/2026-09-15-harness-p4-phase3c-search-efficiency/`：四份侦察报告 +
共享接口契约 + 四段计划的 briefs / reports + 每轮评审 diff 包 + Task 22 三批真栈证据 +
终审报告 `final-review-report.md`。

### 一、每个 Task 的 PR / 合并 SHA / 评审轮次 / 部署验证

| Task | PR | 合并 SHA | 评审轮次 | 部署验证 |
|---|---|---|---|---|
| 1 mig 472 四段 + ORM 镜像 + 真 PG 集成用例接门禁 | #2319 | `c8555e23` | 初审 + 修复轮 1 | `run-migration` success；`\dt` 见 `search_docs` / `output_citations`；生产核对 == 回填前基线预期（`search_docs` run 273 行 / output 35 行，`agent_runs.team_id` NULL 79→67，四条 FK 在，五列在）；`deploy-gpu` 35059442310 success；`readyz` status=ready / dbos=enabled |
| 2（A1）小时表推自身花费 + `record_usage` 五个计数关键字 | #2325 | `a1204af8b` | 初审 + 修复轮 1 + 复审 | `deploy-gpu` success；容器内 `record_usage` 符号在；`readyz` ready |
| 3（A2）`issue_totals` 加 root 过滤 | #2322 | `eaac5a38f` | 初审（spec ❌ 一条，改判口径）+ 修复轮 1 | `deploy-gpu` 35063762038 success；`readyz` ready |
| 4（A3）积分真扣 + 急停开关 | #2337 | `3538a106f` | 初审 + 修复轮 1 + 复审 + 收尾 | `deploy-gpu` success，**完成于 2026-09-16T09:26:59Z**（= 存量报数的截止点）；生产 `AGENT_POINTS_CHARGE_ENABLED=True`；容器内 `team_of_run` 符号在（`app.services.ai.scope.scope_binding`）；`readyz` ready |
| 5（A4）`InboxKind` 单一来源 + 收件箱逐行容错 | #2324 | `0b0f46b52` | 初审 + 修复轮 1 | `deploy-gpu` success；`readyz` ready；生产 `application_logs` 的 inbox `ValidationError`：部署后 2 分钟内 0 条（部署前 24 h 内 31 条） |
| 6（A6）run 的 `team_id` / `project_id` 从议题兜底 | #2323 | `f97932a39` | 初审 + 修复轮 1 | `deploy-gpu` success；`readyz` `"status":"ready"` |
| 8 `tool_call` 耗时/错误码 + efficiency 计数道 + run 行五列 | #2320 | `bcf591037` | 初审 + 修复轮 1 | `deploy-gpu` 35063620855 success；容器内 `tool_events` / `efficiency` 符号在；`readyz` ready / dbos enabled |
| 9 `rollup.efficiency` + `charged_points` + 驾驶舱两格（含 mig 474 partial index） | #2326 | `30b3d388b` | 初审 + 修复轮 1 + 复审 + 修复轮 2 + 复审 2 | `run-migration` 474 success（生产 `idx_point_transactions_agent_run_consume` 已在）；`deploy-gpu` 35074857938 success，符号 `EMPTY_EFFICIENCY` 在，`readyz` ready；`deploy-pages` success，`version.json` `30b3d38` |
| 10 `/usage/summary` 新列 + `/usage/efficiency` + `/runs/costs` | #2330 | `cf06382bd` | 初审（分两份报告）+ 修复轮 1 + 复审 + 收尾 | `deploy-gpu` success；符号 `MAX_RANGE_DAYS` 在；`readyz` ready；两个新端点无 token 返回 401（路由确已挂载） |
| 11 用量页六 tile + turn_end 分布条 + 团队页两 tile | #2342 | `e139da343` | 初审 + 修复轮 1 + 复审 + 收尾 | `deploy-pages` success；`version.json` `"commitSha":"e139da3"` |
| 13 `search_docs` 投影表与四个写方 | #2321 | `f18620b43` | 初审 + 修复轮 1 | `deploy-gpu` success；符号 `project_run_id_best_effort` 在；`readyz` `"status":"ready"` |
| 14 `GET /api/v1/search` 统一检索端点 | #2329 | `053d551ec` | 初审 + 修复轮 1 + 复审 | `deploy-gpu` success；符号 `unified_search` 在（探针原写成 `search_all`，部署后手动补探）；`readyz` `"status":"ready"` |
| 15 `GET /issues?q=` 与 Issues 页搜索改服务端 | #2336 | `32bf94b0b` | 初审 + 修复轮 1 + 复审 + 修复轮 2（控制方核 diff，不再派复审） | `deploy-gpu` success；`readyz` ready；`deploy-pages` success，`version.json` `32bf94b` |
| 16 引用镜像表 + 血缘 `cited_in` + 引用归属放宽为「链可见」 | #2331 | `612e30a1e` | 初审 + 修复轮 1 + 复审（rebase 后 CI 11 项全绿，两个真库 step 实跑 success） | `deploy-gpu` success；符号 `record_output_citations` 在；`readyz` ready |
| 17 前端 ⌘K 面 + @ 页签跨议题 + 引用卡被引显示 | #2340 | `3722c0ea8` | 初审 + 修复轮 1 + 复审 + 修复轮 2 + 复审 2 + 收尾 | `deploy-pages` success；`version.json` `"commitSha":"3722c0e"` |
| 19 带工具调用的步也写 `assistant{partial:true}` | #2327 | `844a498c9` | 初审 + 修复轮 1 | `deploy-gpu` success；符号 `emit_partial_narration` 在；`readyz` `"status":"ready"` |
| 20 narration 节点 + 动作动词 + `RunStatusLine` | #2333 | `80534e45e` | 初审（1 Critical）+ 修复轮 1 | `deploy-pages` success；`version.json` `"commitSha":"80534e4"` |
| 21 `RunCostTail` 两处宿主 + done 帧带花费 + SSE 回合开始帧 `run_id` | #2345 | `f9eec9c40` | 初审 + 修复轮 1 + 复审 + 收尾 | `deploy-gpu` success，符号 `run_cost_for_frame` 在，`readyz` ready；`deploy-pages` success，`version.json` `f9eec9c` |
| **终审修复批**（I1–I5 + 六项零风险顺手项） | #2347 | `f9c3f06fb` | 全分支终审（0 Critical / 5 Important）+ scoped 复审 + 收尾 | `deploy-gpu` success；符号 `AGENT_RUN_REFERENCE_TYPE` 在；`deploy-pages` success，`version.json` `f9c3f06` |
| **补充票**（`search_docs` 存量正文回填脚本） | #2346 | `c2ff98449` | 初审 + 修复轮 1 + 复审 + 收尾 | `deploy-gpu` 35106713857 success；生产回填：`--dry-run` scanned=35 / filled=35，实跑同数（`unavailable=0` / `raced=0` / `failed=0` / `orphans=0`，2026-09-16 14:13 UTC）；容器内 `backfill_search_docs_bodies` 符号在 |
| 7 / 12 / 18 | **无代码** | — | — | 计划里三段的「合并顺序与部署验证」节，不产出 PR；其验证动作已落在上面各段最后一个 Task 的部署证据里 |

### 二、真栈验收（spec §9 十二条 — 证据全文见 `task-22-report.md`）

目标栈 API `https://cn.nous.ink:88`、前端 `https://app.nous.ink`、DB `nous-db`；
固定 fixture：team `331438215859255`、project `337650825568029`、议题 MH-95 `348431024148383`。
第一批（①–⑨、⑪）在 `3722c0ea8` 上跑，第二批（⑩、⑫）在 `f9eec9c40` 上跑。

| # | 判据 | 结论 | 证据 |
|---|---|---|---|
| ① | 第 201+ 条议题也能搜到 | **PASS**（深度口径按真实数据修正） | 全库最大团队仅 45 条议题，`OFFSET 240` 取不到行，「第 201 条」在当前生产数据上不可构造；改判「列表最深处的议题能否搜到」并两端都验：`/api/v1/search?kinds=issue` 命中该条深处议题的 `issue_key`，Issues 页搜索框输同一词也出该行 |
| ② | 分镜正文里的词能搜到并深链到 step | **PASS** | 取 `search_docs` 里**不在 title 中**的词，`/search?kinds=output&project_id=337650825568029` 命中行 `id` 含 `337650953731886`；`deep_link` 只到议题页（`?step=` 按裁定不编造，`search_docs` 无 step 列） |
| ③ | agent 结论摘要里的词能搜到 | **PASS** | `/search?kinds=run` 命中；`meta` = `status` / `model` / `error_code`（契约原写 `cost_cents` / `agent_name`，Task 14 裁定按实况降级，见第四节） |
| ④ | 跨团队零命中（不是 404） | **PASS** | 前提先证伪：`TOK2` 的 `/api/v1/teams` 不含 `331438215859255`（`contains_fixture=False`）。该 token 请求返回 HTTP `200` + `{"i":0,"r":0,"o":0}`；**正对照**同一 URL 换 `TOK` 至少一组非空 |
| ⑤ | 跨议题 @ 引用 | **PASS** | MH-95 回复框 @ 页签命中 MH-94 的分镜产出（行上带来源 chip）→ 发帖 201；`output_citations` 末行 `issue_id=348431024148383`；`/outputs/script_shot/<ref>` 的 `cited_count` 0→1、`cited_in[]` 含 `issue_key="MH-95"` |
| ⑥ | 小时表与 run 对账 | **PASS** | 一次派子 agent 的回合（6 条 run，链深 4 层）：`sum(ai_usage_hourly.cost_cents)` 近 2 h = `0.7744` == `sum(agent_runs.cost_cents)` 全部 run = `0.7744`（root-only 为 `0.3348`，仅对照）；`/usage/issues/348431024148383` 的 `cost_cents=2.6145` == 驾驶舱 `Budget ¢2.6 / ∞` |
| ⑦ | 积分真扣 + 急停 | **PASS（真扣）/ 急停 UNVERIFIED** | 真扣：回合前 `reference_type='agent_run'` 行数 = 0（最强正对照），回合后 6 条 consume 行（root + 5 子 run 各一条），`reference_id` == 各自 run id，每条 `ceil(自身花费)` = 1，`team_quotas.points_balance` 600→594、`balance_after` 单调。急停：按裁定**未在生产验**——避免改 `backend.env` + `up.sh` 重启打断生产 run；已由单测与本地 env 覆盖验证 |
| ⑧ | 收件箱不再 500 | **PASS** | `GET /api/v1/inbox` HTTP `200`，顶层键 `notifications` / `total` / `unread_count`（不是 `items`），`kind` 含 `agent_question` 与 `generation_result`；本账号本来就有该类行，无需触发 AskUser |
| ⑨ | 叙述与动作交错 | **PASS** | transcript 里有 `assistant{partial:true}` 行且排在 `tool_call` 之前，页面 DOM 顺序 == `seq` 顺序；动作行读作 `Edited shot …` 而非工具名；断言一律 `toBeVisible()` |
| ⑩ | 消耗行 + 只发一次 `/runs/costs` | **PASS**（⑩e 记「已知」，非 FAIL） | ⑩a 议题侧尾栏 `◇ 1.00 · doubao-…`、浮层含 `¢` 与积分；⑩b 带历史的会话整个加载期间 `/ai-library/runs/costs` 命中 **1** 次；⑩c 流式期间 `run-status-line` 读 `Step 1 · 0s`；⑩d 新回合尾栏数值与库内 run 行逐字段吻合；⑩e 回合结束时多 1 次请求（共 2 次），且是**一次批量带两个 id**，不是每 run 一次（记票 K39） |
| ⑪ | run 行五列 + `tool_call` 两字段 + 读面 | **PASS** | root run 五列全非空（`steps=4 tool_calls=3 tool_errors=1 deliverables=1 turn_end_reason=completed`）；`tool_call` 三行 `duration_ms` 全非空，两次成功调用 `error_code` 为 null；驾驶舱 `cockpit-tool-errors` / `cockpit-cost-per-output` / `cockpit-budget` / `cockpit-runs` 四格可见；`/usage` 页 `tile-cost-per-output` `¢14.43`、`tile-tool-errors` `40.0%` 与 `turn-end-completed` 分布条可见 |
| ⑫ | 常规走查不回归 | **PASS** | `npm run e2e:prod` → 3 passed / 1 skipped（skip 的是 README 写明默认跳过的 `get-token`）；⑫ 在删掉两个临时 spec **之后**才跑，覆盖的就是仓库里真实的那套 |

**第三批（终审修复批 #2347 上线后的复核，A–D）** —— 在 `f9c3f06fb` 上跑一个会派子 agent 的回合
（root `350206443694958` + 2 条后代，全部 `completed`）：

| # | 判据 | 结论 | 证据 |
|---|---|---|---|
| A | 消耗行取整棵 run 树的合计 | **PASS** | 树查询得 3 条 consume 行、`SUM=3`；尾栏读 `◇ 3.00 · doubao-…`、hover `¢0.32` + `Charged ◇ 3.00 (incl. sub-agents)`；团队余额 591→588。正对照：同线程三棵不同规模的树分别读 `◇ 6.00` / `◇ 3.00` / `◇ 1.00`，无积分行时退回 `¢0.14 · Not charged (completed)` |
| B1 | workforce 委派子 run 带 `issue_id` | **PASS** | 三条 run 的 `issue_id` 全是 `348431024148383`；第一批同形态回合的 5 条子 run `issue_id` **全为空**，对照明确 |
| B2 | 驾驶舱 Tools 格含子 run | **PASS** | 驾驶舱读 `Tool errors 3 errors 8 calls`；库内该议题全部 run `tool_calls=8`、仅 root `=7`，`8` 只可能来自含子 run 的口径 |
| B3 | `/usage/issues` 含子 run | **FAIL** | 端点返回 `cost_cents=3.0837 / run_count=18`，逐字段等于库内 **root-only**；全部 run 是 `20 条 / 3.1952¢`，差额 `2 条 / 0.1115¢` 恰为本回合那两条 workforce 后代。**机理**：`issue_totals` 的 root FILTER 依赖「root 的 `cost_cents` 是树总额」，而 `_finish` 只折 fold store 里 `by_child` 已有的条目——进程内 subagent 已折，**workforce 委派子 run 是独立 workflow、父行定稿时不在那份 fold 里**。小时表实证：13:00 桶三行分别等于各 run 自己的花费，合计 `0.4335` ≠ root 的 `0.3220`。净效果是同一回合 UI 上「钱按 root 报（`¢0.32`）、分按全树报（`◇ 3.00`）」。已裁定不在 3c 硬修，记 **3d 第 0 票（P1）**，见第三节 (d) |
| C | 议题侧 hover 不再显示 0 tokens | **PASS** | 线程上 18 个 `run-cost-tail` 全部取 `title` 检查，`anyTitleHasPrompt = false`；对照第二批同一位置当时是三行含 `0 prompt · 0 completion tokens`，现为两行 |
| D | `/usage/efficiency` 分组 | **无此组，逻辑由单测覆盖** | `group_by=model` 只有 1 组且 `cost_cents>0`，不存在 `cost_cents==0 && deliverables>0` 的组。正对照 `group_by=agent` 拿 4 组：分母为 0 时 `cost_per_deliverable` 给 `null`（不是 0 也不是 ∞），分母非 0 时正常相除；回合后 `deliverables 2 / cost 14.8055 → 7.4027`，除法随数据更新 |

### 三、口径披露（本期改变了钱怎么算，这些必须先说出来）

终审报告的「金额口径总表」把同一棵 run 树在六个面上各显示什么列全了。**能合法不一致的
格子是设计不是缺陷**，但下面八条是「用户读到的数字会变、或者数字没说出自己的口径」，
一条都不能只留在 SDD 工作区里。

**(a) 团队月度「已花」在 A1 上线那一刻变小了，这不是数据丢失。**
预算门禁读的 `get_team_month_spend_cents` 走小时表。A1（Task 2 / #2325）之前，子 agent 的
花费既滚进父 run 又各自进小时表，是**双计**；A1 之后小时表改成「每个终态 run 一行、只记
自身花费」，于是同一段历史的合计变小。没有任何 UI 或发布说明提到这次口径变更——这里就是
那句说明。

**(b) 两处 UI 与用户验过的 spec §6 画板有差，差在这里：**
- **⌘K 面**：画板稿一的每行带 `¢` / `model` / `status` 与 `Cited ×N`，实装没有——行上只有
  标题与来源，三组结果平铺（记票 K40）。
- **消耗行**：终审修复批之后它显示的是**整棵 run 树的积分合计**（`◇ 3.00 (incl. sub-agents)`），
  不是画板上那个 root 自己的数。这是有意改的（原先只算 root 是 I2），但读数与画板不一致。
- 另有一处契约降级：`/search` 的 run `meta` 给 `status` / `model` / `error_code`，议题 `meta`
  给 `assignee_user_id` / `assignee_agent_id` 而**不是** `assignee_name` / `cost_cents`——后端没有
  显示名来源，Task 14 裁定按实况降级，前端按 id 读。

**(c) 效率账的 `¢/output` 分子分母来自不同的 run 集合。**
分母（`deliverables` 等计数）是「带这个 `issue_id` 的全部 run 的自身计数」，分子（`cost_cents`）
是挂在 root 上的树总额。终审修复批（I1）让 workforce 子 run 戳上 `issue_id` 之后分母补全了，
但在那之前委派出去的活不在分母里，**单价偏高**。存量 run 不回填，所以历史区间的 `¢/output`
仍偏高。

**(d) ⚠️ B3：workforce 委派子 run 的花费不进 root 的树总额。**
进程内 subagent 的花费在 `_finish` 时已折进父行，**workforce 委派出去的子 run 是独立
workflow、父行定稿时不在那份 fold 里，所以没折**。后果：凡是按「root = 树总额」解释的读面
（`/usage/issues/{id}`、驾驶舱 Budget 格、效率账的分子）在有 workforce 委派时**少计那部分钱**；
**积分是全的**（每个 run 各自扣，A 已实证全树 3 条流水）。
真栈实例：某议题 `/usage/issues` 报 `18 run / ¢3.0837`，库内实际 `20 run / ¢3.1952`，
少的 `¢0.1115` 正是两条 workforce 后代。同一回合 UI 上读作「`¢0.32` 配 `◇ 3.00`」。
**已裁定不在 3c 收尾硬修**——正解要结构改动（`agent_runs` 加 `own_cost_cents` 由 `_finish` 写
自身花费、root-only 读面改为按树求和 own；或 workforce 子 run 定稿时原子累加到 root 且 root
定稿不覆盖已累加值），牵动 A2 / Task 3、Task 9、Task 10 三处读面加回填，值一份小 spec。
记为 **3d 第 0 票（P1）**。

**(e) ⚠️ ceil 最低消费按子 run 个数叠加，待用户拍板。**
扣分是「每个 run 各自 `ceil(自身花费)`」，每个付费 run 至少 1 分。真栈实测：一次三层委派回合
（root + 5 子 run）自身花费合计 **≈¢0.92**，扣了 **7 分**——约 10 倍的最低消费放大。
两个选项：**保持现状**（逐 run ceil，简单、每个 run 都有下限），或**改为只在 root 按树总额
ceil 一次**（子 run 不扣；晚于 root 结束的后台子 run 单独扣）。**默认保持现状直到用户回话。**
⚠️ `run_recorder.py` 调用处的注释只说「按自身花费扣，不按树总额」，没提每个 run 各有 1 分
下限——只读那一处的人会以为总额 = `ceil(总花费)`。

**(f) A3 之前「有记录未扣分」的存量：只报数，不追扣。**
按 A3 上线时刻 `2026-09-16T09:26:59Z`（Task 4 的 `deploy-gpu` 成功时间）切：

```sql
SELECT count(*) AS logged_never_charged, coalesce(sum(cost_points),0) AS points
FROM public.ai_usage_logs
WHERE cost_points > 0 AND created_at < '2026-09-16T09:26:59Z';
→ 133 行 / 59.67 分
```

交叉确认成立：同一时刻 `SELECT count(*) FROM point_transactions WHERE reference_type='agent_run'`
= **0**，证明这 133 行确实一分未扣。`ai_usage_logs` 没有 `run_id` 列，无法按 run 配对，所以
口径就是「截止点之前全部 `cost_points>0` 的行」。**只向前，不追扣。**

**(g) BYOK 风险面 3 个 run，急停开关默认是开的。**
没有 run 级 BYOK 标记，`run_recorder` 里 `byo_key=False` 恒定，所以**用户自带 key 的图片 run
也会按平台价扣积分**（记票 K16）。生产风险面实测 3 条：`ark` 的 `doubao-seedream-5-0-pro`
2 run、`codex` 的 `gpt-6-astra` 1 run（两者的 `per_call_cents` 都已配）。
急停 `AGENT_POINTS_CHARGE_ENABLED` **默认保持开启**（用户裁定「修好并真扣」，且这 3 个历史 run
都在用户自己团队）。**用户可以选择关掉急停开关，或摘掉这两个 BYOK 图片模型的 `per_call_cents`。**
多扣的分可从 `point_transactions` 对账退回。

**(h) 补充票超出 spec §7「明确不做」，是有意扩范围。**
spec §7 把「存量产出正文回填」列在明确不做里；Task 22 实测 `search_docs` 的 35 条 output 行
`body` 全空（mig 472 的回填段只填了 run 行的 `output_summary`，产出段漏了 `body` 那一列），
表现为「老分镜按正文搜不到、改过一次之后才能搜到」——检索这一交付的主用例在存量数据上是坏的，
所以扩了范围。
两点必须说清：① 它**是脚本不是迁移**（35 条里 32 条要走 `diff.rebuild_content()` 逐条重放
账本，SQL 表达不出来；为剩下 3 条另写一份 SQL 会造出两侧各算一遍、写进同一列、而且没有任何
东西会说出分叉的缝）；② **需要人手在容器里跑**，先 `--dry-run` 再实跑，不随部署自动发生。
生产回填结果：2026-09-16 14:13 UTC 在容器内先 `--dry-run` 后实跑，两次同数：
`scanned=35 filled=35 unavailable=0 raced=0 failed=0`，孤儿普查 `orphans=0`。
验收 SQL：`search_docs` 的 output 行**空正文 0 / 37**（比回填时的 35 多出的 1 条是验收期间
新登记的产出，由实时写方带正文，不是漏网）。
⚠️ **run 行空正文 87 / 285 不变，这是对的**：mig 472 的 run 段本来就写了 `output_summary`，
那 87 条为空是因为 `output_summary` 本身是 NULL——「这次运行没有摘要」不是「回填漏了」，
无可重建的正文，按设计不在候选集里。脚本仍带 run 那条臂，它补的是**顺序**不是历史：摘要若在
472 之后被补上（回溯修数据、以后新加的摘要生成），这条臂就是它进检索的路。

### 四、裁定（全文在 SDD ledger，每条带「错了代价」）

| 裁定 | 理由 | 错了代价 |
|---|---|---|
| Task 8 先合，Task 2 rebase 时把内联读法换成 `_efficiency_counts()` | 两 Task 改同一函数体，键名相同、语义不变 | 两处并存的读法，一处漏零 |
| A6 兜底只改 `ai_library_chat_service.py:1440`，另一侧加回归 fixture | `conversation_agent_turn.py` 那侧是死分支 | 某条未知派发链 `team_id` 仍 NULL，A6 回填 SQL 兜底 |
| BYOK 不跳过扣分：保留 `byo_key=False` + 急停开关 + 完成账对照，写成 Stated Limitation | 没有 run 级 BYOK 标记 | BYOK 图片 run 按平台价扣分（用户可从 `point_transactions` 对账退回） |
| 存量未扣分按 A3 上线时刻切，全部 `cost_points>0` 行即存量 | `ai_usage_logs` 无 `run_id`，无法按 run 配对 | 只是报数口径 |
| 阶段性叙述做两处 diff + 「adapter 无 `stream` 属性」用例 | 缓冲回退已委托 `run_turn`，那是生产唯一路径 | 缓冲分支漏事件，用例会红 |
| 深链只到议题页，不编造 `?step=` | `search_docs` 无 step 列 | 少一个锚点（记票 K35） |
| Task 1：给两张新表四条外键加级联（run CASCADE / issue SET NULL） | 投影随真相消失是 `canvas_asset_refs` 的既有语义；spec 说投影可重建但没说可残留 | 删 run 时级联多删两张派生表的行，都可重建 |
| Task 1：C1 门禁不覆盖 `using` / `opclass` 的缺口本 PR 只在测试里补断言，门禁本身记票 | 改门禁是另一件事 | 门禁上仍看不出 opclass 漂移（记票 K17） |
| 多次并行派发（13∥8、3∥5∥6、9∥10∥19、20、14∥16） | 独立 worktree / PR，文件集互不相交 | 一次 rebase 冲突手解 |
| Task 3：`cost_cents` / `run_count` 按 root-only，token 三列按全部 run | 值相等一致性在桩 session 层不可实现，交 Task 22 ⑥ 真栈兜 | token 口径若将来也上滚，需再对齐一次 |
| Task 8：`duration_ms` 在派发结束处取；主 fold 返回 None 时回滚快照再跑计数道 | 真测工具执行时间；计数道不该救回主 fold 已弃的改动 | 每事件多一次深拷贝（可忽略） |
| Task 13：补接两处终态写方 + 分支测试 + 真 PG 集成用例接 `schema-drift` | `_finish` 不是唯一终态写方 | 两处写方多一次 best-effort 写、集成多一个 step |
| Task 13：章节正文与 gateway `lighting` 不在本 Task 扩范围，记票 | 章节 create 路径行未必有 content，接线非一行 | 章节只能按标题搜（记票 K23 / K24） |
| Task 5：`NOTIFICATION_KINDS` 挪到 `app/schemas/inbox_kinds.py` 作单一来源 | schemas 反向 import services 会拉起 sqlalchemy / settings | 一次三行 import 的搬家 |
| Task 5：整表皆坏时按请求聚合 skipped 计数、一条 ERROR | 每行一条 ERROR 会打穿日志 | 单行定位粒度变粗 |
| Task 2：终态 run **无条件**写小时行（cost / token 可为 0），`event_count = int(tokens>0)` | 零花费失败 run 不进表会让 `failed_runs` 偏低 | 小时表行数略增（每 run 一行本就是设计） |
| Task 9：本 PR 带 mig 474 只加 partial index，扣分写方由 Task 4 落地 | 索引缺席只是慢不是坏，迁移与代码顺序无所谓 | 一次额外迁移号 |
| Task 9：`efficiency` 缺席守卫必须留（类型可选 + `?? EMPTY_EFFICIENCY` 兼容窗口） | 前端链（~2 min）先于后端链（~10 min）上线，两链无顺序保证 | 类型宽一档 |
| Task 10：`charged_points_for_references` 保留 raise 版单一定义，Task 9 先合、Task 10 再 rebase 删重复 | 同一方法两份定义且失败语义相反 | Task 10 多等 Task 9 一轮 |
| Task 14：每个 kind 各发一次 search（不共用一次 overfetch） | 高分组会饿死另一组 | 多一次查询 |
| Task 14：契约 `meta.assignee_name` → `assignee_user_id` / `assignee_agent_id` | 后端没有显示名来源 | Task 17 少两个显示字段 |
| Task 15：搜索态下新建改为触发一次重拉（不前插） | 前插会绕过服务端 `q` | 搜索态下新建后多一次请求 |
| Task 16：引用归属逐版判 `issue_key` + 可见性，判不过退链级 | `newest_with_a_run(chain)` 会把跨议题链标错来源 | 多一条集成 step |
| Task 17：⌘K 的主人定为画布，在 capture 阶段认领 | 画布是既有功能，双主人必须有一个赢 | 结果行获焦时画布仍抢 ⌘K（记票） |
| Task 20：状态行只显示 step；`run_id` 实时接线归 Task 21 | `openTool` 判据在 Task 8 之后恒不命中（`tool_call` 执行后才写）；`Running <tool>` 需 `tool_start` 事件 | 状态行少一个词（记票 K37） |
| Task 4：子 run 的 `team_id` 走旁路 helper `team_of_run` 真透传 | A6 不变量 + partial 索引让 NULL run 在团队效率账里不存在，正是 3c 交付的读面 | PR 多两文件，委派计费打开（自身口径，正确模型） |
| Task 4：`ensure_team_quota` 独立 try + `create_team_quota_if_absent`（DO NOTHING + 回读） | 并发开通撞唯一键会丢一次扣分；回读修掉「输家 `{}` 覆盖赢家余额」的静默清零 | 多一次回读 |
| Task 4：ceil 最低消费按子 run 个数叠加，接受并写进 PR body + 完成账 | 积分是整数，每个付费 run 至少 1 分 | 多子 run 的回合多扣几分（见 (e)） |
| Task 22 ⑦：急停不在生产改 `backend.env` / `up.sh` 重启 | 会打断正在跑的生产 run | 急停少一次真栈证据（单测 + 本地 env 已覆盖） |
| 注释一词的改动不值一轮复审（Task 14 / 16 的 schema-drift 计数注释） | 控制方 grep 核实即可 | 一处注释少一双眼 |
| 终审：最终修复批一次派发 = I1–I5 + 六项零风险顺手项，且 **I2 做全**（不是只加说明） | 五条都是「用户读到的数字或文字与真相不符」，每条 1–10 行 | 一批多约 40 行 |
| 终审 scoped：`heartbeat_lost` 取代 `interrupted` 写进 `turn_end_reason` | 列更具体，transcript 事件 detail 里本来就是同一个词；分布条需要「因为心跳没了」这一层 | `INTERRUPTED` 对该列再无写方，`interrupted` 段对新 run 归零（存量行仍在） |
| B3 不在 3c 收尾硬修，记 3d 第 0 票（P1） | 正解要结构改动 + 三处读面 + 回填，值一份小 spec | 委派回合的议题账 / 效率账在 3d 前少计 workforce 子 run 的钱（分是全的） |
| 补充票选 Python 脚本而非 SQL 迁移 475 | 35 条里 32 条要重放账本，SQL 表达不出 `rebuild_content()` | 回填不随部署自动发生，需人手跑 |
### 五、本轮记的票

以终审报告的「记票合并表」41 条为底（ledger 约 50 条散票去重归并），加终审修复批与第三批
验收之后新发现的 10 条，共 **51 条**。归属：**已解决** = 本期后续 PR 吃掉；**3d** = 下一期顺手；
**票** = 独立开票；**用户** = 需要产品决策。

| # | 内容 | 位置 | 级别 | 归属 | 状态 |
|---|---|---|---|---|---|
| K1 | workforce run 不戳 `issue_id`，效率账与议题账漏计委派 | `workforce/agent_worker.py:291` | Important | — | **已解决（#2347）**，真栈 B1 PASS |
| K2 | 消耗行只算 root 的 `charged_points` | `issue_rollup.py:241` / `ai_library_router.py:2406` | Important | — | **已解决（#2347）**，真栈 A PASS |
| K3 | 议题侧 hover 恒说 `0 prompt · 0 completion` | `mergeRunCosts.ts:30-36` | Important | — | **已解决（#2347）**，真栈 C PASS |
| K4 | 崩溃类终态不进 `turn_end_reason` / 小时表 | `liveness_scanner` / `liveness/reconcile.py:57` / `mark_heartbeat_lost_ids` | Important | — | **已解决（#2347）**，两半都做了 |
| K5 | 分组效率表 `¢0.00 / output`（cost 带 root FILTER 而计数不带） | `agent_runs_repository.py:1074` + `ai_library_router.py:3100` | Important | — | **已解决（#2347）**，改 null + docstring |
| K6 | `reference_type="agent_run"` 五处字面量无常量 | token_billing / issue_rollup / issue_chat_stream / ai_library_router / models.billing | Minor | — | **已解决（#2347）** `AGENT_RUN_REFERENCE_TYPE` |
| K7 | `reconcile_run` 扣费未被 rowcount 幂等守卫罩住 | `run_recorder.py:906-1005` | Minor（钱） | — | **已解决（#2347）** |
| K8 | `reconcile_stranded_runs` 未接投影 | `services/liveness/reconcile.py:57-71` | Minor | — | **已解决（#2347）**，与 K4 同一处改动 |
| K9 | CLAUDE.md「LIKE 无默认转义符」说法错，已复制到两处代码注释 | `CLAUDE.md:197` / `like_escape.py:12-15` / `issue_repository.py:308-311` | Minor | — | **已解决（#2347）**，三处同改 |
| K10 | CostAuditor 摘除后的失真注释 | `hooks_protocol.py:4,15` / `hooks_bridge.py:4` / `agent_runner.py:1085` | Minor | — | **已解决（#2347）** |
| K11 | `schema-drift.yml` 的 step 数词靠注释管，已错四次 | `.github/workflows/schema-drift.yml:198-204` | Minor | — | **已解决（#2347）**，加了 grep 门禁 |
| K12 | `schema-drift.yml` `push.paths` 未跟着扩 | 同上 :211-216 | Minor | 票 | 部分（#2346 给自己的新 step 补了两条 paths，既有缺口仍在） |
| K13 | 存量 35 条 output 行 `body` 为空 | mig 472 回填段 | Minor | — | **已解决（#2346）**。⚠️ 超出 spec §7「明确不做」，是有意扩范围，见第三节 (h) |
| K14 | ceil 逐子 run 叠加（真栈 ≈¢0.92 → 7 分） | `token_billing.py:362` | 产品决策 | **用户** | 已上报，默认保持现状（见 (e)） |
| K15 | 急停 `AGENT_POINTS_CHARGE_ENABLED` 真栈未验 | — | — | **用户** | 裁定接受（避免重启生产），单测 + 本地 env 已覆盖 |
| K16 | BYOK run 按平台价扣分（`byo_key=False` 恒定） | `run_recorder.py:1007-1012` | Stated Limitation | 票（加 run 级 BYOK 标记） | 否，有急停兜底；风险面 3 run，见 (g) |
| K17 | C1 门禁不比 `using` / `opclass` | `tests/db/test_orm_indexes_integration.py` | Minor | 票 | 否 |
| K18 | C1 门禁不比 `ondelete`；`pg_constraint` 守卫只按 `conname` | 同上 | Minor | 票 | 否 |
| K19 | `efficiency` 视图未进 `metadata_json` 镜像 | `run_recorder._mirror` | Minor | 票 | 否（不影响落列，运行中读不到而已） |
| K20 | `interrupted_turn` 注释「可自愈」不实 | `interrupted_turn.py` | Minor | 3d | 否 |
| K21 | Skill 分支 `tool_cache.put` 留在 duration 窗口内 | `agent_runner.py` | Minor | 3d | 否 |
| K22 | 有计数道的四类事件 `apply` 双 deepcopy | `run_projection.py:180-186` | Minor（性能） | 票 | 否 |
| K23 | `script_chapter` 登记未接正文 → 章节只能按标题搜 | `script_service.py:190` | Minor | 3d | 否（回填脚本对该 kind 记 `unavailable`） |
| K24 | gateway `_shot_dict` 缺 `lighting` → 分镜正文少一列 | `scoped_script_gateway.py` | Minor | 3d | 否 |
| K25 | 心跳清扫 N+1；`agent_runs_repository:955` import 在 try 外；`_run_title` strip 与 SQL `LEFT` 分叉 | Task 13 三个 minor | Minor | 票 | 否 |
| K26 | `_hourly_upsert_stmt` 的 `set_` 子句全仓零真库覆盖 | `ai_usage.py` | Minor（假绿） | 票 | 否 |
| K27 | `event_count = int(tokens>0)` 让零 token 出图 run 在 Calls 列显示 0；`run_count` 尚无 UI 出口 | `ai_usage.py:149` / UsagePage | Minor | 3d | 否（#2347 之后崩溃类 run 的 Calls 与 run 数差得更明显） |
| K28 | `charged_points_for_references` 无 team 维度（靠调用方保证） | `points_repository.py:814` | Minor | 票 | 否 |
| K29 | `agent_runs.project_id` 无索引（project scope 效率查询全扫） | — | Minor（性能） | 票（下一个 mig） | 否 |
| K30 | `parse_window_dt` 放宽到 `Any`；`get_project_by_id` 吞错成 404 | `utils/time_window.py` / projects repo | Minor | 票 | 否 |
| K31 | `assert route.response_model is not None` 是假绿（FastAPI 从返回注解推），需扫其他 router | 全仓 | Minor（测试质量） | 票 | 否 |
| K32 | `/runs/costs` 50 个 id 的 query string 长度 | `ai_library_router.py:2370` | Minor | 票 | 否 |
| K33 | `q` 超长走 422 非 typed 400；`issue_id` 过滤对议题组不生效（router docstring 未写明） | `search_router.py:587` | Minor | 3d | 否 |
| K34 | 分 kind 后两次串行 `await` 可 `gather` | `services/search/service.py:212` | Minor（性能） | 3d | 否 |
| K35 | 深链没有 `?step=`（`search_docs` 无 step 列） | 裁定 | Minor | 票（加列或不做） | 否，有裁定 |
| K36 | `toolActivity.ts:162` 的 ok 判定仍是旧形（协作时间线把 denied 读成成功） | `toolActivity.ts` | Minor | **票（建议提前）** | 否——`foldEvents` 已修，这是同一缺陷的第二份拷贝 |
| K37 | `error_code` 未进 detail；被跳过的调用渲成 0ms；`Running <tool>` 需 `tool_start` 事件 | Task 20 三条 | Minor | 3d | 否 |
| K38 | `liveRunCost` / `liveRunId` 永不清除；`status` 缺省 `completed` | `mergeRunCosts.ts` / `AIChatPanel.tsx:297` | Minor | 3d | 否 |
| K39 | `getRunCosts` 并发无上限；回合结束后整屏重取一次（Task 22 ⑩e 实测） | `aiLibraryService.ts:749` / `AIChatPanel.tsx:300` | Minor | 3d | 否 |
| K40 | ⌘K 行缺 spec §6 稿一的 `¢` / `model` / `status` 与 `Cited ×N` | `CommandPalette.tsx:433` | Minor（与用户验过的画板不符） | 票 | 否，见 (b) |
| K41 | `add_points` 非原子读-改-写；`cost_points` 小数 vs `amount` 整数系统性差；两条按变量名钉的既有守卫 | 既有 | Minor | 票 | 否（既有，本期未加剧） |
| K42 | **B3：workforce 委派子 run 的花费不进 root 树总额**，`/usage/issues`、驾驶舱 Budget、效率账分子在有委派时少计 | `run_recorder._finish` 折 fold store `by_child` / `usage_repository.issue_totals` | **Important** | **3d 第 0 票（P1）** | 否，见 (d)。真栈实例 `¢0.1115` / `¢3.1952` |
| K43 | 消耗行 hover 的 `(incl. sub-agents)` 文案无条件，单 run 也这么说（NF1） | `RunCostTail` 文案 | Minor | 3d | 否 |
| K44 | 崩溃词与 `turn_end` 分类器词表交集为空这件事没有断言钉住（NF3） | `tests/runner/test_turn_end_reasons.py` 一带 | Minor（测试质量） | 票 | 否 |
| K45 | `_finish` 的 rowcount 幂等守卫不罩 `project_run_best_effort` 投影（NF4）——CAS 没抢到时再投一次会覆盖清扫器刚写的真终态 | `run_recorder.py` | Minor | 票 | 否（`liveness_scanner._mark_dead` 那边已有这道守卫） |
| K46 | `run_ids_in_trees` 问一个中间节点只回它自己（不递归） | `agent_runs_repository.run_ids_in_trees` | Minor | 3d | 否。现状正确（两个宿主问的都是 root），将来要给子 run 单独画消耗行得换递归 CTE |
| K47 | 树合计多一次 RTT（先取树、再取流水） | `charged_points_for_run_trees` | Minor（性能） | 3d | 否，与 K39 同一个被整屏重取的端点 |
| K48 | `_attach_to_parent_run` 失败被吞 → `parent_run_id` / `root_run_id` 双 NULL 的子 run 被所有 root-only 读面当成 root | `run_recorder` | Minor | 票 | 否 |
| K49 | `test_revert_output_integration.py` 的 `fx` 夹具 `finally` 不清 `search_docs`，本地复用库累积孤儿投影行（`run_id` NULL，外键 CASCADE 带不走） | `tests/db/test_revert_output_integration.py` | Minor（测试基建） | 票 | 否。CI 每次新建库所以看不出来，也不会假红；是补充票的孤儿普查第一次让它们现形 |
| K50 | `agent_run_events` / `provider_monthly_spend` 停写后的 DROP 迁移 | 两张表 | Minor | 票（等一个发布周期） | 否 |
| K51 | `turn` 仍硬编码 1 | 坐标写入处 | Minor | 票 | 否——改它牵动 3a 的坐标契约，本期**明确不做** |

**已被后续 PR 吃掉、不再需要开票的**：`point_transactions` 缺 partial index（Task 9 带 mig 474）；
`InboxKind` 四处口径（Task 5 的 `schemas/inbox_kinds.py` 单一来源）；`_finish` 不是唯一终态写方的
**投影**那一半（Task 13 修复轮接了两处）；`efficiency_for_issue` 的 `.filter()` 挂错位置导致永远
返回 `{}`（Task 9 实施期自查修掉，单测当时全绿）。

### 六、文档更正（终审提出，已随 #2347 落地或记票）

- **D1** — CLAUDE.md「Postgres 的 LIKE 没有默认转义符」**是错的**。反斜杠本来就是
  `LIKE` / `ILIKE` 的默认转义符，Python 侧 `escape_like` 本身就有效；SQL 侧显式
  `ESCAPE '\'` 是**把默认钉死的声明**，不是让转义生效的机制。同一交付里
  `search_docs_repository.py` 写对了、`like_escape.py` 与 `issue_repository.py` 沿用了错的说法，
  一个分支里两种互相矛盾的措辞会让下一个人把「某处漏了 `ESCAPE`」误判成 P0。**三处已随 #2347 同改。**
- **D2** — `runner/replay.py` 现在会改变模型看到什么（Task 19 让回放跳过 `assistant{partial:true}`
  行，理由正确：真实历史里那一步是**一条**带 text + tool_use 的消息，单独重放文本会造成相邻
  同角色消息、Anthropic 拒绝），但 `app/services/ai/runner/` **没有 README**，CLAUDE.md 的
  「Model Experience 三问」从这一刻起咬住它。**记票**：给该目录补一份 README，三问覆盖
  `replay.py` + `narration_events.py` 这一对（模型看到的是 `user` / 非 partial `assistant` 两类行；
  叙述行不进历史所以 fork 上下文不随叙述条数增长；fork 是另起一次请求，本模块不承诺前缀复用）。
- **D3** — `agent_runs_repository.py` 两处 docstring 的口径追不上实现（`efficiency_for_issue` 的
  「root + children」实际取决于 children 有没有 `issue_id`；分组粒度写清了但没说分组后的后果）。
  **已随 #2347 改。**
