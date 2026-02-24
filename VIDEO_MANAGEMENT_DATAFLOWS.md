# MediaHub 视频管理数据流映射

**最后更新**: 2026-02-19  
**范围**: Web 下载、文件上传、任务管理、删除/回收流程

---

## 1. Web 下载流 (Parser → RipVault)

将抖音 URL 解析为 `parsed_media` 记录，通过 Celery 异步下载，自动创建资源库条目。

### 数据库表涉及
- `parsed_media` — 存储解析的视频元数据（platform_id, status, file_path）
- `resources` — 用户资源库条目（source_type='web', media_id FK）
- `resource_items` — 资源与范围的关联（user_id/team_id）
- `versions` — 资源版本记录（HLS 转码后的变体）
- `unified_tasks` — 跨系统任务追踪（celery_task_id, status）

### 关键文件与行号

#### 后端入口：`backend/app/api/media_router.py`
| 函数 | 行号 | 功能 |
|------|------|------|
| `fetch_videos()` | 85-150 | POST /videos/fetch 单个视频解析入口 |
| 内部：URL 提取 | 95-105 | 从分享链接/直链提取视频 ID |
| 内部：平台检测 | 106-115 | 判断是抖音、YouTube 等平台 |
| 内部：points 检查 | 120-130 | 检查用户是否有足够积分 |
| 内部：消费积分 | 131-135 | 调用 `PointsService.consume_points()` |
| `fetch_videos_batch()` | 160-240 | POST /videos/fetch/batch 批量解析 |

#### 后端业务逻辑：`backend/app/services/media_service.py`
| 函数 | 行号 | 功能 |
|------|------|------|
| `parse_and_save()` | 200-290 | 完整流程：解析 → 保存元数据 → 分派下载 |
| `save_metadata_only()` | 438-551 | **关键**：保存只读元数据到 parsed_media |
| 内部：去重检查 | 445-480 | 检查全局是否已下载过该 media_id |
| 内部：零拷贝复用 | 481-495 | 若已存在文件，复用路径，标记 COMPLETED |
| 内部：新记录创建 | 496-520 | 新视频则创建 parsed_media, 状态=PENDING |
| `_execute_downloads()` | 291-374 | 分派 Celery 下载任务（视频/音乐/封面） |
| `_create_resource_record()` | 376-429 | **关键**：下载完成后自动创建资源库条目 |
| 内部：元数据提取 | 385-410 | 从 parsed_media 提取 duration, title 等 |
| 内部：Resource 创建 | 411-420 | 创建 Resource 记录 (source_type='web') |
| 内部：ResourceItem 创建 | 421-429 | 关联到用户的 personal scope |

#### 后端异步任务：`backend/app/tasks/download_tasks.py`
| 函数 | 行号 | 功能 |
|------|------|------|
| `download_media_task()` | 119-200+ | Celery shared_task：主下载任务 |
| 内部：TaskTracker 创建 | 125-140 | 在 Supabase unified_tasks 中创建追踪记录 |
| 内部：进度跟踪初始化 | 141-155 | 结合 Redis + TaskTracker 双轨道进度 |
| 内部：下载分派 | 156-180 | 根据 media_type (0/2/4/61/68) 调用相应下载服务 |
| 内部：转码链 | 181-190 | 成功后链式调用 `maybe_trigger_transcode()` |
| 内部：AI 管道 | 191-200+ | 链式调用 AI 自动转录/摘要（如启用） |

#### 后端资源创建：`backend/app/services/resources_service.py`
| 函数 | 行号 | 功能 |
|------|------|------|
| `create_from_media()` | 297-363 | **去重逻辑**：根据 media_id 复用资源 |
| 内部：存在性检查 | 305-320 | 按 media_id 检查资源是否已存在 |
| 内部：零拷贝关联 | 321-340 | 若属于其他用户，仅创建 resource_item (ref) |
| 内部：新资源创建 | 341-363 | 若不存在，创建新 Resource + ResourceItem |

### 操作序列

