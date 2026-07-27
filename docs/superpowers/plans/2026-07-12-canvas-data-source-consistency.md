# 画布数据源一致性修复 Implementation Plan（2026-07-12）

> **For agentic workers:** REQUIRED SUB-SKILL — 用 superpowers:subagent-driven-development（或 executing-plans）逐 PR 执行。每个 Task 用 checkbox（`- [ ]`）跟踪。**每项一个小 PR**，独立可合。
>
> _注：本 plan 原定用 Fable 5 起草，Fable 5 撞本月额度上限不可用，由 Opus 依据已核实证据链直接落笔。_

**Goal:** 修掉画布「前端硬编码配置 ↔ 平台真实 DB」的成片脱节——让文本 Prompt 节点和生成模型走同一条 DB 目录路（P0-1），补齐真取消（P1-1）和 Preview 数据管道（P1-2），顺手清注释漂移（P2）。

**背景（canvas-data-source-audit 2026-07-12，用户真机走查确诊）：** 用户发现画布「文本模型下拉」的模型（qwen-plus/claude-sonnet/nous-storyboard）和平台管理面板（DeepSeek/Doubao/Nous Qwen3）对不上。深挖是**数据源脱节**：文本 prompt 下拉硬编码常量、默认值指向平台没配的 `qwen-plus`、还编了个不存在的 `nous/storyboard` slug——默认点 Run 大概率 `ProviderNotConfiguredError`。同库三套模型体系：Smart 文本=硬编码（死）、Smart 生成 image/video=走 DB（活）、Classic=自由文本（活）。

**核心教训（决定测试策略）：** 以前单测把后端 `_get_adapter` 直接 `AsyncMock` 掉、e2e 用 stub 假数据，所以这类脱节**根本测不出**。**本 epic 每个后端 resolve 测试必须真跑 `resolve_db_adapter` 对照 fixture `mediahub_models` 目录，严禁 mock `_get_adapter`/`resolve_db_adapter`。**

## Global Constraints

- **TDD**：先红后绿；目标覆盖 ≥80%。
- **小 PR**：每 Task 一个 PR，`feature/*` 分支 ≤1 天，直接 PR 合 master。
- **配置铁律（2026-07-07）**：AI 凭证/配置全走 DB，新代码用 `resolve_db_adapter(model, module)`，不读 env。
- **route-C task_tracking 纪律**：`phase/status/progress/started_at/completed_at/error_msg` 由 `mirror_dbos_lifecycle_to_tracking` trigger 全权同步——**业务代码禁止 PATCH 这些列**；cancel 必须走 DBOS 引擎让 trigger 反映，不能自己写 phase。业务装饰字段写 `metadata` jsonb。
- **i18n**：UI 文本全英文，`en.json`/`zh.json` 键集全等；零 emoji；双主题（禁 zinc）。
- **前端门禁**：`cd frontend && npx vitest run --no-file-parallelism` 全绿 + `npm run lint`（eslint react-hooks rules-of-hooks=error）。
- **后端门禁**：`cd backend && uv run pytest`；改动 .py 跑 black+isort+flake8。
- **版本**：发 PR 前核 master 版本再 bump `frontend/package.json`（当前 master=0.25.254）。
- **CI billing 坑**：private repo instant-fail；发 PR 前 `gh repo edit iocrazy/nous --visibility public`，合完切回 private。

## 依赖顺序

```
PR-P0-1  文本模型走 DB 目录（最紧急，独立）  ← 先做
PR-P1-1  真取消 cancel 端点               ← 独立，可并行
PR-P1-2  Classic Preview 接数据管道        ← 独立，可并行
PR-P2    注释漂移 + aspect 预设统一（chore）← 最后顺手
```

---

### Task 1（PR-P0-1）: 文本 Prompt 节点走平台 DB 文本模型目录 🔴

