# harness 借鉴三期实施计划：类型化提问 · 逐工具超时 · 消息反馈

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.
> 新 session 认领："执行 harness 三期计划 Task N"。

**Goal:** agent 能给人选项而不只是一段话；一个挂住的工具不再吃掉整轮；每条 agent 回复可被点赞/点踩落库。

**Architecture:** 三件互相独立。(1) `FinishIssue` schema 加 `options`，落 `execution_state.question_options`（jsonb 已有，零迁移），NeedsInputCard 渲染按钮、答案编码=label；(2) 工具 spec 上声明 `x-nous-timeout-s`，runner 单一 `_execute_bounded` 用 `wait_for(race_until_abort(...))` 熔合 abort 与 deadline，超时回结构化结果；(3) mig 443 `message_feedback` 表 + PUT/DELETE 端点 + 列表接口补 `my_feedback` + 气泡两个按钮。

**Tech Stack:** FastAPI + SQLAlchemy ORM（裸 SQL 禁令）、asyncio、React 19 + vitest、lucide 图标。

**Spec:** `docs/superpowers/specs/2026-08-26-harness-phase3-typed-interaction-design.md`
（每个 Task 开工先复核 spec §0 对应行——一期五波里四波的计划与实况不符。）

## Global Constraints

- 一 Task 一 worktree 一 PR；迁移 PR 与消费代码 PR 分开；取号前 `git fetch` 并扫全部分支（443/444 写作时空闲）。
- 边界 mock 用真实 wire 形状：FinishIssue 的 `arguments` 是 JSON **字符串**；`messages.id` 是 JSON **number**。
- 遥测/反馈写入失败只 warning，永不影响业务路径。
- UI 文案英文、无 emoji、图标用 lucide、状态色用语义 token。
- 每个守卫做突变复做，结果写进 commit message。
- 后端 `uv run pytest tests/ -q` 全量；前端跑涉及文件的 vitest + `npm run typecheck`（忽略存量错误）。

---

### Task 1: 类型化提问 —— 后端（schema + 校验 + 落库）

**Files:**
- Modify: `backend/app/services/ai/tools/finish_issue_tool.py`（`finish_issue_spec()` :44-80 的 properties）
- Modify: `backend/app/workflows/issue_lifecycle.py`（`route_finish_outcome` :642 签名与 needs_input 分支 :704-712）
- Modify: `backend/app/services/ai/runner/agent_runner.py`（`_dispatch_finish_issue` :922 把 `args.get("options")` 透传到 route）
- Create: `backend/app/services/ai/tools/question_options.py`
- Test: `backend/tests/test_question_options.py`、`backend/tests/test_finish_issue_options_routing.py`

**Interfaces:**
- Produces: `normalize_question_options(raw: Any) -> list[dict] | None`（合规返回 `[{"label": str, "description": str|None}]`，不合规返回 None 并 warning）；`route_finish_outcome(..., options: Optional[list[dict]] = None)`；`execution_state.question_options: list[{label, description}]`（Task 2 消费）

- [ ] **Step 1: 红灯（校验器）** — `backend/tests/test_question_options.py`

```python
import pytest
from app.services.ai.tools.question_options import normalize_question_options as norm

@pytest.mark.unit
def test_valid_options_pass_through():
    assert norm([{"label": "Cold open"}, {"label": "Teaser", "description": "30s hook"}]) == [
        {"label": "Cold open", "description": None},
        {"label": "Teaser", "description": "30s hook"},
    ]

@pytest.mark.unit
@pytest.mark.parametrize("bad", [
    "not a list", [], [{"description": "no label"}], [{"label": ""}],
    [{"label": "x" * 81}], [{"label": "a"}] * 7,
    [{"label": "Same"}, {"label": "Same"}],   # duplicate labels make "answer = label" ambiguous
])
def test_malformed_options_are_dropped_whole(bad):
    assert norm(bad) is None
```

