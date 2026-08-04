# 协作面实施计划（第二批 A2 时间线 + 第三批 A1 Issues 页微改）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** issue 详情页升级为人机协作时间线（运行卡折叠/提问卡内联回复/交付物卡/右栏进度轨道），Issues 列表页贴上「等我的」注意力层与两个状态 chip——agent 干活的过程与交接点全程可见。

**Architecture:** 最小 diff 铁律——现有页面骨架零重排，改动以"贴层/加 chip/重组现有内容"表达。A2 先行（详情页两栏化 + 时间线卡片），A1 随后（AttentionStrip + chips）。数据尽量复用现有投影（`execution_state` 已在 list/detail 响应里但前端从未读过）；唯一后端改动是 dispatch 循环把轮次 merge 进 `execution_state.turn`。Spec: `docs/superpowers/specs/2026-08-02-collab-surface-and-ai-library-redesign-design.md` §A；视觉稿 `2026-08-02-collab-redesign-mockup.html` §A1/A2。

**Tech Stack:** React 19 + TS + Vite / vitest + @testing-library（Todolist 现有风格：`container.querySelector('[data-testid=…]')`）/ FastAPI + SQLAlchemy / pytest。

## Global Constraints

- **真正的 Issues 页是 `TodolistPage`（路由 `/team/:teamId/todolist`）**；`pages/IssuesPage.tsx` 是无人链接的 PR-D6 遗留原型，**本计划不碰它**
- UI 文本英文 + i18n key（en/zh 齐平，带 inline default fallback：`t('key', 'Default')`）；新组件状态色一律语义 token（`text-warn`/`bg-warn-soft`/`border-warn-line`、`agent`、`ok`、`info`），不引入旧色相类名
- `issues` Realtime 发布白名单（mig 172）**排除** `execution_state`/`dbos_workflow_id`——Realtime 事件只能当"去重取"信号，绝不 local merge 这些字段
- `set_status`（`backend/app/workflows/issue_lifecycle.py:64-108`）对 `execution_state` 是**整体覆盖写**——任何新 key 的写入必须用独立的 jsonb merge UPDATE，且要接受被后续 set_status 覆盖（轮次数据每轮重写，可接受）
- 详情页提问卡的回复必须复制 `agent_dispatched===false → AgentNotDispatchedError` 错误分型（CLAUDE.md「触发路径必须类型化失败回显」）
- 新组件必须自带 `data-testid`（现有测试风格用 `container.querySelector` 而非 getByRole）
- 每个 task 独立 commit，消息中文；测试 `cd frontend && npx vitest run components/Todolist` / `cd backend && uv run pytest <file> -q`；触碰的 py 文件过 `uv run black --check` + `isort --check-only` + `flake8`
- 两个 PR：Task 1-5 = PR「A2 任务协作时间线」；Task 6-9 = PR「A1 Issues 页注意力层」。每个 PR 合并可独立上线

## 文件结构

| 动作 | 路径 | 职责 |
|------|------|------|
| 修改 | `frontend/components/Todolist/IssueDetailView.tsx` | 两栏布局 + 右栏（进度/关联卡）+ 提问卡挂载 |
| 修改 | `frontend/components/Todolist/IssueChatThread.tsx` | AgentRunEvent 渲染 meta 富信息 + 运行组折叠 |
| 新建 | `frontend/components/Todolist/runGrouping.ts` + `.test.ts` | 纯函数：连续 agent_run 消息折叠为运行组 |
| 新建 | `frontend/components/Todolist/NeedsInputCard.tsx` + `.test.tsx` | 详情页提问卡（内联回复） |
| 新建 | `frontend/components/Todolist/AttentionStrip.tsx` + `.test.tsx` | 「等我的」横条（唯一新列表组件） |
| 新建 | `frontend/components/Todolist/attentionItems.ts` + `.test.ts` | 纯函数：三类注意力项聚合 |
| 修改 | `frontend/components/Todolist/IssueListView.tsx` | 挂载 AttentionStrip + 两个 chip |
| 修改 | `frontend/components/Todolist/IssueBoardView.tsx` | 看板行同款 chip |
| 修改 | `frontend/pages/TodolistPage.tsx` | Realtime merge bug 修复 |
| 修改 | `backend/app/workflows/issue_lifecycle.py` | dispatch 循环写 `execution_state.turn` |
| 修改 | `frontend/public/locales/en.json` + `zh.json` | i18n |

