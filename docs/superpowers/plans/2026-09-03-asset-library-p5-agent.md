# 资产库 P5 — 聊天 / agent 接入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 资产能被送进聊天（实体页 / 资产卡 Send To Agent → `pendingAsset`；聊天 `@` 选择器加 Assets 栏），后端把资产引用展开成「主图（作为普通 resource ref，模型用 ResourceFetch 取图）+ 一致性提示词」进 `<available_resources>`，失败类型化回显。

**Architecture:** 后端先立解析器（按 team 成员 + 系统预设解析可访问资产，`system_request_scope` 包裹读 `Resources`）与纯函数展开（复用 `slots.PRIMARY_SLOT` / `reference_order` / bundle 的拼接去重，不经 `ProviderCapabilities`）→ 渲染进 `<available_resources>`（`<asset …>一致性提示词</asset>` 元素，属性 `escape_frame_attr`、正文 `escape_frame_body`）→ chat service 分流第三桶 + `attachment_failures` + 持久化键 → 成员范围资产搜索端点 → 前端 `pendingAsset` 通道 + 共享发送 helper + 暂存行 + 气泡 chip → `@` 选择器 Assets 栏（从 `PromptMentionPicker` 抽共享网格）。

**Tech Stack:** FastAPI + SQLAlchemy async ORM（`read_scope` / `system_request_scope` / `is_enforced`）、`app/boundary/frame_markers.py`、React 19 + zustand + tiptap + vitest/RTL + Playwright（mocked，不进 CI）。

**Spec:** `docs/superpowers/specs/2026-08-28-asset-library-loadout-design.md` §6.5 / §7.0 / §9 P5 行。侦察：`.superpowers/p5-recon.md`（引用行号以它为准）。

## 裁决（spec 修订，控制器 2026-09-03）

| # | 问题 | 裁决 | 错了的代价 |
|---|---|---|---|
| A | `<asset>` 是独立框还是 `<available_resources>` 内元素？ | **框内元素** `<asset id=… type=… name=… scope=… primary_resource_id=… has_image=… loadout=…>一致性提示词</asset>`，与 `<resource …/>` 平级；属性全走 `escape_frame_attr`，正文走 `escape_frame_body`；`asset` 加进 `test_frame_escape_wiring.py` 的 `ignore` 名单，**不进 `OWNED_FRAMES`**（spec §6.5「登记进 OWNED_FRAMES」按此修订：它不是框，提前闭合只关自己这一行、逃不出框） | 用户散文里的 `</asset>` 会截断该条目的提示词，但不越权 |
| B | 聊天没有 scope，资产怎么授权？ | **后端按成员关系解析**：`assets.scope_id ∈ (team_members WHERE user_id)` OR `is_system_preset`，`deleted_at IS NULL`，与 `resource_ref_resolver._fetch_accessible_meta` 同构；**守卫测试**钉住与 `assets_router._gate`+`repository.get` 的口径等价（同一 fixture 两条路答案一致） | 两套口径漂移 → 聊天能看到 router 拒绝的资产（或反之） |
| C | 失败回显走哪条？ | `attachment_failures`（`{index, kind:'asset_ref', reason}`，`AttachmentFailureBanner` 现成）；reason 枚举：`asset_not_accessible` / `asset_deleted` / `asset_no_primary_image`（仅在期望有图的类型上）/ `asset_type_unknown` | 只有模型看到失败，用户不知道 |
| D | `loadout_id` | `pendingAsset` 与 `@` 选择器 v1 都不选 loadout，wire 上 `loadout_id` 可为 null；**后端 null → 用默认 loadout**（`is_default`），一致性提示词按 bundle 的拼接顺序（资产自身 → loadout `prompt_extra` → 链接 costume/prop → location），**不拼 user_text**，`negative` 不进聊天 | 默认造型的提示词与用户想要的造型不一致（v2 加选择） |
| E | `prompt` / `audio` 类型 | **显式分支**：`prompt` 资产无主槽 → `has_image="false"` 且正文=其 body（提示词就是内容）；`audio` 主槽是音频 → `primary_resource_id` 给出、`has_image="false"`、正文=描述+提示词；不折叠成「没有」 | 用户 mention 提示词资产后模型看不到内容 |
| F | 主图投递形状 | 主图对应的 `resources.id` 作为**普通 `<resource …/>` 条目一并渲染**（与 `<asset>` 相邻，`asset` 条目用 `primary_resource_id` 指向它），并加入 ResourceFetch 可访问 id 集合；不新增图片通道、不动 vision 分支 | 模型看到 `<asset>` 却取不到图 |
| G | 资产搜索 | 新端点 `GET /assets/search?q=&type=&limit=`（无 `scope_id`，成员范围 + 预设；行带 `scope_id`）供聊天 `@`；卡片 shape 与 `GET /assets` 列表一致；同一解析器复用 B 的口径 | 一个 team 的资产在聊天里搜不到 |
| H | issue 回复框（`IssueReplyBox.tsx:79`） | **本期不做**，PR 已知里写明 | 又一处「两个入口只接一个」 |
| I | `ensureResourceProcessed` | 资产发送**不**触发（资产是实体不是媒体） | — |

