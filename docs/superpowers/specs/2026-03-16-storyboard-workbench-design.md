# 分镜工作台 (Storyboard Workbench) - 设计规格

> MediaHub 分镜管理子模块设计文档
> 日期: 2026-03-16
> 分支: feature/storyboard
> 版本: v2 (spec review 修订版)

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
- **数据存储**: 结构化存储（PostgreSQL），Snowflake BIGINT 主键（与 MediaHub 现有表一致）
- **图片存储**: 独立 NAS 目录，按需与媒体库关联
- **AI Provider**: 复用 MediaHub 的 Celery + 积分体系，接入 Storyboard-Copilot 和麻衣画布的 provider
- **任务状态推送**: Supabase Realtime (WebSocket)，不轮询
- **前端状态管理**: Zustand（新增依赖，画布状态复杂度高于现有 Context 模式，Zustand 更适合高频更新场景）
- **删除策略**: 软删除（项目 status='deleted'），定期清理

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
│  │  5 Router Files:                              │   │
│  │  ├── sb_projects_router.py   (项目 CRUD)      │   │
│  │  ├── sb_canvas_router.py     (节点/边/帧)     │   │
│  │  ├── sb_characters_router.py (角色卡 CRUD)    │   │
│  │  ├── sb_ai_router.py         (AI 生成/分析)   │   │
│  │  └── sb_export_router.py     (导出)           │   │
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

> 所有主键使用 Snowflake BIGINT（与 MediaHub 现有表一致），
> `created_by` 引用 auth.users 保持 UUID。

### 3.1 分镜项目

```sql
CREATE TABLE storyboard_projects (
    id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    team_id BIGINT NOT NULL REFERENCES teams(id),
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
    id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    project_id BIGINT NOT NULL REFERENCES storyboard_projects(id) ON DELETE CASCADE,
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
    id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    project_id BIGINT NOT NULL REFERENCES storyboard_projects(id) ON DELETE CASCADE,
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
    id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    project_id BIGINT NOT NULL REFERENCES storyboard_projects(id) ON DELETE CASCADE,
    source_node_id BIGINT NOT NULL REFERENCES storyboard_nodes(id) ON DELETE CASCADE,
    target_node_id BIGINT NOT NULL REFERENCES storyboard_nodes(id) ON DELETE CASCADE,
    source_handle VARCHAR(50),
    target_handle VARCHAR(50),
    edge_type VARCHAR(20) DEFAULT 'default',
    created_at TIMESTAMPTZ DEFAULT now()
);
```

### 3.5 分镜帧

```sql
CREATE TABLE storyboard_frames (
    id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    node_id BIGINT NOT NULL REFERENCES storyboard_nodes(id) ON DELETE CASCADE,
    project_id BIGINT NOT NULL REFERENCES storyboard_projects(id) ON DELETE CASCADE,
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
    transition_type VARCHAR(20) DEFAULT 'cut'
        CHECK (transition_type IN ('cut', 'fade', 'dissolve')),
    annotations_json JSONB,                    -- Konva 标注数据
    sort_order INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(node_id, frame_index)
);
```

### 3.6 帧-角色关联表（替代 character_ids 数组）

```sql
CREATE TABLE storyboard_frame_characters (
    frame_id BIGINT NOT NULL REFERENCES storyboard_frames(id) ON DELETE CASCADE,
    character_id BIGINT NOT NULL REFERENCES storyboard_characters(id) ON DELETE CASCADE,
    PRIMARY KEY (frame_id, character_id)
);
```

### 3.7 分镜资产

