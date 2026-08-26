# harness 借鉴第二期实施计划：任务跟踪与完成度可视化

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.
> 新 session 认领方式："执行 harness 二期计划 Task N"。

**Goal:** 让 agent 的任务进度（todo 完成度）、压缩生命周期、turn 结束原因、
重试进度对用户可见且崩溃可审计。

**Architecture:** 真相源 = `agent_run_transcript_events` 整值快照事件
（dsh 整值规则：推完整状态绝不推裸 delta）；UI 消费面 = 镜像进
`agent_runs.metadata_json`（业务字段，随既有 Realtime 推整行，零新推送通道）；
外加一个只读事件端点补 W1 欠账。

**Tech Stack:** FastAPI + SQLAlchemy ORM（裸 SQL 禁令）、DBOS、React 19 + vitest。

**Spec:** `docs/superpowers/specs/2026-08-25-harness-phase2-task-visibility-design.md`
（本计划从 spec 论证而来；执行者两份都读。**spec §0 的每条"证据"在开工时复核**——
第一期五波里四波的计划与实况不符，这条纪律就是从那来的。）

## Global Constraints

- 一波一 worktree 一 PR（`bash scripts/worktree-manager.sh create <branch>` 后
  `git reset --hard origin/master`——manager 从当前 HEAD 建树）。
- 迁移 PR 与读写它的代码 PR **分开**；迁移取号前 `git fetch` 并扫全部分支占号。
- 遥测/事件写入**永不失败业务路径**：record/镜像失败只 `logger.warning`。
- 边界 mock 用真实 wire 形状（CLAUDE.md 血泪条款）。
- 每个守卫做突变复做（拆掉→红→还原→绿），结果写进 commit message。
- UI 文本英文、无 emoji；新色走语义 token。
- 后端改动跑 `uv run pytest tests/ -q` 全量；前端跑涉及文件的
  `npx vitest run <files>` + `npm run typecheck`（忽略存量错误，见 spec §8）。
- commit / push / PR 按仓库惯例（squash 合并；堆叠 PR 非末尾不删分支）。

---

### Task 1: W3-1 收尾 —— warm-prefix 接进 compactor 与 runner

**现成工作区**（不要新建）：`.worktrees/feat-harness-w3-warm-prefix`
（分支 `feat/harness-w3-warm-prefix`，基 ca1dd44c，落后 master 若干——先
`git fetch origin master && git rebase origin/master`，rebase 后重跑 11 测确认仍 8 绿 3 红）。

**Files:**
- Modify: `backend/app/agent_framework/context_compactor.py`（`maybe_compact` 与
  `_compact_with_summary` 签名）
- Modify: `backend/app/services/ai/runner/agent_runner.py`（`maybe_compact(` 调用点，约 :1088）
- Test（已存在，3 红）: `backend/tests/agent_framework/test_warm_prefix_summarize.py`

**Interfaces:**
- Consumes: `summarize_warm_prefix(*, adapter, system_message, tools, head, model, max_tokens=600) -> str`
  （已实现于 `summarizer.py`，raise RuntimeError 于空文本/tool-call/超时/形状错）
- Produces: `ContextCompactor.maybe_compact(*, system_message, user_messages, model, adapter=None, tools=None)`；
  `_compact_with_summary(*, messages, keep_recent_turns, model, system_message=None, tools=None, adapter=None)`

- [ ] **Step 1: 确认红灯基线**

Run: `cd backend && uv run pytest tests/agent_framework/test_warm_prefix_summarize.py -q`
Expected: `3 failed, 8 passed`（失败的三个名字见 spec §6）

- [ ] **Step 2: `_compact_with_summary` 加 warm→legacy 链**

在收敛循环（`for attempt in range(1, self.SUMMARY_ATTEMPTS + 1)`）内，把
`summary_text = await summarize(head)` 替换为：

```python
summary_text = await self._produce_summary(
    head, system_message=system_message, tools=tools,
    adapter=adapter, model=model,
)
```

新增私有方法（放 `_compact_with_summary` 之后）：