## Global Constraints

- 实施者/审查者一律 opus。UI 英文 Title Case、禁 emoji、语义色 token；i18n 双语 + parity + referenced-keys 守卫。
- 边界 mock 用真实 wire 形状：`assets` router id 全 `str()`；`AttachmentRequest.kind` 是自由字符串但本期新 kind 固定为 `asset_ref`。
- 后端 ORM only；读 `Resources` 必须 `system_request_scope`（`is_enforced("resources")` gate，照 `resource_ref_resolver.py:86-109`）；black/isort/flake8。
- 新 kind 必须在 `ai_library_chat_service.py:646-658` 分流处显式第三桶；`_DISPLAY_ATTACHMENT_KEYS` 扩 `asset_id`；`AIChatBubble` 加 `asset_ref` 分支。
- 提示词测试一律 tokenize 断言，不加第二个全文 pin；prompts README 必须更新「What the model sees / Token effect / KV Cache effect」。
- vitest 禁 `--reporter=basic`；tsc 基线以 fresh 文件测；禁 `git add -A`；本 worktree 的 `frontend/node_modules` 是符号链接，**禁止 `npm ci`/`npm install`**。
- 三个一次性通道互不清空、不持久化（`globalChatStore.test.ts:30-92` 范式）。
- 禁触碰生产容器；不跑 Playwright 以外的真栈。

---

### Task 1: 后端 — 资产引用解析器 + 展开（纯函数）

**Files:** Create `backend/app/services/ai/chat/asset_ref_resolver.py`、`backend/app/services/assets/chat_ref.py`；Test `backend/tests/services/ai/chat/test_asset_ref_resolver.py`、`backend/tests/services/assets/test_chat_ref.py`。

- `resolve_asset_refs(attachments, *, user_id) -> tuple[list[dict], list[AssetRefFailure]]`：只吃 `kind=='asset_ref'` 的 dict（`asset_id` str，`loadout_id` str|None）；按裁决 B 查 `assets`（ORM，`read_scope`；预设 `or_()` 分支照 `assets_repository.py:243-245`）；加载 `asset_files`（按 slot）、默认或指定 loadout、`asset_links` 的 costume/prop/location 邻居（复用 `asset_relations_repository` 的读法）；主图 resource 行读取包 `system_request_scope`（gate `is_enforced("resources")`）判 `has_image`（沿用 `_stamp_image_availability` 的 `_reference_stored_path` 阶梯）。失败按裁决 C 枚举，逐条 `AssetRefFailure(index, reason)`。
- `chat_ref.py`：纯函数 `build_chat_ref(asset_row, loadout_row, linked_assets, files_by_slot) -> ChatAssetRef{asset_id, name, asset_type, scope_id, primary_resource_id|None, has_image, consistency_prompt, loadout_id|None}`：主图 = `reference_order(files_by_slot, asset_type, max_refs=1)[0]`；提示词复用 `bundle._linked_positive_texts` / `dedupe_fragments`（若为私有名，抽成公开 helper 并保留 bundle 测试）；裁决 E 的两条显式分支各有测试。模块 docstring 带 Model Experience 段（照 `bundle.py:64-69`）。
- 守卫测试（裁决 B）：同一 fixture（成员 / 非成员 / 预设 / 已删）下 `resolve_asset_refs` 的可见集合 == `AssetsRepository.get(asset_id, scope_id)` 对用户每个 team 逐一取并集，逐条断言。
- 数字 id fixture：`asset_id` 以 number 形式进来（前端 wire 是 string，但要证明 str 归一）。
- [ ] 提交 `feat(chat): asset ref resolver + chat_ref expansion`

