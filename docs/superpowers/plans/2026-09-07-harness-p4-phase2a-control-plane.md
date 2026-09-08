# harness 第四轮 · 二期 2a「控制面」实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> 认领方式："执行 harness 二期 2a Task N"。每个 Task 独立 worktree（`./scripts/worktree-manager.sh create feat/p4-2a-tN`）、独立 PR；迁移先行（Task 1）。开工前按 spec §0 复核前提表（另一会话在密集合并聊天/资产代码）。

**Goal:** 给 agent 一个「向人提选择题」的动词（AskUser），给 issue 一个目标级暂停/恢复，让预算用完时停下来问三选一，并把「排队 N 条」计数落到主页、看板与任务中心。

**Architecture:** 一切都骑在一期的三条接缝上：事件日志 + 折叠注册表（新事件 `question_asked / question_answered`、新折叠 `folds/question.py`、`turn_end{awaiting_input|paused}`）；step 边界钩子链（新 `PauseHook`，`BudgetGateHook` 由记录改为提问并停下）；收件箱（暂停期间评论改投、恢复时首步全部领取、列表级 `pending-summary`）。挂起/唤醒**不新造**：issue 侧复用 needs_input 的 `input_gate`（marker 多带 `question_id/options/kind`），聊天侧镜像 `awaiting_approval`（assistant metadata + 卡片 + 下一条消息续跑）。前端一个 `QuestionCard` 三处复用；块注册表、芯片函数、注意力横条各加一项。

**Tech Stack:** FastAPI + SQLAlchemy ORM（禁 `text()`）、DBOS（`input_gate` recv/send）、React 19 + vitest、i18n（`question.*`、`issues.*`、`issueDetail.*`）。

**Spec:** `docs/superpowers/specs/2026-09-06-harness-p4-phase2a-control-plane-design.md`（§0 前提核对 / §1 AskUser / §2 暂停恢复 / §3 预算追问 / §4 计数与 UI / §5 数据接口 / §6 测试 / §7 不做）。UI 稿：Issue Workbench 画板 https://claude.ai/code/artifact/f256ccc7-363b-425e-b493-1ed51e44c0f1 页「二期 2a · 控制面」，8 页。

## Global Constraints

- 迁移与消费代码分 PR；取号前 `git ls-remote origin 'refs/heads/*'` 扫全部分支（2026-09-07 全分支最高 **458** → 本期用 **459**）。
- 每个 Task 的 PR 描述必有「复用 / 删除了什么」一节（用户要求：整洁、复用、模块化、可插拔）。
- 突变复做：每条关键断言至少一次突变转红并记录在 PR 描述里（一期 §7 原话：「每条关键断言至少一次突变转红并记录在 PR」）。
- 真栈验收：`readyz` + 容器内 grep 代码 + 事件真落行 + 前端不刷新即变化；凭证走 `docker exec -i -e DEBUG_TEST_EMAIL -e DEBUG_TEST_PASSWORD nous-backend /app/.venv/bin/python -`。
- 合并门禁：CI 全绿且 `non-success == 0` 再合。
- 新事件只经 `app/services/ai/runner/events.py::emit()` 落行（`tests/runner/test_events_guard.py` 守卫第四个私有 emit 助手）。
- 模型可见面（新工具 schema、FinishIssue 加字段）改动必须同步 `backend/app/services/ai/prompts/README.md` 的「What the model sees / Token effect / KV Cache effect」三问。
- 用户可控文本进框必须转义（`escape_frame_attr` / `escape_frame_prose`）；新框登记 `OWNED_FRAMES`。
- UI 文案英文 + i18n（en/zh 同步）；状态色只用语义 token（`ok/warn/danger/info/agent` 及 `-soft/-line`）；不加 emoji。
- 前端读 `metadata_json.*` 只经 `runView.ts` selector。
- 本计划对 spec 的两处**作者调整**（§1 回答通道、§4 UI 页数）在 Task 1 一并写回 spec：
  1. **回答通道不走收件箱**：回答 = 一条带 `answer_to: <question_id>` 的普通消息（issue 评论 `POST /issues/{id}/messages`；聊天 `POST` 现有发消息端点）。label 校验、`question_answered` 落行、唤醒都在消息端点完成。理由：issue 侧唤醒本就走消息端点的 `_try_wake_waiting_workflow`，聊天侧「下一条消息续跑」本就是 `awaiting_approval` 的路；再经收件箱会让同一个答案注入两次。收件箱 `answer` kind 保留原语义（运行中插话）。
  2. UI 稿 5 页 → 8 页（补看板 / 任务中心 / Progress 卡原因）。

---

### Task 1: 迁移 459 + spec 回写

**Files:**
- Create: `supabase/migrations/459_transcript_event_types_phase2a.sql`
- Modify: `backend/app/models/agents.py`（`AgentRunTranscriptEvents` 的 `CheckConstraint` 字面量同步；schema-drift 门禁只比对表/列/FK、**不比对 CHECK 体**，两侧绑定靠 `tests/models/test_transcript_event_types_phase2a.py` 解析 ARRAY 做集合相等）
- Modify: `docs/superpowers/specs/2026-09-06-harness-p4-phase2a-control-plane-design.md`（§1「回答通道」段、§4「UI 稿」段）

**Interfaces:**
- Produces: 事件类型白名单含 `question_asked`、`question_answered`（Task 2 的折叠与 emit 依赖）。

- [ ] **Step 1: 取号**

```bash
git ls-remote origin 'refs/heads/*' | awk '{print $1}' | while read s; do git ls-tree --name-only "$s" supabase/migrations/ 2>/dev/null; done | grep -oE '[0-9]{3}_' | sort -n | tail -1   # 期望 458_ → 用 459
```

- [ ] **Step 2: 写迁移（照 453 的幂等写法，无 SET ROLE）**

```sql
-- 459: harness P4 phase 2a — typed questions land on the transcript.
BEGIN;
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
    'question_asked'::text, 'question_answered'::text
  ]));
COMMIT;
```

- [ ] **Step 3: ORM 同步** — `backend/app/models/agents.py` 里 `AgentRunTranscriptEvents` 的 `CheckConstraint(...)` 字符串加两个字面量；`cd backend && uv run pytest tests/models -q`。
- [ ] **Step 4: 生产库演练** — `ssh heygo@10.0.0.10 "docker exec -i nous-db psql -U postgres -p 55434 -d postgres" <<'SQL'` 内 `BEGIN; \i`（把文件 cat 进去）`; ROLLBACK;`，记录 NOTICE。
- [ ] **Step 5: spec 回写** — §1「回答通道」段替换为 Global Constraints 第 1 条的表述；§4「UI 稿」段：「追加 5 页」→「追加 8 页（暂停/恢复、QuestionCard 三处、预算三选一、主页、聊天、看板、任务中心、Progress 卡原因）」；UI 清单加「**看板**：卡底一行阶段芯片 + `N queued` + 悬停动作，受阻卡带原因芯片」。
- [ ] **Step 6: PR → CI → 合并 → `run-migration.yml` 应用** — 复验：`SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='agent_run_transcript_events_event_type_check'` 含 `question_asked`。

突变：ORM 字面量少一个 → `tests/models` 红。

---

### Task 2: 提问原语后端核心 —— `question.py` + 折叠 + `stop_reason` 映射

**Files:**
- Create: `backend/app/services/ai/runner/question.py`
- Create: `backend/app/services/ai/runner/folds/question.py`
- Modify: `backend/app/services/ai/runner/run_projection.py:102-111`（import 新折叠）、`:34`（`empty_views()` 的 `view` 加 `question: None`）
- Modify: `backend/app/services/ai/runner/folds/turn_end.py`（`_PHASE_BY_REASON` 加 `"awaiting_input": "waiting_input"`）
- Modify: `backend/app/services/ai/runner/turn_end.py:31-45`（`TurnEndReason.AWAITING_INPUT = "awaiting_input"`）、新增 `STOP_REASON_TO_TURN_END` 表、`classify_run_result` 先读 `stop_reason`
- Modify: `backend/app/services/ai/runner/agent_runner.py:1480-1495`（非流式 STOP 返回）、`:607-619`（流式 STOP 返回终止 chunk 带 `stop_reason`）
- Modify: `backend/app/services/ai/runner/step_hooks.py:58`（`StepContext.stop(reason)` 校验 reason 在表内）
- Test: `backend/tests/runner/test_question.py`、`tests/runner/test_run_projection.py`（追加）、`tests/runner/test_turn_end_reasons.py`（新）