---

### Task 1: 后端 —— dispatch 循环写轮次到 execution_state.turn

**Files:**
- Modify: `backend/app/workflows/issue_lifecycle.py`（`_run_dispatch_with_continuation` 循环体，约 649-770 行）
- Test: `backend/tests/test_issue_turn_progress.py`（新建）

**Interfaces:**
- Produces: 每次 run_turn/run_reply 之前，`issues.execution_state` 被 merge 写入 `{"turn": <int 从1起>, "turn_started_at": <iso8601>}`。**必须是 jsonb merge（`||`），不是覆盖**——与 `set_status` 的覆盖写并存：终态时 set_status 覆盖掉 turn 是预期行为（终态行不再显示轮次）
- Produces: 模块级 `mark_turn_progress(issue_id: int, turn: int) -> None`（async，注入循环用；软失败只记日志）

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_issue_turn_progress.py
"""A1-2 逐轮进度：dispatch 循环每轮把 turn/turn_started_at merge 进 execution_state。

merge（||）而非覆盖——set_status 的覆盖写在终态抹掉 turn 是预期；
运行中列表页靠 REST 拉取显示「第N轮」。"""

from unittest.mock import AsyncMock

from app.workflows.issue_lifecycle import _run_dispatch_with_continuation


async def test_each_turn_marks_progress_before_running():
    marks = []

    async def mark(issue_id, turn):
        marks.append(turn)

    async def run_turn(issue_row, agent_id, user_id, is_continuation=False):
        return {"content": "x", "outcome": "continue", "reason": None}

    await _run_dispatch_with_continuation(
        1, {"id": 1}, "agent", "u1",
        run_turn=run_turn,
        set_status=AsyncMock(),
        load_issue=AsyncMock(return_value={"status": "in_progress"}),
        max_continuations=2,
        mark_turn=mark,
    )
    # 1 初始 + 2 continuation = 3 轮，每轮跑前标记
    assert marks == [1, 2, 3]


async def test_mark_turn_absent_keeps_behavior():
    """不注入 mark_turn（默认 None）→ 行为与现状一致，零调用。"""
    async def run_turn(issue_row, agent_id, user_id, is_continuation=False):
        return {"content": "x", "outcome": "completed", "reason": "done"}

    res = await _run_dispatch_with_continuation(
        1, {"id": 1}, "agent", "u1",
        run_turn=run_turn,
        set_status=AsyncMock(),
        load_issue=AsyncMock(return_value={"status": "in_progress"}),
    )
    assert res["outcome"] == "completed"


async def test_mark_turn_failure_never_breaks_dispatch():
    """mark 抛错被吞——进度装饰不允许影响执行。"""
    async def mark(issue_id, turn):
        raise RuntimeError("db down")

    async def run_turn(issue_row, agent_id, user_id, is_continuation=False):
        return {"content": "x", "outcome": "completed", "reason": None}

    res = await _run_dispatch_with_continuation(
        1, {"id": 1}, "agent", "u1",
        run_turn=run_turn,
        set_status=AsyncMock(),
        load_issue=AsyncMock(return_value={"status": "in_progress"}),
        mark_turn=mark,
    )
    assert res["outcome"] == "completed"
```

- [ ] **Step 2: 确认红** `cd backend && uv run pytest tests/test_issue_turn_progress.py -q` → TypeError（`mark_turn` 参数不存在）
- [ ] **Step 3: 实现** — ① `_run_dispatch_with_continuation` 签名加 `mark_turn: Optional[Callable[..., Awaitable[None]]] = None`；循环内每次 `run_turn`/`run_reply` 调用前 `turn_no += 1`，若 mark_turn 非 None 则 `try: await mark_turn(issue_id, turn_no) except Exception: logger.warning(...)`（外层包 try 吞错）；② 模块级实现：

```python
async def mark_turn_progress(issue_id: int, turn: int) -> None:
    """A1-2 逐轮进度装饰写。jsonb merge——绝不覆盖 set_status 写的键。"""
    from app.db import engine as db_engine

    await db_engine.execute_as_service_role(
        """UPDATE public.issues
           SET execution_state = COALESCE(execution_state, '{}'::jsonb)
               || jsonb_build_object(
                   'turn', CAST(:turn AS integer),
                   'turn_started_at', to_char(now() AT TIME ZONE 'utc',
                                              'YYYY-MM-DD"T"HH24:MI:SS"Z"'))
           WHERE id = :iid""",
        {"turn": turn, "iid": issue_id},
    )
