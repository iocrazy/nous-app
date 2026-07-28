# Chrome 插件 Prompt 分析 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在既有扩展 **MediaHub Push**（`chrome-extension/`，MV3，已有 apiKey 认证 + `importOneImage` 上传管线 + 右键菜单/popup 骨架）上加「网页图片 → 反推提示词」：右键任意图片 → 上传进库 → 调 generate → 页面内浮层面板显示**进度**（轮询）→ **结果卡**（中/EN/JSON tab、copy 跟随 tab、标签已写库提示）→ 「Generate Similar in nous」深链进 web 端同款流程。功能对标偷师截图；UI 风格沿 v6.1 结果卡（ink 暗色）。

**已核实的底盘（零后端改动预期）:**
- 扩展认证：`chrome.storage.local` 的 `apiUrl`/`apiKey`，Bearer 直调（`background.js:34-41` /media/fetch、`:180` /resources/upload、`:225` /resources/ai/batch 均已在用）
- 图片进库：`background.js:157 importOneImage(img, {apiUrl, apiKey, scopeId, folderId})`——页面上下文 fetch 图片字节 → `/api/v1/resources/upload`，返回 resource id（读该函数确认返回结构）
- 反推：`POST /api/v1/resources/{id}/gen-prompt/generate` → `{success, task_id}`（数据线 PR 已带 task_id）
- 进度轮询：`GET /api/v1/tasks/{task_id}/progress`（`task_manager_router.py:467`——读该 handler 确认响应字段与 apiKey scope 放行；若 scope 拦截则在 `core/api_key_scopes.py` 补该路径的只读 scope，这是唯一可能的后端改动）
- 结果回读：`GET /api/v1/resources/{id}`（四列 prompt + gen_prompt_json 全带）
- web 端同款流程：ResourceDetailPage → PromptSection 的 ⚡Generate Similar（modal 选画板 → autoRun）

**分支/工作区:** `feature/extension-prompt-analyze` @ `.worktrees/ext-prompt`（基于 master f8740be）。

## Global Constraints

- 只在本分支/工作区；不切分支不碰主仓库；前端命令从 worktree 的 frontend/ 跑（typecheck 基线 == master，开工实测记录）
- 扩展代码是 vanilla JS（无构建步骤）——保持现状风格（读 background/content/popup 现有写法），不引入打包器
- UI 英文；浮层样式对齐 v6.1 结果卡（ink 色板硬编码 hex 即可，扩展无 tailwind）
- manifest version bump（1.2.3 → 1.3.0）+ README 补用法段
- 每 task 一 commit + trailer `Claude-Session: https://claude.ai/code/session_017MjbKQdeuX9c91L5xdhbns`

---

### Task 1: web 端同款深链（前端小改）

**Files:** Modify `frontend/components/ResourceDetailPage.tsx`、`frontend/components/resources/PromptSection.tsx`(+tests)

- 深链约定：`/resources/file/{id}?generateSimilar=1`——ResourceDetailPage 挂载后（resource ready）读 searchParams，含该 flag 时把一个一次性信号传给 PromptSection（新 optional prop `autoOpenGenerateSimilar?: boolean`）：区块自动展开 + 打开 Generate Similar 的画板选择 modal（若无分析数据则只展开 + toast 提示先 Generate）。消费后清 query（navigate replace，保留其它参数）。
- 防重：StrictMode/重渲染只触发一次（ref guard，参考 CanvasComposer insertedRef 先例）。
- TDD：PromptSection 新 prop 两态测试；ResourceDetailPage 侧集成点走查（若该文件无既有测试则组件层测试覆盖为准）。
- Gates: vitest components/resources/ + typecheck 基线 → Commit `feat(fe): 资源详情支持 generateSimilar 深链 — 扩展一键同款入口`

### Task 2: 扩展 Prompt 分析主体

**Files:** Modify `chrome-extension/manifest.json`（版本、contextMenus 已有）、`chrome-extension/background.js`、`chrome-extension/content.js`；Create `chrome-extension/prompt-panel.js` + `prompt-panel.css`；Modify `chrome-extension/README.md`

- **入口**：background 注册右键菜单项 `contexts:['image']`「Analyze Prompt (nous)」；点击 → 向该 tab 注入/唤起 prompt-panel（`chrome.scripting.executeScript` + insertCSS，MV3 惯例照现有 scan 注入方式）
- **流程**（panel 内驱动，消息走 `chrome.runtime.sendMessage` 到 background 执行网络请求——复用现有消息模式）：
  1. `importOneImage` 复用（图片 URL + 页面 referer 语义照旧）→ resourceId；失败态面板显示错误 + Retry
  2. `POST .../gen-prompt/generate` → task_id
  3. 轮询 `GET /tasks/{task_id}/progress`（2s 间隔，90s 超时兜底——与 web 端 I6 同语义），面板进度条 + subtitle 阶段文案（偷师式「正在分析图片…」布局，文案英文）
  4. 完成 → `GET /resources/{id}` → 结果卡：中/EN/JSON 三 tab（JSON 美化只读）、category/比例 chips（读 gen_prompt_json）、「N tags saved to library」提示行、按钮行 [Copy Prompt（跟随当前 tab）] [⚡ Generate Similar] [Open in nous]
  5. Generate Similar/Open in nous → `chrome.tabs.create` 打开 `${webUrl}/resources/file/{id}?generateSimilar=1` / 不带 query 的详情页。`webUrl` 新增到扩展设置（popup 设置区已有 apiUrl/apiKey 的表单，照样式加一项，默认 `https://app.nous.ink`）
- **面板 chrome**：右上角固定浮层（可关闭、可拖动省略——v1 固定右上即可），z-index 顶层，样式 v6.1 暗色卡；同页重复分析复用同一面板实例
- **失败路径**：上传失败/dispatch 失败/任务 failed/超时四态都有明确文案 + 可重试
- 无自动化测试基建（扩展 vanilla JS 无 harness）——以 README 手测清单代替，逻辑尽量放纯函数（如 progress→UI 状态映射、结果解析）写进 `prompt-panel.js` 顶部并保持可读
- Commit `feat(ext): 网页图片一键反推提示词 — 浮层进度/结果卡/深链同款 (v1.3.0)`

### Task 3: 验证 + 交付

- Task 1 前端 gates 全绿；扩展代码 `node --check` 每个 js 文件语法校验
- 若 Task 2 发现 `/tasks/{id}/progress` 被 apiKey scope 拦截 → 补 scope（后端小改 + 测试）并入本 PR
- README：安装（加载已解压）、配置（apiUrl/apiKey/webUrl）、手测清单（右键分析 → 进度 → 三 tab → copy → 同款深链）
- push + PR（public CI 循环）→ 合并 → 部署（前端链；扩展本身不部署，用户本地加载）→ private
- 交付物说明：扩展目录即成品，用户 chrome://extensions 加载已解压目录（或后续打包 crx/商店，另行）

## Self-Review 记录

- 偷师功能映射：进度提示=T2.3、三格式+copy跟tab=T2.4、一键同款=T1+T2.5、tag 生成=数据线已在服务端做（面板只显示条数）
- 决策：同款不在扩展内直接起画板任务，走深链回 web（画板选择/autoRun 逻辑复用,避免扩展重实现 canvas 协议）；panel 用注入而非 iframe（复用页面上下文 fetch 图片字节的既有能力）
- 唯一可能的后端改动（apiKey scope）已预案