**Interfaces:**
- Produces:
```python
# app/services/ai/runner/question.py
QUESTION_ASKED = "question_asked"; QUESTION_ANSWERED = "question_answered"
PROMPT_MAX = 500; LABEL_MAX = 80; DESC_MAX = 200; MAX_OPTIONS = 6
@dataclass(frozen=True)
class Question:
    question_id: str; kind: str; prompt: str
    options: tuple[dict, ...]        # ({"label": str, "description": str|None}, ...)
    allow_free_text: bool
    def to_payload(self) -> dict     # 事件 payload / marker / metadata 共用同一形状
def normalize_options(raw: Any) -> tuple[list[dict], list[str]]   # (options, warnings)；任何不合规 → ([], [warning])
def question_id_for(kind: str, run_id: int, seq: int) -> str       # "budget:<run>" | f"q:{run}:{seq}"
async def ask_question(recorder, *, kind: str, prompt: str, options: Any, allow_free_text: bool = True, turn: int, step: int) -> Question
def answer_matches(question: dict, value: Any) -> bool             # value == 某 label，或 allow_free_text 且是非空 str
# question_kinds 注册表
OnAnswer = Callable[[dict, str, "AnswerContext"], Awaitable[None]]
def register_kind(kind: str, on_answer: OnAnswer) -> None          # 重复注册 raise
def on_answer_for(kind: str) -> OnAnswer                           # 未注册 raise KeyError
def registered_kinds() -> list[str]
```
```python
# app/services/ai/runner/turn_end.py
class TurnEndReason(str, Enum): ...; AWAITING_INPUT = "awaiting_input"
STOP_REASON_TO_TURN_END: dict[str, TurnEndReason] = {"cancelled": CANCELLED, "paused": PAUSED, "awaiting_input": AWAITING_INPUT}
```
- 折叠：`question_asked` → `view.question = {id, kind, prompt, options, allow_free_text, asked_at}`；`question_answered` → `view.question = None`，`view.last_answer = {id, value, superseded}`。
- 运行器 STOP 结果：`{"content": "", "raw": None, "stop_reason": <reason>, "cancelled": reason == "cancelled"}`；`awaiting_input` 时另带 `"awaiting_input": True, "question": recorder.views["view"]["question"]`。

- [ ] **Step 1: 折叠失败测试**（追加到 `tests/runner/test_run_projection.py`）

```python
def test_question_asked_lands_in_view_and_answer_clears_it():
    v = rp.empty_views()
    q = {"question_id": "q:7:3", "kind": "user", "prompt": "Which ending?",
         "options": [{"label": "Twist", "description": None}], "allow_free_text": True, "asked_at": "2026-09-07T00:00:00Z"}
    v = rp.apply(v, "question_asked", q)
    assert v["view"]["question"]["id"] == "q:7:3" and v["view"]["question"]["options"][0]["label"] == "Twist"
    v = rp.apply(v, "question_answered", {"question_id": "q:7:3", "value": "Twist", "superseded": False})
    assert v["view"]["question"] is None and v["view"]["last_answer"]["value"] == "Twist"

def test_turn_end_awaiting_input_maps_to_waiting_input():
    v = rp.apply(rp.empty_views(), "turn_end", {"reason": "awaiting_input"})
    assert v["view"]["phase"] == "waiting_input"
```

- [ ] **Step 2: 跑红** — `cd backend && uv run pytest tests/runner/test_run_projection.py -q`（`question_asked` 未注册 → 返回同对象 → KeyError/断言失败）。
- [ ] **Step 3: 写 `folds/question.py`**

```python
from app.services.ai.runner.run_projection import register

@register("question_asked")
def fold_question_asked(views, payload):
    qid = payload.get("question_id")
    if not isinstance(qid, str):
        return None
    opts = [o for o in (payload.get("options") or []) if isinstance(o, dict) and isinstance(o.get("label"), str)]
    views["view"]["question"] = {
        "id": qid, "kind": str(payload.get("kind") or "user"), "prompt": str(payload.get("prompt") or "")[:500],
        "options": [{"label": o["label"][:80], "description": (o.get("description") or None)} for o in opts[:6]],
        "allow_free_text": bool(payload.get("allow_free_text", True)), "asked_at": payload.get("asked_at"),
    }
    return views

@register("question_answered")
def fold_question_answered(views, payload):
    qid = payload.get("question_id")
    if not isinstance(qid, str):
        return None
    views["view"]["question"] = None
    views["view"]["last_answer"] = {"id": qid, "value": payload.get("value"), "superseded": bool(payload.get("superseded", False))}
    return views
```
`run_projection.py` import 列表加 `question`；`empty_views()` 的 view 加 `"question": None, "last_answer": None`；`folds/turn_end.py` 加 `"awaiting_input": "waiting_input"`。

- [ ] **Step 4: 跑绿** — 同上命令。
- [ ] **Step 5: `question.py` 失败测试**（`tests/runner/test_question.py`，`_Rec` 照 `test_budget_hook.py` 的写法）

```python
import pytest
from app.services.ai.runner import question as q
pytestmark = pytest.mark.unit

class _Rec:
    def __init__(self, run_id=42):
        self.run_id = run_id; self.events = []; self.views = {"view": {"question": None}}
    async def record_event(self, event_type, payload, *, turn=None, step=None):
        self.events.append((event_type, payload, turn, step))

def test_normalize_options_degrades_to_open_question_on_bad_input():
    opts, warns = q.normalize_options([{"label": "A"}, {"label": "A"}])        # 重复 label
    assert opts == [] and warns
    opts, warns = q.normalize_options([{"label": "x" * 81}])
    assert opts == [] and warns
    opts, warns = q.normalize_options([{"label": str(i)} for i in range(7)])   # 7 > MAX_OPTIONS
    assert opts == [] and warns
    opts, warns = q.normalize_options([{"label": "Twist", "description": "d"}, {"label": "Open"}])
    assert [o["label"] for o in opts] == ["Twist", "Open"] and warns == []

async def test_ask_question_emits_event_with_id_scheme():
    rec = _Rec()
    qu = await q.ask_question(rec, kind="user", prompt="Which?", options=[{"label": "A"}], turn=1, step=3)
    et, payload, turn, step = rec.events[-1]
    assert et == "question_asked" and payload["question_id"] == qu.question_id == "q:42:3"
    assert payload["prompt"] == "Which?" and payload["options"][0]["label"] == "A" and (turn, step) == (1, 3)
    b = await q.ask_question(rec, kind="budget", prompt="Budget exhausted", options=[{"label": "Top up"}], allow_free_text=False, turn=1, step=4)
    assert b.question_id == "budget:42"

def test_answer_matches():
    qd = {"options": [{"label": "Twist"}], "allow_free_text": False}
    assert q.answer_matches(qd, "Twist") and not q.answer_matches(qd, "twist") and not q.answer_matches(qd, "other")
    assert q.answer_matches({**qd, "allow_free_text": True}, "other") and not q.answer_matches({**qd, "allow_free_text": True}, "")

def test_kind_registry_is_enumerable_and_rejects_duplicates():
    async def noop(issue, value, ctx): ...
    q.register_kind("test_kind", noop)
    assert "test_kind" in q.registered_kinds() and q.on_answer_for("test_kind") is noop
    with pytest.raises(ValueError):
        q.register_kind("test_kind", noop)
```

- [ ] **Step 6: 跑红** → **Step 7: 实现 `question.py`**（`ask_question` 用 `events.emit(recorder, QUESTION_ASKED, payload, turn=turn, step=step)`；seq 用 `step`；`asked_at` UTC ISO；`register_kind("user", _noop)` 在模块底部默认注册） → **Step 8: 跑绿**。
- [ ] **Step 9: `stop_reason` 映射失败测试**（`tests/runner/test_turn_end_reasons.py`）

```python
import re, pytest
from pathlib import Path
from app.services.ai.runner import turn_end as te
from app.services.ai.runner.step_hooks import StepContext
pytestmark = pytest.mark.unit

def test_every_stop_literal_in_runner_and_hooks_is_mapped():
    src = "".join(Path(p).read_text() for p in [
        "app/services/ai/runner/step_hooks.py", "app/services/ai/runner/agent_runner.py",
        "app/services/ai/runner/budget_hook.py", "app/services/ai/runner/inbox_hook.py",
    ] + [str(p) for p in Path("app/services/ai/runner").glob("*_hook.py")])
    literals = set(re.findall(r'ctx\.stop\("([a-z_]+)"\)', src))
    assert literals and literals <= set(te.STOP_REASON_TO_TURN_END)

def test_classify_run_result_prefers_stop_reason():
    assert te.classify_run_result({"stop_reason": "paused", "cancelled": False}) is te.TurnEndReason.PAUSED
    assert te.classify_run_result({"stop_reason": "awaiting_input"}) is te.TurnEndReason.AWAITING_INPUT
    assert te.classify_run_result({"cancelled": True}) is te.TurnEndReason.CANCELLED

def test_step_context_rejects_unknown_stop_reason():
    with pytest.raises(ValueError):
        StepContext(turn=1, step=1).stop("nonsense")
```
（`classify_run_result` 现有返回形状若是 `(reason, extra)` 元组，按实际形状改断言；先读 `turn_end.py:82`。）

