# needs_input 一等状态 + 静默失败类型化契约 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** agent 卡住等人回答时,用户在 Task Center 看到、内联回答、任务自动续跑;同时消灭三个静默失败(零产出洗白、auto-dispatch 记账盲区、附件失败不回显)。

**Architecture:** 后端复用已验证的 `respond_to_issue_reply` 回复链路并扩展状态流转(needs_followup→in_progress→按 outcome 路由,可循环);Task Center 增加第二数据源(issues 表 needs_input 行,列表端点 + Realtime);task_tracking.phase 纪律不破(不伪造 phase)。Spec: `docs/superpowers/specs/2026-08-01-needs-input-first-class-design.md`。

**Tech Stack:** FastAPI + SQLAlchemy raw SQL(execute_as_service_role)+ Supabase Realtime;React 19 + i18next + vitest;pytest。

## Global Constraints

- UI 文本英文 + i18n key,en/zh 齐平(CLAUDE.md UI 语言规范);状态色用语义 token(warn=琥珀),不引入旧色相类名
- `task_tracking.phase` 等 trigger 独管列禁止业务 PATCH(路线 C 纪律);issue 状态变更必须走 `transition_status` / `set_status`(带节点同步),不裸写 SQL
- 审阅门语义零改动:in_review→done 只有人
- 不碰 `frontend/components/AILibrary/**`;改 `backend/app/repositories/agent_runs_repository.py` 前先 `git log --oneline -3 origin/master -- <file>` 确认 rebase 后无并行改动冲突
- 每个 task 独立 commit,消息中文、结尾 `Claude-Session: https://claude.ai/code/session_01KvXWh2z8qRAE3yUgUDq4sW`
- 测试跑 `cd backend && uv run pytest <file> -q` / `cd frontend && npx vitest run <path>`;isort 检查触碰的 py 文件

---

### Task 1: 共享 outcome 路由 + 回复驱动的 needs_input 恢复

**Files:**
- Modify: `backend/app/workflows/issue_lifecycle.py`(路由表在 ~L444-475 的 `_run_dispatch_with_continuation` 尾部;回复链在 `_run_reply_turns` ~L217 与 `respond_to_issue_reply` ~L262)
- Test: `backend/tests/test_issue_reply_resume.py`(新建)

**Interfaces:**
- Produces: `route_finish_outcome(issue_id: int, outcome: Optional[str], reason: Optional[str], *, auto_close: bool, set_status) -> None` — 模块级函数,把 completed/needs_input/continue-capped/None 的四路路由收敛为一份(现有 dispatch 尾部改为调用它)
- Produces: 回复路径新行为 — **仅当** issue `status=='needs_followup'` 且 `execution_state->>'agent_outcome'=='needs_input'` 时:回复先 `set_status(issue_id, "in_progress")`,续 turn 结束后调 `route_finish_outcome`;其它状态回复不改状态(Spec-1b 回归保持)

- [ ] **Step 1: 写失败测试**(mock `run_turn`/`load_issue`/`set_status`,直接调 `_run_reply_turns` 或其上层;不需要 DBOS runtime——参照 `tests/test_autopilot_tick.py` 的 impl/shell 测试方式)

