# Script Editor v2 Design Spec

> 对标 Storyboard Copilot v0.2.1，完全重构 Script 编辑器

## 1. Overview

将 MediaHub 的 Script 编辑器从当前的基础 ReactFlow 画布升级为功能完整的剧本创作工具，对标参考应用 Storyboard Copilot v0.2.1 的 UI/UX 和功能集。

### 1.1 Goals

- 竖向流式章节编辑器（约束布局，非自由拖拽）
- TipTap 富文本编辑器，支持剧本专用格式（场景标题、角色对话）
- AI 辅助创作全流程（大纲生成、章节扩写、分支创建）
- 多格式导出（TXT / Word / JSON / Markdown）
- 剧本格式预设系统
- 四种视图模式（网格 / 列表 / 画布 / 缩放控制）

### 1.2 Non-Goals

- 多人实时协作编辑（future scope）
- Storyboard 模块的改动（独立项目）
- 移动端适配

## 2. User Flow

```
项目侧边栏 → Scripts → Scripts 列表页 → New Script 对话框 → 欢迎引导页
                                                              ├─ 路径 A：导入剧本 → 文件上传 → AI 分章 → 画布编辑器
                                                              └─ 路径 B：创建故事 → 故事概要+风格+章节数 → AI 生成大纲 → 画布编辑器
```

## 3. Page Layout

三栏布局：

```
┌──────────────┬──────────────────────────────────────────┐
│  剧本资产     │                                          │
│  (左侧边栏)   │         ReactFlow 画布                    │
│              │                                          │
│  - 剧情摘要   │   ChapterFlowNode (竖向约束排列)           │
│  - 世界观     │   + BranchNode (水平自由布局)              │
│  - 角色档案   │   + StoryRootNode (故事根节点卡片)          │
│  - 场景地点   │                                          │
│  - 关键道具   │                                          │
│  - 埋点追踪   │                          ┌──────────────┐│
│              │                          │⊞ ☰ ⊡ │- 46% +││
└──────────────┴──────────────────────────┴──────────────┘│
```

### 3.1 Left Sidebar — 剧本资产面板

顶部工具栏按钮（参考应用）：
- AI (sparkles) — AI 辅助
- 导出 (download) — 导出剧本
- 复制 (copy) — 复制内容
- 折叠 (panel-left-close) — 折叠侧边栏

资产分组（可折叠）：
- 剧情摘要 — 显示所有章节列表，分支作为子节点缩进，点击跳转到对应章节
- 世界观 — 世界观设定条目，支持增删改
- 角色档案 — 角色信息，支持增删改
- 场景地点 — 场景地点，支持增删改
- 关键道具 — 道具信息，支持增删改
- 埋点追踪 — 剧情埋点，支持增删改

### 3.2 Main Canvas

基于 ReactFlow，使用 dagre 做约束布局：
- 主线章节节点强制竖向排列，固定 x 坐标，y 坐标由 dagre 计算
- 分支/补充节点水平偏移到右侧，可自由拖拽
- 故事根节点（StoryRootNode）定位在左侧，通过曲线连接所有章节
- 章节间有绿色连接点和紫色曲线

### 3.3 View Controls（右下角）

四种视图 + 缩放：
- 网格视图 (grid-2x2) — 章节卡片网格排列
- 列表视图 (list) — 章节标题 + 摘要列表
- 画布视图 (square) — 默认，竖向流式画布
- 缩放控制 — 减号 / 百分比显示 / 加号

## 4. TipTap + ReactFlow Interaction Handling

### 4.1 Focus & Event Management

ReactFlow 拦截键盘事件（delete, backspace）会与 TipTap 编辑冲突。解决方案：
- ChapterFlowNode 设置 `onKeyDown={e => e.stopPropagation()}` 阻止事件冒泡
- ReactFlow `deleteKeyCode` 设为 `null`，禁用键盘删除节点
- 节点删除改为右键菜单或 Delete 按钮
- `Cmd+A` 在 TipTap 焦点内时全选文本，焦点外时全选节点

### 4.2 Scroll & Drag Isolation

- 鼠标在 TipTap 编辑区域内时，滚轮事件用于内容滚动（不缩放画布）
- 鼠标在节点边框/标题栏区域时，允许拖拽节点（仅分支节点）
- 主线章节禁用拖拽（`draggable={false}`）

### 4.3 TipTap Instance Virtualization (性能关键)

