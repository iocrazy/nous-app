# AI Library 管理面实施计划（第四批 B0-B6）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** AI Library 从「侧栏 30+ 项平铺 + 8 tab 编辑器 + 两栏文件树」收敛为「侧栏 7 项 + 分组卡片总览 + 详情三区 + 技能卡片库」——剧组花名册式管理面。

**Architecture:** 先落数据层（`agent_group` 列 + 批量 stats 端点 + skills 列表反查聚合），再自上而下改 UI（B0 侧栏/路由 → B1 总览 → B2 详情三区 → B3/B4 技能 → B5/B6 modal）。系统 preset 的 override 权限模型**零改动**——「官方模板」只是文案与徽章层。Spec: `docs/superpowers/specs/2026-08-02-collab-surface-and-ai-library-redesign-design.md` §B；视觉稿 `2026-08-02-collab-redesign-mockup.html` §B0-B7 + 组件规格表 + 迁移映射表。

**Tech Stack:** React 19 + TS / vitest 纯函数单测为主（AILibrary 现有风格无组件渲染测试，新增组件测试用 @testing-library 但状态推导一律抽纯函数）/ FastAPI + SQLAlchemy + pytest（AsyncMock 打桩，参照 `test_ai_library_dashboard.py` 样板）。

## Global Constraints

- 视觉贴 K1 warm-paper：状态徽章**文字徽章** 运行中=`agent`（李紫）/ 等回复=`warn`（赭）/ 故障=`danger`（砖红）/ 空闲=灰点（`text-ink-500`）；写法 `border border-agent-line bg-agent-soft text-agent` 系；**顺手替换触碰文件里的 emerald/indigo/amber 旧色相**
- 主按钮随状态：空闲→Chat / 运行中→查看运行 / 等回复→去回复 / 故障→修复指引；故障徽章必须携带一行可操作原因（warnline）
- 系统 preset 语义改「官方模板」：**后端权限模型不动**（override 机制即模板+定制语义），只改文案徽章；Fork 不复制技能绑定是后端现状，前端 fork 成功后补一次 PATCH skill_ids
- migration 序号从 **398** 起用前先 `ls supabase/migrations | tail -3` 确认，占用则顺延；列名用 `agent_group`（`group` 是 SQL 保留字）
- **`seed_loader._upsert_agent` 的 `_sha()` 必须加入 agent_group**，否则改分组重启不生效（skip 分支）
- `getAvailableModels` 保持具名导出（`AgentEditor.test.ts:8` 依赖）；AgentEditor 拆分时 hooks 全部保持在早返回之前（React #310 前科，`AgentEditor.tsx:326-331` 注释）
- B0 改路由同步三处：`AgentsPage.tsx:16` / `SkillsPage.tsx:25` 的 `includes('/ai-library/')`、`router.tsx:231-237` legacy 顶层路由、`ai-library:agents-changed` 事件桥
- i18n：`aiLibrary.*` 命名空间，带 inline default fallback（`t('key', 'Default')`），en/zh 齐平；UI 文本英文
- 每 task 独立 commit（中文消息）；前端 `npx vitest run components/AILibrary` + `npx tsc --noEmit`（对照 master 基线）；后端 pytest + black/isort/flake8
- **四个 PR**：PR-B1 = Task 1-4（数据层+侧栏+总览）；PR-B2 = Task 5-6（详情三区）；PR-B3 = Task 7-8（技能库+编辑页）；PR-B4 = Task 9-10（新建/Fork + 例行任务）。每个独立可上线

## 文件结构