**问题定位（file:line 证据）**
- `frontend/features/canvas-core/smart/nodes/PromptNodeView.tsx:28-34` 硬编码 `PROVIDER_OPTIONS`，默认项 `{ slug:'', label:'Default (qwen-plus)' }` + 假 slug `nous/storyboard`。
- 链路：下拉 `provider_slug` → `backend/app/services/canvas/canvas_run_service.py:60-67 _resolve_model`（空→`DEFAULT_MODEL="qwen-plus"`）→ `run_prompt`:238 → `_get_adapter`:209-214 → `resolve_db_adapter(model,"canvas")`（`backend/app/services/ai/providers/ai_provider_helpers.py:331-365`）→ 命中 `mediahub_models.name` 否则 `raise ProviderNotConfiguredError`。
- `qwen-plus` 不在平台 `mediahub_models`（env 凭证已退役）→ 默认点 Run 报错；`nous/storyboard` 是编造 slug → 404。
- repo `list_enabled(type_filter)`（`backend/app/repositories/nous_model_repository.py:139-153`）已支持 `WHERE type == type_filter`——干净接入点。
- governed 默认范式已有：`ai_provider_helpers.py::get_maintenance_model()` 读 `system_settings.maintenance_llm_model`，fallback `DEFAULT_MAINTENANCE_MODEL="mediahub-doubao-seed-2-0-lite"`（真实目录条目）。

**步骤**
- [ ] **RED（后端 resolve 真测）**：`backend/tests/` 新增 `test_canvas_text_model_resolution.py`——fixture 里放一个 enabled `type='llm'` 的 `mediahub_models` 行；断言：(a) 空 provider_slug 解析到该目录默认模型并成功 `resolve_db_adapter`（不 mock）；(b) 目录里不存在的 model → `ProviderNotConfiguredError`。**严禁 mock `_get_adapter`/`resolve_db_adapter`**，走真 resolve 对照 fixture 目录（参考现有 nous_model repo 的 test fixture 装配方式）。先跑，红。
- [ ] **GREEN 后端 · 默认值修正**：加 helper `get_canvas_default_text_model()`（放 `ai_provider_helpers.py` 或 canvas service）：读 `system_settings.canvas_default_text_model` → fallback 首个 enabled llm 行 `name` → 再 fallback `get_maintenance_model()`。改 `canvas_run_service.py` 让空 `provider_slug` 走这个 async 默认（评估 `_resolve_model` 变 async 的改动面：`run_prompt` 已是 async，把 `_resolve_model` 内联/改成 `await _resolve_model_async(provider_slug)`；`run_classic_node` 的 llm 分支同步跟进）。**删除硬编码 `DEFAULT_MODEL="qwen-plus"` 常量的误导性使用**（或改注释指明它只是最末兜底）。
- [ ] **GREEN 后端 · 文本目录读接口**：`canvases_router.py` 加 `GET /api/v1/canvases/text-models`，复用 `get_nous_model_repository().list_enabled('llm')`，只出公开列（复用/仿 `_GENERATION_MODEL_PUBLIC_FIELDS`，**绝不出 api_key/base_url**）。返回 `{success, data}`。加 endpoint 单测（TestClient + fixture 目录，断言只有公开列、只有 llm 行）。
- [ ] **RED/GREEN 前端 · 新 hook + service**：`canvasGenerationService.ts` 加 `listTextModels()`（仿 `listGenerationModels`，打 `/canvases/text-models`）；`smart/nodes/useTextModels.ts`（仿 `useGenerationModels`，模块缓存 + 失败降级空表 + `console.error`）。加 hook 测试。
- [ ] **RED/GREEN 前端 · PromptNodeView 替换硬编码**：删 `PROVIDER_OPTIONS` 常量（含 `nous/storyboard` 假项）；文本模式的 provider `<select>` 用 `useTextModels()` 渲染，value = 目录行 `name`（bare model id），首项 = "Catalog default"（value `''` → 后端选默认）。测试：mock service 返回若干 llm 行，断言下拉渲染的是目录行、默认项为空、选中回写 `provider_slug`。
- [ ] **i18n**：新增文案（如 "Catalog default"）进 `en.json`/`zh.json`，键集全等。
- [ ] **门禁**：`cd frontend && npx vitest run --no-file-parallelism` + `npm run lint`；`cd backend && uv run pytest` + black/isort/flake8（改动 .py）。
- [ ] Commit `fix(canvas): text prompt models come from the platform DB catalog, not hardcoded slugs`

