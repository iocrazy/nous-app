# 蓝图差距收口计划（对齐视觉稿 4 处降级项）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** 把已上线实现与视觉稿 `2026-08-02-collab-redesign-mockup.html` 的 4 处偏离补齐：运行卡接会话流深链、issue 轮次近实时、AttentionStrip 跨视图、工作台等你回复卡带提问原文。

**Architecture:** 全部是小改（一个 PR）。数据面仅一处端点投影扩展（needs-input 列表补 assignee_agent_id）。

**Tech Stack:** React 19 + TS / vitest；FastAPI + pytest。

## Global Constraints

- 语义色 token；i18n en/zh 齐平带 inline fallback；新组件带 data-testid
- 前端基线：master `npx vitest run` 全绿、tsc 91 行不许新增；后端 SOCKS 环境性失败不算
- 单 PR：`feature/blueprint-gap-closure`，全部 task 各自 commit

---

### Task 1: 运行组卡「展开对话流」深链会话视图

**Files:**
- Modify: `frontend/components/Todolist/IssueChatThread.tsx`（RunGroupCard header）
- Modify: `frontend/components/Todolist/IssueDetailView.tsx`（把 issue.raw.ai_session_id 传给 IssueChatThread——先查现有 props 链，若 thread 已拿到 issue 对象则免）

**Interfaces:**
- Consumes: `issues.ai_session_id`（GET /issues/{id} 已返回；A3 表「issue → 会话已通：issue chat 即 conversation」）；会话视图路由 `/team/{teamId}/ai-library/sessions/{sessionId}`（router.tsx 既有 `sessions/:sessionId`）
- Produces: RunGroupCard header 右侧「Open conversation →」链接（i18n `issueDetail.openConversation`，en "Open conversation"，zh "展开对话流"），`ai_session_id` 为空则不渲染链接；`data-testid="run-group-open-conversation"`

- [x] **Step 1: 失败测试** — RunGroupCard 在有 sessionId 时渲染链接且 href 含 `/ai-library/sessions/{id}`；无 sessionId 不渲染
- [x] **Step 2: 红 → 实现 → 绿**（components/Todolist 全套回归）
- [x] **Step 3: Commit** `feat(issues): 运行组卡展开对话流 — 深链会话聚合视图（视觉稿 A2 对齐）`

### Task 2: issue 轮次/耗时近实时

**Files:**
- Modify: `frontend/pages/TodolistPage.tsx`（Realtime UPDATE 分支）
- Test: `frontend/pages/todolistRealtime.test.ts`（新建，纯函数）

**Interfaces:**
- 背景：mig 172 白名单排除 `execution_state`，UPDATE 事件会到但 payload 缺该列，mergeRealtimeIssue 保留旧值 → turn 永远陈旧
- Produces: `shouldRefetchOnRealtime(prev: UiIssue | undefined, incoming: Partial<Issue>): boolean`（导出纯函数）——incoming 行对应的 prev 是 live（有 dbos_workflow_id 且非终态）时返回 true；TodolistPage 在 merge 之后对 true 的行调度 **单行 debounce 重取**（`getIssue(id)`，800ms debounce per-issue，参照 TaskManagerContext 的"事件即重取信号"注释与思路），结果再走 mergeRealtimeIssue 融合（这次 incoming 带全量 execution_state）
- 兜底：任何重取失败静默保留 merge 结果（不比现状差）

- [x] **Step 1: 失败测试** — ① prev live → true；② prev 已终态 → false；③ prev 缺失 → false
- [x] **Step 2: 红 → 实现 → 绿**（含 mergeRealtimeIssue 既有测试回归）
- [x] **Step 3: Commit** `feat(issues): live 行 Realtime 事件触发单行重取 — turn/耗时近实时（mig 172 白名单下的正确姿势）`

### Task 3: AttentionStrip 跨视图（提升到 TodolistPage 层）

**Files:**
- Modify: `frontend/components/Todolist/IssueListView.tsx`（摘除 strip 挂载与 approvals 拉取逻辑,导出复用所需的连接件——以实际实现为准最小搬运）
- Modify: `frontend/pages/TodolistPage.tsx`（工具栏与视图容器之间挂载 strip，list/board 共享）
- Test: 既有 AttentionStrip 测试保持绿；TodolistPage 层新增一条挂载断言（若 page 无测试基建则以 IssueListView.test 的现有 strip 用例迁移为准）

**Interfaces:**
- Produces: strip 在 list 与 board 两个视图下都渲染（数据与折叠状态同一份，localStorage key 不变）；IssueListView 内不再重复渲染

- [x] **Step 1: 迁移挂载 + 测试跟随** → 全套回归
- [x] **Step 2: Commit** `feat(issues): AttentionStrip 提升到页面层 — 列表/看板跨视图（视觉稿 A1 对齐）`

