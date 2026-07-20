# Project Workflow M1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox syntax. Spec = `docs/superpowers/specs/2026-07-20-project-workflow-nodes-v2.md`（一切以 spec 为准）。

**Goal:** project 级 workflow 节点管理——节点库、团队模板（Short/Long 两套种子）、实例化、每节点独立状态、双 hook 与 issue 闭环、owner 审阅制推进门、前端编排器+工作区节点条。

**Architecture:** 三层单向：节点库(project_stages 升级) → 模板(workflow_templates/nodes) → 实例(project_stage_nodes) → 镜像 issue。issue 是执行事实源，节点 status 是投影（transition_status 后置 hook 回流）。advance-preview 与 advance 共用 predicate。

**Tech Stack:** FastAPI + SQLAlchemy(ORM-only 新 repo) + Supabase PG / React 19 + Vite + Tailwind + react-day-picker。

## Global Constraints

- origin_kind='project_stage' 复用不动；origin_id 新格式 `project_stage:{project_id}:{node_id}`，读端兼容旧 `{stage_id}`；存量不迁移
- 新表必同 PR 加 SQLAlchemy model（schema-drift gate 全 0 ratchet）；迁移号从 **380** 起（先核 `ls supabase/migrations | tail`，撞号顺延）
- snowflake id：DB `server_default text("generate_snowflake_id()")`，API 层全程 string
- repo 层日期字段断言 date 对象（禁 isoformat 串喂 asyncpg）
- hook 全部 best-effort：`except Exception: logger.warning(...)`，绝不阻塞主操作
- agent owner 只指派不 dispatch（测试断言）；agents active = `status=='running'`
- 权限：manager+editor 写；owner 审阅制 in_review→done（manager override）；404 先于 403
- UI 英文文案；Issues 面沿硬编码英文不加 i18n；Projects 面用 `projects.*` i18n 命名空间
- 每 PR 用 /ship（自动 merge base+bump 版本，勿手动 bump）；CI 绿即合；push 前 `cd backend && uv run black --check app` + `cd frontend && npm run lint`；CI 不做 typecheck → `npx tsc --noEmit` 后只看自己文件

---

## PR-A 迁移 + models + 种子 + 模板 CRUD 后端

### Task A1: Migration 380 + SQLAlchemy models

**Files:**
- Create: `supabase/migrations/380_workflow_nodes_m1.sql`
- Modify: `backend/app/models/project_library.py`（ProjectStages 加列 + 新 model 类）
- Modify: `backend/app/models/reviews.py`（Issues 加 `due_date`）
- Modify: `backend/app/models/teams.py`（Projects 加 `current_node_id`）
- Test: `backend/tests/db/test_schema_drift.py`（gate 自动覆盖，无需改）

**DDL 要点**（完整列清单见 spec §9）:
```sql
ALTER TABLE project_stages ADD COLUMN phase TEXT, ADD COLUMN default_role_label TEXT,
  ADD COLUMN deliverable_label TEXT, ADD COLUMN review_required BOOL NOT NULL DEFAULT false;
CREATE TABLE workflow_templates (
  id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
  team_id BIGINT NOT NULL, name TEXT NOT NULL,
  is_default BOOL NOT NULL DEFAULT false,
  created_by UUID, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE UNIQUE INDEX uq_workflow_templates_team_default ON workflow_templates(team_id) WHERE is_default;
CREATE TABLE workflow_template_nodes (
  id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
  template_id BIGINT NOT NULL REFERENCES workflow_templates(id) ON DELETE CASCADE,
  name TEXT NOT NULL, sort_order INT NOT NULL, parallel_group INT,
  default_owner_user_id UUID, default_owner_agent_id UUID,
  skip_default BOOL NOT NULL DEFAULT false, review_required BOOL NOT NULL DEFAULT false,
  deliverable_required BOOL NOT NULL DEFAULT false, deliverable_label TEXT,
  source_stage_id BIGINT, duration_days INT,
  CONSTRAINT wtn_owner_xor CHECK (NOT (default_owner_user_id IS NOT NULL AND default_owner_agent_id IS NOT NULL)));
CREATE TABLE workflow_template_node_members (
  node_id BIGINT NOT NULL REFERENCES workflow_template_nodes(id) ON DELETE CASCADE,
  user_id UUID, agent_id UUID,
  CONSTRAINT wtnm_xor CHECK ((user_id IS NULL) <> (agent_id IS NULL)));
CREATE TABLE project_stage_nodes (
  id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
  project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  source_template_node_id BIGINT, legacy_stage_id BIGINT,
  name TEXT NOT NULL, sort_order INT NOT NULL, parallel_group INT,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','in_progress','in_review','done','skipped')),
  owner_user_id UUID, owner_agent_id UUID,
  planned_start DATE, planned_due DATE,
  review_required BOOL NOT NULL DEFAULT false, deliverable_required BOOL NOT NULL DEFAULT false,
  deliverable_label TEXT, skipped BOOL NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT psn_owner_xor CHECK (NOT (owner_user_id IS NOT NULL AND owner_agent_id IS NOT NULL)));
CREATE INDEX psn_project_idx ON project_stage_nodes(project_id, sort_order);
CREATE TABLE project_stage_node_members (
  node_id BIGINT NOT NULL REFERENCES project_stage_nodes(id) ON DELETE CASCADE,
  user_id UUID, agent_id UUID,
  CONSTRAINT psnm_xor CHECK ((user_id IS NULL) <> (agent_id IS NULL)));
ALTER TABLE issues ADD COLUMN due_date DATE;
ALTER TABLE projects ADD COLUMN current_node_id BIGINT;
NOTIFY pgrst, 'reload schema';
```
（member 表加 partial unique 防重复：`CREATE UNIQUE INDEX ... ON (node_id, user_id) WHERE user_id IS NOT NULL` 各两条）

