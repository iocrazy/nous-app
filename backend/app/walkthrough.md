# Tasks 模块代码审查报告

对 `backend/app/tasks/` 下 6 个文件进行的多维度代码审查。

---

## 🔴 高优先级问题

### 1. DRY 违规：`run_async()` 重复定义 4 次

**严重程度**: 🔴 高 — 影响维护性

同一个函数在 4 个文件中被 **完全拷贝**：

| 文件 | 行号 |
|------|------|
| [ai_tasks.py](file:///Volumes/program/project-code/repos/mediahub/backend/app/tasks/ai_tasks.py#L19-L32) | L19-32 |
| [analysis_tasks.py](file:///Volumes/program/project-code/repos/mediahub/backend/app/tasks/analysis_tasks.py#L17-L30) | L17-30 |
| [download_tasks.py](file:///Volumes/program/project-code/repos/mediahub/backend/app/tasks/download_tasks.py#L21-L36) | L21-36 |
| [parse_tasks.py](file:///Volumes/program/project-code/repos/mediahub/backend/app/tasks/parse_tasks.py#L19-L32) | L19-32 |
| [scheduled_tasks.py](file:///Volumes/program/project-code/repos/mediahub/backend/app/tasks/scheduled_tasks.py#L20-L33) | L20-33 |

**建议**: 提取到公共模块，例如 `app/tasks/_utils.py` 或 `app/core/async_helpers.py`，其他文件统一 import。

---

### 2. 并发/异步：`run_async()` 实现存在隐患

**严重程度**: 🔴 高 — 可能导致死锁或资源泄漏

```python
def run_async(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, coro)
                return future.result()
        else:
            return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)
```

**问题**:
- `asyncio.get_event_loop()` 在 Python 3.10+ 中已弃用（当没有运行中 loop 时会发出 DeprecationWarning）
- 在已有事件循环的线程里开新线程执行 `asyncio.run`，会 **为每次调用创建新的事件循环**，开销大且不利于连接池复用
- `ThreadPoolExecutor` 每次都重新创建，应该复用
- 裸 `except RuntimeError` 过于宽泛，可能吞掉非 event-loop 相关的 RuntimeError

**建议实现**:
```python
# app/core/async_helpers.py
import asyncio

def run_async(coro):
    """在 Celery worker 同步上下文中安全执行 async 协程。"""
    try:
        asyncio.get_running_loop()
        # 不应在异步上下文中调用此函数
        raise RuntimeError("run_async() cannot be called from an async context")
    except RuntimeError:
        # 没有运行中的 loop，安全使用 asyncio.run()
        return asyncio.run(coro)
```

> [!IMPORTANT]
> Celery worker 进程默认是同步的，不应有 running event loop。当前代码中处理 `loop.is_running()` 的分支可能本身就不应被触发。建议确认是否有某些配置导致了 worker 中存在 running loop，如有则需要从架构层面解决。

---

### 3. DRY 违规：下载任务中大量重复的 try/except + retry 模式

**严重程度**: 🟡 中 — 影响可读性

[download_tasks.py](file:///Volumes/program/project-code/repos/mediahub/backend/app/tasks/download_tasks.py) 中 `download_video_task`、`download_images_task`、`download_music_task`、`download_cover_task` 四个函数结构几乎完全相同（L534-730），仅调用的 service 方法和状态字段不同。

```python
# 四个函数共享的模式:
try:
    result = run_async(DownloaderService.download_X_by_platform_id(...))
    if result.X_download_status == DownloadStatus.COMPLETED:
        return {"status": "success", ...}
    else:
        raise self.retry(exc=Exception(result.error), ...)
except Exception as e:
    if self.request.retries < self.max_retries:
        raise self.retry(exc=e, ...)
    return {"status": "failed", ...}
```

**建议**: 抽取通用下载任务工厂或装饰器：

```python
def _run_single_download(self, platform_id, user_id, download_fn, status_attr):
    """通用单资源下载逻辑。"""
    try:
        result = run_async(download_fn(platform_id, user_id=user_id))
        status = getattr(result, status_attr, None)
        if status == DownloadStatus.COMPLETED:
            return {"status": "success", "platform_id": platform_id}
        raise self.retry(exc=Exception(result.error), ...)
    except Exception as e:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, ...)
        return {"status": "failed", "platform_id": platform_id, "error": str(e)}
```

---

### 4. DRY 违规：`download_media_task` 中 video / image 分支重复

**严重程度**: 🟡 中

[download_media_task](file:///Volumes/program/project-code/repos/mediahub/backend/app/tasks/download_tasks.py#L77-L306) 中 L144-222，处理 video types 和 image types 的代码块 **大部分相同**（music 和 cover 下载逻辑完全一样），仅 L145-156 vs L185-196 的「主内容下载」不同。

**建议**: 统一分支逻辑，仅针对主内容下载方法做条件判断。

---

## 🟡 中优先级问题

### 5. SOLID — 单一职责：`parse_single_link_task` 职责过多

**严重程度**: 🟡 中

[parse_single_link_task](file:///Volumes/program/project-code/repos/mediahub/backend/app/tasks/parse_tasks.py#L36-L290) 单个函数 ~250 行，承担了：
1. URL 验证提取
2. 抓取远程视频数据
3. 解析元数据
4. 数据验证 (`VideoCreate`)
5. 数据库创建/更新
6. 自动标签分类
7. 触发下载任务
8. 触发 L1 分析任务
9. 用户操作日志
10. 构建响应 metadata

**建议**: 拆分为更小的函数，例如：
- `_extract_and_fetch(url)` → 返回 aweme_detail
- `_save_metadata(parsed_data, user_id)` → 返回 saved_video
- `_auto_tag(video_db_id, aweme_detail, parsed_data)` → 打标签
- `_trigger_downstream(platform_id, parsed_data, ...)` → 触发下载和分析

---

### 6. SOLID — 单一职责：`analysis_tasks.py` 中 L1/L2 的分析-存储-标签-embedding 耦合

**严重程度**: 🟡 中

[analyze_video_l1_task](file:///Volumes/program/project-code/repos/mediahub/backend/app/tasks/analysis_tasks.py#L41-L126) 和 [analyze_video_l2_task](file:///Volumes/program/project-code/repos/mediahub/backend/app/tasks/analysis_tasks.py#L129-L281) 内部的 `_analyze()` 函数都包含:
1. 视觉分析 → 2. 存储结果 → 3. 打标签 → 4. 生成 embedding → 5. 更新状态

而两个函数中 **步骤 2-5 几乎相同**，仅分析调用和置信度不同。

**建议**: 将公共的后处理流程提取为 `_post_analysis(video_id, result, title, description, confidence)`。

---

### 7. 错误处理：`download_media_task` 双重重试机制冲突

**严重程度**: 🟡 中

[download_media_task](file:///Volumes/program/project-code/repos/mediahub/backend/app/tasks/download_tasks.py#L77) 同时使用:
- Celery 内置 `max_retries=3`（via `@shared_task`）
- TaskManager 的 `retry_count`（L280-282）

这两个计数器 **独立运行**，导致：
- Celery 可能已经重试了 3 次，但 TaskManager 的 retry_count 未同步
- L282 的 `if retry_count < 3` 检查的是 TaskManager 的计数，不是 Celery 的

**建议**: 统一使用 `self.request.retries` 作为唯一重试计数源，与 `download_ytdlp_task` 一致。

---

### 8. 错误处理：`ai_tasks.py` 中 `provider_key` 变量作用域问题

**严重程度**: 🟡 中

[transcribe_audio_task](file:///Volumes/program/project-code/repos/mediahub/backend/app/tasks/ai_tasks.py#L237-L250) 的 except 块引用了 `provider_key`（L248），但如果 L200 之前就抛出异常，`provider_key` 未被定义，会导致 `NameError`。

同样的问题出现在 [generate_summary_task](file:///Volumes/program/project-code/repos/mediahub/backend/app/tasks/ai_tasks.py#L336-L349) 中 `summary_model`（L347）。

**建议**: 将这些变量在 try 块开头提前声明默认值：

```python
provider_key = "unknown"
try:
    ...
```

---

### 9. KISS：`DownloadProgressTrackerWithTaskManager._calculate_speed` 未做速度计算

**严重程度**: 🟢 低

[_calculate_speed](file:///Volumes/program/project-code/repos/mediahub/backend/app/tasks/download_tasks.py#L348-L356) 仅基于已下载字节量做格式化，**并不是真正的速率计算**（没有用时间差做除法）。函数名具有误导性。

**建议**: 要么实现真正的速率计算（记录上次字节数和时间戳），要么改名为 `_format_size`。

---

### 10. 错误处理：`analysis_tasks` 中 L2 task 内部异常被静默吞掉

**严重程度**: 🟡 中

[analyze_video_l2_task](file:///Volumes/program/project-code/repos/mediahub/backend/app/tasks/analysis_tasks.py#L267-L274) 的 `_analyze()` 内部 catch 了 `subprocess.CalledProcessError` 和 `FileNotFoundError`，设置 `failed` 状态后 **返回 None**。外层 L276-281 的 `try/except` 只对 **其他异常** 执行 retry。这意味着 FFmpeg 失败时 **不会重试**，而是直接静默返回 None。

**建议**: FFmpeg 失败（可能是临时性的）应该 raise 让外层决定是否 retry。

---

### 11. 并发：`batch_analyze_l1_task` 直接查库而不用 Repository

**严重程度**: 🟢 低 — SOLID 违背

[batch_analyze_l1_task](file:///Volumes/program/project-code/repos/mediahub/backend/app/tasks/analysis_tasks.py#L284-L333) 直接使用 `supabase.table("videos").select(...)` 绕过了 Repository 层，与项目其他地方使用 `VideoRepository` 的模式不一致。

---

### 12. 错误处理：`scheduled_tasks.py` 时区不安全

**严重程度**: 🟢 低

[cleanup_temp_files](file:///Volumes/program/project-code/repos/mediahub/backend/app/tasks/scheduled_tasks.py#L65-L77) 使用 `datetime.now()` 和 `datetime.fromtimestamp()`，这些是 naive datetime（无时区信息），可能在跨时区部署场景下产生问题。

**建议**: 使用 `datetime.now(tz=timezone.utc)` 和 `datetime.fromtimestamp(ts, tz=timezone.utc)`。

---

## 🟢 低优先级 / 小改善

### 13. `__init__.py` 未导出新增的模块

`ai_tasks.py` 和 `analysis_tasks.py` 的任务在 [\_\_init__.py](file:///Volumes/program/project-code/repos/mediahub/backend/app/tasks/__init__.py) 中没有被导出。如果未来有模块发现需求，应保持一致。

### 14. 函数顶部的延迟导入可以统一

多个文件在函数体内使用延迟导入（如 `from app.repositories.video_repository import VideoRepository`），主要是为了避免循环导入。这本身是合理的，但建议在模块文档中注明原因。

### 15. `download_ytdlp_task` 中未使用的变量

[L472](file:///Volumes/program/project-code/repos/mediahub/backend/app/tasks/download_tasks.py#L472) 创建了 `repo = repo_class()` 但未使用。

---

## 优先重构建议（按投入产出比排序）

| 优先级 | 改动 | 预估影响 | 风险 |
|--------|------|----------|------|
| 1 | 提取 `run_async()` 到公共模块 | 消除 5 份重复代码 | 🟢 低 |
| 2 | 优化 `run_async()` 实现 | 避免潜在死锁 | 🟡 中 |
| 3 | 统一下载任务重试机制 | 修复 retry 计数冲突 | 🟡 中 |
| 4 | 修复 except 块中的变量作用域 | 防止 NameError | 🟢 低 |
| 5 | 拆分 `parse_single_link_task` | 提升可读性和可测试性 | 🟡 中 |
| 6 | 提取 L1/L2 公共分析后处理 | 消除~60行重复 | 🟢 低 |
| 7 | 合并 4 个单资源下载任务 | 消除~150行重复 | 🟢 低 |
