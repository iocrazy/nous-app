# Project Workflow M2 (剩余项) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox syntax. Spec = `docs/superpowers/specs/2026-07-20-project-workflow-nodes-v2.md`（一切以 spec 为准）。M1/M1.5/M2-W1~W3 已交付内容见 2026-07-26 审计（本文"已交付边界"节），**勿重做**。

**Goal:** 补完 v2 spec 的 M2 剩余四项——Flow Rules/Events 可配置化、workflow 通知事件、Stage Board 完整工作面、current_stage_id 清理。

**Architecture:** 配置存储加在模板节点 + 实例节点两层（实例化时拷贝，改模板不影响存量实例，与 M1 拷贝语义一致）；通知复用既有 `inbox_notifications` 基础设施（mig 373），workflow 侧 best-effort 挂钩；Stage Board 作为工作区新 module（`?module=stage&node=…`），最小面 = 节点头 + 镜像 issue 子任务 + 阶段文件夹交付物；current_stage_id 整链退役（列 + set_current_stage 事务 + backfill workflow + 前端 SOP 兜底）。

**Tech Stack:** FastAPI + SQLAlchemy(ORM-only) + Supabase PG / React 19 + Vite + Tailwind。

## Global Constraints

- 迁移号从 **386** 起（先核 `ls supabase/migrations | tail`，撞号顺延；merge 即 CI auto-apply prod）
- 新列必同 PR 改 SQLAlchemy model（schema-drift gate 全 0 ratchet）
- snowflake id API 层全程 string；repo 层日期字段断言 date 对象
- hook / 通知全部 best-effort：`except Exception: logger.warning(...)`，绝不阻塞主操作；`notify()` 本身 NEVER raises（`services/notifications.py:65`）
- agent owner 永不静默 dispatch（Suggest agent run 只渲染入口，走既有 run-confirm 门）
- 权限：manager+editor 写；`resolve_effective_role`（`core/workflow_roles.py:97`）是唯一真源；404 先于 403
- 模板路由文件是 `backend/app/api/workflow_templates_router.py`（**不是** workflows_router.py——那是 DBOS run 路由，`GET /workflows/runs`，#1522 撞名事故勿重演；新端点先跑 `tests/test_route_uniqueness.py`）
- router 注册位在 `backend/app/api/__init__.py`（不是 main.py）
- preview 与执行共用服务端 predicate（#1400 纪律）；前端只渲染裁决
- UI 英文文案；Projects 面用 `projects.*` i18n 命名空间；Issues 面沿硬编码英文
- 每 PR 用 /ship（自动 merge base+bump 版本，勿手动 bump）；CI 绿即合；push 前 `cd backend && uv run black --check app` + `cd frontend && npm run lint`；CI 不做 typecheck → `npx tsc --noEmit` 后只看自己文件
- 本 worktree 跑测试须清代理变量：`env -u ALL_PROXY -u HTTP_PROXY -u HTTPS_PROXY uv run pytest`

## 已交付边界（新 PR 勿触碰重做）

- 实例节点增删（M2-W3 #1520，`services/workflow/node_mutations.py`）、逾期标红（`nodeStatus.ts isNodeOverdue`）、project 卡徽标——**已完成**
- 交付物链（M2-W1 #1519）：`node_folders.py` 阶段文件夹、`deliverable_uploads.py`、`DeliverablesZone.tsx`、`_deliverable_present` folder_id 主路径——Stage Board 直接复用
- SOP 静默（M2-W2 #1517）：有 workflow 节点的 project 已跳过 legacy SOP 镜像；本计划 PR-G 做的是**整链退役**
- 编排器视觉已对齐 v9 mockup（#1525/#1527），PR-D 只动 Flow Rules/Events 两个 tab

## 本计划拍板（spec 一句话项的具体化，执行者勿再发散）