```
1. 用户在 RipVaultView 输入抖音链接
   ↓
2. [Frontend] parserService.parseShareLink(url)
   → POST /api/v1/videos/fetch
   ↓
3. [Backend] media_router.fetch_videos()
   ├─ 提取视频 ID 和平台
   ├─ 检查用户积分（402 积分不足）
   ├─ 消费积分（按视频数量）
   ├─ 调用 MediaService.parse_and_save()
   └─ 立即返回 { id, platform_id, download_task_id }
   ↓
4. [Backend] MediaService.save_metadata_only()
   ├─ 调用解析器（LightHTTP 或 DrissionPage）
   ├─ 去重检查：按 media_id 查找已下载记录
   │  ├─ 若存在 & 文件存在 → 零拷贝复用，status=COMPLETED
   │  └─ 若不存在 → 创建 parsed_media, status=PENDING
   └─ 返回 media_id & dedup_hit 标志
   ↓
5. [Backend] MediaService._execute_downloads()
   ├─ 根据 media_type 判断下载类型：
   │  ├─ 0/4/61 → 视频
   │  ├─ 2/68 → 图片
   │  └─ 对应音乐/封面
   ├─ 创建 TaskGroup (并行下载)
   └─ 每个下载调用 DownloaderService.download_*()
   ↓
6. [Celery] download_media_task (异步)
   ├─ 创建 TaskTracker (Supabase)
   ├─ 创建 UnifiedProgressTracker (Redis + DB)
   ├─ 执行下载，定期更新进度
   ├─ 下载完成 → 链式调用 maybe_trigger_transcode()
   └─ 成功 → 更新 parsed_media status=COMPLETED
   ↓
7. [Backend] MediaService._create_resource_record()
   ├─ 按 parsed_media.id 查询已完成的下载
   ├─ 提取元数据（duration, title, cover）
   ├─ 调用 ResourcesService.create_from_media()
   │  ├─ 按 media_id 检查重复
   │  ├─ 若重复 → 零拷贝（仅 resource_item）
   │  └─ 若新增 → 创建 Resource (source_type='web')
   └─ 创建 ResourceItem (user_id, personal scope)
   ↓
8. [Frontend] 轮询 /api/v1/download-progress/{download_task_id}
   ├─ 读取 unified_tasks.status 和进度百分比
   ├─ 下载完成时自动加载到 RipVaultView
   └─ 用户可查看缩略图和元数据
```

### 已知差距与不一致

1. **积分退款未实现**
   - 当解析失败或下载失败时，应退款积分
   - 当前仅在消费时扣除，失败时未返还
   - 影响：用户反复尝试解析同一链接会累积扣费

2. **去重逻辑分散**
   - media_service.save_metadata_only() 有一层去重（检查 parsed_media）
   - resources_service.create_from_media() 又有一层去重（检查 resource）
   - 两层检查存在冗余，且标准不一致（一个按 media_id，一个按 platform_id）

3. **资源创建失败处理**
   - _create_resource_record() 仅记录警告，不会重试
   - 若创建 ResourceItem 失败，用户看不到已下载的视频
   - 应添加重试机制或死信队列

4. **进度追踪不同步**
   - Redis 存短期进度（任务执行期间）
   - TaskTracker 存永久进度（长期查询）
   - 若 Redis 因重启丢失，前端无法恢复进度

---

## 2. 文件上传流 (Upload → Resources)

用户选择本地文件上传，创建资源库条目和版本记录。

### 数据库表涉及
- `resources` — 资源主记录（source_type='upload', file_path）
- `resource_items` — 资源与范围关联
- `versions` — 版本记录（V1, V2, ...）
- `unified_tasks` — 上传任务追踪

### 关键文件与行号

#### 后端入口：`backend/app/api/resources_router.py`
| 函数 | 行号 | 功能 |
|------|------|------|
| `upload_resource()` | 133-196 | POST /resources/upload 文件上传入口 |
| 内部：参数验证 | 135-145 | scope_type, scope_id, folder_id 校验 |
| 内部：TaskTracker 创建 | 146-155 | 创建 unified_task 追踪记录 |
| 内部：委托服务 | 156-170 | 调用 ResourcesService.upload_resource() |
| 内部：缩略图生成 | 171-185 | 后台触发 generate_thumbnail_task() |
| 内部：响应 | 186-196 | 返回完整 Resource 对象 |

#### 后端业务逻辑：`backend/app/services/resources_service.py`
| 函数 | 行号 | 功能 |
|------|------|------|
| `upload_resource()` | 36-127 | **主上传流程** |
| 内部：文件名清理 | 40-50 | 删除路径字符，避免目录遍历 |
| 内部：SHA-256 哈希 | 51-60 | 计算文件内容哈希（用于去重） |
| 内部：MIME 分类 | 61-75 | 判断 video/audio/image/document |
| 内部：Resource 创建 | 76-85 | 创建 DB 记录，获取 resource_id |
| 内部：文件保存 | 86-100 | 写入 teams/{scope_id}/uploads/{resource_id}/v1/{filename} |
| 内部：元数据提取 | 101-110 | 调用 ffprobe 提取视频/音频信息 |
| 内部：Version 记录 | 111-115 | 创建 versions 表记录 (V1) |
| 内部：ResourceItem 创建 | 116-120 | 关联资源到 scope 和 folder |
| 内部：HLS 转码触发 | 121-127 | 后台任务：maybe_trigger_transcode() |

### 操作序列

