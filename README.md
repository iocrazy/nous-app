# 抖音视频分析系统

基于 FastAPI + React + Supabase 的抖音视频分析和下载系统。

## 功能特性

- 解析抖音分享链接，获取视频/图集/图文信息
- 支持视频、图片、音频自动下载
- 多种媒体类型支持（视频、图集、图文）
8
- 用户认证与权限管理（Supabase Auth）
- **API 密钥管理**（支持多密钥、权限范围控制）
- 云端数据库存储（Supabase PostgreSQL）
- 视频收藏夹功能
- 作者信息管理
- 实时统计数据
- 现代化 React 前端界面

## 技术栈

| 层级 | 技术 |
|------|------|
| **后端** | FastAPI + Python 3.11+ |
| **数据库** | Supabase (PostgreSQL) |
| **认证** | Supabase Auth + API Key |
| **前端** | React 18 + TypeScript + Vite |
| **样式** | TailwindCSS |
| **状态管理** | Zustand |
| **浏览器自动化** | DrissionPage |

## 项目结构

```
douyin_analysis/
├── backend/                    # 后端服务
│   ├── app/
│   │   ├── api/               # API 路由
│   │   │   ├── supabase_auth_router.py    # 认证路由
│   │   │   ├── supabase_douyin_router.py  # 视频路由
│   │   │   └── api_key_router.py          # API 密钥路由
│   │   ├── core/              # 核心配置
│   │   │   ├── config.py      # 配置管理
│   │   │   ├── deps.py        # 依赖注入（双重认证）
│   │   │   ├── enums.py       # 枚举定义
│   │   │   └── api_key_scopes.py  # 权限范围定义
│   │   ├── db/                # 数据库
│   │   │   └── supabase_client.py  # Supabase 客户端
│   │   ├── repositories/      # 数据访问层
│   │   │   ├── supabase_douyin_repository.py
│   │   │   └── api_key_repository.py
│   │   ├── schemas/           # Pydantic 模型
│   │   │   ├── douyin.py
│   │   │   └── api_key.py
│   │   └── services/          # 业务逻辑
│   │       ├── douyin_analysis.py
│   │       ├── douyin_parser.py
│   │       ├── downloader.py
│   │       ├── supabase_auth_service.py
│   │       └── supabase_douyin_service.py
│   ├── config.yml             # 业务配置
│   └── pyproject.toml         # Python 依赖
├── frontend/                   # 前端应用
│   ├── src/
│   │   ├── components/        # React 组件
│   │   │   └── Layout.tsx     # 布局组件
│   │   ├── pages/             # 页面组件
│   │   │   ├── LoginPage.tsx
│   │   │   ├── DashboardPage.tsx
│   │   │   ├── VideosPage.tsx
│   │   │   ├── FetchPage.tsx
│   │   │   ├── ApiKeysPage.tsx    # API 密钥管理
│   │   │   └── SettingsPage.tsx
│   │   ├── lib/               # 工具库
│   │   │   ├── api.ts         # 后端 API 调用
│   │   │   ├── supabase.ts    # Supabase 客户端
│   │   │   └── supabase-api.ts
│   │   ├── stores/            # 状态管理
│   │   │   └── authStore.ts
│   │   └── types/             # TypeScript 类型
│   │       ├── database.ts
│   │       └── api-key.ts     # API 密钥类型
│   └── package.json           # 前端依赖
├── supabase/                   # Supabase 配置
│   └── migrations/            # 数据库迁移
│       ├── 001_initial_schema.sql
│       ├── 002_optimize_schema.sql
│       └── 003_api_keys.sql   # API 密钥表
└── docs/                       # 文档
    └── API_DESIGN.md
```

## 快速开始

### 环境要求

