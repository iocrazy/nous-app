# Issues 模块整体升级方案 — 视觉重设计 × 项目挂钩 × Agent 协作层

> 2026-07-15 · Fable 5 起草 · 依据：用户拍板的 mockup（Pipeline 签名）+ multica 两轮分析（opus）+ 项目↔待办脱钩诊断
> Mockup: https://claude.ai/code/artifact/1ff40c97-9616-48c4-a051-5511dc19d1c1
> 记忆锚点: `project_issues_multica_redesign.md`

## 0. 目标与非目标

**目标**
1. Issues/Todolist 页面视觉升级到 mockup 定稿（ink/island 体系内，零新增色）
2. 项目工作区与待办打通：项目里能看/能建/能筛自己的 issue，内容能转 issue
3. 把「agent 在干活」做成一等公民体验：活性呈现、执行日志、触发确认门
4. 顺手修掉 `.btn-tint-*` light 主题对比度 bug（全站受益）

**非目标（决策勿翻）**
- ❌ 不引入 shadcn/oklch/`--surface` 命名（映射到自有 ink token，禁两套并存）
- ❌ 不做 Gantt/Swimlane 视图
- ❌ 不做 leader agent 自主委派（squad 降级为固定流水线，Phase 2）
- ❌ 不做画布式独立日志面板（Task Center 已覆盖）
- ❌ 执行引擎不动：始终 DBOS + task_tracking（路线 C 立约）

## 1. 现状问题清单

| # | 问题 | 层 | 证据 |
|---|------|-----|------|
| P-1 | light 主题 New Issue/筛选按钮几乎不可见 | 全站 token | `index.css` `.btn-tint-indigo` 文字色写死暗调 `#a5b4fc`，不随 `[data-theme="light"]` 翻转 |
| P-2 | 状态图标无进度语义 | 视觉 | `IssueStatusIcon.tsx` 用 lucide 通用圆圈；in_progress/in_review 同为 CircleDot |
| P-3 | 优先级用文字符号 ⚠/↑/— | 视觉 | `IssueStatusIcon.tsx::PriorityIcon` |
| P-4 | 状态视觉常量散落三处 | 架构 | STATUS_LABEL/COLOR/ORDER 在 IssueStatusIcon.tsx，IssuesPage.tsx 又一套 STATUS_COLOR |
| P-5 | 空态是死区、无键盘 affordance | UX | 截图确认；无 C// 快捷键 |
| P-6 | **项目↔待办脱钩** | 接线 | 数据层全通（mig166 FK+索引+payload+筛选参数）；断在 NewIssueDialog 无项目选择器、workspaceModules 无 tasks 模块、origin_kind 无内容类值、project chip 显示 "Project {id}" |
| P-7 | agent 工作过程不可感知 | UX | 详情页只有 DBOS status/steps 裸列表；列表/看板无运行中指示 |
| P-8 | agent 触发不可预期、无确认门 | UX/安全 | dispatch 直接跑；分发场景（发抖音）无人工确认范式 |
| P-9 | 评论(IssueChatThread)与活动(IssueActivityTab)两个 tab 割裂 | UX | 人机协作不在一条时间线 |

## 2. 实施批次（8 个 PR + Phase 2）

依赖图：`PR-1 → PR-2 → PR-3`（视觉线）；`PR-4` 独立可先行；`PR-5 → PR-6 → PR-7`（agent 线，7 需后端）；`PR-8` 收尾。

### PR-1 · btn-tint light 主题修复（S，独立，最先发）
- `frontend/index.css`：为 `.btn-tint-{indigo,amber,violet,green,red,cover}` 增加 `[data-theme="light"]` 覆写（深文字色 + 实底 soft 背景，参照 `--accent-text/soft/border` 三 token 模式）
- **验收**：light 主题下 Issues/资源库/详情页所有 tint 按钮对比度 ≥ 4.5:1；dark 主题逐像素不变
- **风险**：全站按钮受影响 → 发版前 Playwright 双主题截屏抽查 5 个页面

