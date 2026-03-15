# 分镜工作台 (Storyboard Workbench) - 设计规格

> MediaHub 分镜管理子模块设计文档
> 日期: 2026-03-16
> 分支: feature/storyboard

---

## 1. 概述

### 1.1 目标

在 MediaHub 中新增「分镜工作台」一级模块，提供基于节点画布的 AI 分镜创建、编辑、管理和导出能力。该模块作为独立功能运行，后续可挂接到飞书工作流作为某个节点的模块实例。

### 1.2 参考项目

| 项目 | 借鉴点 |
|------|--------|
| **Storyboard-Copilot** | @xyflow/react 节点画布、图片池去重、PNG 元数据嵌入、预览图分离、防抖持久化、Konva 标注工具 |
| **Lovart** | 角色一致性系统（角色卡 + 视觉特征锁定）、对话式 AI 交互（Chat Panel）、@角色引用 |
| **TapNow / 麻衣画布** | 脚本自动拆分镜、镜头元数据（景别/机位/运动/焦段）、Prompt 反推、智能场景检测（像素差分）、批量生成并行聚合 |
| **Boords** | 镜头运动标注（Pan/Zoom/Tilt 叠加层）、Animatic 预览播放 |

### 1.3 技术决策

- **不使用 LangFlow** — 分镜模块是 CRUD + 可视化编辑功能，不需要 LLM 编排框架
- **画布库**: @xyflow/react v12（成熟、社区活跃、可复用 Storyboard-Copilot 组件）
- **数据存储**: 结构化存储（PostgreSQL），支持细粒度权限和多人协作
- **图片存储**: 独立 NAS 目录，按需与媒体库关联
- **AI Provider**: 复用 MediaHub 的 Celery + 积分体系，接入 Storyboard-Copilot 和麻衣画布的 provider
- **任务状态推送**: Supabase Realtime (WebSocket)，不轮询

---

## 2. 系统架构

```
┌─────────────────────────────────────────────────────┐
│                   MediaHub Web App                   │
│  ┌───────────┐  ┌────────────────────────────────┐  │
│  │  侧边栏    │  │       分镜工作台                 │  │
│  │           │  │                                  │  │
│  │ 链接解析   │  │  项目列表页 ←→ 画布编辑器        │  │
│  │ 资源库    │  │                                  │  │
│  │ 项目      │  └──────────┬─────────────────────┘  │
│  │ 待办事项   │             │                        │
│  │ 分镜工作台 │  ┌──────────▼─────────────────────┐  │
│  │ 分享管理   │  │      Frontend (React 19)        │  │
│  │ 积分      │  │  @xyflow/react + Zustand        │  │
│  └───────────┘  │  + Konva (标注) + Chat Panel     │  │
│                 └──────────┬─────────────────────┘  │
└────────────────────────────┼────────────────────────┘
                             │ REST API
┌────────────────────────────▼────────────────────────┐
│                FastAPI Backend                        │
│  ┌──────────────────────────────────────────────┐   │
│  │  storyboard_router.py                         │   │
│  │  ├── /storyboard/projects     (项目 CRUD)     │   │
│  │  ├── /storyboard/nodes        (节点 CRUD)     │   │
│  │  ├── /storyboard/edges        (连线 CRUD)     │   │
│  │  ├── /storyboard/frames       (帧 CRUD)       │   │
│  │  ├── /storyboard/characters   (角色卡 CRUD)   │   │
│  │  ├── /storyboard/generate     (AI 生图/视频)  │   │
│  │  ├── /storyboard/split-script (脚本拆分镜)    │   │
│  │  └── /storyboard/export       (导出)          │   │
│  └──────────────────────────────────────────────┘   │
│  ┌─────────────┐  ┌─────────────┐  ┌────────────┐  │
│  │ AI Service  │  │ Image Svc   │  │ Export Svc  │  │
│  │ (多 provider)│  │ (拆分/合并)  │  │ (PDF/PNG)  │  │
│  └──────┬──────┘  └──────┬──────┘  └─────┬──────┘  │
│         │ Celery          │                │         │
│  ┌──────▼──────────────────▼────────────────▼─────┐ │
│  │              Celery Workers                     │ │
│  └─────────────────────────────────────────────────┘ │
└────────────────────────────┬────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────┐
│              Supabase (PostgreSQL)                    │
│  + Supabase Realtime (WebSocket 推送)                │
└─────────────────────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────┐
│              文件存储 (NAS)                           │
│  teams/{team_id}/storyboard/{project_id}/            │
└─────────────────────────────────────────────────────┘
```

