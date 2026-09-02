# 资产库 P4 — 画布集成 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 画布上出现 `asset` 节点（引用 `assets`，节点自带 loadout / 参考图勾选状态），生成节点上游有资产时按 provider 能力裁剪投递（bundle），Output 节点可 As Asset 预填来源，`canvas_asset_refs` 由保存路径维护并支撑反查，旧的 character/location/prop 智能卡迁到 asset 节点。

**Architecture:** 后端补三块——`canvas_asset_refs` 维护/反查（镜像 `canvas_resource_refs` 范式）、`GET /assets/{id}/bundle?model=&loadout_id=`（复用 P2 的 `slot_prompt`/`reference_order` + 代码内 `ProviderCapabilities`）、画布生成链的 **resource 参考桥**（`/api/v1/resources/{id}/cover|file` URL 可被物化，与 genmedia URL 并列）；前端补 `asset` 智能节点、bundle 接线、Output 的 As Asset、Send To Canvas / Insert Project Assets 入口、旧卡迁移。

**Tech Stack:** FastAPI + SQLAlchemy async ORM、DBOS canvas_generation workflow、React 19 + canvas-core（smart nodes registry/factories/clipboard）、vitest/RTL + Playwright。

**Spec:** `docs/superpowers/specs/2026-08-28-asset-library-loadout-design.md`（§3.7 / §5.1 / §6.3 / §9 P4 行 / §10）。侦察报告：`scratchpad/p4-recon.md`（控制器会在派发时把相关段落带给实施者）。

## Spec 修正裁决（侦察实证，计划以此为准，Task 8 回写 spec）

| # | spec 原文 | 现状 | 裁决 |
|---|---|---|---|
| A | §10 provider 能力表放 `config.yml`，缺省保守 1 张 | 能力表已在代码里：`provider_protocols/base.py::ProviderCapabilities`（`max_refs` 等），`base.py:53-58` 明文禁止第二处真相；`config.yml` 无此键 | **不动 config.yml**；bundle 与前端一律读 `resolve_generation_protocol(model).capabilities` / `GET /canvases/generation-capabilities` |
| B | §6.3 能力表含 `multi_subject` | 全仓无此概念 | **删除**该字段（YAGNI） |
| C | §5.1 `GET /assets/{id}/loadouts/{lid}/bundle` | 只有 character 有 loadout | 改为 **`GET /assets/{id}/bundle?model=&loadout_id=`**（loadout 可选） |
| D | §6.3 bundle 返回 `reference_resource_ids[]`，画布直接投递 | 画布生成链只接受 `/generated-media/` URL（`GENERATED_MEDIA_URL_RE`、前端 `DURABLE_PREFIX`），resource URL 会被**静默丢弃且不进 dropped** | **建 resource 参考桥**（Task 3）：后端物化 `/api/v1/resources/{id}/(cover\|file)`（scope 校验 + `system_request_scope`），不可解析的进 `dropped`；前端 `DURABLE_PREFIX` 扩为两种前缀 |
| E | §6.3「超限的在节点上灰掉并提示」 | `caps.max_refs` 前端零消费者，ref UI 靠 `MAX_REFERENCE_IMAGES=20` | Task 5 让 asset 节点的参考勾选读 `useModelCapabilities().max_refs`；超限灰掉 + 提示；仍以后端 `reconcile` 的 `dropped_knobs` 为最终真相 |
| F | §5.1 `GET /assets/{id}` 带 `used_in` | 从未实现，sheet 显示「Canvas usage arrives with P4」 | Task 1 实现 `used_in.canvases`（storyboard 引用留后续，字段预留 `used_in.storyboards: []` 并注明） |
| G | `canvases.kind='costume'` | DB CHECK 有、Pydantic/TS 枚举无，`canvasKindFor` 把 costume 降为 smart | Task 4 补两侧枚举 |
| H | 旧智能卡 `character_id`/`entity_id` 与 `params.entity_kind/entity_id` 戳 | 读半边（`fetchEntityGenerations`）自 P3 T6 起无调用方；写半边仍活 | Task 5 **两半一起退役**：戳改写 `source_asset_id`（列已存在），删 `fetchEntityGenerations` 与 `entity_kind/entity_id` 写入；旧卡由 Task 6 迁移 |
| I | `canvas_asset_refs.loadout_id` 不在 PK | 照抄 `replace_for_canvas` 的 `on_conflict_do_nothing` 会留旧 loadout | Task 1 用 DELETE-all + INSERT `on_conflict_do_update(set_=loadout_id)` |
| J | 命名 | `services/canvas/asset_refs.py::extract_asset_refs` 已被 **resource** refs 占用 | 新模块 `asset_node_refs.py::extract_asset_node_refs`；旧名不改（避免无关 churn），加一行注释指明 |

