# 资产库 P3 — 项目分级视图 + 迁移执行 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 项目工作区的人物/场景库/道具/服装页换到 assets 数据源（Link from library / New / Import from script 全落 assets+refs），并解封一次性迁移 workflow（先修 P0 终审判给 P3 的四个缺陷与 `_reconcile`/`_apply` 过滤不一致）。

**Architecture:** 后端先修前置缺陷（M2/M3/M4/M11）→ 对齐并解封 `backfill_assets_from_project_entities`（补画布反解与 generated_media 映射两步）→ 新增 `import-from-script` 端点；前端把 `WorkspaceEntities`/`EntityLibrary` 换成 `AssetCard` 网格 + `GET /projects/{pid}/assets?type=`。旧表 rename 是**独立的后续 PR**，只在用户跑完真迁移并对账后发。

**Tech Stack:** FastAPI + SQLAlchemy async ORM（`read_scope`/`write_scope`/`unit_of_work`/`system_request_scope`）、DBOS backfill 范式（admin `_BACKFILLS`、`dry_run=True` 默认、`run_user_id`）、React 19 + vitest/RTL、Envelope[T]。

**Spec:** `docs/superpowers/specs/2026-08-28-asset-library-loadout-design.md`（§3.5/§3.8/§4/§5.1/§6.4/§9 P3 行）

## Global Constraints

- 实施者/审查者一律 opus（用户规矩，不按复杂度降档）。
- UI 全英文 Title Case，禁 emoji（lucide 图标）；i18n camelCase key + en/zh parity 测试。
- 状态色只用语义 token（ok/warn/danger/info/agent）。
- 边界 mock 用真实 wire 形状（本 router id 全 `str()`，`scope_id` 预设为 null）。
- 后端 ORM only（禁新增 `text()`）；black/isort/flake8；触及 `Resources` 的 workflow 必须 `system_request_scope`。
- vitest 禁 `--reporter=basic`；必须看到 "Tests N passed"。
- 禁 `git add -A`；禁触碰生产 DB 容器 `nous-db`；迁移执行只经 admin `_BACKFILLS` 由用户先 dry-run。
- `catch` 不静默；触发路径必须类型化失败回显。
- 迁移文件取号前先 `git fetch` 查最新号（migration 编号冲突教训）。

---

### Task 1: 后端前置 — M2/M3/M4/M11 四缺陷

**Files:** Modify `backend/app/repositories/assets_repository.py`（M2 ~:222、M11 见 relations）、`backend/app/services/assets/assets_service.py:253-270`（M3）、`backend/app/schemas/assets.py:57`（M4）、`backend/app/repositories/asset_relations_repository.py:388-396,428-436`（M11）；Test 各自现有测试文件 + `tests/api/test_assets_router.py`。