**Model 要点**：照 `project_library.py:40-66` 的写法；agent id 用 UUID（对齐 ai_agents.id）；每列 server_default 与 DDL 一致。注意 v1 spec 说 agent BIGINT 是错的——`assignee_agent_id` 是 UUID（`reviews.py`），一律 UUID。

**Steps:** 写 DDL → 写 models → 本地起 CI 同款 PG 跑 `INTEGRATION_DATABASE_URL=... uv run pytest tests/db/test_schema_drift.py` 绿 → commit `feat(workflow): migration 380 + models for node bank/templates/instances`

### Task A2: 节点库种子 + 两套模板种子

**Files:**
- Create: `supabase/migrations/381_workflow_seed_stage_library.sql`（11 节点 UPSERT 进 project_stages：slug 固定 `script/storyboard/voiceover/canvas/shooting/editing/color-grading/vfx/post-delivery/distribution/retrospective`，带 phase/role/deliverable/review_required，幂等 ON CONFLICT (slug) DO UPDATE）
- Create: `backend/app/services/workflow/template_seeder.py` — `ensure_seed_templates(team_id)`：team 无任何模板时建 Short-form（is_default）+ Long-form（skip 矩阵按 spec §3/mockup §01；duration 全 NULL）。**运行时惰性种子**（首次 GET /workflows 时触发），不给存量 team 跑批。
- Test: `backend/tests/test_workflow_template_seeder.py` — fake repo：空 team 建两套；再调不双建；Short-form is_default。

### Task A3: 模板 CRUD（repo + schemas + router）

**Files:**
- Create: `backend/app/repositories/workflow_templates_repository.py` — ORM-only，签名：
  - `list_templates(team_id) -> list[dict]`（含 node count、is_default）
  - `get_template(template_id, team_id) -> dict | None`（nodes 按 sort_order + members）
  - `create_template(team_id, name, created_by) -> dict`
  - `update_template(template_id, team_id, *, name=None, is_default=None, nodes=None) -> dict | None`（nodes 全量替换：同事务 delete+insert；is_default=True 时先清同 team 旧 default）
  - `delete_template(template_id, team_id) -> bool`
  - `list_stage_library() -> list[dict]`
- Create: `backend/app/schemas/workflow.py` — `TemplateNodeIn/Out`（owner XOR validator 照 `issue.py:72` 范式）、`TemplateOut`、`TemplateUpdate`、`StageLibraryItem`。护栏：nodes ≤30（422）、team 模板 ≤20。
- Create: `backend/app/api/workflows_router.py` — `GET/POST /workflows`、`GET/PATCH/DELETE /workflows/{id}`、`GET /workflows/stage-library`。鉴权：`auth: AuthDep` + team 成员校验（照 issues_router 范式）；写操作要求有效角色 manager/editor（用 PR-B 的解析函数前，先用 team 成员放行 + TODO 标记，PR-B 收敛）→ **不**：直接在本 PR 实现 `resolve_project_role` 的 team 部分（见 A4），避免二次改。
- Modify: `backend/app/main.py`（注册 router）
- Test: `backend/tests/test_workflows_router.py`（monkeypatch repo，逐端点 + 403/404 + 护栏 422）、`backend/tests/repositories/test_workflow_templates_repository.py`（INTEGRATION 标记）

### Task A4: 有效角色解析（唯一真源函数）

**Files:**
- Create: `backend/app/core/workflow_roles.py` —
  ```python
  async def resolve_effective_role(user_id: str, *, project_id: str | None = None, team_id: str | None = None) -> str | None:
      # project_members 显式角色优先；无行则 team_members 兜底: owner/admin→manager, member→editor；都无→None
  ```
