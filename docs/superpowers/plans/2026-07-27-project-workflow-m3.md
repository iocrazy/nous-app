# Project Workflow M3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox syntax. Spec = `docs/superpowers/specs/2026-07-27-project-workflow-m3-design.md`(一切以 spec 为准,含「明确不做」边界)。

**Goal:** M3 三波——W1 Stage Hook(confirm 门上膛 agent run)、W2 表单化交付(form builder + FORM_INCOMPLETE 门)、W3 依赖门(backward-only 边 + DEPS_PENDING 门)。

**Architecture:** 全部沿 M1/M2 骨架增量:配置存模板节点、实例化拷贝;推进门在 advance predicate 加并列阻断码(#1400 preview 同源);hook 走 DBOS workflow best-effort;前端零新保存路径。

**Tech Stack:** FastAPI + SQLAlchemy(ORM-only) + DBOS / React 19 + Vite + Tailwind。

## Global Constraints

- 迁移号规划:W1=389(metadata)、W2=390(form)、W3=391(deps);动手前先 `ls supabase/migrations | tail` 核现状,撞号顺延并在报告注明
- 新列/新表必同 PR 改 SQLAlchemy model(schema-drift gate);JSONB server_default 写法照 mig 386 落地范式(`backend/app/models/project_library.py` WorkflowTemplateNodes.events)
- **三个 PR 全部 base master,不做堆叠**(M2 实测堆叠 squash 的 retarget 坑,memory `project-stacked-pr-squash-merge-trap`);每波合并后下一波先 rebase origin/master
- hook/通知 best-effort:`except Exception: logger.warning`,绝不阻塞 advance/创建;**绝不静默 dispatch**(测试必须断言)
- preview 与 execute 共用 predicate(#1400);阻断码新增 `FORM_INCOMPLETE`、`DEPS_PENDING`,与既有四码并列
- 配置(events/form_schema/依赖的模板侧)模板层编辑+实例化拷贝;`form_data` 是运行时数据、`depends_on` 是实例结构——两者可走实例 PATCH
- snowflake id API 全程 string;404 先于 403;`resolve_effective_role` 唯一真源
- UI 英文;i18n `projects.workflow.*` en/zh 两份;测试数据英文
- push 前 `cd backend && uv run black --check app` + `cd frontend && npm run lint`;`npx tsc --noEmit` 只看自己文件;测试清代理:`env -u ALL_PROXY -u all_proxy -u HTTP_PROXY -u http_proxy -u HTTPS_PROXY -u https_proxy`
- 模板路由文件=`workflow_templates_router.py`;新端点先跑 `tests/test_route_uniqueness.py`
- 每 PR 商榷格式:`gh pr create --base master`,title 见各波;CI 绿即合(squash);commit 尾行 `Claude-Session: https://claude.ai/code/session_01KvXWh2z8qRAE3yUgUDq4sW`

---

## PR-H Stage Hook(M3-W1)

### Task H1: mig 389 + events schema 扩展

**Files:**
- Create: `supabase/migrations/389_stage_node_metadata.sql`
- Modify: `backend/app/models/project_library.py`(ProjectStageNodes 加 metadata 列)
- Modify: `backend/app/schemas/workflow.py`(WorkflowNodeEvents 加两 key)
- Test: `backend/tests/test_workflow_flow_rules.py` 扩展(schema 层断言)

**DDL:**
```sql
-- 389_stage_node_metadata.sql
ALTER TABLE project_stage_nodes
  ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;
NOTIFY pgrst, 'reload schema';
```

**Schema 要点**:`WorkflowNodeEvents` 加 `prepare_agent_run: bool = False` 与 `on_complete_workflow: str | None = None` + `@field_validator('on_complete_workflow')` 强制 None(非 None → ValueError "not implemented in M3",422)。extra='ignore' 不变。**events jsonb 的实例化拷贝是整值拷贝(M2 已做),新 key 免费继承——但 NodeOut/TemplateNodeIn 走 WorkflowNodeEvents 序列化,不加字段就会被丢**,所以 schema 是本 task 的核心。同步核 `_node_to_dict`(`workflow_templates_router.py`)是 `events.model_dump()`,加字段自动带上。

**Steps:**
- [ ] 失败测试:events 带 prepare_agent_run=true 过 TemplateNodeIn 校验并在 model_dump 出现;on_complete_workflow='x' → 422 → 跑红
- [ ] DDL + model + schema → 跑绿;schema-drift gate(throwaway PG 法,照 D1/G2 报告范式)
- [ ] commit `feat(workflow): migration 389 — node metadata + hook event keys`

### Task H2: stage_hook_dispatch workflow + 到达接线

**Files:**
- Create: `backend/app/workflows/stage_hook.py` —
  ```python
  @DBOS.workflow()
  async def stage_hook_dispatch(project_id: str, node_id: str) -> None:
      # 1) get_node → events.prepare_agent_run && owner_agent_id 双查(防入队后配置已变)
      # 2) metadata.run_prepared_at 已存在 → return(幂等)
      # 3) 镜像 issue 查询(list_by_origin,build_stage_origin_id 拼)——无 issue 则 return + warning
      # 4) notify(owner 侧无人收也发给 project members? 不——收件人沿 stage_notifications 同规则)
      #    kind="workflow_stage", title=f'Agent run ready — "{node_name}"', link_kind="issue", link_id=identifier
      # 5) set_node_metadata(node_id, {"run_prepared_at": iso_now})
  ```
- Modify: `backend/app/repositories/project_stage_nodes_repository.py`(加 `set_node_metadata(node_id, patch: dict) -> None`——JSONB 浅合并,不触 trigger 管辖列)
- Modify: 到达路径两处(与 stage_notifications 同挂点):`services/workflow/advance_service.py` forward 新到达组、`services/workflow/instantiation.py` 首组——对每节点 best-effort 入队(`DBOS.start_workflow` 或项目现行入队 API,照 `agent_workforce.py` 的用法)
- Test: `backend/tests/test_stage_hook.py`(新)——prepare 绝不 dispatch(monkeypatch `_dispatch_execute_issue` 断言 not called);无 agent owner 不入队;入队抛错不影响 advance 返回;重复到达因 run_prepared_at 幂等跳过;metadata 合并不覆盖既有 key

**Steps:**
- [ ] 失败测试先行 → 实现 → 目标套件 + `test_advance_predicate.py` + `test_stage_notifications.py` 回归绿 → 全量绿
- [ ] commit `feat(workflow): stage hook prepares agent run behind confirm gate`

### Task H3: 前端 Run now chip + Events tab 开关

**Files:**
- Modify: `frontend/types.ts`(WorkflowNodeEvents 接口加两字段;ProjectStageNode 加 `metadata?: { run_prepared_at?: string }`)
- Modify: `frontend/services/workflowService.ts`(DEFAULT_EVENTS 加 `prepare_agent_run: false, on_complete_workflow: null`;normalize 两侧)
- Modify: `frontend/components/workflow/WorkflowTemplateEditor.tsx` EventsTab 加第 4 个 toggle "Prepare agent run on arrival"(on_complete_workflow 不出 UI——spec 只留结构)
- Modify: `frontend/components/workflow/CurrentNodeCard.tsx` + `frontend/components/workspace/WorkspaceStageBoard.tsx`——`metadata?.run_prepared_at` 存在时 suggest chip 变实心 "Run now" 按钮 → 打开 `DispatchConfirmDialog`(`components/Todolist/DispatchConfirmDialog.tsx`,读它的 props 预填镜像 issue+agent;镜像 issue id 从 stage board payload 或 fetch 取——CurrentNodeCard 无 issue id 则点击先跳 Stage Board)
- Modify: locales `projects.workflow.runNow` 等 key en/zh
- Test: `CurrentNodeCard.test.tsx` 扩展(run_prepared 渲 Run now;未 prepared 仍是 suggest chip;点击回调断言)

**Steps:**
- [ ] 测试先行 → 实现 → `npx vitest run components/workflow components/workspace` + tsc + lint 绿
- [ ] commit `feat(workflow): run-now chip opens dispatch confirm (never auto)`

### Task H4: e2e + ship PR-H

**Files:**
- Modify: `frontend/e2e/stage-board.spec.ts`(stub metadata.run_prepared_at → Run now 可见 → 点击弹 confirm dialog,断言零 dispatch 请求;双主题截图沿用)

**Steps:**
- [ ] `npx playwright test stage-board workflow-walkthrough` 全绿
- [ ] commit `test(workflow): stage hook e2e` → PR title `feat(workflow): stage hook — prepared agent runs behind confirm (M3-W1)`

---

## PR-I 表单化交付(M3-W2)

### Task I1: mig 390 + models + schemas + 校验

**Files:**
- Create: `supabase/migrations/390_workflow_form_deliverables.sql`
- Modify: `backend/app/models/project_library.py`(两 model 加 form_schema;ProjectStageNodes 另加 form_data)
- Modify: `backend/app/schemas/workflow.py`(新 `FormFieldDef` + TemplateNodeIn/NodeOut/NodePatch 扩展)
- Test: `backend/tests/test_workflow_form_schema.py`(新)

**DDL:**
```sql
-- 390_workflow_form_deliverables.sql
ALTER TABLE workflow_template_nodes
  ADD COLUMN IF NOT EXISTS form_schema JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE project_stage_nodes
  ADD COLUMN IF NOT EXISTS form_schema JSONB NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS form_data JSONB NOT NULL DEFAULT '{}'::jsonb;
NOTIFY pgrst, 'reload schema';
```

**Schema 要点:**
```python
FORM_FIELD_TYPES = ("text", "textarea", "number", "select", "checkbox", "date")
MAX_FORM_FIELDS = 20

class FormFieldDef(BaseModel):
    model_config = ConfigDict(extra="ignore")
    key: str = ""          # 服务端生成,入参可空
    label: str             # 必填,strip 后非空
    type: Literal[...FORM_FIELD_TYPES]
    required: bool = False
    options: list[str] | None = None   # validator: 仅 select 可非 None 且非空列表
```
- `TemplateNodeIn.form_schema: list[FormFieldDef] = []`(validator:len>20 → 422;key 在 repo 写路径由 label slug 化生成、节点内去重加序号)
- `NodeOut.form_schema` + `NodeOut.form_data: dict = {}`;`NodePatch.form_data: dict | None = None`(**form_schema 不进 NodePatch**——实例不可改配置)

**Steps:**
- [ ] 失败测试(字段类型白名单、options 约束、>20 422、NodePatch 拒 form_schema)→ 红 → 实现 → 绿;drift gate
- [ ] commit `feat(workflow): migration 390 — form schema & data columns with validation`

### Task I2: repo 拷贝 + form_data PATCH + FORM_INCOMPLETE

**Files:**
- Modify: `backend/app/repositories/workflow_templates_repository.py`(全量替换携带 form_schema + key 生成:`_slugify_field_keys(fields) -> list[dict]`,label→kebab slug,重复加 `-2` 序号)
- Modify: `backend/app/repositories/project_stage_nodes_repository.py`(实例化拷贝 form_schema;`update_node` 接受 form_data——**合并前按实例 form_schema 白名单过滤 key**,未知 key 丢弃)
- Modify: `backend/app/api/projects_router.py` PATCH nodes 端点透传 form_data
- Modify: `backend/app/services/workflow/advance_service.py` — 新 `_form_incomplete(node) -> list[str]`(返回缺失字段 label):required 判定=text/textarea/select/date 空串或缺失、number 缺失(0 已填)、checkbox 非 true;目标组任一节点缺 → blocked `FORM_INCOMPLETE`,preview.missing_fields 带 label 列表
- Test: `backend/tests/test_form_incomplete_predicate.py`(新,六类型逐条+0/false 边界+无 schema 零影响)+ `test_workflow_instantiation.py` 扩拷贝断言 + `test_workflow_form_schema.py` 扩未知 key 丢弃

**Steps:**
- [ ] 失败测试 → 实现 → 目标套件 + advance 全量回归绿 → 后端全量绿
- [ ] commit `feat(workflow): form data lifecycle + FORM_INCOMPLETE advance gate`

### Task I3: 编排器 Form tab

**Files:**
- Modify: `frontend/types.ts`(`FormFieldDef` 接口 + 节点类型扩展)+ `workflowService.ts`(normalize/序列化两侧)
- Modify: `frontend/components/workflow/WorkflowTemplateEditor.tsx` — inspectorTab union 加 `'form'`,新 `FormTab` 子组件:字段行列表(label 输入、type 下拉、required Toggle、select 时 options 逗号输入、上移/下移/删除、"+ Add field",≥20 禁用加号)。走既有 patchNode → Save 全量
- locales `projects.workflow.formBuilder.*` en/zh
- Test: e2e 覆盖(I4);vitest 若 editor 家族无 harness 则 tsc+lint(沿 D3 先例,报告说明)

**Steps:**
- [ ] 实现 → tsc + lint + `npx vitest run components/workflow` 绿
- [ ] commit `feat(workflow): form builder tab in template editor`

### Task I4: Stage Board 填写 + e2e + ship PR-I

**Files:**
- Create: `frontend/components/workspace/StageNodeForm.tsx`(六类型控件渲染,受控,失焦调 `updateProjectNode(projectId, nodeId, {form_data})`;required 未填标记)
- Modify: `frontend/components/workspace/WorkspaceStageBoard.tsx`(Deliverables 区上方挂 StageNodeForm,schema 空不渲染)
- Modify: `frontend/components/workflow/CurrentNodeCard.tsx`(完成度 "Form 3/5" 行,点击跳 Stage Board)
- Modify: `frontend/components/workflow/AdvanceConfirmDialog.tsx`(FORM_INCOMPLETE 阻断文案映射 + missing_fields 列表)
- Test: `StageNodeForm.test.tsx`(新,六类型渲染+失焦保存回调+required 标记);e2e `stage-board.spec.ts` 扩(填表单→PATCH payload 断言→FORM_INCOMPLETE preview 弹层)

**Steps:**
- [ ] 测试先行 → 实现 → vitest + tsc + lint + `npx playwright test stage-board` 绿
- [ ] commit + PR title `feat(workflow): form deliverables — builder, fill-in, advance gate (M3-W2)`

---

## PR-J 依赖门(M3-W3)

### Task J1: mig 391 + 边表 + backward-only 校验

**Files:**
- Create: `supabase/migrations/391_workflow_node_deps.sql`
- Modify: `backend/app/models/project_library.py`(两个新 model:WorkflowTemplateNodeDeps / ProjectStageNodeDeps)
- Modify: `backend/app/schemas/workflow.py`(`TemplateNodeIn.depends_on: list[str] = []`(引用同模板节点的临时 index 或已存 id,照 members 的处理范式)+ `NodePatch.depends_on: list[str] | None`)
- Modify: `backend/app/repositories/workflow_templates_repository.py`(全量替换写边;校验 `_validate_deps_backward(nodes)`:dep 目标必须存在且 sort_order 更小,违者 raise → 422 `DEP_BACKWARD_ONLY`;自依赖同罪)
- Modify: `backend/app/repositories/project_stage_nodes_repository.py`(实例化拷贝边(模板 node id → 实例 node id 映射);`update_node` 接受 depends_on 全量替换,同校验;`list_nodes` 每节点带 `depends_on: [ids]`)
- Test: `backend/tests/test_workflow_deps.py`(新)——backward-only 逐条(自依赖/前向/合法)、拷贝映射正确、PATCH 替换、删除节点 CASCADE 后 list_nodes 不再返回悬空边

**DDL:**
```sql
-- 391_workflow_node_deps.sql
CREATE TABLE IF NOT EXISTS workflow_template_node_deps (
  node_id BIGINT NOT NULL REFERENCES workflow_template_nodes(id) ON DELETE CASCADE,
  depends_on_node_id BIGINT NOT NULL REFERENCES workflow_template_nodes(id) ON DELETE CASCADE,
  PRIMARY KEY (node_id, depends_on_node_id)
);
CREATE TABLE IF NOT EXISTS project_stage_node_deps (
  node_id BIGINT NOT NULL REFERENCES project_stage_nodes(id) ON DELETE CASCADE,
  depends_on_node_id BIGINT NOT NULL REFERENCES project_stage_nodes(id) ON DELETE CASCADE,
  PRIMARY KEY (node_id, depends_on_node_id)
);
NOTIFY pgrst, 'reload schema';
```
(RLS 照 mig 380 五表范式:ENABLE + service_role-only policy)

**Steps:**
- [ ] 失败测试 → DDL/models/repo → 绿;drift gate;`test_route_uniqueness.py`
- [ ] commit `feat(workflow): migration 391 — dependency edges with backward-only validation`

### Task J2: DEPS_PENDING predicate

**Files:**
- Modify: `backend/app/services/workflow/advance_service.py` — forward 目标组:每个非 skipped 节点的 deps 全部 `done`/`skipped` 才放行;否则 blocked `DEPS_PENDING`,preview 带 `waiting_on: [node names]`;back 不受限
- Test: `backend/tests/test_deps_predicate.py`(新)——skipped 依赖满足、部分未满足列出正确名单、preview/execute 同源断言、无依赖零回归;`test_advance_predicate.py` 全量回归

**Steps:**
- [ ] 失败测试 → 实现 → 目标 + advance 回归 + 后端全量绿
- [ ] commit `feat(workflow): DEPS_PENDING advance gate`

### Task J3: 前端(编排器多选 + 三处展示)+ e2e + ship PR-J

**Files:**
- Modify: `frontend/types.ts` + `workflowService.ts`(节点带 `depends_on: string[]`,normalize 默认 `[]`)
- Modify: `frontend/components/workflow/WorkflowTemplateEditor.tsx` Node Info tab 加 "Depends on" 多选(候选=sort 靠前节点,胶囊列表 + 勾选;走 patchNode)
- Modify: `frontend/components/workspace/WorkspaceStageBoard.tsx`(deps 未满足 rose 行 "Waiting on: X, Y"——判定用服务端 preview 数据或本地按 workflow.nodes status 推导,**取本地推导**:全量 nodes 在手,规则简单且展示性质)
- Modify: `frontend/components/workflow/WorkflowStrip.tsx`(当前组被 deps 阻塞 → 胶囊加 Lock 小图标;判定同上本地推导,抽 `nodeStatus.ts` 新 helper `unmetDeps(workflow, node): ProjectStageNode[]` 两处共用)
- Modify: `frontend/components/workflow/AdvanceConfirmDialog.tsx`(DEPS_PENDING 文案 + waiting_on 渲染——弹层内一律用服务端 preview 数据)
- locales `projects.workflow.deps.*` en/zh
- Test: `nodeStatus.test.ts` 扩 unmetDeps;e2e `stage-board.spec.ts` 或新 `workflow-deps.spec.ts`(编排器选依赖→Save payload 断言;preview 弹 DEPS_PENDING;双主题截图)

**Steps:**
- [ ] 测试先行 → 实现 → vitest + tsc + lint + playwright 绿
- [ ] commit + PR title `feat(workflow): dependency gates — backward-only edges, DEPS_PENDING (M3-W3)`

---

## Self-Review 摘要

- spec §1-§3 每条有归属:W1(H1 配置/H2 执行/H3 前端/H4 e2e)、W2(I1 模型/I2 生命周期+门/I3 构建器/I4 填写)、W3(J1 边/J2 门/J3 展示);§5 不做清单全部未入任务。
- 类型一致:`WorkflowNodeEvents.prepare_agent_run/on_complete_workflow`、`FormFieldDef`、`form_data`、`depends_on: string[]`、`metadata.run_prepared_at` 在前后端任务间同名同型;阻断码 `FORM_INCOMPLETE`/`DEPS_PENDING` 与 preview 字段 `missing_fields`/`waiting_on` 贯穿 predicate→dialog。
- 顺序:三 PR 严格串行(base master),波间 rebase;波内 task 依赖已按序排列。