1. **Flow Rules 可配置项只有一个**：`completion_policy`（'owner'=owner 本人审阅（M1 现状，默认）/ 'any_editor'=任何 manager/editor 可完成）。review_required / deliverable_required 已是可配置开关，不重复建模。
2. **Events 可配置项三个布尔**，存 `events JSONB`：`notify_on_arrival`（默认 true）/ `notify_on_complete`（默认 false）/ `suggest_agent_run`（默认 false）。到达派生 issue、完成关 issue 两条内建事件**保持硬编码**（spec §4：M3 才做自定义 hook）。
3. **配置只在模板层编辑**，实例化时拷贝到实例节点；实例层不开放就地改（spec §5 就地微调清单没有它们）。
4. **通知收件人**：到达/回退 reopen → 节点 owner_user_id + user members（去重、排除操作者本人）；完成(`notify_on_complete`) → 同一收件人集。agent owner 无人收 → 跳过（不通知 manager，避免噪音）。
5. **Stage Board = 工作区 module**（非独立路由页）：侧栏 Stages 点节点从"滚到 Overview 卡"改为打开 `module=stage&node={id}`。M1 的 Overview 节点卡保留不动。
6. **current_stage_id 退役后，No-workflow 项目不再有任何 stage 概念**（spec §8："No workflow → Overview 不渲染 workflow 区"）；`project_stages` 表保留（它已是节点库运营字典）。

---

## PR-D Flow Rules / Events 可配置化（M2-W4）

### Task D1: Migration 386 + models + schemas

**Files:**
- Create: `supabase/migrations/386_workflow_flow_events.sql`
- Modify: `backend/app/models/project_library.py`（WorkflowTemplateNodes `:242` 与 ProjectStageNodes `:335` 各加两列）
- Modify: `backend/app/schemas/workflow.py`（TemplateNodeIn/Out 加字段 + 校验）

**DDL:**
```sql
-- 386_workflow_flow_events.sql
ALTER TABLE workflow_template_nodes
  ADD COLUMN completion_policy TEXT NOT NULL DEFAULT 'owner'
    CHECK (completion_policy IN ('owner','any_editor')),
  ADD COLUMN events JSONB NOT NULL DEFAULT
    '{"notify_on_arrival": true, "notify_on_complete": false, "suggest_agent_run": false}'::jsonb;
ALTER TABLE project_stage_nodes
  ADD COLUMN completion_policy TEXT NOT NULL DEFAULT 'owner'
    CHECK (completion_policy IN ('owner','any_editor')),
  ADD COLUMN events JSONB NOT NULL DEFAULT
    '{"notify_on_arrival": true, "notify_on_complete": false, "suggest_agent_run": false}'::jsonb;
NOTIFY pgrst, 'reload schema';
```

**Model 要点**：两个类各加
```python
completion_policy: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'owner'"))
events: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text(
    '\'{"notify_on_arrival": true, "notify_on_complete": false, "suggest_agent_run": false}\'::jsonb'))
```

**Schema 要点**：`TemplateNodeIn`/`TemplateNodeOut` 加 `completion_policy: Literal['owner','any_editor'] = 'owner'` 与 `events: WorkflowNodeEvents`（新 BaseModel，三个 bool 带默认值；未知 key 直接丢弃——`model_config = ConfigDict(extra='ignore')`）。

**Steps:**
- [ ] 写 DDL + models + schemas
- [ ] `INTEGRATION_DATABASE_URL=... env -u ALL_PROXY uv run pytest tests/db/test_schema_drift.py` 绿
- [ ] commit `feat(workflow): migration 386 — completion_policy + events on template/instance nodes`

### Task D2: repo 读写 + 实例化拷贝 + 守卫读 policy

**Files:**
- Modify: `backend/app/repositories/workflow_templates_repository.py`（`update_template` nodes 全量替换 `:208` 与 `get_template` `:123` 带上两新列）
- Modify: `backend/app/repositories/project_stage_nodes_repository.py`（`instantiate_from_template` `:150` 拷贝两列；`list_nodes` `:358` 返回它们）
- Modify: `backend/app/api/issues_router.py::_assert_stage_owner_or_manager`（`:233-260`）：取节点后先看 `completion_policy=='any_editor'` → 有效角色 manager/editor 即放行；'owner' 走 M1 现状（owner 本人 / manager override）
- Modify: `backend/app/services/workflow/template_seeder.py`（种子节点带默认两列——直接吃 DB default 即可，断言即可）
- Test: `backend/tests/test_workflow_flow_rules.py`（新）——any_editor 节点非 owner 编辑者可过 in_review→done；owner 节点非 owner 403 不回归；实例化拷贝两列；模板 PATCH 全量替换保留配置
- Test: `backend/tests/test_owner_review_guard.py` 补 any_editor 用例