- [ ] **Step 10: 跑红** → **Step 11: 实现**：`turn_end.py` 加枚举成员与表，`classify_run_result` 开头 `if (sr := result.get("stop_reason")) in STOP_REASON_TO_TURN_END: return ...`；`step_hooks.StepContext.stop` 校验 `reason in STOP_REASON_TO_TURN_END`（延迟 import 避免环）；`agent_runner.py:1486-1491` 返回 `{"content": "", "raw": None, "stop_reason": _step_ctx.stop_reason, "cancelled": _step_ctx.stop_reason == "cancelled"}` 并在 `stop_reason == "awaiting_input"` 时附 `"awaiting_input": True, "question": recorder.views["view"].get("question")`；流式路径 `:616-618` 改为产出一个终止 chunk（照该路径 cancelled 的终止形状，加 `stop_reason` 字段）而不是裸 `return`，并让 `classify_stream_end` 读它。
- [ ] **Step 12: 跑绿 + 全量** — `uv run pytest tests/runner -q`。
- [ ] **Step 13: Commit**：`feat(runner): typed question primitive — question_asked/answered folds, AWAITING_INPUT, stop_reason→TurnEndReason map`

突变：删 `folds/question.py` 注册 → 折叠测红；映射表去掉 `paused` → 穷尽守卫红；流式路径改回裸 `return` → 流式分类测红（补一条 `classify_stream_end` 断言）。PR「复用/删除」：复用 `emit()`、`_Rec` 测试模式、`_PHASE_BY_REASON`；删除运行器两处 `"cancelled": True` 硬编码。

**实施记录（2026-09-08，对抗评审后与本节接口的偏差，Task 3/6 按这里对）**：
- `question_id` 用**事件自己的 transcript seq**（新增 `RunRecorder.next_event_seq`），不是 `step`——同一 step 里两个 AskUser 工具调用 / hook + 工具会撞 id；测试桩没有该属性时才回退 `step`。
- singleton 是注册属性：`register_kind(kind, on_answer, *, singleton=True)` → id 为 `<kind>:<run>`；Task 6 注册 budget 时带 `singleton=True`，不再有硬编码的 `_SINGLETON_KINDS`。
- `ask_question` 写入时校验 `kind` 已注册（`ValueError`）；事件没落行（无 recorder / `run_id` None / recorder 拒收）抛 `QuestionNotRecorded`，不再静默返回一个没人看得到的 Question。
- `StepContext.stop` 的未知理由抛 `UnknownStopReason`，`StepHookChain.run` 对它**不**容纳（其它异常仍容纳），否则拼错理由会退化成静默 CONTINUE。
- 运行器 `awaiting_input` 的 STOP 结果里 `question` 是 `Question.to_payload()` 形状（`question_id`），经 `question.payload_from_view` 从折叠视图（`id`）转回；流式终止 chunk 带 `tool_call_trace`。

---

### Task 3: AskUser 工具 + FinishIssue options + issue 侧挂起与回答

**Files:**
- Create: `backend/app/services/ai/tools/ask_user_tool.py`
- Modify: `backend/app/services/ai/tools/finish_issue_tool.py:46-80`（schema 加 `options`）、`:81`（handler 透传 options）
- Modify: `backend/app/services/ai/runner/agent_runner.py:819-870` 与 `:1681-1789`（两条 if/elif 梯子加 `AskUser` → `self._dispatch_ask_user(args)`）；新方法 `_dispatch_ask_user` 与 `_awaiting_input_response`（镜像 `_awaiting_approval_response` `:2124`）
- Modify: `backend/app/services/ai/chat/ai_library_chat_service.py:946-964`（issue 触发注入 `ask_user_spec()`；聊天触发**也**注入——两条路都给）
- Modify: `backend/app/workflows/issue_lifecycle.py:642`（`route_finish_outcome` 接 `options`）、`:795-899`（`_run_dispatch_with_continuation` 识别 `result["awaiting_input"]`，`mark_waiting(prompt, question=...)`）
- Modify: `backend/app/agent_framework/input_gate.py:97-143`（`mark_awaiting_input` 加 `question: dict | None`，marker 多带 `question_id/kind/options/allow_free_text`）
- Modify: `backend/app/schemas/issue_message.py:103`（`IssueMessagePostPayload.answer_to: Optional[str] = None`）
- Modify: `backend/app/api/issue_messages_router.py:171-186`（`_try_wake_waiting_workflow` 前做 label 校验 + `question_answered` 落行 + `on_answer`）
- Modify: `backend/app/api/issues_router.py:159-192`（`NeedsInputItem` 加 `question_id/options/allow_free_text/kind`）
- Modify: `backend/app/services/ai/runner/run_recorder.py:889-960`（`RunEventWriter.for_run(run_id)`：读 `MAX(seq)+1` 后可往已结束 run 追加事件）
- Modify: `backend/app/services/ai/prompts/README.md`（「工具 schema」节加 AskUser 与 FinishIssue.options 的三问）
- Test: `tests/tools/test_ask_user_tool.py`、`tests/workflows/test_issue_lifecycle_awaiting_input.py`、`tests/api/test_issue_messages_answer.py`、`tests/runner/test_ask_user_dispatch.py`

**Interfaces:**
- Consumes: Task 2 的 `ask_question / answer_matches / on_answer_for / Question.to_payload`、`STOP_REASON_TO_TURN_END`。
- Produces:
```python
# ask_user_tool.py
ASK_USER_TOOL_NAME = "AskUser"
def ask_user_spec() -> dict   # parameters: question(str, ≤500, required) / options(array ≤6 of {label ≤80, description? ≤200}) / allow_free_text(bool, default true)
async def ask_user_handler(args: dict, *, recorder, turn: int, step: int) -> dict   # 调 ask_question(kind="user")；返回 {"asked": True, "question_id", "warnings": [...]}
```
- marker（`issues.execution_state.awaiting_input`）新形状：`{"prompt", "since", "issue_id", "question_id", "kind", "options", "allow_free_text"}`（老字段不动，`NeedsInputCard` 老逻辑仍能读 `prompt`）。
- `IssueMessagePostPayload.answer_to`：有值时正文必须等于某 label 或 `allow_free_text`，否则 400 `answer_shape`；marker 的 `question_id` 不匹配 → 409 `no_open_question`。
- 事件序列（issue）：`tool_call(AskUser)` → `question_asked` → `turn_end{awaiting_input}`；回答：`question_answered`（写到**提问的那个 run**）→ 续跑新 turn。

- [ ] **Step 1: 工具 schema 失败测试**

```python
from app.services.ai.tools.ask_user_tool import ask_user_spec, ASK_USER_TOOL_NAME
from app.services.ai.tools.finish_issue_tool import finish_issue_spec
def test_ask_user_schema_declares_question_options_free_text():
    p = ask_user_spec()["function"]["parameters"]
    assert ask_user_spec()["function"]["name"] == ASK_USER_TOOL_NAME
    assert set(p["properties"]) == {"question", "options", "allow_free_text"} and p["required"] == ["question"]
    assert p["properties"]["options"]["maxItems"] == 6 and p["properties"]["options"]["items"]["properties"]["label"]["maxLength"] == 80
def test_finish_issue_schema_gained_options():
    assert "options" in finish_issue_spec()["function"]["parameters"]["properties"]
```
- [ ] **Step 2: 跑红 → Step 3: 实现两个 spec（FinishIssue 的 `options` 与 AskUser 同一个子 schema，抽成 `question.OPTIONS_JSON_SCHEMA` 常量共用）→ Step 4: 跑绿。**
- [ ] **Step 5: 运行器分派失败测试**（`tests/runner/test_ask_user_dispatch.py`，用源码守卫：两条梯子都含 `ASK_USER_TOOL_NAME`；再用 `_Rec` 断言 `_dispatch_ask_user` 记录了 `question_asked` 且运行器结果带 `awaiting_input`）。实现：`_dispatch_ask_user` 调 `ask_user_handler`，随后置 `self._pending_question = recorder.views["view"]["question"]`，本轮工具循环结束后返回 `self._awaiting_input_response()`（镜像 `_awaiting_approval_response`：`{"awaiting_input": True, "question": ...}`，并 `emit_turn_end(recorder, TurnEndReason.AWAITING_INPUT, {...})`）。
- [ ] **Step 6: 注入失败测试** — 源码守卫：`ai_library_chat_service.py` 在 issue 触发块与聊天触发块都 `+ [ask_user_spec()]`；`FinishIssue` 仍只在 issue 触发。
- [ ] **Step 7: workflow 挂起失败测试**（`tests/workflows/test_issue_lifecycle_awaiting_input.py`，照该文件夹里现有 `_run_dispatch_with_continuation` 测试的 fake 注入方式）