20 个章节 = 20 个 TipTap 实例会导致内存爆炸。必须虚拟化：
- 仅当前编辑的章节（`selectedNodeId`）挂载完整 TipTap 编辑器
- 其他章节渲染静态 HTML（`dangerouslySetInnerHTML` + TipTap 相同 CSS）
- 点击其他章节时：销毁当前 TipTap → 当前章节降级为静态 HTML → 目标章节挂载 TipTap
- 切换延迟 < 100ms（TipTap 初始化很快）

### 4.4 dagre Layout Strategy

- 仅在初始加载、章节增删时运行 dagre 布局
- 编辑内容时不触发重排（节点高度自然撑开）
- 初始间距 300px，吸收高度变化
- 提供手动"重新排列"按钮
- StoryRootNode 连接到第 1 章，章节之间用绿色连接点串联（不是根节点连所有章节）

## 5. Node Types

### 4.1 ChapterFlowNode — 章节节点

从上到下的结构：

```
┌─────────────────────────────────────────────┐
│ 📄 第1章 坠入机械之城              (章节标签)  │
├─────────────────────────────────────────────┤
│ [1] 坠入机械之城                  (编号+标题)  │
├─────────────────────────────────────────────┤
│ H1  H2  H3  H4  [正文]  B  I    (格式工具栏) │
├─────────────────────────────────────────────┤
│                                             │
│ (TipTap 富文本编辑区域)                       │
│                                             │
│ 场景1：未知空间 – 黄昏 – 内景     ← 橙色 h2  │
│ 主角林然跌跌撞撞地走出...          ← 白色正文  │
│ ──────────────────────────       ← 分隔线   │
│ 林然：（喘息）这是......           ← 橙色对话  │
│                                             │
├─────────────────────────────────────────────┤
│ 摘要: 主角意外进入机器世界...        ✨        │
│ ┌─────────────────────────────────────────┐ │
│ │            ↓ 创建分支                    │ │
│ └─────────────────────────────────────────┘ │
└─────────────────────────────────────────────┘
                    ●  (绿色连接点)
```

- 章节标签：文件图标 + "第N章 标题"
- 编号：紫色圆形数字标记 + 可编辑标题
- 格式工具栏：H1-H4、正文（高亮）、B、I
- 内容区域：TipTap 编辑器，支持自定义节点
- 摘要行：摘要文本 + AI 扩写按钮（sparkles 图标，tooltip "基于摘要扩写"）
- 创建分支按钮：紫色渐变，点击打开 CreateBranchDialog

### 4.2 BranchNode — 分支/补充节点

结构与 ChapterFlowNode 类似但更紧凑：
- 标签 "补充" + 文件图标
- 编号 + 标题
- 格式工具栏
- TipTap 编辑区域
- 通过紫色曲线连接到源章节

### 4.3 StoryRootNode — 故事根节点

```
┌─────────────────────┐
│ 📖 迷途代码          │
│ 5 章    [科幻]       │
└─────────────────────┘
```

显示故事名称、章节数、风格标签。通过曲线连接到所有主线章节。

## 5. TipTap Rich Text Editor

### 5.1 Dependencies

```
@tiptap/react
@tiptap/starter-kit (Document, Paragraph, Text, Bold, Italic, Heading, HorizontalRule)
```

### 5.2 Custom Extensions

#### SceneHeading Node

渲染为 `<h2>` 标签，编辑器内橙色显示。

```
格式：场景N：场景名 – 时间 – 内/外景
编辑器颜色：#f59e0b (amber)
HTML 输出：<h2>场景1：未知空间 – 黄昏 – 内景</h2>
```

#### Dialogue Node

渲染为 `<p>` 标签，编辑器内整行橙色，角色名粗体。

```
格式：角色名：（动作描述）台词内容
编辑器颜色：#f59e0b (amber), 角色名 bold
HTML 输出：<p><strong>林然</strong>：（喘息）这是......什么地方？</p>
```

#### 颜色映射（编辑器 vs 导出）

| 元素 | 编辑器（暗色） | Word 导出（亮色） |
|------|--------------|-----------------|
| 场景标题 | 橙色 #f59e0b | 蓝色标题样式 |
| 角色名 | 橙色粗体 | 黑色粗体 |
| 对话内容 | 橙色 | 黑色常规 |
| 动作描述 | 白色 #e0e0e0 | 黑色常规 |
| 场景分隔 | 暗色水平线 | 灰色水平线 |