**验收**：打开一个 canvas → 文本 Prompt 节点 → 下拉里的模型 = 平台管理面板里 enabled 的 llm 模型；默认项点 Run 能真跑通（解析到目录里存在的默认模型）；不再出现 `qwen-plus`/`nous/storyboard`。

---

### Task 2（PR-P1-1）: Stop 真取消（DBOS cancel 端点）

**问题定位**
- `frontend/features/canvas-core/services/canvasGenerationService.ts:90-98 PollStopped` 注释「backend task keeps running (no cancel endpoint yet, G4 挂账)」；`pollGeneration` 的 `shouldStop`（:116）只放弃前端轮询，后端 DBOS 任务继续跑完、继续耗 provider 配额。
- dispatch：`canvases_router.py:461 dispatch_canvas_generations` → `start_workflow_routed("canvas_generation", ...)`，每 item 独立 DBOS workflow，`dbos_workflow_id=wf_id`，映射 `task_tracking` 行。
- 已有 DBOS cancel 基础设施：`backend/app/agent_framework/`（`cancel_workflow_subprocesses`、`abort_controller`）。

**步骤**
- [ ] **调研**：确认 DBOS 原生 cancel 调法（`DBOS.cancel_workflow(wf_id)` 或 orchestrator 封装），以及 gateway/worker 拓扑下从 API 进程发 cancel 的正确通道（参考 `reference_gateway_dbos_client.md` 的 enqueue-only 约束——cancel 可能也要走 routed client）。查取消后 trigger 是否把 `task_tracking.phase` 同步成 `cancelled`。
- [ ] **RED**：后端测试——dispatch 一个 canvas_generation 后调 cancel 端点，断言 DBOS cancel 被调用（对 orchestrator 打桩）、且**不直接 PATCH task_tracking.phase**（纪律：phase 由 trigger 同步）。校验鉴权（只有 task owner 能 cancel，仿 `get_canvas_generation` 的 `user_id` gate）。
- [ ] **GREEN 后端**：`canvases_router.py` 加 `DELETE /api/v1/canvases/generations/{task_id}`（或 `POST .../cancel`）：校验 `task_tracking` 行归属当前用户 → 调 DBOS cancel（走 routed client）→ 返回 `{success}`。**不写 phase 列**，让 `mirror_dbos_lifecycle_to_tracking` trigger 反映 cancelled。
- [ ] **GREEN 前端**：`canvasGenerationService.ts` 加 `cancelGeneration(taskId)`；`CanvasComposer.tsx` 的 `onStopRun`（:272）在中止轮询的同时对每个在飞 task 调 `cancelGeneration`（best-effort，失败只 `console.error` 不阻塞 UI）。更新 `PollStopped` 注释（不再是「keeps running」）。
- [ ] 门禁同上。
- [ ] Commit `fix(canvas): Stop actually cancels backend generation tasks (real DBOS cancel endpoint)`

**验收**：点 Stop 后，后端对应 DBOS workflow 进入 cancelled、不再继续调 provider。

---

### Task 3（PR-P1-2）: Classic Preview 节点接入数据管道

**问题定位**
- `frontend/features/canvas-core/classic/nodes/PreviewNodeView.tsx` 注释「MVP...cascade does not pipe real data yet」，永远只显示静态 "Preview" 框。
- `frontend/features/canvas-core/classic/dataPiping.ts:119-152 inputParamKey()` 有 image_gen/video_gen/llm/comfy/text_join case，**唯独 `preview` 无 case → default return null**，连线过去拿不到值。
- preview 节点 handle：`registry.ts:106-107` = `image-in`（image）+ `text-in`（text）。

