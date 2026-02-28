# Download Progress Architecture

下载进度追踪的完整代码链路和数据流。

## 数据流总览

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ Celery Worker                                                               │
│                                                                             │
│  download_media_task()                                                      │
│    │                                                                        │
│    ├─ 1. TaskTracker.create() ──→ INSERT unified_tasks (status=pending)      │
│    ├─ 2. TaskTracker.start()  ──→ UPDATE status=processing                  │
│    │                                                                        │
│    ├─ 3. _force_progress(3%) ──→ UPDATE progress=3, subtitle="Downloading…" │
│    │                                                                        │
│    ├─ 4. download_file() streaming loop:                                    │
│    │      每 64KB chunk → tracker.update(downloaded, total)                  │
│    │        ├─ Redis SETEX (legacy, 每 0.5s)                                │
│    │        └─ TaskTracker.update_progress() (每 1s, throttled)              │
│    │            └─→ UPDATE unified_tasks SET progress=X, speed=Y            │
│    │                  └─→ Supabase Realtime pg_notify()                     │
│    │                        └─→ WebSocket push to frontend                  │
│    │                                                                        │
│    ├─ 5. _force_progress(72%) ──→ "Downloading audio…"                      │
│    ├─ 6. _force_progress(87%) ──→ "Downloading cover…"                      │
│    │                                                                        │
│    └─ 7. TaskTracker.complete() ──→ UPDATE status=completed, progress=100   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
         │ Supabase Realtime (WebSocket)
         ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Frontend                                                                    │
│                                                                             │
│  TaskManagerContext                                                          │
│    │ supabase.channel('user-tasks-{userId}')                                │
│    │   .on('postgres_changes', 'UPDATE', …)                                 │
│    │                                                                        │
│    └─→ dispatch({ type: 'UPDATE', task: payload.new })                      │
│          │                                                                  │
│          ▼                                                                  │
│  TopBar.tsx → TaskCenterPanel                                               │
│    ├─ progress bar: width = task.progress + "%"                             │
│    ├─ percentage:   task.progress > 0 ? "{progress}%" : ""                  │
│    ├─ speed:        task.speed > 0 ? formatSpeed(speed) : ""                │
│    └─ subtitle:     task.subtitle (e.g. "Downloading video…")               │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 完整流程日志（以 Douyin 视频 Fetch 为例）

从用户点击 "Fetch Video" 到下载完成，每个阶段的日志输出。

### 阶段 1: 前端请求 → API 解析

```
[Frontend] MediaCard.onRefetch({ video: true, cover: true, music: true })
  → POST /api/v1/videos/{platform_id}/fetch  { types: ["video", "cover", "music"] }

[Backend/API] media_router.py
  → INFO  log_user_action(action="fetch", status="success",
           message="Fetch ['video','cover','music'] for 和Vera Blue一起在澳洲...")
  → Celery dispatch: download_media_task.apply_async(platform_id, user_id, ...)
```

**user_logs 写入**: `action=fetch, status=success, message="Fetch ['video',...] for {title}..."`

### 阶段 2: Celery Worker 初始化

```
[Celery/Worker] download_media_task() 开始
  → logger.info("[Download/Start] platform_id={id}, strategy=douyin")

  1. TaskTracker.create() → INSERT unified_tasks
     unified_tasks: { status: "pending", progress: 0, title: "和Vera Blue一起在澳洲..." }

  2. TaskTracker.start()  → UPDATE unified_tasks
     unified_tasks: { status: "processing", started_at: now() }

  3. 计算阶段权重 _calc_stage_ranges()
     → logger.debug("[Download/Stages] video=3~72, music=72~87, cover=87~95")
```

**unified_tasks 状态**: `pending → processing, progress=0`

### 阶段 3: 视频下载（Video Stage）