## Global Constraints

- 实施者/审查者一律 opus；SDD 流程；每任务真实 wire 形状 mock（`assets` 路由 id 字符串；`canvases` 路由 id 字符串；`generated_media` 项按 `GeneratedItem`）。
- UI 英文 Title Case、禁 emoji、lucide；i18n camelCase + parity；语义色 token。
- 后端 ORM only；`Resources` 读必包 `system_request_scope`；black/isort/flake8；失败必须类型化回显（bundle 的 `dropped[]`、生成结果的 `dropped_knobs`）。
- 画布保存路径的 refs 维护**失败只记日志不阻塞保存**（§7），可 backfill。
- 节点复制 = 引用复制：`asset_id`/`loadout_id`/`selected_file_ids` **不得**进 `remapSmartTags`。
- vitest 禁 `--reporter=basic`；tsc 0 新错；禁 `git add -A`；禁触碰生产 DB 容器。

---

### Task 1: 后端 — `canvas_asset_refs` 维护 + 反查 + `used_in`

**Files:** Create `backend/app/services/canvas/asset_node_refs.py`（纯函数 `extract_asset_node_refs(nodes_json) -> list[{asset_id:int, node_id:str, loadout_id:int|None}]`，只认 `type=='asset'` 节点，`data.asset_id` 必须是雪花整数字符串、非法跳过并计数）、`backend/app/repositories/canvas_asset_refs_repository.py`（`replace_for_canvas(canvas_id, refs)`：DELETE-all + 多行 `pg_insert … on_conflict_do_update(index_elements=[canvas_id,asset_id,node_id], set_={loadout_id})`；`list_for_canvas(canvas_id)`；`list_canvases_for_asset(asset_id, scope_id)` join `canvases`+`projects` 限同 scope，返回 `{canvas_id, canvas_name, project_id, node_ids[], loadout_ids[]}`）；Modify `backend/app/services/canvas/canvas_service.py::_sync_refs`（在 resource refs 之后同样调用，同样 try/except+log），`backend/app/api/canvases_router.py`（`GET /canvases/{id}/asset-refs`，复用 `_gate_canvas_read`），`backend/app/api/assets_router.py`（`GET /assets/{id}/canvas-refs`，`_gate` + Envelope），`backend/app/services/assets/assets_service.py::get_asset`（加 `used_in: {canvases: [...], storyboards: []}`）与 `schemas/assets.py`（`AssetDetailResponse.used_in`，`UsedInResponse`）；Create `backend/scripts/backfill_canvas_asset_refs.py`（镜像 `backfill_canvas_resource_refs.py`；此时应为 0 行，占位可跑）；`services/canvas/asset_refs.py` 顶部一行注释指明它是 resource refs。
- Tests：纯函数（合法/非法 id/无 loadout）；repo 编译 SQL 钉 `ON CONFLICT … DO UPDATE SET loadout_id`；service 钉「refs 失败不阻塞保存」；router 两条反查（scope 门控、跨 scope 404）；`used_in` 出现在 detail 且 P2 的响应模型-ORM 列钉子测试更新；E2 用例进 `tests/db/test_assets_repository_integration.py` 系列（真 PG：保存含 asset 节点的 canvas → refs 行 → 换 loadout 再保存 → loadout_id 更新而非残留）。
- [ ] 提交 `feat(canvas): maintain canvas_asset_refs on save + reverse lookups + used_in`

### Task 2: 后端 — bundle 投递协议

**Files:** Create `backend/app/services/assets/bundle.py`（纯函数 `build_bundle(asset_row, loadout_row|None, linked_assets, files_by_slot, caps: ProviderCapabilities, user_text: str|None) -> {prompt:{positive,negative}, reference_resource_ids:[...], dropped:[{resource_id, reason:'over_limit'|'no_image_file'}], max_refs}`：拼接顺序 asset.positive → loadout.prompt_extra → costume/prop positives → 场景 asset positive（若 linked 含 location）→ user_text；negative 并集去重；参考优先级复用 `slot_generation._slot_priority`/`reference_order`，`max_refs` 取 `caps.max_refs`（0 → 全部 dropped 且 reason `provider_no_refs`））；Modify `assets_service.py`（`get_bundle(scope_id, asset_id, model, loadout_id, user_id)`：`_require`（可读即可）、`resolve_generation_protocol(model, user_id=...)` 取 caps，未知 model → 422 `model_unknown`）、`assets_router.py`（`GET /assets/{id}/bundle?model=&loadout_id=`，放在 `/assets/{asset_id}` 之后无冲突，Envelope）、`schemas/assets.py`（`BundleResponse`）；前端 `services/assetsService.ts` 加 `fetchBundle(scopeId, assetId, {model, loadoutId})`。
- Tests：纯函数每 provider 一组（max_refs 0/3/9；顺序；negative 去重；dropped 回显不静默）；service 422 model_unknown / 404 / loadout_mismatch；router 一条 wire 形状。
- [ ] 提交 `feat(assets): bundle endpoint — provider-aware reference trim + prompt composition`

