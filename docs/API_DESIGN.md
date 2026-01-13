# 前后端 API 对接设计文档

## 概述

本文档描述抖音视频分析系统的前后端 API 对接设计，基于 Supabase 作为后端数据存储。

### 项目配置

```
Supabase Project: douyin
Project URL: https://zesczfidxsikvohrxson.supabase.co
Region: ap-southeast-1
```

---

## 一、数据库架构

### 1.1 核心表结构

```
┌─────────────────────┐     ┌─────────────────────┐
│   user_profiles     │     │   douyin_videos     │
├─────────────────────┤     ├─────────────────────┤
│ id (UUID, PK)       │────▶│ user_id (FK)        │
│ username            │     │ id (BIGSERIAL, PK)  │
│ avatar_url          │     │ aweme_id (UNIQUE)   │
│ role (ENUM)         │     │ video_* 字段        │
│ created_at          │     │ music_* 字段        │
│ updated_at          │     │ download_* 字段     │
└─────────────────────┘     └──────────┬──────────┘
                                       │
                            ┌──────────▼──────────┐
                            │   download_tasks    │
                            ├─────────────────────┤
                            │ id (BIGSERIAL, PK)  │
                            │ aweme_id (FK)       │
                            │ task_type           │
                            │ status (ENUM)       │
                            │ priority            │
                            │ retry_count         │
                            └─────────────────────┘
```

### 1.2 枚举类型

```sql
-- 下载状态
CREATE TYPE download_status AS ENUM (
    'pending',      -- 待处理
    'downloading',  -- 下载中
    'completed',    -- 已完成
    'failed',       -- 失败
    'skipped'       -- 跳过
);

-- 用户角色
CREATE TYPE user_role AS ENUM (
    'admin',  -- 管理员
    'user',   -- 普通用户
    'test'    -- 测试用户
);
```

### 1.3 视频类型 (aweme_type)

| 值 | 类型 | 描述 |
|----|------|------|
| 0 | 标准视频 | 普通短视频 |
| 2 | 图片轮播 | 图集/多图 |
| 4 | 特殊视频 | 特殊格式视频 |
| 61 | 特殊视频 | 另一种特殊格式 |
| 68 | 图文 | 带文字的图片内容 |

---

## 二、API 端点设计

### 2.1 认证 API (`/auth`)

| 端点 | 方法 | 描述 | 认证 |
|------|------|------|------|
| `/auth/signup` | POST | 用户注册 | 否 |
| `/auth/signin` | POST | 用户登录 | 否 |
| `/auth/signout` | POST | 用户登出 | 是 |
| `/auth/me` | GET | 获取当前用户 | 是 |
| `/auth/refresh` | POST | 刷新令牌 | 是 |
| `/auth/reset-password` | POST | 发送重置密码邮件 | 否 |
| `/auth/me` | PUT | 更新用户信息 | 是 |

#### 请求/响应示例

**POST /auth/signup**
```json
// Request
{
  "email": "user@example.com",
  "password": "password123",
  "username": "myname"
}

// Response
{
  "success": true,
  "user": {
    "id": "uuid",
    "email": "user@example.com"
  },
  "session": {
    "access_token": "...",
    "refresh_token": "...",
    "expires_at": 1234567890
  }
}
```

**POST /auth/signin**
```json
// Request
{
  "email": "user@example.com",
  "password": "password123"
}

// Response
{
  "success": true,
  "user": { ... },
  "session": { ... }
}
```

---

### 2.2 抖音视频 API (`/douyin`)

| 端点 | 方法 | 描述 | 认证 |
|------|------|------|------|
| `/douyin/fetch` | POST | 获取单个视频 | 是 |
| `/douyin/fetch/batch` | POST | 批量获取视频 | 是 |
| `/douyin/videos` | GET | 获取视频列表 | 是 |
| `/douyin/videos/{aweme_id}` | GET | 获取视频详情 | 是 |
| `/douyin/videos/{aweme_id}` | DELETE | 删除视频 | 是 |
| `/douyin/videos/search` | POST | 搜索视频 | 是 |
| `/douyin/statistics` | GET | 获取统计信息 | 是 |
| `/douyin/pending` | GET | 获取待下载列表 | 是 |
| `/douyin/retry/{aweme_id}` | POST | 重试下载 | 是 |