```
1. 用户在 ResourcesView 选择"上传"，选择本地文件
   ↓
2. [Frontend] ResourceUploadModal 显示文件选择
   ├─ 用户选择文件、输入标题
   └─ 点击"上传"
   ↓
3. [Frontend] resourceService.uploadResource(file, scope_type, scope_id, folder_id)
   ├─ 创建 FormData，包含 file + metadata
   ├─ XMLHttpRequest.upload.progress 监听上传进度
   └─ POST /api/v1/resources/upload
   ↓
4. [Backend] resources_router.upload_resource()
   ├─ 验证 scope_type (personal/team) & scope_id
   ├─ 创建 unified_task 追踪记录
   └─ 委托 ResourcesService.upload_resource()
   ↓
5. [Backend] ResourcesService.upload_resource()
   ├─ 清理文件名（移除 /, \, .. 等）
   ├─ 计算 SHA-256 哈希（用于去重检查）
   ├─ 判断 MIME 类型
   │  ├─ video/* → 提取 duration, resolution, codec
   │  ├─ audio/* → 提取 duration, bitrate
   │  ├─ image/* → 提取 width, height
   │  └─ document → 保留默认值
   ├─ 创建 Resource 记录：
   │  ├─ title, description (来自用户输入)
   │  ├─ source_type = 'upload'
   │  ├─ file_path = teams/{scope_id}/uploads/{resource_id}/v1/{filename}
   │  └─ mime_type (video/mp4, image/jpeg 等)
   ├─ 保存文件到磁盘：
   │  ├─ 读取上传的二进制数据
   │  ├─ 创建目录树 teams/{scope_id}/uploads/{resource_id}/v1/
   │  └─ 写入 {filename}
   ├─ 若为视频，调用 ffprobe 提取：
   │  ├─ duration (秒)
   │  ├─ resolution (1920x1080)
   │  ├─ codec (h264, vp9 等)
   │  └─ bitrate
   ├─ 创建 versions 记录：
   │  ├─ resource_id, version_number=1
   │  ├─ file_path 指向 v1 目录
   │  └─ status = 'ready' (表示已可用)
   ├─ 创建 resource_items：
   │  ├─ resource_id
   │  ├─ scope_type, scope_id (personal/{user_id} 或 team/{team_id})
   │  ├─ folder_id (若指定)
   │  └─ is_favorite=false
   └─ 后台触发 maybe_trigger_transcode() → HLS 转码
   ↓
6. [Background] HLS 转码任务（若为视频）
   ├─ 使用 ffmpeg 转码为 HLS 格式
   ├─ 生成 playlist.m3u8 和分片 segment_*.ts
   ├─ 存储到 versions/{resource_id}/v1/hls/
   └─ 更新 versions.status = 'processed'
   ↓
7. [Background] generate_thumbnail_task()
   ├─ 提取视频首帧或用户指定时间点的截图
   ├─ 缩放为 320x180 (16:9 比例)
   ├─ 生成 WebP 格式缩略图
   └─ 存储到 resources/{resource_id}/thumbnail.webp
   ↓
8. [Frontend] XMLHttpRequest.progress 更新进度条
   ├─ 显示已上传字节 / 总字节
   ├─ 估算剩余时间
   └─ 上传完成时显示"正在处理..."
   ↓
9. 返回 Resource 对象
   ├─ id, title, file_path, mime_type
   ├─ duration (仅视频)
   ├─ thumbnail_url
   └─ versions[0] (V1 记录)
```

### 已知差距与不一致

1. **上传去重逻辑缺失**
   - 计算了 SHA-256 哈希但未利用
   - 若用户上传相同文件两次，不会复用，而是创建两个 Resource
   - 应在 upload_resource() 中按哈希检查重复

2. **HLS 转码失败处理**
   - maybe_trigger_transcode() 异步，不阻塞上传响应
   - 若转码失败，用户无通知机制
   - versions.status 可能停留在 'processing' 状态

3. **大文件处理风险**
   - 未检查磁盘空间
   - 未设置文件大小上限（理论上可上传无限大文件）
   - 可能导致服务器存储溢出

4. **同步元数据提取**
   - ffprobe 在主请求线程中执行（行 101-110）
   - 大视频文件的 ffprobe 可能需要数秒，阻塞响应
   - 应改为后台异步提取

---

## 3. 任务/下载管理器

跨系统任务追踪、Celery 队列管理、前端进度轮询。

### 数据库表涉及
- `unified_tasks` — 所有类型任务的统一追踪表
- `task_logs` — 任务执行日志（可选）
- Redis `download:*` — 短期进度数据（expires after 24h）

### 关键文件与行号

#### 后端任务定义：`backend/app/tasks/download_tasks.py`
| 函数 | 行号 | 功能 |
|------|------|------|
| `download_media_task()` | 119-200+ | Celery shared_task 主任务 |
| 参数与配置 | 119-122 | max_retries=3, retry_delay=30s, bind=True |
| 内部：TaskTracker 初始化 | 125-140 | 创建 unified_tasks 记录，保存 celery_task_id |
| 内部：进度初始化 | 141-155 | UnifiedProgressTracker(celery_id, redis_client) |
| 内部：下载执行 | 156-180 | 根据 media_type 调用 DownloaderService |
| 内部：进度更新 | 161-175 | progress_tracker.update(percent, message) |
| 内部：转码链 | 181-190 | .then(maybe_trigger_transcode.s(...)) |
| 内部：AI 链 | 191-200+ | .then(maybe_trigger_ai_pipeline.s(...)) |
| 重试处理 | 200+ | 失败时自动重试，max_retries=3 |