```python
# backend/tests/test_issue_reply_resume.py
"""Reply-driven resume for needs_input issues (spec §4).

仅当 needs_followup + agent_outcome=needs_input 时回复才驱动状态流转;
其它状态回复保持 Spec-1b 的"不改状态"。"""
import pytest
from unittest.mock import AsyncMock

from app.workflows.issue_lifecycle import route_finish_outcome


@pytest.mark.asyncio
async def test_route_completed_autoclose_off_goes_in_review():
    set_status = AsyncMock()
    await route_finish_outcome(1, "completed", "r", auto_close=False, set_status=set_status)
    set_status.assert_awaited_once_with(
        1, "in_review", agent_outcome="completed", outcome_reason="r"
    )


@pytest.mark.asyncio
async def test_route_needs_input_goes_needs_followup():
    set_status = AsyncMock()
    await route_finish_outcome(1, "needs_input", "which style?", auto_close=True, set_status=set_status)
    set_status.assert_awaited_once_with(
        1, "needs_followup", agent_outcome="needs_input", outcome_reason="which style?"
    )


@pytest.mark.asyncio
async def test_reply_to_needs_input_issue_resumes_and_reroutes():
    """needs_followup+needs_input 的 issue 收到回复:先转 in_progress,
    续 turn 后按新 outcome 路由(这里 agent 答 completed)。"""
    from app.workflows import issue_lifecycle as il
    calls = []
    async def fake_set_status(issue_id, status, **kw):
        calls.append(status)
    # 具体桩位按实现落点:load_issue 返回 needs_followup+needs_input 行,
    # run_turn 返回 {"outcome": "completed", "reason": None, "content": "done"}
    # 断言顺序:["in_progress", "in_review"]（auto_close=False）
    ...


@pytest.mark.asyncio
async def test_reply_to_normal_issue_keeps_status_untouched():
    """in_progress 的 issue 收到回复:set_status 从未被调用(Spec-1b 回归)。"""
    ...
```

（后两个测试的桩位在 Step 3 实现定型后补全断言——先让前两个红。）

- [ ] **Step 2: 跑测试确认红** `uv run pytest tests/test_issue_reply_resume.py -q` → `route_finish_outcome` 未定义
- [ ] **Step 3: 实现** — ① 从 `_run_dispatch_with_continuation` 尾部抽出四路 if/elif 为模块级 `route_finish_outcome`(签名见 Interfaces;原处改调用,行为零变化);② 回复链:在跑续 turn 前 `load_issue` 判定 needs_input 挂起态,是则 `await set_status(issue_id, "in_progress")`(经由它触发节点同步),turn 完成后取 outcome/reason(`extract_issue_outcome` 已在 executor 返回值里)调 `route_finish_outcome`,`auto_close` 用已有 `load_auto_close_flag()`;非挂起态走原逻辑一行不动;③ 回填 Step 1 后两个测试的桩位与断言
- [ ] **Step 4: 全绿** `uv run pytest tests/test_issue_reply_resume.py tests/test_issue_lifecycle_sql.py -q`(后者保回归)
- [ ] **Step 5: Commit** `feat(issues): needs_input 回复驱动恢复 — 共享 outcome 路由 + 状态流转`

### Task 2: needs-input 列表端点

**Files:**
- Modify: `backend/app/api/issues_router.py`(加在已有 list 端点旁,复用其鉴权/可见性口径 `_assert_visibility` 同族)
- Test: `backend/tests/api/test_issues_needs_input.py`(新建,参照同目录现有 router 测试的 client/auth fixture)

**Interfaces:**
- Produces: `GET /api/v1/issues/needs-input` → `{"items": [{"issue_id": int(str 序列化), "title": str, "question": str|null, "project_id": str|null, "team_id": str|null, "asked_at": iso8601}]}`,按 `updated_at DESC`,上限 50
- SQL 判定:`status='needs_followup' AND execution_state->>'agent_outcome'='needs_input'`,`question` 取 `execution_state->>'outcome_reason'`,可见范围=当前用户 team 内(与 issues list 相同的过滤)

- [ ] **Step 1: 写失败测试** — 三条:①seed 一条 needs_input 行 → 返回含 question/asked_at;②seed 一条普通 needs_followup(无 agent_outcome)→ 不出现;③他队 issue → 不出现
- [ ] **Step 2: 确认红**(404)
- [ ] **Step 3: 实现端点**(service_role 查询 + Python 侧过滤口径与 issues_router 现有 list 一致;BIGINT 一律 str 序列化防精度)
- [ ] **Step 4: 全绿** + `uv run isort --check-only app/api/issues_router.py`
- [ ] **Step 5: Commit** `feat(issues): needs-input 列表端点`

### Task 3: Task Center "Needs your answer" 分区 + 内联快回