**Steps:**
- [ ] 先写失败测试（guard any_editor 放行 + 实例化拷贝断言）→ 跑红
- [ ] repo/guard 实现 → 跑绿
- [ ] `env -u ALL_PROXY uv run pytest tests/test_workflow_flow_rules.py tests/test_owner_review_guard.py tests/test_workflow_instantiation.py -q` 全绿
- [ ] commit `feat(workflow): completion policy honored by review guard, config copied on instantiation`

### Task D3: 编辑器 Flow Rules / Events tab 可编辑

**Files:**
- Modify: `frontend/types.ts`（WorkflowTemplateNode `:1038` / ProjectStageNode `:1105` 加 `completion_policy: 'owner' | 'any_editor'` 与 `events: { notify_on_arrival: boolean; notify_on_complete: boolean; suggest_agent_run: boolean }`）
- Modify: `frontend/components/workflow/WorkflowTemplateEditor.tsx`——`FlowRulesTab`（`:781-791`）从纯静态改为受控：completion policy 两选一（radio："Owner reviews & completes (default)" / "Any editor can complete"）；`EventsTab`（`:794-800`）三个 toggle（"Notify on arrival" / "Notify on completion" / "Suggest agent run on arrival"）。两 tab 改动走既有 `updateNode(nodeId, patch)` 本地 state → Save 全量提交（不加新保存路径）
- Modify: `frontend/services/workflowService.ts`（节点序列化带上两字段）
- Modify: `frontend/public/locales/en.json` + `zh.json`（`projects.workflow.flowRules.*` / `projects.workflow.events.*` keys）
- Test: `frontend/components/workflow/WorkflowTemplateEditor` 若无现成测试文件则在 e2e 走查里覆盖（见 D4）；vitest 补 `nodeStatus.test.ts` 不动

**Steps:**
- [ ] types + service + 两 tab 受控化 + i18n
- [ ] `npx tsc --noEmit`（只看自己文件）+ `npm run lint`
- [ ] commit `feat(workflow): flow rules & events tabs editable in template editor`

### Task D4: e2e 走查扩展

**Files:**
- Modify: `frontend/e2e/workflow-walkthrough.spec.ts`——模板编辑器屏加"切到 Flow Rules tab 改 policy → 切 Events tab 开 toggle → Save payload 断言"，双主题截图沿用既有 `forceTheme` 循环（`:178`）

**Steps:**
- [ ] `npx playwright test workflow-walkthrough` 全绿
- [ ] commit `test(workflow): e2e covers flow rules & events editing`

**/ship PR-D**：title `feat(workflow): flow rules & events configuration (M2-W4)`。

---

## PR-E workflow 通知事件（M2-W5）

### Task E1: Migration 387 扩 kind 枚举 + NotificationKind

**Files:**
- Create: `supabase/migrations/387_inbox_workflow_stage_kind.sql`
- Modify: `backend/app/services/notifications.py`（`NotificationKind` Literal 加 `'workflow_stage'`）

**DDL:**
```sql
-- 387_inbox_workflow_stage_kind.sql
ALTER TABLE public.inbox_notifications DROP CONSTRAINT inbox_notifications_kind_check;
ALTER TABLE public.inbox_notifications ADD CONSTRAINT inbox_notifications_kind_check
  CHECK (kind IN ('generation_result', 'publish_result', 'autopilot_output', 'workflow_stage'));
NOTIFY pgrst, 'reload schema';
```
（先 `\d inbox_notifications` 核实约束名，若为匿名约束则用 `DROP CONSTRAINT` 实名后重建；`link_kind` 不动——workflow 通知一律 `link_kind='issue'` 深链镜像 issue。）

**Steps:**
- [ ] DDL + Literal；schema-drift gate 跑绿
- [ ] commit `feat(notifications): workflow_stage notification kind`

### Task E2: 到达/完成/回退通知接线

