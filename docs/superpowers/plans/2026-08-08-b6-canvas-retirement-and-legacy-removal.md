# B6 — Canvas 阶段节点退役 + legacy 双路径删除 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 两个独立 PR：PR-1 退役流程条上重复的「Canvas (AI Generation)」独立阶段节点（画布功能/数据不动，它已是 Storyboard 节点的三视图之一），workflow_method(ai/live/hybrid) 内部语义改为 Shooting-only；PR-2 删除 B2 引入的 `episode_id is None` legacy 双路径（advance/autopilot/router/前端），并先行修复盘点发现的现网 bug：`get_active_group` 仍读永久陈旧的项目级游标，导致按集项目上「不能删活动组节点」守卫静默失效。

**Architecture:** PR-1 = 一条数据迁移（node bank 摘除 + 模板/实例行删除 + 防御性关镜像）+ repository 三处 method 逻辑收敛 + 测试钉子跟改，前端零改动（ai/live/hybrid 三选项保留，hybrid 仅作标签、节点链与 live 等价）。PR-2 按依赖序删除：先修 get_active_group（独立 bug），再删 advance_service 双路 → 孤儿化的项目级游标写入口 → router 参数必填 → autopilot legacy fixpoint → 前端 gate + 类型收紧 → 测试三层分类处置。

**用户拍板（2026-08-08）**：Canvas 完整退役（含 method 语义改 Shooting-only）✅；legacy 双路径本次一并删 ✅。

**前置事实（已验证，实施者不需重查）**：生产 `project_stage_nodes` 无 `episode_id IS NULL` 行（0）；4 个 Canvas 实例节点全 pending、无镜像 issue、非任何游标目标；孤儿 issue 329311134486169 已手工 cancelled；`project_stage_node_deps` 无 Canvas 边（seeder 不产生 dep，模板边只来自用户显式声明且生产模板无）。

## Global Constraints

- 全程 ORM 禁 text()（.sql 迁移文件除外）。
- commit/PR 信息中文；每 Task 独立 commit。
- migration 号：**执行时 `git fetch origin master` 后取最大号+1**（写本计划时最新为 410；下文以 411 占位，撞号顺延并同步文件内注释）。
- 集成测试 gated on `INTEGRATION_DATABASE_URL`（ephemeral 引导同 B4：pgvector:pg17 + ci_bootstrap + schema_baseline + migrations ≥365）。
- 前端语义色 token 纪律、i18n en/zh 齐平（PR-2 Task 6 无文案改动，仅逻辑）。
- **PR-1 与 PR-2 独立分支都从 origin/master 拉**；PR-2 不依赖 PR-1 合并（改动文件交集仅 project_stage_nodes_repository.py 的不同区域，冲突时后发者 rebase）。
- scope-resolver 守卫若拦新的直连，照仓库惯例补文档化 allowlist 条目。
- 行号基于 2026-08-08 盘点（worktree HEAD ≈ 947d269），执行时以内容锚定为准。

---

## PR-1: Canvas 退役 + method Shooting-only（branch `feat/b6-canvas-retirement`）

### Task 1: migration 411 — Canvas 节点数据退役

**Files:**
- Create: `supabase/migrations/411_retire_canvas_stage_node.sql`

**Interfaces:**
- Produces: node bank 无 canvas 行（phase=NULL 摘除，不硬删避 `project_stage_history.stage_id` NO ACTION FK）；全部模板/实例的 Canvas 节点行删除（FK 级联清 members/deps）；任何非终态 Canvas 镜像 issue 关闭。

- [ ] **Step 1: 写 migration**

