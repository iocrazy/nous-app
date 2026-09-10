# harness 二期 2b-2「编排：后台/续聊子代理 + 定时唤醒进收件箱 + 接活 workforce 链」实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** issue 上的 agent 能把子任务放到后台跑、能对同一子代理续聊，结果经收件箱回到父 run；用户与 agent 都能给 issue 定一次性唤醒，到点时在跑就插进下一步、空闲就开新一轮；workforce 委派链真正跑通并可开关；顺手关掉三张小票（派发窗口、`issue_id` 创建即写、Vitest 拆卸抖动）。

**Architecture:** 后台子代理 = 一条 `task_tracking` 任务行 + 既有 `agent_workforce_workflow`（接活后的 inbox_dispatch tick 统一派发所有「queued 且未派发」的任务，step 只返回、body 才 enqueue）+ `run_one_task` 的 `subagent` 分支调同一个 `_spawn`；完成后写 `agent_run_inbox(kind=subagent_result)`，父 run 在下一个步边界经既有 InboxClaim 钩子读到；续聊 = 用 2b-1 的 `replay.messages_from_events` 从子 run 事件重建消息再跑一轮，新行复用 `fork_of_run_id/fork_at_seq`。定时唤醒 = `user_schedules` 新 task_type `issue_wakeup`（一次性 `cron_expr IS NULL`），`scheduled_master` 的 step 返回 order、body 调新抽出的 `deliver_or_dispatch`（评论 / 定时 / 子代理结果三处共用「在跑投收件箱、空闲派发」）。子代理与唤醒在父 run 上落事件（`subagent_spawned/done`、`schedule_set`），折叠成 `view.children` / `view.wakeups`，前端从事件推导卡片与芯片。

**Tech Stack:** FastAPI + SQLAlchemy async（新 SQL 一律 ORM，禁 `text()`）、DBOS（scheduled tick + queue，**绝不在 `@DBOS.step` 内 enqueue**）、pytest；React 19 + vitest/RTL、i18n `t(key, fallback)` en/zh 同改、语义色 token（子代理 agent、后台/定时 info、完成 ok）。

**Spec:** `docs/superpowers/specs/2026-09-10-harness-p4-phase2b2-orchestration-design.md`

## Global Constraints

- 每 Task 独立 worktree：`cd <主检出> && bash scripts/worktree-manager.sh create feat/p4-2b2-tN` **单独一条 Bash**，建完立即 `git -C <wt> fetch origin && git -C <wt> reset --hard origin/master && git -C <wt> branch --unset-upstream`（脚本从本机 master 分叉，本机 master 可能带别的会话的提交）；前端 worktree 需 `ln -s <主检出>/frontend/node_modules <wt>/frontend/node_modules`；写操作一律 `git -C <绝对路径>`，同一轮最多一条依赖 cwd 的 Bash；主检出只读。
- 独立 PR；PR 描述必有「复用 / 删除了什么」「偏差」「对抗评审」「突变记录」「测试」。推送用显式 lease：`R=$(git ls-remote origin refs/heads/X | cut -f1); git push --force-with-lease=refs/heads/X:$R origin HEAD:refs/heads/X`；PR 一出来先看 `gh pr view N --json mergeable,mergeStateStatus`，DIRTY 就先修基底。
- TDD：每个关键断言先红后绿；每个 Task 至少一处突变让测试转红并记进 PR。
- 对抗评审用 `Agent`（model: opus），发现全修。
- 后端全量 `cd <wt>/backend && uv run pytest -q -p no:cacheprovider --deselect tests/api/test_distribution_music_search.py --deselect tests/api/test_distribution_topic_suggest.py tests`（qishui-audio 一例为本机 mimetypes 平台差异）；前端全量在 `<wt>/frontend` 下 `npx vitest run`。
- lint：后端 `flake8 app tests` 零告警 + isort/black/ruff 改动文件；前端 `npx eslint <改动文件>`。
- 仓库现为 private、Actions 额度未恢复前托管 CI 是账单假红（`runner_name` 空 + `steps=0`），以本地全量 + actionlint 替代门禁后合并；后端 `deploy-gpu.yml` 是 self-hosted 不受影响，合并后 `gh run list --workflow=deploy-gpu.yml` 盯到 success 并探 `readyz`；前端链恢复后再验 `version.json`。
- 迁移与消费代码分 PR；迁移不 `SET ROLE`，CHECK 用 DROP/ADD 幂等写法；事件类型 / 收件箱 kind 新增必须先迁移。
- **给 run_turn 结果或工具结果加任何新标志，必须在 `tests/runner/test_turn_end_reasons.py` 用「adapter 无 `stream` 属性」用例证明穿过缓冲回退分支**（CLAUDE.md 已知陷阱）。
- 边界 mock 用真实 wire 形状（Snowflake id 在 issues/progress 是 number）；新触发路径必须带类型化失败回显。
- UI 文案英文、Title Case；新 key en/zh 同加；不引入旧色相类名。
- 偏离本计划或 spec 之处，回写两处「实施记录」。

---

# harness 二期 2b-2「编排」实施计划 · PART 1（Task 1–3）

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 逐 Task 执行。步骤用 `- [ ]` 勾选。

**Goal（本 PART）：** 先把地基铺平——迁移 461 放行后续三个 Task 要写的类型、堵住「派发窗口」这个 fork/resume 静默失败的洞、把 `agent_runs.issue_id` 在创建时就写上，然后把 workforce 执行链的四处断线一次接通并删掉死组件。

**Spec:** `docs/superpowers/specs/2026-09-10-harness-p4-phase2b2-orchestration-design.md`（§0 前提核对、§1 接活 workforce、§4.1/§4.2 两张小票、§6 汇总、§7 测试）

**Tech Stack:** FastAPI + SQLAlchemy async（新 SQL 一律 ORM，禁 `text()`）、DBOS（step 只整形、workflow body 才派发）、pytest。

## Global Constraints

- 每 Task 独立 worktree（`bash scripts/worktree-manager.sh create feat/p4-2b2-tN`，建完立刻 `git fetch && git rebase --onto origin/master`）+ 独立 PR。
- TDD：关键断言先红后绿；每 Task 至少一处突变让测试转红并记进 PR。
- 后端全量：`cd backend && uv run pytest -q -p no:cacheprovider --deselect tests/api/test_distribution_music_search.py --deselect tests/api/test_distribution_topic_suggest.py tests`。
- lint：改动文件跑 `uv run isort`、`uv run black`、`uv run ruff check`。
- 迁移不 `SET ROLE`；DROP/ADD 幂等；**迁移与消费代码分 PR**。
- 对抗评审用 `Agent`（model: opus），发现全修；偏离本计划或 spec 之处回写两处「实施记录」。

**勘察后与 spec 的六处已定偏差（T1 一并回写 spec §0/§1/§6）：**

1. `scripts/check-realtime-publication-drift.sh` **没有硬编码期望表清单**——它 grep 前端订阅再与生产发布比对，全文不含 `agent_tasks`。DROP 后该脚本零改动；发布成员资格随 `DROP TABLE` 自动消失（mig 164 `ALTER PUBLICATION ... ADD TABLE` 加的）。
2. `agent_tasks` **有 ORM 模型**（`app/models/agents.py:568` `class AgentTasks`，`models/__init__.py:43,283` 导入 + `__all__`），生产零消费方（仓库层只有注释提到表名）。schema-drift 两向零容忍，模型必须与迁移**同一个 PR**删（同 mig 451 的口径）——这与「迁移与消费代码分 PR」冲突，本 Task 取前者。
3. `backend/tests/models/test_transcript_event_types_phase2a.py:33` 把 `LATEST_MIGRATION` **硬编码成 460**；不改它，461 加的三个类型会被读成 ORM 单侧漂移。
4. `agent_run_inbox.kind` 的 ORM 镜像在 `models/agents.py:713-715`，但**没有任何测试钉住它**（事件类型有，inbox kind 没有）。T1 补一个。
5. `InboxProcessor.tick()` 已经返回 `{"agents_processed","tasks_created","errors"}`——`created` 语义已存在，叫 `tasks_created`。而 `workforce_dispatch.inbox_dispatch_workflow` 读的是 `result.get("tasks_enqueued", 0)`，**这个键从来不存在**，那行 info 日志一次都没打过。真 bug，T3 顺手修。
6. `inflight_count` **没有 healthz 消费方**。唯一生产读者是 `services/workforce/scheduler.py:124`（本期删的死组件）；`api/workforce_router.py:409` 读 `app.state.workforce_scheduler`，PR-D8 后没人写过这个 attr，所以那个健康端点今天恒返回 `scheduler not initialised` / `status=down`。改 async 不破坏任何调用方；那个端点的谎言另记，不在本期。

---

### Task 1: 迁移 461（三个事件类型 + `subagent_result` + `cron_expr` 可空 + 存量自禁 + DROP `agent_tasks`）

**Files:**
- Create: `supabase/migrations/461_harness_p4_phase2b2_orchestration.sql`
- Modify: `backend/app/models/agents.py`（事件 CHECK `527-534`；inbox kind CHECK `712-716`；删 `class AgentTasks` `568-670`）
- Modify: `backend/app/models/__init__.py:43,283`（去 `AgentTasks` 导入与 `__all__` 条目）
- Modify: `backend/tests/models/test_transcript_event_types_phase2a.py:33`（`LATEST_MIGRATION` → 461）
- Test: `backend/tests/db/test_migration_461_orchestration.py`（新，源码守卫）
- Modify: spec §0/§1/§6 实施记录（上面六条偏差）

**Interfaces:**
- Produces（后续 Task 依赖）：transcript 事件白名单含 `subagent_spawned` / `subagent_done` / `schedule_set`；`agent_run_inbox.kind` 含 `subagent_result`；`user_schedules.cron_expr` 可空且受 `user_schedules_cron_or_once` 约束。
- Consumes：`tests/models/test_transcript_event_types_phase2a._literals` / `._orm_check_sql`（复用，不重写解析器）。

- [ ] **Step 1: 写迁移**

```sql
-- 461: harness 第四轮 · 二期 2b-2 —— 编排（子代理 / 定时唤醒 / workforce 接活）
-- spec: docs/superpowers/specs/2026-09-10-harness-p4-phase2b2-orchestration-design.md §6
-- 五件事（a-e 见下方分节），消费代码在后续 Task。
-- 照 459/460 的 DROP/ADD 幂等写法；不 SET ROLE（以连接角色 postgres 跑）。
BEGIN;

-- (a) 事件白名单 ---------------------------------------------------
ALTER TABLE public.agent_run_transcript_events
  DROP CONSTRAINT IF EXISTS agent_run_transcript_events_event_type_check;
ALTER TABLE public.agent_run_transcript_events
  ADD CONSTRAINT agent_run_transcript_events_event_type_check
  CHECK (event_type = ANY (ARRAY[
    'user'::text, 'assistant'::text, 'tool_call'::text, 'error'::text, 'system'::text,
    'llm_retry'::text, 'todo_write'::text,
    'compaction_start'::text, 'compaction_summary'::text, 'compaction_end'::text,
    'turn_end'::text,
    'step_start'::text, 'step_end'::text, 'inbox_claimed'::text,
    'deliverable'::text, 'budget_check'::text,
    'question_asked'::text, 'question_answered'::text,
    'capability_denied'::text,
    'fork'::text,
    'subagent_spawned'::text, 'subagent_done'::text, 'schedule_set'::text
  ]));
COMMENT ON CONSTRAINT agent_run_transcript_events_event_type_check
  ON public.agent_run_transcript_events IS
  'Allowed transcript event types. 436/443/453/459 as before. 460: fork. '
  '461: subagent_spawned / subagent_done (written on the PARENT run, sync or '
  'async), schedule_set (agent armed a one-time issue wake-up) — phase 2b-2.';

-- (b) 收件箱 kind -------------------------------------------------
ALTER TABLE public.agent_run_inbox
  DROP CONSTRAINT IF EXISTS agent_run_inbox_kind_check;
ALTER TABLE public.agent_run_inbox
  ADD CONSTRAINT agent_run_inbox_kind_check
  CHECK (kind = ANY (ARRAY[
    'steer'::text, 'answer'::text, 'pause'::text, 'resume'::text,
    'budget_reply'::text,
    'subagent_result'::text
  ]));
COMMENT ON CONSTRAINT agent_run_inbox_kind_check ON public.agent_run_inbox IS
  '453: steer/answer/pause/resume/budget_reply. 461: subagent_result — a background '
  'sub-agent delivers its envelope to the parent through the ONE queue.';

-- (c) cron_expr 可空 ----------------------------------------------
ALTER TABLE public.user_schedules ALTER COLUMN cron_expr DROP NOT NULL;
ALTER TABLE public.user_schedules
  DROP CONSTRAINT IF EXISTS user_schedules_cron_or_once;
ALTER TABLE public.user_schedules
  ADD CONSTRAINT user_schedules_cron_or_once
  CHECK (
    cron_expr IS NOT NULL
    OR (task_type = 'issue_wakeup' AND (payload->>'once') = 'true')
  );
COMMENT ON COLUMN public.user_schedules.cron_expr IS
  '5-field UTC cron. NULLABLE since 461: a one-time issue_wakeup gives next_fire_at '
  'directly and self-disables after firing. The cron_or_once CHECK keeps NULL out of '
  'every recurring task_type, where it would silently never re-arm.';

-- (d) 白名单曾允许这两种，但引擎注册表从不认：到点每分钟 skip 一次，永不执行。
--     禁掉存量行；T5 把白名单改成从注册表导出。
UPDATE public.user_schedules
   SET enabled = false, pause_reason = 'task_type_unsupported'
 WHERE task_type IN ('ai_transcription', 'ai_visual_analysis')
   AND enabled;

-- (e) mig 200 (A4) 第 234 行承诺的 DROP。仓库层只读写 task_tracking，零 SELECT
-- 命中此表；两个自引用 FK 随表消失，无外部依赖，不需要 CASCADE。
DROP TABLE IF EXISTS public.agent_tasks;

COMMIT;
```

- [ ] **Step 2: 源码守卫测试（红）** — `backend/tests/db/test_migration_461_orchestration.py`

```python
"""461 must keep every 460 literal, admit the three orchestration events and
subagent_result, free cron_expr behind a CHECK, and actually drop agent_tasks.
A DROP/ADD that forgets one literal silently rejects that family's inserts."""

import pathlib
import re

import pytest

from tests.models.test_transcript_event_types_phase2a import _literals

pytestmark = pytest.mark.unit
MIG = pathlib.Path(__file__).resolve().parents[3] / "supabase/migrations"
BODY = (MIG / "461_harness_p4_phase2b2_orchestration.sql").read_text(encoding="utf-8")
_ARRAYS = re.compile(r"ARRAY\[(.*?)\]", re.DOTALL)
_LITERAL = re.compile(r"'([a-z_]+)'::text")


def _array(index: int) -> frozenset[str]:
    found = _LITERAL.findall(_ARRAYS.findall(BODY)[index])
    assert len(found) == len(set(found)), "duplicate literal"
    return frozenset(found)


def test_461_is_a_superset_of_460_plus_the_orchestration_events():
    prev = _literals((MIG / "460_transcript_event_type_fork.sql").read_text())
    cur = _array(0)
    assert {"subagent_spawned", "subagent_done", "schedule_set"} <= cur
    assert prev <= cur, prev - cur


def test_461_admits_subagent_result_and_keeps_the_453_inbox_kinds():
    kinds = _array(1)
    assert "subagent_result" in kinds
    assert {"steer", "answer", "pause", "resume", "budget_reply"} <= kinds


def test_461_frees_cron_disables_dead_task_types_and_drops_agent_tasks():
    for needle in (
        "ALTER COLUMN cron_expr DROP NOT NULL",
        "user_schedules_cron_or_once",
        "task_type = 'issue_wakeup'",
        "pause_reason = 'task_type_unsupported'",
        "DROP TABLE IF EXISTS public.agent_tasks",
    ):
        assert needle in BODY, needle
    assert not any(
        re.match(r"(?i)^SET\s+ROLE\b", line.strip()) for line in BODY.splitlines()
    ), "migrations must not SET ROLE (see CLAUDE.md)"
```

同一文件再加一个 `test_inbox_kind_orm_literal_matches_the_migration_exactly`：从 `app.models.agents` 取 `agent_run_inbox_kind_check` 这个 `CheckConstraint`，断言 `_literals(str(check.sqltext)) == _array(1)`。**没有别的东西绑住这两份清单**——schema-drift 只比列不比 CHECK 体，事件类型的镜像测试只管另一张表。⚠️ 类名先 `grep -n "agent_run_inbox" backend/app/models/agents.py` 核实。

- [ ] **Step 3: 跑红** `cd backend && uv run pytest -q tests/db/test_migration_461_orchestration.py`——先写测试再写 SQL，断言失败即红；补上 Step 1 的 SQL 后除 ORM 镜像那条外全绿。

- [ ] **Step 4: 改 ORM 镜像**（`backend/app/models/agents.py`）：事件 CHECK 字面量末尾追加 `" 'subagent_spawned'::text, 'subagent_done'::text, 'schedule_set'::text])"`（注意原来 `'fork'::text])` 那行的 `])` 要移到新行末）；inbox kind CHECK 追加 `" 'subagent_result'::text])"`。

- [ ] **Step 5: 删 `AgentTasks` 模型**：删 `agents.py:568-670` 整个类，删 `models/__init__.py:43` 的导入与 `:283` 的 `"AgentTasks",`。`grep -rn "AgentTasks" backend/ --include="*.py" | grep -v .venv` 必须只剩零行。

- [ ] **Step 6: 修 `LATEST_MIGRATION`** — `backend/tests/models/test_transcript_event_types_phase2a.py:33`：

```python
# The allowlist is re-declared whole by each migration that touches it; the
# ORM literal must equal the LATEST one (461 admitted the phase-2b-2
# orchestration events).
LATEST_MIGRATION = MIGRATION.parent / "461_harness_p4_phase2b2_orchestration.sql"
```

⚠️ 该文件的 `_literals` 断言「恰好一个 `ARRAY[...]`」，而 461 有两个（事件 + inbox kind）。把 `_literals` 改成接受可选下标会污染 460 的调用方；**改法是给 `LATEST_MIGRATION` 单独切片**：在该文件加一个 `_first_array(sql)` 只取第一个 ARRAY 再复用 `_LITERAL`，`test_migration_and_orm_event_type_sets_are_identical` 用它。这是本 Task 唯一一处改既有测试助手，PR 里写明。

