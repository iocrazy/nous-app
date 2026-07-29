# AI Library Agent 页 UX 三项改进 — 设计

日期：2026-07-28 · 分支：`feat/ai-library-agent-ux` · 来源：用户截图反馈（Prop-Visual 横幅看不清 / Analyze 页想看使用方 / 偷师 App 的"去设置"引导范式）

## 目标

1. **横幅配色**：浅色主题下"提供方未配置"横幅文字不可读（dark-only 琥珀色）。经用户确认**一次全修**：全站 64 处 `text-amber-200/300` 类 dark-only 用法全部主题感知化。
2. **使用方面板**：agent 详情页直观展示"哪些模块在使用这个 agent"（静态代码事实 + 动态运行事实）。
3. **未配置引导**：所有"未配置"类提示必须带可点击的修复动作（偷师范式：提示 → 去设置按钮 → 跳转对应设置页）。

## 现状事实

- 主题机制：`[data-theme="light|dark"]` + `--ink-*` 阶梯翻转（`frontend/index.css`）；语义色（amber 等）**有意不翻转**，已有范式是逐类覆盖：`[data-theme="light"] .btn-tint-amber { color:#b45309 }`。
- `agent_runs.trigger` 已记录调用来源，生产实测取值：`chat` / `script_ai` / `issue_reply` / `visual_analysis_l1` / `prompt_caption` / `issue_dispatch` / `chat_summon` / `asset_classify`。
- 设置页 tab 是客户端状态（`useNavigation.settingsTab`，含 `ai`），**无 URL 深链**。
- 后端按 slug 调 agent 的模块：`workflows/{caption_asset,classify_asset,script_*}.py`、`services/ai/translate/`、conversation/issue/routines/canvas 等。

## 方案

### A. 主题感知琥珀色（全站）

1. `frontend/index.css` 新增语义工具类（与 `.btn-tint-*` 同区块）：
   - `.banner-amber`（横幅容器：边框/底色/正文色）+ `[data-theme="light"]` 覆盖（正文 `#92400e`，标题 `#b45309`）
   - `.chip-tint-amber`（状态 chip 场景）同款双主题
2. 逐点替换 64 处 `text-amber-200/300`（约 10+ 个文件）：
   - 普通页面内文字 → 语义类或 `text-amber-300 [data-theme=light]:…` 等价类
   - **例外**：覆盖在图片/视频/深色浮层上的文字（如 `CompactMediaCard` 类覆盖层）**保持原样**——那些底色恒深，改了反而坏。逐处人工判断，不做全局 CSS 盲覆盖。
3. AgentEditor 横幅换 `.banner-amber`；"切换到 xx"按钮换现成 `.btn-tint-amber`。

### B. Agent 使用方面板

1. **后端注册表**（用户确认放后端）：`backend/app/services/ai/agent_usage_registry.py`
   - `AGENT_MODULE_REGISTRY: dict[slug, list[ModuleRef]]`，`ModuleRef = {module_key, feature_key}`。module_key 对齐前端路由段（`resources` / `projects` / `canvas` / `issues` / `parser`…），feature_key 供 i18n 细分（如 `visualAnalysis` / `scriptEditor` / `assetCaption`）。
   - `TRIGGER_MODULE_MAP: dict[trigger, feature_key]` 把 `agent_runs.trigger` 归到同一套 feature_key。
   - 种子一致性测试：注册表里的 slug 必须存在于 `backend/seeds/agents/`；生产已见 trigger 必须有映射（未映射的归 `other` 并容忍）。
2. **API**：`GET /api/v1/ai-library/agents/{slug}/usage`
   ```json
   {
     "modules": [{"module_key": "resources", "feature_key": "visualAnalysis"}],
     "trigger_counts": [{"trigger": "visual_analysis_l1", "feature_key": "visualAnalysis", "count": 8}],
     "conversation_count": 3,
     "routine_count": 1,
     "window_days": 30
   }
   ```
   数据：注册表 + `agent_runs` 近 30 天按 trigger 分组 + `conversation_ai_meta.agent_id` 计数 + routines 绑定计数。权限沿用现有 ai-library GET 的鉴权。
3. **前端**：`AgentDashboardTab` 顶部新增"使用方"卡片：
   - 模块 chips（icon + i18n label），点击跳 `/team/{currentTeamId}/{module_key}`；无 team 上下文时 chips 不带链接
   - 右侧小字动态计数（近 30 天按 feature 聚合 + 会话/例行任务数）
   - 全空时显示空态文案"暂无模块使用此 Agent"（i18n key，英文 UI）

### C. 未配置引导

1. `SettingsPage` 支持 `?tab=<general|api|logs|monitor|tasks|tags|ai|docs>` 深链（读 query 初始化 `settingsTab`，切 tab 时回写 query）。
2. Provider 横幅加第二个按钮 **Go to AI Settings** → `/settings?tab=ai`。
3. 模型下拉 `noModelsAvailable` 空态文案追加同款跳转链接。
4. 立纪律（写入本 spec，不改 CLAUDE.md）：新增"未配置"类提示必须带修复动作按钮。

## 测试

- 后端：usage endpoint 单测（注册表一致性 ×2、计数聚合、未知 slug 404）；pytest 全量回归。
- 前端：`npm run build` + typecheck 过；配色改动靠人工双主题截图核对（本机起 dev server 验证）。

## 不做（YAGNI）

- 不做运行时代码扫描自动发现使用方——注册表 + trigger 双轨已足够且可测。
- 不改 `agent_runs` schema、不加新表。
- 不做全局 CSS 盲覆盖 amber 类（会误伤深色浮层场景）。