- M2：`list` 的 `q` 过滤 `ilike(like)` 不转义 —— 改为对 `q` 先做 `\` `%` `_` 转义并传 `escape="\\"`（`name.ilike(like, escape="\\")`），测试：`q="a_b"` 不命中 `"axb"`、命中 `"a_b"`。
- M3：`create_asset` 的 create 与 Default loadout 分属两个事务 —— 包进一个 `unit_of_work()`（沿用 batch-attach 的裸形式与注释规则），测试：loadout 创建抛错时资产不落库。
- M4：`AssetCreate.source` 客户端可设任意值 —— API 边界白名单：router 入参模型限 `Literal["manual","generated"]`（`duplicated`/`migrated`/`system_preset` 只许服务端内部设置；`SaveAsAssetDialog` 用的 `generated` 保留）。前端 `AssetCreateBody.source` 类型同步收窄。测试：POST `source="migrated"` → 422。
- M11：两处 `with_for_update()` 无序 —— 均加 `.order_by(AssetLoadouts.id)`，注释写明防死锁；编译 SQL 断言含 `ORDER BY`。
- [ ] 提交 `fix(assets): P3 prereqs — LIKE escape, atomic create, source allowlist, ordered locks`

### Task 2: 后端 — `_reconcile`/`_apply` 对齐 + 解封 dry_run

**Files:** Modify `backend/app/workflows/backfill_assets_from_project_entities.py`；Test `backend/tests/workflows/test_backfill_assets_plan.py`（现有）+ 新增 integration 用例进 `tests/db/test_assets_repository_integration.py` 系列（INTEGRATION_DATABASE_URL，schema-drift 门禁跑）。

- 对齐：`_apply` 按 `(scope_id, asset_type, lower(name))` 认领**任意来源**的同名资产（故意，合并用户手建的），而 `_reconcile` 只数 `source='migrated'` —— 改 `_reconcile` 为按 plan 的 keys 逐个查存在性（scope+type+lower(name)，不看 source）+ 数 refs，两边口径一致；`reconcile_counts` 的期望值同步改。
- 解封：删除 `_reject_execution_until_p3()` 及调用；`dry_run=False` 走通 `_apply` → `_reconcile`。整段 `Resources` 无涉，但 `_load_inputs` 若触及带 scope mixin 的模型需确认 `system_request_scope` 包裹（对照 PR #2070 形状）。
- 幂等：重跑 `_apply` 全部走 `existing` 分支、refs upsert 不重复；integration 用例断言两次 apply 后 `count(assets)` 不变。
- [ ] 提交 `feat(assets): unseal migration apply — reconcile aligned with adoption semantics`

### Task 3: 后端 — 迁移补全（画布反解 + generated_media 映射 + 封面反解）

**Files:** Modify 同上 workflow；Test 同上。

- 封面（spec §4 步 1/2 尾注）：`portrait_url`/`cover_url` 试反解为 `resources` 行（按 URL 中的 resource id 或 file_path 匹配；只认本 scope）→ 命中则写 `cover_file_id` 并 attach `unsorted` 槽；不命中留 `attrs.legacy_cover_url`（现状已留）。触及 `Resources` 读 → `system_request_scope(reason=...)`。
- 步 4：`canvases WHERE kind IN ('character','location','prop')` 按 `"{name} · {Kind}"` 反解到本 scope 同名 asset，命中写 `canvases.asset_id`；不命中跳过并计数（`skipped_unparsed_canvas`）。
- 步 5：`generated_media.params->>'entity_kind'/'entity_id'` 按步 1/2 的 legacy→asset id 映射表写 `source_asset_id`；`promoted_resource_id IS NOT NULL` 置 `review_state='saved'`；被 `asset_files` 引用的置 `in_assets`（复用 P1 回填的判定 SQL 形状）。
- dry-run 输出把新增各步的计数并入 Task Center subtitle（沿用 skipped_personal_project 桶的呈现）。
- [ ] 提交 `feat(assets): migration steps — canvas reverse-parse, genmedia mapping, cover resolution`

### Task 4: 后端 — `POST /projects/{pid}/assets/import-from-script`

**Files:** Modify `backend/app/api/projects_router.py`（新端点，放 assets 相关段）、`backend/app/services/assets/assets_service.py`（新方法 `import_from_script`）；Test `backend/tests/api/test_projects_assets_import.py`（新）。

- 语义：取 `ProjectsService().get_project_entities(project_id)` 的 characters + locations 名单（同现有 extract 端点的来源），逐名 `create_asset`（type=character/location，scope=项目所属 team 或 owner 个人 team——复用 assets_router 现有 `_project_gate` 解析）+ `link_project`；同名已存在 → 只补 ref（不是 409）。**旧 extract 端点保持不动**（退役归 rename PR）。
- 返回类型化逐条结果 `{name, action: created|linked|skipped, asset_id?, code?}` + Envelope；个人项目无 team → 422 `personal_team_missing`。幂等：重跑全部 `linked`/`skipped`。
- [ ] 提交 `feat(assets): import-from-script lands assets + project refs`

### Task 5: 前端 — 项目工作区素材页换数据源

**Files:** Modify `frontend/components/workspace/WorkspaceEntities.tsx`（改为 AssetCard 网格容器）、工作区侧栏（素材组加 Costumes 项——先 grep 挂载点）、`frontend/services/assetsService.ts`（`importFromScript` 调用 + 已有 `listProjectAssets`/`linkProject`/`unlinkProject` 摘掉 foothold 注释）；Create `frontend/components/workspace/ProjectAssetsPanel.tsx` + `LinkFromLibraryDialog.tsx`；Test 对应 `.test.tsx` + i18n parity。

- 页面 = `AssetCard`（P2 组件复用）网格，数据 `listProjectAssets(scopeId, projectId, type)`；点卡进 `resources/assets/item/:id`（跨模块导航）。
- 顶部动作：`Link From Library`（搜本 team 未关联的同类资产 → `linkProject`）、`+ New`（`NewAssetDialog` 复用，创建后自动 `linkProject`）、`Import From Script`（新端点，逐条结果回显 created/linked 计数与失败行）、卡片菜单 `Unlink`（只删 ref，文案说明资产仍在库）。
- 空态区分：项目无关联资产（引导 Link/New）≠ 加载失败（loadError 形状照 AssetShelf）。
- [ ] 提交 `feat(assets): project workspace panels on the assets source`

### Task 6: 前端 — 旧读者退役/降级

**Files:** Modify/Delete `frontend/components/workspace/EntityLibrary.tsx`、`EntityAssetStrip.tsx`、`frontend/services/libEntitiesService.ts` 的消费面；`frontend/editor/components/EditorShell.tsx` 与 `frontend/features/canvas-core/smart/entityRef.ts` 先盘点再动。

- 盘点每个 `libEntitiesService` 消费者：属于本期换源的（WorkspaceEntities 链）删除；属于 P4 画布节点的（entityRef/smart）**保留并加注释**指向 P4；编辑器内联用途逐个判定并在报告记录。
- `EntityLibrary`/`EntityAssetStrip` 若无剩余消费者即删文件与测试；有剩余则留文件、删已换源的入口，报告说明。
- 全套件回归：删除不得留死 import / 死 i18n key（清 key 要过 parity）。
- [ ] 提交 `refactor(assets): retire legacy entity-library readers superseded by assets`

### Task 7: i18n / e2e / docs / PR 草稿

**Files:** locales、`frontend/e2e/project-assets.spec.ts`（新，mock 真实 wire 形状：链接 → 计数变化 → Import 逐条结果）、`e2e-prod/walkthrough.spec.ts`（项目素材页可见步，仅 toBeVisible）、CLAUDE.md 一行、`.superpowers` 工作区 `pr-body-p3.md`（不 push 不开 PR，终审后由控制器执行）。

- PR body 必列：依赖 P2；迁移执行是**部署后由用户在 admin 跑**（dry-run → 对账 → live）；rename PR 是后续独立 PR 且以对账通过为前提；本期不动旧 extract 端点。
- [ ] 提交 `chore(assets): P3 wrap-up — i18n, e2e, docs`

### Task 8（独立后续 PR，本期只备好文件，不进本 PR）: 旧表 rename legacy

**Files:** Create `supabase/migrations/<fetch 后取号>_rename_legacy_project_entity_tables.sql`（`ALTER TABLE project_characters RENAME TO _legacy_project_characters;` 同 lib_entities；顺带 DROP 旧 extract/characters/lib 端点与两个 repository、`models/project_library.py`、前端 `libEntitiesService` 残余）。

- **门槛**：仅在用户确认真迁移对账通过后另开分支执行；本计划内此 task 只写迁移文件草稿放 `.superpowers` 工作区（不落 `supabase/migrations/`），并在 PR body 已知中声明。
- [ ] （本期无提交）

## Self-review

- Spec 覆盖：§9 P3 行五项——侧栏数据源（T5）、Link from library（T5）、import-from-script（T4）、跑迁移（T2/T3 解封 + 用户执行）、rename legacy（T8 门控后续）。P0 判给 P3 的 M2/M3/M4/M11 + 过滤对齐（T1/T2）。§4 七步中 6 已由 P1 覆盖、7 是占位，其余在 T2/T3。
- 无占位符；类型/签名与现行代码核对过（`_apply` 认领语义、`AssetSource`、`listProjectAssets` 已存在）。
- 顺序依赖：T1→T2→T3 后端串行；T4 独立；T5 依赖 T4 端点；T6 依赖 T5；T7 收尾。
