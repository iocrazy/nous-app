# 异步下载任务系统设计

> 创建日期: 2026-01-14

## 概述

解决当前下载任务阻塞 API 响应的问题，实现：
- API 快速返回，下载在后台执行
- WebSocket 实时推送任务状态
- 服务重启后自动恢复未完成任务

## 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                        Frontend                              │
│  ┌─────────────┐    ┌──────────────┐    ┌───────────────┐   │
│  │  FetchPage  │───▶│ WebSocket    │◀───│ TaskListPanel │   │
│  │  (提交URL)  │    │ Connection   │    │ (任务状态)     │   │
│  └─────────────┘    └──────────────┘    └───────────────┘   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                        Backend                               │
│  ┌─────────────┐    ┌──────────────┐    ┌───────────────┐   │
│  │ /fetch API  │───▶│ TaskQueue    │───▶│ DownloadWorker│   │
│  │ (快速返回)  │    │ (asyncio.Q)  │    │ (后台消费)    │   │
│  └─────────────┘    └──────────────┘    └───────────────┘   │
│                              │                    │          │
│                              ▼                    ▼          │
│                     ┌──────────────┐    ┌───────────────┐   │
│                     │ WSManager    │◀───│ 状态变更事件   │   │
│                     │ (推送管理)   │    │               │   │
│                     └──────────────┘    └───────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

**核心流程：**
1. 用户提交 URL → `/fetch` API 解析视频数据，存入数据库，任务加入队列，**立即返回**
2. 后台 Worker 从队列取任务 → 执行下载 → 更新数据库状态
3. 每次状态变更 → 通过 WebSocket 推送给对应用户
4. 前端收到推送 → 更新 FetchPage 任务列表

## 技术选型

| 组件 | 选择 | 理由 |
|------|------|------|
| 任务队列 | asyncio.Queue | 进程内，无需额外部署，适合当前规模 |
| 实时推送 | WebSocket | 双向通信，实时性最好 |
| 任务持久化 | Supabase (已有) | 利用现有 pending 状态字段 |

## 后端实现

### 新增文件结构

```
backend/app/
├── services/
│   └── task_queue.py       # 任务队列管理器
├── api/
│   └── websocket_router.py # WebSocket 端点
└── core/
    └── ws_manager.py       # WebSocket 连接管理
```

### TaskQueue 设计

```python
class DownloadTaskQueue:
    def __init__(self):
        self.queue = asyncio.Queue(maxsize=100)
        self.active_tasks: Dict[str, TaskInfo] = {}

    async def add_task(self, task_info: TaskInfo) -> None:
        """添加任务到队列"""

    async def worker(self) -> None:
        """后台消费者，持续处理队列任务"""

    async def recover_pending_tasks(self) -> None:
        """启动时恢复数据库中 pending 状态的任务"""

    async def shutdown(self) -> None:
        """优雅关闭，等待当前任务完成"""
```

### 任务状态

- `queued` - 排队中
- `downloading` - 下载中（含进度百分比）
- `completed` - 完成
- `failed` - 失败（含错误信息）

### WebSocket 管理器

```python
class WebSocketManager:
    def __init__(self):
        # user_id -> List[WebSocket] (同一用户可能多个标签页)
        self.connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, user_id: str, ws: WebSocket) -> None
    async def disconnect(self, user_id: str, ws: WebSocket) -> None
    async def send_to_user(self, user_id: str, message: dict) -> None
```

### 媒体类型映射

```python
MEDIA_TYPE_LABELS = {
    "0": "video",           # 标准视频
    "2": "image_album",     # 图片合集
    "4": "video",           # 特殊视频
    "61": "video",          # 特殊视频
    "68": "image_text",     # 图文
}
```

## API 设计

### POST /douyin/fetch (修改)

快速返回，任务加入后台队列。

**响应：**
```json
{
  "success": true,
  "message": "任务已加入队列",
  "task": {
    "aweme_id": "7591485198434605241",
    "video_title": "等一分钟",
    "author": "音乐分享",
    "media_type": "video",
    "status": "queued",
    "cover_url": "https://...",
    "downloads": {
      "video": true,
      "cover": true,
      "music": false
    }
  }
}
```