#### 任务追踪服务：`backend/app/services/task_tracker.py`
| 函数 | 行号 | 功能 |
|------|------|------|
| `TaskTracker.__init__()` | 15-35 | 初始化，创建 Supabase unified_tasks 记录 |
| `TaskTracker.update_status()` | 40-60 | 更新 status (pending/processing/success/failed) |
| `TaskTracker.update_progress()` | 65-80 | 保存进度百分比 + message 到 DB |
| `TaskTracker.log_error()` | 85-100 | 记录错误信息，更新 error_details JSON |
| `UnifiedProgressTracker` | 105-150 | 组合 Redis (短期) + TaskTracker (长期) |
| 内部：update() | 110-135 | 同时更新 Redis 和 DB |
| 内部：get() | 136-150 | 查询当前进度 (Redis 优先，回落 DB) |

#### 后端 API 端点：`backend/app/api/tasks_router.py`
| 函数 | 行号 | 功能 |
|------|------|------|
| `get_download_progress()` | 50-100 | GET /download-progress/{task_id} |
| 内部：查询 unified_tasks | 60-75 | 按 id 或 celery_task_id 查询记录 |
| 内部：返回进度 | 76-100 | status, progress_percent, message, error_details |
| `get_task_history()` | 110-160 | GET /tasks/history 获取历史任务列表 |

#### 前端服务：`frontend/services/downloadService.ts`
| 函数 | 行号 | 功能 |
|------|------|------|
| `pollDownloadProgress()` | 45-95 | 轮询 /api/v1/download-progress/{task_id} |
| 内部：轮询间隔 | 50 | 500ms 轮询一次 |
| 内部：超时 | 60 | 若 24h 未更新则停止轮询 |
| 内部：回调 | 75-95 | onProgress(percent, message), onComplete(result) |
| `cancelDownload()` | 100-115 | 调用 Celery revoke() 取消任务 |

#### 前端 UI 组件：`frontend/components/DownloadManager.tsx`
| 函数 | 行号 | 功能 |
|------|------|------|
| `DownloadManager` | 30-80 | 显示下载队列，实时进度条 |
| `useDownloadProgress()` | 90-150 | React Hook：管理进度轮询逻辑 |
| 内部：轮询启动 | 100-110 | useEffect 启动 pollDownloadProgress() |
| 内部：清理 | 140-150 | 组件卸载时停止轮询 |
| `RipVaultView` 集成 | 250-280 | 下载完成时自动刷新媒体库列表 |

### 操作序列

```
1. 用户点击"下载"，触发 media_router.fetch_videos()
   ↓
2. 后端返回 { download_task_id, ... }
   ↓
3. [Frontend] 启动进度轮询
   ├─ 存储 download_task_id 到本地状态
   ├─ 调用 downloadService.pollDownloadProgress(task_id)
   └─ 每 500ms 查询一次进度
   ↓
4. [Celery Worker] 接收 download_media_task
   ├─ 绑定 self (task context)
   ├─ 分配唯一 celery_task_id
   └─ 从队列中取出消息
   ↓
5. [Task Execution] 初始化追踪
   ├─ TaskTracker(task_id, task_type='download')
   ├─ 创建 unified_tasks 记录：
   │  ├─ id = download_task_id
   │  ├─ celery_task_id
   │  ├─ task_type = 'download'
   │  ├─ status = 'pending'
   │  ├─ progress_percent = 0
   │  └─ created_at = now()
   ├─ Redis 初始化：
   │  ├─ Key: download:{task_id}
   │  ├─ Value: { status: 'pending', percent: 0 }
   │  └─ TTL: 24 hours
   └─ UnifiedProgressTracker(celery_id, redis_client)
   ↓
6. [Download Execution]
   ├─ 更新 status → 'processing'
   ├─ DownloaderService.download_video(media_id, url, ...)
   │  └─ 循环读取响应流：
   │     ├─ 每读取 1MB，计算百分比
   │     ├─ 调用 progress_tracker.update(percent=25, message='Downloading...')
   │     │  ├─ 更新 Redis: download:{task_id} → { percent: 25, ... }
   │     │  └─ 更新 DB: unified_tasks.progress_percent = 25
   │     └─ 继续读取
   ├─ 写入文件到磁盘
   ├─ 更新 parsed_media.file_path, status='completed'
   └─ progress_percent → 100%
   ↓
7. [Frontend Polling] 每 500ms 轮询
   ├─ GET /api/v1/download-progress/{task_id}
   ├─ 查询 unified_tasks：
   │  ├─ status = 'processing'
   │  └─ progress_percent = 25, 50, 75, 100
   ├─ 更新 UI 进度条
   └─ 若 status='success'，停止轮询，自动刷新媒体库
   ↓
8. [Task Chaining] 下载成功后
   ├─ .then(maybe_trigger_transcode.s(...))
   │  └─ 若为视频，触发 HLS 转码
   ├─ .then(maybe_trigger_ai_pipeline.s(...))
   │  └─ 若启用，触发 AI 转录/摘要
   └─ 最后更新 unified_tasks.status = 'success'
   ↓
9. [Error Handling]
   ├─ 若下载失败（网络超时、文件损坏等）
   ├─ catch 块捕获异常
   ├─ 记录错误到 unified_tasks.error_details
   ├─ 自动重试（max_retries=3，延迟 30s）
   ├─ 若重试 3 次仍失败，更新 status='failed'
   ├─ Redis 记录 TTL 后自动删除
   └─ 前端显示"下载失败，请重试"
   ↓
10. [User Cancellation]
   ├─ 用户点击"取消下载"
   ├─ Frontend 调用 downloadService.cancelDownload(task_id)
   ├─ 发送 POST /api/v1/tasks/{task_id}/cancel
   ├─ Backend 执行 celery_app.control.revoke(celery_task_id)
   ├─ Celery Worker 接收 revoke，停止当前 task
   └─ 更新 unified_tasks.status = 'cancelled'
```