---

## 3. 数据库 Schema

### 3.1 分镜项目

```sql
CREATE TABLE storyboard_projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id UUID NOT NULL REFERENCES teams(id),
    created_by UUID NOT NULL REFERENCES auth.users(id),
    name VARCHAR(200) NOT NULL,
    description TEXT,
    cover_image_url TEXT,
    viewport_json JSONB,
    settings_json JSONB,
    status VARCHAR(20) DEFAULT 'active'
        CHECK (status IN ('active', 'archived', 'deleted')),
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);
```

### 3.2 角色卡

```sql
CREATE TABLE storyboard_characters (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES storyboard_projects(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    reference_image_url TEXT,
    thumbnail_url TEXT,
    visual_traits JSONB,
    sort_order INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);
```

### 3.3 画布节点

```sql
CREATE TABLE storyboard_nodes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES storyboard_projects(id) ON DELETE CASCADE,
    node_type VARCHAR(30) NOT NULL
        CHECK (node_type IN (
            'upload', 'image_edit', 'storyboard_split',
            'storyboard_gen', 'text_annotation', 'group',
            'export', 'image_to_video'
        )),
    position_x FLOAT NOT NULL DEFAULT 0,
    position_y FLOAT NOT NULL DEFAULT 0,
    width FLOAT,
    height FLOAT,
    data_json JSONB NOT NULL DEFAULT '{}',
    sort_order INT DEFAULT 0,
    locked BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);
```

### 3.4 画布连线

```sql
CREATE TABLE storyboard_edges (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES storyboard_projects(id) ON DELETE CASCADE,
    source_node_id UUID NOT NULL REFERENCES storyboard_nodes(id) ON DELETE CASCADE,
    target_node_id UUID NOT NULL REFERENCES storyboard_nodes(id) ON DELETE CASCADE,
    source_handle VARCHAR(50),
    target_handle VARCHAR(50),
    edge_type VARCHAR(20) DEFAULT 'default',
    created_at TIMESTAMPTZ DEFAULT now()
);
```

### 3.5 分镜帧

```sql
CREATE TABLE storyboard_frames (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    node_id UUID NOT NULL REFERENCES storyboard_nodes(id) ON DELETE CASCADE,
    project_id UUID NOT NULL REFERENCES storyboard_projects(id) ON DELETE CASCADE,
    frame_index INT NOT NULL,
    image_url TEXT,
    thumbnail_url TEXT,
    note TEXT,
    shot_type VARCHAR(30),
    camera_angle VARCHAR(30),
    camera_movement VARCHAR(30),
    focal_length VARCHAR(20),
    lighting TEXT,
    duration_seconds FLOAT DEFAULT 3.0,
    character_ids UUID[],
    sort_order INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);
```

### 3.6 分镜资产

```sql
CREATE TABLE storyboard_assets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES storyboard_projects(id) ON DELETE CASCADE,
    file_path TEXT NOT NULL,
    file_hash VARCHAR(64),
    file_size BIGINT,
    mime_type VARCHAR(50),
    width INT,
    height INT,
    preview_path TEXT,
    metadata_json JSONB,
    source_type VARCHAR(20) DEFAULT 'generated'
        CHECK (source_type IN ('uploaded', 'generated', 'split', 'imported')),
    created_at TIMESTAMPTZ DEFAULT now()
);
```

### 3.7 视频资产

```sql
CREATE TABLE storyboard_video_assets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES storyboard_projects(id) ON DELETE CASCADE,
    source_frame_id UUID REFERENCES storyboard_frames(id),
    source_node_id UUID REFERENCES storyboard_nodes(id),
    file_path TEXT NOT NULL,
    thumbnail_path TEXT,
    duration_seconds FLOAT,
    width INT,
    height INT,
    file_size BIGINT,
    provider VARCHAR(30),
    generation_params JSONB,
    status VARCHAR(20) DEFAULT 'pending'
        CHECK (status IN ('pending', 'processing', 'completed', 'failed')),
    created_at TIMESTAMPTZ DEFAULT now()
);
```

### 3.8 索引和 RLS