```
[Celery/Worker] _do_douyin_download()
  → _force_progress(3, "Downloading video...")
     unified_tasks: { progress: 3, subtitle: "Downloading video..." }

  → tracker.set_stage(offset=3, weight=69)

[Downloader] download_video_by_platform_id()
  → HEAD request → expected_size = 27696546 (26.4 MB)
  → logger.info("[Download/File] Stream started: content-length=27696546, ...")

  → Streaming loop (64KB chunks):
     chunk  1: downloaded=65536   → tracker.update(65536, 27696546)
                                    → progress=3  (3 + 0.2% * 69 ≈ 3)
     chunk 10: downloaded=655360  → tracker.update(655360, 27696546)
                                    → progress=4  (3 + 2.4% * 69 ≈ 5)
     chunk 100: downloaded=6.5MB  → tracker.update(6553600, 27696546)
                                    → progress=19 (3 + 23.7% * 69 ≈ 19)
                                    → speed=2.1 MB/s
     ...
     chunk 422: downloaded=27.6MB → tracker.update(27696546, 27696546)
                                    → progress=71 (3 + 100% * 69 ≈ 72)

  → logger.success("[Download/File] Video downloaded successfully: {path}")

  → log_user_action(action="download", status="success",
       message="Video downloaded: 和Vera Blue一起在澳洲...")
```

**unified_tasks 状态**: `progress: 3 → 19 → 45 → 71 (每秒更新), speed: 2.1MB/s`
**user_logs 写入**: `action=download, status=success, message="Video downloaded: {title}..."`

### 阶段 4: 音频下载（Music Stage）

```
[Celery/Worker]
  → _force_progress(72, "Downloading audio...")
     unified_tasks: { progress: 72, subtitle: "Downloading audio..." }

  → tracker.set_stage(offset=72, weight=15)

[Downloader] download_music_by_platform_id()
  → 无 progress_tracker，只有最终结果
  → logger.success("[Download/File] Music downloaded: {path}")

  → _force_progress(87)
     unified_tasks: { progress: 87 }
```

**unified_tasks 状态**: `progress: 72 → 87 (跳跃式, 无逐 chunk 更新)`

### 阶段 5: 封面下载（Cover Stage）

```
[Celery/Worker]
  → _force_progress(87, "Downloading cover...")
     unified_tasks: { progress: 87, subtitle: "Downloading cover..." }

  → tracker.set_stage(offset=87, weight=8)

[Downloader] download_cover_by_platform_id()
  → 无 progress_tracker，只有最终结果
  → logger.success("[Download/File] Cover downloaded: {path}")

  → _force_progress(95)
     unified_tasks: { progress: 95 }
```

**unified_tasks 状态**: `progress: 87 → 95 (跳跃式)`

### 阶段 6: 完成 / 失败

**成功路径**:
```
[Celery/Worker]
  → tracker.complete()
  → TaskTracker.complete(unified_task_id)
     unified_tasks: { status: "completed", progress: 100, completed_at: now() }

  → logger.success("[Download/Done] All types completed: {platform_id}")

  → log_user_action(action="download", status="success",
       message="Download completed (douyin): 和Vera Blue一起在澳洲...")
```

**user_logs 写入**: `action=download, status=success, message="Download completed (douyin): {title}..."`

**失败路径**:
```
[Celery/Worker]
  → tracker.failed(error_msg)
  → TaskTracker.fail(unified_task_id, error_msg)
     unified_tasks: { status: "failed", error_msg: "All 3 URLs failed: ..." }

  → logger.error("[Download/douyin] Max retries reached for {platform_id}")

  → log_user_action(action="download", status="error",
       message="Download failed (douyin): 和Vera Blue一起在澳洲...",
       details={ error: "...", retry_count: 3 })
```

**user_logs 写入**: `action=download, status=error, message="Download failed (douyin): {title}..."`

### 阶段 7: 前端实时更新