| 动作 | 路径 | 职责 |
|------|------|------|
| 新建 | `supabase/migrations/398_ai_agents_group.sql` | agent_group 列 |
| 修改 | `backend/app/models/ai.py` / `schemas/ai_library.py` / `services/ai/runner/seed_loader.py` | group 贯通 + seed 默认分组 |
| 修改 | `backend/app/api/ai_library_router.py` | `GET /agents/stats` 批量端点 + `GET /skills` 列表补 agents |
| 修改 | `backend/app/repositories/skill_repository.py` | agents 反查批量聚合 |
| 新建 | `frontend/components/AILibrary/agentStatus.ts` + `.test.ts` | 纯函数：状态/主按钮推导 |
| 新建 | `frontend/components/AILibrary/AgentGalleryPage.tsx`（+子组件 AgentCard） | B1 总览卡片页 |
| 修改 | `frontend/components/AILibrary/AILibrarySidebar.tsx` + `router.tsx` | B0 侧栏瘦身 |
| 修改 | `frontend/components/AILibrary/AgentEditor.tsx` → 拆出 `AgentWorkbenchTab.tsx` / `AgentPersonaTab.tsx` / `AgentProfileTab.tsx` / `agentEditorModel.tsx` | B2 三区 |
| 修改 | `frontend/components/AILibrary/SkillList.tsx` / `SkillsTab.tsx` → 新建 `SkillGallery.tsx` | B3 卡片化 |
| 修改 | `frontend/components/AILibrary/SkillEditor.tsx` | B4 文件页签 |
| 修改 | `frontend/components/AILibrary/NewAgentModal.tsx` | B5 模板优先 |
| 修改 | `frontend/components/AILibrary/AgentRoutinesTab.tsx` + 新建 `NewRoutineModal.tsx` | B6 |

---

### Task 1: 数据层 —— agent_group 列 + seed 默认分组

**Files:**
- Create: `supabase/migrations/398_ai_agents_group.sql`（先 `ls supabase/migrations | tail -3` 确认 398 空闲，占用则顺延并全文替换号码）
- Modify: `backend/app/models/ai.py`（AiAgents 加列）、`backend/app/schemas/ai_library.py`（AgentBase/AgentCreate/AgentUpdate）、`backend/app/services/ai/runner/seed_loader.py`
- Modify: `frontend/types.ts`（AILibraryAgent 加 `agent_group?: string | null`）
- Test: `backend/tests/test_agent_group_seed.py`（新建）

**Interfaces:**
- Produces: `ai_agents.agent_group TEXT NULL`；seed preset 默认分组映射（`seed_loader.py` 顶部常量）：

```python
# 分组口径来自设计稿 §B1：编剧组(writing)/美术组(art)/工具组(tools)
AGENT_GROUP_BY_SLUG = {
    "script_ai": "writing", "storyboard": "writing", "summarize": "writing",
    "character_portrait": "art", "character_persona": "art", "character_expression": "art",
    "character_turnaround": "art", "location_design": "art", "location_visual": "art",
    "prop_design": "art", "prop_visual": "art",
    "analyze": "tools", "caption": "tools", "classify": "tools",
    "coordinator": "tools", "topic_scorer": "tools", "translate": "tools",
}
# ⚠️ 以 backend/seeds/agents/ 实际目录名为准逐一核对 slug（下划线/连字符差异），
# 目录里有而表里没有的 slug 落 "tools"。
```

- Produces: Fork 的 agent 继承来源分组（`create_agent` fork 分支把源行 agent_group 带入默认值，payload 显式传入则覆盖）

- [ ] **Step 1: migration**

```sql
-- 398: AI Library 管理面 B1（spec 2026-08-02）— agent 分组列。
-- 列名避开 SQL 保留字 group；NULL = 未分组（UI 落"工具组"兜底）。
ALTER TABLE public.ai_agents ADD COLUMN IF NOT EXISTS agent_group TEXT;
```

本地执行验证：`docker exec -i nous-db psql -U postgres -p 55434 -d postgres < supabase/migrations/398_ai_agents_group.sql` 后 information_schema 确认列存在
- [ ] **Step 2: 写失败测试** — ① `_read_agent_fields('script_ai', ...)` 返回含 `agent_group='writing'`；② `_sha` 参与字段含 agent_group（改分组 → hash 变 → 不走 skip）：直接断言 `_upsert_agent` 源码或对比两次 hash；③ AGENT_GROUP_BY_SLUG 的 key 与 `backend/seeds/agents/` 目录名集合一致（防拼写漂移的守卫测试）
- [ ] **Step 3: 确认红** → **Step 4: 实现**（models/schemas/seed_loader/fork 继承/types.ts 五处 + seed_hash）→ **Step 5: 全绿** `uv run pytest tests/test_agent_group_seed.py tests/ -q -k "seed_loader or ai_library_create" ` + style 三件套
- [ ] **Step 6: Commit** `feat(ai-library): ai_agents.agent_group 列 + seed 默认分组（writing/art/tools）`