```sql
-- 411_retire_canvas_stage_node.sql
--
-- B6(用户拍板 2026-08-08):退役流程条上的「Canvas (AI Generation)」独立阶段
-- 节点。画布功能/数据(canvases 表、AI 生成)完全不动——画布自 B5 起是
-- Storyboard 节点的三视图之一(spec §2:Storyboard/Canvas/Shot List 是同一个
-- 分镜节点的三种视图,不是三个节点),独立阶段节点是 B1 前旧模板把视图误当
-- 阶段的遗留,surface NULL 永远按交付物型降级,是流程条上的重复死节点。
--
-- ① node bank 摘除:phase=NULL 而非 DELETE——project_stage_history.stage_id
--   是 NOT NULL 无 ON DELETE 的 FK(mig 295),硬删遇历史行会 RESTRICT;
--   phase=NULL 让 seeder(list_stage_library 只取 phase IS NOT NULL)与
--   picker 立即失效,后续 CI 重放时本迁移编号大于 381,顺序压过其
--   ON CONFLICT DO UPDATE 回填。
-- ② 模板/实例行删除:按 source_stage_id/legacy_stage_id 关联(无 FK,靠
--   381 种子的 slug='canvas' 行取 id),members/deps 双端 CASCADE 自动清;
--   episodes.current_node_id/projects.current_node_id 均 ON DELETE SET NULL
--   (生产已验证无游标指向 Canvas,此为防御)。
-- ③ 镜像 issue 不级联(origin_id 字符串关联,生产已验证 0 行,此为防御):
--   删除前按 origin_id 关闭非终态镜像。
-- 幂等:重跑时子查询空集,各语句 no-op。

BEGIN;

WITH canvas_stage AS (
    SELECT id FROM public.project_stages WHERE slug = 'canvas'
),
gone_nodes AS (
    SELECT n.project_id, n.id
    FROM public.project_stage_nodes n
    WHERE n.legacy_stage_id IN (SELECT id FROM canvas_stage)
)
UPDATE public.issues i
   SET status = 'cancelled'
 WHERE i.origin_kind = 'project_stage'
   AND i.status NOT IN ('done', 'cancelled')
   AND i.origin_id IN (
       SELECT 'project_stage:' || g.project_id || ':' || g.id FROM gone_nodes g
   );

DELETE FROM public.project_stage_nodes
 WHERE legacy_stage_id IN (SELECT id FROM public.project_stages WHERE slug = 'canvas');

DELETE FROM public.workflow_template_nodes
 WHERE source_stage_id IN (SELECT id FROM public.project_stages WHERE slug = 'canvas');

UPDATE public.project_stages SET phase = NULL WHERE slug = 'canvas';

COMMIT;
```

- [ ] **Step 2: ephemeral 验证（双跑幂等 + 合成数据验删除面）**

同 B4 引导（容器 + ci_bootstrap + schema_baseline + migrations ≥365 到 411）。合成验证：insert 一个带 canvas source_stage_id 的模板节点、一个 legacy_stage_id 指向 canvas 的实例节点 + 一条 open 镜像 issue → 跑 411 → 断言模板/实例行没了、issue cancelled、`SELECT phase FROM project_stages WHERE slug='canvas'` 为 NULL；再跑一遍 411 确认幂等 exit 0。**容器留给 Task 3 集成回归。**

- [ ] **Step 3: Commit**

```bash
git add supabase/migrations/411_retire_canvas_stage_node.sql
git commit -m "feat(db): mig 411 — 退役 Canvas 独立阶段节点(node bank 摘除+模板/实例行删除+防御关镜像) — 画布功能不动,它是 Storyboard 的视图"
```

### Task 2: method 语义 Shooting-only

**Files:**
- Modify: `backend/app/repositories/project_stage_nodes_repository.py`（`_SLUG_CANVAS` 常量、`_resolve_skip` :192-207、hybrid 合组 :417-424 与其注释 :394-395/:243-244、`infer_legacy_binding` :746-830）
- Modify: `backend/app/services/workflow/template_seeder.py`（:15 docstring）
- Modify: `backend/app/schemas/projects.py`（:28-32 注释）
- Test: `backend/tests/test_workflow_instantiation.py`（钉子重写）

**Interfaces:**
- `_resolve_skip(slug, skip_default, method)` 签名不变；行为变化仅：删 `slug == _SLUG_CANVAS` 分支（canvas slug 已从 node bank 摘除，防御上视同普通节点走 skip_default）。
- `instantiate_from_template` 的 hybrid 分组：**整段删除**（method 不再改 parallel_group；hybrid 与 live 节点链等价，仅作用户可见标签）。
- `infer_legacy_binding` 反推：`shooting.skipped → 'ai'`，否则 `'live'`（hybrid 不可反推——既有 docstring 已声明 reinstantiate 可显式传 method 覆盖，保持）。canvas 查找逻辑删除。
- `_SLUG_CANVAS` 常量删除；所有「Canvas 常驻不可关」注释清理。

- [ ] **Step 1: RED — 重写测试钉子**

`test_workflow_instantiation.py`：
- 删 `_SLUG_CANVAS` import 与 `test_canvas_never_skipped_regardless_of_method_or_default`；
- 新增 `test_canvas_slug_gets_no_special_treatment`（`_resolve_skip("canvas", True, m) is True`、`(..., False, m) is False` 对全 method——canvas 已是普通 slug）；
- Shooting 三条钉子保留不动；`test_other_nodes_always_keep_template_default` 的 slug 列表加 `"canvas"`；
- 新增反推钉子 `test_infer_method_shooting_only`（构造 FakeSession：shooting skipped→'ai'；shooting 未 skip→'live'；**没有 canvas 节点也不炸**）——FakeSession 语句序参照该文件既有 `_Result` 范式。