- Test: `backend/tests/test_workflow_roles.py` — 逐格：显式 manager/editor/viewer/external；兜底三档；无成员 None。

**/ship PR-A**：title `feat(workflow): node bank, team templates, seeds and CRUD (M1 PR-A)`。

---

## PR-B 实例化 + 双 hook + advance 链 + 守卫

### Task B1: 实例 repo + 实例化服务

**Files:**
- Create: `backend/app/repositories/project_stage_nodes_repository.py` —
  - `instantiate_from_template(project_id, template_id, *, method: str | None, overrides: list | None) -> list[dict]`（拷贝节点+members；method 快捷键翻 skip：live→AI-gen skip、ai→shooting skip、hybrid→全开；幂等：project 已有节点则 no-op）
  - `list_nodes(project_id) -> list[dict]`（含 members、按 sort_order）
  - `update_node(node_id, project_id, *, owner..., members..., planned_start: date|None, planned_due: date|None, skipped) -> dict | None`（**断言 date 对象**）
  - `set_node_status(node_id, status)`（仅供 hook 调）
  - `get_active_group(project_id) -> list[dict]`（current_node_id 所在 parallel_group 全组）
- Modify: `backend/app/services/library/projects_service.py` 创建链路：请求带 `workflow_template_id`（None=No workflow）时实例化 + 设 current_node_id=首组 + 触发到达 hook。
- Modify: `backend/app/schemas/projects.py` — ProjectCreate 加 `workflow_template_id: str | None`、`workflow_method: Literal['live','ai','hybrid'] | None`。
- Test: `backend/tests/test_workflow_instantiation.py` — 拷贝独立性（改模板不影响实例）、method 矩阵、幂等、No workflow 零节点。

### Task B2: origin 双格式 + ensure 继承 + 状态回流 hook

**Files:**
- Modify: `backend/app/services/library/project_stage_issues.py` —
  - `build_stage_origin_id` 改产 `project_stage:{project_id}:{node_id}`；新增 `parse_stage_origin_id(origin_id) -> tuple[project_id|None, node_or_stage_id]` 兼容旧两段式
  - `ensure_stage_issue` 改收 node dict：payload 加 `assignee_user_id`/`assignee_agent_id`（=节点 owner，XOR）、`due_date`（=planned_due，date 对象）；**不 dispatch**
- Modify: `backend/app/repositories/issue_repository.py` — `transition_status` 末尾追加 `_fire_stage_node_sync(issue, new_status)`（best-effort）：origin_kind=='project_stage' → parse origin_id 新格式取 node_id → `set_node_status` 映射（todo→pending, in_progress→in_progress, in_review→in_review, done→done, cancelled→pending）；旧格式（两段）跳过回流。
- Test: `backend/tests/test_stage_node_sync_hook.py` — 状态映射逐条、旧格式跳过、抛错不阻塞 transition、非 project_stage 不触发；`test_project_stage_auto_issue.py` 扩展：断言继承 owner/due_date（date 型）+ **断言未 dispatch**（monkeypatch dispatch 入口，assert not called）。

### Task B3: advance predicate + 端点 + owner 审阅守卫

**Files:**
- Create: `backend/app/services/workflow/advance_service.py` —
  ```python
  async def compute_advance_preview(project_id, user_id, direction: Literal['forward','back']) -> AdvancePreview
  async def execute_advance(project_id, user_id, direction) -> AdvanceResult  # 内部先 compute，同一 predicate
  ```
  预览三行裁决：当前组 issue 将标 done（列开放子任务警告）/ 下一组将建 issue 指派给谁（due date）/ "No agent will start automatically"。阻断项：`NOT_MANAGER_OR_EDITOR` / `REVIEW_PENDING`（review_required 组内 issue 未 done）/ `DELIVERABLE_MISSING`（deliverable_required 且阶段文件夹 0 files）/ `NO_NEXT`（尽头）。回退：目标组 reopen 镜像 issue（transition→in_progress），preview 明示。
- Modify: `backend/app/api/projects_router.py` — `GET /{id}/workflow`（nodes+status+agents_active：`agent_runs status=='running'` count）、`PATCH /{id}/workflow/nodes/{node_id}`、`GET /{id}/advance-preview`、`POST /{id}/advance`。鉴权 `verify_project_read/write_access` + `resolve_effective_role`。
- Modify: issues transition 链路（`issues_router.py` `POST /{id}/transition` 或 service 层）：目标 in_review→done 且 origin_kind=='project_stage' 且 issue.project_id 非空 → 守卫 `当前用户==节点 owner_user_id OR 有效角色==manager`，否则 403；散 issue 不受限。
- Test: `backend/tests/test_advance_predicate.py`（阻断逐项、preview/advance 同源断言——同函数调用、并行组全终态、回退 reopen）、`backend/tests/test_owner_review_guard.py`（owner 过/非 owner 403/manager override/agent-owner 节点 manager 审/散 issue 不受限）。

