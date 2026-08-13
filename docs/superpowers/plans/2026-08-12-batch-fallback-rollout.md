# 批量 fallback 推广 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** caption / classify / translate 三路接 `build_fallback_llm` 链 + `test_fallback_wiring.py` batch 分支测试补课。

**Architecture:** 逐服务照 summarize/visual 既有样板（spec §0/§1 已给全部 file:line 锚点）：resolve 直调 `resolve_task_ai_config` 带出 `fallback_models` → service 用 `build_fallback_llm` 建链 → LLM 异常真上抛 → step `max_attempts=1`（translate 无 step，router 放行类型化异常）。

**Tech Stack:** FastAPI + DBOS + pytest；黑格式化。

**Spec:** `docs/superpowers/specs/2026-08-12-batch-fallback-rollout-design.md`（改动清单与全部锚点在 §1，样板原文位置在 §0；实施者以 spec 为需求单源）。

## Global Constraints

- Worktree：`/media/heygo/program/projects-code/repos/nous-app/.worktrees/feat-batch-fallback-rollout`，分支 `feat/batch-fallback-rollout`。
- 严格 TDD；后端测试 `cd backend && uv run pytest ...`。
- **样板照抄纪律**：接线代码以 `summarize_service.py:173-189`（service 侧）与 `analyze_l1.py:49-75`（resolve step 侧）为准；集成测试以 `test_summarize_fallback.py:166-253` 为准；regex 钉子以 `:391-400` 为准。不自创新形状。
- `module` 门禁键：caption→`"caption"`、classify→`"classification"`、translate→`"translation"`。
- caption 的 `record_ai_error_code` 两处已有（`caption_asset.py:245`、`caption_slide.py:153`），**不许重复加**；classify 要新增（照 `ai_summary.py:365-374`）。
- 每 task 单独 commit，message 尾注 `Claude-Session: https://claude.ai/code/session_01PsmzBfabDy1JdoS82w26D9`。
- 改完跑 `uv run black app/ tests/ 后只 add 本分支触碰的文件`（black 会顺带重排存量文件，别夹带）。

---

### Task 1: `test_fallback_wiring.py` batch 分支测试补课

**Files:** Test: `backend/tests/test_fallback_wiring.py`（现仅 74 行 4 测试，全 chat 风格）
**Interfaces:** Produces: 钉死 `build_fallback_llm` batch 分支契约，后续 task 的接线依赖此契约。

- [ ] Step 1: 按 spec §1-F4.3 的五个断言面写测试（研读 `fallback_wiring.py:65-196` 真实实现后写，模仿该文件现有 4 个测试的 patch 风格）：
  1. `provider_key="doubao"` → `get_adapter_for_user` 收到 `{"doubao": {"api_key","base_url","app_id"}}` 包裹形状；
  2. `provider_key=""` + 模型前缀可派生（如 `doubao-x`）→ 用派生 key；
  3. `provider_key=""` + 前缀不可派生 → `_flat_degrade`（返回 `OpenAICompatibleAdapter`，`api_url`/`api_key` 取 flat config，无 app_id）；
  4. `get_adapter_for_user` raise ValueError → `_flat_degrade`；
  5. `module="caption"` 透传到 `resolve_mediahub_model` 调用。
- [ ] Step 2: 跑 `uv run pytest tests/test_fallback_wiring.py -v` 全绿（新测试对现有实现应直接绿——这是补课不是改行为；若发现实现与预期不符，停下报告，不改实现）。
- [ ] Step 3: commit `test(ai): fallback_wiring batch 分支直接测试 — 补 #1810 遗留缺口`。

### Task 2: caption 接链

**Files:** Modify: `backend/app/workflows/caption_asset.py`（resolve step :46 / call_caption step :66-67 / 透传）、`backend/app/services/ai/caption/caption_service.py`（删 :280 `_build_adapter`，:339-346 改链，异常口径 :364-368/:404-410）；Test: 新 `backend/tests/test_caption_fallback.py` + 既有 `test_caption_error_code.py` 等保持绿。
**Interfaces:** Consumes: Task 1 钉住的链契约。Produces: `CaptionService` 构造/调用签名带 `fallback_models`（供 caption_slide 复用核对）。