### 已知差距与不一致

1. **Redis 短期数据丢失风险**
   - 若 Redis 因重启丢失，前端轮询会查不到进度
   - 应回落到 DB 查询，但当前未实现
   - 表现：用户看到进度卡在某个百分比

2. **进度精度问题**
   - 文件大小估算不准（Content-Length 可能缺失或错误）
   - 百分比计算可能非线性（如解析时间长，下载时间短）
   - 应在 message 中显示实际字节数而非百分比

3. **任务查询性能**
   - 每次轮询都查询 Supabase（网络延迟 ~100ms）
   - 应优先查 Redis（本地，~1ms）
   - 当前仅在 UnifiedProgressTracker 内部做了优化，API 层未优化

4. **过期任务清理机制缺失**
   - unified_tasks 无 TTL，表会无限增长
   - 应定期清理 7 天前的已完成任务
   - Redis 虽有 TTL，但 DB 记录永久保留

5. **并发任务限制缺失**
   - 用户可无限提交下载任务
   - 应限制：最多同时 3-5 个下载，其余排队

---

## 4. 删除/回收流程

软删除（移至回收站）、永久删除、孤立资源自动回收、级联删除逻辑。

### 数据库表涉及
- `resources` — 主记录（is_trashed, trashed_at）
- `resource_items` — 范围关联（is_deleted）
- `parsed_media` — 解析元数据（可能被级联删除）
- `versions` — 版本历史（随 resource 删除）

### 关键文件与行号

#### 前端 UI：`frontend/components/RipVaultView.tsx`
| 函数 | 行号 | 功能 |
|------|------|------|
| `RipVaultView` | 39-100 | 主组件，显示解析视频列表 |
| `useState: selection` | 45-50 | 多选状态（Set<string>）|
| `handleSelectionChange()` | 105-130 | 单选/Shift+Click 范围选/Ctrl 切换 |
| `handleBatchDelete()` | 158-181 | 选中多项后，批量删除 |
| 内部逻辑 | 165-175 | 循环调用 trashResourceByPlatformId() |
| 内部刷新 | 176-181 | 成功后清空 selection，刷新列表 |
| `handleDeleteSingle()` | 183-200 | 单项删除（右键菜单） |
| 内部逻辑 | 190-195 | 调用 trashResourceByPlatformId() |
| 内部反馈 | 196-200 | 显示 toast，刷新列表 |

#### 前端服务：`frontend/services/resourceService.ts`
| 函数 | 行号 | 功能 |
|------|------|------|
| `trashResourceByPlatformId()` | 786-805 | 软删除：移至回收站 |
| 内部请求 | 790-800 | POST /api/v1/resources/by-platform-id/{platformId}/trash |
| 参数 | 795 | scope_type, scope_id (可选) |
| 返回 | 804-805 | { success: boolean, resource_id } |
| `unlinkResourceByPlatformId()` | 810-829 | 删除 resource_item 关联 |
| 内部请求 | 815-825 | DELETE /api/v1/resources/by-platform-id/{platformId} |
| 触发孤立检测 | 824-829 | 若无其他 resource_items，自动 trash resource |
| `permanentDeleteResource()` | 845-870 | 硬删除：从回收站永久删除 |
| 内部请求 | 850-865 | DELETE /api/v1/resources/{resourceId}/permanent |
| 删除文件 | 860-865 | 物理删除磁盘文件和所有版本 |