### Task 2: 后端 — 渲染、转义、守卫、README

**Files:** Modify `backend/app/services/ai/prompts/prompt_composer.py:637-720`（`render_available_resources` 接受 asset 条目）、`backend/tests/services/ai/prompts/test_frame_escape_wiring.py:131-142`（`ignore` 加 `asset`）、`backend/app/services/ai/prompts/README.md:71-104`；Test `backend/tests/services/ai/prompts/test_prompt_composer_assets.py`（新）。

- 渲染形状（裁决 A/F）：在资源行之后追加 `  <asset id=… type=… name=… scope=… primary_resource_id=… has_image=… loadout=…>{escape_frame_body(consistency_prompt)}</asset>`；主图 resource 行照常渲染（由 Task 3 把它并进 refs）；尾随说明加一句 asset 用法（"取主图用 ResourceFetch(primary_resource_id, mode=image)"）。空 refs 仍返回空串。
- hostile-value 测试照 `test_frame_escape_wiring.py:31-66`：属性含 `"`/`<`/换行、正文含 `</available_resources>` 与 `</asset>`——前者必须被转义为 `<\/available_resources>`，后者按裁决 A **允许原样**（测试断言这一点，并在注释写明为什么）。
- README：更新 `### <available_resources>` 三问（贴真实字面量含一条 asset；Token effect：每条 asset ≈ 60-200 token，上限由附件数决定、提示词截断到 N 字符（定一个常量并在此说明）；KV Cache：仍在缓存边界之后）；Known Limitations 记「issue 回复框不支持 asset」（裁决 H）。
- [ ] 提交 `feat(prompts): render <asset> entries inside <available_resources> with escaping`

### Task 3: 后端 — chat service 接线 + 类型化失败 + 持久化

**Files:** Modify `backend/app/services/ai/chat/ai_library_chat_service.py:646-720,813-869,1301-1307`、`backend/app/services/ai/chat/conversations_ai_store.py:90`、`backend/app/schemas/ai_library_chat.py:114-154`（`asset_id` / `loadout_id` 字段，`kind` 文档）；Test `backend/tests/services/ai/chat/test_chat_asset_refs.py`（走真实消费方：桩 provider，断言 system message 含 `<asset` 与主图 `<resource`、ResourceFetch 可访问集合含主图 id、`attachment_failures` 形状）。

- 分流：`asset_ref` 第三桶 → `resolve_asset_refs` → 展开的主图并进 `refs`（复用 `resolve_resource_refs` 的 meta 查询取 mime/name，避免第二套渲染）→ `render_available_resources(refs, assets)`；失败进 `attachment_failures`（`kind:'asset_ref'`）。
- `_DISPLAY_ATTACHMENT_KEYS` 加 `asset_id` / `loadout_id`；`display_attachments` 测试更新。
- 端到端契约测试：非流式 `/chat` 与流式 `done` 都带 `attachment_failures`（tokenize 断言）。
- [ ] 提交 `feat(chat): asset_ref attachments — third bucket, typed failures, persisted keys`

### Task 4: 后端 — 成员范围资产搜索端点

**Files:** Modify `backend/app/api/assets_router.py`（`GET /assets/search`，放在 `/{id}` 路由之前）、`backend/app/services/assets/assets_service.py`、`backend/app/repositories/assets_repository.py`；Test `backend/tests/api/test_assets_search_router.py`。

- 参数 `q`（LIKE 转义照 M2）、`type`（可选，`ASSET_TYPES` 之一）、`library`（`in|out|all`，默认 `all`）、`limit`（≤50）；无 `scope_id`；可见集合 = 裁决 B 的解析器同一查询（抽成 repository 方法 `list_accessible(user_id, ...)`，Task 1 的解析器改用它——两处一个真源）。
- 返回卡片 shape 与 `GET /assets` 列表一致 + `scope_id`（str）+ `cover_file_id`；Envelope；id 全 `str()`。
- 测试：成员可见、非成员不可见、预设可见、`type` 过滤、`q` 转义、数字 id 序列化为 str。
- [ ] 提交 `feat(assets): membership-wide GET /assets/search for chat mentions`

