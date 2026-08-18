# 素材→agent 链路升级 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 spec 的 F1-F4：附上未处理素材自动补处理、@ 选择器缩略图/状态、右键 Send to Agent、素材芯片升级。

**Architecture:** 后端只做"状态透传"（resolver/prompt/ResourceFetch 文案/search 响应），触发权在前端；前端沿 globalChatStore 既有一次性通道范式加第三条，任务进度用 optional 化的 TaskManager。零 migration。

**Tech Stack:** FastAPI + SQLAlchemy（后端）；React 19 + zustand + tiptap + vitest（前端）。

**Spec:** `docs/superpowers/specs/2026-08-17-asset-to-agent-pipeline-design.md`（需求单源，含决策与验收）。
**侦察事实**：分支根 `RECON.md`（21 条带锚点，实施前必读，不 commit）。

## Global Constraints

- Worktree：`.worktrees/feat-asset-to-agent-pipeline`，分支 `feat/asset-to-agent-pipeline`。
- 严格 TDD；后端 `cd backend && uv run pytest ...`；前端 `cd frontend && npx vitest run ...`。
- UI 文案英文、lucide 图标、语义色 token（芯片的 indigo 必须换掉）；i18n en/zh 双份。
- search 响应**不得**出现凭据类字段（file_path/api_key 等），配 tripwire 测试。
- **不让 agent 触发处理**（spec §2 明确范围外）。
- `uv run black` 后只 add 本分支触碰的文件；每 task 单独 commit，尾注
  `Claude-Session: https://claude.ai/code/session_01PsmzBfabDy1JdoS82w26D9`。

---

### Task 1: 后端状态透传（spec §1-F1 后端半边 + F2 后端）

**Files:** Modify: `backend/app/services/ai/chat/resource_ref_resolver.py`（meta 增补两状态列）、`backend/app/services/ai/prompts/prompt_composer.py:594-636`（available_resources 渲染带状态）、`backend/app/services/ai/tools/resource_fetch_tool.py:199-235`（video/audio 空内容时读 resources 状态列区分 processing 文案）、`backend/app/api/resources_search_router.py` + `backend/app/repositories/resources_repository.py:2615-2745`（SELECT 增列 + thumbnail_url/状态字段）。
**Interfaces:** Produces: `ResourceSearchResult` 新字段 `thumbnail_url`(相对路径 `/api/v1/resources/{id}/cover` 或 null，判定照 RECON 的 buildThumbnailSrc 范式) / `transcript_status` / `summary_status`；resolver meta 新键同名。
- [ ] RED：resolver 状态测试、available_resources 渲染测试、ResourceFetch processing 文案测试（区分 processing vs none/failed）、search 响应新字段测试 + 凭据 tripwire。
- [ ] 实现 → GREEN → 既有相关测试保持绿 → commit。

### Task 1b: in-flight 状态从 task_tracking 派生（T2 审查 Critical 修复，后端）

**背景**：`resources.transcript_status/summary_status` 生产写点只有 completed/failed，中间态 0 行——T1 三态分支与前端 processing 徽标按列现状永不点亮（task-2-review.md Critical）。
**拍板**：方案 (b)——不往列写中间态（避免第二个会说谎的状态持有者）；后端读路径计算有效状态：终态列优先，否则 task_tracking 在飞（queued→pending / in_progress→processing，转录链含 extract_audio），否则 none。wire 契约不变，前端无感。
**Files:** 共享 helper + `resource_ref_resolver` / `resource_fetch_tool` / `resources_search_router` 三处消费；触发端点 dedup 纳入 extract_audio（T2 审查 I1，修 500）。
- [ ] RED：task_tracking 在飞→pending/processing；终态列优先；无行→none；extract_audio 在飞时触发转录回 200。
- [ ] 实现 → GREEN → commit。详见 task-1b-brief.md。

### Task 2: 前端基础设施（F1 前端触发 + 通道）