### Task 2: 数据层 —— GET /agents/stats 批量端点

**Files:**
- Modify: `backend/app/api/ai_library_router.py`（挂在 list_agents 附近）
- Modify: `backend/app/repositories/agent_runs_repository.py`（7d 聚合查询）+ `issue_repository.py`（needs_input 按 agent 分组计数）
- Test: `backend/tests/test_ai_library_agent_stats.py`（新建，照 `test_ai_library_dashboard.py` 的 AsyncMock 样板）

**Interfaces:**
- Produces: `GET /api/v1/ai-library/agents/stats?days=7` → `{"items": {"<agent_id>": {"runs_7d": int, "tokens_7d": int, "running_count": int, "needs_input_count": int, "fault": {"kind": "budget"|"manual"|"dead_runs"|null, "detail": str|null}}}}`（agent_id 为 UUID 字符串 key）
  - runs/tokens：`agent_runs` 按 `agent_id` GROUP BY，`started_at >= now()-interval 'N days'`（SUM total_tokens, COUNT(*)）
  - running_count：同表 `status='running'` 计数
  - needs_input_count：`issues` 用现成谓词 `needs_input_predicate()` + `GROUP BY assignee_agent_id`（部分索引 `issues_assignee_agent_status_idx` 现成）
  - fault：`ai_agents.paused_reason`（budget→detail="Monthly budget exceeded — raise budget or resume"；manual→"Paused by admin"）优先；否则近 7d 有 `liveness_state IN ('stuck','dead')` 的 run → kind=dead_runs，detail 取最近一条 `error_message` 前 120 字符；无 → null
- 单次响应覆盖调用者可见的全部 agent（复用 list_agents 的可见性集合逻辑），前端一次请求替代 19 次 `/dashboard`

- [ ] **Step 1: 写失败测试** — ① mock 三个 repo 聚合返回 → 响应按 agent_id 汇合正确；② 无数据 agent → 全零 + fault null；③ paused_reason='budget' → fault.kind='budget' 且 detail 非空（spec「故障必须携带可操作原因」的钉子）
- [ ] **Step 2: 确认红** → **Step 3: 实现**（repo 三个聚合方法 + router 汇合；⚠️ 不在循环里查——每类一条 GROUP BY SQL）→ **Step 4: 全绿** + style → **Step 5: Commit** `feat(ai-library): agents/stats 批量端点 — 7d 统计/运行/等回复/故障一次拉齐`

### Task 3: B0 —— 侧栏瘦身 + 路由

**Files:**
- Modify: `frontend/components/AILibrary/AILibrarySidebar.tsx`（Chat 区 agent 平铺 :314-331、Agents 区 :339-406、Skills 区 :412-422 收敛）
- Modify: `frontend/router.tsx`（`ai-library` index → AgentGalleryPage 占位重定向，本 task 先保持 index 不变、只减侧栏项）
- Test: 快照式断言不适用（无渲染测试基建）——改为纯函数测试 `sidebarItems.test.ts`（若侧栏项列表抽成常量）；否则以 tsc + 手测为准并在 commit message 说明

**Interfaces:**
- Produces: 侧栏分区变为：`AI Library`（单入口 → `/ai-library`）/ Runtime(Workforce) / Chat(仅 Sessions + Storyboard，**删除 agent 逐行平铺**) / Insights 三项不动；Agents 分区整块删除（`+` 新建入口移入 B1 总览页）；`systemAgentsCollapsed` localStorage 逻辑与 `sidebar.*` 下失效 i18n key 一并清理
- 保留 `ai-library:agents-changed` 事件监听者迁移说明：侧栏不再持有 agents 列表后，事件桥消费者移至 AgentGalleryPage（Task 4 接手）