```python
async def _produce_summary(
    self, head, *, system_message, tools, adapter, model
) -> str:
    """Warm-prefix first, legacy cheap-model second.

    The chain is warm → legacy → (caller's) emergency cap: a warm hiccup
    must not skip straight to lossy truncation. No adapter / no system
    message → straight to legacy (background paths that never had one)."""
    from app.agent_framework import summarizer

    if adapter is not None and system_message:
        try:
            return await summarizer.summarize_warm_prefix(
                adapter=adapter, system_message=system_message,
                tools=tools, head=head, model=model,
            )
        except Exception as exc:
            logger.warning(
                "[compactor] warm-prefix summarize failed, falling back "
                "to the maintenance model: {}", exc,
            )
    return await summarizer.summarize(head)
```

注意：测试 patch 的是 `app.agent_framework.summarizer.summarize`（模块属性），
所以这里必须 `from app.agent_framework import summarizer` 后取属性调用，
不能 `from ... import summarize`（那会把函数对象钉死在本模块，patch 失效）。

- [ ] **Step 3: `maybe_compact` 与 runner 透传**

`maybe_compact` 签名追加 `adapter=None, tools: Optional[list] = None`，
orange/red 分支调用 `_compact_with_summary(..., system_message=system_message, tools=tools, adapter=adapter)`。
runner 调用点（`_preflight_compact_and_budget` 内）改为：

```python
user_messages, compaction_stats = await _DEFAULT_COMPACTOR.maybe_compact(
    system_message=composed.system_message,
    user_messages=user_messages,
    model=composed.model,
    adapter=self.adapter,
    tools=composed.tools,
)
```

- [ ] **Step 4: 全绿确认**

Run: `uv run pytest tests/agent_framework/ tests/test_context_compactor.py -q`
Expected: 全绿（含原 3 红）。

- [ ] **Step 5: 突变复做**

把 `_produce_summary` 的 warm 分支删成直通 legacy → 
`test_compactor_uses_warm_prefix_when_it_has_an_adapter` 必须红；还原。
把 except 吞掉改为 re-raise → `test_warm_failure_falls_back_to_legacy_then_succeeds` 必须红；还原。

- [ ] **Step 6: lint + 后端全量 + commit + PR（base=master）**

```bash
uv run black <改动文件> && uv run isort <改动文件> && uv run flake8 <改动文件>
uv run pytest tests/ -q
git add -A && git commit   # message 记录突变结果与"成本挪移已拍板"
git push -u origin feat/harness-w3-warm-prefix
gh pr create --base master --title "feat(compaction): W3-1 摘要复用 warm prefix — 前缀逐字重放,同模型同账户" --body-file <生成的说明>
```

---

### Task 2: migration 439 —— 本期事件类型一次放行

**Files:**
- Create: `supabase/migrations/439_transcript_event_types_phase2.sql`
- Modify: `backend/app/models/agents.py`（`AgentRunTranscriptEvents.__table_args__` 的
  CheckConstraint 同步，位于 :505 附近）

**Interfaces:**
- Produces: event_type 白名单新增 `todo_write` / `compaction_start` /
  `compaction_summary` / `compaction_end` / `turn_end`（Task 3/5/6 依赖）

- [ ] **Step 1: 取号复核**（439 是写计划时的下一空号，执行时必须重扫）

```bash
git fetch origin master
git ls-tree --name-only origin/master supabase/migrations/ | grep -oE '/[0-9]+' | grep -oE '[0-9]+' | sort -n | tail -1
for b in $(git branch -a --format='%(refname:short)' | grep -v HEAD); do
  git ls-tree --name-only "$b" supabase/migrations/ 2>/dev/null | grep -E "/(439|440)_"; done
```

- [ ] **Step 2: 写迁移**（幂等：DROP IF EXISTS 再 ADD；不写 SET ROLE）

```sql
BEGIN;
ALTER TABLE public.agent_run_transcript_events
  DROP CONSTRAINT IF EXISTS agent_run_transcript_events_event_type_check;
ALTER TABLE public.agent_run_transcript_events
  ADD CONSTRAINT agent_run_transcript_events_event_type_check
  CHECK (event_type = ANY (ARRAY[
    'user'::text,'assistant'::text,'tool_call'::text,'error'::text,
    'system'::text,'llm_retry'::text,
    'todo_write'::text,
    'compaction_start'::text,'compaction_summary'::text,'compaction_end'::text,
    'turn_end'::text
  ]));
COMMIT;
```