**Files:**
- Create: `frontend/components/TaskCenter/NeedsInputSection.tsx`
- Modify: `frontend/contexts/TaskManagerContext.tsx`(新数据源:首屏拉列表端点 + Supabase Realtime 订阅 `issues` 表 UPDATE 增量刷新;若 Realtime 对 issues 表不可用则 60s 轮询兜底,实现哪种由探测决定并在报告注明)
- Modify: `frontend/components/TaskCenter/TaskCenter.tsx`(置顶挂载分区)
- Modify: `frontend/services/issuesService.ts`(`listNeedsInput()` + 复用已有回复发送函数;若无现成回复函数则加 `replyToIssue(issueId, body)` POST 到 issue messages 端点)
- Modify: `frontend/public/locales/en.json` + `zh.json`
- Test: `frontend/components/TaskCenter/NeedsInputSection.test.tsx`

**Interfaces:**
- Consumes: Task 2 端点;已有 issue 回复 POST(`issue_messages_router` L343 附近的发送端点,实际路径以 `issuesService`/router 为准)
- Produces: `<NeedsInputSection items onAnswer />`;i18n keys `taskCenter.needsAnswer`(en: "Needs your answer")、`taskCenter.needsAnswerEmpty`、`taskCenter.viewConversation`、`taskCenter.answerPlaceholder`(en: "Type your answer…")

- [ ] **Step 1: 写失败测试** — ①有 items 时渲染分区标题+计数徽章+问题原文;②提交调用 `onAnswer(issueId, text)` 且输入清空、卡片进 pending 态;③items 空时整个分区不渲染
- [ ] **Step 2: 确认红**
- [ ] **Step 3: 实现** — 分区置顶、warn 语义色(`text-warn`/`bg-warn-*` 语义 token)、计数徽章;卡片:问题原文 + `<textarea>` + Answer 按钮 + View conversation 链接(`/team/{teamId}/todolist?issue={id}` 按现有 issue 深链格式,先查 IssuesPage 的路由再定);提交后乐观 pending,数据源刷新后行消失
- [ ] **Step 4: 全绿** `npx vitest run components/TaskCenter` + `npx tsc --noEmit`(仅允许 master 既有报错)
- [ ] **Step 5: Commit** `feat(task-center): Needs your answer 分区 + 内联快回`

### Task 4: A2 — 零产出类型化 + run 收尾字段

**Files:**
- Modify: `backend/app/workflows/issue_lifecycle.py`(`_run_dispatch_with_continuation` 与 Task 1 的回复续 turn 出口)
- Modify: `backend/app/services/issues/issue_agent_executor.py`(run 记录关联处)
- Modify: `backend/app/repositories/agent_runs_repository.py`(如需新 update 帮助函数;改前按 Global Constraints 查并行改动)
- Test: `backend/tests/test_issue_empty_output.py`(新建)

**Interfaces:**
- Produces: 判定规则 — turn 返回 `content` 长度为 0 **且** `outcome is None` ⇒ ①issue `set_status(id, "needs_followup", agent_outcome=None, outcome_reason="Agent produced no output (EMPTY_OUTPUT)")`;②该 run 行 `error_code='EMPTY_OUTPUT'`、`error_message` 摘要;**绝不落 in_review**。有产出无 outcome 仍走原默认(in_review)——回归线
- Produces: run 完结统一收尾 — `liveness_state` 置终态(读 `liveness_scanner.py` 的状态集选正确终值)、`agent_runs.issue_id` 在派发创建时回填(追踪 `issue_session.py` 的 create 调用链找落点)

- [ ] **Step 1: 写失败测试** — ①(0 字符, None outcome) → needs_followup + EMPTY_OUTPUT,断言 set_status 从未收到 "in_review";②("有内容", None) → in_review(回归);③run 行收尾断言(mock repository)
- [ ] **Step 2: 确认红**
- [ ] **Step 3: 实现**(判定放在 outcome 路由之前,dispatch 与 reply 两个出口共用——自然落点是 Task 1 的 `route_finish_outcome` 入口处加参数 `content_len: int`)
- [ ] **Step 4: 全绿** `uv run pytest tests/test_issue_empty_output.py tests/test_issue_reply_resume.py -q`
- [ ] **Step 5: Commit** `fix(agent-runs): 零产出 run 类型化为 EMPTY_OUTPUT — 不再洗成待审阅`