### 5.3 Data Storage

**`content_json` 为 source of truth**，`content` 为派生字段：
- `script_chapters.content_json` (JSONB, 新增) — TipTap ProseMirror JSON 文档结构，**唯一写入源**
- `script_chapters.content` (TEXT) — 纯文本，由后端从 content_json 自动派生（用于全文搜索和 AI 处理）

派生策略：前端只写 `content_json`，后端在 save/sync 时自动从 JSON 提取纯文本写入 `content`。避免双写不一致风险。

AI 扩写返回 HTML 格式，前端解析为 TipTap JSON 后写入 `content_json`。

### 5.4 Auto-Save Strategy

- 编辑器内容变化后 debounce 500ms 自动保存到后端
- 保存时只发送变化的章节（增量 sync）
- 网络断开时本地暂存到 localStorage，恢复后自动同步
- 保存状态指示：editing → saving... → saved

## 6. AI Features

### 6.1 Generate Outline（大纲生成）

增强 CreateStoryDialog：
- 故事概要输入（textarea）
- 章节数量滑块（1-20，默认 5）
- 高级设置（可折叠）：
  - 故事风格下拉：不指定 / 悬疑 / 爱情 / 科幻 / 奇幻 / 历史 / 现代都市 / 喜剧 / 悲剧 / 动作冒险 / 恐怖 / 自定义...
  - 章节数量（红色数字）

生成结果对话框：
- 故事概要回显
- 高级设置回显
- 故事名称（可编辑）+ 重新生成按钮
- 章节大纲列表（编号 + 标题 + 摘要）
- 确认创建大纲按钮

### 6.2 Expand Chapter（章节扩写）

点击章节摘要旁的 sparkles 图标触发：
- 原文：显示章节摘要（只读）
- 扩写要求（可选）：用户自定义输入，如"增加对白、强化冲突"
- 生成结果：实时显示 AI 返回的 HTML 内容
- 确认替换 / 取消

后端 API 变更：`POST /scripts/expand-chapter` 增加 `expansion_request` 参数。

AI prompt 指定输出 HTML 格式：
- `<h2>` 场景标题
- `<p>` 正文段落
- `<strong>` 角色名
- `<hr>` 场景分隔

**AI 输出安全处理**：后端在接收 AI 输出后，用 bleach 做 HTML 白名单过滤（仅允许 h2, h3, p, strong, em, hr, br），过滤掉 script, iframe, style 等危险标签，再返回给前端。

**AI 输出异常处理**：
- LLM 返回空内容 → 提示"生成失败，请重试"，保留原内容不替换
- LLM 返回非 HTML（纯文本） → 自动包裹 `<p>` 标签后注入
- LLM 返回无效 JSON → 后端 try/catch + 返回 400 错误
- AI 服务不可用 → 前端显示"AI 服务暂时不可用"，禁用 AI 按钮但允许手动编辑

### 6.3 Create Branches（创建分支）

点击章节底部"创建分支"按钮：
- Branch Count：2 / 3 / 4（可选）
- Source Chapter 信息显示
- AI Generate Branches 按钮
- Confirm Create 按钮

## 7. Import Script（剧本导入）

### 7.1 Welcome Screen

新建 Script 后显示欢迎引导页：

```
✨ 欢迎使用剧本助手

请选择如何开始您的剧本创作：

┌──────────────────────────────┐
│ ⬆ 导入剧本                    │
│   从 TXT、PDF、Word 文件导入    │
└──────────────────────────────┘
┌──────────────────────────────┐
│ ✨ 创建故事                    │
│   输入故事概要，AI 生成大纲     │
└──────────────────────────────┘

← 返回项目管理页面
```

### 7.2 Import Flow

1. 用户选择文件（支持 .txt / .pdf / .docx）
2. 前端上传到后端 `POST /scripts/import`
3. 后端解析文件内容：
   - TXT：直接读取
   - PDF：PyPDF2 提取文本
   - Word：python-docx 提取文本
4. 后端调用 AI 服务进行智能分章（识别场景、对话、章节边界）
5. 返回结构化章节列表
6. 前端创建 ChapterFlowNode 节点

### 7.3 Backend API