Run: `cd backend && uv run pytest tests/test_workflow_instantiation.py -q` → 新用例 FAIL（canvas 分支还在/反推还看 canvas）。

- [ ] **Step 2: GREEN — 实现**

1. `_resolve_skip`：删 :200-201 两行；docstring 改「Shooting is forced on for live/hybrid and off for ai; every other node keeps its template default」。
2. hybrid 合组 :417-424 整段删 + :394-395/:243-244 注释删；`_SLUG_CANVAS` 常量删（:62），:61 注释改。
3. `infer_legacy_binding`：删 canvas 节点查找（:803-812 中 canvas 部分）与 hybrid 判定（:814-825 中 `canvas is not None and ...` 分支）；docstring :754-762 改为 Shooting-only 规则并注明「hybrid 无法从链形反推,点火/重实例化需显式传 method(既有行为)」。
4. `template_seeder.py:15`、`schemas/projects.py:28-32` 注释同步改（method 只翻 Shooting 开关）。

Run: `uv run pytest tests/test_workflow_instantiation.py tests/api/test_project_workflow_attach.py -q` → PASS。

- [ ] **Step 3: Commit**

```bash
git add backend/app/repositories/project_stage_nodes_repository.py backend/app/services/workflow/template_seeder.py backend/app/schemas/projects.py backend/tests/test_workflow_instantiation.py
git commit -m "feat(workflow): B6 method 语义 Shooting-only — 删 Canvas 常驻分支/hybrid 合组/反推 canvas 判定,hybrid 保留为标签"
```

### Task 3: seeder 测试跟改 + 集成回归

**Files:**
- Modify: `backend/tests/test_workflow_template_seeder.py`（node bank fixture 删 canvas 行 :44-45；`len(tpl["nodes"]) == 11` → `10` :190；:16 注释；删 :217 Canvas 断言）

- [ ] **Step 1: RED→GREEN**：先改断言跑 FAIL（fixture 还有 canvas 时 11≠10），再删 fixture canvas 行 → PASS。
- [ ] **Step 2: 集成回归**（Task 1 容器）：`INTEGRATION_DATABASE_URL=... uv run pytest tests/integration/test_episode_instantiation_db.py -q -m integration` → 全绿（其模板节点不带 source_stage_id，slug 恒 None，不受影响——盘点已确认）。
- [ ] **Step 3: Commit**（中文，略）。

### Task 4: 全量验证 + 发 PR-1

- [ ] 后端全量单测 + lint 三件套（改动文件）+ migration 撞号复核。
- [ ] PR 描述要点：删的是流程条重复阶段节点非画布功能；method 三选项 UI 不变、hybrid 变纯标签；合并后生产验收 SQL（模板 10 节点、`SELECT count(*) FROM project_stage_nodes WHERE name='Canvas (AI Generation)'` = 0）。
- [ ] 跑完 `docker rm -f` ephemeral 容器。

---

## PR-2: legacy 双路径删除（branch `feat/b6-legacy-dual-path-removal`）

### Task 5: 修 `get_active_group` 现网 bug（独立先行）

**Files:**
- Modify: `backend/app/repositories/project_stage_nodes_repository.py`（`get_active_group` :1435-1516、badges :1570-1589）
- Modify: `backend/app/services/workflow/node_mutations.py`（:98 调用）
- Test: `backend/tests/test_workflow_node_mutations.py`

**背景（盘点确认的现网 bug）**：`get_active_group` 游标恒读 `Projects.current_node_id`（:1459），而按集项目只写 `episodes.current_node_id` → 该列永久 NULL → 守卫 Guard 2（DELETE_BLOCK_ACTIVE）在按集项目上静默失效；badges :1589 同列 → 项目列表徽章 current_node_name 恒空。

**Interfaces:**
- `get_active_group(project_id, episode_id=None)` 改为：episode_id 给定 → 读 `episodes.current_node_id`；未给 → **遍历项目全部 episodes 的游标**（守卫语义：节点只要在任何一集的活动组就不可删）。
- `node_mutations` Guard 2 改传被删节点自身的 `episode_id`。
- badges：current_node 改读「sort_order 最小的、有游标的 episode」的游标节点。