**Files:**
- Create: `backend/app/services/workflow/stage_notifications.py` —
  ```python
  async def notify_stage_event(
      *, event: Literal["arrival", "completion", "reopen"],
      project_id: str, project_name: str, node: dict,
      issue_identifier: str | None, team_id: int | None,
      actor_user_id: str | None,
  ) -> None:
      # 读 node["events"]：arrival/reopen 看 notify_on_arrival，completion 看 notify_on_complete
      # 收件人 = {owner_user_id} ∪ user members - {actor_user_id}；空集直接 return
      # 逐人 notify(user_id, "workflow_stage", title, body=..., link_kind="issue",
      #            link_id=issue_identifier, team_id=team_id)
      # title 形如 f'Stage "{node["name"]}" started — {project_name}'（completion: "completed"; reopen: "reopened"）
      # 整函数外层 try/except Exception: logger.warning —— 绝不上抛
  ```
- Modify: `backend/app/services/workflow/advance_service.py::execute_advance`（`:318` 之后的成功路径）——forward：对新到达组每节点发 `arrival`，对刚完成组每节点发 `completion`；back（`:344-351` reopen 分支）：发 `reopen`。放在既有 best-effort hook 区域，同层 swallow
- Modify: `backend/app/services/library/projects_service.py`（创建项目实例化首组后 `:520-521` 附近，对首组每节点发 `arrival`）
- Test: `backend/tests/test_stage_notifications.py`（新）——monkeypatch `notify`：arrival 尊重 `notify_on_arrival=false` 不发；收件人去重且排除 actor；agent owner 无 user 收件人跳过；notify 抛错不影响 advance 返回值；completion 只在 `notify_on_complete=true` 发；back 走 reopen 文案

**Steps:**
- [ ] 先写失败测试 → 跑红
- [ ] 实现 `stage_notifications.py` + 三处接线 → 跑绿
- [ ] `env -u ALL_PROXY uv run pytest tests/test_stage_notifications.py tests/test_advance_predicate.py -q` 全绿（advance 既有 22 用例不回归）
- [ ] commit `feat(workflow): best-effort stage notifications on arrival/completion/reopen`

### Task E3: Suggest agent run 入口（前端）

**Files:**
- Modify: `frontend/components/workflow/CurrentNodeCard.tsx`——节点 `events.suggest_agent_run && owner_agent_id` 时渲染琥珀 chip "Suggested: run {agentName}"，点击 = 既有 "Open in Todolist" 跳转到镜像 issue（dispatch 走 Todolist 既有 run-confirm 门，**本组件不做任何 dispatch 调用**）
- Modify: `frontend/public/locales/en.json` + `zh.json`（`projects.workflow.suggestAgentRun`）
- Test: `frontend/components/workflow/CurrentNodeCard` 相关渲染断言加进 `WorkflowStrip.test.tsx` 同目录新文件 `CurrentNodeCard.test.tsx`（chip 仅在 flag+agent owner 时出现；点击调用 onOpenTodolist）

**Steps:**
- [ ] 测试先行 → 组件实现 → vitest 绿
- [ ] `npx tsc --noEmit` + `npm run lint`
- [ ] commit `feat(workflow): suggest-agent-run chip (never auto-dispatch)`

**/ship PR-E**：title `feat(workflow): stage notification events + suggest agent run (M2-W5)`。

---

## PR-F Stage Board 完整工作面（M2-W6）

### Task F1: 聚合端点

**Files:**
- Modify: `backend/app/api/projects_router.py` — 新端点
  ```
  GET /projects/{id}/workflow/nodes/{node_id}/board
  ```
  返回 `{"success": true, "data": {"node": {...list_nodes 单节点全字段...},
  "issue": {...镜像 issue + 子 issue 列表(id/identifier/title/status/assignee)...} | null,
  "files": [...folder_id 下非回收站文件（id/filename/size/created_at/source_issue_identifier）...]}`
  实现：`build_stage_origin_id`（`services/library/project_stage_issues.py:48`）拼 origin → issue repo 按 origin 查镜像 issue 及其子 issue；files 复用 `_deliverable_present` 同款 folder 查询（`advance_service.py:123-163` 的查询路径抽成 repo 函数 `list_folder_files(folder_id)` 供两处共用）。鉴权照 `GET /{id}/workflow`（`:424`）：`verify_project_read_access` + 404 先于 403
- Modify: `backend/app/repositories/project_stage_nodes_repository.py`（加 `get_node(node_id, project_id) -> dict | None` 若无；`list_folder_files` 抽取）
- Test: `backend/tests/test_workflow_stage_board.py`（新）——monkeypatch repos：无镜像 issue 返回 issue=null；旧格式 origin 项目（legacy）不 500；files 只含非 trashed；404/403 次序

