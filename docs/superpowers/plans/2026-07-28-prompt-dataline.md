# Prompt 数据线 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** mockup v6.1 §2-4（用户已确认，artifact 44a26470-c032-453a-9ee2-b3668abaf90a）：① 图集逐张提示词 ② 反推升级（中/EN/JSON 三格式 + 实时进度 + 语义标签直写 tags）③ ⚡一键生成同款（画板三连自动 Run）④ UX2 尾巴 R1。

**分支/工作区:** `feature/prompt-dataline` @ `.worktrees/prompt-dataline`（基于 master 27566e8；node_modules/.venv 已符号链接）。migration 号从 **392** 起。

## 侦察确认的事实（直接引用，不必重探）

- **caption workflow**：`backend/app/workflows/caption_asset.py:89-158`，产出写 `gen_prompt/gen_prompt_zh`（repo.update_resource line 140）；vision 调用经 `CaptionService.caption()`（`app/services/ai/caption.py`，模板在服务内）；进度已用 `manager.update_progress(wf_id, pct, subtitle=...)`（20/85/100 三档）；dispatch 入口 `resources_ai_router.py:182 _dispatch_asset_ai`，先建 task_tracking 行（`dbos_workflow_id=wf_id`）
- **前端现状轮询**：`ResourceDetailPage.tsx:513-561 handleGeneratePrompt` 每 3s 轮询 resource 字段变化，30 次超时——本计划将其替换为任务订阅
- **单任务订阅**：无现成 hook；`useTaskManager().tasks: UnifiedTask[]`（`TaskManagerContext.tsx:132`，realtime 维护，含 `progress`/`subtitle`）——`tasks.find(t => t.id === taskId)` 即可，需 dispatch 响应带回 task id（读 generate 端点响应结构确认字段名）
- **AI 标签写入复用**：`backend/app/workflows/classify_asset.py:97-203` 的序列：`get_or_create_group` → `get_tag_by_name`(EN→ZH) → 缺则 `create_tag(name, user_id, name_zh, group_id)` → `add_tag_to_resource(resource_id, tag_id, confidence=0.8, source="ai")`，全程包 `system_request_scope("ai-classify-tags")`（共享词表先例 #608）
- **SlidePlayer**（下载图集）：`GET /media/{id}/slides` → `Slide{name,...}`，当前张 `slides[currentIndex]`，**稳定 key = slide.name**；底部渐变条 lines 253-286，prompt 条另起 sibling
- **GalleryViewer**（上传图集）：`getGalleryItems` → 子项是**真实 resource**（`current.id`）——**逐张提示词直接复用子 resource 的 gen_prompt 列 + ResourcePromptSection，零新存储**
- **画板自动 Run**：`rerunPrompt(promptId)`（`regenerate.ts:74`，从 store 实时读节点 data）；`PromptGenSettings = {kind:'image'|'video', model:string, ratio?:string, count?, aspect?}`（`types.ts:98-108`）；`CanvasComposer.tsx:230-284` 的 promptInsert 消费处在 `setNodes` 前把 `gen` merge 进 `filledPromptNode`，提交后调 `rerunPrompt(id)`（zustand set 同步，getState 可见新节点）
- **PATCH 四层**：migration 加列 → `models/media.py Resources` 加 `Mapped`（mapped columns 即白名单）→ `schemas/resources.py ResourceUpdate` 加字段 → `frontend/types.ts` 同步

## Global Constraints

- 只在本分支/工作区；不切分支不碰主仓库；前端命令从 worktree 的 frontend/ 目录跑（根目录跑 vitest 会丢 jsdom）
- 后端提交前 black/isort/flake8 三关；前端 typecheck 基线 == master（开工时实测记录一次）；UI 英文 + en/zh i18n
- JSON 格式六维范式：`{subject, style, composition, lighting, color, text?, aspect_ratio}`（业界通行，spec 讨论已定）
- 每 task 一 commit + trailer `Claude-Session: https://claude.ai/code/session_017MjbKQdeuX9c91L5xdhbns`