- [ ] **Step 2: 跑红** `uv run pytest tests/test_question_options.py -q` → ImportError
- [ ] **Step 3: 实现** `question_options.py`

```python
MAX_OPTIONS, MAX_LABEL, MAX_DESC = 6, 80, 200

def normalize_question_options(raw):
    if not isinstance(raw, list) or not raw or len(raw) > MAX_OPTIONS:
        return None
    out, seen = [], set()
    for item in raw:
        if not isinstance(item, dict):
            return None
        label = str(item.get("label") or "").strip()
        if not label or len(label) > MAX_LABEL or label in seen:
            return None
        desc = item.get("description")
        desc = str(desc).strip() if desc else None
        if desc and len(desc) > MAX_DESC:
            return None
        seen.add(label)
        out.append({"label": label, "description": desc})
    return out
```

- [ ] **Step 4: 绿** → **Step 5: schema + 路由红灯** — `test_finish_issue_options_routing.py`：
  (a) `finish_issue_spec()["function"]["parameters"]["properties"]` 含 `options`（maxItems 6）；
  (b) 驱动 `route_finish_outcome(1, "needs_input", "Which opening?", options=[{"label":"A"},{"label":"B"}], auto_close=False, set_status=spy)` → spy 的 kwargs 含 `question_options=[{"label":"A","description":None},...]`；
  (c) `outcome="completed"` 带 options → spy kwargs **不含** `question_options`；
  (d) 不合规 options → needs_input 仍落 `outcome_reason`，无 `question_options`（一次坏输出不卡 turn）。
- [ ] **Step 6: 实现**：schema 加 spec §1.1 的 `options`；`route_finish_outcome` 加 `options=None`，needs_input 分支 `opts = normalize_question_options(options)`，`set_status(..., **({"question_options": opts} if opts else {}))`；`_dispatch_finish_issue` 透传 `args.get("options")`。⚠️ 核对 `set_status` 是否接受任意 execution_state 键（读 `issue_lifecycle` 里 `set_status` 的实现/`update_execution_state`），必要时扩它。
- [ ] **Step 7: 突变** — 去掉去重 → 重复 label 用例红；completed 分支不忽略 options → (c) 红。
- [ ] **Step 8: lint / 全量 / commit / PR（base=master）**

---

### Task 2: 类型化提问 —— 前端（选项按钮，答案=label）

**Files:**
- Modify: `frontend/components/Todolist/NeedsInputCard.tsx`（props :25-29）
- Modify: `frontend/components/Todolist/IssueDetailView.tsx`（:437-450 把 `execState.question_options` 传入）
- Modify: `frontend/components/Todolist/issueChips.ts`（needs_input 芯片文案）
- Test: `NeedsInputCard.test.tsx`、`issueChips.test.ts` 增块

**Interfaces:**
- Consumes: `execution_state.question_options: {label, description?}[]`（Task 1）
- Produces: `NeedsInputCardProps.options?: {label: string; description?: string | null}[]`；点击选项 → `onSubmit(label)`

- [ ] **Step 1: 红灯**

```tsx
it('renders one button per option and submits the LABEL verbatim', async () => {
  const onSubmit = vi.fn().mockResolvedValue(undefined);
  render(<NeedsInputCard question="Which opening?" options={[
    { label: 'Cold open' }, { label: 'Teaser', description: '30s hook' }]} onSubmit={onSubmit} />);
  await userEvent.click(screen.getByRole('button', { name: /Teaser/ }));
  expect(onSubmit).toHaveBeenCalledWith('Teaser');
});
it('keeps the free-text box even when options exist', () => { /* textbox 仍在 */ });
it('no options → no button list, legacy layout unchanged', () => { /* 无 options 时按钮数 0 */ });
it('chip says "Pick one of 2" when options exist', () => { /* issueChips */ });
```

- [ ] **Step 2–4: 实现 / vitest / typecheck** — 按钮用既有 primitives；description 次级文案；无 emoji。
- [ ] **Step 5: 突变** — 提交 `option.description` 而非 `label` → 第一条红。
- [ ] **Step 6: i18n 双语 + commit + PR（依赖 Task 1 合并，消费同一形状）**