```python
async def test_awaiting_input_result_parks_with_question_marker():
    marks = []
    async def mark_waiting(issue_id, prompt, *, question=None): marks.append((prompt, question))
    async def wait_for_input(issue_id, *, ttl_seconds): return None      # 超时 → needs_followup 留着
    async def run_turn(*a, **k): return {"awaiting_input": True, "question": {"id": "q:1:2", "kind": "user", "prompt": "Which?", "options": [{"label": "A"}], "allow_free_text": True}, "content": ""}
    ...  # set_status/load_issue/clear_waiting/run_reply 用 AsyncMock；照现有测试装配
    await il._run_dispatch_with_continuation(1, issue_row, "agent", "user", run_turn=run_turn, set_status=set_status, load_issue=load_issue,
                                             wait_for_input=wait_for_input, mark_waiting=mark_waiting, clear_waiting=clear_waiting, run_reply=run_reply)
    assert marks and marks[0][1]["id"] == "q:1:2"
    set_status.assert_awaited()   # needs_followup（走 route_finish_outcome("needs_input", ...)）
```
实现：在 `result` 处理处，`if result.get("awaiting_input"): outcome, reason = "needs_input", question["prompt"]`，把 `question` 传给 `mark_waiting`；`execute_issue` 的 `mark_waiting` 闭包（`:1013-1046`）透传到 `mark_awaiting_input(..., question=question)`，marker 合并这些键。`FinishIssue(needs_input, options=[...])` 同路：`route_finish_outcome` 把 `options` 经 `normalize_options` 后组成 `question`（`question_id = q:<run_id>:0`，并用 `RunEventWriter.for_run(run_id)` 补一条 `question_asked`，让两条路的事件序列一致）。
- [ ] **Step 8: 跑红 → 实现 → 跑绿。**
- [ ] **Step 9: 回答端点失败测试**（`tests/api/test_issue_messages_answer.py`，client 装配照 `tests/api/test_agent_inbox_router.py:_client`）

```python
def test_answer_to_with_wrong_label_is_400_answer_shape(client, patched):
    patched.marker.return_value = {"question_id": "q:1:2", "options": [{"label": "A"}], "allow_free_text": False, "prompt": "?"}
    r = client.post("/api/v1/issues/1/messages", json={"body": "B", "answer_to": "q:1:2"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "answer_shape"
def test_answer_to_unknown_question_is_409(client, patched):
    patched.marker.return_value = {"question_id": "q:1:9", ...}
    assert client.post("/api/v1/issues/1/messages", json={"body": "A", "answer_to": "q:1:2"}).status_code == 409
def test_matching_answer_emits_question_answered_then_wakes(client, patched):
    patched.marker.return_value = {"question_id": "q:1:2", "options": [{"label": "A"}], "allow_free_text": False, "run_id": 7}
    r = client.post("/api/v1/issues/1/messages", json={"body": "A", "answer_to": "q:1:2"})
    assert r.status_code == 200 and r.json()["agent_dispatched"] is True
    patched.writer.append.assert_awaited_with("question_answered", {"question_id": "q:1:2", "value": "A", "superseded": False}, turn=None, step=None)
    patched.wake.assert_awaited()
```
实现要点：marker 里补记 `run_id`（`mark_awaiting_input` 拿 `recorder.run_id`，Task 3 Step 7 一起带上）；端点顺序：`answer_to` 校验 → `RunEventWriter.for_run(marker["run_id"]).append("question_answered", ...)` → `on_answer_for(marker.get("kind","user"))(issue_row, value, ctx)` → 既有 `_try_wake_waiting_workflow`。无 `answer_to` 的普通评论若正文恰等于某 label，也视为回答（spec §1 兼容）。
- [ ] **Step 10: `NeedsInputItem` 扩字段**（`issues_router.py:159-192` 映射 marker 的 `question_id/options/allow_free_text/kind`）+ 一条测试。
- [ ] **Step 11: README 三问** — 「工具 schema」节新增 AskUser 小节：What the model sees 原样贴 `ask_user_spec()` JSON；Token effect（约 +120 token/请求，常量）；KV Cache effect（tools 在系统消息之后、稳定前缀，仅 spec 文本变动才失效）。FinishIssue 段补 `options`。
- [ ] **Step 12: 全量 + Commit**：`feat(agent): AskUser tool + FinishIssue options; issue path parks on typed question and answers via answer_to`

突变：梯子里删一处 `AskUser` → 源码守卫红；`answer_matches` 改成大小写不敏感 → 400 测红；不写 `question_answered` → wake 测红。PR「复用/删除」：复用 `input_gate` 挂起、`_awaiting_approval_response` 形状、`_try_wake_waiting_workflow`；不新增端点；删除三期 plan 里「收件箱 answer 为唯一回答通道」的设想（spec 已回写）。

---

### Task 4: 聊天侧提问（镜像 awaiting_approval）

**Files:**
- Modify: `backend/app/services/ai/chat/ai_library_chat_service.py:1296-1305`（`asst_metadata["awaiting_input"] = question payload`）；发消息入口（该文件里读取用户消息与 metadata 的位置，grep `append_user_message`）：新参数 `answer_to`
- Modify: 聊天发消息端点的请求 schema（`app/api/ai_library_router.py` 中 conversations messages 的 `BaseModel`，grep `class .*MessageRequest`）：`answer_to: Optional[str] = None`
- Test: `tests/services/ai/chat/test_awaiting_input_metadata.py`、`tests/api/test_chat_answer_to.py`

**Interfaces:**
- Consumes: Task 2/3 的 `answer_matches`、`RunEventWriter.for_run`、运行器结果 `awaiting_input`。
- Produces: assistant `metadata_json.awaiting_input = {question_id, kind, prompt, options, allow_free_text, run_id}`；回答后同一条消息的 metadata 追加 `awaiting_input.answered = {value, at}`（前端据此渲染只读高亮）。
- 过期：下一条**不带** `answer_to` 的用户消息到来 → 若上一条 assistant 有未答 `awaiting_input` → 记 `question_answered{value: null, superseded: true}` 并 `answered = {value: null, superseded: true}`。

- [ ] **Step 1: 失败测试** — 用 `result = {"awaiting_input": True, "question": {...}}` 走 `ai_library_chat_service` 的落库分支（照 `awaiting_approval` 的现有测试装配，grep `awaiting_approval` under `backend/tests/services`），断言 `append_assistant_message` 收到的 metadata 含 `awaiting_input.question_id`。
- [ ] **Step 2: 跑红 → 实现（与 `awaiting_approval` 同位同形，紧挨 `:1300-1305`）→ 跑绿。**
- [ ] **Step 3: 回答/过期失败测试** — 端点收到 `answer_to`：label 不匹配 400 `answer_shape`；匹配 → `question_answered` 落到 `awaiting_input.run_id` 那个 run，metadata 标 `answered`，正常开始下一回合（用户消息正文就是 label，模型直接读到）；不带 `answer_to` 的新消息 → `superseded`。
- [ ] **Step 4: 跑红 → 实现 → 跑绿 → Commit**：`feat(chat): AskUser lands in assistant metadata; answer_to answers, next plain message supersedes`

突变：把 `superseded` 分支删掉 → 测红；metadata 键名改 → 前端 selector 测（Task 7）红。PR「复用/删除」：复用 `awaiting_approval` 的写入位置与「下一条消息续跑」；聊天路径**不**引入收件箱。

---

### Task 5: 暂停 / 恢复后端 —— PauseHook + `/pause` `/resume` + 暂停期间改投