```
POST /api/v1/scripts/import
Content-Type: multipart/form-data

Parameters:
  - file: 上传文件 (.txt / .pdf / .docx, 最大 10MB)
  - script_id: 目标 Script 项目 ID

Response (异步，返回 task_id):
  {
    "task_id": "uuid",
    "status": "pending"
  }

通过 unified_tasks 表轮询进度，完成后获取结果:
GET /api/v1/scripts/import/{task_id}/result
  {
    "chapters": [
      { "title": "...", "summary": "...", "content": "...", "content_html": "..." }
    ]
  }
```

注：导入涉及文件解析 + AI 分章，可能耗时较长，使用异步 task 模式（与 generate-outline 一致）。

### 7.4 File Validation

- 文件大小限制：10MB
- MIME 类型校验：仅允许 text/plain, application/pdf, application/vnd.openxmlformats-officedocument.wordprocessingml.document
- 页数/字数限制：PDF 最大 200 页，总字数最大 50 万字

## 8. Export Script（剧本导出）

### 8.1 Export Flow

1. 点击左上角导出按钮（download 图标）
2. 分支选择对话框：
   - 无分支：显示"将导出完整故事"
   - 有分支：列出可选分支路径
3. 格式选择下拉：
   - 导出为 TXT — 纯文本，无格式
   - 导出为 Word (.docx) — 格式化文档，蓝色标题，粗体角色名
   - 导出为 JSON — 结构化数据
   - 导出为 Markdown — Markdown 格式

### 8.2 Export Format Details

#### TXT 输出
```
迷途代码
类型：科幻

第1章 坠入机械之城

场景1：未知空间 – 黄昏 – 内景
主角林然跌跌撞撞地走出...

林然：（喘息）这是......什么地方？

摘要：主角意外进入机器世界...
```

#### Word (.docx) 输出
- 标题：居中大字
- 类型：居中副标题
- 章节名：蓝色标题样式
- 场景标题：蓝色斜体
- 动作描述：黑色常规
- 角色对话：角色名黑色粗体 + 对话内容常规
- 对话组之间水平线分隔

#### JSON 输出
```json
{
  "name": "迷途代码",
  "genre": "科幻",
  "chapters": [
    {
      "number": 1,
      "title": "坠入机械之城",
      "summary": "...",
      "content_html": "<h2>场景1...</h2><p>...</p>",
      "content_text": "..."
    }
  ]
}
```

#### Markdown 输出
```markdown
# 迷途代码
**类型**：科幻

## 正文

### 第1章 坠入机械之城
...

> 摘要：主角意外进入机器世界...
```

### 8.3 Backend API

```
GET /api/v1/scripts/{script_id}/export?format=txt|docx|json|md&branch_id=<chapter_id>

Parameters (query string):
  - format: "txt" | "docx" | "json" | "md" (required)
  - branch_id: chapter_id (optional, null = 完整故事)

Response: 文件下载 (Content-Disposition: attachment)
```

注：使用 GET 而非 POST，因为导出是读操作，GET 支持浏览器直接下载链接。

## 9. Format Preset System（格式预设）

### 9.1 Preset Configuration

存储在 `script_projects.settings_json` 中：

```json
{
  "format_preset": {
    "scene_heading": "场景N：场景名 – 时间 – 内/外景",
    "dialogue": "角色名：（动作描述）台词内容",
    "scene_separator": "hr",
    "action": "paragraph",
    "voiceover": "italic"
  },
  "genre": "科幻"
}
```

### 9.2 Configuration Timing

- 创建故事时在"高级设置"中初始化（根据风格自动填充默认值）
- 项目 Settings 页可随时修改
- AI 扩写时作为 system prompt 一部分传入，确保输出格式一致

## 10. Database Changes

### 10.1 Migration: script_chapters 新增字段

```sql
ALTER TABLE script_chapters
  ADD COLUMN content_json JSONB;
```

### 10.2 Migration: script_projects 新增字段

```sql
ALTER TABLE script_projects
  ADD COLUMN genre VARCHAR(50);
```

`settings_json` 已存在，format_preset 存入其中，无需新增字段。

## 11. File Structure (New/Modified)

### Frontend (new files)