#### 后端 API：`backend/app/api/resources_router.py`
| 函数 | 行号 | 功能 |
|------|------|------|
| `trash_resource()` | 250-310 | POST /resources/by-platform-id/{platform_id}/trash |
| 内部参数 | 255-265 | scope_type, scope_id 校验 |
| 内部查询 | 270-285 | 按 platform_id 查找 resource（权限检查） |
| 内部更新 | 286-295 | 调用 ResourcesService.trash_resource() |
| 返回结果 | 305-310 | { resource_id, trashed_at } |
| `unlink_resource_item()` | 320-380 | DELETE /resources/by-platform-id/{platform_id} |
| 内部参数 | 325-335 | scope_type, scope_id 必需 |
| 内部查询 | 340-360 | 按 resource_id + scope 查找 resource_item |
| 内部删除 | 365-375 | 调用 repo.delete_resource_item() |
| 孤立检测触发 | 376-380 | 触发 orphan_detection_trigger (DB 触发器) |
| `permanent_delete_resource()` | 390-450 | DELETE /resources/{resource_id}/permanent |
| 内部权限检查 | 395-405 | 确保用户有删除权限 |
| 内部查询 | 410-420 | 确保资源状态 is_trashed=true |
| 内部委托 | 425-430 | 调用 ResourcesService.permanent_delete() |
| 返回结果 | 445-450 | { success: true } |

#### 后端服务：`backend/app/services/resources_service.py`
| 函数 | 行号 | 功能 |
|------|------|------|
| `trash_resource()` | 387-400 | 软删除实现 |
| 内部更新 | 390-400 | is_trashed=true, trashed_at=now() |
| 返回结果 | 400 | 更新成功的 Resource 对象 |
| `permanent_delete()` | 414-429 | 硬删除实现 |
| 内部步骤 1 | 415-420 | 删除物理文件：_delete_physical_files() |
| 内部步骤 2 | 421-425 | 删除 DB resource 记录 |
| 内部步骤 3 | 426-429 | 若 media_id 存在，检查孤立 parsed_media |
| 返回结果 | 429 | boolean success |
| `_delete_physical_files()` | 457-493 | 删除所有版本和覆盖文件 |
| 内部逻辑 1 | 460-475 | 遍历 versions，删除 v1, v2, ... 目录 |
| 内部逻辑 2 | 476-485 | 删除 thumbnail 文件 |
| 内部逻辑 3 | 486-493 | 删除封面 (cover.jpg) |

#### 数据库触发器：`supabase/migrations/xxx_orphan_detection.sql`
| 触发器 | 事件 | 功能 |
|--------|------|------|
| `on_resource_item_deleted` | DELETE on resource_items | 检查删除后 resource 是否孤立 |
| 内部逻辑 | - | 若 resource 无任何 resource_items，设置 is_trashed=true |

### 操作序列

#### 场景 1：软删除单个资源

```
1. 用户在 RipVaultView 右键点击视频，选择"删除"
   ↓
2. [Frontend] handleDeleteSingle(platform_id)
   ├─ 显示确认对话框："确定删除此视频？"
   └─ 用户确认
   ↓
3. [Frontend] resourceService.trashResourceByPlatformId(platform_id, scope_type, scope_id)
   ├─ 构造请求 body { scope_type: 'personal', scope_id: user_id }
   └─ POST /api/v1/resources/by-platform-id/{platform_id}/trash
   ↓
4. [Backend] resources_router.trash_resource()
   ├─ 验证 scope_type & scope_id
   ├─ 按 platform_id 查询 resources (WHERE source_type='web' AND platform_id=...)
   ├─ 权限检查：确保 resource 属于当前用户 scope
   └─ 调用 ResourcesService.trash_resource(resource_id)
   ↓
5. [Backend] ResourcesService.trash_resource()
   ├─ 更新 resources 表：
   │  ├─ is_trashed = true
   │  ├─ trashed_at = now()
   │  └─ WHERE resource_id = ...
   └─ 返回更新后的 Resource 对象
   ↓
6. [Frontend] 更新 UI
   ├─ 从 RipVaultView 列表移除该项
   ├─ 显示 toast: "已删除"
   ├─ 清空 selection 状态
   └─ 刷新列表 (重新查询 /api/v1/resources)
```

#### 场景 2：批量删除多个资源

```
1. 用户选择 3 个视频（Ctrl+Click），点击"删除"按钮
   ↓
2. [Frontend] handleBatchDelete()
   ├─ selection = Set { resource_id_1, resource_id_2, resource_id_3 }
   ├─ 显示确认对话框："删除 3 项？"
   └─ 用户确认
   ↓
3. [Frontend] 并行删除请求
   ├─ 循环 selection 中每个 resource_id
   └─ Promise.all([
      trashResourceByPlatformId(platform_id_1),
      trashResourceByPlatformId(platform_id_2),
      trashResourceByPlatformId(platform_id_3)
    ])
   ↓
4-6. [Backend] 重复场景 1 的步骤，每个资源独立软删除
   ↓
7. [Frontend] 所有请求完成
   ├─ 清空 selection
   ├─ 显示 toast: "已删除 3 项"
   └─ 刷新列表
```

#### 场景 3：永久删除（从回收站）