#### 请求/响应示例

**POST /douyin/fetch**
```json
// Request
{
  "url": "https://www.douyin.com/video/xxx",
  "video_bool": true,
  "music_bool": false,
  "video_categories": "搞笑"
}

// Response
{
  "success": true,
  "message": "视频处理任务已提交",
  "aweme_id": "7123456789",
  "video_title": "视频标题",
  "author": "作者名",
  "aweme_type": "0"
}
```

**POST /douyin/fetch/batch**
```json
// Request
{
  "urls": [
    "https://www.douyin.com/video/xxx",
    "https://www.douyin.com/video/yyy"
  ],
  "video_bool": true,
  "music_bool": false,
  "video_categories": "教程"
}

// Response
{
  "success": true,
  "total": 2,
  "submitted": 2,
  "failed": 0,
  "results": [
    { "url": "...", "aweme_id": "...", "status": "submitted" }
  ],
  "errors": []
}
```

**GET /douyin/videos**
```
Query Parameters:
- skip: number (default: 0)
- limit: number (default: 20, max: 100)
- order_by: string (default: "created_at")
- ascending: boolean (default: false)
```

```json
// Response
{
  "success": true,
  "count": 20,
  "videos": [
    {
      "id": 1,
      "aweme_id": "7123456789",
      "author": "作者名",
      "video_title": "视频标题",
      "video_digg_count": 1000,
      "video_comment_count": 100,
      "video_share_count": 50,
      "video_collect_count": 200,
      "video_download_status": "completed",
      "aweme_type": "0",
      "created_at": "2026-01-13T00:00:00Z"
    }
  ]
}
```

**POST /douyin/videos/search**
```json
// Request
{
  "keyword": "搜索关键词",
  "author": "作者名",
  "status": "completed",
  "aweme_type": "0",
  "category": "搞笑",
  "start_date": "2026-01-01T00:00:00Z",
  "end_date": "2026-01-31T23:59:59Z"
}

// Response
{
  "success": true,
  "count": 10,
  "videos": [ ... ]
}
```

**GET /douyin/statistics**
```json
// Response
{
  "success": true,
  "statistics": {
    "total_videos": 100,
    "downloaded": 80,
    "pending": 10,
    "failed": 5,
    "skipped": 5,
    "standard_videos": 70,
    "image_collections": 20,
    "image_texts": 5,
    "special_videos": 5
  }
}
```

---

## 三、前端集成

### 3.1 环境变量配置

```bash
# frontend/.env
VITE_SUPABASE_URL=https://zesczfidxsikvohrxson.supabase.co
VITE_SUPABASE_ANON_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
VITE_API_URL=http://localhost:8000/api
```

### 3.2 Supabase 客户端初始化

```typescript
// frontend/src/lib/supabase.ts
import { createClient } from '@supabase/supabase-js'
import type { Database } from '@/types/database'

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY

export const supabase = createClient<Database>(supabaseUrl, supabaseAnonKey)
```

### 3.3 API 调用封装

```typescript
// frontend/src/lib/api.ts
import axios from 'axios'
import { useAuthStore } from '@/stores/authStore'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || '/api',
})

// 自动附加认证令牌
api.interceptors.request.use((config) => {
  const token = useAuthStore.getState().session?.access_token
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// 401 自动登出
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      useAuthStore.getState().signOut()
    }
    return Promise.reject(error)
  }
)
```

### 3.4 TypeScript 类型

详见 `frontend/src/types/database.ts`，包含：

- `DouyinVideo` - 视频数据类型
- `DownloadTask` - 下载任务类型
- `UserProfile` - 用户配置类型
- `VideoStatistics` - 视频统计视图类型
- `DownloadStatus` - 下载状态枚举
- `UserRole` - 用户角色枚举

---

## 四、认证流程

### 4.1 登录流程