---

### Task 1: 存储四层打通（mig 392 + model + schema + types）

**Files:** Create `supabase/migrations/392_resources_prompt_dataline.sql`；Modify `backend/app/models/media.py`、`backend/app/schemas/resources.py`、`frontend/types.ts`；Test `backend/tests/test_resources_model_prompt_columns.py`（追加断言）

```sql
-- 392: prompt 数据线 — 逐张提示词 + JSON 格式反推结果
ALTER TABLE public.resources
    ADD COLUMN IF NOT EXISTS slide_prompts JSONB,
    ADD COLUMN IF NOT EXISTS gen_prompt_json TEXT;
COMMENT ON COLUMN public.resources.slide_prompts IS
    'Per-slide prompts for download albums, keyed by slide filename: {"<name>": {en, zh, neg_en, neg_zh}}. Upload galleries use each child resource''s own gen_prompt columns instead.';
COMMENT ON COLUMN public.resources.gen_prompt_json IS
    'Structured JSON prompt (subject/style/composition/lighting/color/text/aspect_ratio) produced by the caption workflow.';
```

- model：`slide_prompts: Mapped[Optional[dict]]`（JSONB，照邻近 jsonb 列风格）+ `gen_prompt_json: Mapped[Optional[str]]`（Text）；schema：ResourceUpdate 加两个 Optional 字段（slide_prompts: `Optional[dict]`；gen_prompt_json max_length=20000）；types.ts：`slide_prompts?: Record<string, {en?:string; zh?:string; neg_en?:string; neg_zh?:string}> | null; gen_prompt_json?: string | null`
- 追加 mapper 断言（防 Phase 1 的 ORM 漏映射重演）；本地 psql 可达则执行 392 验列，不可达跳过（CI 兜底）
- TDD → lint 三关 → Commit `feat(db): 逐张提示词 slide_prompts + gen_prompt_json (mig 392, 四层打通)`

### Task 2: caption 升级 — 三格式 + 语义标签 + 阶段进度

**Files:** Modify `backend/app/services/ai/caption.py`、`backend/app/workflows/caption_asset.py`；Test 各自新增/扩展

- `CaptionService`：模板改造为一次调用产出结构化结果（读现有模板与解析方式后扩展）：`{prompt_en, prompt_zh, prompt_json: {六维}, tags: [{en, zh}], category, aspect_ratio}`——JSON 输出用严格指令 + 解析容错（解析失败降级为现状两字段，不 fail 整个任务）
- workflow：写 `gen_prompt, gen_prompt_zh, gen_prompt_json`（json dumps 字符串）；标签写入**逐字复用** classify_asset.py:145-179 的序列（抽成共享函数放 `classify_asset.py` 导出或新 helper 模块，两个 workflow 共用，标 `source="ai"`）；进度档位细化：10 "Resolving provider" → 30 "Analyzing image..." → 70 "Parsing result" → 85 "Saving prompt & tags" → 100（subtitle 英文）
- 测试：解析器单测（合法 JSON / 缺字段容错 / 降级路径）；workflow 测试 mock CaptionService 断言写入字段与标签调用
- lint 三关 → Commit `feat(ai): 反推升级 — 中/EN/JSON 三格式 + 语义标签直写 tags(source=ai) + 阶段进度`

### Task 3: 前端结果卡 + 实时进度 + R1