### PR-2 · 状态/优先级图标 + 配置收敛（M）
- 新增 `frontend/components/Todolist/issueConfig.ts`：单一数据源 `STATUS_CONFIG: Record<IssueStatus, {label, iconColor, hoverBg, columnBg, tick}>` + `PRIORITY_CONFIG: {label, bars, color}`（multica `config/status.ts` 范式）
- `IssueStatusIcon.tsx` 重写为进度饼图 SVG：backlog=虚线环 / todo=空环 / in_progress=半饼 / in_review=¾饼 / needs_followup=?环 / blocked=禁止斜杠 / done=满圆白勾 / cancelled=✕（mockup 已实现，直接移植 `stIcon()`）
- `PriorityIcon` 换三柱信号 SVG，urgent=红方块+!（mockup `priIcon()`）
- 迁移调用点：IssueListView / IssueBoardView / IssuesPage / IssueDetailView / IssueFilterPopover
- **验收**：所有状态/优先级在 list/board/detail/filter 四处渲染一致；`IssuesPage.tsx` 本地 STATUS_COLOR 删除

### PR-3 · 列表/看板视觉重构 + Pipeline（L，依赖 PR-2）
按 mockup 落地 `IssueListView.tsx` / `IssueBoardView.tsx`：
- 工具栏：New Issue 按钮带 `C` kbd 提示；搜索框 `/` kbd；页头常驻快捷键提示
- **Pipeline 胶囊链**（签名元素）：Quick 行右侧，状态胶囊按工作流顺序连线（Backlog→Todo→In Progress→In Review→Done），Blocked 玫红旁挂；点击=筛选（复用现有 `filters.statuses`）；计数恒全局；空态胶囊 opacity .45；<1100px 藏 label
- 分组头：sticky + 状态色 tick + 细分隔线 + hover 浮现 `+`（预填状态建卡）
- 行：7px 紧凑行高、ID 定宽 mono+tabular-nums、hover 提亮标题、`dbos_workflow_id && !done` 显示琥珀 pulse "running" 徽标
- 看板列：`--island-2` 底 + `columnBg` 状态色顶部 2px hairline（读 STATUS_CONFIG）+ 空列虚线拖放区
- 空态：`No issues — press C to create one` 指令式 + kbd
- 快捷键：`C`=新建、`/`=聚焦搜索（IME composing / 可编辑焦点 / defaultPrevented 三重守卫，multica `global-shortcuts.tsx` 范式）
- **验收**：真机双主题走查 list/board/空态/弹窗四态 vs mockup；`npm run lint` 过

### PR-4 · 项目↔待办接线 W1+W2（M，独立，可与视觉线并行）
- `workspaceModules.ts`：TOP_MODULES 加 `{ key:'tasks', labelKey:'projects.workspace.modules.tasks', icon:ListTodo }`
- `ProjectWorkspace.tsx`：tasks 模块渲染 `IssueListView`（`listIssues({project_id})` 预筛；code-split 懒加载与其他模块一致）；工作区内 New Issue 自动带 `project_id`
- `NewIssueDialog.tsx`：加项目选择器（工作区内=锁定当前项目只读展示；团队待办页=可选下拉，数据源 projectService）
- 列表/看板 project chip：显示真实项目名（issues 加载后批量 resolve 项目名，或后端 list 端点 join 返回 project_name——优先前者零后端改动），点击跳 `/team/:id/projects/:pid`
- i18n：`en.json`/`zh.json` 加 `projects.workspace.modules.tasks` 等 key
- **验收**：项目里建 issue → 团队待办页可见且 project chip 正确；工作区 tasks 列表只显示本项目；project 筛选下拉不再恒空

### PR-5 · agent 活性呈现（M，纯前端，数据=task_tracking）
multica 三层照搬（`issue-agent-header-chip` / `execution-log-section` / `issue-agent-activity-indicator`）：
- 详情页头部 chip："{agent} is working"（running 时 accent 描边光效）/ "queued"（安静无光效）；数据 `task_tracking WHERE issue_id AND phase IN (queued,in_progress)`，与下面执行日志共享同一 fetch/Realtime 订阅（消除竞态）
- 详情页执行日志区（替换现 DBOS 裸列表）：active 置顶常驻 + past 折叠 "Show past runs (N)"；running 行实时跳秒 elapsed；行 hover 原地换出 取消/重试 按钮；失败行带 task id 精确重试
- 列表行/看板卡角徽标：running → 迷你头像 + shimmer "Working"；仅 queued → 半透明 "Queued"；无 → null 不占位
- **验收**：dispatch 一条 issue 后三处指示 <2s 内出现（Realtime）；完成后归入 past
- **纪律**：只读 task_tracking，不碰 dbos.workflow_status（路线 C）

