# Celery + Redis 异步任务队列设计

> 创建日期: 2026-01-18

## 概述

为 MediaHub 添加 Celery + Redis 异步任务队列，支持：
- 视频下载任务（异步后台下载）
- 批量解析任务（批量处理抖音链接）
- 定时任务（清理、统计等）

## 架构设计

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Frontend  │────▶│   FastAPI   │────▶│    Redis    │
│  (React)    │     │  (Backend)  │     │  (Broker)   │
└─────────────┘     └─────────────┘     └──────┬──────┘
                           │                    │
                           │              ┌─────▼─────┐
                           │              │  Celery   │
                           │              │  Worker   │
                           │              └─────┬─────┘
                           │                    │
                    ┌──────▼──────┐      ┌─────▼─────┐
                    │   Flower    │      │  Celery   │
                    │  (Monitor)  │      │   Beat    │
                    └─────────────┘      └───────────┘
```

### 核心组件

| 组件 | 作用 | 端口 |
|------|------|------|
| Redis | 消息队列 + 结果存储 | 6379 |
| Celery Worker | 执行异步任务 | - |
| Celery Beat | 定时任务调度 | - |
| Flower | Web 监控界面 | 5555 |

## 高并发考虑

**当前方案**：暂不过度设计，但预留扩展能力。

**理由**：
1. 抖音 API 限制是主要瓶颈，内部并发意义不大
2. 单用户/小团队场景，不需要高并发
3. Celery 本身支持水平扩展，需要时可快速扩容

**推荐配置**：
```yaml
celery-worker:
  command: celery -A app.celery_app worker -c 4  # 4 并发
  deploy:
    replicas: 1  # 初始 1 个 worker

# 扩展命令
# docker-compose up --scale celery-worker=3
```

**预留扩展点**：

| 组件 | 当前 | 扩展方案 |
|------|------|----------|
| Worker | 1 实例 × 4 并发 | 多实例 × N 并发 |
| Redis | 单节点 | Redis Cluster |
| 任务队列 | 单队列 | 多优先级队列 |

## 任务定义

```
任务分类
├── 下载任务（download）
│   ├── download_video      # 下载单个视频
│   ├── download_images     # 下载图集
│   ├── download_cover      # 下载封面
│   └── download_music      # 下载音频
│
├── 解析任务（parse）
│   ├── parse_single_link   # 解析单个链接
│   └── parse_batch_links   # 批量解析（拆分为多个子任务）
│
└── 定时任务（scheduled）
    ├── cleanup_temp_files      # 清理临时文件（每天）
    ├── retry_failed_downloads  # 重试失败下载（每小时）
    └── update_statistics       # 更新统计数据（每 6 小时）
```

**关键设计**：
- 批量任务拆分成多个单任务，便于并行和重试
- 下载任务支持重试（最多 3 次，指数退避）
- 定时任务使用 Celery Beat 调度

## 项目文件结构

```
backend/
├── app/
│   ├── celery_app.py          # Celery 应用初始化
│   ├── tasks/                  # 任务模块（新增）
│   │   ├── __init__.py
│   │   ├── download_tasks.py  # 下载任务
│   │   ├── parse_tasks.py     # 解析任务
│   │   └── scheduled_tasks.py # 定时任务
│   ├── services/
│   │   └── downloader.py      # 保持不变，被 tasks 调用
│   └── api/
│       └── douyin.py          # 改为调用 task.delay()
├── celeryconfig.py            # Celery 配置
└── docker-compose.yml         # 新增
```

## Docker Compose 配置

```yaml
version: '3.8'

services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data

  backend:
    build: ./backend
    ports:
      - "8080:8080"
    depends_on:
      - redis
    environment:
      - CELERY_BROKER_URL=redis://redis:6379/0

  celery-worker:
    build: ./backend
    command: celery -A app.celery_app worker -l info -c 4
    depends_on:
      - redis
    volumes:
      - ./downloads:/app/downloads  # 共享下载目录

  celery-beat:
    build: ./backend
    command: celery -A app.celery_app beat -l info
    depends_on:
      - redis

  flower:
    image: mher/flower
    ports:
      - "5555:5555"
    environment:
      - CELERY_BROKER_URL=redis://redis:6379/0

volumes:
  redis_data:
```

## 代码实现

### celery_app.py

```python
from celery import Celery
from app.core.config import settings

celery_app = Celery(
    "mediahub",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "app.tasks.download_tasks",
        "app.tasks.parse_tasks",
        "app.tasks.scheduled_tasks",
    ]
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    task_track_started=True,
    task_time_limit=600,  # 10分钟超时
    worker_prefetch_multiplier=1,  # 公平调度
)
```

### download_tasks.py

```python
from app.celery_app import celery_app
from app.services.downloader import DownloaderService

@celery_app.task(bind=True, max_retries=3)
def download_video(self, aweme_id: str, video_url: str, save_path: str):
    """下载单个视频"""
    try:
        downloader = DownloaderService()
        result = downloader.download_video_by_aweme_id(aweme_id, video_url, save_path)
        return {"status": "success", "path": result}
    except Exception as e:
        # 指数退避重试：10s, 60s, 300s
        self.retry(exc=e, countdown=10 * (3 ** self.request.retries))
```

### API 调用方式

```python
# 之前（同步）
@router.post("/fetch")
async def fetch_video(url: str):
    result = downloader.download_video(...)  # 阻塞等待
    return result

# 之后（异步）
@router.post("/fetch")
async def fetch_video(url: str):
    task = download_video.delay(aweme_id, video_url, save_path)
    return {"task_id": task.id, "status": "queued"}

# 查询任务状态
@router.get("/task/{task_id}")
async def get_task_status(task_id: str):
    result = AsyncResult(task_id)
    return {"status": result.status, "result": result.result}
```

## 前端对接

### 任务状态轮询

```typescript
// services/taskService.ts
export const submitDownloadTask = async (url: string) => {
  const response = await api.post('/douyin/fetch', { url });
  return response.data; // { task_id: "xxx", status: "queued" }
};

export const getTaskStatus = async (taskId: string) => {
  const response = await api.get(`/task/${taskId}`);
  return response.data; // { status: "SUCCESS", result: {...} }
};

// 轮询 Hook
export const useTaskPolling = (taskId: string | null) => {
  const [status, setStatus] = useState<string>('PENDING');
  const [result, setResult] = useState(null);

  useEffect(() => {
    if (!taskId) return;

    const interval = setInterval(async () => {
      const data = await getTaskStatus(taskId);
      setStatus(data.status);

      if (['SUCCESS', 'FAILURE'].includes(data.status)) {
        setResult(data.result);
        clearInterval(interval);
      }
    }, 2000); // 每 2 秒轮询

    return () => clearInterval(interval);
  }, [taskId]);

  return { status, result };
};
```

### UI 状态展示

| 状态 | UI 展示 |
|------|---------|
| PENDING | 排队中... |
| STARTED | 下载中 ⏳ |
| SUCCESS | 完成 ✓ |
| FAILURE | 失败 ✗ [重试] |

## 实施步骤

1. **添加依赖**：在 `pyproject.toml` 添加 `celery`
2. **创建 Celery 入口**：`app/celery_app.py`
3. **实现任务模块**：`app/tasks/` 目录
4. **修改 API**：改为提交任务而非直接执行
5. **添加配置**：环境变量 `CELERY_BROKER_URL` 等
6. **编写 Docker Compose**：多服务编排
7. **前端对接**：任务状态轮询
8. **测试验证**：端到端测试

## 配置项

```bash
# .env
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0
```