- [ ] **Step 7: 跑绿 + lint**

```bash
cd backend && uv run pytest -q tests/db/test_migration_461_orchestration.py \
  tests/models/test_transcript_event_types_phase2a.py tests/db/test_schema_drift.py
uv run isort app/models/agents.py app/models/__init__.py tests/db/test_migration_461_orchestration.py
uv run black <同上> && uv run ruff check <同上>
```

预期：全绿。schema-drift 需 `INTEGRATION_DATABASE_URL` 才真连库，本机没有会 skip——**skip 不是通过**，PR 描述里写清它在 CI 的 `schema-drift.yml` 上才有效。

- [ ] **Step 8: spec 回写** — 把上面「六处偏差」逐条写进 spec §0 表格下方的实施记录、§1 第 4 条（`agent_tasks` 部分）与 §6 表格的 mig 461 行。

- [ ] **Step 9: 突变** — 把迁移里 `'fork'::text,` 那行删掉 → `test_461_is_a_superset_of_460_plus_the_orchestration_events` 红（`prev - cur == {'fork'}`）且 `test_migration_and_orm_event_type_sets_are_identical` 红；还原。

- [ ] **Step 10: Commit**

```bash
git add supabase/migrations/461_harness_p4_phase2b2_orchestration.sql \
  backend/app/models backend/tests docs/superpowers/specs
git commit -m "feat(db): 迁移 461 编排类型放行 + cron_expr 可空 + DROP agent_tasks（harness 二期 2b-2 Task 1）"
```

---

### Task 2: 派发窗口守卫 + `agent_runs.issue_id` 创建时写入与回填

两张小票，一个 PR（都只碰 issue 派发这条缝，分开开 PR 反而互相 rebase）。

**Files:**
- Modify: `backend/app/services/issues/issue_dispatch.py`（全文 47 行；加 `is_dispatching` + 派发前后写标记）
- Modify: `backend/app/workflows/issue_lifecycle.py:47-67`（`atomic_checkout` 同一 UPDATE 清标记）
- Modify: `backend/app/services/issues/issue_fork.py:154-168`（`issue_busy` 判定加 `dispatching`）
- Modify: `backend/app/api/issues_router.py:631-645`（resume 的 `execution_locked_at` 分支前加 `dispatching` 判定）
- Modify: `backend/app/api/issue_messages_router.py:556-596`（`_divert_to_inbox_if_running` 加 `dispatching` 分支）
- Modify: `backend/app/services/ai/chat/ai_library_chat_service.py:552-591`（`run_session_turn` 签名）、`:593-620`（`_run_session_turn_inner` 签名）、`:1331-1346`（`RunRecorder(issue_id=...)`）
- Modify: `backend/app/services/issues/issue_agent_executor.py:207-215`（传 `issue_id`）
- Modify: `backend/app/services/ai/runner/subagent_task_service.py:350-368`（`RunRecorder(issue_id=self.issue_id)`，继承父 run）
- Create: `backend/app/workflows/backfill_agent_runs_issue_id.py`
- Modify: `backend/app/api/admin/backfill_router.py:24-60`（`_BACKFILLS` 加一项）
- Test: `backend/tests/services/issues/test_dispatch_window_guard.py`（新）、`backend/tests/test_backfill_agent_runs_issue_id.py`（新）、`backend/tests/services/ai/test_run_session_turn_issue_id.py`（新）

**Interfaces:**
- Produces：`issue_dispatch.is_dispatching(issue: dict, *, now: datetime | None = None, ttl_s: int = 60) -> bool`；`AILibraryChatService.run_session_turn(..., issue_id: int | None = None)` → `RunRecorder(issue_id=)`；`_BACKFILLS["agent_runs_issue_id"]`。
- Consumes：`execution_state.merge_execution_state(issue_id, patch)`（已有）；`agent_runs_repository.backfill_issue_id`（保留为兜底，不删）。

**不需要「adapter 无 `stream`」用例**：本 Task 没有给 `run_turn` 结果或工具结果加任何新标志——`issue_id` 只经构造参数流向 DB 行，不进 turn 结果字典。PR 里显式写这句，免得评审当漏项。

- [ ] **Step 1: 守卫测试（红）** — `backend/tests/services/issues/test_dispatch_window_guard.py`

```python
"""The dispatch window: _dispatch_execute_issue returns before atomic_checkout
runs, so between them running_root_run_id AND execution_locked_at are both
empty. A fork in that window swings ai_session_id away and the workflow's own
checkout then silently returns {"skipped": True}. The marker closes it."""

from datetime import datetime, timedelta, timezone

import pytest

from app.services.issues import issue_dispatch

pytestmark = pytest.mark.unit


def _issue(at):
    return {"execution_state": {"dispatching": {"workflow_id": "wf-1", "at": at}}}


def test_a_fresh_marker_means_busy():
    now = datetime.now(timezone.utc)
    assert issue_dispatch.is_dispatching(_issue(now.isoformat()), now=now) is True


def test_stale_null_and_malformed_markers_are_all_not_busy():
    """A crashed dispatch must not wedge the issue forever, and an unparseable
    marker is not a guard we can trust."""
    now = datetime.now(timezone.utc)
    assert issue_dispatch.is_dispatching(
        _issue((now - timedelta(seconds=61)).isoformat()), now=now
    ) is False
    for state in ({}, {"dispatching": None}, {"dispatching": {}},
                  {"dispatching": {"at": "not-a-time"}}):
        assert issue_dispatch.is_dispatching({"execution_state": state}, now=now) is False


@pytest.mark.asyncio
async def test_start_execute_issue_marks_before_dispatch_and_clears_on_failure(monkeypatch):
    calls = []

    async def _merge(issue_id, patch):
        calls.append(patch)

    monkeypatch.setattr(issue_dispatch, "merge_execution_state", _merge)
    import app.api.issues_router as router_mod

    def _boom(issue_id, workflow_id):
        raise RuntimeError("dbos is down")

    monkeypatch.setattr(router_mod, "_dispatch_execute_issue", _boom)
    with pytest.raises(issue_dispatch.DispatchFailed):
        await issue_dispatch.start_execute_issue(7)
    assert list(calls[0]["dispatching"]) == ["workflow_id", "at"]
    assert calls[1] == {"dispatching": None}   # cleared, not left to rot for 60s
```

- [ ] **Step 2: 实现 `is_dispatching` + 标记写入**（`issue_dispatch.py`）

```python
# 派发标记的有效期。execute_issue 起来后第一件事就是 atomic_checkout，正常在
# 毫秒级；60s 是「workflow 根本没起来」的判定线，与收割器同款——过期标记按不
# 存在处理，绝不让一次失败的派发把 issue 永久钉成 busy。
DISPATCH_MARKER_TTL_S = 60


def is_dispatching(
    issue: dict, *, now: datetime | None = None, ttl_s: int = DISPATCH_MARKER_TTL_S
) -> bool:
    """True while a dispatch is in flight but the workflow has not checked out.

    ``_dispatch_execute_issue`` returns as soon as DBOS accepts the enqueue, but
    ``execution_locked_at`` is written by ``atomic_checkout`` INSIDE the
    workflow. In between there is no run row and no lock — every pre-existing
    busy check reads idle."""
    marker = (issue.get("execution_state") or {}).get("dispatching") or {}
    at = marker.get("at")
    if not at:
        return False
    try:
        stamped = datetime.fromisoformat(str(at))
    except ValueError:
        return False  # unparseable = not a guard we can trust; never wedge on it
    if stamped.tzinfo is None:
        stamped = stamped.replace(tzinfo=timezone.utc)
    return (now or datetime.now(timezone.utc)) - stamped < timedelta(seconds=ttl_s)
```

`start_execute_issue` 改成：生成 `workflow_id` 后先
`await merge_execution_state(issue_id, {"dispatching": {"workflow_id": workflow_id, "at": datetime.now(timezone.utc).isoformat()}})`，再 `try: _dispatch_execute_issue(...)`；`except` 分支在 `raise DispatchFailed` **之前**
`await merge_execution_state(issue_id, {"dispatching": None})`（软成功的 duplicate 分支不清，workflow 会来清）。

⚠️ `merge_execution_state` 是 `||` 合并，写 `None` 得到的是 JSON `null` 而不是删键——所以 `is_dispatching` 必须把 `null` 读成不忙（Step 1 已有该用例）。真正的删键在 `atomic_checkout`。

- [ ] **Step 3: `atomic_checkout` 同一 UPDATE 清标记**（`issue_lifecycle.py:55-64`）。删键的写法照 `set_status:135-140` 的既有形态（显式 `cast`，否则无类型 bind 撞 jsonb `-` 的重载歧义）：

```python
    from sqlalchemy import Text, cast, func, literal, text, update
    from sqlalchemy.dialects.postgresql import JSONB
    ...
        result = await session.execute(
            update(Issues)
            .where(Issues.id == issue_id, Issues.execution_locked_at.is_(None))
            .values(
                execution_locked_at=func.now(),
                dbos_workflow_id=dbos_workflow_id,
                # The dispatch window closes HERE, in the same UPDATE that opens
                # the lock — a separate write could be interleaved by the very
                # fork this marker exists to stop.
                execution_state=func.coalesce(
                    Issues.execution_state, cast(literal("{}"), JSONB)
                ).op("-", return_type=JSONB)(cast(literal("dispatching"), Text)),
            )
        )
```

配套测试追加到既有 `backend/tests/test_issue_lifecycle_sql.py`（该文件已有 `test_atomic_checkout_updates_with_lock_guard` 的编译断言范式）：断言编译出的 SQL 同时含 `execution_locked_at` 与 `- ` 的 jsonb 删键片段。

- [ ] **Step 4: 三个读方接线**（各配一个测试，与 Step 1 同文件）
  - `issue_fork.py`：`if issue.get("execution_locked_at"):` 那段 **之前**插入
    `if is_dispatching(issue): raise ForkRejected("issue_busy", 409, "a dispatch is in flight")`。
  - `issues_router.py` resume：`if existing.get("execution_locked_at"):` 之前插入同判定，抛
    `HTTPException(409, detail={"code": "issue_busy", "message": "a dispatch is in flight"})`。
  - ⚠️ 生产错误体是 `ErrorResponse` 外壳（`app/core/exceptions.py`），类型化码落在 `details.code` 而不是 `detail`。给 fork/resume 的 `issue_busy` 写测试时贴真栈原样响应体，别用 FastAPI 裸 `{detail}`——那样单测全绿而线上每个拒绝都退化成 `http_409`。
  - `issue_messages_router._divert_to_inbox_if_running`：签名加 `issue_row: dict`，把
    `if running is None and not paused:` 改成 `if running is None and not paused and not is_dispatching(issue_row):`，`why` 串补 `"dispatch in flight"` 分支。调用点把已加载的 issue 行传进来（该函数的调用方本来就持有它，不要再查一次库）。

- [ ] **Step 5: 跑绿** `cd backend && uv run pytest -q tests/services/issues/test_dispatch_window_guard.py tests/test_issue_lifecycle_sql.py tests/api/test_issue_fork*.py tests/api/test_issue_messages*.py`

- [ ] **Step 6: `issue_id` 透传测试（红）** — `backend/tests/services/ai/test_run_session_turn_issue_id.py`

```python
"""agent_runs.issue_id must be set AT CREATION, not backfilled after the turn.
backfill_issue_id runs from issue_lifecycle only after a turn RETURNS — a
crash, a cancel, or an empty-output turn leaves the row NULL forever, and
issue_fork then has to reverse-derive the issue from conversation_id."""

import inspect

import pytest

pytestmark = pytest.mark.unit


def test_run_session_turn_accepts_and_forwards_issue_id():
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService

    for fn in (
        AILibraryChatService.run_session_turn,
        AILibraryChatService._run_session_turn_inner,
    ):
        p = inspect.signature(fn).parameters["issue_id"]
        assert p.default is None and p.kind is inspect.Parameter.KEYWORD_ONLY


def test_the_recorder_and_the_issue_executor_are_actually_wired():
    """Signature alone proves nothing — the value has to reach RunRecorder and
    the issue path has to supply it."""
    import pathlib

    chat = pathlib.Path("app/services/ai/chat/ai_library_chat_service.py")
    exe = pathlib.Path("app/services/issues/issue_agent_executor.py")
    assert "issue_id=int(issue_id) if issue_id else None," in chat.read_text("utf-8")
    assert "issue_id=iid," in exe.read_text("utf-8")
```

- [ ] **Step 7: 实现透传** — 两个签名各加 `issue_id: Optional[int] = None`（keyword-only），`run_session_turn` 转发给 inner；`RunRecorder(...)` 参数表里 `fork_of_run_id` 上方加
`issue_id=int(issue_id) if issue_id else None,`；`issue_agent_executor.py:207` 的调用加 `issue_id=iid,`；`subagent_task_service.py` 的 `RunRecorder(...)` 加 `issue_id=self.issue_id`（`self.issue_id` 由构造方从父 run 继承——若该属性尚不存在，本 Task 顺带加一个 `issue_id: int | None = None` 构造参数，spec §2.5「子 run 的 issue_id 继承父 run」就是这一处）。

- [ ] **Step 8: 回填 workflow（红→绿）** — `backend/app/workflows/backfill_agent_runs_issue_id.py`，照 `backfill_issue_scope.py` 的范式（`manager.create/start/complete`、`dry_run=True` 默认、失败先 `patch_metadata` 再 `raise`、`SYSTEM_RUN_USER_ID`）。核心语句是相关子查询，**不用 `text()`**：