### Task 4: 工作台等你回复卡带提问原文

**Files:**
- Modify: `backend/app/api/issues_router.py`（needs-input 列表投影补 `assignee_agent_id: str|null` 与 `identifier: str|null`）
- Modify: `backend/tests/api/test_issues_needs_input.py`（既有测试文件追加断言）
- Modify: `frontend/services/issuesService.ts`（NeedsInputItem 加两字段）
- Modify: `frontend/components/AILibrary/AgentWorkbenchTab.tsx`（计数卡升级为 waitcard 列表：提问原文 + 「去回复」深链 `/team/{teamId}/todolist/{identifier}`）
- Test: workbench 渲染测试（有 items 渲染原文；空则整卡不渲染）

**Interfaces:**
- Produces: `GET /issues/needs-input` items 增加 `assignee_agent_id`（str 序列化）与 `identifier`；前端 workbench 过滤 `assignee_agent_id === agent.id` 渲染 waitcard（warn 语义色，样式对齐视觉稿 §02 waitcard）

- [x] **Step 1: 后端失败测试 → 红 → 实现 → 绿** + style 三件套
- [x] **Step 2: 前端失败测试 → 红 → 实现 → 绿**
- [x] **Step 3: Commit** `feat(ai-library): 工作台等你回复卡带提问原文与深链（视觉稿 B2 对齐）`

### Task 5: 回归 + PR

- [x] 前端全量 vitest + tsc（91 基线）+ e2e issue-list-parity；后端相关 suite
- [x] push + PR `feat: 蓝图差距收口 — 对话流深链/轮次近实时/横条跨视图/等你回复原文`

---

## 完成账（2026-08-04 落地，2026-09-09 补记入库）

四项全部由 **PR #1675（`f13e3ef7`，2026-08-04）** 一次交付，commit 标题与本稿 Task 5
写的 PR 标题一字不差。该 PR 带了 18 个文件（+616 / −55），但**没有把本计划稿一起提交** ——
这份稿子在工作树里滞留了一年多，2026-09-09 核对后补入库。

| Task | 交付位置（2026-09-09 复核） | 与计划的偏差 |
|---|---|---|
| 1 运行卡深链 | `IssueChatThread.tsx:329` 挂 `data-testid="run-group-open-conversation"`，href 在 `:478`；i18n `issueDetail.openConversation` 落在 `en.json` / `zh.json` 同为 5554 行；`IssueChatThread.test.tsx` 三例覆盖有/无 sessionId | 无 |
| 2 轮次近实时 | `shouldRefetchOnRealtime` 导出在 `mergeRealtimeIssue.ts:67`；`TodolistPage.tsx:330` 判定后调 `scheduleLiveRefetch(raw.id)`；6 例覆盖 live / 终态 / prev 缺失 | **测试落点不同** —— 计划写的是新建 `frontend/pages/todolistRealtime.test.ts`，实际并入既有的 `mergeRealtimeIssue.test.ts`。纯函数与它测的模块同文件，这样更对 |
| 3 AttentionStrip 跨视图 | `IssueListView.tsx:973` 挂载，位置在 list/board 分支（`:1012`）**之上**，两视图共享同一份数据与折叠态；`IssueListView.test.tsx:183-194` 对 `'list'` 与 `'board'` 各断言一次 | **做法不同、目标达成** —— 计划要把挂载搬到 `TodolistPage`，实际发现只要放在视图分支上方就等价，省掉两个文件间搬运连接件。`TodolistPage.tsx` 与 `IssueBoardView.tsx` 里都没有 `AttentionStrip`，是刻意的，不是漏改 |
| 4 等你回复卡带原文 | `schemas/issue.py::NeedsInputItem` 有 `assignee_agent_id` / `identifier`；`AgentWorkbenchTab.tsx:89` 按 `assignee_agent_id === agent.id && !!identifier` 过滤，`:257` 渲染 `issue.question ?? issue.title`，`:264` 深链 `/todolist/{identifier}`，testid `waitcard` / `waitcard-answer-link` | 无 |

⚠️ **Task 4 的 `NeedsInputItem` 后来被 harness 二期 2a 扩过**（`question_id` / `kind` /
`options` / `allow_free_text`，见 `2026-09-07-harness-p4-phase2a-control-plane.md`）。
那是在本计划的字段之上做增量，不是重做 —— 读这张表时别把 2a 的字段误认成本计划的产出。

**教训**：计划稿执行完没跟着入库，一年后就变成"看不出做没做"的孤儿文件。判断它是否还有效
花的力气（逐项 grep 四个 Task 的落点）远超当初随 PR 提交它的成本。**计划稿应与实现同 PR 提交，
完成账在收尾 Task 里追加** —— 这正是 `2026-09-07-harness-p4-phase2a-control-plane.md`
Task 9 的做法。