头部注释写清每个类型是什么、为什么不复用 error（语义占坑教训，照 436 的口径）。

- [ ] **Step 3: 影子演练四件套**（生产库临时表 + ROLLBACK，照 436 的脚本形）

旧约束拒 `todo_write` ✅ / 连跑两遍幂等 ✅ / 新约束收五个新值 ✅ / 乱值 `bogus` 仍拒 ✅

- [ ] **Step 4: ORM 同步 + commit + PR（迁移单独 PR，base=master，合并后不删分支若有堆叠）**

合并后验收：workflow 绿**不算数**，对生产库真实 INSERT 一条 `todo_write` 再 ROLLBACK。

---

### Task 3: todo 事件 + 快照镜像（后端）

**Files:**
- Modify: `backend/app/services/ai/skills/skill_tool_service.py`
  （`_execute_todo_impl`，:137 起；`SkillToolService.__init__` 增 `self.recorder=None`）
- Modify: `backend/app/services/ai/runner/agent_runner.py`
  （run_turn 里构建/持有 skill_tool 处，把 recorder 交给它——找
  `skill_tool` 的装配点，与 W1-B 的 on_retry 挂载同一段）
- Create: `backend/app/services/ai/runner/todo_events.py`
- Test: `backend/tests/test_todo_events.py`

**Interfaces:**
- Consumes: `RunRecorder.record_event(event_type: str, payload: dict)`（已存在）；
  `AgentTodoList.items: list[TodoItem]`、`TodoItem{id,content,status,active_form}`、
  `completed_count()`（已存在，agent_todo.py）
- Produces: `emit_todo_snapshot(recorder, todo_list) -> None`（异步，永不 raise）；
  `agent_runs.metadata_json.todos = {"todos":[...], "counts":{...}}` 镜像；
  payload 形状见 spec §2.1（Task 4 前端按此消费）

- [ ] **Step 1: 红灯**（`backend/tests/test_todo_events.py`）

```python
"""todo_write — the whole-list snapshot that makes agent progress durable.

Whole-value rule (dsh, first-phase W2): push the complete post-change
state, never a bare delta. The current list is the LAST todo_write event."""
import pytest
from unittest.mock import AsyncMock

from app.agent_framework.agent_todo import AgentTodoList
from app.services.ai.runner.todo_events import emit_todo_snapshot


def _list():
    tl = AgentTodoList()
    tl.replace([{"content": "step A"}, {"content": "step B", "active_form": "doing B"}])
    tl.mark(2, "in_progress")   # 执行时核对真实 mutation API 名，以 agent_todo.py 为准
    return tl


@pytest.mark.asyncio
async def test_snapshot_is_whole_list_with_counts():
    rec = AsyncMock()
    await emit_todo_snapshot(rec, _list())
    et, payload = rec.record_event.await_args.args
    assert et == "todo_write"
    assert [t["content"] for t in payload["todos"]] == ["step A", "step B"]
    assert payload["counts"] == {"total": 2, "completed": 0, "in_progress": 1}


@pytest.mark.asyncio
async def test_none_recorder_is_a_no_op():
    await emit_todo_snapshot(None, _list())   # must not raise


@pytest.mark.asyncio
async def test_recorder_failure_never_breaks_the_tool():
    rec = AsyncMock()
    rec.record_event.side_effect = RuntimeError("sink down")
    await emit_todo_snapshot(rec, _list())    # must not raise


@pytest.mark.asyncio
async def test_show_op_does_not_emit():
    """Reads are not writes — a snapshot per `show` floods the log with
    identical frames."""
    # 驱动 _execute_todo_impl(op="show")，断言 recorder 未被调用
```

- [ ] **Step 2: 实现 `todo_events.py`**