```python
async def _one_batch(dry_run: bool, limit: int) -> int:
    """One batch of `agent_runs.issue_id := the issue whose ai_session_id is this
    run's conversation`. Idempotent by construction: the WHERE re-checks
    issue_id IS NULL, so a replayed batch just finds fewer rows."""
    issue_for_run = (
        select(Issues.id)
        .where(Issues.ai_session_id == AgentRuns.conversation_id)
        .correlate(AgentRuns)
        .limit(1)
        .scalar_subquery()
    )
    candidates = (
        select(AgentRuns.id)
        .where(AgentRuns.issue_id.is_(None), AgentRuns.conversation_id.isnot(None))
        .where(issue_for_run.isnot(None))
        .order_by(AgentRuns.id)
        .limit(limit)
    )
    if dry_run:
        async with read_scope() as session:
            return len((await session.execute(candidates)).scalars().all())
    async with write_scope() as session:
        result = await session.execute(
            update(AgentRuns)
            .where(AgentRuns.id.in_(candidates))
            .where(AgentRuns.issue_id.is_(None))
            .values(issue_id=issue_for_run)
        )
        return result.rowcount or 0
```

测试（`tests/test_backfill_agent_runs_issue_id.py`）用编译断言：`str(stmt.compile(dialect=postgresql.dialect()))` 里必须同时出现 `issue_id IS NULL` 与 `ai_session_id`，且 **不含** `text(` 痕迹；再用假 session 跑一遍 `dry_run=True` 断言零 `write_scope` 调用。

- [ ] **Step 9: 注册** — `backfill_router.py` 顶部 import + `_BACKFILLS` 加 `"agent_runs_issue_id": backfill_agent_runs_issue_id_workflow,`。

- [ ] **Step 10: 跑绿 + lint** `uv run pytest -q tests/services/issues/test_dispatch_window_guard.py tests/services/ai/test_run_session_turn_issue_id.py tests/test_backfill_agent_runs_issue_id.py tests/api/test_admin_backfill*.py`；isort/black/ruff 全部改动文件。

- [ ] **Step 11: 突变** — 去掉 `atomic_checkout` 里的 jsonb 删键 → `test_issue_lifecycle_sql` 的编译断言红；再把 `is_dispatching` 的 TTL 比较改成 `<=` 且删掉 `ValueError` 分支 → `test_absent_null_and_malformed_markers_are_all_not_busy` 红。两处都还原。

- [ ] **Step 12: Commit**

```bash
git add backend/app backend/tests
git commit -m "fix(issues): 派发窗口 dispatching 守卫 + agent_runs.issue_id 创建时写入与回填（harness 二期 2b-2 Task 2）"
```

---

### Task 3: 接活 workforce 执行链（四条断线 + CAS + 删死组件 + 特性开关迁 settings）

**Files:**
- Modify: `backend/app/workflows/workforce_dispatch.py:66-78`（step 只整形并返回订单）、`:110-122`（workflow body 派发）
- Modify: `backend/app/repositories/agent_workforce_repository.py:654-688`（删 `claim_next_queued`，加 `claim_task` / `list_undispatched_queued_tasks` / `mark_dispatched` / `count_inflight_agent_tasks`）
- Modify: `backend/app/services/workforce/dbos_pool.py:44-50,95,115-121`（删 `_inflight_estimate`，`inflight_count` 改 async 派生）
- Modify: `backend/app/services/workforce/agent_worker.py:1-45`（docstring：CAS 事实 + 路线 C 例外段）、`:106-118`（用 `claim_task` 替代读后判）
- Modify: `backend/app/workflows/agent_workforce.py:9-11,39-42`（两处 `claim_next_queued` docstring 改真）
- Modify: `backend/app/services/workforce/delegate_feature.py`（读 settings）、`backend/app/core/config.py`（加字段）、`backend/config.yml`（加键）
- Delete: `backend/app/services/workforce/scheduler.py`、`backend/app/services/workforce/worker_pool.py`、`backend/tests/test_workforce_scheduler.py`、`backend/tests/test_worker_pool.py`
- Test: `backend/tests/workflows/test_workforce_chain.py`（新）；改 `backend/tests/test_dbos_workforce_pool.py`（`inflight_count` 三处断言）

**Interfaces（后续 Task 依赖）:**
- `AgentWorkforceRepository.claim_task(task_id: str) -> dict | None`（CAS `phase='queued' → 'assigned'`，返回 agent_tasks 风格 shape，抢输返回 None）
- `AgentWorkforceRepository.list_undispatched_queued_tasks(limit: int = 100) -> list[dict]`
- `AgentWorkforceRepository.mark_dispatched(task_id: str) -> None`
- `AgentWorkforceRepository.count_inflight_agent_tasks() -> int`
- `settings.FEATURE_WORKFORCE_DELEGATE: bool`（`delegate_feature_enabled()` 函数名不变，composer / delegate_tool 零改动）
- `DbosAgentWorkforcePool.inflight_count` 变成 **async 方法**（不再是 property）

⚠️ 列名：task_tracking 的生命周期列是 **`phase`**（8 状态原值），`status` 是 5 状态映射，任务 id 是 **`dbos_workflow_id`**（TEXT uuid4），payload/metadata 是 ORM 属性 **`metadata_`**（DB 名 `metadata`）。仓库层 `LIFECYCLE_TO_STATUS` 负责两列同写。

- [ ] **Step 1: 链路测试（红）** — `backend/tests/workflows/test_workforce_chain.py`

```python
"""The workforce chain, end to end with a stubbed DBOS: the @DBOS.step only
SHAPES the queue and returns orders; the workflow BODY dispatches. Enqueuing
from inside a step is route-C forbidden (the empty-string AssertionError of
PR #495) — a source guard pins that, because a stub cannot reproduce it."""

from __future__ import annotations

import inspect
import re

import pytest

from app.workflows import workforce_dispatch as wd

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_step_returns_the_undispatched_queue_and_does_not_dispatch(monkeypatch):
    class _Proc:
        async def tick(self):
            return {"agents_processed": 1, "tasks_created": 1, "errors": 0}

    rows = [{"id": "t-1", "agent_id": "a-1"}, {"id": "t-2", "agent_id": "a-1"}]

    async def _list():
        return rows

    monkeypatch.setattr(wd, "_inbox_processor", lambda: _Proc())
    monkeypatch.setattr(wd, "_list_undispatched", _list)
    out = await wd.inbox_dispatch_tick_step()
    assert out["tasks_created"] == 1 and out["orders"] == rows


@pytest.mark.asyncio
async def test_workflow_body_dispatches_each_order_then_marks_it(monkeypatch):
    dispatched, marked = [], []

    async def _tick():
        return {"tasks_created": 1, "errors": 0,
                "orders": [{"id": "t-1", "agent_id": "a-1"}]}

    class _Pool:
        async def dispatch(self, task):
            dispatched.append(task)

    monkeypatch.setattr(wd, "inbox_dispatch_tick_step", _tick)
    monkeypatch.setattr(wd, "_pool", lambda: _Pool())
    monkeypatch.setattr(wd, "_mark_dispatched", lambda tid: marked.append(tid))
    await wd.inbox_dispatch_workflow(None, None)
    assert [t["id"] for t in dispatched] == ["t-1"]
    assert marked == ["t-1"]


def test_no_dispatch_call_inside_any_step_body():
    """Source guard: a stub cannot reproduce DBOS's in-step enqueue assertion."""
    bodies = re.findall(
        r"@DBOS\.step\(\)\s*\nasync def \w+\(.*?\n(.*?)(?=\n@|\Z)",
        inspect.getsource(wd),
        re.DOTALL,
    )
    assert bodies, "no @DBOS.step bodies found — the guard itself is broken"
    for body in bodies:
        assert "dispatch(" not in body, body[:200]
```

（`_inbox_processor` / `_pool` / `_list_undispatched` / `_mark_dispatched` 是本 Task 新加的模块级间接层——不这么切，测试只能 patch 深处的仓库单例。）

- [ ] **Step 2: 仓库四个方法（红→绿）**，追加到 `agent_workforce_repository.py`，删掉 `claim_next_queued` 整段：

```python
    async def claim_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """CAS claim by id: queued → assigned, or None if someone got there first.

        Replaces the by-agent ``claim_next_queued`` (zero production callers —
        the dedup that actually ran was ``run_one_task``'s read-then-check,
        which is not atomic). The dispatcher already picked WHICH task; the only
        question left is whether this worker owns it."""
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            async with write_scope() as session:
                updated = await session.execute(
                    sa_update(TaskTracking)
                    .where(TaskTracking.dbos_workflow_id == str(task_id))
                    .where(TaskTracking.task_kind == TASK_KIND_AGENT)
                    .where(TaskTracking.phase == "queued")  # the CAS
                    .values(
                        phase="assigned",
                        status=LIFECYCLE_TO_STATUS["assigned"],
                        metadata_=TaskTracking.metadata_.op("||", return_type=JSONB)(
                            cast(literal(json.dumps({"assigned_at": now_iso})), JSONB)
                        ),
                    )
                    .returning(TaskTracking)
                )
                return _task_shape(updated.scalars().first())
        except Exception as e:
            logger.exception(f"Failed to claim task {task_id}: {e}")
            return None

    async def list_undispatched_queued_tasks(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Queued agent_tasks nobody has enqueued yet — the dispatch tick's work
        list. Not just this tick's new rows: a task created outside the inbox
        path (a background sub-agent, phase 2b-2 §2.2) is picked up here too."""
        async with read_scope() as session:
            rows = (
                await session.execute(
                    select(TaskTracking)
                    .where(TaskTracking.task_kind == TASK_KIND_AGENT)
                    .where(TaskTracking.phase == "queued")
                    .where(
                        TaskTracking.metadata_.op("->>", return_type=Text)(
                            cast(literal("dispatched_at"), Text)
                        ).is_(None)
                    )
                    .order_by(TaskTracking.created_at.asc())
                    .limit(limit)
                )
            ).scalars().all()
        return [s for s in (_task_shape(r) for r in rows) if s]

```

另两个照同样形状写，不再贴全：`mark_dispatched(task_id)` 用 `sa_update(...).values(metadata_= 既有 || {"dispatched_at": iso})`，docstring 写明「best effort：DBOS 以 `workforce-<task_id>` 固定 workflow id，丢一次戳只多一次被去重的 enqueue，不会双跑」；`count_inflight_agent_tasks()` 是 `select(func.count()).select_from(TaskTracking).where(task_kind==..., phase.in_(("queued","in_progress")))`，docstring 写明「派生而非进程内计数——旧估算只增不减」。

对应测试用编译断言（照 `tests/test_orm_b3_task1_compile_coverage.py` 的范式）：`claim_task` 的 SQL 必须含 `phase = ` 的 WHERE **和** `RETURNING`；`list_undispatched_queued_tasks` 必须含 `->>` 与 `IS NULL`。

- [ ] **Step 3: 改 `workforce_dispatch.py`**：`inbox_dispatch_tick_step` 在 `processor.tick()` 之后
`stats["orders"] = await _list_undispatched()` 并返回；`inbox_dispatch_workflow` 在拿到 result 之后

```python
    orders = result.get("orders") or []
    pool = _pool()
    for order in orders:
        try:
            await pool.dispatch(order)
            await _mark_dispatched(str(order["id"]))
        except Exception as e:  # one bad order never starves the rest
            logger.exception(f"[workforce-dispatch] order {order.get('id')}: {e}")
    created = result.get("tasks_created", 0)
    if created or orders:
        logger.info(f"[workforce-dispatch] inbox created={created} dispatched={len(orders)}")
```

⚠️ 顺手修偏差 5：原日志读的 `tasks_enqueued` 键从不存在，那行 info 一次都没打过。

- [ ] **Step 4: `inflight_count` 派生** — `dbos_pool.py` 删 `self._inflight_estimate` 与 `+= 1`，把 property 换成

```python
    async def inflight_count(self) -> int:
        """Derived from task_tracking, not counted in this process. The old
        estimate only ever incremented (nothing decremented on terminal), so it
        was a monotonically rising number wearing a gauge's name."""
        from app.repositories.agent_workforce_repository import (
            get_agent_workforce_repository,
        )

        return await get_agent_workforce_repository().count_inflight_agent_tasks()
```

`tests/test_dbos_workforce_pool.py` 五处 `pool.inflight_count == N` 改成 `await pool.inflight_count()` 并 patch 仓库；`test_inflight_count_property_returns_estimate` 整个删掉（它测的正是被删的估算器），换成一个「派生自仓库计数」的用例。**没有 healthz 调用方要改**（勘察偏差 6）。

- [ ] **Step 5: `run_one_task` 用 CAS**（`agent_worker.py:106-118`）：把 `fresh = await workforce.get_task(...)` 那段读后判换成

```python
    claimed = await workforce.claim_task(str(task_id))
    if claimed is None:
        logger.info(f"[agent-worker] task {task_id} not claimable (already taken)")
        return {"task_id": str(task_id), "status": "skipped", "reason": "not_claimable"}
    task = {**task, **claimed}
```

三处 docstring 改真：`agent_worker.py:13` 的生命周期图 `claim_next_queued` → `claim_task`；`agent_workforce.py:9-11` 与 `:39-42` 两处 `AgentWorkforceRepository.claim_next_queued` → `claim_task`。（`worker_pool.py:11` 那处随文件一起删。）

- [ ] **Step 6: `agent_worker.py` 模块 docstring 加路线 C 例外段**（顶部「Failure semantics」之后）：

```
## Why this module PATCHes phase/status directly (route C rule 2 exception)

Route C rule 2 forbids business code from writing task_tracking's lifecycle
columns — ``mirror_dbos_lifecycle_to_tracking`` owns them. It cannot own THESE
rows: a workforce task's DBOS workflow id is ``workforce-<task_id>``, while
``task_tracking.dbos_workflow_id`` holds the application-level uuid4 the
repository minted at create time. They never match, the trigger's join finds
nothing, and the row would sit at ``queued`` for the whole run. These writes
are deliberate, not drift. Changing the workflow-id scheme re-opens the
decision: matching ids would hand phase back to the trigger, and these
UPDATEs would start fighting it.
```

- [ ] **Step 7: 特性开关迁 settings** — `config.py` 照 `FEATURE_SHOT_VIDEO:146-165` 的形态加：

```python
    FEATURE_WORKFORCE_DELEGATE: bool = Field(
        default=False,
        description="Advertise + allow the Workforce Delegate tool. Off (default) = "
        "not composed into the system prompt AND execute() fail-closes. Was a bare "
        "os.getenv until phase 2b-2; the chain it waited on (scheduled tick → "
        "workflow-body dispatch → agent_workforce_workflow → claim_task) is wired as "
        "of T3. Flip true only after the T7 real-stack run — a truthy flag over a "
        "broken chain returns status='queued' for work that never runs.",
    )
```

`config.yml` 加一行 `FEATURE_WORKFORCE_DELEGATE: false`；`delegate_feature.py` 的 `delegate_feature_enabled()` 改成 `return bool(settings.FEATURE_WORKFORCE_DELEGATE)`，删 `os` / `_TRUTHY`，并把文件顶部那段「四条未接线」docstring 改写成「四条已接线（见 2b-2 T3），剩下的只是真栈验证」。**函数名不变**，`delegate_tool.py:140` 与 `prompt_composer.py:437` 零改动。

- [ ] **Step 8: 删死组件**

```bash
cd backend && git rm app/services/workforce/scheduler.py app/services/workforce/worker_pool.py \
  tests/test_workforce_scheduler.py tests/test_worker_pool.py
grep -rn "worker_pool\|WorkforceScheduler\|AgentWorkerPool" app tests --include="*.py"
```

预期：只剩注释/docstring 里的历史叙述（`main.py:78`、`lifespan_helpers.py:20`、`agent_workforce.py` 的对比段）——把这些改成过去式，别留「可以 swap pool」的假承诺。`services/workforce/__init__.py` 未导出这两个模块，无需改。

- [ ] **Step 9: 跑绿 + lint**

```bash
cd backend && uv run pytest -q tests/workflows/test_workforce_chain.py \
  tests/test_dbos_workforce_pool.py tests/test_workforce_inbox_outbox.py \
  tests/services/workforce tests/test_agent_worker*.py
uv run isort <改动文件> && uv run black <改动文件> && uv run ruff check <改动文件>
```

再跑一遍后端全量（Global Constraints 的命令），确认删文件没打断别的 import。

- [ ] **Step 10: 突变（两处）** — ① 把 workflow body 的派发循环整段注释掉 → `test_workflow_body_dispatches_each_order_then_marks_it` 红；② 把 `claim_task` 的 `.where(TaskTracking.phase == "queued")` 删掉 → CAS 编译断言红。都还原并记进 PR。

- [ ] **Step 11: Commit**

```bash
git add -A backend
git commit -m "feat(workforce): 接活执行链——step 只整形/body 派发、claim_task CAS、inflight 派生、删死组件、开关迁 settings（harness 二期 2b-2 Task 3）"
```

---

### Task 4: 后台 / 续聊子代理 + 事件与折叠 + `subagent_result` 投递 + `deliver_or_dispatch` 抽取

**Files:**
- Create: `backend/app/services/issues/inbox_or_dispatch.py`
- Create: `backend/app/services/ai/runner/folds/subagents.py`
- Modify: `backend/app/services/ai/runner/subagent_task_service.py`（`_spawn` 头部加 `await` / `child_run_id` 分支；新增 `_spawn_async`、`_continue_messages`、`_assert_own_child`、`run_background_task`；`ENVELOPE_KEYS` 说明）
- Modify: `backend/app/services/ai/prompts/prompt_composer.py:565-595`（`Skill` schema 的 properties 加 `await` / `child_run_id`）
- Modify: `backend/app/services/workforce/agent_worker.py:73-120`（`run_one_task` 在 claim 之后、`agent.persistent` 门之前分支到子代理）
- Modify: `backend/app/services/ai/runner/run_projection.py:44`（`children` 占位换成五键；底部 import 加 `subagents`）
- Modify: `backend/app/services/ai/runner/inbox.py:60-72`（`render_inbox_message` 给 `subagent_result` 补两个属性）
- Modify: `backend/app/api/issue_messages_router.py:554-600,745-760`（`_divert_to_inbox_if_running` + 派发段改调 `deliver_or_dispatch`）
- Test: `backend/tests/runner/test_subagent_async.py`、`tests/runner/test_subagent_continue.py`、`tests/runner/test_fold_subagents.py`、`tests/services/issues/test_inbox_or_dispatch.py`、`tests/workforce/test_run_one_task_subagent.py`；扩 `tests/test_subagent_task_service.py`

**Interfaces:**
- Consumes（Task 2/3 提供）：`app.services.issues.issue_dispatch.is_dispatching(issue, *, now=None, ttl_s=60) -> bool`；`AgentWorkforceRepository.claim_task(task_id)`；`inbox_dispatch` workflow 会在 ≤10s 内派发任何 `task_kind='agent_task'` 的 queued 未派发行。
- Produces：
  - `SubAgentTaskService.run_background_task(payload: dict) -> dict`（envelope）
  - `deliver_or_dispatch(issue_id: int, *, kind: str, content: dict, user_id: str, message_body: str | None = None, source: dict | None = None, already_enqueued: bool = False) -> DeliverResult`；`@dataclass(frozen=True) DeliverResult(mode: Literal["inbox","dispatched","skipped"], inbox_id: int | None, workflow_id: str | None, reason: str | None)`
  - 空闲分支写的 `issue_messages` 行：`kind='comment'`、`author_user_id=user_id`、`body=message_body`、`meta={"source": source}`。⚠️ **`issue_messages` 没有 `author_kind` 列**（mig 205 建表只有 `kind` / `author_user_id` / `author_agent_id` / `body` / `meta` jsonb），来源只能进 `meta`。`source` 形状 `{"kind": "schedule", "schedule_id": str, "created_by": "user"|"agent"}`；评论路径传 `source=None`，那时 `meta` 里**不写** `source` 键。Part 3 据此判 `meta.source?.kind === "schedule"`
  - 父 run 事件 `subagent_spawned{child_run_id: str|None, task_id: str|None, mode: "sync"|"async", subagent_type: str, description: str, continued_from: str|None}`
  - 父 run 事件 `subagent_done{child_run_id: str, task_id: str|None, mode: str, status: str, cost_cents: float, tokens_used: int, duration_ms: int}`
  - `view.children = {total, done, running, async_pending, last: {child_run_id, subagent_type, status} | None}`；`cost.by_child = {"<child_run_id>": cents}`
  - 收件箱内容 `kind="subagent_result"`，`content = {child_run_id, subagent_type, description, status, summary, cost_cents, tokens_used}`
  - workforce task payload（落在 `task_tracking.metadata->'agent_payload'`）：`{"kind":"subagent","parent_run_id","subagent_type","prompt","description","child_run_id","reply_to":{"target_kind","target_id"},"user_id","agent_depth"}`

**勘察确认（不要再猜）：** 子 run 的历史经 `AgentRunner.run_turn(composed, user_messages=[...], recorder=...)` 传入，参数名是 **`user_messages`**（`subagent_task_service.py:378`）；`create_task(agent_id, user_id, payload, title=...)` 把 payload 塞进 `metadata_["agent_payload"]`，`task_kind` 固定 `agent_task`（`agent_workforce_repository.py:610-640`）；`inbox_message` 框已在 `OWNED_FRAMES`（`frame_markers.py:49`）；`run_one_task` 的 `agent.persistent` 门在 `:136`，子代理分支必须放在它**之前**（子代理通常非 persistent）。

- [ ] **Step 1: 拒绝分支 + payload 形状测试（红）** `tests/runner/test_subagent_async.py`：

```python
"""await=false 的三条拒绝 + 成功时的 task payload 形状。"""
from uuid import uuid4

import pytest

from app.services.ai.runner.subagent_task_service import SubAgentTaskService

pytestmark = pytest.mark.unit


def _svc(**kw):
    base = dict(caller_agent_id=uuid4(), caller_user_id=uuid4(), parent_run_id="900")
    return SubAgentTaskService(**{**base, **kw})


@pytest.mark.parametrize(
    "kw,args,err",
    [
        ({}, {"await": False}, "no_reply_target"),
        ({"agent_depth": 1}, {"await": False}, "async_not_allowed_for_subagent"),
        ({}, {"tasks": [{"subagent_type": "a", "prompt": "b"}], "child_run_id": "5"},
         "continue_not_allowed_in_fanout"),
    ],
)
async def test_typed_rejections(monkeypatch, kw, args, err):
    svc = _svc(**kw)
    monkeypatch.setattr(svc, "_reply_target",
                        lambda: None if err == "no_reply_target" else ("issue", 7))
    out = await svc.spawn({"subagent_type": "librarian", "prompt": "go", **args})
    assert out["status"] == "failed" and out["error"] == err


async def test_async_creates_task_row_and_emits_spawned(monkeypatch):
    created, events = {}, []

    class _Repo:
        @staticmethod
        async def create_task(*, agent_id, user_id, payload, title=None, **kw):
            created.update(payload=payload, title=title)
            return {"id": "task-1"}

    class _Rec:
        run_id = 900
        issue_id = 7

    monkeypatch.setattr(
        "app.repositories.agent_workforce_repository.get_agent_workforce_repository",
        lambda: _Repo())
    monkeypatch.setattr("app.services.ai.runner.events.emit",
                        lambda rec, t, p, **kw: events.append((t, p)))
    svc = _svc(parent_recorder=_Rec())
    monkeypatch.setattr(svc, "_resolve_agent_id", lambda slug: _ok(uuid4()))
    out = await svc._spawn({"subagent_type": "librarian", "prompt": "dig",
                            "description": "d", "await": False})
    assert out["status"] == "queued" and out["task_id"] == "task-1" and out["sub_run_id"] is None
    p = created["payload"]
    assert p["kind"] == "subagent" and p["reply_to"] == {"target_kind": "issue", "target_id": 7}
    assert p["parent_run_id"] == "900" and p["child_run_id"] is None and p["agent_depth"] == 0
    assert events[0][0] == "subagent_spawned"
    assert events[0][1]["mode"] == "async" and events[0][1]["child_run_id"] is None
```
- [ ] **Step 2: 跑红** `cd backend && uv run pytest -q tests/runner/test_subagent_async.py`（`AttributeError: _reply_target`）。

- [ ] **Step 3: 实现异步分支。** `_spawn` 开头（`slug`/`prompt` 解析之后、rate limit 之前）：

```python
        want_await = args.get("await")
        want_await = True if want_await is None else bool(want_await)
        child_run_id = (args.get("child_run_id") or "").strip() or None
        if not want_await:
            return await self._spawn_async(
                slug=slug, prompt=prompt, description=description, child_run_id=child_run_id)
```

`spawn`（并行入口）在 `if args.get("tasks") is not None:` 之前加一句 `if args.get("tasks") is not None and args.get("child_run_id"): return self._failed("continue_not_allowed_in_fanout")`。新方法：

```python
    def _reply_target(self) -> Optional[tuple[str, int]]:
        """后台子代理的结果回哪里。父 run 的 issue 优先，其次会话；两者都无
        （子代理自身、探针 run）→ None，调用方据此拒绝 await=false。"""
        rec = self.parent_recorder
        if getattr(rec, "issue_id", None):
            return ("issue", int(rec.issue_id))
        conv = getattr(rec, "conversation_id", None) or self.session_id
        return ("conversation", int(conv)) if conv else None

    async def _spawn_async(self, *, slug, prompt, description, child_run_id) -> dict[str, Any]:
        if self.agent_depth >= 1:
            return self._failed("async_not_allowed_for_subagent")
        target = self._reply_target()
        if target is None:
            return self._failed("no_reply_target")
        agent_id = await self._resolve_agent_id(slug)
        if agent_id is None:
            return self._failed(f"unknown agent slug: {slug!r}")
        payload = {
            "kind": "subagent",
            "parent_run_id": str(self.parent_run_id) if self.parent_run_id else None,
            "caller_agent_id": str(self.caller_agent_id),
            "subagent_type": slug, "prompt": prompt, "description": description or None,
            "child_run_id": child_run_id,
            "reply_to": {"target_kind": target[0], "target_id": target[1]},
            "user_id": str(self.caller_user_id), "agent_depth": self.agent_depth,
        }
        row = await get_agent_workforce_repository().create_task(
            agent_id=agent_id, user_id=self.caller_user_id, payload=payload,
            title=(description or prompt)[:120])
        if not row:
            return self._failed("task_create_failed")
        task_id = str(row["id"])
        await self._emit_parent("subagent_spawned", {
            "child_run_id": None, "task_id": task_id, "mode": "async",
            "subagent_type": slug, "description": description or "",
            "continued_from": child_run_id})
        return {**self._failed(""), "status": "queued", "task_id": task_id, "error": None}
```

`_emit_parent` 是薄封装（父 recorder 在场就 `events.emit(...)`，不在场 `logger.debug` 后跳过）；`_resolve_agent_id(slug)` 走 `get_agent_repository().get_by_slug`，找不到返回 `None`。**不自己 enqueue** —— issue 的 agent 回合本身跑在 DBOS step 里，在这里 enqueue 就是 step 内派发（spec §2.2 第 2 条）；Task 3 的 `inbox_dispatch` tick 在 ≤10s 内接走。

⚠️ 若 `tests/test_subagent_task_service.py` 把 `ENVELOPE_KEYS` 断成**精确**集合，改成 `set(ENVELOPE_KEYS) <= set(envelope)`，并在该测试里写明 queued envelope 多带 `task_id`。
- [ ] **Step 4: 续聊测试（红）** `tests/runner/test_subagent_continue.py`：

```python
"""child_run_id：父链校验 + 消息重建 + 新子 run 的 fork 列。"""
from uuid import uuid4

import pytest

from app.services.ai.runner.subagent_task_service import SubAgentTaskService

pytestmark = pytest.mark.unit


def _svc(**kw):
    base = dict(caller_agent_id=uuid4(), caller_user_id=uuid4(), parent_run_id="900")
    return SubAgentTaskService(**{**base, **kw})


async def test_foreign_child_is_rejected(monkeypatch):
    svc = _svc()
    monkeypatch.setattr(svc, "_child_chain_ok", lambda cid: False)
    out = await svc._spawn({"subagent_type": "a", "prompt": "more", "child_run_id": "51"})
    assert out["status"] == "failed" and out["error"] == "not_your_child"


async def test_continue_messages_rebuild_history_then_append_prompt(monkeypatch):
    evs = [
        {"seq": 1, "event_type": "user", "payload": {"content": "first"}},
        {"seq": 2, "event_type": "tool_call", "payload": {"tool": "Skill"}},
        {"seq": 3, "event_type": "assistant", "payload": {"content": "answer"}},
        {"seq": 9, "event_type": "assistant", "payload": {"content": "after cut"}},
    ]
    svc = _svc()
    monkeypatch.setattr(svc, "_load_child_events", lambda rid: _ok((evs, 3)))
    msgs, last_seq = await svc._continue_messages("51", "keep going")
    assert last_seq == 3
    assert msgs == [{"role": "user", "content": "first"},
                    {"role": "assistant", "content": "answer"},
                    {"role": "user", "content": "keep going"}]
```

（`_ok` 是一行 `async def _ok(v): return v` 的辅助；`seq 9` 那条证明 `events_upto` 真的按 seq 切，`tool_call` 那条证明它不进历史。）

- [ ] **Step 5: 实现续聊。** `_spawn` 的异步分支之后：

```python
        continue_from: Optional[tuple[list[dict], int]] = None
        if child_run_id is not None:
            if not await self._child_chain_ok(child_run_id):
                return self._failed("not_your_child")
            continue_from = await self._continue_messages(child_run_id, prompt)
```

`RunRecorder(...)` 加 `fork_of_run_id=int(child_run_id) if child_run_id else None, fork_at_seq=continue_from[1] if continue_from else None`，`metadata` 加 `"continued_from": child_run_id, "round": await self._round_of(child_run_id) if child_run_id else 1`；`run_turn` 的 **`user_messages`**（参数名已核实）改成 `continue_from[0] if continue_from else [{"role": "user", "content": prompt}]`。新方法：

```python
    async def _continue_messages(self, child_run_id: str, prompt: str):
        from app.services.ai.runner.replay import events_upto, messages_from_events

        events, last_seq = await self._load_child_events(child_run_id)
        msgs = messages_from_events(events_upto(events, last_seq))
        return ([*msgs, {"role": "user", "content": prompt}], last_seq)
```

`_child_chain_ok(child_run_id)`：从该 run 向上 `select(AgentRuns.parent_run_id, AgentRuns.fork_of_run_id)`，命中 `int(self.parent_run_id)` → True，走满 `MAX_PARENT_HOPS = 10` 或到根 → False（跳数上限与 `delegate_tool._detect_cycle` 同理，防数据环把 walker 卡死）。`_load_child_events` 读 `AgentRunTranscriptEvents` 按 seq 升序，返回 `(rows, max_seq)`；`_round_of` 从被续 run 的 `metadata.round` +1，缺省 2。

- [ ] **Step 6: 同步路径的两个事件。** `RunRecorder` 的 `async with` 体内、`_attach_to_parent_run` 之后 emit `subagent_spawned{mode:"sync", child_run_id: str(recorder.run_id), task_id: None, continued_from: child_run_id}`；`return self._build_envelope(...)` 之前 emit `subagent_done{child_run_id, task_id: None, mode: "sync", status, cost_cents, tokens_used, duration_ms}`（`cost_cents` 取 `getattr(recorder, "cost_cents", 0.0)`，`duration_ms` 用进入 recorder 前记的 `time.monotonic()` 差）。扩 `tests/test_subagent_task_service.py`：断言同步一次 spawn 后父 recorder 上两个事件按 `spawned → done` 顺序、`mode == "sync"`。

- [ ] **Step 7: 折叠测试（红）** `tests/runner/test_fold_subagents.py`：

```python
import pytest

from app.services.ai.runner import run_projection as rp

pytestmark = pytest.mark.unit


def _sp(mode, cid=None, tid=None):
    return {"child_run_id": cid, "task_id": tid, "mode": mode, "subagent_type": "librarian", "description": "d"}


def test_sync_spawn_counts_running_then_done_moves_it():
    v = rp.apply(rp.empty_views(), "subagent_spawned", _sp("sync", cid="51"))
    assert v["view"]["children"] == {"total": 1, "done": 0, "running": 1, "async_pending": 0,
        "last": {"child_run_id": "51", "subagent_type": "librarian", "status": "running"}}
    v = rp.apply(v, "subagent_done", {"child_run_id": "51", "mode": "sync", "status": "success", "cost_cents": 3.0})
    assert v["view"]["children"]["done"] == 1 and v["view"]["children"]["running"] == 0
    assert v["cost"]["by_child"] == {"51": 3.0}


def test_async_spawn_is_pending_until_done():
    v = rp.apply(rp.empty_views(), "subagent_spawned", _sp("async", tid="t1"))
    assert v["view"]["children"]["async_pending"] == 1 and v["view"]["children"]["running"] == 0
    v = rp.apply(v, "subagent_done", {"child_run_id": "52", "task_id": "t1", "mode": "async", "status": "failed"})
    assert v["view"]["children"]["done"] == 1 and v["view"]["children"]["async_pending"] == 0


def test_bad_payload_changes_nothing():
    base = rp.empty_views()
    assert rp.apply(base, "subagent_done", {"mode": "sync"}) is base
```

- [ ] **Step 8: 实现 `folds/subagents.py`** + 注册（docstring 写明：后台完成事件由 worker 以父 run id 补写，父 run 可能早已结束，所以 `done` 必须能在**没有对应 spawned** 的情况下也自洽——`total` 兜底 +1）：

```python
_EMPTY = {"total": 0, "done": 0, "running": 0, "async_pending": 0, "last": None}


def _children(views):
    return {**_EMPTY, **(views["view"].get("children") or {})}


@register("subagent_spawned")
def fold_spawned(views, payload):
    mode = payload.get("mode")
    if mode not in ("sync", "async"):
        return None
    c = _children(views)
    c["total"] += 1
    c["running" if mode == "sync" else "async_pending"] += 1
    cid = payload.get("child_run_id")
    c["last"] = {"child_run_id": str(cid) if cid else None,
                 "subagent_type": str(payload.get("subagent_type") or ""),
                 "status": "running" if mode == "sync" else "queued"}
    views["view"]["children"] = c
    return views


@register("subagent_done")
def fold_done(views, payload):
    cid, mode = payload.get("child_run_id"), payload.get("mode")
    if not cid or mode not in ("sync", "async"):
        return None
    c = _children(views)
    bucket = "running" if mode == "sync" else "async_pending"
    if c[bucket] > 0:
        c[bucket] -= 1
    else:
        c["total"] += 1
    c["done"] += 1
    c["last"] = {"child_run_id": str(cid),
                 "subagent_type": str(payload.get("subagent_type") or (c.get("last") or {}).get("subagent_type") or ""),
                 "status": str(payload.get("status") or "success")}
    views["view"]["children"] = c
    cents = payload.get("cost_cents")
    if isinstance(cents, (int, float)):
        views["cost"]["by_child"] = {**(views["cost"].get("by_child") or {}), str(cid): float(cents)}
        views["cost"]["spent_cents"] = round(float(views["cost"].get("spent_cents") or 0) + float(cents), 4)
    return views
```


`run_projection.empty_views()`：`"children": {"total": 0, "done": 0}` 换成 `{"total": 0, "done": 0, "running": 0, "async_pending": 0, "last": None}`，`"cost"` 里加 `"by_child": {}`；底部 import 列表加 `subagents`。跑 `tests/runner/test_fold_subagents.py tests/runner/test_run_projection*.py tests/runner/test_fold_fork.py`（最后一个钉住「注册类型 ⊆ ORM CHECK」，mig 461 已放行两个新类型）。

- [ ] **Step 9: `deliver_or_dispatch` 测试（红）** `tests/services/issues/test_inbox_or_dispatch.py`：三态各一例——在跑（`running_root_run_id` 非空）→ `mode == "inbox"` 且 `inbox_id` 非空、不派发；派发窗口内（`is_dispatching` 为真）→ 同样进 inbox；空闲 → `mode == "dispatched"`、`workflow_id` 非空，且当 `message_body` 非空时先落一条 `issue_messages`（断言 append 调用早于派发）；issue 终态/不可见 → `mode == "skipped", reason == "issue_terminal"`。

- [ ] **Step 10: 实现 `services/issues/inbox_or_dispatch.py`**（把 `issue_messages_router._divert_to_inbox_if_running` 的判定 + 尾部的 `_dispatch_respond_to_issue_reply` 段搬进来，不改语义；导入全部放函数体内，避开与该 router 的循环）：

```python
"""评论 / 定时唤醒 / 子代理结果三条触发路径共用的一段判定：目标 issue 在跑就投
收件箱，空闲就派发一轮。返回类型化结果 —— silent no-op 不可接受。"""

TERMINAL_STATUSES = ("done", "cancelled")


@dataclass(frozen=True)
class DeliverResult:
    mode: Literal["inbox", "dispatched", "skipped"]
    inbox_id: Optional[int] = None
    workflow_id: Optional[str] = None
    reason: Optional[str] = None


async def deliver_or_dispatch(
    issue_id: int, *, kind: str, content: dict[str, Any], user_id: str,
    message_body: Optional[str] = None, source: Optional[dict] = None,
    already_enqueued: bool = False,
) -> DeliverResult:
    issue = await issue_repository.get_by_id(issue_id)
    if not issue or issue.get("status") in TERMINAL_STATUSES or issue.get("hidden_at"):
        return DeliverResult("skipped", reason="issue_terminal")
    session_id = await get_or_create_issue_session(issue_id)
    running = await get_agent_runs_repository().running_root_run_id(
        issue_id=issue_id, conversation_id=int(session_id) if session_id else None)
    # 三个 busy 信号缺一不可：在跑 / 已暂停 / 派发窗口内（Task 2 的 dispatching 标记 ——
    # workflow 还没起来时 running_root_run_id 仍是空，那正是 fork 抢跑的那个窗口）。
    if running is not None or issue.get("paused_at") or is_dispatching(issue):
        if already_enqueued:
            return DeliverResult("inbox", reason="already_enqueued")
        row = await get_agent_run_inbox_repository().enqueue(
            target_kind="issue", target_id=issue_id, user_id=user_id, kind=kind, content=content)
        return DeliverResult("inbox", inbox_id=int(row["id"]))
    if message_body:
        await _append_thread_message(issue_id, session_id, user_id, message_body, source)
    wf_id = f"issue-reply-{issue_id}-{uuid.uuid4()}"
    try:
        _dispatch_respond_to_issue_reply(issue_id, user_id, message_body or "", None, wf_id)
    except Exception as exc:  # noqa: BLE001 — 类型化失败回显，不静默
        logger.opt(exception=True).error(f"[deliver_or_dispatch] issue {issue_id} 派发失败: {exc}")
        return DeliverResult("skipped", reason=f"dispatch_failed: {exc!s:.120}")
    return DeliverResult("dispatched", workflow_id=wf_id)
```

`_append_thread_message` 复用 `ConversationsAiStore().append_user_message`，并往 `issue_messages` 插一行 `kind='comment'` / `author_user_id=user_id` / `body=message_body` / `meta={"source": source} if source else {}`。**不要写 `author_kind`——那一列不存在**（mig 205）；来源只走 `meta.source`，这也是 Part 3 给「由定时唤醒开始」芯片判定的唯一依据。`issue_messages_router` 的两处改为调用它，响应字段（`diverted_to_inbox` / `inbox_id` / `agent_dispatched`）不变；`tests/api/test_issue_messages*.py` 全绿即证明语义没漂。
- [ ] **Step 11: worker 分支测试（红）** `tests/workforce/test_run_one_task_subagent.py`：桩 `SubAgentTaskService.run_background_task` 返回 `{"status":"success","summary":"s","sub_run_id":"52","tokens_used":9}`，喂一条 `payload.kind == "subagent"` 的 task，断言 ① 写了一条 `agent_run_inbox`，`kind == "subagent_result"`、`target_kind/target_id` 等于 `reply_to`、`content["child_run_id"] == "52"`；② `RunEventWriter` 以 **父 run id 900** 收到 `subagent_done`；③ issue 目标且空闲时 `deliver_or_dispatch` 被调用一次。

- [ ] **Step 12: 实现 worker 分支。** `run_one_task` 里可领取判定之后（`agent = await agent_repo.get_by_id(...)` **之前**，因为子代理通常非 persistent，走到那道门会被误判失败）：

```python
    if (payload.get("kind") or "") == "subagent":
        return await _run_subagent_task(task_id=task_id, payload=payload, workforce=workforce)
```

同文件新增：

```python
async def _run_subagent_task(*, task_id, payload, workforce) -> dict[str, Any]:
    """后台子代理：以 payload 里的调用方上下文重建 SubAgentTaskService，跑同一个
    ``_spawn(await=True)``，结果进收件箱 + 在父 run 上补一条 subagent_done。
    父 run 可能早已结束 —— 事件比 run 长寿，照写（spec §2.4）。"""
    started = time.monotonic()
    parent_run_id = payload.get("parent_run_id")
    svc = SubAgentTaskService(
        caller_agent_id=UUID(payload["caller_agent_id"]),
        caller_user_id=UUID(payload["user_id"]),
        parent_run_id=str(parent_run_id) if parent_run_id else None,
        agent_depth=int(payload.get("agent_depth") or 0),
    )
    envelope = await svc.run_background_task(payload)
    reply_to = payload.get("reply_to") or {}
    content = {
        "child_run_id": envelope.get("sub_run_id"),
        "subagent_type": payload.get("subagent_type"),
        "description": payload.get("description"),
        "status": envelope.get("status"),
        "summary": envelope.get("summary") or "",
        "cost_cents": envelope.get("cost_cents") or 0,
        "tokens_used": envelope.get("tokens_used") or 0,
    }
    if reply_to.get("target_kind"):
        await get_agent_run_inbox_repository().enqueue(
            target_kind=str(reply_to["target_kind"]), target_id=int(reply_to["target_id"]),
            user_id=str(payload["user_id"]), kind="subagent_result", content=content)
    if parent_run_id:
        try:
            writer = await RunEventWriter.for_run(int(parent_run_id))
            await writer.append("subagent_done", {
                "child_run_id": content["child_run_id"], "task_id": str(task_id), "mode": "async",
                "status": content["status"], "cost_cents": content["cost_cents"],
                "tokens_used": content["tokens_used"],
                "duration_ms": int((time.monotonic() - started) * 1000)})
        except Exception:  # noqa: BLE001 — 观测写失败不该让任务判失败
            logger.exception(f"[agent-worker] subagent_done 写父 run {parent_run_id} 失败")
    if reply_to.get("target_kind") == "issue":
        await deliver_or_dispatch(int(reply_to["target_id"]), kind="subagent_result",
                                  content=content, user_id=str(payload["user_id"]),
                                  already_enqueued=True)
    await workforce.update_task_status(
        task_id=task_id, lifecycle_status="done" if content["status"] == "success" else "failed")
    return {"task_id": str(task_id), "status": content["status"], "run_id": content["child_run_id"]}
```

⚠️ issue 目标上有**两次**判断：`enqueue` 已把结果放进收件箱，`deliver_or_dispatch` 再判一次「要不要开新一轮」。`already_enqueued=True` 让它的 busy 分支直接返回而不重复入箱，只留空闲派发那一支。`payload["caller_agent_id"]` 因此要在 Step 3 的 payload 里一并写入（`"caller_agent_id": str(self.caller_agent_id)`，Interfaces 段的 payload 形状同步补这个键）。

`SubAgentTaskService.run_background_task(payload)` 只是薄壳：`return await self._spawn({"subagent_type": payload["subagent_type"], "prompt": payload["prompt"], "description": payload.get("description") or "", "child_run_id": payload.get("child_run_id"), "await": True})`。
- [ ] **Step 13: 收件箱框渲染。** `inbox.py::render_inbox_message` 在 `kind`/`at` 之后按 kind 补两个属性：

```python
    extra = ""
    if item.kind == "subagent_result":
        c = item.content
        extra = (f' child_run_id="{escape_frame_attr(c.get("child_run_id") or "")}"'
                 f' subagent_type="{escape_frame_attr(c.get("subagent_type") or "")}"')
```

并让 `InboxItem.body()` 对 `subagent_result` 返回 `content["summary"]`（否则整段 JSON 进框，白烧 token）。测试补进 `tests/runner/test_inbox_render*.py`：喂 `subagent_type='a" onmouseover="'` 断言属性被转义、正文只有 summary。

- [ ] **Step 14: schema 与分类守卫。** `prompt_composer` 的 `Skill` properties 里，`tasks` 之后加两个键（描述照既有 `skill='task' only:` 的口吻）：

```python
                        "await": {"type": "boolean", "description": (
                            "skill='task' only: false runs the sub-agent in the "
                            "background; the result arrives later as an inbox "
                            "message instead of this call's return.")},
                        "child_run_id": {"type": "string", "description": (
                            "skill='task' only: continue an earlier sub-run "
                            "instead of starting a fresh one.")},
```

跑 `tests/agent_framework/test_tool_descriptor_allowlist.py`（`Tool` 没加字段，应当无改动即绿）与 `PIN_REFRESH=1 uv run pytest tests/services/ai/prompts/test_system_message_pin.py` —— **工具 schema 不在系统消息文本里**，pin 预期无 diff；若真有 diff，读完再提交。

- [ ] **Step 15: 缓冲回退守卫。** 本 Task **没有**给 `run_turn` 的结果加任何新标志（`subagent_spawned` / `subagent_done` 都是事件，envelope 的 `task_id` 是工具返回值不是 run 结果）。因此 `tests/runner/test_turn_end_reasons.py` 不动，在 PR 描述里写明这一判断。

- [ ] **Step 16: 跑全 + lint**

```bash
cd backend && uv run pytest -q tests/runner/test_subagent_async.py tests/runner/test_subagent_continue.py \
  tests/runner/test_fold_subagents.py tests/services/issues/test_inbox_or_dispatch.py \
  tests/workforce/test_run_one_task_subagent.py tests/test_subagent_task_service.py \
  tests/test_subagent_parallel.py tests/api/test_issue_messages_post.py
cd backend && uv run ruff check app tests && uv run black --check app tests && uv run isort --check-only app tests
```

期望：全绿，ruff/black/isort 无输出。

- [ ] **Step 17: 突变** 把 `_run_subagent_task` 里 `RunEventWriter.for_run(int(parent_run_id))` 改成 `for_run(int(content["child_run_id"]))`（即 spec §7 点名的「`subagent_done` 不以父 run id 写」）→ `test_run_one_task_subagent.py` 断言父 run 收到事件那条**必须红**；还原。第二发：`fold_done` 去掉 `bucket` 递减 → `test_async_spawn_is_pending_until_done` 红；还原。两次输出贴进 PR。

- [ ] **Step 18: Commit**

```bash
git commit -am "feat(runner,workforce): 后台/续聊子代理 + subagent 事件与折叠 + subagent_result 投递 + deliver_or_dispatch 抽取（harness 二期 2b-2 Task 4）"
```

---

### Task 5: `issue_wakeup` 定时 + `_fire_issue_wakeup` + `ScheduleWakeup` 工具 + 白名单单一来源 + `GET /issues/{id}/schedules`

**Files:**
- Create: `backend/app/services/ai/tools/schedule_wakeup_tool.py`
- Create: `backend/app/services/ai/runner/folds/schedule.py`
- Modify: `backend/app/workflows/scheduled_master.py`（`SUPPORTED_TASK_TYPES` 导出；`_dispatch_one:340` 的 `agent_routine` 分支旁加 `issue_wakeup`；新增 `_fire_issue_wakeup`；`_dispatch_routine_orders:711` 按 `order["kind"]` 分流）
- Modify: `backend/app/api/schedules_router.py:52-61,100-115,166-200`（白名单改 import；`ScheduleCreatePayload` 加 `fire_at`、`cron_expr`/`name` 转可选；`ScheduleResponse.cron_expr` 转可选；`_validate_issue_wakeup_payload`）
- Modify: `backend/app/api/issues_router.py`（`GET /{issue_id}/schedules`，照 `:706` 的 `pipeline-runs` 写法）
- Modify: `backend/app/services/ai/chat/ai_library_chat_service.py:1140-1158`（issue 轮次注册 `ScheduleWakeup`）、`backend/app/services/ai/runner/agent_runner.py:118-130,860-900`（`SUPPORTED_TOOLS` + 分派）
- Modify: `backend/app/services/ai/runner/run_projection.py`（`"wakeups": []`；import 加 `schedule`）、`backend/app/services/ai/prompts/README.md`
- Test: `backend/tests/workflows/test_issue_wakeup.py`、`tests/api/test_issue_schedules_router.py`、`tests/runner/test_schedule_wakeup_tool.py`、`tests/runner/test_fold_schedule.py`；扩 `tests/test_scheduled_master_autopilot.py`、`tests/test_schedules_router_autopilot.py`

**Interfaces:**
- Consumes：Task 1 的 mig 461（`user_schedules.cron_expr` 可空 + CHECK；事件 `schedule_set` 放行）；Task 4 的 `deliver_or_dispatch`。
- Produces：
  - `scheduled_master.SUPPORTED_TASK_TYPES: frozenset[str]`（`{"parse","download","transcode","ai_summary","agent_routine","issue_wakeup"}`）
  - `scheduled_master._fire_issue_wakeup(row) -> dict | None`，order 形状 `{"kind":"issue_wakeup","sched_id","issue_id","text","source":{"kind":"schedule","schedule_id","created_by"}}`（`agent_routine` 的 order 补 `"kind":"agent_routine"`）
  - 父 run 事件 `schedule_set{schedule_id: str, fire_at: str(ISO), note: str}`；`view.wakeups = [{schedule_id, fire_at, note}]`
  - `POST /api/v1/schedules` 与 `GET /api/v1/issues/{issue_id}/schedules` 的 wire 形状见下。

**wire 形状（Part 3 直接照抄）：**

```jsonc
// POST /api/v1/schedules —— issue_wakeup 一次性。name/cron_expr 现为可选。
{ "task_type": "issue_wakeup",
  "fire_at": "2026-09-11T09:00:00+00:00",     // ISO-8601，带时区；→ next_fire_at
  "timezone": "Asia/Shanghai",                 // 可选，默认 UTC
  "payload": { "issue_id": 123, "text": "check the render", "once": true } }
// 201 → ScheduleResponse，其中 cron_expr 为 null

// GET /api/v1/issues/123/schedules
{ "items": [
    { "id": "…", "task_type": "issue_wakeup", "fire_at": "2026-09-11T09:00:00+00:00",
      "cron_expr": null, "text": "check the render", "created_by": "agent", "enabled": true } ] }
```

`fire_at` 是**响应侧**字段名（值取自 `next_fire_at`），请求侧同名；`text` 取自 `payload.text`（`agent_routine` 行取 `payload.prompt_md`）；`created_by` 取自 `payload.created_by`，`agent_routine` 行固定 `"user"`。

- [ ] **Step 1: 白名单单一来源测试（红）** 扩 `tests/test_schedules_router_autopilot.py`：

```python
def test_router_whitelist_is_the_engine_registry():
    from app.api import schedules_router as r
    from app.workflows.scheduled_master import SUPPORTED_TASK_TYPES

    assert r._ALLOWED_TASK_TYPES == SUPPORTED_TASK_TYPES
    assert "ai_transcription" not in SUPPORTED_TASK_TYPES
    assert {"agent_routine", "issue_wakeup"} <= SUPPORTED_TASK_TYPES
```

- [ ] **Step 2: 实现导出。** `scheduled_master` 顶部：

```python
# 引擎真正能跑的 task_type —— _resolve_workflow_callable 的注册表键，加上两个
# 在 _dispatch_one 里就地分流的类型。API 白名单从这里 import（单一来源）：
# 白名单比引擎宽会让 schedule 每分钟被静默 skip，那是最难查的一类"配了没生效"。
_REGISTRY_TASK_TYPES: Final = frozenset({"parse", "download", "transcode", "ai_summary"})
SUPPORTED_TASK_TYPES: Final = _REGISTRY_TASK_TYPES | {"agent_routine", "issue_wakeup"}
```

`schedules_router` 把 `_ALLOWED_TASK_TYPES = {...}` 换成 `from app.workflows.scheduled_master import SUPPORTED_TASK_TYPES as _ALLOWED_TASK_TYPES`（保留旧名，`_validate_task_type` 不动）。

- [ ] **Step 3: API 测试（红）** 扩 `tests/test_schedules_router_autopilot.py`：`issue_wakeup` 无 `fire_at` → 400；`payload.text` 为空 → 400；`issue_id` 不可见 → 404；合法 → 201 且落库行 `cron_expr is None`、`next_fire_at == fire_at`；旧的 cron 型（`agent_routine`）无 `fire_at` 仍 201（回归）。

- [ ] **Step 4: 实现 API。** `ScheduleCreatePayload` 三处放宽（`name` / `cron_expr` 转 `Optional`，新增 `fire_at: Optional[datetime]`），`ScheduleResponse.cron_expr` 转 `Optional[str]`；`create_schedule` 按 task_type 分流：

```python
    _validate_task_type(payload.task_type)
    if payload.task_type == "issue_wakeup":
        text = await _validate_issue_wakeup_payload(payload, auth)
        name, cron_expr, next_at = (payload.name or text)[:200], None, payload.fire_at
    else:
        if not payload.name or not payload.cron_expr:
            raise HTTPException(400, {"code": "cron_required", "message": "name and cron_expr are required"})
        name, cron_expr = payload.name, payload.cron_expr
        next_at = _validate_cron(payload.cron_expr, payload.timezone)
        if payload.task_type == "agent_routine":
            _validate_agent_routine_payload(payload.payload)
```

`_validate_issue_wakeup_payload` 校验并返回 text：`fire_at` 必填、tz-aware、落在 `now` 与 `now + 30 天` 之间；`payload["text"]` strip 后非空；`payload["issue_id"]` 经 `assert_issue_visible(issue_id, auth)`（404 由它抛）；`payload.setdefault("once", True)`、`setdefault("created_by", "user")`。

⚠️ 400 的 detail 一律给 **dict**（`{"code": ..., "message": ...}`）：`app/core/exceptions.py` 把 `HTTPException` 包成 `ErrorResponse` 外壳，`detail` 是字符串时 `details` 就是 `null`，前端拿不到可判别的 code，只能看到 `http_400`。Part 3 的「稍后」弹层要区分 `fire_at_out_of_range` / `text_required` / `cron_required`，所以这里必须是 dict。
- [ ] **Step 5: 引擎测试（红）** `tests/workflows/test_issue_wakeup.py`（`fake_db` 照 `tests/test_scheduled_master_autopilot.py` 既有的 `write_scope` 桩写法，采集每行的 UPDATE values）：

```python
def _row(**kw):
    base = {"id": "s1", "user_id": "u1", "task_type": "issue_wakeup",
            "payload": {"issue_id": 7, "text": "ping", "once": True, "created_by": "user"}}
    return {**base, **kw}


async def test_terminal_issue_disables_the_schedule(monkeypatch, fake_db):
    monkeypatch.setattr(sm, "_load_issue", _issue({"status": "done"}))
    assert await sm._fire_issue_wakeup(_row()) is None
    assert fake_db.updates["s1"]["enabled"] is False
    assert fake_db.updates["s1"]["pause_reason"] == "issue_terminal"


async def test_live_issue_returns_an_order_and_writes_nothing(monkeypatch, fake_db):
    monkeypatch.setattr(sm, "_load_issue", _issue({"status": "open"}))
    order = await sm._fire_issue_wakeup(_row())
    assert order["kind"] == "issue_wakeup" and order["issue_id"] == 7 and order["text"] == "ping"
    assert order["source"] == {"kind": "schedule", "schedule_id": "s1", "created_by": "user"}
    assert "s1" not in fake_db.updates      # step 内不派发、也不额外写


async def test_once_disables_after_firing(monkeypatch, fake_db):
    await sm._finish_once(_row())
    assert fake_db.updates["s1"] == {"enabled": False, "pause_reason": "fired_once"}
```
- [ ] **Step 6: 实现引擎。** `_dispatch_one` 里 `if task_type == "agent_routine":` 分支**之前**加：

```python
    if task_type == "issue_wakeup":
        order = await _fire_issue_wakeup(row)
        if order is None:
            return {"outcome": "skipped"}
        if (payload or {}).get("once", True):
            await _finish_once(row)
        return {"outcome": "fired", "order": order}
```

⚠️ 位置：放在既有那条 advance UPDATE（`last_fired_at` / `next_fire_at` / `fire_count`）**之后**，沿用它的簿记；`_finish_once` 随即 `enabled=False`，那条算出来的 `next_fire_at` 因此无害。`cron_expr` 为空时 `_compute_next_fire` 的入参已是 `row.get("cron_expr") or "* * * * *"`，不用改。

```python
_WAKEUP_TERMINAL_STATUSES = ("done", "cancelled")


async def _fire_issue_wakeup(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """一次到点：只做 DB 判断，返回 order。派发在 workflow body（同 agent_routine）。
    issue 已终态 / 隐藏 → 不投，禁用该行并计入 skipped_count —— 一条永远投不进去的
    唤醒每分钟重试是纯噪声。"""
    payload = _as_dict(row.get("payload"))
    issue_id, text = payload.get("issue_id"), (payload.get("text") or "").strip()
    if not issue_id or not text:
        raise RuntimeError(f"issue_wakeup {row['id']} payload incomplete")
    issue = await _load_issue(int(issue_id))
    if not issue or issue.get("status") in _WAKEUP_TERMINAL_STATUSES or issue.get("hidden_at"):
        await _disable_schedule(row["id"], "issue_terminal", bump_skipped=True)
        return None
    return {"kind": "issue_wakeup", "sched_id": str(row["id"]), "issue_id": int(issue_id),
            "text": text, "user_id": str(row.get("user_id") or ""),
            "source": {"kind": "schedule", "schedule_id": str(row["id"]),
                       "created_by": payload.get("created_by") or "user"}}
```

`_disable_schedule(sched_id, reason, *, bump_skipped=False)`、`_finish_once(row)`（`enabled=False, pause_reason="fired_once"`）、`_as_dict`（asyncpg 可能把 jsonb 给成 str）都是本文件内的小 helper；`_load_issue` 走 `read_scope` + `select(Issues.status, Issues.hidden_at)`，单独成函数就是为了让 Step 5 的测试能 monkeypatch 它。

- [ ] **Step 7: workflow body 分流。** `_dispatch_routine_orders` 的 `for order in orders:` 首行加：

```python
        if order.get("kind") == "issue_wakeup":
            await _dispatch_issue_wakeup(order, counters)
            continue
```

```python
async def _dispatch_issue_wakeup(order: Dict[str, Any], counters: Dict[str, Any]) -> None:
    """到点 = 一条收件箱 steer 或一轮新派发；哪种由 deliver_or_dispatch 决定。
    失败按 agent_routine 同款簿记（连击计数 → 阈值自动暂停）。"""
    from app.services.issues.inbox_or_dispatch import deliver_or_dispatch

    try:
        result = await deliver_or_dispatch(
            int(order["issue_id"]),
            kind="steer",
            content={"text": order["text"], "source": order["source"]},
            user_id=str(order.get("user_id") or ""),
            message_body=order["text"],
            source=order["source"],
        )
    except Exception as exc:
        counters["errors"] = (counters.get("errors") or 0) + 1
        await record_routine_dispatch_error_step(str(order["sched_id"]), f"wakeup failed: {exc}")
        return
    if result.mode == "skipped":
        logger.info(f"[scheduled_master] wakeup {order['sched_id']} skipped: {result.reason}")
```

`user_id` 由 `_fire_issue_wakeup` 一并放进 order（上面的 order 形状里补 `"user_id": str(row.get("user_id") or "")`，Interfaces 段同步）。空闲分支把 `order["source"]` 原样落进 `issue_messages.meta.source`，线程因此能显示「由定时唤醒开始」。

- [ ] **Step 8: `ScheduleWakeup` 工具测试（红）** `tests/runner/test_schedule_wakeup_tool.py`：`delay_minutes=60` → 建行且返回 `{schedule_id, fire_at}`；`at` 超过 30 天 → `{"error": "fire_at must be within 30 days"}`；同一 run 第 4 次 → `{"error": "too_many_wakeups"}`；成功一次后父 recorder 上有 `schedule_set{schedule_id, fire_at, note}`；`at` 与 `delay_minutes` 都缺 → 类型化错误。

- [ ] **Step 9: 实现工具** `app/services/ai/tools/schedule_wakeup_tool.py`：`schedule_wakeup_spec()` 返回 OpenAI function spec（照 `finish_issue_tool.py:49` 的写法），描述固定为

  > `Schedule a one-time wake-up for this issue; when it fires you will receive the note as a message. Use it to wait for long external work instead of polling.`

  参数 `{at?: string (ISO-8601), delay_minutes?: integer, note: string}`。`make_schedule_wakeup_handler(*, issue_id, user_id, run_id, recorder)` 返回闭包，内部计数 `MAX_WAKEUPS_PER_RUN = 3`，把 `at`/`delay_minutes` 归一成 `fire_at`（`at` 优先；都缺 → `{"error":"at or delay_minutes required"}`；`> now + 30d` → `{"error":"fire_at must be within 30 days"}`），INSERT 一行 `user_schedules(task_type="issue_wakeup", cron_expr=None, next_fire_at=fire_at, user_id=issue owner, payload={"issue_id","text": note,"once": True,"created_by":"agent","run_id": run_id})`，然后 `emit(recorder, "schedule_set", {...})`，返回 `{"schedule_id","fire_at"}`。

  注册：`agent_runner.SUPPORTED_TOOLS` 加 `"ScheduleWakeup"`，`self.schedule_wakeup_handler: Optional[Any] = None`，分派段照 `FinishIssue` 的形状加一支（handler 为 None → `{"error": "ScheduleWakeup is not available for this turn."}`）；`ai_library_chat_service` 在既有的 `if trigger in ("issue_dispatch", "issue_dispatch_auto", "issue_reply"):` 块里、**且 `parent_run_id is None`** 时把 spec 拼进 `composed.tools` 并挂 handler —— 子代理 run 不公开（spec §3.2）。

- [ ] **Step 10: 折叠** `folds/schedule.py`（照 `folds/tools.py` 的形状），docstring 写明：

> 只记本 run 设的唤醒；取消不产生事件（用户从右栏调 DELETE），所以这个列表是「设过什么」的历史，不是「还剩什么」——右栏那张卡读实时表，别拿它当真相。

```python
@register("schedule_set")
def fold_schedule_set(views, payload):
    sid, fire_at = payload.get("schedule_id"), payload.get("fire_at")
    if not sid or not fire_at:
        return None
    views["view"]["wakeups"] = [*(views["view"].get("wakeups") or []),
        {"schedule_id": str(sid), "fire_at": str(fire_at), "note": str(payload.get("note") or "")}]
    return views
```

`empty_views()` 的 `"view"` 加 `"wakeups": []`（放在 `"fork"` 后）；底部 import 加 `schedule`。测试 `tests/runner/test_fold_schedule.py`：两次 set 累积成两条；缺 `fire_at` 时 `apply` 返回**同一对象**（`is` 断言）。
- [ ] **Step 11: `GET /issues/{id}/schedules` 测试（红）** `tests/api/test_issue_schedules_router.py`：不可见 issue → 404；返回体是 `{"items":[...]}`，每项七个键；`issue_wakeup` 行按 `payload->>'issue_id'` 匹配、`agent_routine` 行按 `payload->>'last_issue_id'` 匹配（它就是那条例行**造出**这个 issue 的证据，`agent_routine` payload 没有别的指向 issue 的字段——已核 `_fire_agent_routine`）；两类混排按 `next_fire_at` 升序。

- [ ] **Step 12: 实现端点**（`issues_router.py`，照 `:706` 的 `pipeline-runs` 那套：先 `issue_repository.get_by_id` + `_assert_visibility`，再查库）：

```python
@router.get("/{issue_id}/schedules")
async def list_issue_schedules(issue_id: int, auth: AuthDep) -> dict:
    """这个 issue 上的定时：自己的一次性唤醒行，加上造出它的那条例行。
    只读；取消走 DELETE /api/v1/schedules/{id}。"""
    ...
    sid = str(issue_id)
    stmt = (
        select(*UserSchedules.__table__.columns)
        .where(or_(
            (UserSchedules.task_type == "issue_wakeup") & (UserSchedules.payload["issue_id"].astext == sid),
            (UserSchedules.task_type == "agent_routine") & (UserSchedules.payload["last_issue_id"].astext == sid),
        ))
        .order_by(UserSchedules.next_fire_at)
    )
    items = [{
        "id": str(r["id"]), "task_type": r["task_type"],
        "fire_at": r["next_fire_at"].isoformat() if r["next_fire_at"] else None,
        "cron_expr": r["cron_expr"],
        "text": ((r["payload"] or {}).get("text") or (r["payload"] or {}).get("prompt_md") or "")[:500],
        "created_by": (r["payload"] or {}).get("created_by") or "user",
        "enabled": bool(r["enabled"]),
    } for r in rows]
    return {"items": items}
```
- [ ] **Step 13: prompts README + pin。** `app/services/ai/prompts/README.md` 在「工具 schema：`AskUser`」之后插一节 `### 工具 schema：ScheduleWakeup（仅 issue 根 run）`，含强制三小节：`What the model sees` 原样贴出上面那句 description 与三个参数（稳定字面量，markdown 围栏）；`Token effect` 写「约 70 token，恒定；只在 issue 根 run 的 tools 数组里，聊天路与子代理 run 上完全不存在」；`KV Cache effect` 写「tools 数组是稳定前缀的一部分，issue run 与 chat run 因而是两个前缀族——本模块任何改动都会让 issue 路的前缀复用失效，chat 路不受影响」。然后：

```bash
cd backend && PIR=1 PIN_REFRESH=1 uv run pytest tests/services/ai/prompts/test_system_message_pin.py
git diff -- backend/tests/services/ai/prompts/snapshots/system_message_text_turn.txt
```

期望：**snapshot 无 diff**（工具 schema 不进系统消息文本）。若有 diff，读完再决定是否提交——那意味着改动误碰了系统消息散文。

- [ ] **Step 14: 跑全 + lint**

```bash
cd backend && uv run pytest -q tests/workflows/test_issue_wakeup.py tests/api/test_issue_schedules_router.py \
  tests/runner/test_schedule_wakeup_tool.py tests/runner/test_fold_schedule.py \
  tests/test_scheduled_master_autopilot.py tests/test_schedules_router_autopilot.py \
  tests/runner/test_run_projection_shape.py
cd backend && uv run ruff check app tests && uv run black --check app tests && uv run isort --check-only app tests
```

期望：全绿，lint 三件套无输出。

- [ ] **Step 15: 突变** 把 `_fire_issue_wakeup` 的终态判定去掉（`if not issue or ...` 整段删）→ `test_terminal_issue_disables_the_schedule` 必须红；还原。第二发：`SUPPORTED_TASK_TYPES` 手工塞回 `"ai_transcription"` → `test_router_whitelist_is_the_engine_registry` 红；还原。两次输出贴进 PR。

- [ ] **Step 16: Commit**

```bash
git commit -am "feat(schedules,runner): issue_wakeup 定时进收件箱 + ScheduleWakeup 工具 + 白名单单一来源 + issue 定时列表端点（harness 二期 2b-2 Task 5）"
```

---

### Task 6: 前端——子代理卡、Cockpit 子代理格、「稍后」弹层、定时块、Quick 芯片、Vitest 拆卸修

**Files:**
- Modify: `frontend/components/agentActivity/TrajectoryRenderer/foldEvents.ts`（`StepNode.children[]` 折 `subagent_spawned/done`；`InboxNode` 加 `result`（`subagent_result` 内容）与 `source`（steer 的 `content.source`）；新节点 kind `schedule` 折 `schedule_set`）
- Modify: `frontend/components/agentActivity/TrajectoryRenderer/nodes/builtins.tsx`（step 卡下 `SubagentCards`；`InboxNodeView` 三种文案；`ScheduleNodeView` + 注册）
- Create: `frontend/components/Todolist/childRunContext.ts`（子 run 面板的开/关，跨 `TrajectoryRenderer` 传递）
- Modify: `frontend/components/TaskCenter/runView.ts`（`RunView.children` 扩形 + `wakeups[]`；`childrenState` / `wakeupsState`）
- Modify: `frontend/components/Todolist/blocks/CockpitBlock.tsx`（第六格 `cockpit-children`，仅 `children.total > 0`；副行 `⏰ Wakes at …`）
- Create: `frontend/components/Todolist/blocks/SchedulesBlock.tsx`；Modify: `frontend/components/Todolist/blocks/index.ts`
- Create: `frontend/components/Todolist/laterPresets.ts`、`frontend/components/Todolist/LaterPopover.tsx`
- Modify: `frontend/components/Todolist/IssueReplyBox.tsx:66-96,537-545`（`issueId` prop + 页脚「⏰ Later」按钮）、`frontend/components/Todolist/IssueDetailView.tsx`（传 `issueId`、持 `childRun` 状态并 Provider、`DetachedRunPanel` 传 origin）
- Modify: `frontend/components/Todolist/IssueChatThread.tsx:250-335`（`run-header-chips` 加 `run-wakeup-chip`；`DetachedRunPanel` 加 `origin` 变体与「Back To Parent」）
- Modify: `frontend/services/schedulesService.ts`（`createIssueWakeup` / `listIssueSchedules`）、`frontend/services/issuesService.ts`（`Issue.pending_wakeups?: number`）
- Modify: `frontend/components/Todolist/IssueListView.tsx:620-640,915-940`（Quick 加 `Scheduled N`）
- Modify（后端，随本 Task 一并上）: `backend/app/schemas/issue.py`（`Issue.pending_wakeups: int = 0`）、`backend/app/repositories/issue_repository.py::list_for_user`（分页后一次聚合 `user_schedules`）
- Modify: `frontend/components/AISettings.codexDaemon.test.tsx:31-38`、`frontend/tests/setup.ts`
- Modify: `frontend/public/locales/en.json`、`zh.json`
- Test: `foldEvents.test.ts`（追加）、`nodes/subagentCards.test.tsx`（新）、`runView.test.ts`（追加）、`blocks/CockpitBlock.test.tsx`（追加）、`laterPresets.test.ts`（新）、`LaterPopover.test.tsx`（新）、`blocks/SchedulesBlock.test.tsx`（新）、`IssueChatThread.test.tsx`（追加唤醒芯片）、`IssueListView.test.tsx`（追加 Scheduled 芯片）、`subagentI18nParity.test.ts`（新）、`backend/tests/repositories/test_issue_list_pending_wakeups.py`（新）

**Interfaces:**
- Consumes（Part 1–2 已交付，**照抄不改**）：父 run 事件 `subagent_spawned{task_id?, child_run_id?, mode:"sync"|"async", subagent_type, description, continued_from?}`、`subagent_done{child_run_id, task_id?, mode, status, cost_cents, tokens_used, duration_ms}`、`schedule_set{schedule_id, fire_at, note}`；`inbox_claimed{inbox_id, kind, turn, step, content}`，`kind` 可为 `subagent_result`（content `{child_run_id, subagent_type, description, status, summary, cost_cents, tokens_used}`）或 `steer`（`content.source={kind:"schedule", schedule_id, created_by}`）；`run.view.children={total,done,running,async_pending,last}`、`run.view.wakeups=[{schedule_id,fire_at,note}]`；`GET /api/v1/issues/{id}/schedules` → `{items:[{id, task_type, fire_at, cron_expr, text, created_by, enabled}]}`；`POST /api/v1/schedules`、`DELETE /api/v1/schedules/{id}`。
- Produces：`SubagentChild`、`StepNode.children`、`ScheduleNode`；`childrenState(view)` / `wakeupsState(view)`；`createIssueWakeup(issueId, {fireAt, text})`、`listIssueSchedules(issueId)`；`wakeupPresets(now)`；`ChildRunContext`。
- ⚠️ **wire 形状**（CLAUDE.md「边界 mock 必须用真实 JSON 形状」）：`issues.id` 是 JSON **number**（`Issue.id: int`，无 `str()` 归一），所以 `POST /schedules` 的 `payload.issue_id` 断言必须是数字；`user_schedules.id` 是**字符串** UUID；`child_run_id` 是 Snowflake **字符串**（run id 全线 str）。桩 fetch 的响应体照 `ScheduleResponse` 全字段给，不要只给用到的几个。

- [ ] **Step 1: 折叠测试（红）** —— `foldEvents.test.ts` 追加：

```ts
const ev = (seq: number, event_type: string, payload: Record<string, unknown> = {}, step?: number) =>
  ({ seq, event_type, payload, step: step ?? null, turn: 1, created_at: '' } as never);

it('folds subagent_spawned/done into the step that dispatched them, three states', () => {
  const nodes = foldEvents([
    ev(1, 'step_start', { turn: 1, step: 1 }, 1),
    ev(2, 'subagent_spawned', { child_run_id: '347786145852739', mode: 'sync', subagent_type: 'librarian', description: 'Find the deck' }, 1),
    ev(3, 'subagent_spawned', { task_id: 'tk-9', mode: 'async', subagent_type: 'archivist', description: 'Sweep old runs' }, 1),
    ev(4, 'subagent_done', { child_run_id: '347786145852739', mode: 'sync', status: 'completed', cost_cents: 0.03, tokens_used: 1200, duration_ms: 8400 }, 1),
  ], { isRunning: true });
  const step = nodes.find((n) => n.kind === 'step') as StepNode;
  expect(step.children).toEqual([
    { key: 'child:347786145852739', childRunId: '347786145852739', taskId: null, mode: 'sync', subagentType: 'librarian', description: 'Find the deck', continuedFrom: null, status: 'completed', costCents: 0.03, tokensUsed: 1200, durationMs: 8400 },
    { key: 'child:tk-9', childRunId: null, taskId: 'tk-9', mode: 'async', subagentType: 'archivist', description: 'Sweep old runs', continuedFrom: null, status: null, costCents: null, tokensUsed: null, durationMs: null },
  ]);
});

it('keeps a continued child tagged with the run it continues', () => {
  const nodes = foldEvents([ev(1, 'step_start', {}, 1), ev(2, 'subagent_spawned', { child_run_id: '9', mode: 'sync', subagent_type: 'librarian', description: 'more', continued_from: '7' }, 1)]);
  expect((nodes[0] as StepNode).children[0].continuedFrom).toBe('7');
});

it('reads a subagent_result inbox row and a schedule-sourced steer', () => {
  const nodes = foldEvents([
    ev(1, 'inbox_claimed', { inbox_id: 'i1', kind: 'subagent_result', turn: 1, step: 4, content: { child_run_id: '9', subagent_type: 'librarian', description: 'Find the deck', status: 'completed', summary: 'Found 3 decks', cost_cents: 0.03, tokens_used: 900 } }),
    ev(2, 'inbox_claimed', { inbox_id: 'i2', kind: 'steer', turn: 1, step: 3, content: { text: 'ping', source: { kind: 'schedule', schedule_id: 'sc-1', created_by: 'user' } } }),
  ]);
  expect(nodes[0]).toMatchObject({ kind: 'inbox', inboxKind: 'subagent_result', result: { childRunId: '9', subagentType: 'librarian', status: 'completed', summary: 'Found 3 decks' } });
  expect(nodes[1]).toMatchObject({ kind: 'inbox', inboxKind: 'steer', source: { kind: 'schedule', scheduleId: 'sc-1', createdBy: 'user' } });
});

it('folds schedule_set into its own node', () => {
  expect(foldEvents([ev(1, 'schedule_set', { schedule_id: 'sc-2', fire_at: '2026-09-11T01:00:00Z', note: 'check the render' })])[0])
    .toEqual({ kind: 'schedule', key: 'seq:1', scheduleId: 'sc-2', fireAt: '2026-09-11T01:00:00Z', note: 'check the render' });
});
```

- [ ] **Step 2: 实现折叠** —— `foldEvents.ts`：`StepNode` 加 `children: SubagentChild[]`（`newStep` 里初始化 `children: []`），`InboxNode` 加 `result` / `source`，新增 `ScheduleNode`：

```ts
export interface SubagentChild {
  key: string; childRunId: string | null; taskId: string | null;
  mode: 'sync' | 'async'; subagentType: string; description: string;
  continuedFrom: string | null;
  /** null 之前 = 还在跑（同步等结果 / 后台排队中）。 */
  status: string | null; costCents: number | null; tokensUsed: number | null; durationMs: number | null;
}
export interface ScheduleNode { kind: 'schedule'; key: string; scheduleId: string; fireAt: string | null; note: string }
export interface InboxSource { kind: string; scheduleId: string | null; createdBy: string | null }
export interface SubagentResult { childRunId: string | null; subagentType: string; description: string; status: string; summary: string; costCents: number | null; tokensUsed: number | null }

const childKey = (p: Record<string, unknown>): string | null => {
  const id = str(p.child_run_id) ?? str(p.task_id);
  return id ? `child:${id}` : null;
};

// switch 内新增：
case 'subagent_spawned': {
  const node = ensureStep(ev, null);
  const key = childKey(p);
  if (!key) break; // 无标识的 spawn 不画卡（宁可少一张，不要一张点不开的）
  if (node.children.some((c) => c.key === key)) break;
  node.children.push({
    key, childRunId: str(p.child_run_id), taskId: str(p.task_id),
    mode: p.mode === 'async' ? 'async' : 'sync',
    subagentType: str(p.subagent_type) ?? 'subagent', description: str(p.description) ?? '',
    continuedFrom: str(p.continued_from), status: null, costCents: null, tokensUsed: null, durationMs: null,
  });
  break;
}
case 'subagent_done': {
  const key = childKey(p);
  // done 可能落在**下一步**（后台结果回来时父 run 已走远）：全局找那张卡。
  for (const n of nodes) {
    if (n.kind !== 'step') continue;
    const child = n.children.find((c) => c.key === key);
    if (!child) continue;
    child.status = str(p.status) ?? 'completed';
    child.costCents = num(p.cost_cents); child.tokensUsed = num(p.tokens_used); child.durationMs = num(p.duration_ms);
    if (!child.childRunId) child.childRunId = str(p.child_run_id);
    break;
  }
  break;
}
case 'schedule_set': {
  const scheduleId = str(p.schedule_id);
  if (scheduleId) nodes.push({ kind: 'schedule', key: `seq:${ev.seq}`, scheduleId, fireAt: str(p.fire_at), note: str(p.note) ?? '' });
  break;
}
```

`inbox_claimed` 分支改为读 `content`（同样经 `obj()`，真 wire 上是 JSON 字符串）：

```ts
const content = obj(p.content) ?? {};
const src = obj(content.source);
nodes.push({
  kind: 'inbox', key: `seq:${ev.seq}`, inboxKind: str(p.kind) ?? 'steer',
  turn: num(ev.turn) ?? num(p.turn), step: num(ev.step) ?? num(p.step), at: ev.created_at ?? null,
  source: src ? { kind: str(src.kind) ?? '', scheduleId: str(src.schedule_id), createdBy: str(src.created_by) } : null,
  result: str(p.kind) === 'subagent_result'
    ? { childRunId: str(content.child_run_id), subagentType: str(content.subagent_type) ?? 'subagent', description: str(content.description) ?? '', status: str(content.status) ?? 'completed', summary: str(content.summary) ?? '', costCents: num(content.cost_cents), tokensUsed: num(content.tokens_used) }
    : null,
});
```

- [ ] **Step 3: 子 run 上下文 + 卡片渲染测试（红）** —— `childRunContext.ts`：

```ts
export interface ChildRunOrigin { childRunId: string; parentRunId: string | null; step: number; mode: 'sync' | 'async'; subagentType: string; description: string }
export interface ChildRunState { current: ChildRunOrigin | null; open: (o: ChildRunOrigin) => void; close: () => void }
export const ChildRunContext = createContext<ChildRunState | null>(null);
export function useChildRun(): ChildRunState | null { return useContext(ChildRunContext); }
```

`nodes/subagentCards.test.tsx`（mock `react-i18next` 为 `t: (k, f, o) => (typeof f === 'string' ? f : k)`）：三态各渲染一次 → `subagent-card[data-state=running|queued|done]`；同步在跑 → 有 spinner、文案含 `Waiting`；后台未完成 → 文案含 `Result Arrives In The Inbox`；完成 → 含 `¢0.03` 与 `8.4s` 与摘要行；`continuedFrom` → `subagent-continued` 芯片；有 `childRunId` 时 `subagent-open` 按钮点击 → `open()` 收到完整 origin；无 `childRunId`（后台排队中）→ 按钮不渲染。

- [ ] **Step 4: 实现卡片与两个节点渲染** —— `builtins.tsx` 内新增，`StepNodeView` 的 `{open && …}` 块之后插入 `<SubagentCards node={node} />`：

```tsx
const CHILD_TONE: Record<'running' | 'queued' | 'done', string> = {
  running: 'border-agent-line bg-agent-soft/40 text-agent',
  queued: 'border-info-line bg-info-soft/40 text-info',
  done: 'border-ok-line bg-ok-soft/40 text-ink-300',
};

export const SubagentCards: React.FC<{ node: StepNode }> = ({ node }) => {
  const { t } = useTranslation();
  const childRun = useChildRun();
  if (node.children.length === 0) return null;
  return (
    <div className="flex flex-col gap-1 px-2.5 pb-1.5 pl-7" data-testid="subagent-cards">
      {node.children.map((c) => {
        const state = c.status ? 'done' : c.mode === 'async' ? 'queued' : 'running';
        return (
          <div key={c.key} data-testid="subagent-card" data-state={state} data-mode={c.mode}
               className={`rounded-md border px-2 py-1 text-[11px] ${CHILD_TONE[state]}`}>
            <div className="flex items-center gap-1.5 min-w-0">
              {state === 'running' && <span className="inline-block h-2.5 w-2.5 shrink-0 animate-spin rounded-full border-2 border-agent-line border-t-agent" />}
              <Users size={11} className="shrink-0" />
              <span className="font-medium">{c.subagentType}</span>
              {c.continuedFrom && (
                <span data-testid="subagent-continued" className="rounded border border-agent-line px-1">
                  {t('subagent.continued', 'Continued From #{{run}}', { run: c.continuedFrom.slice(-6) })}
                </span>
              )}
              <span className="truncate text-ink-400">{c.description}</span>
              <span className="ml-auto shrink-0 tabular-nums">
                {state === 'running' && t('subagent.waiting', 'Waiting For Result')}
                {state === 'queued' && t('subagent.queued', 'Background · Result Arrives In The Inbox')}
                {state === 'done' && [fmtMs(c.durationMs), fmtCents(c.costCents)].filter(Boolean).join(' · ')}
              </span>
              {c.childRunId && childRun && (
                <button type="button" data-testid="subagent-open" className="shrink-0 underline decoration-dotted"
                        onClick={() => childRun.open({ childRunId: c.childRunId as string, parentRunId: null, step: node.step, mode: c.mode, subagentType: c.subagentType, description: c.description })}>
                  {t('subagent.open', 'Open Run #{{run}}', { run: c.childRunId.slice(-6) })}
                </button>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
};
```

`InboxNodeView` 三分支：`node.result` → `📥` 文案 `t('trajectory.inboxSubagent', 'Sub-agent result · {{type}} · read before step {{n}}')` + 摘要行（`data-testid="traj-inbox-summary"`）；`node.source?.kind === 'schedule'` → `t('trajectory.inboxWakeup', 'Wake-up · set by {{who}} · read before step {{n}}')`；其余保持现文案。新增 `ScheduleNodeView`（`data-testid="traj-schedule"`，info 色，文案 `t('schedule.agentSet', 'Agent scheduled a wake-up · {{at}}')` + note + `schedule-cancel` 按钮调 `schedulesService.remove(node.scheduleId)`，失败 `console.error` 并显示 `schedule.cancelFailed`），底部 `registerTrajectoryNode('schedule', ScheduleNodeView)`。

- [ ] **Step 5: 选择器 + Cockpit 格（红 → 绿）** —— `runView.ts`：

```ts
export interface RunChildren { total: number; done: number; running: number; async_pending: number; last: { child_run_id: string; subagent_type: string; status: string } | null }
export interface RunWakeup { schedule_id: string; fire_at: string; note: string }
// RunView：children: RunChildren；wakeups?: RunWakeup[] | null

export function childrenState(view: RunView | null): RunChildren | null {
  const c = view?.children;
  if (!c || typeof c.total !== 'number' || c.total <= 0) return null;
  const n = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : 0);
  const last = c.last && typeof c.last === 'object' ? c.last : null;
  return { total: c.total, done: n(c.done), running: n(c.running), async_pending: n(c.async_pending), last };
}

export function wakeupsState(view: RunView | null): RunWakeup[] {
  const raw = view?.wakeups;
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((w): w is RunWakeup => !!w && typeof w === 'object' && typeof (w as RunWakeup).fire_at === 'string')
    .slice()
    .sort((a, b) => a.fire_at.localeCompare(b.fire_at));
}
```

`runView.test.ts` 追加：`children:{total:0,…}` → `null`（0 不出格，同 Tools 格规则）；缺字段补 0；`wakeups` 非数组 → `[]` 且按 `fire_at` 升序。
`CockpitBlock.tsx`：`const children = childrenState(view); const wakeups = wakeupsState(view);`，网格列数改成按可见格数算（`3 + (tools?1:0) + (children?1:0)` 映射到 `sm:grid-cols-*`），Tools 格之后：

```tsx
{children && (
  <Cell label={t('issueDetail.subagents', 'Sub-agents')} testId="cockpit-children">
    {children.done}
    <span className="text-ink-500 text-[12px]">/{children.total}</span>
    {children.async_pending > 0 && (
      <div className="text-[11px] text-info truncate">{t('issueDetail.subagentsBackground', '{{count}} in background', { count: children.async_pending })}</div>
    )}
  </Cell>
)}
```

副行末尾追加 `{wakeups.length > 0 && <span className="text-info" data-testid="cockpit-wakeup">{t('issueDetail.wakesAt', 'Wakes at {{at}}', { at: fmtWhen(wakeups[0].fire_at) })}</span>}`（`fmtWhen` = `new Date(x).toLocaleString()`，无效日期回原串）。`CockpitBlock.test.tsx` 追加：`total:2,done:1,async_pending:1` → `cockpit-children` 含 `1/2` 与 `1 in background`；`total:0` → 无该格；`wakeups` 一条 → `cockpit-wakeup` 出现。

- [ ] **Step 6: 「⏰ Later」预设与弹层（红 → 绿）** —— `laterPresets.ts` 纯函数（先测：`In 1 Hour` = now+60min；`Tonight 20:00` 当天 20:00，若已过 20:00 则该预设被剔除；`Tomorrow 09:00` 次日 09:00 且跨月正确）：

```ts
export interface WakeupPreset { key: 'in1h' | 'tonight' | 'tomorrow'; labelKey: string; fallback: string; at: Date }
export function wakeupPresets(now: Date): WakeupPreset[] {
  const at = (d: number, h: number, m: number) => { const x = new Date(now); x.setDate(x.getDate() + d); x.setHours(h, m, 0, 0); return x; };
  const out: WakeupPreset[] = [{ key: 'in1h', labelKey: 'later.in1h', fallback: 'In 1 Hour', at: new Date(now.getTime() + 3600_000) }];
  const tonight = at(0, 20, 0);
  if (tonight.getTime() > now.getTime()) out.push({ key: 'tonight', labelKey: 'later.tonight', fallback: 'Tonight 20:00', at: tonight });
  out.push({ key: 'tomorrow', labelKey: 'later.tomorrow', fallback: 'Tomorrow 09:00', at: at(1, 9, 0) });
  return out;
}
```

`schedulesService.ts` 追加（**不复用 `create`**：它必带 `name` / `cron_expr`）：

```ts
export interface IssueScheduleItem { id: string; task_type: string; fire_at: string | null; cron_expr: string | null; text: string; created_by: string; enabled: boolean }

export async function createIssueWakeup(issueId: number, opts: { fireAt: Date | string; text: string }): Promise<ScheduleResponse> {
  const fire_at = typeof opts.fireAt === 'string' ? opts.fireAt : opts.fireAt.toISOString();
  return request<ScheduleResponse>('', {
    method: 'POST',
    body: JSON.stringify({ task_type: 'issue_wakeup', fire_at, payload: { issue_id: issueId, text: opts.text, once: true } }),
  });
}

export async function listIssueSchedules(issueId: number): Promise<IssueScheduleItem[]> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/issues/${issueId}/schedules`, { headers });
  if (!res.ok) throw new Error(`Issue schedules API ${res.status}`);
  return ((await res.json()) as { items?: IssueScheduleItem[] }).items ?? [];
}
```

`LaterPopover.tsx`：`{issueId, text, onClose, onScheduled}`；`role="dialog"`、`data-testid="later-popover"`；预设按钮 `later-preset-<key>`；`Custom` 展开 `<input type="datetime-local" data-testid="later-custom">`；说明句 `t('later.explain', …)`；主按钮 `later-confirm`（文案 `Schedule`，`text` 为空时 disabled）；提交调 `createIssueWakeup`，失败 `later-error` + `console.error`。
`LaterPopover.test.tsx`（真实 wire 断言）：`fetch` 桩返回完整 `ScheduleResponse`；点 `In 1 Hour` → `Schedule` → 断言 `JSON.parse(body)` 深等于 `{task_type:'issue_wakeup', fire_at:<ISO>, payload:{issue_id: 347474243723822, text:'check the render', once:true}}`，且 **`typeof payload.issue_id === 'number'`**；4xx → `later-error` 可见且不调 `onScheduled`。

- [ ] **Step 7: 接入作曲区** —— `IssueReplyBox.tsx`：props 加 `issueId?: number`；页脚 `⌘↩ to send` 之后插入按钮（仅 `issueId` 存在时）：

```tsx
{issueId != null && (
  <div className="relative">
    <button type="button" data-testid="reply-later" onClick={() => setLaterOpen((v) => !v)} disabled={inputBlocked}
            className="inline-flex items-center gap-1 px-2 py-1 text-[12px] rounded text-ink-400 hover:bg-ink-800 hover:text-ink-200 disabled:opacity-40">
      <Clock size={11} /> {t('later.button', 'Later')}
    </button>
    {laterOpen && (
      <LaterPopover issueId={issueId} text={editor?.getText().trim() ?? ''} onClose={() => setLaterOpen(false)}
                    onScheduled={() => { setLaterOpen(false); onScheduled?.(); }} />
    )}
  </div>
)}
```

`IssueDetailView.tsx` 传 `issueId={issue.id}` 与 `onScheduled={() => setSchedulesRefresh((n) => n + 1)}`（`schedulesRefresh` 进 `blockCtx.env.refreshKey` 同族的新字段 `env.schedulesRefreshKey`，供右栏块重拉）。

- [ ] **Step 8: 右栏「Schedules」块** —— `SchedulesBlock.tsx`：`match: (ctx) => ctx.rollup !== null`、`zone: 'context'`、`order: 55`（Budget 之后、Links 之前，读 `blocks/index.ts` 现有 order 填实际值）；`useEffect` 拉 `listIssueSchedules(issueId)`（依赖 `ctx.env.schedulesRefreshKey`）；空列表**不渲染任何东西**（同 AttentionStrip 的空即消失口径）；每行 `schedule-row`（一次性显示 `fire_at` 本地时间 + `Once`，例行显示 `cron_expr` + `Routine`）、右侧 `schedule-cancel` 调 `schedulesService.remove(id)` 后本地剔除；底部 `+ Later` 按钮 `schedules-add` 打开同一个 `LaterPopover`（text 空 → 主按钮 disabled，提示 `later.needText`）。测试：两行渲染；点取消 → `DELETE /api/v1/schedules/sc-1` 被调且行消失；DELETE 失败 → 行还在且 `schedules-error` 出现。`blocks/index.ts` 加 `schedulesBlock` 到 `BUILTIN_ISSUE_BLOCKS`。

- [ ] **Step 9: 子 run 面板 + 唤醒芯片** —— `IssueChatThread.tsx`：`DetachedRunPanel` 加可选 `origin?: ChildRunOrigin`、`onBack?: () => void`，头部二选一：

```tsx
{origin ? (
  <div className="mb-1 flex items-center gap-2 text-[12px] text-agent">
    <span data-testid="child-run-header">
      {t('subagent.panelTitle', 'Sub-run #{{run}} · from run #{{parent}} step {{step}} · {{mode}}', {
        run: runId.slice(-6), parent: (origin.parentRunId ?? '').slice(-6) || '—', step: origin.step,
        mode: origin.mode === 'async' ? t('subagent.modeAsync', 'Background') : t('subagent.modeSync', 'Foreground'),
      })}
    </span>
    <button type="button" data-testid="child-run-back" onClick={onBack} className="ml-auto underline decoration-dotted">
      {t('subagent.backToParent', 'Back To Parent')}
    </button>
  </div>
) : ( …既有 replay.originRun 行… )}
```

`IssueDetailView` 持 `const [childRun, setChildRun] = useState<ChildRunOrigin | null>(null)`，用 `ChildRunContext.Provider` 包住既有 `ReplayContext.Provider`，并在 `detachedRunId` 面板旁渲染 `{childRun && <DetachedRunPanel runId={childRun.childRunId} origin={childRun} onBack={() => setChildRun(null)} />}`。
唤醒芯片：`RunTrajectory` 新增 prop `startedByWakeup?: boolean`，`run-header-chips` 内渲染 `run-wakeup-chip`（`t('schedule.startedBy', 'Started By Wake-up')`，info 色）；`AgentRunEvent` 从 `IssueChatThread` 的消息列表判定——**紧邻该 run 行之前的消息 `meta.source.kind === 'schedule'`**。定时发起的线程行是普通 `kind='comment'` 行、`author_user_id` 是规则所有者，**后端不加 `author_kind` 列**，标记全在既有 `meta JSONB` 里：`meta.source = {"kind":"schedule","schedule_id":"<uuid 字符串>","created_by":"user"|"agent"}`（`user_schedules.id` 是 UUID，mig 204）。`issueMessageService.IssueMessage` 的 `meta` 由 `Record<string, unknown>` 收窄为带可选 `source` 的交叉类型：

```ts
export interface IssueMessageSource { kind: string; schedule_id?: string; created_by?: string }
// IssueMessage：meta: Record<string, unknown> & { source?: IssueMessageSource };
export const startedByWakeup = (msg: IssueMessage | undefined): boolean => msg?.meta?.source?.kind === 'schedule';
```

测试（`meta` 按真实 wire 形状给**对象**，不是字符串；`schedule_id` 是 JSON **number**）：构造 `[{kind:'comment', author_user_id:'…', body:'check the render', meta:{source:{kind:'schedule', schedule_id: 4021, created_by:'user'}}}, {kind:'agent_run', agent_run_id:'9', meta:{}}]` → 芯片可见；把 `meta.source` 去掉 → 芯片消失；`meta.source.kind='comment'` → 也不出芯片。

- [ ] **Step 10: Quick「Scheduled N」芯片（含后端字段）** —— 后端：`Issue` 加 `pending_wakeups: int = 0`；`list_for_user` 在 `items` 组装后一次聚合（ORM，不写 `text()`）：

```python
if items:
    ids = [i["id"] for i in items]
    counts = await session.execute(
        select(UserSchedules.payload["issue_id"].astext.cast(BigInteger), func.count())
        .where(
            UserSchedules.task_type == "issue_wakeup",
            UserSchedules.enabled.is_(True),
            UserSchedules.payload["issue_id"].astext.cast(BigInteger).in_(ids),
        )
        .group_by(UserSchedules.payload["issue_id"].astext.cast(BigInteger))
    )
    by_issue = dict(counts.all())
    for i in items:
        i["pending_wakeups"] = int(by_issue.get(i["id"], 0))
```

`backend/tests/repositories/test_issue_list_pending_wakeups.py`：两条 issue、一条 enabled 唤醒 + 一条 `enabled=false` → 分别 1 / 0（真库集成用例挂 `INTEGRATION_DATABASE_URL`，无库时 skip，与 `test_assets_repository_integration.py` 同款）。
前端：`issuesService.Issue` 加 `pending_wakeups?: number`；`IssueListView` 的 `phaseCounts` 旁加 `const scheduledCount = useMemo(() => scopedIssues.filter((i) => (i.raw.pending_wakeups ?? 0) > 0).length, [scopedIssues])`，新增 state `scheduledOnly`，`filtered` 里 `if (scheduledOnly) …filter((i) => (i.raw.pending_wakeups ?? 0) > 0)`；Quick 行 `QUICK_PHASES.map` 之后渲染 `data-testid="quick-scheduled"` 芯片（`t('issues.quick.scheduled', 'Scheduled')` + 计数，`count === 0 && !active` 时 disabled，样式复用 `PHASE_TONE.paused` 的 info 调）。`IssueListView.test.tsx` 追加：两条 issue 一条带 `pending_wakeups: 1` → 芯片计数 1，点击后列表只剩那条，再点还原。

- [ ] **Step 11: Vitest 拆卸抖动（spec §4.3）** —— `AISettings.codexDaemon.test.tsx` 的 `aiService` mock 补一行：

```ts
  getAIGovernance: vi.fn().mockResolvedValue({
    chat: true, transcription: true, translation: true, visual_analysis: true,
    caption: true, classification: true, summarization: true, nous_enabled: false, nous_modules: {},
  }),
```

`tests/setup.ts` 在 storage 垫片之后加全局 fetch 桩（**同步拒绝**，不进事件循环，拆卸后没有待落定的 promise）：

```ts
// 单元测试碰网络本身就是 bug：未 mock 的 fetch 立即拒绝，错误落在发起它的
// 测试里，而不是在 jsdom 拆完之后变成 EnvironmentTeardownError（2026-09-10
// AISettings 拆卸抖动的根因）。自己装 mock 的测试照常覆盖这个桩。
Object.defineProperty(globalThis, 'fetch', {
  value: vi.fn((input: RequestInfo | URL) => Promise.reject(new Error(`fetch is disabled in unit tests: ${String(input)}`))),
  writable: true,
  configurable: true,
});
```

⚠️ `afterEach` 里的 `vi.restoreAllMocks()` 会把桩还原成原生 `fetch`，所以桩必须**每个测试前**重装：在同文件加 `beforeEach(() => { globalThis.fetch = disabledFetch; })`。验证：`cd frontend && npx vitest run`（worktree 先 `ln -s <主检出>/frontend/node_modules frontend/node_modules`）全绿且**无 `EnvironmentTeardownError`**，连跑三次记录；再单跑 `npx vitest run components/AISettings.codexDaemon.test.tsx`。

- [ ] **Step 12: i18n + parity + lint + 全量 + 突变**
  - en：`subagent: {waiting:"Waiting For Result", queued:"Background · Result Arrives In The Inbox", continued:"Continued From #{{run}}", open:"Open Run #{{run}}", panelTitle:"Sub-run #{{run}} · from run #{{parent}} step {{step}} · {{mode}}", modeSync:"Foreground", modeAsync:"Background", backToParent:"Back To Parent"}`；`schedule: {agentSet:"Agent scheduled a wake-up · {{at}}", cancel:"Cancel", cancelFailed:"Could not cancel that wake-up.", startedBy:"Started By Wake-up", once:"Once", routine:"Routine", title:"Schedules", add:"+ Later"}`；`later: {button:"Later", in1h:"In 1 Hour", tonight:"Tonight 20:00", tomorrow:"Tomorrow 09:00", custom:"Custom", confirm:"Schedule", needText:"Type the message this wake-up should send.", explain:"When it fires: if the agent is running it is picked up before its next step; if idle it starts a new turn with this text; if the issue is finished nothing is sent. Fires once.", error:"Could not schedule that wake-up."}`；`issueDetail.subagents/"Sub-agents"`、`issueDetail.subagentsBackground`、`issueDetail.wakesAt`；`trajectory.inboxSubagent`、`trajectory.inboxWakeup`；`issues.quick.scheduled/"Scheduled"`。zh 对应「等待结果 / 后台 · 结果将进收件箱 / 续聊自 #{{run}} / 打开 run #{{run}} / 子 run #{{run}} · 来自 run #{{parent}} 第 {{step}} 步 · {{mode}} / 前台 / 后台 / 回到父 run」「Agent 定了唤醒 · {{at}} / 取消 / 取消失败 / 由定时唤醒开始 / 一次性 / 例行 / 定时 / + 稍后」「稍后 / 1 小时后 / 今晚 20:00 / 明早 09:00 / 自定义 / 定时发送 / 先写一句到点要发的话 / 到点时：agent 在跑就插进下一步；空闲就用这句话开始新一轮；issue 已结束则不再发。只发一次。/ 定时创建失败」「子代理 / {{count}} 个后台 / {{at}} 唤醒 / 子代理结果 · {{type}} · 已在第 {{n}} 步前读到 / 定时唤醒 · {{who}} 设 · 已在第 {{n}} 步前读到 / 定时」。
  - 照 `replayI18nParity.test.ts` 复制出 `subagentI18nParity.test.ts`，正则改成 `(subagent|schedule|later)\.`。
  - `npx eslint <本 Task 改动的 tsx/ts>`；前端全量 `npx vitest run`；后端改动文件 `isort/black/ruff` + `uv run pytest -q backend/tests/repositories/test_issue_list_pending_wakeups.py`。
  - **突变**（各记一条，转红后还原）：① `subagent_done` 只在 `current` 步里找卡 → 「done 落在下一步」用例红；② `childrenState` 去掉 `total <= 0` 判定 → `total:0` 不出格用例红；③ `createIssueWakeup` 把 `issue_id` 写成 `String(issueId)` → wire 形状用例红；④ 去掉 `tests/setup.ts` 的 fetch 桩 → `AISettings.codexDaemon` 拆卸抖动可复现（记录一次真跑输出）。

- [ ] **Step 13: Commit**

```bash
git commit -am "feat(ui): 子代理卡与子 run 面板 + Cockpit 子代理格 + 稍后弹层与定时块 + Quick Scheduled 芯片 + Vitest 拆卸修（harness 二期 2b-2 Task 6）"
```

---

### Task 7: 真栈验收 + 翻开关 + 回填 + 完成账

**Files:**
- Modify: `backend/config.yml`（`FEATURE_WORKFORCE_DELEGATE: false → true`，**单独 PR**）
- Modify: 本计划（末尾「完成账」）、`docs/superpowers/specs/2026-09-10-harness-p4-phase2b2-orchestration-design.md`（偏差回写）
- Modify: `CLAUDE.md`（仅当发现新陷阱）；记忆 `project-harness-p4-phase2b1-in-progress`（改写为 shipped + 2b-2 状态）

**前置口径（照抄，不要重新发现）**：生产 API `https://cn.nous.ink:88`（`frontend/.env.production` 第 16 行 `VITE_API_URL`，已核）；调试账号凭据 `~/.nous/claude-debug.env`，anon key 在 `frontend/.env.production`；取 token：

```bash
source ~/.nous/claude-debug.env
TOKEN=$(curl -sS "$SUPABASE_URL/auth/v1/token?grant_type=password" -H "apikey: $ANON_KEY" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"$DEBUG_TEST_EMAIL\",\"password\":\"$DEBUG_TEST_PASSWORD\"}" | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
API=https://cn.nous.ink:88/api/v1
AGENT=$(curl -sS "$API/ai-library/agents?slug=script_ai" -H "Authorization: Bearer $TOKEN" | python3 -c 'import sys,json;d=json.load(sys.stdin);print((d["items"] if isinstance(d,dict) else d)[0]["id"])')
mk() { curl -sS -X POST "$API/issues/" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"title\":\"$1\",\"description\":\"$2\",\"team_id\":331438215859255,\"assignee_agent_id\":\"$AGENT\"}"; }
# 派发：curl -sS -X POST "$API/issues/<id>/dispatch" -H "Authorization: Bearer $TOKEN"
```

DB / 探针（SSH 必须带 `-i ~/.ssh/id_macmini`）：

```bash
SSH="ssh -i ~/.ssh/id_macmini heygo@10.0.0.10"
$SSH 'docker exec nous-worker curl -sS http://localhost:8080/api/v1/readyz'
$SSH 'docker exec nous-db psql -U postgres -p 55434 -d postgres -c "<SQL>"'
```

- [ ] **Step 1: 部署确认**：T1–T6 每个 PR 合并后 `gh run list --workflow=deploy-gpu.yml --limit 3` 到 `completed success`；`readyz` 回 `{"status":"ready","dbos":"enabled"}`；容器内逐 Task 抓一个标识符证明代码真在里面（`docker exec nous-backend grep -c "def inbox_dispatch_workflow" /app/app/workflows/workforce_dispatch.py`、`grep -c "def claim_task" /app/app/repositories/agent_workforce_repository.py`、`grep -c "issue_wakeup" /app/app/workflows/scheduled_master.py`、`grep -c "class ScheduleWakeup" /app/app/services/ai/tools/*.py`）；前端 `curl -sS https://app.nous.ink/version.json` 的 `commitSha` = T6 的 merge SHA。

- [ ] **Step 2: 翻 `FEATURE_WORKFORCE_DELEGATE`**：没有 staging，所以顺序是「链路测试绿 → 单独翻开关 PR → 立刻验 ①」。翻之前先确认生效前状态（`docker exec nous-backend /app/.venv/bin/python -c "from app.core.config import settings; print(settings.FEATURE_WORKFORCE_DELEGATE)"` → `False`），合并部署后同一条命令 → `True`。⚠️ `secrets/backend.env` 若已有同名行会静默盖掉 `config.yml`：`docker exec nous-backend printenv | grep -i WORKFORCE`，有输出就先删那一行（CLAUDE.md 部署陷阱第一条）。

- [ ] **Step 3: ① Delegate(await=false) 端到端**：建 issue 让 `script_ai` 调 `Skill(skill="task", await=false, subagent_type="general-purpose", description="…")`。证据链四段，每段留原文：
  - 任务落库：`SELECT id, task_kind, phase, metadata->>'dispatched_at' FROM task_tracking WHERE task_kind='agent_task' ORDER BY created_at DESC LIMIT 3;` —— 建出来时 `dispatched_at` 为 NULL；
  - ≤10s 后同一条 `dispatched_at` 非空，且 `SELECT status FROM dbos.workflow_status WHERE workflow_uuid='workforce-<task_id>';` 存在；
  - 完成后 `SELECT lifecycle FROM agent_tasks_outbox…`（实际表名以 T3 实现为准）有 outbox 行；
  - `docker exec nous-worker curl -sS http://localhost:8080/api/v1/healthz` 里 `inflight_count` 与 `SELECT count(*) FROM task_tracking WHERE task_kind='agent_task' AND phase IN ('queued','in_progress')` 一致（⑧ 的一半）。

- [ ] **Step 4: ② 后台子代理回父 run**：同一 issue，父 run 事件 `SELECT seq, event_type, payload->>'mode', payload->>'child_run_id' FROM agent_run_transcript_events WHERE run_id=<parent> ORDER BY seq;` 应见 `subagent_spawned{mode:async}`；子 run 行 `SELECT id, parent_run_id, issue_id FROM agent_runs WHERE parent_run_id=<parent>;`；issue 空闲后收件箱触发新一轮 → 新 run 事件里 `inbox_claimed{kind:subagent_result}`，父 run 上有 `subagent_done`；`view.children` 经 `GET /ai-library/runs/<parent>/view-at?seq=<末尾>` 读出 `{total,done,running,async_pending,last}` 与事件计数对得上。

- [ ] **Step 5: ③ 续聊**：对 ② 的子 run 再 `Skill(skill="task", child_run_id="<child>", prompt="…")` 一次 → 新子 run `SELECT id, fork_of_run_id, parent_run_id FROM agent_runs WHERE fork_of_run_id=<child>;` 有行；父 run 事件 `subagent_spawned{continued_from:"<child>"}`；新子 run 的首批事件里能看到被重建的历史消息（`SELECT count(*) FROM agent_run_transcript_events WHERE run_id=<new_child> AND event_type IN ('user','assistant')`）。

- [ ] **Step 6: ④ 定时唤醒三态**：
  - 空闲到点：UI「⏰ Later → Custom」设 +2 分钟（或 `POST /schedules` 直调），到点后 `SELECT kind, body, meta->'source' FROM issue_messages WHERE issue_id=<id> ORDER BY created_at DESC LIMIT 2;` 见 `meta->'source'->>'kind' = 'schedule'` 的 comment 行，紧随一条新 run；新 run 首个 `user` 事件文本 = 唤醒文本；页面该 run 行头出 `Started By Wake-up`。
  - 运行中到点：先派发一轮长任务，再设 +1 分钟 → 新 run 事件里 `inbox_claimed{kind:'steer', content.source.kind:'schedule'}`。
  - agent 自设：提示模型用 `ScheduleWakeup` → 父 run `schedule_set{schedule_id, fire_at, note}`；`GET /issues/<id>/schedules` 列出该行；右栏块点取消 → `DELETE /schedules/<id>` 204 且 `SELECT enabled FROM user_schedules WHERE id='<id>'` 为 false 或行已删。
  - 一次性自禁：到点后 `SELECT enabled, pause_reason FROM user_schedules WHERE id='<id>';` → `false / fired_once`。

- [ ] **Step 7: ⑤ 回填 + ⑥ 派发窗口**：
  - 回填先 dry-run 后真跑，两次都记行数：`curl -sS -X POST "$API/admin/backfill" -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' -d '{"name":"agent_runs_issue_id","limit":5000,"dry_run":true}'` → 拿 `workflow_id`，结果在 `SELECT phase, metadata FROM task_tracking WHERE dbos_workflow_id='<wf>'`；`dry_run:false` 重跑；前后对照 `SELECT count(*) FROM agent_runs r JOIN issues i ON r.conversation_id=i.ai_session_id WHERE r.issue_id IS NULL;` 应归零。⚠️ 该端点走 `AdminAuthDep`，调试账号若非管理员则用 gpupc 上的管理员 token，拿不到就记为「未验 + 原因」，不要伪造。
  - ⑥：`POST /issues/<id>/dispatch` 后 **3 秒内** `POST /ai-library/runs/<run>/fork` → 期望 409 `issue_busy`（旧行为是静默 `{"skipped":true}`）；60 秒后同样调用不再 busy（TTL 过期）。

- [ ] **Step 8: ⑦ 前端走查**：`cd frontend && npm run e2e:prod` 全过；再用临时 Playwright spec（**不入库**，理由同 2b-1：它建真 issue、花真钱）以 `frontend/e2e-prod/helpers.ts` 的 `loadProdCreds` 登录，访问 `/team/331438215859255/todolist/MH-<n>`，全部断言用 `toBeVisible`，截图到 `~/Downloads/2b2-ui-*.png`：① 子代理三态卡 + `subagent-open` 打开子 run 面板（头部含 `Sub-run #… · from run #… step N · Background`、`Back To Parent` 可点回）；② 收件箱行 `Sub-agent result · …`、Cockpit `cockpit-children` 读 `1/2 · 1 in background`；③ 作曲区 `Later` 弹层四个预设 + 说明句，定时后右栏 `Schedules` 块出现该行并可取消，Cockpit 副行 `Wakes at …`；④ 主页 Quick `Scheduled 1` 芯片可筛。与画板「二期 2b-2 · 编排（浅色）」两块稿逐项对照，差异写进完成账。

- [ ] **Step 9: 完成账 + 回写 + 记忆**：
  - 本文件末尾追加「完成账」表：每个 Task 的 PR → merge SHA → 上线证据（容器内 grep / version.json）；⑧ 项验收表逐条给证据（run id / seq / SQL 输出 / 截图名），未验项写原因而不是留空。
  - 偏差全部回写 spec 与本计划的「实施记录」（尤其：`meta.source` 的实际键名与 `schedule_id` 类型、`POST /schedules` 是否接受无 `name` 的 body、`pending_wakeups` 最终落在哪个响应）。
  - 记忆：把 `project-harness-p4-phase2b1-in-progress` 改写为「2b-1 已上线并验收完毕 + 2b-2 状态」，下期入口写「第 3 期 产出与账」；`FEATURE_WORKFORCE_DELEGATE` 已翻 true 这条要单独一行（它改变生产行为，下次会话必须知道）。
  - Discord 通知按 CLAUDE.md 为**可选**：MCP 未登录就在回复里说明，不重试。

---

## 自审记录（写完后对照 spec，2026-09-10）

- **覆盖**：spec §1 五条（四根断线 + 开关迁 settings + 删死组件 + 链路测试 + DROP `agent_tasks`）→ T1/T3；§2 后台/续聊/事件/折叠/投递/边界 → T4；§3 `issue_wakeup` / `_fire_issue_wakeup` / `ScheduleWakeup` / 白名单单一来源 / `GET /issues/{id}/schedules` → T5，主页 Quick 所需的 `pending_wakeups` 列表字段 → T6；§4.1 派发窗口、§4.2 `issue_id` 创建即写 + 回填 → T2；§4.3 Vitest → T6；§5 两块稿 → T6；§7 真栈八项 + 翻开关 + 跑回填 → T7。无遗漏。
- **占位符**：扫过 TBD / TODO / 「类似 Task」/「适当」，零命中；136 个代码围栏成对。
- **类型一致**：`deliver_or_dispatch(issue_id, *, kind, content, user_id, message_body=None, source=None, already_enqueued=False) -> DeliverResult` 在 T4 定义、T5 调用一致；`is_dispatching(issue, *, now=None, ttl_s=60)` T2 定义、T4 的 `deliver_or_dispatch` 调用一致；`claim_task / list_undispatched_queued_tasks / mark_dispatched / count_inflight_agent_tasks` 只在 T3；`schedule_id` 统一为 UUID 字符串（`user_schedules.id`，mig 204）——T6 的 `IssueMessageSource.schedule_id` 已改 `string`；事件 payload 与 `view.children / view.wakeups / cost.by_child` 三段用同一份形状（T4 Interfaces 为准）。
- **与 spec 的已定偏差**已回写 spec §0「写 plan 时的偏差」（`author_kind` → `meta.source`、`user_messages`、`agent_payload`、`already_enqueued`、`ScheduleCreatePayload` 可选字段、dict detail、`last_issue_id`、`pending_wakeups`、`tasks_enqueued` 假键、健康端点恒 down、`LATEST_MIGRATION` 钉桩、`agent_tasks` 模型同 PR）。