```
frontend/features/script/
  ├── components/
  │   ├── ChapterFlowNode.tsx          # 章节节点（内嵌 TipTap）
  │   ├── BranchNode.tsx               # 分支/补充节点
  │   ├── StoryRootNode.tsx            # 故事根节点卡片
  │   ├── ScriptTipTapEditor.tsx       # TipTap 编辑器封装
  │   ├── WelcomeScreen.tsx            # 欢迎引导页
  │   ├── ExportDialog.tsx             # 导出对话框
  │   ├── ImportScriptDialog.tsx       # 导入对话框
  │   └── ViewControls.tsx             # 视图切换 + 缩放
  ├── extensions/
  │   ├── SceneHeading.ts              # TipTap 场景标题扩展
  │   └── Dialogue.ts                  # TipTap 对话扩展
  └── views/
      ├── GridView.tsx                 # 网格视图
      └── ListView.tsx                 # 列表视图
```

### Frontend (modified files)

```
frontend/features/script/
  ├── ScriptCanvas.tsx                 # 改造为约束布局
  ├── ScriptAssetsSidebar.tsx          # 样式对齐 + 章节导航
  ├── CreateStoryDialog.tsx            # 增加风格选择 + 高级设置
  ├── ExpandChapterDialog.tsx          # 增加扩写要求输入 + 结果预览
  └── CreateBranchDialog.tsx           # UI 对齐
frontend/stores/scriptCanvasStore.ts   # 增加视图模式状态
frontend/services/scriptService.ts     # 增加导入/导出 API
```

### Backend (new files)

```
backend/app/api/script_export_router.py   # 导出 API
backend/app/api/script_import_router.py   # 导入 API
backend/app/services/script_export_service.py  # 导出格式转换
backend/app/services/script_import_service.py  # 文件解析 + AI 分章
```

### Backend (modified files)

```
backend/app/services/script_ai_service.py   # prompt 增强（HTML 输出 + 格式预设）
backend/app/schemas/script_schemas.py       # 增加导入/导出 schema
```

### Database

```
supabase/migrations/XXX_script_editor_v2.sql  # content_json + genre 字段
```

## 12. Dependencies (new)

### Frontend

```
@tiptap/react
@tiptap/starter-kit
@tiptap/extension-placeholder
```

### Backend

```
python-docx    # Word 文件解析和生成
pypdf          # PDF 文件解析（PyPDF2 已停止维护，改用 pypdf）
bleach         # HTML 白名单过滤（AI 输出安全）
```

## 14. Interaction States Matrix

| 功能 | Loading | Empty | Error | Success | Partial |
|------|---------|-------|-------|---------|---------|
| AI 大纲生成 | Spinner + "正在生成大纲..." | N/A | "生成失败，请重试" + 重试按钮 | 大纲列表 + 确认按钮 | N/A（原子操作） |
| AI 章节扩写 | Streaming 打字机效果 | N/A | "扩写失败" + 保留原内容 | 预览结果 + 确认替换 | 中断时显示已生成部分 |
| AI 创建分支 | Spinner + "正在生成分支..." | N/A | "分支生成失败" | 分支节点出现在画布 | N/A |
| 文件导入 | 进度条 + "正在解析文件..." | N/A | "文件格式不支持"/"文件过大" | 章节列表预览 + 确认导入 | 解析部分成功时显示已解析章节 |
| 文件导出 | Spinner + "正在生成文件..." | N/A | "导出失败" | 浏览器自动下载 | N/A |
| 画布加载 | 骨架屏（节点占位） | WelcomeScreen | "加载失败" + 重试 | 完整画布 | 部分章节加载失败时标记 |
| 章节编辑 | N/A | "开始编写剧本内容..." placeholder | 保存失败 → Toast + 本地暂存 | "saved" 状态指示 | 自动保存中 → "saving..." |
| 资产面板 | 骨架屏 | "暂无XX" + 添加按钮 | "加载失败" | 资产列表 | N/A |

## 15. Review Findings (已整合)

本 spec 经过三轮 review（CEO / Engineering / Design），以下发现已整合到各章节：

- [x] content_json 定为 source of truth (Section 6.3)
- [x] PyPDF2 → pypdf (Section 13)
- [x] 导出 API 改 GET (Section 9.3)
- [x] 导入 API 改异步 (Section 8.3)
- [x] TipTap 实例虚拟化 (Section 4.3)
- [x] AI 输出 HTML 白名单过滤 (Section 7.2)
- [x] 自动保存策略 (Section 6.4)
- [x] TipTap-ReactFlow 焦点/事件处理 (Section 4.1-4.2)
- [x] 交互状态矩阵 (Section 14)
- [x] 文件上传限制 (Section 8.4)