```
[Frontend/WebSocket] Supabase Realtime
  ← postgres_changes UPDATE: unified_tasks row
    { progress: 45, speed: 2100000, subtitle: "Downloading video..." }

[Frontend/TaskManagerContext]
  → dispatch({ type: 'UPDATE', task: payload.new })
  → React re-render

[Frontend/TopBar → TaskCenterPanel]
  → progress bar: width=45%
  → percentage: "45%"
  → speed: "2.1 MB/s"
  → subtitle: "Downloading video..."
```

---

## 完整日志时间线示例

一次成功的 Video + Music + Cover 下载:

```
T+0.0s  [API]     INFO   Fetch ['video','music','cover'] for 和Vera Blue...
T+0.1s  [Celery]  INFO   [Download/Start] platform_id=xxx, strategy=douyin
T+0.2s  [DB]      INSERT unified_tasks { status: pending, progress: 0 }
T+0.3s  [DB]      UPDATE unified_tasks { status: processing }
T+0.5s  [DB]      UPDATE unified_tasks { progress: 3, subtitle: "Downloading video..." }
T+0.6s  [HTTP]    INFO   Stream started: content-length=27696546
T+1.5s  [DB]      UPDATE unified_tasks { progress: 8, speed: 5242880 }
T+2.5s  [DB]      UPDATE unified_tasks { progress: 19, speed: 5500000 }
T+3.5s  [DB]      UPDATE unified_tasks { progress: 31, speed: 5200000 }
...每秒更新...
T+8.0s  [DB]      UPDATE unified_tasks { progress: 71, speed: 4800000 }
T+8.1s  [Log]     SUCCESS  Video downloaded: 和Vera Blue...
T+8.2s  [DB]      UPDATE unified_tasks { progress: 72, subtitle: "Downloading audio..." }
T+9.5s  [DB]      UPDATE unified_tasks { progress: 87 }
T+9.6s  [DB]      UPDATE unified_tasks { progress: 87, subtitle: "Downloading cover..." }
T+10.2s [DB]      UPDATE unified_tasks { progress: 95 }
T+10.3s [DB]      UPDATE unified_tasks { status: completed, progress: 100 }
T+10.4s [Log]     SUCCESS  Download completed (douyin): 和Vera Blue...
```

---

## Activity Logs 系统

### Log Levels

| Level | DB status 值 | 用途 | 颜色 |
|-------|-------------|------|------|
| SUCCESS | `success` | 操作成功完成 | 绿色 |
| INFO | `info` | 任务已提交/一般信息 | 蓝色 |
| WARN | `warning` | 警告（部分失败等） | 黄色 |
| ERROR | `error` | 操作失败 | 红色 |
| DEBUG | `debug` | 调试信息（详细诊断） | 紫色 |

### 日志写入点

下载流程中，`log_user_action()` 的写入时机:

| 阶段 | action | status | message 示例 |
|------|--------|--------|-------------|
| API 收到请求 | `fetch` | `info` | `Fetch ['video','cover'] for {title}...` |
| 解析元数据成功 | `fetch` | `success` | `Video parsed successfully: {title}...` |
| 解析失败 | `fetch` | `error` | `Failed to fetch video: {error}` |
| 单文件下载成功 | `download` | `success` | `Video downloaded: {title}...` |
| 单文件下载失败 | `download` | `error` | `Video download failed: {title}...` |
| 整体下载完成 | `download` | `success` | `Download completed (douyin): {title}...` |
| 整体下载失败 | `download` | `error` | `Download failed (douyin): {title}...` |
| 图集下载成功 | `download` | `success` | `Image set downloaded: {title}... (N files)` |
| 图集下载失败 | `download` | `error` | `Image set download failed: {title}...` |
| AI Pipeline 开始 | `ai` | `info` | `AI pipeline started: {id} (N tasks)` |
| 转录完成 | `ai` | `success` | `Transcription completed: {id}` |
| 转录失败 | `ai` | `error` | `Transcription failed: {id}` |
| 转码完成 | `transcode` | `success` | `Transcode completed: {title}` |
| 重试下载 | `retry` | `info` | `Retry download: {title}...` |
| 删除 | `delete` | `info` | `Deleted: {title}` |
| 登录 | `auth` | `success` | `User signed in` |
| 批量提交 | `fetch_batch` | `info` | `Submitted batch Celery task: N links` |