- [ ] **Step 1: RED** — `test_workflow_node_mutations.py` 加用例：按集项目（节点带 episode_id、episodes.current_node_id 指向它）删除该节点 → 断言 DELETE_BLOCK_ACTIVE（当前会错误放行）。FakeSession/repo 范式照该文件既有。
- [ ] **Step 2: GREEN** — 实现上述三点。
- [ ] **Step 3**: `uv run pytest tests/test_workflow_node_mutations.py tests/test_workflow_stage_board.py -q` → PASS；Commit（中文说明这是现网守卫失效修复）。

### Task 6: advance_service 双路径删除

**Files:**
- Modify: `backend/app/services/workflow/advance_service.py`
- Test: `backend/tests/test_advance_episode_router.py`、`backend/tests/test_advance_predicate.py`

按盘点清单逐项（行号见盘点，内容锚定）：
1. 删模块级 shim 注释块 :366-386。
2. `_load_scoped_nodes_and_cursor`：删 :404-411 None 分支，签名 `episode_id: str`。
3. `_write_cursor`：删 :430-438 None 分支，签名收为 `(episode_id, node_id)`（project_id 参数删除，调用点 :698 等同步改）。
4. `compute_advance_preview`/`execute_advance`/`_preview_forward`：`episode_id: str` 必填；`_preview_back` 的 episode_id 参数**整个删除**（本就 `del episode_id`）。
5. `_deliverable_present`：删第三参 episode_id + docstring shim 段 + :491-493 threading 注释。

- [ ] **Step 1: RED** — 先删 `test_advance_episode_router.py` 的 4 条 legacy 钉子（:234-256/:258-278/:298-320/:356-380）与 `_FakeNodesRepo` 的 legacy 注释；`test_advance_predicate.py` 25 处未传 episode_id 的调用**全部补传 `_EP1`**（其 Fake 已有 episode 侧范式 :60-110），删 :767/:792 的项目游标探针断言与 `_FakeNodesRepo.set_current_node_id`。跑两文件确认 FAIL（服务还接受 None/还写项目游标）。
- [ ] **Step 2: GREEN** — 按清单实现 → 两文件全绿。
- [ ] **Step 3: Commit**。

### Task 7: 删项目级游标写入口 + 注释清理

**Files:**
- Modify: `backend/app/repositories/project_stage_nodes_repository.py`（删 `set_current_node_id` :1518-1529——Task 2 后零调用方）
- Modify: `backend/app/models/scripts.py`（:393 注释）、`backend/app/repositories/episode_repository.py`（:511-513 注释）
- Modify: `backend/tests/test_scope_resolver_single_choke_point.py`（:218-224 allowlist 条目 prose 刷新）

- [ ] grep 确认零调用方 → 删除 → `uv run pytest tests/test_scope_resolver_single_choke_point.py tests/test_workflow_episode_scoping.py -q` PASS → Commit。
- 注：`projects.current_node_id` **列本身保留**（历史数据 + ON DELETE SET NULL 链），只删写入口；列的退役留给未来 schema 清理。

### Task 8: router 四端点 episode_id 必填

**Files:**
- Modify: `backend/app/api/projects_router.py`（4 处 `Query(None)` → `Query(...)` 必填；`_require_project_episode` 无条件化；删 get_project_workflow 的 legacy body :413-416；start-early 删 legacy fallback :944-961——node.episode_id 为 NULL 时按 all-or-nothing 不可能，防御性 422 `EPISODE_MISMATCH` 复用 :949-958 既有分支；四处 docstring 的「None → legacy」句删除）
- Test: `backend/tests/test_start_early.py`（:178-180 尾参 None → 真值 + fake episode-ify）

- [ ] RED（先改测试传真值+断言缺参 422）→ GREEN → `uv run pytest tests/test_start_early.py tests/api/ -q` PASS → Commit。

### Task 9: autopilot legacy fixpoint 删除

**Files:**
- Modify: `backend/app/workflows/autopilot.py`
- Test: `backend/tests/test_autopilot_tick.py`

1. 删 `_run_legacy_fixpoint` :562-576；:667-671 改为：`bound_episode_ids` 非空但 `episodes` 空（读失败降级）→ `logger.warning` + return（盘点 §3e case 3 可见性）；真无绑定节点 → debug no-op return（case 1 行为等价）。
2. `_auto_start_pass`：`episode_id: str` 与 `budget` 必填，删 :277-286 fresh-quota 读与 legacy docstring 段；`_cascade_pass` 同收紧。
3. tick docstring :632-646 重写。