---

### Task 3: 逐工具超时

**Files:**
- Modify: `backend/app/services/ai/runner/agent_runner.py`（六处执行点 :731/:799/:922/:971/:1400/:1488 + MCP/ResourceFetch 分支；新增 `_execute_bounded`）
- Modify: `backend/app/services/ai/prompts/prompt_composer.py`（`_build_tools` 出口剥 `x-nous-*` 键；`_skill_tool_spec` 加 `"x-nous-timeout-s": 30`）
- Modify: `backend/app/services/ai/tools/screenwriting_specs.py`、`resource_fetch_tool.py` 的 spec（加声明）
- Create: `backend/app/services/ai/runner/tool_timeouts.py`（默认表 + `timeout_for(tool_name, spec) -> float | None`）
- Test: `backend/tests/test_tool_timeouts.py`

**Interfaces:**
- Produces: `timeout_for(tool_name: str, tools: list[dict]) -> Optional[float]`（spec 声明优先，其次默认表：Skill 30 / ResourceFetch 120 / FinishIssue 10 / screenwriting 60 / Delegate None / MCP None）；`AgentRunner._execute_bounded(tool_name, coro, *, timeout_s, abort) -> dict`；超时结果 `{"error":"tool timed out","tool":name,"timeout_s":t,"code":"TOOL_TIMEOUT"}`

- [ ] **Step 1: 红灯**

```python
@pytest.mark.asyncio
async def test_a_hung_tool_returns_a_typed_timeout_not_a_dead_turn():
    class Hang:  # skill tool that never returns
        async def execute(self, args): await asyncio.sleep(999)
    adapter = AsyncMock(); adapter.call.side_effect = [_assistant(_call("c1","Skill",{"skill":"x"})), _final()]
    runner = AgentRunner(adapter=adapter, skill_tool=Hang())
    with patch("app.services.ai.runner.tool_timeouts.DEFAULTS", {"Skill": 0.05}):
        out = await runner.run_turn(_composed(), [{"role":"user","content":"hi"}])
    assert out["content"] == "DONE"                       # turn 活着
    _, messages = adapter.call.await_args_list[1].args
    tool_msg = next(m for m in messages if m.get("role") == "tool")
    assert json.loads(tool_msg["content"])["code"] == "TOOL_TIMEOUT"

@pytest.mark.asyncio
async def test_abort_wins_over_timeout():   # abort 在 deadline 前触发 → 走 RunAborted 路径,不是 TOOL_TIMEOUT
@pytest.mark.asyncio
async def test_spec_declared_timeout_overrides_default():   # x-nous-timeout-s 优先
@pytest.mark.unit
def test_x_nous_keys_never_reach_the_provider_body():   # _build_tools 出口无 x-nous-* 键（W5 白名单纪律）
@pytest.mark.unit
def test_delegate_and_mcp_have_no_layered_timeout():   # timeout_for 返回 None
```

- [ ] **Step 2–4: 实现** — `_execute_bounded`：`timeout_s is None → 直接 await race_until_abort(coro, abort)`；否则 `asyncio.wait_for(race_until_abort(coro, abort), timeout_s)`，`TimeoutError → 结构化结果 + logger.warning`，`RunAborted` 原样抛。六处执行点改经它（tool 结果追加逻辑不动）。
- [ ] **Step 5: 突变** — 把 TimeoutError 改为 re-raise → 第一条红；剥键改为不剥 → 白名单用例红。
- [ ] **Step 6: lint / 全量 / commit / PR**

---

### Task 4: migration 443 —— message_feedback

**Files:**
- Create: `supabase/migrations/443_message_feedback.sql`（spec §3.1 原文 + RLS 策略——**执行时先** `\d+ public.messages` 与 `pg_policies WHERE tablename='messages'` 抄现有口径）
- Modify: `backend/app/models/*.py`（新增 ORM 模型 `MessageFeedback`，表名 `message_feedback`）