### 日志相关文件

| 文件 | 角色 |
|------|------|
| `supabase/migrations/006_create_user_logs_table.sql` | 数据表定义 |
| `backend/app/repositories/user_logs_repository.py` | 写入层 (`log_user_action()`) |
| `backend/app/repositories/logs_repository.py` | 查询层 (分页/筛选/导出) |
| `backend/app/api/logs_router.py` | REST API (`GET /api/v1/logs`) |
| `frontend/components/LogsPanel.tsx` | 前端 Logs 页面 |

---

## 涉及的文件

| 文件 | 角色 | 关键行 |
|------|------|--------|
| `backend/app/tasks/download_tasks.py` | Celery 任务 + 进度追踪器 | L109-253 (tracker), L890-1240 (task) |
| `backend/app/services/task_tracker.py` | 数据库写入层（CRUD unified_tasks） | L86-118 (update_progress) |
| `backend/app/services/downloader.py` | HTTP 下载 + 逐 chunk 回调 | L230-254 (streaming loop) |
| `frontend/contexts/TaskManagerContext.tsx` | Supabase Realtime 订阅 | L233-266 (channel subscribe) |
| `frontend/components/TopBar.tsx` | 进度条 UI 渲染 | L234-284 (progress bar) |
| `backend/app/api/task_manager_router.py` | REST API（初始加载/操作） | L22-47 (list/active) |

---

## 1. 后端：Celery 任务入口

**文件**: `backend/app/tasks/download_tasks.py` L890-990

```python
@shared_task(bind=True, ...)
def download_media_task(self, platform_id, user_id, ...):
    # 1. 创建 unified_tasks 记录
    tracker_unified = get_task_tracker()
    unified_task_id = run_async(tracker_unified.create(
        user_id=user_id,
        task_type="download",
        title=video_title or platform_id,
        subtitle="Video + Audio + Cover",  # 根据勾选项生成
        media_id=platform_id,
        celery_task_id=task_id,
    ))
    run_async(tracker_unified.start(unified_task_id))  # status → processing

    # 2. 创建 UnifiedProgressTracker（桥接 Redis + Supabase）
    redis_client = celery_app.backend.client
    tracker = UnifiedProgressTracker(
        task_id=task_id,
        redis_client=redis_client,
        unified_tracker=tracker_unified,        # TaskTracker 实例
        unified_task_id=unified_task_id,        # unified_tasks 行 ID
    )

    # 3. 执行下载（douyin 或 yt-dlp 路径）
    if url:
        results = _do_ytdlp_download(...)
    else:
        results = _do_douyin_download(...)

    # 4. 完成/失败
    if has_failures:
        run_async(tracker_unified.fail(unified_task_id, error_msg))
    else:
        run_async(tracker_unified.complete(unified_task_id))  # progress → 100
```

## 2. 后端：UnifiedProgressTracker

**文件**: `backend/app/tasks/download_tasks.py` L109-228

双写设计：同时写 Redis（legacy 轮询）和 Supabase（Realtime 推送）。

