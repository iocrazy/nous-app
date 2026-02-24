# Multi-User Download Isolation Design

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将 parsed_media 改造为全局内容表（无 user_id），资源下载状态隔离到 resources 表，实现多用户下载互不干扰。

**Architecture:** 双层状态模型 — parsed_media 记录全局物理文件状态（服务器上有没有这个文件），resources 记录每用户的下载请求生命周期（用户有没有请求、请求到哪一步了）。用户 Fetch 时先查全局缓存，命中则秒完成，未命中则启动实际下载。

**Tech Stack:** PostgreSQL (Supabase), FastAPI, React 19, Celery

---

## 核心概念

### 双层状态模型

| 层 | 表 | 作用域 | 状态含义 |
|---|---|---|---|
| 全局层 | `parsed_media` | 一条内容一条记录 | 物理文件在服务器上的存在状态 |
| 用户层 | `resources` | 每用户每内容一条 | 用户的下载请求生命周期 |

### 状态枚举（复用 `download_status`）

| 状态 | parsed_media 含义 | resources 含义 |
|---|---|---|
| `skipped` | 从未有人请求过此类型 | 此用户未请求此类型 |
| `pending` | 有人请求了，等待下载 | 此用户请求了，等待处理 |
| `downloading` | 正在下载到服务器 | 正在处理中 |
| `completed` | 文件在服务器上 | 用户的请求已完成（可下载到本地） |
| `failed` | 下载失败 | 请求失败 |

### 内容类型

| 类型 | aweme_type | 可下载媒体 |
|---|---|---|
| 视频 | 0, 4, 61 | video + music(可选) + cover(必选) |
| 图集 | 2, 68 | images + music(可选) + cover(必选) |

**Cover 强制下载**，用户无法取消选择。

---

## 数据库变更

### parsed_media 表

**移除列：**
- `user_id` — 全局表无用户概念
- `need_download_video` — 每次请求参数，通过 Celery task params 传递
- `need_download_music` — 同上
- `need_download_cover` — 同上

**新增列：**
- `image_download_status download_status DEFAULT 'skipped'` — 图集下载状态
- `image_download_path TEXT` — 图集文件路径（JSON array 或目录路径）

**移除索引：**
- `idx_parsed_media_user_id`
- `idx_parsed_media_user_created`
- `idx_parsed_media_datasize_bytes`（composite with user_id）

**移除约束：**
- `douyin_videos_user_id_fkey` (FK → auth.users)

**更新 RLS 策略：**
- UPDATE: 改为 "任何认证用户可更新"（移除 user_id = auth.uid() 检查）
- 后端使用 service_role key 访问，不受 RLS 限制

### resources 表

**新增列：**
```sql
video_download_status download_status DEFAULT 'skipped'
music_download_status download_status DEFAULT 'skipped'
cover_download_status download_status DEFAULT 'skipped'
image_download_status download_status DEFAULT 'skipped'
```

**新增索引：**
```sql
idx_resources_download_status ON resources (creator_id, video_download_status)
  WHERE video_download_status != 'skipped'
```

---

## 数据迁移策略

```sql
-- 1. 将 parsed_media 中现有的用户下载状态迁移到 resources
UPDATE resources r
SET
  video_download_status = pm.video_download_status,
  music_download_status = pm.music_download_status,
  cover_download_status = pm.cover_download_status
FROM parsed_media pm
WHERE r.media_id = pm.id;

-- 2. image_download_status: 目前图集走的是 video_download_status
-- 对于 media_type = 'images' 的记录，将 video_download_status 复制到 image_download_status
UPDATE parsed_media
SET image_download_status = video_download_status,
    video_download_status = 'skipped'
WHERE media_type IN ('images', 'image');

-- 同步到 resources
UPDATE resources r
SET image_download_status = pm.image_download_status
FROM parsed_media pm
WHERE r.media_id = pm.id AND pm.media_type IN ('images', 'image');

-- 3. 然后删除 user_id, need_download_* 列
```

---

## Fetch 流程

### 首次解析（Parser Page）

```
用户提交 URL + 选项 (video=true, music=false)
  ↓
DouyinParser 解析元数据
  ↓
parsed_media: 查找 (platform_id, source_platform)
  ├─ 不存在 → INSERT（video=pending, music=skipped, cover=pending）
  └─ 已存在 → 不修改全局状态（其他用户可能已下载完成）
  ↓
resources: 为当前用户创建记录
  → video=pending, music=skipped, cover=pending
  ↓
创建 unified_task（用户看到进度）
  ↓
Celery 任务：
  对每种请求的媒体类型：
    检查 parsed_media.xxx_download_status
    ├─ completed → 文件在服务器 → 直接设 resources.xxx = completed
    └─ 非completed → 实际下载
         先试 parsed_media.xxx_download_urls
         ├─ URL 有效 → 下载 → 更新 parsed_media + resources = completed
         └─ URL 失效 → 用 original_url 重新 parse → 获取新 URL → 下载
```