```sql
CREATE INDEX idx_sb_nodes_project ON storyboard_nodes(project_id);
CREATE INDEX idx_sb_edges_project ON storyboard_edges(project_id);
CREATE INDEX idx_sb_frames_node ON storyboard_frames(node_id);
CREATE INDEX idx_sb_frames_project ON storyboard_frames(project_id);
CREATE INDEX idx_sb_assets_hash ON storyboard_assets(file_hash);
CREATE INDEX idx_sb_characters_project ON storyboard_characters(project_id);
CREATE INDEX idx_sb_projects_team ON storyboard_projects(team_id);

ALTER TABLE storyboard_projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE storyboard_nodes ENABLE ROW LEVEL SECURITY;
ALTER TABLE storyboard_edges ENABLE ROW LEVEL SECURITY;
ALTER TABLE storyboard_frames ENABLE ROW LEVEL SECURITY;
ALTER TABLE storyboard_assets ENABLE ROW LEVEL SECURITY;
ALTER TABLE storyboard_characters ENABLE ROW LEVEL SECURITY;
ALTER TABLE storyboard_video_assets ENABLE ROW LEVEL SECURITY;
```

---

## 4. 前端组件架构

### 4.1 目录结构

```
frontend/
├── pages/
│   └── StoryboardWorkbench/
│       ├── index.tsx                    # 路由入口
│       ├── ProjectListPage.tsx          # 项目列表页
│       └── CanvasEditorPage.tsx         # 画布编辑器页
│
├── components/storyboard/
│   ├── canvas/
│   │   ├── StoryboardCanvas.tsx         # @xyflow/react 主画布
│   │   ├── CanvasToolbar.tsx            # 顶部工具栏
│   │   ├── CanvasMiniMap.tsx            # 右下角小地图
│   │   ├── NodeSelectionMenu.tsx        # 节点类型选择菜单
│   │   └── edges/SmartEdge.tsx          # 自定义连线
│   │
│   ├── nodes/
│   │   ├── UploadNode.tsx
│   │   ├── ImageEditNode.tsx
│   │   ├── StoryboardSplitNode.tsx
│   │   ├── StoryboardGenNode.tsx
│   │   ├── ImageToVideoNode.tsx
│   │   ├── TextAnnotationNode.tsx
│   │   ├── GroupNode.tsx
│   │   ├── ExportNode.tsx
│   │   └── shared/
│   │       ├── NodeWrapper.tsx
│   │       ├── NodeControlStyles.ts
│   │       └── NodeImagePreview.tsx
│   │
│   ├── chat/
│   │   ├── ChatPanel.tsx
│   │   ├── ChatMessage.tsx
│   │   └── ChatInput.tsx
│   │
│   ├── timeline/
│   │   ├── FrameTimeline.tsx
│   │   ├── FrameThumb.tsx
│   │   └── AnimaticPlayer.tsx
│   │
│   ├── characters/
│   │   ├── CharacterPanel.tsx
│   │   ├── CharacterCard.tsx
│   │   └── CharacterEditor.tsx
│   │
│   ├── tools/
│   │   ├── CropTool.tsx
│   │   ├── AnnotateTool.tsx
│   │   ├── SplitTool.tsx
│   │   ├── ShotMetadataEditor.tsx
│   │   └── CameraOverlay.tsx
│   │
│   ├── project/
│   │   ├── ProjectCard.tsx
│   │   ├── NewProjectDialog.tsx
│   │   └── ScriptImportDialog.tsx
│   │
│   ├── export/
│   │   ├── ExportDialog.tsx
│   │   └── ExportPreview.tsx
│   │
│   └── shared/
│       ├── ImageViewerModal.tsx
│       └── ImagePool.ts
│
├── stores/storyboardStore.ts
├── services/storyboardService.ts
│
└── hooks/storyboard/
    ├── useStoryboardCanvas.ts
    ├── useStoryboardPersist.ts
    ├── useStoryboardRealtime.ts
    ├── useAnimaticPlayer.ts
    └── useImagePool.ts
```

### 4.2 画布编辑器布局

```
┌─────────────────────────────────────────────────────────┐
│ CanvasToolbar [+ 节点] [缩放] [适应] [锁定] [导出] [角色] │
├─────────────────────────────────────┬───────────────────┤
│                                     │                   │
│                                     │   ChatPanel       │
│          StoryboardCanvas           │   (可收起)         │
│          (@xyflow/react)            │                   │
│                                     │   自然语言修改帧   │
│                                     │   支持 @角色引用   │
│                                     │                   │
│                          [MiniMap]  │                   │
├─────────────────────────────────────┴───────────────────┤
│ FrameTimeline [▶ 播放] [帧缩略图序列...] (可收起)        │
└─────────────────────────────────────────────────────────┘
```

### 4.3 Zustand Store