```python
class UnifiedProgressTracker:
    def __init__(self, task_id, redis_client, unified_tracker, unified_task_id):
        self._stage_offset = 0    # 当前阶段起始百分比
        self._stage_weight = 100  # 当前阶段权重（百分点）

    def set_stage(self, offset: int, weight: int):
        """设置阶段边界，用于将 raw download % 映射到 overall task %"""
        self._stage_offset = offset
        self._stage_weight = weight

    async def update(self, downloaded: int, total: int):
        # 内部限流: 每 0.5s 最多执行一次
        if now - self.last_update < 0.5:
            return

        raw_percent = int((downloaded / total) * 100) if total > 0 else 0

        # 1. Redis 写入（legacy，不限流）
        self.redis.setex(f"download_progress:{self.task_id}", 3600, json.dumps({
            "percent": raw_percent, "speed": speed_str, "status": "downloading"
        }))

        # 2. Supabase 写入（stage 映射后的整体百分比）
        overall_percent = self._stage_offset + int(raw_percent * self._stage_weight / 100)
        overall_percent = min(max(overall_percent, 0), 99)  # 100 留给 complete()
        await self.unified_tracker.update_progress(
            self.unified_task_id, overall_percent, speed=int(self._speed)
        )
```

### 阶段映射示例

当同时下载 Video + Music + Cover 时：

```
0%   3%                          72%  87%  95% 100%
|----|-----------------------------|----|----|---|
init       Video (70%)          Music  Cover  Done
            ↑                     ↑      ↑
      set_stage(3, 69)    set_stage(72, 15)  set_stage(87, 8)
```

**计算逻辑** (`_calc_stage_ranges`):

```python
raw_weights = {'video': 70, 'music': 15, 'cover': 15}
# 按需过滤，缩放到 3%~95% 区间 (92 个百分点)
# video: offset=3, weight=64
# music: offset=67, weight=14
# cover: offset=81, weight=14
```

## 3. 后端：阶段强制更新

**文件**: `backend/app/tasks/download_tasks.py` L233-252

在阶段边界强制写入 DB，绕过 TaskTracker 的 1s 限流：

```python
def _force_progress(tracker, progress, subtitle=None):
    """用于阶段切换点，确保用户看到有意义的进度"""
    # 清除限流记录 → 下次写入立即生效
    tracker.unified_tracker._last_progress.pop(tracker.unified_task_id, None)
    run_async(tracker.unified_tracker.update_progress(
        tracker.unified_task_id,
        min(max(progress, 0), 99),
        subtitle=subtitle,  # e.g. "Downloading audio…"
    ))
```

**调用时机**（以 Douyin 视频为例）：
```
_force_progress(tracker, 3,  "Downloading video...")   # 视频阶段开始
  → download_video_by_platform_id(tracker=tracker)     # 流式下载，逐 chunk 更新
_force_progress(tracker, 72)                           # 视频阶段结束
_force_progress(tracker, 72, "Downloading audio...")    # 音频阶段开始
  → download_music_by_platform_id(...)                 # 无 tracker，无逐 chunk 更新
_force_progress(tracker, 87)                           # 音频阶段结束
_force_progress(tracker, 87, "Downloading cover...")    # 封面阶段开始
  → download_cover_by_platform_id(...)                 # 无 tracker
_force_progress(tracker, 95)                           # 封面阶段结束
tracker_unified.complete(unified_task_id)              # → progress=100
```

## 4. 后端：TaskTracker（DB 写入层）

**文件**: `backend/app/services/task_tracker.py` L86-118

```python
class TaskTracker:
    THROTTLE_INTERVAL = 1.0  # 每个任务最多 1 次/秒

    async def update_progress(self, task_id, progress, *, speed=None, subtitle=None):
        # 限流检查
        now = time.time()
        if now - self._last_progress.get(task_id, 0) < self.THROTTLE_INTERVAL:
            return  # 跳过
        self._last_progress[task_id] = now

        # Supabase REST API → PostgreSQL UPDATE
        updates = {"progress": min(max(progress, 0), 100), "status": "processing"}
        if speed is not None:
            updates["speed"] = speed
        if subtitle is not None:
            updates["subtitle"] = subtitle
        await client.table("unified_tasks").update(updates).eq("id", task_id).execute()
```

