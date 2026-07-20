# Project Workflow 节点管理 × Todolist 进度联动 — 设计定稿

日期：2026-07-19 ｜ 状态：已过用户评审拍板 ｜ Mockup：https://claude.ai/code/artifact/784903cc-788d-4f92-be0d-a48476d21532

## 1. 背景与目标

用户诉求（源自飞书项目对照）：项目工作按流程走——节点可编排（负责人/工期），节点能挂人也能挂 AI，能看到几个 AI session 在项目上跑；Todolist 不只是同步项目，要跟踪其中状态。团队形态为 2-5 人真人小团队 + AI agents。

**明确不抄飞书**：审批流实体/审批节点、字段级权限、估分/人天工时（内容工厂的成本单位是 AI token，Cost & Budget 已有）、并行分支 DAG（挂起到 M3）。

## 2. 已拍板决策（勿翻）

| # | 决策 | 拍板 |
|---|------|------|
| D1 | 团队形态 2-5 人真人小团队 → 激活既有角色体系，不建新权限架构 | 2026-07-19 |
| D2 | 验收 = 角色约定式（in_review→done 仅 manager），零新字段；reviewer_id 指派后补 | 2026-07-19 |
| D3 | 配置在 Projects 模块、进度在 Todolist；**不进 Settings（太重）** | 2026-07-19 |
| D4 | 节点 issue **进入节点时才派生**（沿用镜像语义），不预生成 | 2026-07-19 |
| D5 | 配置入口 = 项目列表页左侧二级栏视图列表下方的 **Workflows 区块** | 2026-07-19 |
| D6 | **多套流程模板**（团队级，如 Short Video / Image Post），新建项目选模板实例化 | 2026-07-19 |

## 3. 职责分工

- **Projects 模块**：流程配置。侧栏 Workflows 区块管理模板；工作区 Overview 展示节点条 + 就地微调本项目实例。
- **Todolist**：进度管理。节点派生的镜像 issue + 子任务在此流转，进度回流项目侧。Todolist **零新 UI**。

## 4. 数据模型

### 新表

```
workflow_templates            -- 团队级流程模板
  id            BIGINT snowflake PK
  team_id       BIGINT NOT NULL          -- 团队铁边界
  name          TEXT NOT NULL
  is_default    BOOL DEFAULT false       -- 新建项目默认选中（每 team 至多一个，partial unique）
  created_by    UUID
  created_at / updated_at

workflow_template_nodes       -- 模板节点（存相对值，不存绝对日期）
  id            BIGINT snowflake PK
  template_id   BIGINT FK → workflow_templates ON DELETE CASCADE
  name          TEXT NOT NULL
  sort_order    INT NOT NULL
  default_owner_user_id   UUID NULL      -- 默认负责人（人）
  default_owner_agent_id  BIGINT NULL    -- 默认负责人（agent）
  duration_days INT NULL                 -- 工期天数
  skip_default  BOOL DEFAULT false

project_stage_nodes           -- 项目节点实例（模板的拷贝，此后独立）
  id            BIGINT snowflake PK
  project_id    BIGINT FK → projects ON DELETE CASCADE
  source_template_node_id BIGINT NULL    -- 溯源，不做同步
  legacy_stage_id         BIGINT NULL    -- 存量 backfill 时映射旧 project_stages 行
  name          TEXT NOT NULL
  sort_order    INT NOT NULL
  owner_user_id   UUID NULL
  owner_agent_id  BIGINT NULL
  planned_start / planned_due  DATE NULL
  skipped       BOOL DEFAULT false
```

### 既有表变更

- `issues` 加 `due_date DATE NULL`（镜像 issue 继承节点排期；普通 issue 也可用）。
- `projects` 加 `current_node_id BIGINT NULL`（新游标）；`current_stage_id` 过渡期保留只读，M2 收尾清理。
- 全局 `project_stages` SOP 字典：首发迁移把它**种子化为每个 team 一套 "Default" 模板**后退役为只读 legacy；存量项目 backfill 实例化 `project_stage_nodes`（`legacy_stage_id` 记映射）。

### 镜像 origin 兼容（⚠️ 四面镜纪律）

- `origin_kind='project_stage'` **复用不动**——不新增枚举值，不触发 DB CHECK / SQLAlchemy / Pydantic / TS 四面镜改动。
- `origin_id` 新格式 `project_stage:{project_id}:{node_id}`；读端 `parseOriginId` / 后端 lookup 兼容旧 `{stage_id}` 格式，**存量镜像 issue 不迁移**。

## 5. 运行时语义

1. **实例化**：新建项目选模板（默认 is_default 那套）→ 拷贝 nodes → 绝对排期 = 项目开工日起按 duration_days 顺序累加（skip_default 节点不占工期）。
2. **进入节点**：`set_current_node`（承接现 `set_current_stage` 的 repo 后置回调机制，三调用方全钩）→ 幂等 ensure 镜像 issue，**继承节点 owner 与 planned_due**。agent owner **只指派不 dispatch**——启动仍走 run-confirm 门（"绝不静默指派/计费"立约；来自用户显式配置的指派不算静默）。
3. **推进**：`GET /projects/{id}/advance-preview`（纯读）→ 前端只渲染服务端裁决（将关什么 / 建什么指派给谁 / 开放子任务警告 / "No agent will start automatically"）→ `POST advance`。skipped 节点直接越过（preview 不出现）。
4. **验收**：镜像 issue 走 in_review → done（守卫见 §6）；当前节点镜像 issue 及全部子 issue 终态 → 项目侧亮 "Ready to advance"（**不自动推进**——multica/paperclip 双仓验证的成熟决策）。
5. **微调单向性**：改模板不影响已创建项目；改项目实例不回写模板；已派生 issue 不回写（进入时快照继承）。三层各自独立，杜绝双向同步。
6. **失败路径**：镜像钩子 best-effort（swallow + warning，不阻塞主操作）——沿用现行纪律。