```typescript
interface StoryboardState {
  // 项目
  projectList: ProjectSummary[]
  currentProjectId: string | null

  // 画布
  nodes: StoryboardNode[]
  edges: StoryboardEdge[]
  viewport: { x: number; y: number; zoom: number }
  selectedNodeId: string | null

  // 角色
  characters: Character[]

  // 历史
  history: { past: Snapshot[]; future: Snapshot[] }  // max 50

  // 对话
  chatMessages: ChatMessage[]

  // 时间线
  timelineOrder: string[]  // frame IDs ordered

  // 图片池
  imagePool: Map<string, ImagePoolEntry>
}
```

### 4.4 持久化策略

- **节点/边变更**: 防抖 260ms → 调用 sync_canvas 批量接口
- **视口变更**: 防抖 280ms → 调用 update_viewport 轻量接口
- **帧元数据**: 即时保存（用户点击"保存"按钮）
- **图片池**: 前端引用计数，后端 SHA-256 去重

### 4.5 Supabase Realtime 订阅

```typescript
// 订阅 unified_tasks 表变更
// status=processing → 更新节点进度条
// status=completed → 更新帧图片/视频，零额外请求
// status=failed → 显示节点错误
```

---

## 5. 后端 Service 层

### 5.1 API 端点 (25 个)

```
# 项目管理
POST   /storyboard/projects
GET    /storyboard/projects
GET    /storyboard/projects/{id}
PUT    /storyboard/projects/{id}
DELETE /storyboard/projects/{id}
PUT    /storyboard/projects/{id}/viewport

# 画布节点
POST   /storyboard/projects/{id}/nodes
PUT    /storyboard/nodes/{node_id}
DELETE /storyboard/nodes/{node_id}
POST   /storyboard/projects/{id}/nodes/batch

# 画布连线
POST   /storyboard/projects/{id}/edges
DELETE /storyboard/edges/{edge_id}

# 分镜帧
GET    /storyboard/nodes/{node_id}/frames
PUT    /storyboard/frames/{frame_id}
POST   /storyboard/frames/reorder

# 角色卡
POST   /storyboard/projects/{id}/characters
PUT    /storyboard/characters/{char_id}
DELETE /storyboard/characters/{char_id}
GET    /storyboard/projects/{id}/characters

# AI 能力
POST   /storyboard/generate/image
POST   /storyboard/generate/video
POST   /storyboard/split-script
POST   /storyboard/analyze-video
POST   /storyboard/detect-scenes
POST   /storyboard/chat

# 导出
POST   /storyboard/projects/{id}/export
```

### 5.2 核心 Service

- **StoryboardService** — 项目 CRUD、画布批量同步、角色管理
- **StoryboardAIService** — 图片/视频生成、脚本拆分、视频分析、AI 对话
- **StoryboardImageService** — 图片拆分、场景检测、合并、预览图、PNG 元数据
- **StoryboardExportService** — PNG/PDF/ZIP 导出

### 5.3 AI Provider 架构

```python
# 图片 Provider
BaseImageProvider → PPIOProvider, GRSAIProvider, KIEProvider,
                    FALProvider, JimengProvider

# 视频 Provider
BaseVideoProvider → KlingProvider, RunwayProvider, ViduProvider,
                    JimengProvider

# 注册表
ProviderRegistry.get_image_provider(name) → BaseImageProvider
ProviderRegistry.get_video_provider(name) → BaseVideoProvider
```

### 5.4 Repository 层

遵循 MediaHub 现有的 Repository 模式：
- StoryboardProjectRepository
- StoryboardNodeRepository
- StoryboardEdgeRepository
- StoryboardFrameRepository
- StoryboardCharacterRepository
- StoryboardAssetRepository

---

## 6. Celery 异步任务

### 6.1 任务列表

| 任务 | 说明 | 超时 |
|------|------|------|
| generate_storyboard_image | 单帧 AI 生图 | 120s |
| generate_storyboard_image_batch | 批量生图（并行聚合） | 600s |
| generate_storyboard_video | 图生视频 | 300s |
| split_script_to_storyboard | 脚本 → 分镜（LLM） | 120s |
| analyze_video_scenes | 视频分析 + prompt 反推 | 300s |
| export_storyboard | 导出 PNG/PDF/ZIP | 120s |
| split_image_grid | 网格拆分图片 | 30s |
| process_annotation | 应用标注到图片 | 30s |

### 6.2 状态推送

所有任务通过 Supabase Realtime 推送状态变更，前端零轮询。