- [ ] Step 1（RED）: 写 `test_caption_fallback.py`：fake-adapter 真链 429→fallback 直通测（照 §2b 结构）+ `call_caption` step `max_attempts=1` regex 钉子。跑出 FAIL。
- [ ] Step 2: 实现（照 spec §1-F1 清单与样板锚点；`caption_slide.py:126-130` 调用点核对传参完整）。
- [ ] Step 3（GREEN）: `uv run pytest tests/test_caption_fallback.py tests/test_caption_error_code.py tests/test_caption_classify_materialize.py tests/test_slide_prompt_caption.py tests/test_caption_source_gate.py -v` 全绿；既有测试如因异常口径变化需最小适配，适配理由写报告。
- [ ] Step 4: commit `feat(ai): caption 接 fallback 链 — resolve 绕元组 shim,LLM 异常上抛,重试收敛`。

### Task 3: classify 接链

**Files:** Modify: `backend/app/workflows/classify_asset.py`（resolve :46 / step :67-68 / **尾部新增 record_ai_error_code**）、`backend/app/services/ai/classify/classify_service.py`；Test: 新 `backend/tests/test_classify_fallback.py` + 既有 `test_classification_service.py` 保持绿。
**Interfaces:** Consumes: Task 1 契约。与 Task 2 同构（module 键不同：`"classification"`）。

- [ ] Step 1（RED）: `test_classify_fallback.py`：真链直通测 + `call_classify` regex 钉子 + **workflow 尾 record_ai_error_code 被调的测试**（照 `test_caption_error_code.py` 既有写法）。
- [ ] Step 2: 实现（spec §1-F2）。
- [ ] Step 3（GREEN）: 新旧测试全绿。
- [ ] Step 4: commit `feat(ai): classify 接 fallback 链 + workflow 尾补 record_ai_error_code`。

### Task 4: translate 接链（同步 API）

**Files:** Modify: `backend/app/services/ai/translate/translate_service.py`、`backend/app/api/resources_ai_router.py:73-127`；Test: 新 `backend/tests/test_translate_fallback.py` + `test_gen_prompt_translate_plan.py` 保持绿。
**Interfaces:** Consumes: Task 1 契约。Produces: router 放行 `AllModelsFailed`/`LLMCallError` 至全局类型化错误面。

- [ ] Step 1（RED）: `test_translate_fallback.py`：真链直通测 + router 层测试（`AllModelsFailed` 不被 catch-all 吞成裸 500——按仓库既有 router 测试基建写，断言类型化异常穿透或对应 503 码）。
- [ ] Step 2: 实现（spec §1-F3；provider 解析直调 `resolve_task_ai_config(task_key="translation")` 带出 fallback_models）。
- [ ] Step 3（GREEN）: 新旧测试全绿。
- [ ] Step 4: commit `feat(ai): translate 接 fallback 链 — 同步 API 放行类型化 provider 异常`。

### Task 5: 全量回归（纯验证，无 commit）

- [ ] `uv run pytest --collect-only -q` 无 import 错。
- [ ] `uv run pytest tests/test_fallback_wiring.py tests/test_caption_fallback.py tests/test_classify_fallback.py tests/test_translate_fallback.py tests/test_summarize_fallback.py tests/test_visual_analyze_fallback.py tests/test_caption_error_code.py tests/test_caption_classify_materialize.py tests/test_classification_service.py tests/test_gen_prompt_translate_plan.py tests/test_model_assignment_honored.py -v` 全绿。
- [ ] `uv run black --check app/ tests/` 触碰文件干净。
- [ ] `git status --porcelain` 干净；commit 数 = spec+plan+4 task。