- [ ] **Step 1: RED** — 删 `test_autopilot_tick.py` 两条 legacy 钉子（:1171-1206/:1245-1287），新增 `test_no_bound_episodes_tick_is_noop`（无绑定节点 → 不调 list_nodes 之外的任何 pass，无 cascade 调用）与 `test_episode_read_failure_warns_not_silent`（bound ids 非空 + episodes 读挂 → warning 记录）。跑 FAIL。
- [ ] **Step 2: GREEN** → `uv run pytest tests/test_autopilot_tick.py tests/test_autopilot_sweep.py -q` PASS → Commit。

### Task 10: 前端 gate + 类型收紧

**Files:**
- Modify: `frontend/hooks/useProjectWorkflow.ts`（episodeId 为 null 时跳过 fetch——盘点发现首屏必发一次无 episode_id 请求，参数必填后会 422）
- Modify: `frontend/components/Todolist/issueFlow.ts` :167 / `frontend/components/workspace/WorkspaceTasks.tsx` :59（同 gate）
- Modify: `frontend/services/workflowService.ts`（4 个 wrapper `episodeId: string` 必填，删 `|| undefined`）
- Modify: `frontend/components/workspace/ProjectWorkspace.tsx` :190/:206、`WorkflowSection.tsx` :186、`WorkspaceStageBoard.tsx` :269（调用点判空后传）
- Test: 相应 *.test.tsx 与 4 套 e2e 跟改（e2e 桩 route 已带 `?episode_id` 尾 `*` glob——B2 T5 处理过，确认不回退）

- [ ] **Step 1: RED** — useProjectWorkflow 测试：episodeId null → 不发请求；service 类型收紧后 tsc 报出所有漏传点逐一修。
- [ ] **Step 2: GREEN** → `npx vitest run`（全量）+ `npm run typecheck`（基线 59 不新增）+ 4 套 e2e（`npx playwright test e2e/stage-board.spec.ts e2e/workflow-deps.spec.ts e2e/autopilot.spec.ts e2e/projects-workspace.spec.ts`）→ Commit。

### Task 11: Tier B 测试 episode-ify

**Files:**
- Modify: `backend/tests/test_deps_predicate.py`、`test_form_incomplete_predicate.py`、`test_stage_notifications.py`、`test_workflow_flow_rules.py`（机械：节点 dict 补 episode_id、Fake 补 `list_nodes_by_episode`/episodes repo、调用补 episode_id——范式抄 `test_advance_predicate.py` :60-110）
- `test_cross_episode_deps.py`：:180-189 防御钉子**保留**（docstring 措辞 legacy→defensive），:460 空探针删。

- [ ] 逐文件改 → `uv run pytest tests/test_deps_predicate.py tests/test_form_incomplete_predicate.py tests/test_stage_notifications.py tests/test_workflow_flow_rules.py tests/test_cross_episode_deps.py -q` 全绿 → Commit。
- 注：`instantiate_from_template` **保留**为 test-only building block（docstring 既有立场；14 处测试调用不迁移）。

### Task 12: 全量验证 + 发 PR-2

- [ ] 后端全量单测 + 前端 vitest 全量 + tsc 基线比对 + 集成套件（B4 三文件 + episode_instantiation）+ lint。
- [ ] PR 描述要点：get_active_group 现网守卫失效修复（独立价值）；API breaking（4 端点 episode_id 必填——前端同 PR 已适配，无第三方调用方）；autopilot 读失败从「静默回退 legacy」变「warning 可见」；`projects.current_node_id` 列保留只删写入口。
- [ ] 合并后生产验收：readyz；删节点守卫抽验（试删某集游标节点应 DELETE_BLOCK_ACTIVE）；autopilot tick 正常（dbos.workflow_status 查询）。

## Self-Review 记录

- 覆盖两份盘点的全部 🔴 项：Canvas 四个代码触点（_resolve_skip/合组/反推/常量）、seeder 幂等（编号压 381）、FK/镜像防御、legacy 六层（shim 注释/load/write/预览签名/router/autopilot）、get_active_group bug、前端首屏 422 风险（gate 先于类型收紧）、Tier A/B/C 测试分类处置。
- 不做：`projects.current_node_id` 列删除（留未来）；`instantiate_from_template` 删除（保留 test-only）；hybrid 枚举值收缩（用户可见标签保留）；badges 之外的项目级展示重构。
- 类型一致性：`_write_cursor(episode_id, node_id)`、`get_active_group(project_id, episode_id=None)` 新签名在各 Task 引用一致。