**Files:** Modify `frontend/components/resources/PromptSection.tsx`(+test)、`ResourcePromptSection.tsx`(+test)、`ResourceDetailPage.tsx`、`ResourcesContext.tsx`、`DownloadsView/useDownloadsData.ts`、locales
- **进度态**：Generate dispatch 响应取 task id（读 generate 端点响应结构 + `generateGenPrompt` 前端服务），存入 state；`useTaskManager().tasks.find(id)` 渲染 mockup §3 的分析中卡（进度条 + subtitle）；任务 completed → 重取 resource 展示结果，failed → toast。**删除 ResourceDetailPage 的 3s 轮询逻辑**（handleGeneratePrompt 的 interval/refs），改共享新流程；Generate 下放到 PromptSection 内部自管（wrapper 与详情页同享），`canGenerate = resource.file_type === 'image'` 在 wrapper 打开
- **结果卡**：有 `gen_prompt_json` 时展开态顶部出现 中文/EN/JSON 三 tab（中文/EN 即现 lang toggle 语义合并；JSON 只读 `<pre>` 展示 + 格式化）；**Copy 跟随当前 tab**（JSON tab 复制 JSON 原文）；负面框逻辑不变
- **R1**：`ResourcesContext.refetchSelectedResourceTags` 与 `useDownloadsData.refetchTags` 补 allTags 同步（对齐 MediaCard.tsx:249-263 的完整实现）
- 测试：进度态渲染（mock useTaskManager）、tab 切换与 copy 内容、R1 两处
- Commit `feat(fe): 反推结果卡(中/EN/JSON+copy跟随tab) + 任务实时进度 + allTags 同步补齐`

### Task 4: 图集逐张提示词

**Files:** Create `frontend/components/SlidePromptStrip.tsx`(+test)；Modify `frontend/components/SlidePlayer.tsx`、`frontend/components/resources/GalleryViewer.tsx`、locales
- **SlidePromptStrip**（下载图集）：props `{resourceId, slideName}`；读父 resource `slide_prompts[slideName]`；浏览条（有内容首行 + Expand/Copy；无内容 "+ Add prompt for this slide"）+ 浮出编辑卡（en/zh × 正/负 双框精简版 + 保存 = PATCH `slide_prompts` 整体 merge 后写回）；样式按 mockup §2（条 = 半透明底 bar；卡 = 图上浮出）
- SlidePlayer：渐变条 sibling 处挂 strip（仅 resourceId 存在时；DownloadDetailPage 传入 resourceId——追一下 SlidePlayer 的调用点把 resourceId 传进去）
- **GalleryViewer**：当前子项挂 `<ResourcePromptSection resourceId={current.id} />` 的紧凑变体——直接复用组件（子项是真 resource）；挂在底部信息区
- 测试：strip 三态、PATCH merge 不覆盖其它张、切张切数据
- Commit `feat(fe): 图集逐张提示词 — 下载 slide_prompts 条+编辑卡;上传图集复用子资源 PromptSection`

### Task 5: ⚡一键生成同款

**Files:** Modify `frontend/components/resources/PromptSection.tsx`、`SendToCanvasModal.tsx`、`features/canvas-core/smart/CanvasComposer.tsx`(+sendToCanvas test)、locales
- 结果卡操作行加 `⚡ Generate Similar`（仅 gen_prompt_json/分析结果存在时）；点击复用 SendToCanvasModal，payload 扩 `{autoRun: true, ratio: <analysis aspect_ratio | undefined>}`
- CanvasComposer promptInsert 消费：payload.autoRun 时在 setNodes 前 merge `gen: {kind:'image', model:'', ratio}` 进 filledPromptNode，提交后 `rerunPrompt(filledPromptNode.id)`（侦察确认的路径）；insertedRef guard 语义保持
- 测试：autoRun payload → gen merge + rerunPrompt 被调（mock regenerate 模块）；非 autoRun 行为不变
- Commit `feat(canvas): 一键生成同款 — Send to Canvas 扩 autoRun,三连节点插入即跑`

### Task 6: 全量验证 + 交付

- 后端相关 pytest + lint 三关；前端全量 vitest + typecheck 基线 + lint
- 终审（opus）→ 修复 → ledger → push → PR（public CI 循环）→ 合并 → Deploy GPU+Pages → readyz + 冒烟 → private

## Self-Review 记录

- mockup §2=T4、§3=T2+T3、§4=T5、R1=T3；四层存储=T1
- 关键简化已确认：上传图集零新存储（子项即 resource）；slide_prompts 仅服务下载图集
- 决策：JSON 解析失败降级不失败任务（反推可用性优先）；Generate 下放组件内自管使四点位统一