**Steps:**
- [ ] 测试先行 → 端点实现 → `uv run pytest tests/test_workflow_stage_board.py tests/test_route_uniqueness.py -q` 绿
- [ ] commit `feat(workflow): stage board aggregate endpoint`

### Task F2: 工作区 module + 侧栏改跳转

**Files:**
- Modify: `frontend/components/workspace/workspaceModules.ts`——`WorkspaceModule` union 加 `'stage'`（**不**进三个 MODULE 数组——Stage Board 不出现在固定菜单，只从 Stages 区块进入）
- Modify: `frontend/components/workspace/ProjectWorkspace.tsx`——`activeModule` 状态机支持 `'stage'` + `stageNodeId` state；URL 参数 `?module=stage&node={id}`（沿 `:117-128` 既有 searchParams 同步模式）；渲染分支挂 `WorkspaceStageBoard`
- Modify: `frontend/components/workspace/WorkspaceSidebar.tsx`——Stages 区块（`:332-337`）点击从 `onJumpToNode`（滚 Overview）改为 `onOpenStage(nodeId)`（打开 stage module）；当前 `module=stage` 时对应节点高亮
- Create: `frontend/services/workflowService.ts` 加 `fetchStageBoard(projectId, nodeId)`
- Modify: `frontend/types.ts` 加 `StageBoardData` 接口（node/issue/files 三段，与 F1 返回一致）

**Steps:**
- [ ] types + service + module 接线
- [ ] `npx tsc --noEmit` + `npm run lint`
- [ ] commit `feat(workflow): stage module routing + sidebar entry`

### Task F3: WorkspaceStageBoard 组件

**Files:**
- Create: `frontend/components/workspace/WorkspaceStageBoard.tsx` — 三段布局：
  1. **节点头**：name + status 胶囊（复用 `nodeStatus.ts` 色板与 `isNodeOverdue` rose 标）+ owner（`OwnerPicker` 只读展示）+ schedule 区间 + suggest-agent-run chip（同 E3 逻辑）
  2. **Tasks**：镜像 issue + 子 issue 清单（identifier/title/status 胶囊，全部只读）+ "Open in Todolist" 按钮（既有跳转）；issue=null 时空态 "No mirror issue yet"
  3. **Deliverables**：复用 `DeliverablesZone.tsx`（`components/Todolist/DeliverablesZone.tsx`，M2-W1 已带 dropzone+Filed 列表）传镜像 issue id；无 folder 时只读文件列表
  底部动作条：当前活跃组节点显示 "Complete stage"（走既有 `fetchAdvancePreview` → `AdvanceConfirmDialog` 门，`ProjectWorkspace.tsx:176` 同链）；非活跃节点不渲染动作
- Modify: `frontend/public/locales/en.json` + `zh.json`（`projects.workflow.stageBoard.*`：tasks/deliverables/noIssue/openTodolist 等）
- Test: `frontend/components/workspace/WorkspaceStageBoard.test.tsx`——三段渲染、issue 空态、非活跃节点无动作条、overdue 标红

**Steps:**
- [ ] 测试先行（渲染断言）→ 组件实现 → vitest 绿
- [ ] commit `feat(workflow): stage board workspace panel`

### Task F4: e2e 走查 + 双主题截图

**Files:**
- Create: `frontend/e2e/stage-board.spec.ts` — 照 `workflow-walkthrough.spec.ts` 范式：stub `GET .../workflow` + `GET .../board`，走"侧栏点 Stages 节点 → stage module 打开 → 三段可见 → Complete stage 弹确认"链路，`forceTheme` 双主题 fullPage 截图

**Steps:**
- [ ] `npx playwright test stage-board` 全绿，截图人工过目
- [ ] commit `test(workflow): stage board e2e walkthrough`

**/ship PR-F**：title `feat(workflow): stage board full working surface (M2-W6)`。

---

## PR-G current_stage_id 整链退役（M2-W7，纯退役，≤24h 合入）

> 前置：PR-D/E/F 已合。本 PR 只删不加逻辑；触及面见 2026-07-26 审计清单。

### Task G1: 后端退役