```python
async def emit_todo_snapshot(recorder, todo_list) -> None:
    if recorder is None or todo_list is None:
        return
    try:
        items = [
            {"id": t.id, "content": t.content, "status": t.status.value,
             "active_form": t.active_form}
            for t in todo_list.items
        ]
        counts = {
            "total": len(items),
            "completed": sum(1 for t in items if t["status"] == "completed"),
            "in_progress": sum(1 for t in items if t["status"] == "in_progress"),
        }
        await recorder.record_event("todo_write", {"todos": items, "counts": counts})
    except Exception as exc:
        logger.warning(f"[todo_events] snapshot not recorded: {exc!r}")
```

- [ ] **Step 3: 接线** —— `_execute_todo_impl` 三个变更 op 的成功返回前
  `await emit_todo_snapshot(svc.recorder, svc.todo_list)`；runner 在挂 on_retry 的
  同一段把 `self.skill_tool.recorder = recorder`（turn 结束 finally 同步置 None，
  防跨 run 串台——照 W1-B on_retry 的摘除纪律，测试同款钉）。
  ⚠️ `_execute_todo_impl` 现为同步函数则升 async——执行时核实调用方是否已 await。

- [ ] **Step 4: 镜像** —— `RunRecorder.record_event` 内特判：`event_type == "todo_write"`
  时顺手 `UPDATE agent_runs SET metadata_json = jsonb_set(...todos...)`（走既有 ORM
  update 模式，参考 `mark_empty_output`）；失败 warning 不影响事件。断言镜像的测试
  mock 真实 ORM 边界。

- [ ] **Step 5: 突变复做 + lint + 全量 + commit + PR（base=migration 439 分支或 master——
  以 439 是否已合并定；堆叠则遵守非末尾不删分支）**

突变：拆掉 whole-list（只发 delta）→ counts 断言红；拆掉 finally 摘 recorder → 串台测试红。

---

### Task 4: 完成度上 UI（前端）

**Files:**
- Modify: `frontend/components/TaskCenter/agentRunPresentation.ts`（+ 其 test）
- Modify: `frontend/components/TaskCenter/ActiveTaskCard.tsx`（+ 其 test）
- Modify: `frontend/components/TaskCenter/TaskDetailModal.tsx`（展开列表）

**Interfaces:**
- Consumes: `metadata_json.todos = {"todos":[{id,content,status,active_form}], "counts":{total,completed,in_progress}}`
- Produces: `todoProgress(meta): {label: string, done: number, total: number} | null`

- [ ] **Step 1: 红灯**（agentRunPresentation.test.ts 增块）

```ts
it('renders "3/7 · doing B" from the todo snapshot', () => {
  const p = todoProgress({ todos: { todos: [
    { id: 1, content: 'step A', status: 'completed', active_form: null },
    { id: 2, content: 'step B', status: 'in_progress', active_form: 'doing B' },
  ], counts: { total: 7, completed: 3, in_progress: 1 } } });
  expect(p).toEqual({ label: 'doing B', done: 3, total: 7 });
});
it('falls back to content when active_form is null', () => { /* label === 'step B' 当 active_form 缺 */ });
it('returns null when no snapshot ever landed', () => {
  expect(todoProgress({})).toBeNull();   // 旧 run 无 todos 键 —— 不渲染,不猜
});
it('malformed counts render nothing rather than NaN/7', () => { /* counts 缺 total → null */ });
```

- [ ] **Step 2: 实现 + 卡片渲染**（`{done}/{total} · {label}`，无 todo 的 run 完全不占位）
- [ ] **Step 3: TaskDetailModal 展开整表**（status 三态用既有语义 token 上色；不 emoji）
- [ ] **Step 4: vitest + typecheck + commit + PR（与 Task 3 分 PR——前后端可独立回滚）**

---

### Task 5: 压缩事件括号

**Files:**
- Modify: `backend/app/agent_framework/context_compactor.py`（`maybe_compact` 增可选
  `recorder=None`；orange/red 分支落三事件）
- Modify: `backend/app/services/ai/runner/agent_runner.py`（调用点补 `recorder=recorder`）
- Test: `backend/tests/agent_framework/test_compaction_events.py`

**Interfaces:**
- Consumes: `recorder.record_event`；Task 2 的三个 event_type
- Produces: spec §3 的三事件 payload（孤儿检测口径：有 start 无同 run 后继 end = 崩溃现场）