- [ ] **Step 1: 实现瘦身**（渐进：本 task 侧栏仍点向现有 AgentsPage 路由，B1 落地后 index 才换 Gallery——避免中间态死链）
- [ ] **Step 2: 三处同步检查** `grep -n "includes('/ai-library/')" frontend/pages/*.tsx` 确认无破坏；legacy 路由 :231-237 保留不动
- [ ] **Step 3: 回归** `npx vitest run components/AILibrary && npx tsc --noEmit` → **Step 4: Commit** `feat(ai-library): B0 侧栏瘦身 — agent/技能平铺收敛为单入口`

### Task 4: B1 —— 智能体总览卡片页

**Files:**
- Create: `frontend/components/AILibrary/agentStatus.ts` + `agentStatus.test.ts`
- Create: `frontend/components/AILibrary/AgentGalleryPage.tsx`（内含 AgentCard 子组件）+ `AgentGalleryPage.test.tsx`（组件测试：分组渲染/徽章/主按钮）
- Modify: `frontend/router.tsx`（`ai-library` index element → AgentGalleryPage；页内 tabs 智能体|技能|市场·留位，技能 tab 先链接到现有 SkillsPage 路由）
- Modify: `frontend/services/aiLibraryService.ts`（`getAgentStats(days=7)`）

**Interfaces:**
- Consumes: Task 2 端点；`useAgentRuns(userId).runningAgentIds`（实时运行）；`listAgents()` 现有
- Produces: 纯函数（**这是本 task 的测试重心**）：

```ts
export type AgentDerivedStatus =
  | { kind: 'running' }
  | { kind: 'needs_reply'; count: number }
  | { kind: 'fault'; detail: string }
  | { kind: 'idle' };

export function deriveAgentStatus(
  agent: AILibraryAgent,
  stats: AgentStatsItem | undefined,
  runningIds: Set<string>,
): AgentDerivedStatus;
// 优先级：fault > running(runningIds 实时 ∪ stats.running_count>0) > needs_reply(count>0) > idle

export function primaryAction(status: AgentDerivedStatus):
  { key: 'chat' | 'viewRuns' | 'goReply' | 'fixGuide' };
```

- Produces: 页面结构对齐视觉稿 §B1：顶部 pagetabs（`智能体 N | 技能 N | 市场(规划中)`）+ `+ New Agent` 按钮（打开 NewAgentModal）；筛选行（搜索 + 分组 fchip + `仅故障`）；李紫 live 横幅（`runningAgentIds` 非空时显示第一个运行中 agent，`data-testid="live-banner"`）；分组区块 writing/art/tools（`agent_group` 空落 tools），卡片含头像（AgentIconPicker 现有图标 + 组色 `av-agent/av-ok/av-info` 语义色背景）/名称/model/状态徽章/`官方模板|自建`+`N skills`+scope 徽章/7d meta/fault warnline/hover 操作行（主按钮随状态 + Fork 定制/配置 + 运行记录）
- 状态徽章色（Global Constraints 写法）；`官方模板` 徽章 i18n `aiLibrary.officialTemplate`（en: "Official template"）——**替换所有 "System preset" 文案处**

- [ ] **Step 1: agentStatus 失败测试** — ① paused_reason=budget → fault 且 detail 非空；② runningIds 含 id → running（即使 stats 为 undefined）；③ needs_input_count=2 → needs_reply(2)；④ 全无 → idle；⑤ fault 优先于 running；⑥ primaryAction 四态映射各一条
- [ ] **Step 2: 确认红** → **Step 3: 实现纯函数** → **Step 4: 页面与卡片实现 + 组件测试**（mock services：三个 agent 分属三组 → 三个分组块；fault agent 卡渲染 warnline 文本；主按钮文案随状态）
- [ ] **Step 5: 全绿** + tsc → **Step 6: Commit + PR-B1** `feat(ai-library): B1 智能体总览 — 分组卡片/状态徽章/主按钮随状态`（含 Task 1-3，push + PR，描述引用 spec §B0/B1）