### Task 5: A1 — issue-dispatch 路径记账

**Files:**
- Modify: 追踪定位(入口:`issue_agent_executor.py` L145 `run_session_turn` → `ai_library_chat_service.py` 内 usage 的记录点 → 为何 `trigger=issue_dispatch*` 的 agent_runs 行 tokens 全 0——两种可能:usage 写到了别的行,或该路径根本没调记账)
- Test: `backend/tests/test_issue_dispatch_usage.py`(新建)

**Interfaces:**
- Produces: issue-dispatch(auto 与手动)完成后,该次 `agent_runs` 行的 `prompt_tokens/completion_tokens/total_tokens` 来自 provider usage,`cost_cents` 按现有计价函数(chat 路径怎么算就怎么算,不新造)

- [ ] **Step 1: 写失败测试** — mock provider 返回 usage(如 100/50/150),跑一次 issue dispatch turn,断言 run 行三个 tokens 字段与 cost_cents 非空非零
- [ ] **Step 2: 确认红**(当前为 0/NULL)
- [ ] **Step 3: 实现**(在 chat 路径已有的记账函数上接线,不复制计价逻辑)
- [ ] **Step 4: 全绿**
- [ ] **Step 5: Commit** `fix(agent-runs): issue-dispatch 路径补齐 token/cost 记账`

### Task 6: attachment_failures 回显 + 纪律入档

**Files:**
- Modify: 前端 chat 消息渲染处(追踪:响应里 `attachment_failures` 字段已由后端返回——`ai_library_chat_service.py` L296/L403;grep 前端谁消费该响应,若丢弃则在消息下方渲染 warn 色提示条 "N attachment(s) failed to load: <names>")。⚠️ 若消费点在 `frontend/components/AILibrary/**` 内(禁区),则本 task 只做后端确保字段贯通 + i18n key 预埋,UI 渲染记债并在报告说明
- Modify: `CLAUDE.md`「已知陷阱」邻近新增条款
- Test: 前端渲染测试(若 UI 落在禁区则免)

**Interfaces:**
- Produces: CLAUDE.md 条款文案(逐字):**"用户动作→agent 触发的每条路径必须返回类型化结果(成功/失败/原因),silent no-op 不可接受——与'DBOS 失败必须 raise'同族。新增触发路径时先写失败分支的用户可见回显。"**
- Produces: i18n `chat.attachmentFailures`(en: "{{count}} attachment(s) failed to load"),zh 齐平

- [ ] **Step 1: 追踪消费点并定 UI 落点**(结论写进 commit message)
- [ ] **Step 2: 写失败测试**(有 failures 时渲染提示条;禁区则免)
- [ ] **Step 3: 实现 + CLAUDE.md 条款**
- [ ] **Step 4: 全绿**(触碰范围内 vitest + tsc)
- [ ] **Step 5: Commit** `feat(chat): attachment_failures 用户回显 + 触发路径类型化纪律入档`

---

## Self-Review 记录

- Spec 覆盖:§2→T2/T3;§3→T3;§4→T1;§5.1→T4;§5.2→T5;§5.3/5.4→T6;§7 测试口径分散于各 task Step 1。§5.4 的"盘点现有触发路径静默分支"以 T6 的 CLAUDE.md 条款 + 追踪结论承载,全量盘点超出本计划记债——spec §5.4 已允许。
- 占位符:T1 Step 1 后两个测试的桩位标注了"实现定型后补全"并在 Step 3 ③ 闭合,非悬空;T5 实现位置依赖追踪结论,已给出完整追踪入口链,属"定位类任务"的最小充分信息。
- 类型一致:`route_finish_outcome` 签名 T1 定义、T4 扩参(`content_len`)在 T4 Interfaces 声明;端点响应字段 T2 定义、T3 消费同名。