## 6. 权限矩阵

**有效角色解析**：`project_members` 显式角色优先；无行则 team 角色兜底映射 owner/admin→manager、member→editor。（不兜底则每个项目都要手动录成员，小团队没人维护。）

| 动作 | manager | editor | viewer / external |
|------|---------|--------|-------------------|
| 建/改/删流程模板 | ✅ | ✅ | ❌（入口不渲染） |
| 推进/回退阶段、就地微调节点 | ✅ | ✅ | ❌ |
| 归档项目、改项目设置 | ✅ | ❌ | ❌ |
| issue 提交 in_review | ✅ | ✅ | ❌ |
| issue in_review → done（验收） | ✅ | ❌ | ❌ |

- 验收守卫**只对挂 project 的 issue 生效**——团队散 issue / 个人待办不上锁。
- 后端 403（角色不足）；不存在/无成员资格保持 404 范式（不泄露存在性）。

## 7. UI 落点（对应 mockup 三屏）

1. **Projects 侧栏 Workflows 区块**（D5 位置）：模板列表 + "+ New workflow"；点开在主内容区展示模板编辑器（拖拽排序 / Default owner picker 人-agent 二合一 / Duration / Skip by default / 删除；"Save template"）。
2. **工作区 Overview 的 Workflow section**：节点条（复用 Issues Pipeline 胶囊链签名视觉：done 翠绿勾 / current accent 描边+琥珀脉冲 / skipped 虚线删除线 / 未来节点带日期）+ 当前节点卡（Owners 人-AI 混排 / 镜像 issue 回链 / 子任务进度 N/M / Advance 按钮）+ 点节点就地微调小卡。
3. **推进确认弹层**：服务端 preview 三行裁决（同 run-confirm #1400 纪律）。
4. **顶栏 MiniStepper 升级**：保留紧凑形态，点 dot 跳阶段从直接 `setCurrentStage` 改为先走 advance-preview 确认门（堵现存无确认跳阶段的口子）。
5. **新建项目对话框**：加模板选择（默认选 is_default）。

## 8. AI 活性呈现

- 项目工作区头部 + （M2）项目卡片："N agents active" 琥珀 chip。数据 = `agent_runs.project_id` 聚合（active = `ended_at IS NULL`），纯读，复用 W3c 归因。
- 节点卡：正在跑的 agent 头像琥珀脉冲（复用 issue agent-working chip 范式，isAgentWorking 信号）。

## 9. API 概览（均 `/api/v1`，team 铁边界，snowflake id 全程 string）

```
GET/POST           /workflows                      模板列表/新建（team scoped）
GET/PATCH/DELETE   /workflows/{id}                 模板详情/改名/删（含 nodes 全量替换式保存）
GET                /projects/{id}/workflow          节点实例 + 进度摘要 + agents active
PATCH              /projects/{id}/workflow/nodes/{node_id}   就地微调（owner/dates/skipped）
GET                /projects/{id}/advance-preview   纯读裁决
POST               /projects/{id}/advance           推进（服务端复算守卫，绝不信前端）
```

## 10. 分期

- **M1**：三张新表 + `issues.due_date` + 种子化/backfill 迁移；模板 CRUD + 侧栏区块 + 编辑器；实例化 + 镜像继承 + advance-preview/advance + 验收/角色守卫；Overview 节点条 + 节点卡 + 就地微调；MiniStepper 确认门；工作区头部 agents active chip。
- **M2**：排期呈现（逾期标红）+ 项目卡片徽章（阶段名 + agents active）+ 节点产物区（子 issue 内容回链聚合）+ `current_stage_id` 清理。
- **M3（挂起，待真实使用验证再拍板）**：并行分支、表单化交付/评审投票、reviewer_id 指派。

## 11. 测试要点

- 迁移：种子化幂等（重跑不双建）；存量项目 backfill 与 live 钩子不双创（沿用 ensure_stage_issue 幂等范式）。
- origin_id 新旧双格式 parse 回归（前后端各一套，1:1 镜像锁定）。
- 权限矩阵逐格测试（含 team 兜底映射、404 不泄露存在性）。
- advance-preview 与 advance 共用同一 predicate（杜绝"预览说 A 实际 B"漂移——#1400 纪律）。
- 实例化排期推算（skip 节点不占工期）；agent owner 派生 issue 断言**未 dispatch**。
- repo 层日期字段断言 date 对象类型（isoformat 串坑 [[bug_orm_isoformat_string_datetime_download_path]]）。

## 12. 明确不做

审批流实体/节点、字段级权限、估分人天、自动 rollup（issue 完成自动翻阶段）、模板↔实例双向同步、飞书式可配置表单、并行 DAG（M3 拍板前不做）。