```
1. 用户进入"回收站"（is_trashed=true）
   ↓
2. 用户选择已回收资源，点击"永久删除"
   ↓
3. [Frontend] 显示警告对话框："此操作不可撤销，确定删除？"
   └─ 用户确认
   ↓
4. [Frontend] resourceService.permanentDeleteResource(resource_id)
   └─ DELETE /api/v1/resources/{resource_id}/permanent
   ↓
5. [Backend] resources_router.permanent_delete_resource()
   ├─ 权限检查：确保用户有权删除
   ├─ 状态检查：确保 is_trashed=true （防误删）
   └─ 调用 ResourcesService.permanent_delete(resource_id)
   ↓
6. [Backend] ResourcesService.permanent_delete()
   ├─ 第一步：删除物理文件
   │  ├─ 查询 versions 表，获取所有版本目录
   │  ├─ 遍历 v1, v2, ... 目录
   │  │  ├─ 删除 .ts 分片文件（HLS）
   │  │  ├─ 删除 .m3u8 播放列表
   │  │  └─ 删除原始文件
   │  ├─ 删除 thumbnail.webp
   │  └─ 删除 cover.jpg
   ├─ 第二步：删除 DB 记录
   │  ├─ 删除 versions 表记录
   │  ├─ 删除 resource_items 表记录
   │  └─ 删除 resources 表记录 (硬删除)
   ├─ 第三步：检查 parsed_media 孤立
   │  ├─ 若 resource 有 media_id
   │  ├─ 查询是否有其他 resource 引用该 media_id
   │  └─ 若无，删除 parsed_media 记录
   └─ 返回 success=true
   ↓
7. [Frontend] 列表刷新
   ├─ 从回收站列表移除该项
   ├─ 显示 toast: "已永久删除"
   └─ 若回收站变空，显示"回收站为空"
```

#### 场景 4：孤立资源自动回收

```
1. 假设：
   ├─ 资源 R1 有 media_id M1
   ├─ 用户 A 和 B 都有该资源的 resource_item 引用
   └─ 两个 resource_items 指向同一 R1 (zero-copy)

2. 用户 A 删除 resource_item（调用 unlink）
   └─ DELETE /api/v1/resources/by-platform-id/{platform_id}
   ↓
3. [Backend] resources_router.unlink_resource_item()
   ├─ 查询 resource_items
   │  └─ WHERE resource_id=R1 AND scope_type='personal' AND scope_id=user_a_id
   ├─ 删除该 resource_item 记录
   └─ 触发数据库触发器 on_resource_item_deleted
   ↓
4. [Database] Trigger on_resource_item_deleted
   ├─ 检查 R1 是否仍有其他 resource_items
   ├─ 查询 SELECT COUNT(*) FROM resource_items WHERE resource_id=R1
   │  ├─ 结果=1（用户 B 的 resource_item 仍存在）
   │  └─ 不回收（保持原样）
   ↓
5. 稍后，用户 B 也删除 resource_item
   └─ 重复步骤 2-3
   ↓
6. [Database] Trigger on_resource_item_deleted (第二次)
   ├─ 检查 R1 是否仍有其他 resource_items
   ├─ 查询 SELECT COUNT(*) FROM resource_items WHERE resource_id=R1
   │  ├─ 结果=0（无任何引用）
   │  └─ 自动执行：UPDATE resources SET is_trashed=true WHERE resource_id=R1
   ├─ 自动执行：UPDATE parsed_media SET orphan=true WHERE media_id=M1
   └─ R1 和 M1 都被标记为孤立，可被后续清理任务删除

7. [Cleanup Job] 定期清理孤立资源（每天 00:00）
   ├─ 查询 orphan=true 且 trashed_at < 7 days ago 的记录
   ├─ 调用 permanent_delete() 删除它们
   └─ 释放磁盘空间
```

### 已知差距与不一致

1. **孤立资源清理机制不完整**
   - 触发器设置 orphan=true，但无定期清理 job
   - 孤立资源留在表中，占用磁盘空间
   - 应实现 cronjob：每天清理 7 天前的孤立资源

2. **软删除与永久删除标语不清**
   - 用户界面显示"删除"，但实际是软删除
   - 用户可能不理解资源仍占用磁盘空间
   - 应在 UI 中明确区分"删除"(soft) vs "永久删除"(hard)

3. **并发删除风险**
   - 若用户 A 和 B 同时删除相同 resource_item
   - 竞态条件可能导致触发器逻辑错误
   - 应使用行级锁或原子性更新

4. **文件删除失败处理缺失**
   - _delete_physical_files() 若文件不存在或权限拒绝
   - 仅记录警告，继续删除 DB 记录
   - 导致 DB 与磁盘不同步（幽灵文件）
   - 应在失败时回滚整个删除操作

5. **回收站界面缺失**
   - 当前无独立"回收站"视图
   - 应添加类似操作系统的回收站功能：
     - 查看所有 is_trashed=true 的资源
     - 恢复（untrash）功能
     - 定期自动清理（30 天后）