### Task 3: 后端 + 前端 — resource 参考桥

**Files:** Modify `backend/app/services/library/generated_media_service.py`（新增 `RESOURCE_URL_RE = r"/resources/(\d+)/(?:cover|file)$"` 与 `resource_local_path(url, *, scope_id, media_kind)`：`system_request_scope(reason="canvas-generation: resolve resource reference")` 下读 `Resources`，校验行 scope == 生成的 scope（用 `resource_in_scope` 同族判定），走 `media_storage.materialize()`，含 containment guard；不命中/跨 scope → `None`）、`backend/app/workflows/canvas_generation.py`（image 与 daemon 分支：每个 ref 先试 genmedia 再试 resource；**不可解析的 ref 记入结果的 `dropped_knobs`/新字段 `dropped_refs:[{url, reason}]`**，永不静默）、`frontend/features/canvas-core/smart/promptInputs.ts`（`DURABLE_PREFIXES = ['/api/v1/generated-media/', '/api/v1/resources/']`，头注释同步改写）、`generationRunner.ts`（把 `dropped_refs` 汇入 PromptNodeView 的「Ignored」徽章）。
- Tests：后端 URL 解析（两种形状 + 拒绝 `HTTPS://`/外域）、跨 scope 拒绝、物化失败 → dropped；workflow 单测钉「不可解析的 ref 出现在 dropped 而非消失」；前端 `durableUrls` 两前缀 + 徽章渲染。
- [ ] 提交 `feat(canvas): resource reference bridge — resources URLs materialize like generated-media`

### Task 4: 前端 — `asset` 智能节点 + costume 枚举

**Files:** Modify `frontend/features/canvas-core/smart/types.ts`（`SmartNodeType` 加 `'asset'`；`AssetNodeData{asset_id:string, loadout_id:string|null, selected_file_ids:string[], name, asset_type, cover_file_id:string|null, readiness_state, removed?:boolean}`；`canConnectSmart`：asset → prompt/shot/llm 允许，asset ← 任何禁止）、`smart/nodes/registry.ts`、`smart/factories.ts`（`createAssetNode(asset: AssetRow, loadoutId?)`）、Create `smart/nodes/AssetNodeView.tsx`（封面 `getResourceCoverUrl`、名字/类型 chip、loadout 下拉（仅 character，来自 `fetchAssetDetail`）、参考文件勾选列表（主槽默认勾选；`selected_file_ids` 节点本地）、`Asset Removed` 占位（detail 404 → `removed:true`，节点不消失）、`Open Sheet` 链接）；`ui/TopNodeBar.tsx` / `ui/DragCreateMenu.tsx` 加入口（从库选：复用 `LinkFromLibraryDialog` 风格的选择器或 `searchAssets`）；`smart/clipboard.ts` 钉测试：asset 字段不被 remap；costume：`backend/app/schemas/canvas.py` `CanvasKind`/`CreatableCanvasKind` 加 `costume`，`frontend/features/canvas-core/types.ts` 同步，`assetSheetModel.ts::canvasKindFor` costume → `'costume'`。
- Tests：view 渲染（wire 形状 fixture）、404 → removed 占位、复制保留 asset_id、连接规则、kind 枚举两侧钉子。
- [ ] 提交 `feat(canvas): asset smart node with loadout + reference selection`

### Task 5: 前端 — bundle 接线 + 溯源戳换代 + 旧戳退役

**Files:** Modify `smart/promptInputs.ts`（`resolveAssetInputs(promptId, nodes, connections)`：一跳上游 asset 节点 → 对每个调 `fetchBundle(model)` → 参考 URL = `/api/v1/resources/{id}/cover`（按 bundle 顺序，只取 `selected_file_ids ∩ reference_resource_ids`）；prompt 前缀 = bundle.prompt.positive；negative 合并；bundle.dropped 回显到 asset 节点）、`smart/generationRunner.ts`（`params.source_asset_id` = 最近上游 asset（沿用 `resolveEntityRef` 的 BFS 改名为 `resolveAssetRef`）、`params.loadout_id`；**删除** `entity_kind/entity_id` 写入）、`smart/entityRef.ts` → `assetRef.ts`（调用点 `CanvasComposer.tsx:370`、`regenerate.ts:108`、`chainRun.ts:186`、`loopRunner.ts:125`、`runner.ts:38` 同步）、`frontend/services/generatedMediaService.ts`（删 `fetchEntityGenerations` 及其测试——两半一起退役）、`smart/nodes/AssetNodeView.tsx`（超限灰掉：读 `useModelCapabilities()` 的 `max_refs`，勾选超过上限的项 disabled + 提示 `assets.node.refsLimit`）。
- Tests：bundle 接线（mock fetchBundle 真实 wire 形状）；source_asset_id 戳；旧戳不再写（负向）；超限灰掉/提示；`assetRef` BFS 就近。
- [ ] 提交 `feat(canvas): bundle-aware generation inputs, source_asset_id provenance, retire entity stamp`