### Task 5: B2 —— AgentEditor 拆分（工作台/人格与技能/档案）

**Files:**
- Modify: `frontend/components/AILibrary/AgentEditor.tsx`（骨架保留：hooks/save/header/tab nav；tab 内容改挂三个新子组件）
- Create: `frontend/components/AILibrary/agentEditorModel.tsx`（`getAvailableModels`/`PROVIDER_DISPLAY_NAMES`/`renderModelSelect` 平移，**保持具名导出**）
- Create: `frontend/components/AILibrary/AgentWorkbenchTab.tsx`（工作台 = AgentDashboardTab 精简嵌入 + AgentRunsSplit 入口 + 等你回复卡 + AgentRoutinesTab 开关卡 + 本周统计条）
- Create: `frontend/components/AILibrary/AgentPersonaTab.tsx`（人格文档三段切换 IDENTITY/SOUL/AGENT 复用 MarkdownEditor + 属性卡（model/temperature/maxTokens/scope/budget）+ SkillsSection 绑定开关 + PermissionsSection）
- Create: `frontend/components/AILibrary/AgentProfileTab.tsx`（VersionHistoryPanel + BudgetFields 成本区）
- Modify: `frontend/components/AILibrary/AgentEditor.test.ts`（import 路径 → `./agentEditorModel`）

**Interfaces:**
- Produces: SubTab 收敛为 `workbench | persona | profile`（i18n `aiLibrary.agents.tab.workbench/persona/profile`，en: Workbench/Persona & Skills/Profile）；旧 8 tab 的 URL 参数兼容：`dashboard|runs|routines→workbench`，`overview|files|skills|permissions→persona`，`versions→profile`（读 tab 参数处做映射，旧书签不 404）
- **保存边界**：单一保存按钮保留在 header（管 draft + localSkillIds，作用于 persona 区）；permissions 沿用独立保存（persona 区内原样嵌入）；workbench/profile 无编辑态
- 三个子组件的 props 全部由 AgentEditor 下传（draft/updateDraft/readOnly/catalogLocked/localSkillIds/skill 操作函数/agent 对象），**hooks 一律留在 AgentEditor 顶部早返回之前**

- [ ] **Step 1: 平移 agentEditorModel + 改测试 import** — `npx vitest run components/AILibrary/AgentEditor.test.ts` 保持绿（先做这一步锁住不回归）
- [ ] **Step 2: 三个子组件落地**（内容从现有 tab 块整块搬运：workbench←:535-537+:826+:828+dashboard 统计；persona←:539-733 表单拆（属性部分）+:735-774+:776-786+:788-824；profile←:830-842+BudgetFields）；AgentEditor 内 subTabs 数组与渲染分派改三项 + URL 兼容映射
- [ ] **Step 3: 等你回复卡**（workbench 右列）：`listNeedsInput()` 过滤 `assignee_agent_id === agent.id`……侦察确认 NeedsInputItem 无 agent 字段——**降级**：改用 Task 2 的 stats.needs_input_count > 0 时显示计数卡 + 「View in Issues」链接到 `/team/{teamId}/todolist`（不做逐条列表，避免为此扩端点；协作面 A1 已有逐条入口）。i18n `aiLibrary.agents.waitingReplies`（en: "{{count}} issue(s) waiting for your reply"）
- [ ] **Step 4: 回归** `npx vitest run components/AILibrary && npx tsc --noEmit`（AgentEditor.tsx 应显著变薄；React #310 手测：连续打开两个 agent 无白屏——在 commit message 记录）
- [ ] **Step 5: Commit** `refactor(ai-library): B2 AgentEditor 8 tab 归并三区 — 工作台/人格与技能/档案`

### Task 6: B2 收尾 —— PR