### PR-6 · 统一时间线（M/L）
- `IssueDetailView` 把 IssueActivityTab + IssueChatThread 合并成一条 `TimelineItem` discriminated union：`comment | activity-group | task-result`（multica `issue-detail.tsx` 范式）
- 人/agent 评论同一 CommentCard，身份靠头像区分；连续系统事件（状态/指派变更）coalesce 成一行活动组；task_tracking 的 completed/failed 作为 "Agent completed/failed" 条目进时间线
- blocked 闭环：agent 设 blocked+评论说明（后端已可）；**人类回评论 = 重新触发 agent**（接现有 dispatch 链）
- **验收**：一条 issue 的完整故事（建→派→跑→评→改状态→完成）单条时间线可读

### PR-7 · run-confirm 双确认门（L，需后端）
- 后端：`POST /api/v1/issues/{id}/trigger-preview`（body: 拟发生的动作/评论草稿）→ 返回 `{agents:[...], blocked:[...], totalCount}`；**predicate 只在后端**，前端零猜测
- 前端模态门：指派 agent / dispatch 前弹确认：「{agent} will start working」+ handoff note 文本框 + 主按钮 Start / 次按钮 Assign only（`suppress_run`）；backlog 指派短路不弹
- 前端内联门：IssueReplyBox 输入时实时 preview chip「这条会唤醒 {agent}」，可点击 suppress；发送带 `suppressAgentIds`
- **分发对齐**：同一模态复用到 Distribution 发布链（"将发布到 抖音" preview + 人工确认）——D2 依赖此件
- **验收**：无确认不触发；suppress 后只改指派不跑；preview 与实际触发 100% 一致（同一后端 predicate）

### PR-8 · 内容↔待办 W3（M，需后端小改）
- `issues.origin_kind` 枚举加 `'content'`（或细分 script/canvas/distribution）；`origin_id` 存内容 id
- 内容侧入口：剧本场景右键 / 画布卡片菜单 / 分发记录行 → "Create issue"（预填标题+origin）
- `IssueRelatedTab`：origin 回链区块（该 tab 注释已预留位）+ 反查「此内容关联的 issues」
- **验收**：画布卡片转 issue → issue Related 可跳回卡片

### Phase 2 · Squad 固定流水线（另立计划）
squad = 有序 agent 序列（脚本→配图→分发），DBOS workflow 串联，时间线记录每棒交接；UI 借 multica 三态 assignee/方形 squad 头像/编排过程进时间线。**不做 leader 自主委派**。启动前单独出计划评审。

## 3. 发布与验证策略

- 全部走 `/ship`（自动 merge base + tests + review + VERSION/CHANGELOG）；每 PR 合并即发（trunk-based，寿命 ≤1 天）
- 视觉 PR（1/2/3）**不加 flag**——用户规矩「合了再看，fix forward」；接线/agent PR（4/5/6）行为可回退、面小，也不加 flag；PR-7 涉及触发语义变化，加 `FEATURE_ISSUE_RUN_CONFIRM` 灰度
- 每 PR done 前：Playwright 双主题截屏真看（feedback_ui_early_visual_ux_pass）+ `cd frontend && npm run lint`
- 真机走查节点：PR-3 后（视觉线收口）、PR-6 后（agent 线收口）各一次
- CI 红先查是否 AgentMemoriesPanel teardown flake / runner 不接单（0 steps），rerun 即过

## 4. 工作量与顺序汇总

| 批 | PR | 量级 | 依赖 | 交付 |
|----|----|------|------|------|
| 第一波 | PR-1 btn-tint 修复 | S | — | 全站 light 可读 |
| 第一波 | PR-4 项目接线 | M | — | 项目内待办闭环 |
| 第二波 | PR-2 图标+配置收敛 | M | PR-1 | 饼图/三柱/单一 config |
| 第二波 | PR-3 视觉重构+Pipeline | L | PR-2 | mockup 落地 |
| 第三波 | PR-5 agent 活性 | M | — | running 可感知 |
| 第三波 | PR-6 统一时间线 | M/L | PR-5 | 人机一条线程 |
| 第四波 | PR-7 run-confirm | L | PR-5，后端 | 触发确认门（D2 复用） |
| 第四波 | PR-8 内容↔待办 | M | PR-4，后端小改 | origin 双向回链 |

第一波两个 PR 互相独立，可同日并行发。