```

③ `execute_issue` 调用处接线 `mark_turn=mark_turn_progress`
- [ ] **Step 4: 全绿** `uv run pytest tests/test_issue_turn_progress.py tests/test_issue_continuation.py -q`（后者保回归）+ black/isort/flake8
- [ ] **Step 5: Commit** `feat(issues): dispatch 循环逐轮 merge 写 execution_state.turn — 列表页第N轮数据源`

### Task 2: A2 —— 详情页两栏布局 + 右栏进度/关联卡

**Files:**
- Modify: `frontend/components/Todolist/IssueDetailView.tsx`（342-511 主内容块两栏化；:400 顺手修 TS2322）
- Test: `frontend/components/Todolist/IssueDetailView.test.tsx`（新建——详情页当前零测试）

**Interfaces:**
- Consumes: `usageService.getIssueUsage(issueId)` 返回 `{ run_count, total_tokens, cost_cents, ... }`（run_count 已返回从未显示）；`issue.raw.started_at`、`issue.raw.execution_state?.turn`
- Produces: 布局 `md:grid md:grid-cols-[1.5fr_1fr]`（<760px 单栏）；右栏两张卡 `data-testid="detail-progress-panel"` / `data-testid="detail-links-panel"`；原正文流里的 SubtaskBar/PipelineRunStrip/IssueCostLine/项目 chip 移入右栏（DeliverablesZone、StageBriefMirror、动作按钮组留在左栏）

- [ ] **Step 1: 写失败测试** — mock `usageService`（`getIssueUsage` 返回 `{run_count: 2, total_tokens: 14000, cost_cents: 20, ...}`）与 `issueMessageService`/`issueChatSocket`/supabase（照 IssueChatThread.test.tsx 的 vi.mock 风格），render 后断言：① `[data-testid="detail-progress-panel"]` 存在且文本含 `Status`、`Runs`、执行者名；② `[data-testid="detail-links-panel"]` 含 Project 名；③ 窄容器仍渲染（不断言布局，只断言两卡都在 DOM）
- [ ] **Step 2: 确认红**
- [ ] **Step 3: 实现** — 参照视觉稿右栏两卡（进度：Status/Runs·turns/Elapsed·Cost/Assignee；关联：Project/Deliverables 计数/Sub-issues/Pipeline 下一站）。字段来源：Status=issue.status 语义色文字；Runs=`getIssueUsage.run_count` + `execution_state.turn` 有值时 `· turn N`；Elapsed=`started_at` 起算（复用 `formatElapsed`，已有纯函数）；Cost=IssueCostLine 内容并入（组件保留复用，issueId 传 `String(issue.id)` 修 TS2322）；Assignee=agentsById 取名。i18n keys：`issueDetail.progress`、`issueDetail.links`、`issueDetail.runs`、`issueDetail.elapsed`（en: Progress/Links/Runs/Elapsed；zh: 进度/关联/运行/耗时）
- [ ] **Step 4: 全绿** `npx vitest run components/Todolist/IssueDetailView.test.tsx` + `npx tsc --noEmit` 确认 :400 的 TS2322 消失且无新增错误
- [ ] **Step 5: Commit** `feat(issues): 详情页两栏化 — 右栏进度/关联轨道（含 run_count 首次显示 + TS2322 修复）`

### Task 3: A2 —— 运行组折叠卡 + 运行卡富信息

**Files:**
- Create: `frontend/components/Todolist/runGrouping.ts` + `runGrouping.test.ts`
- Modify: `frontend/components/Todolist/IssueChatThread.tsx`（AgentRunEvent :124-201 与消息分派 :302-314）

**Interfaces:**
- Consumes: `IssueMessage` 列表；`kind==='agent_run'` 行的 `meta`（8 key：`status/liveness_state/cost_cents/model/prompt_tokens/completion_tokens/error_code/continuation_attempt`）+ `duration_seconds`
- Produces: `groupAgentRuns(messages: IssueMessage[]): TimelineEntry[]`，`TimelineEntry = { kind: 'run_group', runs: IssueMessage[], startedAt: string, totals: { tokens: number, costCents: number, durationSeconds: number }, anyRunning: boolean } | { kind: 'single', message: IssueMessage }`——**连续**（中间无 comment/deliverable 打断，system_status 不打断）的 ≥2 条 agent_run 折成一组；组卡 `data-testid="run-group-card"`，默认折叠显示 `▶ <Agent> run · N turns · Xm · Yk tok · ¥Z`，展开显示逐轮（原 AgentRunEvent）；单条 agent_run 直接用增强版 AgentRunEvent（显示 model/tokens/cost/duration，`data-testid` 保持 `agent-run-row` 兼容旧测试断言的现有 testid——先读现有测试确认名字再定）

- [ ] **Step 1: 写失败测试（纯函数）** — ① 3 条连续 agent_run → 1 个 run_group（totals 累加正确，tokens=prompt+completion 求和）；② agent_run, comment, agent_run → 两个 single + comment 原样（不跨 comment 分组）；③ agent_run 之间夹 system_status → 仍归同组（status 事件不打断运行组）；④ 单条 agent_run → single；⑤ 组内任一 `meta.status==='running'` → `anyRunning: true`
- [ ] **Step 2: 确认红**
- [ ] **Step 3: 实现纯函数**（注意 `meta` 值可能缺失/字符串数字，用 `Number(x) || 0` 防御）
- [ ] **Step 4: 组件接线** — IssueChatThread 分派处：coalesceSystemStatus 之后先跑 groupAgentRuns；新增 `RunGroupCard` 子组件（agent 语义色 `border-agent-line bg-agent-soft`，折叠态 header + 展开态逐轮渲染现有 AgentRunEvent）；AgentRunEvent 增补一行 meta 信息（`model · Nk tok · ¥x.xx · Xs`，缺失字段跳过）。组件测试补两条：折叠组渲染 header 数字、点击展开出现逐轮行
- [ ] **Step 5: 全绿** `npx vitest run components/Todolist` （IssueChatThread.test.tsx 既有用例必须全过——若 testid 变更导致失败，改实现不改旧断言语义）
- [ ] **Step 6: Commit** `feat(issues): 时间线运行组折叠卡 — 多轮 dispatch 不再淹没对话（meta 富信息首次渲染）`

### Task 4: A2 —— needs_input 提问卡（详情页内联回复）

**Files:**
- Create: `frontend/components/Todolist/NeedsInputCard.tsx` + `NeedsInputCard.test.tsx`
- Modify: `frontend/components/Todolist/IssueDetailView.tsx`（Tab 条上方挂载）

**Interfaces:**
- Consumes: 判据 `issue.status === 'needs_followup' && (issue.raw.execution_state as any)?.agent_outcome === 'needs_input'`；问题文本 `execution_state.outcome_reason`；回复 `postIssueMessage(issue.id, { body })`（`issueMessageService.ts`）与 `AgentNotDispatchedError`（`issueMessageService.ts:92-97`）
- Produces: `<NeedsInputCard question onSubmit pending dispatchError />`，`data-testid="needs-input-card"`；warn 语义色（`border-warn-line bg-warn-soft`）；含 textarea + Reply 按钮；`agent_dispatched===false` → 卡内 warn 提示（i18n `issueDetail.answerNotDispatched`，文案与 `taskCenter.answerNotDispatched` 同款）且**不恢复草稿**；网络错误 → 恢复草稿可重试（照抄 NeedsInputSection.test.tsx 的分型断言思路）

- [ ] **Step 1: 写失败测试** — ① 有问题文本渲染卡与原文；② 提交调 onSubmit 且成功后输入清空；③ AgentNotDispatchedError → 显示 not-dispatched 提示、textarea 空；④ 普通 reject → 草稿保留
- [ ] **Step 2: 确认红**
- [ ] **Step 3: 实现 + 挂载**（IssueDetailView 内判据成立才渲染，位置在 Tab 条上方、DeliverablesZone 之后；回复成功后调已有的消息刷新路径——WS/Realtime 会带回状态翻转，卡片随 issue.status 变化自然消失）。i18n：`issueDetail.agentAsking`（en: "Agent needs your answer"）、`issueDetail.replyHere`（en: "Reply here…"）
- [ ] **Step 4: 全绿** + tsc
- [ ] **Step 5: Commit** `feat(issues): 详情页 needs_input 提问卡 — 内联回复原地续跑（类型化失败回显）`

### Task 5: A2 收尾 —— 交付物卡样式 + IssueRelatedTab 吞错修复 + PR

**Files:**
- Modify: `frontend/components/Todolist/IssueChatThread.tsx`（DeliverableFiledEvent :107-122 升级绿色卡样式）
- Modify: `frontend/components/Todolist/IssueRelatedTab.tsx`（:34,:40 两处 `catch {}` → `catch (err) { console.error(...) }`）

- [ ] **Step 1: DeliverableFiledEvent 样式对齐视觉稿**（`border-ok-line bg-ok-soft` 绿色卡 + 📎 + 资源库落点文案；若 `ok-line/ok-soft` token 不存在则查 `index.css` @theme 用实际存在的 ok 系 token，都没有就用 `text-ok` + `border-line`——**不引入 emerald**）；既有 DeliverableFiledEvent 测试保持绿
- [ ] **Step 2: 吞错修复** + 全套回归 `npx vitest run components/Todolist && npx tsc --noEmit`
- [ ] **Step 3: Commit + PR** `feat(issues): 任务协作时间线（A2）` — push + `gh pr create --base master`，PR 描述引用 spec §A2，注明「A2 独立可上线，A1 在后续 PR」

### Task 6: A1 —— TodolistPage Realtime merge bug 修复（running chip 闪灭）

**Files:**
- Modify: `frontend/pages/TodolistPage.tsx`（:256-265 UPDATE 分支）
- Test: `frontend/pages/TodolistPage.test.tsx`（新建，仅测 merge 纯逻辑——将 merge 抽为可导出纯函数）

**Interfaces:**
- Produces: `mergeRealtimeIssue(prev: UiIssue, incoming: Issue, agentsById, projectsById): UiIssue`（导出纯函数）——mig 172 白名单外的字段（`dbos_workflow_id`/`execution_state`/`execution_locked_at`）从 `prev.raw` 保留，白名单内字段取 incoming

- [ ] **Step 1: 写失败测试** — ① incoming 缺 `dbos_workflow_id` 时 merge 结果保留 prev 的值（isLive 不翻转）；② incoming 的 `status`/`title` 更新生效；③ prev 无 raw 兜底不炸
- [ ] **Step 2: 确认红** → **Step 3: 实现 merge 并替换 :262-263 的整行替换** → **Step 4: 全绿** → **Step 5: Commit** `fix(issues): Realtime UPDATE 整行替换致 running chip 闪灭 — 白名单外字段 merge 保留`

### Task 7: A1 —— 两个 chip（running 后缀 + 等你回复）

**Files:**
- Create: `frontend/components/Todolist/issueChips.ts` + `issueChips.test.ts`（纯函数：chip 内容推导）
- Modify: `frontend/components/Todolist/IssueListView.tsx`（:202-207）、`IssueBoardView.tsx`（:42-47）

**Interfaces:**
- Produces: `runningChipLabel(issue: UiIssue, now: Date): string | null` —— isLive 时返回 `running` 或 `running · turn N · Xm`（turn 取 `execution_state.turn`，elapsed 取 `started_at`；二者缺失则只 `running`）；`needsReplyChip(issue: UiIssue): { question: string | null } | null` —— `status==='needs_followup' && execution_state.agent_outcome==='needs_input'` 时非 null
- Produces: 列表与看板行尾新增等你回复 chip：warn 语义色、文本 `needs your reply`（i18n `issues.needsReplyChip`，看板短版直接复用）、`title` 属性 = 提问原文（tooltip）；running chip 文本换为 `runningChipLabel` 输出（样式类不动——现有 amber 保持，避免本 PR 扩散重着色）

- [ ] **Step 1: 写失败测试（纯函数）** — ① running + turn 2 + started 4 分钟前 → `running · turn 2 · 4m`；② running 无 turn → `running`；③ done → null；④ needs_followup + needs_input + reason → `{question: reason}`；⑤ needs_followup 无 agent_outcome → null
- [ ] **Step 2: 确认红** → **Step 3: 实现 + 两个视图接线**（IssueListView.test.tsx 既有 running 用例保持绿；新增两条：等你回复 chip 渲染与 title 内容）
- [ ] **Step 4: 全绿** `npx vitest run components/Todolist` → **Step 5: Commit** `feat(issues): running chip 轮次/耗时后缀 + needs_followup 等你回复 chip`

### Task 8: A1 —— AttentionStrip「等我的」横条

**Files:**
- Create: `frontend/components/Todolist/attentionItems.ts` + `attentionItems.test.ts`
- Create: `frontend/components/Todolist/AttentionStrip.tsx` + `AttentionStrip.test.tsx`
- Modify: `frontend/components/Todolist/IssueListView.tsx`（:765 落点，Quick 行后、project context bar 前）

**Interfaces:**
- Consumes: `useTaskManager().needsInputItems`（`NeedsInputItem[]`，issue_id 为 string）；`aiLibraryService.listApprovalRequests()`（`AILibraryApprovalRequest[]` + `approveRequest`/`rejectRequest`）；`scopedIssues`（IssueListView 作用域内现成，筛 `status==='in_review'`）
- Produces: `buildAttentionItems(needsInput, approvals, inReviewIssues): AttentionItem[]`，`AttentionItem = { type: 'question'|'approval'|'review', id: string, title: string, detail: string | null, issueId?: number }`；`<AttentionStrip items collapsed onToggle onApprove onReject />` `data-testid="attention-strip"`——空 items **整条零渲染**；卡片三色（question=warn / approval=info / review=ok）；question 卡点击滚到/导航该 issue 详情（`/team/{teamId}/todolist/{identifier}`——needsInputItems 无 identifier，需从 IssueListView 的 `issues` state 按 id 匹配取 identifier，匹配不到则退化为不可点）；approval 卡内联 Approve/Reject 按钮；review 卡点击进详情；折叠态存 localStorage key `mediahub:todolist:attention`（照 `IssueColumnPicker.tsx:54-78` 的 load/save 函数对模式）

- [ ] **Step 1: 写失败测试（纯函数）** — ① 三类输入各 1 → 3 项且 type 正确、question 卡 detail=提问原文；② 全空 → `[]`
- [ ] **Step 2: 组件失败测试** — ① items 空 → 容器 null（`container.firstChild === null`）；② 3 items → 计数徽章 `3` + 三张卡；③ 点折叠 → 卡片隐藏 + localStorage 写入（mock localStorage）；④ approval 卡 Approve 点击调 onApprove(id)
- [ ] **Step 3: 确认红** → **Step 4: 实现**（横条容器 warn 边框 `border-warn-line bg-warn-soft`；标题 `⏸ Waiting on you N`（i18n `issues.waitingOnYou`）；横向滚动 `overflow-x-auto`；approvals 数据在 IssueListView 挂载时拉一次 + 60s 轮询——与 TopBar 的 ApprovalsPanel 同款节奏）
- [ ] **Step 5: 全绿** `npx vitest run components/Todolist` + tsc → **Step 6: Commit** `feat(issues): AttentionStrip 等我的横条 — 提问/审批/待验收三类聚合`

### Task 9: A1 收尾 —— i18n 齐平 + 全量回归 + PR

- [ ] **Step 1: i18n 核对** — 新增 keys 在 en.json/zh.json 齐平：`issues.needsReplyChip`（Needs your reply/等你回复）、`issues.waitingOnYou`（Waiting on you/等我的）、`issueDetail.*`（Task 2/4 已加，最终核对一遍）；`npx vitest run components/Todolist && npx tsc --noEmit`
- [ ] **Step 2: e2e 基线检查** — `frontend/e2e/issue-list-parity.spec.ts` 是全 stub 截图对比，AttentionStrip 空态零渲染意味着 stub 数据不含注意力项时截图不变；若 spec 失败则更新基线截图并在 PR 里说明
- [ ] **Step 3: Commit + PR** `feat(issues): Issues 页注意力层（A1）` — push + PR，描述引用 spec §A1 三处微改表格

## Self-Review 记录

- Spec §A1 表格 3 行 → Task 7（chip×2）/ Task 8（横条）；§A2 四要点 → Task 3（运行卡）/ Task 4（提问卡）/ Task 5（交付物卡）/ Task 2（右栏）；§A3 表格中「运行进度→UI 半通」→ Task 1（turn 写入）+ Task 7（消费）。审批卡的 issue 关联缺口（approval 表无 issue_id）按侦察结论降级为 agent 级卡片内联批准，不做 run_id→issue 反查（YAGNI，spec 未要求深链）。
- 「第N轮」数据源 spec 原文假设 task_tracking 透传，侦察证实 issue dispatch 无 task 行——本计划以 Task 1 的 execution_state.turn 替代，语义等价、改动更小，已在 Global Constraints 说明。
- 类型一致性：`NeedsInputItem.issue_id: string` 与 `Issue.id: number` 的混用点只在 Task 8 identifier 匹配处，已注明 `Number()` 转换。