**Files:**
- Create: `backend/app/services/ai/runner/pause_hook.py`
- Modify: `backend/app/services/ai/chat/ai_library_chat_wiring.py:442-444`（链序 Heartbeat → Cancel → **Pause** → InboxClaim → BudgetGate）
- Modify: `backend/app/services/ai/runner/run_recorder.py:488`（旁加 `check_paused()`：读 `agent_runs.pause_requested`，与 `check_cancelled` 同一节流）
- Modify: `backend/app/repositories/agent_runs_repository.py:563-574`（旁加 `request_pause(run_id, *, user_id)`）
- Modify: `backend/app/repositories/issue_repository.py`（`set_paused_at(issue_id, value: datetime | None)`；写 `execution_state.resumed_from_run_id` 走与 input_gate 同款的 service_role 事务）
- Modify: `backend/app/api/issues_router.py`（新增 `POST /{issue_id}/pause`、`POST /{issue_id}/resume`，放在 `GET /{issue_id}` 之后即可，均 `AuthDep` + `assert_issue_visible`）
- Modify: `backend/app/workflows/issue_lifecycle.py:795-899`（`stop_reason == "paused"` → 释放 `execution_locked_at`（复用 `_clear_issue_lock`），保持 `in_progress`，返回）
- Modify: `backend/app/api/issue_messages_router.py:400-420`（`_divert_to_inbox_if_running` → 运行中**或** `issue_row["paused_at"]` 都改投）
- Modify: `backend/app/workflows/agent_runs_sweeper.py:138-148` + `agent_run_inbox_repository.py:133-147`（`expire_stale(older_than, *, skip_paused_issues=True)`：子查询排除 `issues.paused_at IS NOT NULL` 的 issue 目标）
- Test: `tests/runner/test_pause_hook.py`、`tests/api/test_issues_pause_resume.py`、`tests/workflows/test_issue_lifecycle_paused.py`、`tests/api/test_issue_messages_divert_paused.py`、`tests/repositories/test_inbox_expire_skips_paused.py`

**Interfaces:**
- Produces: `PauseHook.before_llm_call(ctx)`: 根 run 且 `await ctx.recorder.check_paused()` 为真 → `ctx.stop("paused")`。
- `POST /issues/{id}/pause` → 200 `{"issue_id", "paused_at", "run_id": <被请求暂停的运行中根 run 或 null>}`；已暂停 → 409 `already_paused`。
- `POST /issues/{id}/resume` → 200 `{"issue_id", "dispatched": bool, "workflow_id": str|None}`；未暂停且无待领 → 409 `not_paused`。有待领条目**或**上一 run `view.ended.reason == "paused"` → `_dispatch_execute_issue(issue_id, f"issue-{id}-{uuid12}")`，先写 `execution_state.resumed_from_run_id`。
- 事件：`turn_end{reason: paused}`；rollup phase 由 `paused_at` 判出（已实现，无改动）。

- [ ] **Step 1: PauseHook 失败测试**

```python
class _Rec:
    def __init__(self, paused): self.run_id = 1; self._p = paused
    async def check_paused(self): return self._p
async def test_pause_hook_stops_with_paused_reason():
    ctx = StepContext(turn=1, step=2, recorder=_Rec(True), parent_run_id=None)
    assert await PauseHook().before_llm_call(ctx) is StepDecision.STOP and ctx.stop_reason == "paused"
async def test_pause_hook_ignores_sub_runs_and_unpaused():
    assert await PauseHook().before_llm_call(StepContext(turn=1, step=2, recorder=_Rec(True), parent_run_id="p")) is StepDecision.CONTINUE
    assert await PauseHook().before_llm_call(StepContext(turn=1, step=2, recorder=_Rec(False), parent_run_id=None)) is StepDecision.CONTINUE
def test_chain_order_is_heartbeat_cancel_pause_inbox_budget():
    src = Path("app/services/ai/chat/ai_library_chat_wiring.py").read_text()
    assert re.search(r"HeartbeatHook\(\),\s*CancelHook\(\),\s*PauseHook\(\),\s*InboxClaimHook\(\),\s*BudgetGateHook\(\)", src)
```
- [ ] **Step 2: 跑红 → 实现 hook / `check_paused` / 链序 → 跑绿。**
- [ ] **Step 3: 端点失败测试**（client 装配照 `test_agent_inbox_router.py`；repo 用 `AsyncMock`）：pause 写 `paused_at` 且对运行中根 run 调 `request_pause`；重复 pause 409；resume 清 `paused_at`；有待领 → `_dispatch_execute_issue` 被调且 `execution_state.resumed_from_run_id` 写入；无待领且上一 run 非 paused → 409 `not_paused`；不可见 issue 404。
- [ ] **Step 4: 跑红 → 实现 → 跑绿。**
- [ ] **Step 5: workflow 失败测试** — `run_turn` 返回 `{"stop_reason": "paused", "content": ""}` → `set_status` **不**被调（状态留 `in_progress`）、锁被释放、函数返回 `{"outcome": "paused"}`；`PREEMPT_STATUSES` 检查不误伤。
- [ ] **Step 6: 跑红 → 实现 → 跑绿。**
- [ ] **Step 7: 改投 + sweeper 失败测试** — issue `paused_at` 非空且无运行中 run → 评论进收件箱，响应 `diverted_to_inbox=True`；`expire_stale` 对已暂停 issue 的条目不写 `expired_at`（此测试需 `INTEGRATION_DATABASE_URL`，放 `tests/integration/`，单测层用 SQL 编译字符串断言含 `paused_at IS NULL` 子查询）。
- [ ] **Step 8: 跑红 → 实现 → 跑绿 → 全量 → Commit**：`feat(issues): target-level pause/resume — PauseHook, /pause /resume, comments queue while paused`

突变：hook 不判 `parent_run_id` → 子 run 测红；`request_pause` 不调 → 端点测红；workflow 在 paused 时仍 `set_status` → 测红；sweeper 去掉子查询 → 集成测红。PR「复用/删除」：复用 `request_cancel` 形状、`_clear_issue_lock`、`_dispatch_execute_issue`、`derive_phase` 的 `paused_at` 优先级（零改动）；删除 §0 表里「pause 只有壳」这一行。

**实施记录（2026-09-08，Task 5 落地）**：
- 测试位置：sweeper 的真库测试放 `tests/db/test_inbox_expire_skips_paused_integration.py`（挂 `schema-drift.yml`，与其它 `tests/db` 集成测试同款 `orm_dsn`/`pg` fixture），不是 `tests/integration/`；单测层对 `expire_stale_stmt(older_than, *, skip_paused_issues)` 纯构造器做 SQL 编译断言。
- `stop_reason` 需要从 runner 一路透传到 workflow：`run_session_turn` 返回 `stop_reason`（流式路径从终止 chunk 的 `usage.stop_reason` 取），`run_issue_agent` / `run_issue_reply_step` 原样带回，循环在 FinishIssue 路由**之前**读它。
- 开新 turn 的分支新增 `paused_at` 检查（计划没写；不能放循环顶，见 spec §2 实施记录）；`/resume` 先清 `paused_at` 再派发、失败恢复；`/resume` 有完整决策表（withdrawn / running / parked / dispatched / cleared，响应带 `reason`），撞上未观察到的 pause 时撤回而不派发（`AgentRunsRepository.clear_pause_request`），撤回落空则重读再判；`request_pause` 的 `user_id` 可选（目标层鉴权）；`resumed_from_run_id` 写 `execution_state`（计划口径），spec §2 原文的 `metadata_json` 落点不可行（run 行尚不存在）；`running_root_run_id` 读失败改为 raise，端点 503。
- `/dispatch` 与 `/resume` 共用 `_start_execute_issue`（生成 workflow_id → `_dispatch_execute_issue` → `_persist_workflow_id`）；`SET LOCAL ROLE service_role` 只剩 `_persist_workflow_id` 一处。
- 暂停中 `answer_to` 照常唤醒（评审推翻了一版的 409，spec §2 已回写理由）。
- 「暂停中 cancel 清 `paused_at`」放在 `issue_repository.transition_status` **与** `issue_lifecycle.set_status` 两处（`PAUSE_CLEARING_STATUSES`）。

---

### Task 6: 预算 100% → 三选一（`kind="budget"`）

**Files:**
- Modify: `backend/app/services/ai/runner/budget_hook.py:96-115`（halt 分支：`ask_question(kind="budget", ...)` + `ctx.stop("awaiting_input")`；有 `execution_state.budget_wrap_up` 时放行本 run 并记 `budget_check{action: "wrap_up"}`）
- Create: `backend/app/services/ai/runner/question_kinds/budget.py`（`register_kind("budget", on_answer)`；在 `question.py` 底部 import 以完成注册）
- Modify: `backend/app/services/ai/runner/folds/budget.py`（`action == "wrap_up"` → `view.budget.state = "wrap_up"`）
- Test: `tests/runner/test_budget_hook.py`（追加）、`tests/runner/test_budget_question_kind.py`