### GET /ws/tasks (新增)

WebSocket 端点，用于接收任务状态推送。

- 连接时需要携带 JWT token 进行认证
- 断线自动重连由前端处理

### GET /douyin/tasks (新增)

获取当前用户的活跃任务列表（用于页面刷新后同步状态）。

## WebSocket 消息格式

```json
{
  "type": "task_update",
  "data": {
    "aweme_id": "7591485198434605241",
    "video_title": "等一分钟",
    "author": "音乐分享",
    "media_type": "video",
    "status": "downloading",
    "progress": 45,
    "cover_url": "https://...",
    "downloads": {
      "video": {"status": "downloading", "progress": 45},
      "cover": {"status": "completed"},
      "music": {"status": "skipped"}
    },
    "error": null
  }
}
```

**推送时机：**
1. 任务入队 → 推送 `queued`
2. 开始下载 → 推送 `downloading` + 进度
3. 下载完成 → 推送 `completed`
4. 下载失败 → 推送 `failed` + 错误信息

## 前端实现

### 新增文件结构

```
frontend/src/
├── hooks/
│   └── useTaskWebSocket.ts   # WebSocket 连接 hook
├── stores/
│   └── taskStore.ts          # Zustand 任务状态管理
└── components/
    └── TaskListPanel.tsx     # 任务列表组件
```

### FetchPage 布局

```
┌─────────────────────────────────────────────────────┐
│  FetchPage                                          │
├─────────────────────────────────────────────────────┤
│  ┌───────────────────────────────────────────────┐  │
│  │  URL 输入框 + 选项 + 提交按钮                  │  │
│  └───────────────────────────────────────────────┘  │
├─────────────────────────────────────────────────────┤
│  ┌───────────────────────────────────────────────┐  │
│  │  任务列表 (TaskListPanel)                     │  │
│  │  ┌─────────────────────────────────────────┐  │  │
│  │  │ 📹 等一分钟          下载中 ████░░ 65%  │  │  │
│  │  │    视频 ✓  封面 ✓  音乐 ⏳              │  │  │
│  │  ├─────────────────────────────────────────┤  │  │
│  │  │ 📹 经典老歌合集       排队中 ⏳          │  │  │
│  │  ├─────────────────────────────────────────┤  │  │
│  │  │ 📹 夏日回忆          完成 ✅            │  │  │
│  │  └─────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

### 状态管理 (Zustand)

```typescript
interface TaskInfo {
  aweme_id: string
  video_title: string
  author: string
  media_type: 'video' | 'image_album' | 'image_text'
  status: 'queued' | 'downloading' | 'completed' | 'failed'
  progress: number
  cover_url: string
  downloads: {
    video: { status: string; progress?: number }
    cover: { status: string }
    music: { status: string }
  }
  error: string | null
}

interface TaskStore {
  tasks: Map<string, TaskInfo>
  addTask: (task: TaskInfo) => void
  updateTask: (awemeId: string, update: Partial<TaskInfo>) => void
  removeTask: (awemeId: string) => void
}
```

## 错误处理

| 场景 | 处理方式 |
|------|----------|
| 单个 URL 下载失败 | 重试 3 次，仍失败则标记 `failed`，推送错误信息 |
| WebSocket 断开 | 前端自动重连，重连后拉取当前任务列表同步状态 |
| 服务重启 | 启动时查询 `pending` 状态记录，重新入队 |
| 队列满 | 设置队列上限（默认 100），超出返回 429 错误 |

## 启动恢复流程

```python
# app/main.py lifespan
async def lifespan(app):
    # 启动
    task_queue = DownloadTaskQueue()
    asyncio.create_task(task_queue.worker())      # 启动消费者
    await task_queue.recover_pending_tasks()       # 恢复未完成任务
    yield
    # 关闭
    await task_queue.shutdown()
```

## 未来扩展

如需支持多服务器部署或大量并发，可迁移到：
- Redis + ARQ (轻量级异步队列)
- Redis + Celery (功能完整的分布式队列)

当前进程内队列方案足够应对单机使用场景。