### Task 6: 前端 — 入口：Output As Asset / Send To Canvas / Insert Project Assets / 旧卡迁移

**Files:** Modify `smart/nodes/OutputNodeToolbar.tsx` + `OutputNodeView.tsx`（`As Asset…`：按 `GeneratedImageRef.id` 调 `generatedService` 取 `GeneratedItem`（已有 by-id 或列表过滤——先核；缺则 Task 6 在 `generated_router` 加 `GET /generated/{id}`）→ `SaveAsAssetDialog` `items=[item]`，`source_asset_id/loadout_id` 预填由 item 自带）；`components/resources/assets/sheet/SheetSidebar.tsx`（`Send To Canvas` 启用：选目标画布（本项目画布列表，或新建）→ 在其 `nodes_json` 追加 `createAssetNode` → `PUT` 保存 → 导航 `?node=`）；`ui/TopNodeBar.tsx` 或画布菜单加 `Insert Project Assets`（`listProjectAssets` 四类 → 四条 lane 铺节点，零手工）；**旧卡迁移**：`CanvasPage` 加载后对 `character/location/prop` 节点按 `data.character_id/entity_id` 调新增后端 `GET /assets/resolve-legacy?kind=&legacy_id=`（Task 6 后端小端点：按 `attrs.legacy_ids` 查，scope 门控）→ 命中则原地替换为 asset 节点（保留位置/连线，下次保存写回）；未命中 → 保留旧卡但打 `Unmigrated` 标记（不静默）；`CanvasPage` 的 `?characterId/?entityId` 播种分支删除，改为：canvas 行有 `asset_id` 且节点为空 → 播一个绑定的 asset 节点。
- Tests：As Asset 打开对话框且 items 形状真实；Send To Canvas 写入节点并导航；Insert 四 lane；迁移命中/未命中两态；播种。
- [ ] 提交 `feat(canvas): asset entry points — As Asset, Send To Canvas, Insert Project Assets, legacy card migration`

### Task 7: 后端 — `resolve-legacy` 端点 + `GET /generated/{id}`（若 Task 6 核出缺失）

**Files:** `assets_router.py`（`GET /assets/resolve-legacy?scope_id=&kind=character|location|prop&legacy_id=`，查 `assets.attrs->'legacy_ids' @> [[table, id]]`——ORM `JSONB.contains`，注册顺序在 `/assets/{asset_id}` 之前）、`generated_router.py`（`GET /generated/{id}`，scope 门控，返回 `GeneratedItem`）。
- Tests：命中/未命中/跨 scope；路由顺序回归；wire 形状。
- [ ] 提交 `feat(assets): resolve-legacy + generated item lookup`
- 注：为让 Task 6 不被阻塞，**Task 7 先于 Task 6 执行**（控制器按 1→2→3→4→5→7→6→8 派发）。

### Task 8: i18n / e2e / docs / spec 回写 / PR 草稿

**Files:** locales；`frontend/e2e/canvas-assets.spec.ts`（mock 真实 wire：拖入 asset 节点 → 连到 prompt → 生成请求体带 resources URL 与 bundle 前缀 → Output As Asset 打开对话框预填）；`e2e-prod/walkthrough.spec.ts`（画布 tab 加「asset 节点可见或空画布」的可见性步）；CLAUDE.md 一行；spec 回写本计划头部 A–J 十条裁决；`.superpowers` 工作区 `pr-body-p4.md`（不 push）。
- [ ] 提交 `chore(canvas): P4 wrap-up — i18n, e2e, docs, spec amendments`

## Self-review

- Spec 覆盖（§9 P4 行）：asset 节点（T4）/ bundle 投递协议（T2+T3+T5）/ Output 预填（T6）/ Insert project assets（T6）/ canvas_asset_refs（T1）。§6.3 其余：Asset removed 占位（T4）、复制=引用复制（T4 钉）、超限灰掉（T5）。§5.1 反查两端点 + used_in（T1）。
- 十条不一致均有归属（A/B/E→T2/T5+T8 回写；C→T2；D→T3；F/I/J→T1；G→T4；H→T5/T6）。
- 顺序：T1→T2→T3 后端串行；T4 独立；T5 依赖 T2/T3/T4；T7 先于 T6；T8 收尾。
- 无占位符；类型/签名对照侦察报告的 file:line 写出。