**Interfaces:**
- Consumes: Task 2 `ask_question / register_kind / AnswerContext`；Task 3 的回答端点会按 marker `kind` 调 `on_answer_for("budget")`。
- Produces: `on_answer(issue_row, value, ctx)`：
  - `"Top up"`：重新读 issue `budget_cents` 与已花（`spent_cents_for_issue`），若 `budget <= spent` → `raise AnswerRejected(409, "budget_still_exhausted")`；否则什么都不做（唤醒由端点完成）。
  - `"Wrap up"`：写 `execution_state.budget_wrap_up = {"run_id": marker.run_id, "at": now}`；再 `enqueue(kind="steer", body="Budget is exhausted. Finish in one step: summarize what is done and stop.")`。
  - `"Cancel"`：`transition_status(issue, "cancelled", reason="budget_exhausted")`（复用 `issues_router.transition_status` 底层 service）；唤醒后 workflow 因 `PREEMPT_STATUSES` 退出。
- 事件序列：`budget_check{halt}` → `question_asked{kind: budget, options: [Top up, Wrap up, Cancel], allow_free_text: false}` → `turn_end{awaiting_input}`。

- [ ] **Step 1: 失败测试**（追加到 `test_budget_hook.py`）

```python
async def test_halt_asks_budget_question_and_stops():
    rec = _Rec(spent=120.0); rec.views["view"] = {"question": None}
    ctx = StepContext(turn=1, step=2, recorder=rec, parent_run_id=None)
    assert await _hook(budget=100).before_llm_call(ctx) is StepDecision.STOP and ctx.stop_reason == "awaiting_input"
    types = [e[0] for e in rec.events]
    assert types == ["budget_check", "question_asked"]
    qa = rec.events[1][1]
    assert qa["question_id"] == "budget:42" and [o["label"] for o in qa["options"]] == ["Top up", "Wrap up", "Cancel"] and qa["allow_free_text"] is False
async def test_wrap_up_flag_lets_run_through_once():
    rec = _Rec(spent=120.0); rec.execution_state = {"budget_wrap_up": {"run_id": 41}}
    ctx = StepContext(turn=1, step=2, recorder=rec, parent_run_id=None)
    assert await _hook(budget=100).before_llm_call(ctx) is StepDecision.CONTINUE
    assert rec.events[-1][1]["action"] == "wrap_up"
```
（`_hook` 工厂需同时注入读 `execution_state` 的 loader；把 `BudgetLoader` 返回值扩为 `(budget, prior, wrap_up: bool)`。）
- [ ] **Step 2: 跑红 → 实现 → 跑绿。**
- [ ] **Step 3: `question_kinds/budget.py` 失败测试** — 三个 label 各一条：Top up 预算仍不够 → `AnswerRejected`；Wrap up 写 flag + enqueue steer（repo `AsyncMock`）；Cancel 调 transition；非法 label 不可能到达（端点已 400），但 `on_answer` 对未知 value 应 `raise AnswerRejected(400, "answer_shape")`。
- [ ] **Step 4: 跑红 → 实现 → 跑绿；`test_question.py` 加断言 `registered_kinds() == ["budget", "user"]`。**
- [ ] **Step 5: Commit**：`feat(budget): 100% halts into a typed three-way question; budget question_kind handles Top up / Wrap up / Cancel`

突变：halt 分支不 `stop` → 测红；`Top up` 不复核预算 → 409 测红；`wrap_up` 不放行 → 测红。PR「复用/删除」：复用 `ask_question`、收件箱 `enqueue`、`transition_status`；`budget_reply` inbox kind 不再有生产者（留 CHECK 不删，下期清）。

**实施记录（2026-09-08，Task 6 落地）**：
- `BudgetLoader` 返回 `BudgetInfo(budget_cents, prior_cents, wrap_up, issue_id)`（只读）；宽限在 halt 处经注入的 `consume`（默认 `claim_wrap_up_grace` → 条件 UPDATE）认领，只放 `WRAP_UP_GRACE_STEPS` 个边界；链序 Budget 先于 InboxClaim；Cancel 不唤醒且循环唤醒后重查 preempt；loader 抛错 fail-open 一次；Top up 读失败 503；Wrap up 幂等——全部来自对抗评审 8 条，见 spec §3 实施记录。
- halt 已提问后同一 run 再到 step 边界（重试）→ 直接再 STOP，不再问第二次（`_reported` 集合）。
- `ask_question` 抛 `QuestionNotRecorded` → 记 error 仍 STOP。
- Cancel = `transition_status(cancelled)` + merge `outcome_reason=budget_exhausted`；`target` 无 `id` → 409 `no_issue_target`；未知 value → 400 `answer_shape`。
- T2 里那条自己注册临时 `budget` kind 的测试改为直接用真 kind。

---

### Task 7: 前端 QuestionCard 三处 + 聊天 metadata selector

**Files:**
- Create: `frontend/components/Todolist/QuestionCard.tsx`、`QuestionCard.test.tsx`
- Create: `frontend/components/Todolist/questionTypes.ts`（`TypedQuestion { id; kind; prompt; options: {label; description?}[]; allowFreeText; answered?: {value: string|null; superseded?: boolean} }` + `questionFromMarker(execState)` + `questionFromRunView(view)`）
- Modify: `frontend/components/Todolist/NeedsInputCard.tsx:23-30`（新 prop `question?: TypedQuestion | null`；有 options 时内嵌 `QuestionCard`，否则老 textarea）
- Modify: `frontend/components/Todolist/IssueDetailView.tsx:306-320, 463-469`（把 marker 解析成 `TypedQuestion`；`handleAnswerQuestion(body, answerTo?)` → `postIssueMessage(issue.id, { body, answer_to: answerTo })`）
- Modify: `frontend/services/issueMessageService.ts:172` 附近的 `IssueMessagePostPayload`（加 `answer_to?: string`）
- Modify: `frontend/components/Todolist/blocks/CockpitBlock.tsx:96-106`（`phase === 'waiting_input' && question` → 头部下方内嵌 `QuestionCard`）
- Modify: `frontend/components/TaskCenter/NeedsInputSection.tsx:19-22, 78-113`（`NeedsInputItem` 带 `question_id/options/allow_free_text` → 摘要芯片 `Pick one of N` + `QuestionCard`；`onAnswer(issueId, text, answerTo?)`）、`TaskCenter.tsx:66-72`（透传 `answer_to`）
- Modify: `frontend/components/chat/AIChatBubble.tsx:414` 旁（`awaitingInput && <QuestionCard ... />`）、`AIChatPanel.tsx:134-147`（旁加 `extractAwaitingInput(msg)`）、聊天发消息 service（加 `answer_to`）
- Modify: `frontend/public/locales/en.json`、`zh.json`（`question.pickOne = "Pick one of {{count}}"`, `question.orType = "Or type your own answer…"`, `question.answer = "Answer"`, `question.answered = "Answered"`, `question.superseded = "No longer waiting"`）
- Create: `frontend/components/Todolist/questionI18nParity.test.ts`（照 `components/prompts/promptsI18nParity.test.ts` 抄，前缀 `question.`）
- Test: `QuestionCard.test.tsx`、`NeedsInputCard.test.tsx`（追加）、`NeedsInputSection.test.tsx`（追加）、`AIChatBubble` 现有测试追加一例

**Interfaces:**
- Consumes: 后端 marker / `NeedsInputItem` / assistant `metadata_json.awaiting_input` 的形状（Task 3/4）。
- Produces:
```ts
export interface QuestionCardProps {
  question: TypedQuestion;
  onAnswer: (value: string, answerTo: string) => Promise<void>;
  compact?: boolean;              // 任务中心用
}
```
渲染：prompt；每个 option 一个按钮（`data-testid="question-option"`，label 即 value，description 作 title/副行）；`allowFreeText` 时下方一个输入框 + `Answer`；提交中禁用全部；`answered` 时按钮只读、所选高亮（`bg-ok-soft border-ok-line text-ok`）、`superseded` 时灰显并显示 `question.superseded`。颜色只用语义 token。

- [ ] **Step 1: QuestionCard 失败测试**（照 `NeedsInputCard.test.tsx` 的 i18n mock 与 RTL 写法）