**Files:**
- Modify: `backend/app/repositories/project_stages_repository.py` — 删 `set_current_stage` 五步事务（`:345-432`）及 `current_stage_id` 其余 7 处引用（`:32/:34/:50/:68/:110/:154`）；保留节点库字典读取（`list_stage_library` 依赖面不动）
- Delete: `backend/app/workflows/backfill_project_stage_issues.py`（整个 DBOS 回填 workflow 以 current_stage_id 为输入，整体退役；同步删 workflow 注册处——grep `backfill_project_stage_issues` 的 import）
- Modify: `backend/app/services/library/projects_service.py:457`（去掉 No-workflow 项目写 `current_stage_id`）
- Modify: `backend/app/api/projects_router.py` — 删 `GET/PUT /projects/{id}/current_stage` 端点（grep `current_stage` 定位）
- Modify: `backend/app/models/teams.py` — 删 `:344` 列定义 + `:298-301` FK 声明
- Delete/Modify tests: `tests/test_project_sop_stages.py:224-241` 相关用例、`tests/test_backfill_project_stage_issues.py`（整删）、`tests/test_create_project_default_stage.py:31`、`tests/test_create_project_workflow_no_sop_seed.py:66/90`

**Steps:**
- [ ] 删代码 + 改测试 → `env -u ALL_PROXY uv run pytest -q` 后端全量绿
- [ ] commit `refactor(projects): retire set_current_stage chain and backfill workflow`

### Task G2: Migration 388 drop 列

**Files:**
- Create: `supabase/migrations/388_drop_projects_current_stage_id.sql`

**DDL:**
```sql
-- 388_drop_projects_current_stage_id.sql
ALTER TABLE projects DROP CONSTRAINT IF EXISTS projects_current_stage_id_fkey;
DROP INDEX IF EXISTS idx_projects_current_stage;   -- 名字先核 295_project_sop_stages.sql:37
ALTER TABLE projects DROP COLUMN IF EXISTS current_stage_id;
NOTIFY pgrst, 'reload schema';
```

**Steps:**
- [ ] 核 295 里的真实索引/约束名后写 DDL；schema-drift gate 绿（model 已在 G1 删列）
- [ ] commit `feat(db): drop projects.current_stage_id (migration 388)`

### Task G3: 前端退役

**Files:**
- Modify: `frontend/services/projectsService.ts:472/486`（删 GET/PUT `/current_stage` 两函数）
- Modify: `frontend/types.ts:719`（删 `current_stage?: ProjectCardStage`）
- Modify: `frontend/components/ProjectCard.tsx:78`、`frontend/components/ProjectsQueueView.tsx:135/146` — StageRing 数据源只留 workflow 徽标（把 `badge?.current_node_name ?? project?.current_stage?.name` 兜底右半段删掉；No-workflow 项目不渲染 stage 环）
- Modify e2e stubs: `projects-workspace.spec.ts:155`、`projects-phase-b.spec.ts:150`、`script-editor-entry.spec.ts:63`、`script-editor-scene-toc.spec.ts:160`（删 `/current_stage` stub）

**Steps:**
- [ ] 删代码 → `npx tsc --noEmit` + `npm run lint` + vitest 相关文件绿
- [ ] `npx playwright test projects-workspace projects-phase-b script-editor-entry script-editor-scene-toc` 绿
- [ ] commit `refactor(frontend): drop SOP current_stage source, workflow badges only`

**/ship PR-G**：title `refactor(projects): retire current_stage_id end to end (M2-W7)`。

---

## Self-Review 摘要

- spec M2 六项：实例节点增删/逾期标红已交付（W3），其余四项 → PR-D（Flow Rules/Events）/ PR-E（通知）/ PR-F（Stage Board）/ PR-G（current_stage_id）各有归属。M3 项（任意 DAG、自定义 hook、表单化交付）明确不做。
- 类型一致：`completion_policy: 'owner'|'any_editor'` 与 `events` 三布尔在 D1(DDL)/D2(repo/guard)/D3(前端 types)/E2(通知读取)/E3+F3(chip) 全程同名同型；`StageBoardData` 与 F1 返回一致。
- 顺序依赖：E 读 D 的 events 列；F3 chip 读 D 的字段;G 独立但放最后（减少与 D-F 的 rebase 交叉）。
- 撞车面：无并行 session 声明；仍守"迁移号先核再用、模板路由文件名、route uniqueness 测试"三条既往事故线。