### 后续 Fetch（PlayerPage "Fetch Video"）

```
用户点击 "Fetch Video"（当前 resources.video = skipped）
  ↓
创建 unified_task
更新 resources.video_download_status = pending
  ↓
后端检查 parsed_media.video_download_status
  ├─ completed → 文件已在服务器
  │   → resources.video = completed → task 完成（秒级）
  └─ 非completed → 启动下载
       ① 先试 parsed_media.video_download_urls
       ② URL 失效 → 用 original_url 重新 parse
       ③ 下载 → parsed_media.video = completed
       ④ resources.video = completed → task 完成
```

---

## UI 显示逻辑

### PlayerPage 下载菜单

| resources.xxx_status | 显示 | 操作 |
|---|---|---|
| `skipped` | Fetch Video / Fetch Audio | 点击触发 Fetch 流程 |
| `pending` / `downloading` | Downloading... | 禁用按钮，显示进度 |
| `completed` | Download Video / Download Audio | 下载到本地 |
| `failed` | Failed - Retry | 点击重试（同 Fetch 流程） |

**Cover** 不在菜单中显示选项（强制下载），但 cover 状态影响缩略图/封面展示。

### Parser Page 列表

移除 `user_id` 后，通过 resources 关联查询：

```sql
SELECT pm.*, r.video_download_status AS user_video_status,
       r.music_download_status AS user_music_status,
       r.cover_download_status AS user_cover_status,
       r.image_download_status AS user_image_status
FROM resources r
JOIN parsed_media pm ON r.media_id = pm.id
WHERE r.creator_id = auth.uid()
ORDER BY r.created_at DESC;
```

前端展示状态优先使用 `resources` 中的用户级状态。

---

## 多用户场景示例

**场景：同一视频 (platform_id=ABC)**

| 时间线 | 用户 A | 用户 B | parsed_media 状态 |
|---|---|---|---|
| T1 | 解析 video+cover | — | video=pending, cover=pending |
| T2 | video+cover 下载完成 | — | video=completed, cover=completed |
| T3 | — | 解析 music only | music=pending（video/cover 不动） |
| T4 | — | music 下载完成 | music=completed |

**用户 A 的 resources：** video=completed, music=skipped, cover=completed
**用户 B 的 resources：** video=skipped, music=completed, cover=pending→completed（cover 必选）

用户 B 的解析不会修改 parsed_media.video_download_status（已是 completed），也不会影响用户 A 的 resources 记录。

---

## 受影响的代码文件

### 后端

| 文件 | 变更 |
|---|---|
| `supabase/migrations/082_*.sql` | DB 迁移 |
| `backend/app/schemas/media.py` | MediaBase 移除 user_id, need_download_*; 新增 image_download_status |
| `backend/app/services/media_service.py` | save_metadata_only() 重写：分离全局/用户状态更新 |
| `backend/app/repositories/media_repository.py` | 移除 user_id 相关查询；新增按 resource 查询 |
| `backend/app/api/media_router.py` | Fetch 端点：创建 resources 记录 + 设置用户状态 |
| `backend/app/tasks/download_tasks.py` | Celery 任务：下载完成后同时更新 parsed_media + resources |
| `backend/app/schemas/resources.py` | ResourceInDB 新增 4 个 download_status 字段 |
| `backend/app/repositories/resources_repository.py` | 新增按 media_id+creator_id 查询 |

### 前端

| 文件 | 变更 |
|---|---|
| `frontend/types.ts` | Resource 接口新增 4 个 download_status 字段 |
| `frontend/pages/PlayerPage.tsx` | 下载菜单读取 resources 状态而非 parsed_media |
| `frontend/components/ParserView.tsx` | 列表查询改为通过 resources |
| `frontend/services/parserService.ts` | parseShareLink 移除 cover_bool 参数（强制 true） |
| `frontend/services/resourceService.ts` | 新增 fetchMedia(resourceId, type) 方法 |

---

## 不在本次范围内

- 团队共享 resources（当前每用户独立，未来可通过 shares 表实现）
- 存储清理策略（parsed_media 全局文件何时清理）
- 下载配额/积分系统