```tsx
const q = { id: 'q:1:2', kind: 'user', prompt: 'Which ending?', options: [{ label: 'Open ending' }, { label: 'Twist' }], allowFreeText: true };
it('option click answers with the label and the question id', async () => {
  const onAnswer = vi.fn().mockResolvedValue(undefined);
  render(<QuestionCard question={q} onAnswer={onAnswer} />);
  fireEvent.click(screen.getByRole('button', { name: 'Twist' }));
  await waitFor(() => expect(onAnswer).toHaveBeenCalledWith('Twist', 'q:1:2'));
});
it('free text is disabled when allowFreeText is false', () => {
  render(<QuestionCard question={{ ...q, allowFreeText: false }} onAnswer={vi.fn()} />);
  expect(screen.queryByRole('textbox')).toBeNull();
});
it('answered state renders read-only with the pick highlighted', () => {
  render(<QuestionCard question={{ ...q, answered: { value: 'Twist' } }} onAnswer={vi.fn()} />);
  const btn = screen.getByRole('button', { name: 'Twist' });
  expect(btn).toBeDisabled(); expect(btn.className).toMatch(/border-ok-line/);
});
```
- [ ] **Step 2: 跑红 → 实现 → 跑绿。**
- [ ] **Step 3: 三处挂点各一条失败测试**：NeedsInputCard 有 options 时不出 textarea（老 `question` 字段仍能渲染）；NeedsInputSection 出 `Pick one of 2` 芯片且 `onAnswer` 收到第三参数；AIChatBubble 在 `awaitingInput` 时挂 QuestionCard（照 `awaitingApproval` 的现有测试）。
- [ ] **Step 4: 跑红 → 实现 → 跑绿；`npm run typecheck && npm run lint && npm test`。**
- [ ] **Step 5: Commit**：`feat(ui): QuestionCard — one typed-question card mounted in NeedsInputCard, cockpit waiting state, task center and chat bubble`

突变：按钮 value 改成 index → 第 1 条红；`answered` 不禁用 → 第 3 条红；i18n 删一个 zh key → parity 红。PR「复用/删除」：复用 `NeedsInputCard` 的 `AgentNotDispatchedError` 处理、`ApprovalCard` 的挂点；不碰 `IssueReplyBox` 作曲区。

**实施记录（2026-09-08，Task 7 落地）**：
- `NeedsInputCard` 的新 prop 叫 `typed`（不是计划里的 `question`——那个名字已被现有的 prompt 字符串 prop 占用，改名会牵动一堆测试）；有 options 且给了 `onAnswer` 才挂 `QuestionCard`，否则老 textarea；prompt 只由卡头显示一次。
- `questionTypes.ts` 多一个 `questionFromNeedsInputItem(item)`（feed 行的扁平字段）；三个解析器的夹具照抄后端真实 wire 形状。
- `QuestionCard` 多一个 `disabled` prop：Task Center 行的 pending 由「行离开 items」清除，卡片自身的 pending 在 `onAnswer` resolve 后就结束，需要外部把行 pending 压进来。
- Cockpit：`phase === 'waiting_input'` 时先读 run view 的 `question`，没有再回退到 issue marker（run 行结束后视图里的问题也在，但 marker 更权威）；只在 `ctx.env.onAnswerQuestion` 存在时挂载。
- `runView.RunBudget.state` 加 `'wrap_up'`（Task 6 的折叠值），`budgetState` 不再把它过滤掉。
- 聊天：`MessageBubble` 新 props `awaitingInput` / `onAnswerQuestion`；`AIChatPanel.handleSend` 第三参 `{ answerTo }` → `opts.answer_to`；`sendChatMessage` / `streamChatMessage` 都收 `answer_to`。
- `npm run typecheck` 在 master 上本来就有 56 个错，本任务前后数量与集合一致（不是本任务引入，也不在本任务修）。
- 对抗评审 10 条全部处理：① 无选项的类型化问题也走卡片（textarea 回复不带 `answer_to`，问题永远不关、phase 卡在 waiting_input）；② 聊天里回答时不折叠上下文胶囊、不带附件（body 要与 label 逐字相等）；③ 聊天路径的 409/400 不再吞成 toast，SSE error 事件的 `code` 挂到 Error 上抛给卡片；④ 详情页只画一张卡——cockpit 只在 marker 不存在时才从 run view 挂卡；⑤ `issueMessageService._json` 把 `{detail:{code,message}}` 解析成 `IssueAnswerRejectedError`，`answerErrorText` 映射到 `question.error.<code>`；⑥ 选项 label 逐字发送、只 trim 自由文本；⑦ 后端 `mark_question_answered` 加 `answered_value`，marker 路径的已答卡能高亮所选；⑧ 聊天只有最新一条 assistant 消息的卡可答，旧卡只读，issue 上下文会话不挂卡；⑨ 分支已 rebase 到含 Task 4/5 的 master；⑩ 三处弱断言换掉、补双卡与开放式问题测试。

---

### Task 8: 暂停 UI + `pending-summary` + 主页/看板/任务中心芯片 + Progress 卡 Reason

**Files:**
- Backend: Modify `backend/app/api/agent_inbox_router.py`（`GET /ai-library/inbox/pending-summary?target_kind=issue` → `list[{target_id: str, count: int, oldest_at: str}]`，只含可见 issue：`agent_run_inbox_repository.pending_summary(user_id)` 用 `visibility_predicate(user_id)` 子查询）；`GET /issues/paused`（`issues_router.py`，放在 `GET /{issue_id}` **之前**，与 `needs-input` 同范式，`paused_at IS NOT NULL AND hidden_at IS NULL`）
- Frontend services: `services/issuesService.ts`（`pauseIssue(id)`、`resumeIssue(id)`、`listPaused()`）、`services/agentInboxService.ts`（`fetchPendingSummary(): Promise<Record<string, {count; oldestAt}>>`）
- `components/Todolist/blocks/CockpitBlock.tsx:96-106`：`running` → 旁加 `Pause`（`issueDetail.pause`）；`paused` → `Resume`（`issueDetail.resume`，info 色）+ 保留 Cancel；均调用后 `ctx.env.onIssueChanged?.()`
- `components/Todolist/blocks/StatusBlock.tsx`：`phase === 'blocked'` 或 `execution_state.agent_outcome === 'empty_output'` 时加 `Reason` 行（不截断、可换行，`text-danger` / `text-warn`），文字取 `execution_state.error_message ?? execution_state.outcome_reason`
- `components/Todolist/issueChips.ts`：新增 `queuedChip(count: number | undefined): string | null`（`issues.queued = "{{count}} queued"`）
- `components/Todolist/IssueListView.tsx:229, 247-271`：`rowAction` 加 `phase === 'paused' ? t('issues.action.resume','Resume')`；行芯片加 `queuedChip`；顺手把 `:247-252` 的 `text-amber-400 bg-amber-500/10` 换成 `text-ok bg-ok-soft`（同一次触碰，非顺便重构）
- `components/Todolist/IssueBoardView.tsx:34-88`：卡底 footer 行加阶段芯片 + `queuedChip` + `hidden group-hover:inline-flex` 动作（Steer/Resume/Reply）；`blocked` 卡加原因芯片（`execution_state.error_message` 截 60 字）；同样换掉 `:48-52` 的 amber
- `components/Todolist/attentionItems.ts`：`AttentionType` 加 `'queued'`；`buildAttentionItems(..., pausedIssues, pendingSummary)`：有排队且 `paused_at` 的 issue 进 `queued`（`queued:<id>`）
- `pages/TodolistPage.tsx:181` 附近：并行拉 `listPaused()` 与 `fetchPendingSummary()`，经 props 传给 `IssueListView` / `IssueBoardView`
- `components/TaskCenter/ActiveTaskCard.tsx:90-96`：issue 型且 processing → `Pause` 图标按钮（调 `pauseIssue(steerTargetOf(task).id)`）；`components/TaskCenter/TaskCenter.tsx`：新增 `PausedSection`（数据来自 `listPaused()`，每行 `Resume`）
- i18n：`issueDetail.pause/resume/reason`, `issues.action.resume`, `issues.queued`, `issues.attention.queued`, `taskCenter.paused`, `taskCenter.resume`
- Test: `tests/api/test_inbox_pending_summary.py`、`tests/api/test_issues_paused_list.py`；前端 `issueChips.test.ts`、`attentionItems.test.ts`、`IssueListView.test.tsx`、新 `IssueBoardView.test.tsx`、`CockpitBlock.test.tsx`（新）、`StatusBlock.test.tsx`（新）、`ActiveTaskCard.test.tsx`（追加）

**Interfaces:**
- Consumes: Task 5 的 `/pause` `/resume`；rollup `phase`；`issuePhase(issue)`（已读 `raw.paused_at`）。
- Produces: `pending-summary` 响应 `[{ "target_id": "123", "count": 2, "oldest_at": "…" }]`；`GET /issues/paused` 响应形状与 `NeedsInputListResponse` 同族（`items[{issue_id, identifier, title, paused_at, team_id, project_id}]`）。