**步骤**
- [ ] **RED**：`dataPiping.test.ts` 加 case——`inputParamKey('preview','image-in')` / `('preview','text-in')` 应返回目标 data key（如 `preview_image` / `preview_text`），当前返回 null，先红。
- [ ] **GREEN dataPiping**：`inputParamKey` 加 `case 'preview'`：`image-in` → `preview_image`，`text-in` → `preview_text`。核对 cascade 记录/读取 output 的路径（`RecordedOutput`），确保上游 output 能落到 preview 节点 data。
- [ ] **GREEN PreviewNodeView**：读 piped data（`readRunData` 或节点 data 的 `preview_image`/`preview_text`），有图渲染 `<img>`（同源 /cover|/stream URL），有文本渲染文本，否则回落静态 "Preview" 占位。更新注释。
- [ ] 测试：给 preview 节点 data 注入 image/text，断言渲染真内容而非占位。
- [ ] 门禁同上。
- [ ] Commit `feat(canvas): Classic Preview node renders piped upstream image/text`

**验收**：Classic 画布把 image/text 输出连到 Preview → Preview 框显示真内容。

---

### Task 4（PR-P2）: 注释漂移清理 + aspect 预设统一（chore）

**问题定位**
- `frontend/features/canvas-core/classic/runner.ts:9-13` 顶部注释仍写 "MOCK,真实 provider 后续 PR"——早接了 `runner.backend.ts`，`mockRunner` 只在 canvasId 空时兜底（正常路由不可达）。
- `runner.ts:33-35` `preview_text` "future slice" 注释过期（P1-2 落地后更需改）。
- aspect 比例预设两处不一致：`PromptNodeView.tsx:39 RATIO_OPTIONS` 有 4:3/3:4；Timeline 侧（`smart/timeline*`）没有——建议抽成共享常量统一。

**步骤**
- [ ] 修 `runner.ts` 过期注释（说明现役走 `runner.backend.ts`，mock 仅空 canvasId 兜底）。
- [ ] 抽 `RATIO_OPTIONS` 到共享常量（如 `smart/aspectPresets.ts`），PromptNode 与 Timeline 同引用；核对 Timeline 现有行为不回归（纯常量收敛，不改逻辑）。
- [ ] 若 P1-2 已合，同步 `preview_text` 相关注释。
- [ ] 门禁同上（纯注释/常量，测试应零改动全绿）。
- [ ] Commit `chore(canvas): fix stale runner comments + unify aspect ratio presets`

---

## Self-Review / 风险

- **默认文本模型来源（P0-1 关键决策）**：不再硬编码 `qwen-plus`。优先级 `system_settings.canvas_default_text_model` → 首个 enabled llm 行 → `get_maintenance_model()`。这样默认永远指向目录里真实存在的模型，符合 config→DB 铁律。风险：`_resolve_model` 从同步变 async——`run_prompt`/`run_classic_node` 已是 async，改动面可控，但要确保 `test_canvas_run_service.py` 现有用例同步更新（它们 mock `_get_adapter`，本 PR 不删这些既有用例、但**新增**不 mock 的真 resolve 用例作为回归网）。
- **cancel 与 trigger 纪律（P1-1）**：绝不自己 PATCH `task_tracking.phase`——只调 DBOS 引擎 cancel，让 `mirror_dbos_lifecycle_to_tracking` trigger 把 phase 同步成 cancelled。否则重蹈双表不一致覆辙。gateway/worker 拓扑下 cancel 通道需先调研（enqueue-only 约束是否适用）。
- **Preview handle 命名（P1-2）**：已核实 `preview` 节点 handle = `image-in`（image）+ `text-in`（text），data key 建议 `preview_image`/`preview_text`，与 cascade 的 `RecordedOutput` 记录方式对齐。
- **测不出的根因防线**：P0-1 的 acceptance 不能只靠单测——合并前必须真机/preview 打开一个 canvas 核对下拉模型 = 平台面板 enabled llm 列表，且默认项能真跑通（这正是本 epic 被触发的原因）。
- **不做**：合并三套模型体系的数据模型；文本模型加 BYOK（`_get_adapter` 传 `user_provider_config={}` 恒空是既有裁剪，本 epic 不动）；loop/image-input coupling 的既有 deferred。