- [ ] **Step 1: 取号复核**（443；全分支扫 `/443_`）
- [ ] **Step 2: 写迁移 + ORM**
- [ ] **Step 3: 影子演练**（生产库临时表 + ROLLBACK）：幂等两遍；`rating='meh'` 被 CHECK 拒；同 (message_id,user_id) 二次 INSERT 撞主键；删 messages 行级联删反馈。
- [ ] **Step 4: commit / PR（迁移单独）**；合并后对生产库真实 INSERT+ROLLBACK 验证列与约束。

---

### Task 5: 消息反馈 —— 端点 + 列表补字段

**Files:**
- Create: `backend/app/api/message_feedback_router.py`（挂进 `main.py`）
- Create: `backend/app/repositories/message_feedback_repository.py`
- Modify: 会话消息列表端点（执行时 `grep -rn '"/messages"' backend/app/api/` 定位）：assistant 消息补 `my_feedback`
- Test: `backend/tests/api/test_message_feedback.py`

**Interfaces:**
- Produces: `PUT /api/v1/conversations/{cid}/messages/{mid}/feedback {rating, note?}` → `{message_id, rating, note, updated_at}`；`DELETE` → 204；列表项 `my_feedback: {rating, note} | null`

- [ ] **Step 1: 红灯** — upsert 两次同一 rating 只有一行；改 rating 覆盖；删后 GET 列表 `my_feedback` 为 null；对 `sender_type='user'` 消息 PUT → 422；非会话成员 → 404（不泄露存在性）；列表 LEFT JOIN 一次查询（断言 `execute` 调用次数不随消息数增长）。
- [ ] **Step 2–4: 实现 / 突变（去掉 sender_type 校验 → 422 用例红；N+1 → 调用次数用例红）/ lint 全量 commit PR（base=Task 4 分支或 master 视合并状态）**

---

### Task 6: 消息反馈 —— 气泡按钮

**Files:**
- Modify: `frontend/components/chat/AIChatBubble.tsx`（props :21-43 加 `messageId?`、`feedback?`、`onFeedback?`）
- Modify: 装配 AIChatBubble 的父组件（执行时 `grep -rn '<AIChatBubble' frontend/components/chat`）传 `messageId` 与 `feedback`，并接 `feedbackService`
- Create: `frontend/services/feedbackService.ts`
- Test: `AIChatBubble.test.tsx` 增块、`feedbackService.test.ts`

- [ ] **Step 1: 红灯** — assistant 气泡有两个按钮（`ThumbsUp`/`ThumbsDown` aria-label "Helpful"/"Not helpful"）；user 气泡没有；点一次 → `onFeedback('positive')`；已选态再点 → `onFeedback(null)`；`feedbackService.put` 发 `{rating}` 到正确 URL、`remove` 发 DELETE。
- [ ] **Step 2–4: 实现（乐观更新 + 失败回滚 toast）/ vitest / typecheck / 突变（再点不取消 → 红）/ commit / PR（依赖 Task 5）**

---

## 依赖与并行

```
Task 1 → Task 2        （类型化提问,前后端）
Task 3                 （逐工具超时,独立）
Task 4 → Task 5 → Task 6（消息反馈,迁移→端点→UI）
```

三条线互不相关，可三个 worktree 并行。

## Self-Review 已做

- spec 覆盖：§1→T1/T2，§2→T3，§3→T4/T5/T6；§0 spill 非目标无任务（正确）。
- 类型一致：`question_options` 形状 T1 Produces == T2 Consumes；`_execute_bounded` 签名与超时结果形状在 T3 内自洽；`my_feedback` 形状 T5 == T6。
- 刻意标注的核对点（不假装确定）：`set_status` 是否接受任意 execution_state 键（T1）、messages 的 RLS 现有口径（T4）、消息列表端点位置（T5）、AIChatBubble 的装配父组件（T6）——各附核对命令。