- Python 3.11+
- Node.js 18+
- Chrome/Chromium 浏览器
- [uv](https://github.com/astral-sh/uv) 包管理器

### 1. 克隆项目

```bash
git clone https://github.com/your-repo/douyin_analysis.git
cd douyin_analysis
```

### 2. 配置 Supabase

1. 在 [Supabase](https://supabase.com) 创建新项目
2. 获取项目 URL 和 API Keys（在 Settings > API 中）
3. 在 SQL Editor 中依次执行迁移文件：
   - `supabase/migrations/001_initial_schema.sql`
   - `supabase/migrations/002_optimize_schema.sql`
   - `supabase/migrations/003_api_keys.sql`

### 3. 配置环境变量

**后端配置** (`backend/.env`):

```bash
# Supabase 配置（必需）
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your-anon-key
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key

# 下载路径
NAS_BASE_PATH=/path/to/download/videos

# 服务器配置（可选）
HOST=0.0.0.0
APP_PORT=8080
```

**前端配置** (`frontend/.env`):

```bash
VITE_SUPABASE_URL=https://your-project.supabase.co
VITE_SUPABASE_ANON_KEY=your-anon-key
VITE_API_URL=http://localhost:8080
```

### 4. 启动后端

```bash
cd backend
uv sync                                    # 安装依赖
uv run uvicorn app.main:app --reload       # 启动开发服务器
```

后端 API 文档: http://localhost:8080/docs

### 5. 启动前端

```bash
cd frontend
npm install                                # 安装依赖
npm run dev                                # 启动开发服务器
```

前端界面: http://localhost:5173

---

## 前后端 API 对接文档

### 认证方式

系统支持两种认证方式：

| 认证方式 | Header | 格式 | 说明 |
|---------|--------|------|------|
| **JWT Token** | `Authorization` | `Bearer <token>` | 用户登录后获取，拥有完整权限 |
| **API Key** | `X-API-Key` | `dk_<secret>` | 用户创建，按权限范围控制访问 |

前端通过 Axios 拦截器自动添加 JWT Token：

```typescript
// frontend/src/lib/api.ts
api.interceptors.request.use((config) => {
  const token = useAuthStore.getState().session?.access_token
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})
```

---

### 认证 API

#### 前端调用

```typescript
// 通过 Supabase 客户端直接调用
import { supabase } from '@/lib/supabase'

// 登录
const { data, error } = await supabase.auth.signInWithPassword({
  email: 'user@example.com',
  password: 'password123'
})

// 登出
await supabase.auth.signOut()

// 获取当前用户
const { data: { user } } = await supabase.auth.getUser()
```

#### 后端端点

| 后端端点 | 方法 | 说明 | 认证 |
|---------|------|------|------|
| `/auth/signup` | POST | 用户注册 | 否 |
| `/auth/signin` | POST | 用户登录 | 否 |
| `/auth/signout` | POST | 用户登出 | 是 |
| `/auth/me` | GET | 获取当前用户 | 是 |
| `/auth/refresh` | POST | 刷新令牌 | 是 |
| `/auth/reset-password` | POST | 发送重置密码邮件 | 否 |
| `/auth/me` | PUT | 更新用户信息 | 是 |

---

### 视频 API

#### 前端调用

```typescript
import { douyinApi } from '@/lib/api'

// 获取单个视频
const result = await douyinApi.fetchVideo(url, { video: true, music: false })

// 批量获取视频
const result = await douyinApi.fetchBatch(urls, { video: true, music: false })

// 获取视频列表
const { videos, count } = await douyinApi.getVideos({ skip: 0, limit: 20 })

// 获取单个视频详情
const { video } = await douyinApi.getVideo(awemeId)

// 删除视频
await douyinApi.deleteVideo(awemeId)

// 搜索视频
const result = await douyinApi.searchVideos({ keyword: '搞笑', status: 'completed' })

// 获取统计信息
const { statistics } = await douyinApi.getStatistics()

// 获取待下载列表
const { videos } = await douyinApi.getPending(100)

// 重试下载
await douyinApi.retryDownload(awemeId)
```

#### 前后端映射表

| 前端方法 | 后端端点 | HTTP 方法 | 权限范围 |
|---------|---------|----------|---------|
| `douyinApi.fetchVideo()` | `/douyin/fetch` | POST | `douyin:fetch` |
| `douyinApi.fetchBatch()` | `/douyin/fetch/batch` | POST | `douyin:fetch:batch` |
| `douyinApi.getVideos()` | `/douyin/videos` | GET | `douyin:videos:read` |
| `douyinApi.getVideo()` | `/douyin/videos/{aweme_id}` | GET | `douyin:videos:read` |
| `douyinApi.deleteVideo()` | `/douyin/videos/{aweme_id}` | DELETE | `douyin:videos:write` |
| `douyinApi.searchVideos()` | `/douyin/videos/search` | POST | `douyin:search` |
| `douyinApi.getStatistics()` | `/douyin/statistics` | GET | `douyin:statistics` |
| `douyinApi.getPending()` | `/douyin/pending` | GET | `douyin:videos:read` |
| `douyinApi.retryDownload()` | `/douyin/retry/{aweme_id}` | POST | `douyin:retry` |

#### 请求/响应示例

**获取视频**:

```typescript
// 前端请求
await douyinApi.fetchVideo('https://v.douyin.com/xxx', {
  video: true,
  music: false,
  categories: '搞笑'
})

// 后端接收
// POST /douyin/fetch
// Body: { url, video_bool, music_bool, video_categories }

// 响应
{
  "success": true,
  "message": "视频处理任务已提交",
  "aweme_id": "7123456789",
  "video_title": "视频标题",
  "author": "作者名",
  "aweme_type": "0"
}
```

**获取视频列表**:

```typescript
// 前端请求
await douyinApi.getVideos({ skip: 0, limit: 20, order_by: 'created_at', ascending: false })

// 后端接收
// GET /douyin/videos?skip=0&limit=20&order_by=created_at&ascending=false

// 响应
{
  "success": true,
  "count": 20,
  "videos": [
    {
      "id": 1,
      "aweme_id": "7123456789",
      "video_title": "视频标题",
      "author": "作者名",
      "video_download_status": "completed",
      ...
    }
  ]
}
```

---

### API 密钥管理

#### 前端调用

```typescript
import { apiKeyApi } from '@/lib/api'

// 获取可用权限范围
const { scopes } = await apiKeyApi.getScopes()

// 创建 API 密钥
const result = await apiKeyApi.create({
  name: '自动化脚本',
  description: '用于定时任务',
  scopes: ['douyin:fetch', 'douyin:videos:read'],
  expires_at: '2026-12-31T23:59:59Z'
})
// 注意：result.secret_key 仅此时返回一次！

// 获取密钥列表
const { keys, count } = await apiKeyApi.list(includeRevoked)

// 获取单个密钥详情
const key = await apiKeyApi.get(keyId)

// 更新密钥
await apiKeyApi.update(keyId, { name: '新名称' })

// 撤销密钥
await apiKeyApi.revoke(keyId)

// 删除密钥
await apiKeyApi.delete(keyId)
```

#### 前后端映射表

| 前端方法 | 后端端点 | HTTP 方法 | 认证 |
|---------|---------|----------|------|
| `apiKeyApi.getScopes()` | `/api-keys/scopes` | GET | 否 |
| `apiKeyApi.create()` | `/api-keys` | POST | JWT |
| `apiKeyApi.list()` | `/api-keys` | GET | JWT |
| `apiKeyApi.get()` | `/api-keys/{key_id}` | GET | JWT |
| `apiKeyApi.update()` | `/api-keys/{key_id}` | PATCH | JWT |
| `apiKeyApi.delete()` | `/api-keys/{key_id}` | DELETE | JWT |
| `apiKeyApi.revoke()` | `/api-keys/{key_id}/revoke` | POST | JWT |

#### 权限范围（Scopes）

| 权限范围 | 名称 | 说明 |
|---------|------|------|
| `douyin:fetch` | 获取视频 | 允许通过 URL 获取单个视频信息 |
| `douyin:fetch:batch` | 批量获取视频 | 允许批量获取多个视频信息 |
| `douyin:videos:read` | 读取视频 | 允许查看视频列表和详情 |
| `douyin:videos:write` | 管理视频 | 允许删除视频记录 |
| `douyin:search` | 搜索视频 | 允许搜索视频 |
| `douyin:statistics` | 查看统计 | 允许查看统计信息 |
| `douyin:retry` | 重试下载 | 允许重新触发视频下载 |
| `douyin:*` | 全部抖音权限 | 拥有所有抖音相关操作权限 |

#### 请求/响应示例

**创建 API 密钥**:

```typescript
// 前端请求
const result = await apiKeyApi.create({
  name: '自动化脚本',
  description: '用于定时任务',
  scopes: ['douyin:fetch', 'douyin:videos:read']
})

// 后端接收
// POST /api-keys
// Headers: { Authorization: Bearer <jwt_token> }
// Body: { name, description, scopes, expires_at?, rate_limit? }

// 响应
{
  "success": true,
  "message": "API 密钥创建成功，请妥善保存密钥！",
  "id": 1,
  "key_id": "abc123def456...",
  "key_prefix": "dk_abc12345...",
  "name": "自动化脚本",
  "scopes": ["douyin:fetch", "douyin:videos:read"],
  "status": "active",
  "secret_key": "dk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"  // 仅此次返回！
}
```

**使用 API 密钥访问**:

```bash
# 使用 API Key 替代 JWT Token
curl -X GET http://localhost:8080/douyin/videos \
  -H "X-API-Key: dk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

---

### 前端页面与 API 对应关系

| 页面 | 路由 | 使用的 API |
|-----|------|-----------|
| 登录页 | `/login` | Supabase Auth |
| 仪表盘 | `/dashboard` | `douyinApi.getStatistics()` |
| 视频管理 | `/videos` | `douyinApi.getVideos()`, `douyinApi.deleteVideo()`, `douyinApi.searchVideos()` |
| 获取视频 | `/fetch` | `douyinApi.fetchVideo()`, `douyinApi.fetchBatch()` |
| API 密钥 | `/api-keys` | `apiKeyApi.*` |
| 设置 | `/settings` | Supabase Auth |

---

## 数据库架构

### 表结构

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  user_profiles  │     │  douyin_videos  │     │    authors      │
├─────────────────┤     ├─────────────────┤     ├─────────────────┤
│ id (UUID, PK)   │     │ id (BIGSERIAL)  │     │ id (BIGSERIAL)  │
│ username        │     │ aweme_id (UK)   │     │ author_id (UK)  │
│ avatar_url      │     │ video_title     │     │ nickname        │
│ role (enum)     │     │ author          │────▶│ avatar_url      │
│ created_at      │     │ author_id (FK)  │     │ follower_count  │
│ updated_at      │     │ aweme_type      │     │ signature       │
└─────────────────┘     │ video_desc      │     │ created_at      │
                        │ video_*_count   │     └─────────────────┘
                        │ cover_url       │
                        │ download_status │
                        │ user_id (FK)    │
                        │ created_at      │
                        └────────┬────────┘
                                 │
┌─────────────────┐     ┌────────▼────────┐     ┌─────────────────┐
│   collections   │     │video_collections│     │    api_keys     │
├─────────────────┤     ├─────────────────┤     ├─────────────────┤
│ id (BIGSERIAL)  │◀────│ collection_id   │     │ id (BIGSERIAL)  │
│ name            │     │ video_id        │     │ key_id (UK)     │
│ description     │     │ added_at        │     │ key_hash        │
│ user_id (FK)    │     └─────────────────┘     │ key_prefix      │
│ created_at      │                             │ name            │
└─────────────────┘                             │ scopes (JSONB)  │
                                                │ status (enum)   │
                                                │ user_id (FK)    │
                                                │ expires_at      │
                                                │ usage_count     │
                                                └─────────────────┘
```

### 枚举类型

**下载状态 (download_status)**:

| 值 | 说明 | 颜色 |
|----|------|------|
| `pending` | 待下载 | 黄色 |
| `downloading` | 下载中 | 蓝色 |
| `completed` | 已完成 | 绿色 |
| `failed` | 失败 | 红色 |
| `skipped` | 已跳过 | 灰色 |

**视频类型 (aweme_type)**:

| 值 | 说明 |
|----|------|
| `0` | 标准视频 |
| `2` | 图片轮播/图集 |
| `4` | 特殊视频 |
| `61` | 特殊视频变体 |
| `68` | 图文 |

**API 密钥状态 (api_key_status)**:

| 值 | 说明 |
|----|------|
| `active` | 活跃 |
| `revoked` | 已撤销 |
| `expired` | 已过期 |

---

## 安全策略

### 行级安全 (RLS)

| 表 | SELECT | INSERT | UPDATE | DELETE |
|----|--------|--------|--------|--------|
| user_profiles | 仅自己 | 仅自己 | 仅自己 | - |
| douyin_videos | 仅自己 | 仅自己 | 仅自己 | 仅自己 |
| authors | 所有认证用户 | 所有认证用户 | - | - |
| collections | 仅自己 | 仅自己 | 仅自己 | 仅自己 |
| api_keys | 仅自己 | 仅自己 | 仅自己 | 仅自己 |

### 认证流程

```
用户登录方式：
1. 用户登录 → Supabase Auth 验证 → 返回 JWT Token
2. 前端存储 Token (Zustand + localStorage)
3. API 请求携带 Authorization: Bearer <token>
4. 后端验证 Token → 检查权限 → 执行操作

API 密钥方式：
1. 用户创建 API 密钥 → 选择权限范围
2. 系统返回完整密钥（仅一次）→ 用户保存
3. API 请求携带 X-API-Key: dk_xxx
4. 后端验证密钥 → 检查权限范围 → 执行操作
```

---

## 开发命令

### 后端

```bash
cd backend
uv sync                                    # 同步依赖
uv run uvicorn app.main:app --reload       # 启动开发服务器
uv run pytest                              # 运行测试
```

### 前端

```bash
cd frontend
npm install                                # 安装依赖
npm run dev                                # 启动开发服务器
npm run build                              # 构建生产版本
npm run lint                               # 代码检查
npm run preview                            # 预览生产构建
```

---

## 常见问题

### Q: 视频获取失败怎么办？

检查以下几点：
1. 确保 Chrome/Chromium 浏览器已安装
2. 检查抖音链接是否有效（尝试在浏览器中打开）
3. 查看后端日志获取详细错误信息
4. 确认网络可以访问抖音

### Q: 如何使用 API 密钥？

1. 登录前端，进入「API 密钥」页面
2. 点击「创建密钥」，选择所需权限
3. **立即复制并保存密钥**（仅显示一次！）
4. 在 API 请求中使用：
   ```bash
   curl -X GET http://localhost:8080/douyin/videos \
     -H "X-API-Key: dk_your_secret_key"
   ```

### Q: JWT 和 API Key 有什么区别？

| 特性 | JWT Token | API Key |
|------|-----------|---------|
| 获取方式 | 用户登录 | 用户创建 |
| 有效期 | 短（需刷新） | 可自定义（永久/指定天数） |
| 权限 | 完整权限 | 按 scopes 限制 |
| 使用场景 | 前端用户交互 | 后端脚本/第三方集成 |
| Header | `Authorization: Bearer <token>` | `X-API-Key: dk_xxx` |

### Q: 如何重置下载失败的视频？

使用重试接口：
```bash
curl -X POST http://localhost:8080/douyin/retry/{aweme_id} \
  -H "Authorization: Bearer <token>"
```

或使用 API Key：
```bash
curl -X POST http://localhost:8080/douyin/retry/{aweme_id} \
  -H "X-API-Key: dk_xxx"
```

---

## 更新日志

### v2.1.0 (2026-01-13)

**新功能**:
- 添加 API 密钥管理系统
- 支持双重认证（JWT + API Key）
- 添加权限范围（Scopes）控制
- 添加 API 密钥前端管理页面
- 用户数据隔离（每个用户只能访问自己的数据）

**后端变更**:
- 新增 `api_key_router.py` - API 密钥 CRUD 端点
- 新增 `api_key_repository.py` - API 密钥数据访问
- 新增 `api_key_scopes.py` - 权限范围定义
- 新增 `api_key.py` - Pydantic 模型
- 修改 `deps.py` - 支持双重认证
- 修改 `supabase_douyin_router.py` - 所有端点添加认证

**前端变更**:
- 新增 `ApiKeysPage.tsx` - API 密钥管理页面
- 新增 `api-key.ts` - TypeScript 类型定义
- 修改 `api.ts` - 添加 `apiKeyApi` 调用
- 修改 `Layout.tsx` - 添加导航链接

**数据库变更**:
- 新增 `003_api_keys.sql` 迁移
- 新增 `api_keys` 表
- 新增 `api_key_logs` 表（审计日志）

### v2.0.0 (2026-01)

**重大变更**:
- 完全迁移到 Supabase 数据库
- 移除本地 SQLite 支持
- 使用 Supabase Auth 替代自建 JWT 认证

**新功能**:
- 添加 `authors` 表管理作者信息
- 添加 `collections` 收藏夹功能
- 添加视频封面 `cover_url` 字段

### v1.0.0

- 初始版本
- 支持 SQLite 本地数据库
- 基础视频获取和下载功能

---

## 许可证

MIT License
