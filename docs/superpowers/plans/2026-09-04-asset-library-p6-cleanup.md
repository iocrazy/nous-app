# 资产库 P6 — 清尾 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 删掉 temp TTL / sweeper 死代码；temp 文件夹以 `system_key='chat_uploads'` 身份改名 "Chat Uploads" 并在 My Uploads 根网格可见；My Uploads 右键新增 As Asset；legacy 表 DROP 另立有门禁的 PR。

**Architecture:** 先纯删除（前端四件 + sweeper + TTL 路由，保留 `temp_ttl_settings.py`）→ 迁移把既有 `name='temp'` 文件夹**收养**为 `system_key='chat_uploads'`（照 mig 441 / `cover_templates_repository` 范式，幂等，不新建行）并把 `chat_upload.py` 与 `backfill_generated_inbox.py` 改按 key 查找 → 删 `ResourceGrid` 的 `temp` 过滤 → 新端点 `POST /resources/{id}/save-as-asset`（服务端一事务：反查/铸 inbox 行 + save-as-asset，铸出的行直接 `in_assets`）→ 右键菜单 + 对话框资源变体。DROP（迁移 + 两个 ORM 类 + workflow + `_BACKFILLS` 项 + 两个测试文件，一个 PR）在**独立分支**，合并前人工 `pg_dump`。

**Tech Stack:** FastAPI + SQLAlchemy async ORM、SQL migration（取号前 fetch）、React 19 + vitest/RTL + Playwright mocked。

**Spec:** `docs/superpowers/specs/2026-08-28-asset-library-loadout-design.md` §3.7 / §3.8 / §9 P6 行。侦察：`.superpowers/p6-recon.md`（行号以它为准）。

## 裁决（控制器 2026-09-04）

| # | 问题 | 裁决 | 错了的代价 |
|---|---|---|---|
| A | EntityAssetStrip | **no-op**（P3 已删），只改 spec §9 那一行 | — |
| B | temp TTL 删除范围 | **方案 B**：删前端四件 + `temp_resource_sweeper.py` + 两个 sweeper 测试 + `temp_ttl_router.py` + `api/__init__.py` 两处注册 + `test_temp_ttl_router.py`；**保留** `temp_ttl_settings.py`（ORM B2 守卫样本 + 通用 settings_json helper）。PR 里明说 ORM B5 覆盖少一个样本 | 多删一层就得动 ORM 迁移守卫 |
| C | temp 文件夹身份 | **迁到 `system_key='chat_uploads'` + `is_system=true` + `name='Chat Uploads'`**（Title Case 按 UI 规范；spec 写 "Chat uploads" 按此修订），迁移幂等（已置位跳过；同 scope 已有该 key 则不再改第二个）；`TEMP_FOLDER_NAME` 退役 → `CHAT_UPLOADS_SYSTEM_KEY` + `CHAT_UPLOADS_DISPLAY_NAME`；查找一律按 key；`backfill_generated_inbox.py:165` 同步。**绝不**改常量了事 | 每 scope 长出第二个文件夹、历史附件成孤儿 |
| D | 可见性 | 迁移落地后删 `ResourceGrid.tsx:319-321` 过滤，行为与封面模板系统文件夹一致（根网格可见、`is_system` 锁 rename/move/trash） | 迁移前删会露出一个还叫 temp 的文件夹 |
| E | My Uploads → As Asset | **新端点 `POST /resources/{id}/save-as-asset`**（body 同 `save-as-asset`）：服务端一个事务内 ① 按 `promoted_resource_id` 反查 inbox 行（`_registered_resource_lookup_stmt`），② 没有则用 import-from-resource 的物化逻辑铸一行且 `review_state='in_assets'`，③ 走既有 save-as-asset 服务；类型化失败（`resource_not_accessible` / `resource_not_image` / `materialize_failed`）。对话框加 `resource` 变体（预填名称/封面来自 resource），取消不留任何行 | 取消对话框留孤儿收件箱行；普通上传文件被折叠成「没有」 |
| F | DROP | **独立分支 `feat/asset-library-drop-legacy`、独立 PR，等用户点头才合**；合并前 `pg_dump -t` 两表到仓库外并在 PR 贴行数；合并后正向探针 `GET /assets/resolve-legacy` 仍映射 + `\dt _legacy*` 为空；`legacy_refs.py` 与其 resolve 测试**不删**，`test_legacy_refs_mirror.py` 改写为只钉自身 | 不可逆数据丢失 |