**限流机制**：
- TaskTracker 内部: 1 次/秒 per task（可被 `_force_progress` 绕过）
- UnifiedProgressTracker 内部: 0.5 秒 per update（仅限流式下载场景）
- 实际 DB 写入频率: ~1 次/秒（TaskTracker 的 1s 限流是最终瓶颈）

## 5. 后端：HTTP 流式下载（逐 chunk 回调）

**文件**: `backend/app/services/downloader.py` L230-257

```python
async with client.stream("GET", url, ...) as response:
    total = int(response.headers.get("content-length", 0))
    downloaded = 0

    async with aiofiles.open(file_path, mode="wb") as f:
        async for chunk in response.aiter_bytes(chunk_size=65536):  # 64KB chunks
            await f.write(chunk)
            downloaded += len(chunk)
            await progress_tracker.update(downloaded, total)
            #     ↑ 调用 UnifiedProgressTracker.update()
            #       → Redis + Supabase (throttled)
```

**注意**: 只有 `download_video_by_platform_id` 传递了 `progress_tracker`。
`download_music_by_platform_id` 和 `download_cover_by_platform_id` **不接受 tracker**，
所以音频/封面阶段只有 `_force_progress` 的阶段起止更新，没有逐 chunk 进度。

## 6. 数据库：unified_tasks 表

```sql
CREATE TABLE unified_tasks (
    id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id     UUID NOT NULL REFERENCES auth.users(id),
    task_type   TEXT NOT NULL,          -- 'download' | 'upload' | 'transcode' | 'ai_pipeline' | …
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending → processing → completed/failed/cancelled
    title       TEXT,                   -- 显示标题 (e.g. "和Vera Blue一起在澳洲…")
    subtitle    TEXT,                   -- 阶段描述 (e.g. "Downloading video…")
    progress    INT DEFAULT 0,          -- 0-100
    speed       BIGINT,                 -- bytes/sec
    total_bytes BIGINT,
    error_msg   TEXT,
    resource_id TEXT,
    media_id    TEXT,
    celery_task_id TEXT,
    dedup_key   TEXT,
    metadata    JSONB DEFAULT '{}',
    created_at  TIMESTAMPTZ DEFAULT now(),
    started_at  TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    updated_at  TIMESTAMPTZ DEFAULT now()
);
```

**Realtime 触发**: Supabase 自动监听此表的 INSERT/UPDATE/DELETE，通过 `pg_notify` 推送给订阅的 WebSocket 客户端。

## 7. 前端：Supabase Realtime 订阅

**文件**: `frontend/contexts/TaskManagerContext.tsx` L233-266

```typescript
const channel = supabase
  .channel(`user-tasks-${currentUserId}`)
  .on('postgres_changes', {
    event: 'INSERT',
    schema: 'public',
    table: 'unified_tasks',
    filter: `user_id=eq.${currentUserId}`,
  }, (payload) => {
    dispatch({ type: 'INSERT', task: payload.new as UnifiedTask });
  })
  .on('postgres_changes', {
    event: 'UPDATE',           // ← 进度更新走这里
    schema: 'public',
    table: 'unified_tasks',
    filter: `user_id=eq.${currentUserId}`,
  }, (payload) => {
    dispatch({ type: 'UPDATE', task: payload.new as UnifiedTask });
    //                                 ↑ payload.new 包含最新的 progress, speed, subtitle
  })
  .subscribe();
```

**初始加载**: 页面加载时调用 `GET /api/v1/task-manager/tasks?limit=200` 获取历史任务列表。

## 8. 前端：进度条 UI 渲染

**文件**: `frontend/components/TopBar.tsx` L234-284