**Files:** Create: `frontend/utils/ensureResourceProcessed.ts`、`frontend/hooks/useOptionalTaskManager.ts`；Modify: `frontend/services/aiService.ts:58-71/159-172`（返回类型对齐真实响应体，RECON#4）、`frontend/stores/globalChatStore.ts`（第三通道 pendingResource，照 PendingQuote 形状：nonce + sendResourceToChat(open:true) + consumePendingResource）、`frontend/types.ts`（ResourceSearchResult 新字段）。
**Interfaces:** Produces: `ensureResourceProcessed(r): Promise<EnsureResourceProcessedResult>`——**对象返回**：`{action: 'triggered_transcribe'|'triggered_summary'|'ready'|'skipped'|'failed', attempted?, error?}`（video/audio：无转录→触发转录；转录完无摘要→触发摘要；其余 skip；触发 reject 返回 `action:'failed'` 的类型化结果并 console.error，**不抛**——触发失败不能阻塞聊天，但消费方必须给用户可见回显。权威契约见 task-2-status.md §1，**读 `.action`，别把返回值当字符串比**）；`useOptionalTaskManager(): TaskManagerContextType | null`（无 Provider 返 null 不 throw，照 useOptionalToast 形状——先读 TaskManagerContext 确认 Context 是否导出，未导出则导出之）；store 三通道互不干扰。
- [ ] RED：helper 四分支 + 触发失败不抛；optional hook 无 Provider 不 throw；store 通道测试（照既有 store 测试写法）。
- [ ] 实现 → GREEN → commit。

### Task 3: 前端 UI（F2 前端 + F4 芯片）

**Files:** Modify: `frontend/components/chat/ResourcePickerSuggestion.tsx`（行加 `<img>` 缩略图回退图标、状态徽标、容器 max-h+滚动）、`frontend/hooks/useResourceSearch.ts:35`（limit 20→50）、`frontend/components/chat/ResourceChipNode.tsx`（attrs 增 thumbnailUrl/初始状态快照；View 加缩略图、indigo→语义色、processing 状态点）、`frontend/components/chat/ChatInputResourceMention.ts`（insertResourceRef 透传新 attrs）。
**Interfaces:** Consumes: Task 1 的 search 新字段、Task 2 的类型。Produces: `insertResourceRef(item)` 接受含 `thumbnail_url`/状态的 item（向后兼容缺省）。
- [ ] RED：picker 行含 img（有 thumbnail_url 时）/回退图标（无时）/状态徽标/容器滚动类；芯片含缩略图与语义色、processing 渲染状态点、旧 indigo 类不再出现。
- [ ] 实现 → GREEN → 既有 picker/chip 相关测试保持绿 → commit。

### Task 4: 右键发送 + 消费接线（F3 + F1 入口接线）

**Files:** Modify: `frontend/hooks/useContextMenuItems.tsx`（file 分支加 Send to Agent，lucide Bot，onClick = ensureResourceProcessed + sendResourceToChat）、`frontend/components/AIChatPanel.tsx`（消费 pendingResource：照 pendingQuote 的 rAF 30 帧范式插入 insertResourceRef；selectedAgentSlug 为空先设 'analyze'；@ 选中路径 handleMentionSelect 也调 ensureResourceProcessed）、i18n en/zh 加菜单项文案 key。
**Interfaces:** Consumes: Task 2 的通道与 helper（契约见 task-2-status.md，读 `.action`）、Task 3 的 insertResourceRef 新签名。
**T2 审查移交（task-2-review.md）**：① `action:'failed'` 必须有用户可见回显（toast，CLAUDE.md「触发路径必须类型化失败回显」）；② helper 需把响应体 `message`/`points_charged` 透传出来（T2 审查 I2——区分「已在处理中」与「新排队扣分」两种 toast）；③ **spec F1 的「转录完成后自动补摘要」在此认领**：用 TaskManager（useOptionalTaskManager）监听本会话触发的转录任务完成→调 triggerSummaryByResource（与既有前端 transcribe→summarize 链同族，RECON#19 范式）。
- [ ] RED：菜单项存在且 onClick 触发两个调用；AIChatPanel 消费测试（有 pendingResource 时插入芯片并 consume；无 agent 时落到 analyze）；@ 选中触发 ensureResourceProcessed；failed 回显 toast；转录完成→自动触发摘要。
- [ ] 实现 → GREEN → commit。

### Task 5: 全量回归（纯验证，无 commit）

- [ ] 后端：`uv run pytest --collect-only -q` 无 import 错；本立项测试文件 + `pytest -k "resource_fetch or resource_ref or resources_search"` 全绿。
- [ ] 前端：`npx vitest run` 全量绿；`npm run build` 过。
- [ ] `git status` 干净（RECON.md 除外）；commit 数 = spec+plan+4 task。