- [ ] **Step 1: 全量回归** `npx vitest run components/AILibrary && npx tsc --noEmit` → **Step 2: push + PR-B2**，描述引用 spec §B2 + 迁移映射表（8 tab 去向），注明 URL 兼容映射

### Task 7: B3 —— 技能库卡片化 + 列表反查

**Files:**
- Modify: `backend/app/repositories/skill_repository.py`（新增 `map_binding_agents(skill_ids: list[int]) -> dict[int, list[dict]]`，一条 JOIN + GROUP 不循环）
- Modify: `backend/app/api/ai_library_router.py`（`GET /skills` :949-978 补 `agents`；删掉 schema :221-223 的「list 留空」注释）
- Create: `frontend/components/AILibrary/SkillGallery.tsx` + `SkillGallery.test.tsx`（卡片网格，替换 SkillsTab 左栏列表形态；SkillsTab 改为：无选中 slug → SkillGallery 全宽；有 slug → 现有编辑器布局）
- Test: `backend/tests/test_skills_list_agents.py`（新建）

**Interfaces:**
- Produces: `GET /skills` 每项带 `agents: [{slug, name}]`；前端卡片含名称/描述/来源徽章（`Nous 内置`=info 色 / `自建`）/scope 徽章/`usedby` 行（头像小方块 + "N agents using"；**空 → warn 色 "⚠ No agent uses this — bind or archive"**，i18n `aiLibrary.skills.orphanHint`）
- SkillList.tsx 的文件树职责移交 B4（本 task 只动列表形态，编辑器不动）

- [ ] **Step 1: 后端失败测试** — ① 两个 skill 各绑 1/0 个 agent → 列表响应 agents 字段正确、零绑定为 `[]`；② 断言 router 源码不含循环调用 `list_binding_agents`（防 N+1 守卫）
- [ ] **Step 2: 红→实现→绿** + style → **Step 3: 前端 SkillGallery + 测试**（① 卡片渲染 usedby；② 孤儿卡渲染 orphanHint 文本；③ 点击卡片调用 onOpen(slug)）→ **Step 4: 回归** → **Step 5: Commit** `feat(ai-library): B3 技能库卡片化 — 被使用关系可见 + 孤儿提醒`

### Task 8: B4 —— 技能编辑页文件页签化 + PR

**Files:**
- Modify: `frontend/components/AILibrary/SkillEditor.tsx`（顶部加 filetabs 条：SKILL.md + 各子文件 + `+ File`；activeTab 已是文件路径 state，页签只是换 UI）
- Modify: `frontend/components/AILibrary/SkillsTab.tsx`（编辑态左栏文件树移除——文件导航职责已由页签承担；`+ File` 复用 NewSkillFileModal）

- [ ] **Step 1: 实现页签条**（monospace 页签样式对齐视觉稿 filetabs；Fork 横幅原样保留；核对 :26-27 过期注释——「Used by」右栏改读详情端点已返回的 `agents` 字段渲染真实数据）
- [ ] **Step 2: 回归** `npx vitest run components/AILibrary && npx tsc --noEmit` → **Step 3: Commit + PR-B3**（Task 7+8，描述引用 spec §B3/B4）

### Task 9: B5 —— 新建/Fork modal 模板优先

**Files:**
- Modify: `frontend/components/AILibrary/NewAgentModal.tsx`
- Test: `frontend/components/AILibrary/newAgentForm.test.ts`（表单纯函数：模板选择→默认值推导、scope 互斥校验）

**Interfaces:**
- Produces: 第一步模板卡片网格（系统 preset 各一张 + `空白创建` 卡；预选 `initialForkFrom`）→ 名称 + 权限域（个人/团队/项目，**前端互斥校验**——AgentCreate 无后端 validator）→ 说明可选；`buildCreatePayload(form): AgentCreate`（纯函数，导出测试）；**fork 成功后若源 agent 有 skill_ids，追加一次 `PATCH /agents/{slug}` 带 skill_ids 复制绑定**（后端 fork 不复制的补偿），PATCH 失败 → toast warn "Agent created; skills not copied — bind manually"（类型化回显，不静默）
- Fork 继承 agent_group（Task 1 后端已做，modal 不需处理）