```tsx
// 百分比文字
{task.progress > 0 && (
  <span className="text-[10px] text-zinc-500">{task.progress}%</span>
)}

// 速度文字
{task.speed != null && task.speed > 0 && (
  <span className="text-[10px] text-zinc-600">{formatSpeed(task.speed)}</span>
)}

// 阶段描述
{task.subtitle && (
  <span className="text-[10px] text-zinc-600 truncate">{task.subtitle}</span>
)}

// 进度条
{(task.status === 'pending' || task.status === 'processing') && (
  <div className="mt-1.5 h-1 bg-zinc-800 rounded-full overflow-hidden">
    <div
      className={`h-full rounded-full transition-all duration-300 ${progressBarColor(task.status)}`}
      style={{ width: `${Math.max(task.progress, task.status === 'processing' ? 2 : 0)}%` }}
    />
  </div>
)}
```

## 9. 前端：TaskManagerContext 类型定义

**文件**: `frontend/contexts/TaskManagerContext.tsx` L23-49

```typescript
export interface UnifiedTask {
  id: string;
  task_type: TaskType;       // 'download' | 'upload' | 'transcode' | …
  status: TaskStatus;        // 'pending' | 'processing' | 'completed' | 'failed'
  title: string;
  subtitle?: string;         // 阶段描述
  progress: number;          // 0-100
  speed?: number;            // bytes/sec
  total_bytes?: number;
  error_msg?: string;
  // ...
}
```

---

## 限流层级总结

```
Layer 1: downloader.py streaming loop
         每 64KB chunk 触发一次 tracker.update()
         ↓
Layer 2: UnifiedProgressTracker.update()
         内部限流 0.5s → 实际调用频率 ~2次/秒
         ↓
Layer 3: TaskTracker.update_progress()
         内部限流 1.0s → 实际 DB 写入 ~1次/秒
         ↓
Layer 4: Supabase PostgreSQL
         UPDATE unified_tasks → pg_notify → Realtime
         ↓
Layer 5: Frontend WebSocket
         收到 UPDATE 事件 → React re-render
```

**最终效果**: 前端每 ~1 秒收到一次进度更新，包含 progress(%)、speed(B/s)、subtitle(阶段描述)。

## Content-Length=0 处理

某些 CDN（如抖音）对 streaming GET 响应不返回 `Content-Length`（使用 chunked transfer encoding）。

### 第一层 fallback：HEAD expected_size（downloader.py）

```python
total = int(response.headers.get("content-length", 0))
if total == 0 and expected_size > 0:
    total = expected_size  # Use HEAD's content-length as fallback
```

在 streaming 前先做 HEAD 请求获取 `expected_size`，如果 streaming 的 `content-length=0`，用 HEAD 的值回填。

### 第二层 fallback：渐近模拟进度（download_tasks.py）

当 HEAD 和 GET 都没有返回 Content-Length（`total=0`）时，用已下载字节数做渐近进度模拟：

```python
if total > 0:
    raw_percent = int((downloaded / total) * 100)
else:
    # 渐近公式: 90 * mb / (mb + 5)
    mb = downloaded / (1024 * 1024)
    raw_percent = min(int(90 * mb / (mb + 5)), 90)
```

| 已下载 | 模拟进度 |
|--------|----------|
| 1 MB   | 15%      |
| 5 MB   | 45%      |
| 10 MB  | 60%      |
| 20 MB  | 72%      |
| 50 MB  | 82%      |
| 100 MB | 86%      |

进度渐近趋向 90%，最终由 `complete()` 跳到 100%。比卡在 3% 不动好得多。

---

## 已知限制

1. **音频/封面下载无逐 chunk 进度** — `download_music_by_platform_id` 和 `download_cover_by_platform_id` 不接受 progress_tracker，只有阶段起止的离散更新
2. **PostgreSQL 写入开销** — 每秒 1 次 UPDATE + WAL + pg_notify，当前规模（< 10 并发）完全没问题，大规模需迁移到 Redis Pub/Sub
3. **Redis 双写冗余** — 目前同时写 Redis 和 Supabase，Redis 数据实际已不被前端使用（legacy），可移除