- [ ] **Step 1: 红灯** —— 关键断言（每条独立测试）：
  start 在 summarize 调用**之前**落（顺序表法，照 W1-B `test_the_event_lands_before_the_wait`）；
  成功路径三事件齐且 end 带 tokens_saved；summarize 全败走 emergency cap 时 summary 事件
  path="emergency_cap"；**摘要抛到底时 end 仍落且带 error**（try/finally）；
  recorder=None 全程静默；green/yellow 档零事件。
- [ ] **Step 2: 实现**（start 同步先落；end 放 finally）
- [ ] **Step 3: 突变** —— end 挪出 finally → "失败仍落 end"红；start 挪到 summarize 后 → 顺序测试红。
- [ ] **Step 4: lint/全量/commit/PR**

---

### Task 6: TurnEndReason

**Files:**
- Create: `backend/app/services/ai/runner/turn_end.py`（spec §4 的枚举原文）
- Modify: `backend/app/services/ai/runner/agent_runner.py`（每个出口标定 + 落
  `turn_end` 事件 + 镜像 `metadata_json.turn_end_reason`）
- Modify: `frontend/components/TaskCenter/agentRunPresentation.ts`（completed 态副标题）
- Test: `backend/tests/test_turn_end_reason.py` + presentation test

**Interfaces:**
- Produces: `TurnEndReason` 枚举（spec §4 六值原文）；事件 payload
  `{reason, iterations, finish_reason?}`；前端 `turnEndSubtitle(meta): string | null`
  （completed+max_iterations → "Stopped at tool limit"；provider_length →
  "Cut off by model limit"；completed → null 即默认文案）

- [ ] **Step 1: 红灯** —— 六个出口各一测（正常 stop / preflight 拒 / cancelled / abort /
  max_iterations / 异常）驱动 run_turn 假 adapter 断言事件 reason；
  **穷尽守卫**：源码扫描 run_turn/stream_turn 的 `return {`/`yield` 终点数 vs 标定数
  （照 frame-guard 范式，路径存在断言防扫空）。
- [ ] **Step 2-4: 实现 / 突变（漏标一个出口→穷尽守卫红）/ lint 全量 commit PR**

---

### Task 7: W1 读侧 —— 事件端点 + 重试进度 + errorChain

**改判（2026-08-25 复核）：事件端点已存在**（`ai_library_router.py:2335`
`list_run_events`，after_seq/limit、外人 404）。本任务不建新端点。

**Files:**
- Modify: `backend/app/api/ai_library_router.py`（`list_run_events` 加
  `types: str = ""` CSV 过滤，空=全部保持向后兼容）
- Modify: `backend/app/services/ai/llm/retry_events.py`（镜像 last_retry）
- Modify: `backend/app/services/ai/llm/llm_retry_middleware.py`（errorChain：
  `describe_llm_error` 拼 `__cause__` 链，每层 `f"{type(c).__name__}: {c}"`，
  总长守 `_BODY_SNIPPET_MAX` 同族预算）
- Modify: `frontend/components/TaskCenter/ActiveTaskCard.tsx`（"Retry 2/4 · waiting 3.2s"）
- Test: `backend/tests/api/test_agent_run_events_router.py`、middleware/前端各自 test 文件

**Interfaces:**
- Produces: 既有 `GET /api/v1/ai-library/runs/{run_id}/events` 增
  `types=<csv>`（如 `types=todo_write,llm_retry`）；响应形状不变。
  鉴权已是所有者-404 口径，不动。

- [ ] **Step 1: 红灯** —— `types=todo_write` 只回该类型；`types=""`/缺省回全部
  （向后兼容——useRunToolActivity 等既有消费者一行不改仍工作，此测必写）；
  乱值类型静默空集不 500。
- [ ] **Step 2: 实现端点 + last_retry 镜像 + errorChain**（cause 链测试：三层嵌套异常
  → error_message 含三层名+文案且不超预算）。
- [ ] **Step 3: 前端 Retry 进度**（读 `metadata_json.last_retry`；无则不渲染）。
- [ ] **Step 4: 突变/lint/全量/commit/PR（后端前端分 PR）**

---