- [ ] **Step 1: 纯函数失败测试** — ① 选模板→payload.fork_from 正确、名称必填校验；② team+project 同时选 → 校验错误；③ 空白创建 → fork_from 为空
- [ ] **Step 2: 红→实现→绿**（modal UI 对齐视觉稿 §07：mtmpl 卡片选择器 + mgrid 两列）→ **Step 3: Commit** `feat(ai-library): B5 新建/Fork modal 模板优先 + 技能绑定复制补偿`

### Task 10: B6 —— 例行任务 modal + 工作台入口 + PR

**Files:**
- Create: `frontend/components/AILibrary/NewRoutineModal.tsx` + `routineForm.test.ts`
- Modify: `frontend/components/AILibrary/AgentRoutinesTab.tsx`（CRON_PRESETS 改档 + RoutineForm 复用导出）
- Modify: `frontend/components/AILibrary/AgentWorkbenchTab.tsx`（例行任务卡 `+ New task` 入口）

**Interfaces:**
- Produces: 频率档（**删除 `Every 15 min`**——spec YAGNI 条目）：

```ts
export const FREQUENCY_PRESETS = [
  { key: 'hourly',   label: 'Hourly',    cron: (hh: string, mm: string) => `${mm} * * * *` },
  { key: 'daily',    label: 'Daily',     cron: (hh, mm) => `${mm} ${hh} * * *` },
  { key: 'weekdays', label: 'Weekdays',  cron: (hh, mm) => `${mm} ${hh} * * 1-5` },
  { key: 'weekly',   label: 'Weekly',    cron: (hh, mm) => `${mm} ${hh} * * 1` },
  { key: 'custom',   label: 'Custom',    cron: null },  // 露出原始 cron 输入
] as const;
export function buildRoutinePayload(form): ScheduleCreatePayload;  // 纯函数导出
```

- Modal 结构对齐视觉稿 §07·B：自然语言任务描述（`prompt_md`）+ 频率档 + 时间选择 + 结果去向（固定文案说明「结果回落为 issue 回复」，复用现有 routine→issue 链）+ 审批策略（现有 `delivery_policy` 两值）；创建走 `schedulesService.create`（`task_type='agent_routine'`, payload 含 `agent_slug`/`prompt_md`）

- [ ] **Step 1: 纯函数失败测试** — ① daily 21:00 → `0 21 * * *`；② weekdays 9:30 → `30 9 * * 1-5`；③ hourly → 分钟位保留 `mm * * * *`；④ payload 含 agent_slug/prompt_md（后端 400 契约的两个必填）
- [ ] **Step 2: 红→实现→绿** → **Step 3: 全量回归** `npx vitest run components/AILibrary && npx tsc --noEmit` → **Step 4: Commit + PR-B4**（Task 9+10，描述引用 spec §B5/B6 + YAGNI 条目）

## Self-Review 记录

- Spec §B0→T3、§B1→T1/T2/T4、§B2→T5/T6、§B3→T7、§B4→T8、§B5→T9、§B6→T10；数据与接口改动清单 7 行全部有归属（group→T1、周统计/健康聚合→T2、skills 反查→T7、issues 投影/task_tracking 透传→协作面计划、Fork 端点→现状已有（POST /agents + fork_from，T9 只做前端）、routine 参数→T10）。
- B2 工作台「等你回复」逐条列表降级为计数卡（NeedsInputItem 无 agent 维度，扩端点违反最小 diff；逐条入口由协作面 A1 承担）——偏离视觉稿一处，已在 T5 Step 3 说明。
- 「市场」tab 仅留位（disabled tab + `规划中` 角标），零实现——spec YAGNI。
- 类型一致：`AgentDerivedStatus`/`primaryAction` 在 T4 定义 T5 不复用（workbench 状态用 header 现有 chip）；`FREQUENCY_PRESETS` 只在 T10。