## Global Constraints

- 实施者/审查者一律 opus。UI 英文 Title Case、禁 emoji、语义色 token；i18n 双语 + parity + referenced-keys 守卫（新 key 在 `resources.*`，别碰 `canvas.asAsset.*`）。
- 后端 ORM only；black/isort/flake8；读 `Resources` 的 workflow / 服务端路径按既有 scope 纪律。
- 迁移取号前 `git fetch` 重新定号；schema-drift 门禁两向零容忍（本 PR 不改表结构，只做 UPDATE 收养）。
- vitest 禁 `--reporter=basic`；tsc 基线 fresh 文件；禁 `git add -A`；`frontend/node_modules` 是符号链接，禁 `npm ci`。
- 触发路径类型化失败回显；「没有 inbox 行」不是「没有」。
- 禁触碰生产容器；不跑真栈。

---

### Task 1: 纯删除 — temp TTL 前端四件 + sweeper + TTL 路由（裁决 B）

**Files:** Delete `frontend/services/tempTtlService.ts`、`frontend/services/tempTtlService.test.ts`、`frontend/components/ChatTempTtlPanel.tsx`、`frontend/components/ChatTempTtlPanel.test.tsx`、`backend/app/workflows/temp_resource_sweeper.py`、`backend/tests/test_temp_resource_sweeper.py`、`backend/tests/test_sweeper_scope_wiring.py`、`backend/app/api/temp_ttl_router.py`、`backend/tests/test_temp_ttl_router.py`；Modify `backend/app/api/__init__.py:86,159`（注销）、`backend/app/workflows/_scheduled_bundle.py:65`（删那行注释）、`backend/tests/test_orm_b5_task2_row_shape_e2e.py:79,204`（sweeper 样本移除，PR 明说）；保留 `temp_ttl_settings.py` 与其测试。

- 全仓库 grep 确认零残留引用（`tempTtl`、`ChatTempTtl`、`temp_resource_sweeper`、`temp_ttl_router`）；`_BACKFILLS` 不涉及。
- [ ] 提交 `chore(cleanup): retire temp TTL panel/service/router and the unscheduled sweeper`

### Task 2: 后端 — temp 文件夹收养为 `system_key='chat_uploads'`（裁决 C）

**Files:** Create `supabase/migrations/<fetch 后取号>_chat_uploads_system_folder.sql`；Modify `backend/app/services/library/chat_upload.py:31`（及三处查找）、`backend/app/workflows/backfill_generated_inbox.py:165`；Test `backend/tests/services/library/test_chat_upload_folder.py`（新）+ 迁移幂等的 integration 用例（`tests/db/`，skip-if-unset）。

- 迁移：对每个 scope 中 `name='temp' AND is_trashed=false AND system_key IS NULL` 的文件夹，若该 scope 尚无 `system_key='chat_uploads'` 行，则 `UPDATE ... SET system_key='chat_uploads', is_system=true, name='Chat Uploads'`（同 scope 多个 temp 只收养 id 最小的一个，其余不动并在迁移注释说明）；再跑一次零变更。
- 代码：`CHAT_UPLOADS_SYSTEM_KEY = "chat_uploads"`、`CHAT_UPLOADS_DISPLAY_NAME = "Chat Uploads"`；查找按 key（照 `cover_templates_repository.py:52/:89/:106`）；不存在时创建带 key 的行（不再按名字找）。
- [ ] 提交 `feat(library): chat uploads folder identified by system_key, adopted from legacy temp`