- [ ] **Step 1: 后端两个端点失败测试**（装配照 `test_agent_inbox_router.py`）：pending-summary 只回可见 issue、按 `target_id` 聚合、`count/oldest_at` 正确；`/issues/paused` 只回 `paused_at` 非空且未隐藏；路由顺序守卫（`/paused` 声明在 `/{issue_id}` 之前——照 `needs-input` 的既有注释）。
- [ ] **Step 2: 跑红 → 实现 → 跑绿。**
- [ ] **Step 3: 前端纯函数失败测试**：`queuedChip(0) === null`、`queuedChip(2) === '2 queued'`；`buildAttentionItems` 对「暂停且有排队」产出 `queued:<id>`、只暂停无排队产出 `paused:<id>`。
- [ ] **Step 4: 跑红 → 实现 → 跑绿。**
- [ ] **Step 5: 组件失败测试**：CockpitBlock `phase: 'running'` 出 `cockpit-pause`，`paused` 出 `cockpit-resume` 且点击调 `resumeIssue`；StatusBlock blocked 出 `status-reason` 且文本完整；IssueListView paused 行 `row-action` 文本 Resume、行上有 `2 queued`；IssueBoardView 卡底出阶段芯片与 `queued`；ActiveTaskCard issue 型出 `agent-pause`。
- [ ] **Step 6: 跑红 → 实现 → 跑绿；`npm run typecheck && npm run lint && npm test`。**
- [ ] **Step 7: Commit**：`feat(ui): pause/resume controls, queued counts on list/board/task center, blocked reason on the progress card`

突变：`pending_summary` 去掉可见性子查询 → 端点测红；`queuedChip` 对 0 返回 `'0 queued'` → 红；StatusBlock 截断原因 → 完整文本断言红。PR「复用/删除」：复用 `issueChips` / `attentionItems` / 块注册表 / `steerTargetOf`；删除 §0 表「横条 paused 类恒空」这一行的成因（`listPaused` 首次给横条真数据）。

**实施记录（2026-09-08，Task 8 落地）**：
- `pendingSummary` 不在 `TodolistPage` 拉，而在 `IssueListView` 的注意力 effect 里与审批同频（60s）拉一次并同时喂行芯片、`IssueBoardView`（props）与横条——`TodolistPage` 并不持有横条数据，放那里要多穿一层 props 且 list/board 切换时重复拉；`listPaused()` 只喂 Task Center 的 `PausedSection`（自拉自刷），横条的 `paused`/`queued` 类仍由 `scopedIssues.filter(paused_at)` 出，不再"恒空"是因为 Task 5 起 `paused_at` 真有值。
- `attentionItems.buildAttentionItems` 第五参 `pendingSummary`：paused 且 `count>0` → `queued:<id>`（不再同时出 `paused:<id>`）；`AttentionStrip` 三张表补 `queued`（info 色、`Layers` 图标）。
- `IssueBoardView.blockedReason(issue)` 回全文，`clipReason` 截 `BOARD_REASON_MAX=60` 只用于芯片文字，`title` 挂全文；`outcome_reason` 兜底。
- `ActiveTaskCard` 的 `agent-pause` 带 `data-state`（idle/sending/paused/failed），失败不静默；`PausedSection` resume 失败留在行上。
- 顺手换掉触碰到的 `IssueListView` `DUE_CLASS`/`creates-in-badge` 与 `IssueBoardView` 的 amber/rose 为 ok/warn/danger。
- 新增 i18n：`issues.queuedTitle`（芯片 tooltip）。
- 对抗评审 10 条全修：`N queued` 改走 `issues.queued`（`queuedChip(count, t)`，`buildAttentionItems` 第六参 `t`），删掉死键 `issues.attention.queued` / `taskCenter.resumed`；pause/resume 的失败改为 `IssueControlError{code,status}`（`_controlJson` 解析 `detail.code`），UI 经 `issueControlErrors.controlErrorText` 映射 `issueDetail.controlError.*`，绝不回显原始 HTTP 体；`ActiveTaskCard` 暂停结果用文字三态（`agent-pause-state`）并经 `issuePauseSignal` 通知 `PausedSection` 立刻重拉；`PausedSection` 30s 轮询 + 信号重拉，后端 `GET /paused` 多取一行出 `has_more`，UI 显示 `N+`；看板阶段芯片改用 `PHASE_LABEL_KEY`/`PHASE_FALLBACK` 与列表同源；running 芯片用 `agent` 色而非 `ok`（绿=完成）；看板 `blockedReason` 改按 `issuePhase` 判定；顺手清理注释/文档串位与 info 色对比度。

---

### Task 9: 真栈验收 + 完成账

**Files:**
- Modify: `docs/superpowers/plans/2026-09-07-harness-p4-phase2a-control-plane.md`（本文末尾追加「完成账」）
- Modify: `CLAUDE.md`（若新增了值得后人知道的陷阱才写；否则不动）

- [ ] **Step 1: 部署确认** — 全部 PR 合并后 `gh run watch` 最近一次 `deploy-gpu`；`ssh heygo@10.0.0.10 'docker exec nous-worker curl -sS http://localhost:8080/api/v1/readyz'`；容器内 `grep -c "class PauseHook" /app/app/services/ai/runner/pause_hook.py`。
- [ ] **Step 2: ① 暂停/恢复** — 调试账号建 issue（英文标题）并派发；运行中 `POST /issues/{id}/pause` → 断言下一条 `turn_end` 事件 `reason=paused`、`GET /progress` `phase=paused`；暂停期间发评论 → 响应 `diverted_to_inbox=true`；`POST /resume` → 新 run 首个 `inbox_claimed` 领到那条评论；前端详情页不刷新即从 Pause 变 Resume 再变运行。
- [ ] **Step 3: ② AskUser 走 issue** — 用一个会调用 AskUser 的提示（在评论里直接要求 "Ask me to pick between A and B before continuing"）→ 事件 `question_asked` 落行、`NeedsInputCard` 出两个按钮、点 A → `question_answered{value: "A"}` 落在提问 run，下一 run 的首条 user 消息正文为 "A"。
- [ ] **Step 4: ③ AskUser 走聊天** — 同样提示在聊天面板 → 气泡出按钮，回答后气泡变只读高亮，下一回合模型读到 label；再发一条普通消息 → 旧问题 `superseded`。
- [ ] **Step 5: ④ 零预算** — `budget_cents=0` 的 issue 派发 → `budget_check{halt}` → `question_asked{kind: budget}` → 三选一卡；分三次 issue 各验 Top up（改预算后续跑）、Wrap up（下一 run `budget_check{action: wrap_up}`、一步收尾）、Cancel（issue `cancelled`）。
- [ ] **Step 6: ⑤ 计数** — 两条排队评论 → `pending-summary` 返 `count=2`；主页行 `2 queued`、看板卡底同数、横条出「queued」类。
- [ ] **Step 7: 前端** — `cd frontend && npm run e2e:prod`；真机按 8 页 UI 稿逐页对照，截图留 PR。
- [ ] **Step 8: 完成账** — 表格记每条验收的证据（事件 id / 截图 / 命令输出），未验项写明原因；记忆文件 `project-harness-p4-phase2a-spec-in-progress` 改名为 shipped 并写下期入口（2b：回放 + fork、schedule、continuable、逐工具超时）。

---

## 自审记录（写完后对照 spec）

- §1 AskUser：Task 2（原语/折叠/枚举）+ Task 3（工具、FinishIssue options、issue 挂起、回答）+ Task 4（聊天）+ Task 7（卡片三处）。回答通道按 Global Constraints 第 1 条调整，Task 1 回写 spec。
- §2 暂停恢复：Task 2（映射表 + 流式路径修复）+ Task 5（hook/端点/改投/sweeper）+ Task 8（UI）。
- §3 预算追问：Task 6；`question_kinds` 注册表在 Task 2 定义、Task 6 注册 `budget`。
- §4 计数与 UI：Task 8（pending-summary、主页、看板、任务中心、StatusBlock Reason）+ Task 7（气泡）；`/issues/paused` 是为横条 paused 类补数据源，spec 未列但 §0 表点名了「恒空」。
- §5 数据接口：Task 1 迁移；无新列（`resumed_from_run_id` 进 `execution_state`、`budget_wrap_up` 进 `execution_state`）；`AskUser` 两条路都给（Task 3 Step 6）。
- §6 测试：每 Task 都列了突变；穷尽守卫在 Task 2 Step 9；真栈五条在 Task 9。
- §7 不做：无任务触碰回放/fork/schedule/超时/聊天暂停。
- 类型一致性：`TypedQuestion.id` ↔ 后端 `question_id`（`questionFromMarker` 负责改名）；`answer_to` 前后端同名；`stop_reason` 字面量三处（`cancelled/paused/awaiting_input`）与表一致。