---

## 数据流关键指标

| 指标 | 值 | 备注 |
|------|-----|------|
| 解析平均延迟 | 2-5s | LightHTTP 模式，浏览器模式 10-20s |
| 下载速度 | 1-10 MB/s | 取决于源站和网络 |
| HLS 转码时间 | 视频长度 + 20% | 例如 10min 视频转码约 12min |
| 缩略图生成时间 | < 1s | WebP 格式缩放 320x180 |
| 积分消费 | 1 point/video | 失败未退款（待修复） |
| 最大上传文件 | 无限制 | 建议设置上限（如 4GB） |
| Celery 重试次数 | 3 次，30s 间隔 | 总耗时最多 2 分钟 |
| 任务追踪 TTL | Redis 24h, DB 永久 | 建议 7 天后自动清理 |
| 并发下载限制 | 无限制 | 建议限制为 5 个并发 |

---

## 总体架构图

```
┌─────────────────────────────────────────────────────────────┐
│                     Frontend (React)                        │
├─────────────────┬─────────────────┬──────────────┬──────────┤
│  RipVaultView   │ ResourcesView    │ Download     │ RecycleBin
│  (Web DL UI)    │ (Upload UI)      │ Manager      │ (Missing)
└────────┬────────┴────────┬────────┴──────┬───────┴──────────┘
         │                 │               │
         │ parserService   │ resourceSvc   │ downloadService
         │                 │               │
┌────────v─────────────────v───────────────v──────────────────┐
│                  Backend (FastAPI)                          │
├──────────┬──────────────┬──────────────┬────────────────────┤
│media     │resources     │tasks         │files               │
│_router   │_router       │_router       │_router             │
└────┬─────┴──────┬───────┴──────┬───────┴────────┬───────────┘
     │            │              │                │
     │ MediaSvc   │ ResourcesSvc  │ TaskTracker    │ FileService
     │            │              │                │
┌────v────────────v──────────────v────────────────v───────────┐
│           Service Layer + Repositories                      │
├──────────────┬──────────────┬────────────────┬──────────────┤
│ MediaRepo    │ ResourceRepo │ TaskTrackerDB  │ File I/O     │
└──────────────┴──────────────┴────────────────┴──────────────┘
         │                            │              │
┌────────v────────────────────────────v──────────────v────────┐
│              Supabase (PostgreSQL)                          │
├─────────────┬──────────────┬──────────────┬────────────────┤
│parsed_media │resources     │unified_tasks │versions        │
│resource_items              │              │recycle_bin     │
└─────────────┴──────────────┴──────────────┴────────────────┘
         │
┌────────v──────────────────────────────────────────────────────┐
│              Celery (Task Queue)                             │
├──────────────┬─────────────────────────────────┬────────────┤
│download_     │maybe_trigger_transcode_task    │AI pipeline │
│media_task    │(HLS encoding)                   │(optional)  │
└──────────────┴─────────────────────────────────┴────────────┘
         │
┌────────v──────────────────────────────────────────────────────┐
│              External Services                              │
├──────────────┬──────────────┬──────────────────┬────────────┤
│DouYin API    │ YouTube (    │FFmpeg/FFprobe    │File Storage
│ (LightHTTP)  │yt-dlp)       │(transcoding)     │(NAS)       │
└──────────────┴──────────────┴──────────────────┴────────────┘
```

---

## 修复优先级

### P0（高优先级，影响功能）
1. **积分退款逻辑** — 失败时应自动退款，防止用户扣费
2. **Resource 创建失败重试** — 下载成功但 resource 创建失败会导致用户看不到视频
3. **孤立资源清理机制** — 无清理会导致磁盘空间泄漏

### P1（中优先级，改善体验）
1. **回收站界面** — 用户需要恢复/永久删除已删除资源的入口
2. **上传文件大小限制** — 防止用户上传过大文件导致服务器宕机
3. **并发下载限制** — 防止用户无限发起下载任务

### P2（低优先级，优化）
1. **同步 ffprobe 改异步** — 大文件上传时会阻塞响应
2. **上传文件去重** — 相同文件应复用而非创建副本
3. **进度精度优化** — 用实际字节数而非百分比

---

## 总结

MediaHub 视频管理系统通过四个核心流程实现完整生命周期：

1. **Web Download Flow** — 从 URL 解析到自动创建资源库条目，包括去重和零拷贝复用
2. **Upload Flow** — 本地文件上传，自动版本管理和 HLS 转码
3. **Task Management** — Celery 异步队列 + Redis/DB 双轨进度追踪
4. **Delete Flow** — 软删除/永久删除/孤立检测，支持恢复和批量操作

主要风险点：
- 积分扣费未有对应退款机制
- 资源创建失败可能导致下载丢失
- 孤立资源无清理机制，磁盘泄漏
- 并发控制缺失，可能导致服务过载

建议优先修复 P0 项以确保数据一致性和用户体验。