### Task 3: 前端 — Chat Uploads 在 My Uploads 根网格可见（裁决 D）

**Files:** Modify `frontend/components/ResourceGrid.tsx:289-291,319-321`；Test `ResourceGrid` 测试补「系统文件夹（含 chat_uploads）在根网格可见」断言；`frontend/e2e/resources-folders.spec.ts`（若无则新建，mocked 真实 wire 形状）一条：根网格出现 Chat Uploads 且右键无 Rename/Move。

- [ ] 提交 `feat(resources): Chat Uploads visible under My Uploads`

### Task 4: 后端 — `POST /resources/{id}/save-as-asset`（裁决 E）

**Files:** Modify `backend/app/api/resources_router.py`（新端点）、`backend/app/services/generated/…`（抽 save-as-asset 服务复用）、`backend/app/repositories/generated_media_repository.py:186-200`（暴露反查）；Test `backend/tests/api/test_resources_save_as_asset.py`（新，真实消费方）。

- 语义与失败枚举见裁决 E；铸行复用 `generated_media_router.py:337-400` 的物化逻辑（`resolve_resource_file_path` 阶梯）；同一 `unit_of_work`；重复调用幂等（第二次命中已有 inbox 行）。
- [ ] 提交 `feat(resources): save a library resource as an asset in one transaction`

### Task 5: 前端 — 右键 As Asset + 对话框资源变体 + PR 草稿

**Files:** Modify `frontend/hooks/useContextMenuItems.tsx:164-262`（条目放 Send to Agent 之后）、`frontend/components/assets/SaveAsAssetDialog.tsx`（`items: GeneratedItem[] | { resource: ResourceItem }` 变体）、`frontend/services/assetsService.ts` 或 `resourceService.ts`（`saveResourceAsAsset`）、`ResourcesViewInner`/`ResourcesContext`（对话框状态）；i18n `resources.saveAsAsset`；Test 各对应 + `frontend/e2e/resources-as-asset.spec.ts`（mocked）；`.superpowers/pr-body.md`（变化 / 已知：裁决 A 的 spec 修订、B5 样本、DROP 另立 PR / 验证）。

- [ ] 提交 `feat(resources): As Asset from the My Uploads context menu`

### Task 6（独立分支 `feat/asset-library-drop-legacy`，有门禁，本计划只备好不合）: legacy 表 DROP（裁决 F）

**Files:** Create `supabase/migrations/<取号>_drop_legacy_project_entity_tables.sql`；Modify `backend/app/models/project_library.py`（摘 `ProjectCharacters` :168 / `ProjectLibEntities` :649 两个类 + 改 docstring）、`backend/app/models/__init__.py:170,428`、`backend/app/api/admin/backfill_router.py:24-26,59-65`；Delete `backend/app/workflows/backfill_assets_from_project_entities.py`、`backend/tests/workflows/test_backfill_assets_plan.py`、`backend/tests/db/test_assets_migration_integration.py`；Rewrite `backend/tests/services/assets/test_legacy_refs_mirror.py`（只钉自身）；Modify `backend/tests/db/test_schema_drift.py:83-84` 注释。

- 合并前：人工 `docker exec nous-db pg_dump -U postgres -p 55434 -d postgres -t public._legacy_project_characters -t public._legacy_project_lib_entities > ~/legacy-project-entities-<date>.sql`，PR 贴行数。
- [ ] 提交 `chore(assets): drop legacy project entity tables with their last reader`

## Self-review

- §9 P6 五项：EntityAssetStrip（A，spec 改行）、tempResources/sweeper/TTL（T1）、Chat uploads 可见（T2+T3）、As Asset（T4+T5）、DROP（T6 独立）。
- 类型一致：`CHAT_UPLOADS_SYSTEM_KEY` 在 T2/T3 一致；端点 `POST /resources/{id}/save-as-asset` 在 T4/T5 一致。