### 6.3 定期清理

- temp/ 目录: 24h 清理
- 孤立资产: 每日检查并回收

---

## 7. 文件存储

### 7.1 NAS 目录结构

```
teams/{team_id}/storyboard/{project_id}/
├── frames/          # 帧图片（原图）
├── thumbnails/      # 预览缩略图（512px JPEG）
├── characters/      # 角色参考图
├── videos/          # 视频资产
├── exports/         # 导出文件
└── temp/            # 临时文件
```

### 7.2 与资源库集成

- 资源库侧边栏「我的资源」下自动显示 `storyboard` 文件夹
- 按项目名组织子文件夹
- 支持双向操作：从媒体库导入 / 保存到媒体库（复制，非移动）

---

## 8. 前端交互设计

### 8.1 页面流转

```
侧边栏「分镜工作台」
    → 项目列表页（卡片展示，缩略图预览）
        ├── 新建项目（空白 / 从脚本生成 / 从视频导入）
        └── 点击项目 → 画布编辑器（全屏）
            ├── 左侧：节点画布（@xyflow/react）
            ├── 右侧：AI 对话面板（可收起）
            ├── 顶部：工具栏
            └── 底部：帧时间线 + Animatic 播放器（可收起）
```

### 8.2 节点类型

| 节点 | 功能 |
|------|------|
| upload | 上传图片 |
| image_edit | AI 生图（prompt + 模型选择 + 角色引用） |
| storyboard_split | 分镜拆分（网格/智能场景检测） |
| storyboard_gen | 分镜批量生成（多帧描述 + 批量生图） |
| image_to_video | 图生视频（Kling/Runway/Vidu） |
| text_annotation | 文字注释 |
| group | 分组 |
| export | 导出 |

### 8.3 AI 对话面板

- 右侧可收起面板
- 选中帧后用自然语言修改（镜头、灯光、prompt）
- 支持 @角色名 引用角色卡
- AI 响应可包含可执行操作按钮（应用到帧 / 重新生成）

### 8.4 角色卡系统

- 工具栏打开左侧角色面板
- 创建角色：名称 + 参考图 + 描述
- AI 自动提取 visual_traits（发型、服装、体型、年龄）
- 生图时选择角色 → 自动拼接特征到 prompt

### 8.5 镜头元数据编辑

选中帧后可编辑：
- 景别: 特写/近景/中景/全景/远景
- 机位: 平视/仰拍/俯拍/鸟瞰/蚁视
- 运动: 静止/推/拉/摇/移/升降/跟随
- 焦段: 24mm/35mm/50mm/85mm/135mm
- 灯光: 自由文本
- 时长: 秒数（Animatic 用）

### 8.6 镜头运动标注叠加层

帧图片上叠加半透明运动图标：
- 推: ⊕ →→→
- 拉: ←←← ⊕
- 摇: ←————→
- 移: ↑↓
- 升降: ⬆⬇
- 跟随: ⟿
- 静止: ⊙

### 8.7 Animatic 播放器

底部时间线：
- 帧缩略图序列，可拖拽排序
- 静态帧按 duration_seconds 播放
- 视频帧播放实际视频
- 帧间转场：硬切（默认）/ 淡入淡出 / 交叉溶解
- 播放时画布自动跳转到当前帧对应的节点
- 支持 0.5x / 1x / 1.5x / 2x 播放速度

### 8.8 标注工具

基于 Konva 的向量绘制：
- 工具: 画笔、直线、矩形、圆形、文字、箭头
- 颜色 + 线宽可调
- 标注数据独立存储（不修改原图）
- 导出时可选是否叠加标注

### 8.9 快捷键

```
Space + 拖拽       平移画布
滚轮               缩放
Cmd/Ctrl + 0       适应全部
Cmd/Ctrl + Z       撤销
Cmd/Ctrl + Shift+Z 重做
Delete             删除选中
Cmd/Ctrl + A       全选
Cmd/Ctrl + C/V     复制/粘贴
双击空白            新建节点菜单
双击节点图片        全屏查看
L                  锁定/解锁
```

---

## 9. 后续迭代

### Phase 2（未来）
- ComfyUI 集成 — 作为新的视频 Provider，支持复杂视频生成 workflow
- 多人协作 — 基于 Supabase Realtime 的实时协同编辑
- 飞书工作流挂接 — 分镜项目实例作为工作流节点

### Phase 3（未来）
- AI 分镜自动生成 → 视频生成 → 配音配乐的全流程自动化
- 3D 预可视化集成