**/ship PR-B**：title `feat(workflow): instantiation, dual hooks, advance chain with owner review (M1 PR-B)`。

---

## PR-C 前端

### Task C1: services + types

**Files:**
- Create: `frontend/services/workflowService.ts` — `fetchTemplates/fetchTemplate/createTemplate/updateTemplate/deleteTemplate/fetchStageLibrary/fetchProjectWorkflow/updateProjectNode/fetchAdvancePreview/executeAdvance`（apiClient + Envelope unwrap 范式，`projectsService.ts:20-22` 同款；id 全 string）
- Modify: `frontend/types.ts` — `WorkflowTemplate/TemplateNode/ProjectStageNode/AdvancePreview` 接口

### Task C2: 左下角入口 + 编排器

**Files:**
- Modify: `frontend/components/project/ProjectFilterSidebar.tsx` — 底部（`mt-auto` + 分隔线）加 `Workflow Templates` 入口（`data-testid="workflow-templates-entry"`；viewer 隐藏）
- Create: `frontend/components/workflow/WorkflowTemplateEditor.tsx` — 左 chain（胶囊链+parallel group 竖排框+拖拽 sort）+ 右 inspector（Node Info 全字段；Flow Rules/Events tab M1 只读展示固定约定）；"+ Add from library" 弹节点库
- Create: `frontend/components/workflow/OwnerPicker.tsx` — 人+agent 二合一（成员列表 + ai_agents 列表混排，头像区分；单选 owner 模式 / 多选 members 模式）

### Task C3: 创建项目 + 工作区

**Files:**
- Modify: `frontend/components/CreateProjectModal.tsx` — Workflow 区块：两卡（Short-form 默认选中/Long-form）+ No workflow + method 三选（Live/AI/Hybrid）+ 提交带 `workflow_template_id`/`workflow_method`
- Create: `frontend/components/workflow/WorkflowStrip.tsx` — 节点胶囊链（done 翠绿勾/current accent+琥珀脉冲/skipped 虚线删除线/并行组竖排；视觉复用 `IssueListView.tsx:285-330` capsule 范式 + `issueConfig.ts` STATUS_CONFIG 色）
- Create: `frontend/components/workflow/CurrentNodeCard.tsx` — owner/members（OwnerPicker）/Schedule（**react-day-picker range**：双月、区间、"N days"、Cancel/Done，Tailwind 主题化）/deliverable 行/Open in Todolist/Complete stage
- Create: `frontend/components/workflow/AdvanceConfirmDialog.tsx` — 照 `DispatchConfirmDialog.tsx` 范式：只渲染服务端 preview，blocked 分支映射文案
- Modify: `frontend/components/workspace/WorkspaceOverview.tsx`（strip+node card 区块）、`workspaceModules.ts`+`WorkspaceSidebar.tsx`（动态 Stages 组，点节点滚到 Overview 节点卡）、`WorkspaceTopBar.tsx`+`MiniStepper.tsx`（onJump 接 advance-preview 确认门，canWrite 按角色）、agents active chip（头部，`agent_runs` running 数据走 `GET /projects/{id}/workflow`）
- Modify: `frontend/public/locales/en.json`+`zh.json` — `projects.workflow.*` keys
- Dep: `cd frontend && npm i react-day-picker`

### Task C4: e2e stub 走查 + 双主题截图

**Files:**
- Create: `frontend/e2e/workflow-walkthrough.spec.ts` — 照 `issue-trigger-walkthrough.spec.ts` 范式：`setupStubbedSession` + workflow 端点 stub（templates/workflow/advance-preview）+ `forceTheme` 双主题 fullPage 截图（编排器/Overview strip/确认弹层三屏 × dark/light）
- 跑 `npx playwright test workflow-walkthrough` + `npx tsc --noEmit`（grep 自己文件）+ `npm run lint`

**/ship PR-C**：title `feat(workflow): template editor, workspace strip, node card and stages sidebar (M1 PR-C)`。

---

## Self-Review 摘要

- spec §1-§10 每条均有归属 task（Ideation=M1.5 不在本计划；Stage Board=M2 不在）。
- 类型一致：node id string（前端）/BIGINT（DB）；agent id UUID 全程；日期 date 对象（B1/B2 断言）。
- 撞车面：issues 域三点（transition 守卫、origin 双格式、due_date 列）全在本专题，Issues 改造 session 不碰；rebase 勤 + 合并前重核 master 版本号。