```
┌─────────┐    ┌─────────┐    ┌─────────────┐
│ 前端    │    │ 后端    │    │ Supabase    │
└────┬────┘    └────┬────┘    └──────┬──────┘
     │              │                │
     │ 1. POST /auth/signin          │
     │─────────────▶│                │
     │              │ 2. signInWithPassword
     │              │───────────────▶│
     │              │                │
     │              │◀───────────────│
     │              │  3. Session    │
     │◀─────────────│                │
     │ 4. Store session              │
     │              │                │
```

### 4.2 受保护 API 调用

```
┌─────────┐    ┌─────────┐    ┌─────────────┐
│ 前端    │    │ 后端    │    │ Supabase    │
└────┬────┘    └────┬────┘    └──────┬──────┘
     │              │                │
     │ GET /douyin/videos            │
     │ Authorization: Bearer {token} │
     │─────────────▶│                │
     │              │ verify token   │
     │              │───────────────▶│
     │              │◀───────────────│
     │              │                │
     │              │ query videos   │
     │              │ (with RLS)     │
     │              │───────────────▶│
     │              │◀───────────────│
     │◀─────────────│                │
```

---

## 五、行级安全策略 (RLS)

### 5.1 用户配置表

```sql
-- 用户只能查看/更新/插入自己的配置
CREATE POLICY "用户可以查看自己的配置" ON user_profiles
    FOR SELECT USING (auth.uid() = id);
```

### 5.2 视频表

```sql
-- 认证用户可以查看所有视频
CREATE POLICY "认证用户可以查看所有视频" ON douyin_videos
    FOR SELECT USING (auth.role() = 'authenticated');

-- 用户可以更新自己创建的视频，管理员可以更新所有
CREATE POLICY "用户可以更新自己的视频" ON douyin_videos
    FOR UPDATE USING (
        auth.uid() = user_id OR
        EXISTS (SELECT 1 FROM user_profiles WHERE id = auth.uid() AND role = 'admin')
    );

-- 只有管理员可以删除
CREATE POLICY "管理员可以删除视频" ON douyin_videos
    FOR DELETE USING (
        EXISTS (SELECT 1 FROM user_profiles WHERE id = auth.uid() AND role = 'admin')
    );
```

---

## 六、实时订阅

### 6.1 监听视频状态变化

```typescript
// 订阅下载状态更新
const channel = supabase
  .channel('video-updates')
  .on(
    'postgres_changes',
    {
      event: 'UPDATE',
      schema: 'public',
      table: 'douyin_videos',
      filter: `user_id=eq.${userId}`,
    },
    (payload) => {
      console.log('Video updated:', payload.new)
      // 更新本地状态
    }
  )
  .subscribe()

// 清理
channel.unsubscribe()
```

### 6.2 监听下载任务

```typescript
const channel = supabase
  .channel('task-updates')
  .on(
    'postgres_changes',
    {
      event: '*',
      schema: 'public',
      table: 'download_tasks',
    },
    (payload) => {
      // 处理任务状态变化
    }
  )
  .subscribe()
```

---

## 七、错误处理

### 7.1 HTTP 状态码

| 状态码 | 描述 |
|--------|------|
| 200 | 成功 |
| 400 | 请求参数错误 |
| 401 | 未认证/令牌过期 |
| 403 | 权限不足 |
| 404 | 资源不存在 |
| 500 | 服务器内部错误 |

### 7.2 错误响应格式

```json
{
  "detail": "错误描述信息"
}
```

---

## 八、最佳实践

### 8.1 前端

1. 使用 React Query 进行数据获取和缓存
2. 使用 Zustand 管理认证状态
3. 实现乐观更新提升用户体验
4. 处理令牌刷新逻辑

### 8.2 后端

1. 使用后台任务处理耗时操作
2. 实现幂等性操作
3. 添加请求速率限制
4. 记录详细的操作日志

### 8.3 安全

1. 始终使用 HTTPS
2. 不在客户端暴露 Service Role Key
3. 使用 RLS 策略保护数据
4. 验证所有用户输入