```sql
CREATE TABLE storyboard_assets (
    id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    project_id BIGINT NOT NULL REFERENCES storyboard_projects(id) ON DELETE CASCADE,
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

### 3.8 视频资产

```sql
CREATE TABLE storyboard_video_assets (
    id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    project_id BIGINT NOT NULL REFERENCES storyboard_projects(id) ON DELETE CASCADE,
    source_frame_id BIGINT REFERENCES storyboard_frames(id),
    source_node_id BIGINT REFERENCES storyboard_nodes(id),
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

### 3.9 索引、约束和触发器

```sql
-- 索引
CREATE INDEX idx_sb_nodes_project ON storyboard_nodes(project_id);
CREATE INDEX idx_sb_edges_project ON storyboard_edges(project_id);
CREATE INDEX idx_sb_frames_node ON storyboard_frames(node_id);
CREATE INDEX idx_sb_frames_project ON storyboard_frames(project_id);
CREATE INDEX idx_sb_assets_hash ON storyboard_assets(file_hash);
CREATE INDEX idx_sb_characters_project ON storyboard_characters(project_id);
CREATE INDEX idx_sb_projects_team ON storyboard_projects(team_id);
CREATE INDEX idx_sb_frame_chars_character ON storyboard_frame_characters(character_id);

-- updated_at 自动更新触发器
CREATE OR REPLACE FUNCTION update_sb_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_sb_projects_updated_at
    BEFORE UPDATE ON storyboard_projects
    FOR EACH ROW EXECUTE FUNCTION update_sb_updated_at();

CREATE TRIGGER trg_sb_characters_updated_at
    BEFORE UPDATE ON storyboard_characters
    FOR EACH ROW EXECUTE FUNCTION update_sb_updated_at();

CREATE TRIGGER trg_sb_nodes_updated_at
    BEFORE UPDATE ON storyboard_nodes
    FOR EACH ROW EXECUTE FUNCTION update_sb_updated_at();

CREATE TRIGGER trg_sb_frames_updated_at
    BEFORE UPDATE ON storyboard_frames
    FOR EACH ROW EXECUTE FUNCTION update_sb_updated_at();
```

### 3.10 RLS 策略

```sql
-- 启用 RLS
ALTER TABLE storyboard_projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE storyboard_nodes ENABLE ROW LEVEL SECURITY;
ALTER TABLE storyboard_edges ENABLE ROW LEVEL SECURITY;
ALTER TABLE storyboard_frames ENABLE ROW LEVEL SECURITY;
ALTER TABLE storyboard_assets ENABLE ROW LEVEL SECURITY;
ALTER TABLE storyboard_characters ENABLE ROW LEVEL SECURITY;
ALTER TABLE storyboard_video_assets ENABLE ROW LEVEL SECURITY;
ALTER TABLE storyboard_frame_characters ENABLE ROW LEVEL SECURITY;

-- 团队成员可访问项目（通过 team_members 关联）
CREATE POLICY sb_projects_team_access ON storyboard_projects
    FOR ALL USING (
        team_id IN (
            SELECT team_id FROM team_members
            WHERE user_id = auth.uid()
        )
    );

-- 子表通过 project_id 继承项目权限
CREATE POLICY sb_nodes_access ON storyboard_nodes
    FOR ALL USING (
        project_id IN (
            SELECT id FROM storyboard_projects
            WHERE team_id IN (
                SELECT team_id FROM team_members WHERE user_id = auth.uid()
            )
        )
    );

-- 同样的策略应用到：edges, frames, assets, characters, video_assets, frame_characters
-- （省略重复，实际迁移文件中会完整定义）

-- service_role 绕过（Celery worker 使用）
CREATE POLICY sb_service_role_bypass ON storyboard_projects
    FOR ALL USING (auth.role() = 'service_role');
-- （每个表都需要 service_role bypass 策略）

-- Realtime 发布（用于 Supabase Realtime 订阅）
ALTER PUBLICATION supabase_realtime ADD TABLE storyboard_projects;
ALTER PUBLICATION supabase_realtime ADD TABLE storyboard_nodes;
ALTER PUBLICATION supabase_realtime ADD TABLE storyboard_frames;
```

---

## 4. Pydantic Schemas

```python
# backend/app/schemas/storyboard.py

class StoryboardProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    settings_json: Optional[dict] = None

class StoryboardProjectUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    status: Optional[str] = Field(None, pattern="^(active|archived)$")
    settings_json: Optional[dict] = None

class StoryboardNodeCreate(BaseModel):
    node_type: str = Field(..., pattern="^(upload|image_edit|storyboard_split|storyboard_gen|text_annotation|group|export|image_to_video)$")
    position_x: float = 0
    position_y: float = 0
    width: Optional[float] = None
    height: Optional[float] = None
    data_json: dict = Field(default_factory=dict)

class StoryboardNodeUpdate(BaseModel):
    position_x: Optional[float] = None
    position_y: Optional[float] = None
    width: Optional[float] = None
    height: Optional[float] = None
    data_json: Optional[dict] = None
    locked: Optional[bool] = None

class CanvasSyncRequest(BaseModel):
    added_nodes: list[StoryboardNodeCreate] = []
    updated_nodes: list[dict] = []           # {id, ...partial fields}
    deleted_node_ids: list[str] = []
    added_edges: list[dict] = []
    deleted_edge_ids: list[str] = []

class StoryboardFrameUpdate(BaseModel):
    note: Optional[str] = Field(None, max_length=2000)
    shot_type: Optional[str] = Field(None, pattern="^(extreme_close_up|close_up|medium_close_up|medium|medium_wide|wide|extreme_wide)$")
    camera_angle: Optional[str] = Field(None, pattern="^(eye_level|low_angle|high_angle|bird_eye|worm_eye)$")
    camera_movement: Optional[str] = Field(None, pattern="^(static|push|pull|pan|tilt|dolly|crane|tracking)$")
    focal_length: Optional[str] = Field(None, pattern="^(24mm|35mm|50mm|85mm|135mm)$")
    lighting: Optional[str] = Field(None, max_length=500)
    duration_seconds: Optional[float] = Field(None, ge=0.5, le=30.0)
    transition_type: Optional[str] = Field(None, pattern="^(cut|fade|dissolve)$")
    annotations_json: Optional[dict] = None

class CharacterCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=2000)
    visual_traits: Optional[dict] = None

class CharacterUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=2000)
    visual_traits: Optional[dict] = None

class GenerateImageRequest(BaseModel):
    project_id: str
    node_id: str
    prompt: str = Field(..., min_length=1, max_length=4000)
    model: str = Field(..., min_length=1)
    provider: str = Field(..., min_length=1)
    aspect_ratio: Optional[str] = "16:9"
    character_ids: list[str] = []
    reference_image_url: Optional[str] = None

class GenerateVideoRequest(BaseModel):
    project_id: str
    node_id: str
    source_image_url: str
    prompt: Optional[str] = Field(None, max_length=4000)
    provider: str = Field(..., min_length=1)
    duration_seconds: float = Field(5.0, ge=3.0, le=25.0)
    motion_intensity: Optional[str] = Field("medium", pattern="^(low|medium|high)$")

class SplitScriptRequest(BaseModel):
    project_id: str
    script_text: str = Field(..., min_length=10, max_length=50000)
    style_guide: Optional[str] = Field(None, max_length=2000)
```

### 4.1 上传限制

| 类型 | 最大大小 | 允许 MIME |
|------|---------|-----------|
| 帧图片 | 20MB | image/png, image/jpeg, image/webp |
| 角色参考图 | 10MB | image/png, image/jpeg, image/webp |
| 视频导入 | 500MB | video/mp4, video/webm, video/quicktime |

---

## 5. 前端组件架构

### 5.1 目录结构

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

### 5.2 画布编辑器布局

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

### 5.3 Zustand Store

> 新增依赖: `zustand`。选择 Zustand 而非 Context 的原因：画布状态更新频率极高（拖拽、缩放），
> Context 会导致不必要的重渲染，Zustand 的 selector 模式更适合此场景。

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

  // 图片池（Record 而非 Map，支持序列化）
  imagePool: Record<string, ImagePoolEntry>
}
```

### 5.4 持久化策略

- **节点/边变更**: 防抖 260ms → 调用 sync_canvas 批量接口
- **视口变更**: 防抖 280ms → 调用 update_viewport 轻量接口
- **帧元数据**: 即时保存（用户点击"保存"按钮）
- **图片池**: 前端引用计数，后端 SHA-256 去重

### 5.5 Supabase Realtime 订阅

```typescript
// 订阅 unified_tasks 表变更
// filter: metadata->>project_id = currentProjectId
// AND task_type IN ('sb_generate', 'sb_export', 'sb_split', 'sb_analyze')
//
// status=processing → 更新节点进度条
// status=completed → 更新帧图片/视频，零额外请求
// status=failed → 显示节点错误
```

---

## 6. 后端 Service 层

### 6.1 Router 拆分 (5 个文件)

```python
# sb_projects_router.py (prefix="/storyboard/projects")
POST   /                               # 创建项目
GET    /                               # 项目列表（分页: page, limit, 默认 page=1 limit=20）
GET    /{id}                           # 项目详情（含节点、边、帧、角色）
PUT    /{id}                           # 更新项目
DELETE /{id}                           # 软删除（status='deleted'）
PUT    /{id}/viewport                  # 独立更新视口