---

### Task 8: Issues 详情页消费（spec §9）

**Files:**
- Modify: `frontend/components/Todolist/IssueDetailView.tsx`
  （PROGRESS 栏 reason 行，:436-460 的 NeedsInputCard 门旁；勿动 needs_input 既有路径）
- Create: `frontend/components/agentActivity/useRunTodoProgress.ts`
  （照同目录 `useRunToolActivity.ts` 的轮询/停机范式）
- Modify: `frontend/components/Todolist/IssueChatThread.tsx`
  （run 卡 `RunToolActivity` 旁挂 todo 进度 + retry 行）
- Test: `IssueDetailView.test.tsx` 增块、`useRunTodoProgress.test.ts`

**Interfaces:**
- Consumes: Task 2/3 的 `todo_write` payload（spec §2.1 形状）；Task 7 的
  `types` 过滤（**可选**——没合并时客户端过滤同样正确，只是多拖数据；两种都可发）；
  `issue.raw.execution_state.{agent_outcome, outcome_reason}`（已在行上）。
- Produces: `useRunTodoProgress(runId, isRunning): {done,total,label} | null`；
  reason 行文案键 `issueDetail.stoppedReason.*`（en/zh locales 同步）。

- [ ] **Step 1: 红灯（reason 行）**

```tsx
it('a blocked issue shows the recorded reason', () => {
  render(<IssueDetailView issue={mk({ status: 'blocked',
    execution_state: { agent_outcome: 'empty_output',
      outcome_reason: 'Agent produced no output (EMPTY_OUTPUT)' } })} />);
  expect(screen.getByText(/produced no output/)).toBeInTheDocument();
});
it('needs_input keeps flowing through NeedsInputCard, not duplicated', () => {
  /* status=needs_followup+needs_input → reason 行不渲染,NeedsInputCard 渲染(既有测试保持绿) */
});
it('no reason recorded renders nothing — never an empty label', () => { /* execution_state null → 无该行 */ });
```

- [ ] **Step 2: 红灯（todo 进度 hook）** —— 事件序列 [3 条 todo_write] 取**最后一条**；
  零事件 → null；isRunning=false 停轮询（照 useRunToolActivity 的既有停机断言抄形）。
- [ ] **Step 3: 实现 + 挂载**（timeline run 卡 + PROGRESS 栏活跃 run 行）。
- [ ] **Step 4: 突变** —— 取第一条快照而非最后一条 → last-write-wins 测试红。
- [ ] **Step 5: vitest + typecheck + i18n 双语 + commit + PR**

⚠️ blocked 写入方核对（spec §9 不确定点）：`grep -rn "'blocked'" backend/app | grep -v test`
——若来源是依赖谓词，reason 行改落依赖名，测试相应换形。

## 波次与 PR 依赖

```
Task 1 (W3-1)          独立,最先 —— 在途工作区接完
Task 2 (mig 439)       独立 —— 合并后其余任务才能落事件
Task 3 (todo 后端)  ─┐
Task 5 (压缩括号)     ├─ 依赖 Task 2;彼此独立,可并行(各自 worktree)
Task 6 (turn_end)   ─┘
Task 4 (todo 前端)     依赖 Task 3 合并(消费 metadata 形状)
Task 7 (W1 读侧)       types 过滤独立可先行;errorChain 独立
Task 8 (Issue 详情页)  reason 行完全独立可先行;todo 进度依赖 Task 3(+可选 Task 7)
```

## Self-Review 已做

- spec 逐节对照：§1→Task1、§2→Task2/3/4、§3→Task5、§4→Task6、§5→Task7、
  §6→Task1 工作区、§7 非目标无任务（正确）。
- 类型一致性：`todoProgress`/`emit_todo_snapshot`/`TurnEndReason` 各任务间签名一致；
  todo payload 形状 Task 3 Produces == Task 4 Consumes。
- 已知不确定点（刻意留给执行者复核而非假装确定）：`AgentTodoList` 的 mutation 方法名
  （Task 3 Step 1 注明以 agent_todo.py 为准）；`_execute_todo_impl` 同步/异步；
  439 号是否仍空闲。这三处都写了核对指令。