### Task 5: 前端 — `pendingAsset` 通道 + Send To Agent 接活

**Files:** Modify `frontend/stores/globalChatStore.ts`（+test）、`frontend/hooks/useComposerResourceAttach.ts`（或新 `useComposerAssetAttach.ts`）、`frontend/components/chat/stagedResources.ts`（`StagedAssetRef` + `toAssetAttachment` 五字段：`kind:'asset_ref'` / `asset_id` / `loadout_id` / `name` / `asset_type`）、`frontend/components/AIChatPanel.tsx`（暂存行渲染 asset chip；`allAttachments` 合并）、`frontend/components/chat/AIChatBubble.tsx:237`（`asset_ref` 分支）、`frontend/components/chat/AttachmentFailureBanner.tsx`（reason 文案 i18n）、`frontend/components/resources/assets/sheet/SheetSidebar.tsx:124-135`（去 disabled）、资产卡右键/菜单入口（`AssetCard` 的动作菜单）；Create `frontend/utils/sendAssetToAgent.ts`；Test 各对应 `.test.tsx`。

- `pendingAsset {assetId, loadoutId:null, name, assetType, coverFileId, scopeId, nonce}`；`sendAssetToChat` / `consumePendingAsset`；不持久化、不清兄弟通道（扩 `globalChatStore.test.ts`）。
- `sendAssetToAgent(asset, {addToast, t})` 共享 helper（裁决 I：不跑 `ensureResourceProcessed`），sheet 侧栏与资产卡菜单都调它（测试各一）。
- composer：读 `pendingAsset` → 暂存行 asset chip（封面缩略 + 类型图标 + 名称，可移除）→ 发送时 `toAssetAttachment` 进 `attachments`；`mergeRefAttachments` 按 `asset_id` 去重。
- 气泡：`asset_ref` 渲染 `AssetRefChip`（新，复用 `ResourceRefChip` 样式）；失败横幅文案覆盖裁决 C 的四个 reason（i18n 双语）。
- [ ] 提交 `feat(chat): pendingAsset channel + Send To Agent from sheet and asset cards`

### Task 6: 前端 — `@` 选择器 Assets 栏 + e2e + PR 草稿

**Files:** Create `frontend/components/assets/AssetGridPicker.tsx`（从 `frontend/features/canvas-core/smart/nodes/PromptMentionPicker.tsx` 抽 Assets 半边：搜索 + 类型 chip + 缩略网格 + `PinLightbox` 预览 + 键盘 handle）；Modify `PromptMentionPicker.tsx`（改用共享组件，行为不变，既有测试不删）、`frontend/components/chat/ResourcePickerSuggestion.tsx`（加 Assets tab，数据源 `searchAssetsAccessible(q, type)` → `GET /assets/search`）、`frontend/components/AIChatPanel.tsx:389-424`、`frontend/services/assetsService.ts`；Test 组件测试 + `frontend/e2e/chat-assets.spec.ts`（mocked，真实 wire 形状：Send To Agent → 暂存 chip → 发送 attachments 含 `asset_ref`；`@` → Assets tab → 选中 → 暂存 chip；失败横幅）；`.superpowers/pr-body.md`（中文：变化 / 已知（裁决 H、v1 无 loadout 选择、prompt/audio 表现）/ 验证）。

- 选中资产 → `stageAsset(item)`（同 Task 5 的暂存行，不插 tiptap 节点），`removeMentionTrigger` 照 `ChatInputResourceMention.ts:57-91`；复用 `MENTION_QUERY_TERMINATORS`。
- 键盘：↑↓/Enter/Esc 与现有 tab 一致；tab 切换保留 query。
- [ ] 提交 `feat(chat): Assets tab in the @ picker via shared AssetGridPicker`

## Self-review

- 覆盖 §6.5 三条：Send To Agent（T5）、resolver + 框（T1-T3）、@ Assets（T6）；spec 「登记 OWNED_FRAMES」按裁决 A 修订并需在 spec 文末追加修订记录（T2 顺带）。
- 类型一致：`asset_ref` kind、`ChatAssetRef` 字段名、`GET /assets/search` 参数在 T1/T3/T4/T5/T6 中一致。