# sb_canvas_router.py (prefix="/storyboard")
POST   /projects/{id}/nodes            # 批量创建/更新节点
PUT    /nodes/{node_id}                # 更新单个节点
DELETE /nodes/{node_id}                # 删除节点
POST   /projects/{id}/nodes/batch      # 画布同步（CanvasSyncRequest）
POST   /projects/{id}/edges            # 批量创建/更新边
DELETE /edges/{edge_id}                # 删除边
GET    /nodes/{node_id}/frames         # 获取帧列表（分页: page, limit）
PUT    /frames/{frame_id}              # 更新帧
POST   /frames/reorder                 # 时间线重排序

# sb_characters_router.py (prefix="/storyboard")
POST   /projects/{id}/characters       # 创建角色
PUT    /characters/{char_id}           # 更新角色
DELETE /characters/{char_id}           # 删除角色
GET    /projects/{id}/characters       # 角色列表

# sb_ai_router.py (prefix="/storyboard")
POST   /generate/image                 # AI 生图
POST   /generate/video                 # AI 图生视频
POST   /split-script                   # 脚本自动拆分镜
POST   /analyze-video                  # 视频分析 + prompt 反推
POST   /detect-scenes                  # 智能场景检测
POST   /chat                           # AI 对话

# sb_export_router.py (prefix="/storyboard")
POST   /projects/{id}/export           # 导出 PNG/PDF/ZIP
```

### 6.2 核心 Service

- **StoryboardService** — 项目 CRUD、画布批量同步、角色管理
- **StoryboardAIService** — 图片/视频生成、脚本拆分、视频分析、AI 对话
- **StoryboardImageService** — 图片拆分、场景检测、合并、预览图、PNG 元数据
- **StoryboardExportService** — PNG/PDF/ZIP 导出

### 6.3 AI Provider 架构

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

### 6.4 Repository 层

遵循 MediaHub 现有的 Repository 模式：
- StoryboardProjectRepository
- StoryboardNodeRepository
- StoryboardEdgeRepository
- StoryboardFrameRepository
- StoryboardCharacterRepository
- StoryboardAssetRepository

---

## 7. Celery 异步任务

### 7.1 任务列表

| 任务 | task_type (unified_tasks) | 说明 | 超时 |
|------|--------------------------|------|------|
| generate_storyboard_image | sb_generate | 单帧 AI 生图 | 120s |
| generate_storyboard_image_batch | sb_generate | 批量生图（并行聚合） | 600s |
| generate_storyboard_video | sb_generate | 图生视频 | 300s |
| split_script_to_storyboard | sb_split | 脚本 → 分镜（LLM） | 120s |
| analyze_video_scenes | sb_analyze | 视频分析 + prompt 反推 | 300s |
| export_storyboard | sb_export | 导出 PNG/PDF/ZIP | 120s |
| split_image_grid | sb_split | 网格拆分图片 | 30s |
| process_annotation | sb_generate | 应用标注到图片 | 30s |

### 7.2 状态推送

所有任务通过 Supabase Realtime 推送 unified_tasks 表变更，前端按 task_type 前缀 `sb_` 过滤。

### 7.3 定期清理

- temp/ 目录: 24h 清理
- 孤立资产: 每日检查并回收
- status='deleted' 的项目: 30 天后硬删除

---

## 8. 文件存储

### 8.1 NAS 目录结构

```
teams/{team_id}/storyboard/{project_id}/
├── frames/          # 帧图片（原图）
├── thumbnails/      # 预览缩略图（512px JPEG）
├── characters/      # 角色参考图
├── videos/          # 视频资产
├── exports/         # 导出文件
└── temp/            # 临时文件
```

### 8.2 与资源库集成

- 资源库侧边栏「我的资源」下自动显示 `storyboard` 文件夹
- 按项目名组织子文件夹
- 支持双向操作：从媒体库导入 / 保存到媒体库（复制，非移动）

---

## 9. 前端交互设计

### 9.1 页面流转

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

### 9.2 节点类型

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

### 9.3 AI 对话面板

- 右侧可收起面板
- 选中帧后用自然语言修改（镜头、灯光、prompt）
- 支持 @角色名 引用角色卡
- AI 响应可包含可执行操作按钮（应用到帧 / 重新生成）

### 9.4 角色卡系统

- 工具栏打开左侧角色面板
- 创建角色：名称 + 参考图 + 描述
- AI 自动提取 visual_traits（发型、服装、体型、年龄）
- 生图时选择角色 → 自动拼接特征到 prompt

### 9.5 镜头元数据编辑

选中帧后可编辑：
- 景别: 特写/近景/中景/全景/远景
- 机位: 平视/仰拍/俯拍/鸟瞰/蚁视
- 运动: 静止/推/拉/摇/移/升降/跟随
- 焦段: 24mm/35mm/50mm/85mm/135mm
- 灯光: 自由文本
- 时长: 秒数（Animatic 用）
- 转场: 硬切/淡入淡出/交叉溶解

### 9.6 镜头运动标注叠加层

帧图片上叠加半透明运动图标：
- 推: ⊕ →→→
- 拉: ←←← ⊕
- 摇: ←————→
- 移: ↑↓
- 升降: ⬆⬇
- 跟随: ⟿
- 静止: ⊙

### 9.7 Animatic 播放器

底部时间线：
- 帧缩略图序列，可拖拽排序
- 静态帧按 duration_seconds 播放
- 视频帧播放实际视频
- 帧间转场根据 transition_type 字段渲染
- 播放时画布自动跳转到当前帧对应的节点
- 支持 0.5x / 1x / 1.5x / 2x 播放速度

### 9.8 标注工具

基于 Konva 的向量绘制：
- 工具: 画笔、直线、矩形、圆形、文字、箭头
- 颜色 + 线宽可调
- 标注数据存储在 storyboard_frames.annotations_json
- 导出时可选是否叠加标注

### 9.9 快捷键

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

## 10. 后续迭代

### Phase 2（未来）
- ComfyUI 集成 — 作为新的视频 Provider，支持复杂视频生成 workflow
- 多人协作 — 基于 Supabase Realtime 的实时协同编辑
- 飞书工作流挂接 — 分镜项目实例作为工作流节点

### Phase 3（未来）
- AI 分镜自动生成 → 视频生成 → 配音配乐的全流程自动化
- 3D 预可视化集成

---

## Appendix: Spec Review 修订记录

| # | 严重度 | 问题 | 修复 |
|---|--------|------|------|
| 1 | CRITICAL | UUID vs BIGINT 主键不匹配 | 所有 PK 改为 BIGINT DEFAULT generate_snowflake_id() |
| 2 | CRITICAL | RLS 启用但未定义策略 | 添加 team_members 关联策略 + service_role bypass |
| 3 | HIGH | character_ids 数组列 | 替换为 storyboard_frame_characters 关联表 |
| 4 | HIGH | 缺少 updated_at 触发器 | 添加 BEFORE UPDATE 触发器 |
| 5 | HIGH | 25 端点单文件 | 拆分为 5 个 router 文件 |
| 6 | MEDIUM | 缺少 Pydantic schemas | 添加完整 schema 定义 |
| 7 | MEDIUM | 列表端点无分页 | 添加 page/limit 参数 |
| 8 | MEDIUM | Zustand Map 不可序列化 | 改为 Record 类型 |
| 9 | MEDIUM | Zustand vs Context 不一致 | 文档说明选择原因 |
| 10 | MEDIUM | 无上传校验 | 定义大小/MIME 限制 |
| 11 | MEDIUM | unified_tasks 集成不完整 | 定义 sb_ 前缀 task_type |
| 12 | LOW | frame_index 无唯一约束 | 添加 UNIQUE(node_id, frame_index) |
| 13 | LOW | 软删除 vs 硬删除不明确 | 明确软删除策略 + 30 天清理 |
| 14 | LOW | 转场类型未持久化 | 添加 transition_type 列 |
| 15 | LOW | 标注数据无存储位置 | 添加 annotations_json 列 |
