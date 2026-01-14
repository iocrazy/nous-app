# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 在此仓库中工作时提供指导。

## 项目结构

```
douyin_analysis/
├── backend/                    # 后端服务（FastAPI + Supabase）
│   ├── app/                   # 主应用包
│   │   ├── api/              # API 路由
│   │   ├── core/             # 核心配置
│   │   ├── db/               # Supabase 数据库配置
│   │   ├── repositories/     # 数据访问层
│   │   ├── schemas/          # Pydantic 模型
│   │   └── services/         # 业务逻辑层
│   ├── config.yml            # 业务配置
│   └── pyproject.toml        # 后端依赖
├── frontend/                   # 前端应用（React 19 + Vite）
│   ├── components/            # React 组件
│   ├── services/              # API 服务层
│   │   ├── dataService.ts    # Supabase 数据操作
│   │   └── parserService.ts  # 后端 API 调用
│   ├── App.tsx               # 主应用组件
│   ├── types.ts              # TypeScript 类型
│   ├── supabaseClient.ts     # Supabase 客户端配置
│   └── package.json          # 前端依赖
└── supabase/                   # Supabase 配置
    └── migrations/            # SQL 迁移脚本
```

## 常用命令

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
```

### Supabase

```bash
# 通过 Supabase 控制台执行 supabase/migrations/*.sql
# 或使用 Supabase CLI
supabase db push
```

## 架构设计

### 数据库

项目使用 **Supabase**（云端 PostgreSQL）：
- 内置用户认证（Supabase Auth）
- 实时订阅支持
- 行级安全策略（RLS）

### 核心组件

**浏览器自动化层** (`backend/app/services/douyin_analysis.py`)
- 使用 DrissionPage 的 ChromiumPage 实现单例模式
- 通过网络监听器拦截抖音 API 响应（`aweme/post/`、`aweme/detail/`）
- 执行隐身脚本绕过自动化检测
- 线程安全的懒加载初始化

**服务层** (`backend/app/services/`)
- `SupabaseDouyinService`: 编排完整工作流程
- `DouyinParser`: 将原始 aweme_detail JSON 解析为结构化模式
- `DownloaderService`: 异步媒体下载，带重试逻辑
- `SupabaseAuthService`: Supabase 用户认证

**存储层** (`backend/app/repositories/`)
- `SupabaseDouyinRepository`: Supabase 视频数据访问

### 配置系统

双层配置，优先级：环境变量 > .env > config.yml > 默认值

- **config.yml**: 业务配置（USER_AGENTS、CORS、超时时间）
- **.env**: 敏感配置（SUPABASE_URL、API Keys）

### aweme_type 值

- `0`: 标准视频
- `2`: 图片轮播/图集
- `4`: 特殊视频类型
- `61`: 另一种特殊视频变体
- `68`: 图文类型

### 前端技术栈

- **React 19** + **TypeScript**
- **Vite** 构建工具
- **TailwindCSS** 样式（CDN 版本）
- **Recharts** 数据可视化
- **Lucide React** 图标库
- **Supabase JS** 客户端

### 前后端对接

前端通过 `services/parserService.ts` 调用后端 API：

```typescript
// 解析单个链接
const response = await parseShareLink(url, {
  video_bool: true,
  music_bool: false,
  cover_bool: true,
});

// 批量解析
const response = await parseBatchLinks(urls, options);
```

**认证方式**：
- API Key：存储在 `localStorage.douyin_api_key`
- JWT Token：从 Supabase session 获取

**数据库表名**：`douyin_videos`（前后端统一）

## Supabase 配置

### 1. 创建项目

在 [Supabase](https://supabase.com) 创建新项目，获取：
- Project URL
- Publishable Key（公开密钥，前端使用）
- Secret Key（私密密钥，后端使用）

**注意**: Supabase 同时支持新格式密钥和旧版 JWT 格式密钥：
- 新格式: `sb_publishable_...` / `sb_secret_...`
- 旧格式: `eyJhbGciOiJIUzI1NiIs...`（anon key / service_role key）

### 2. 执行数据库迁移

在 Supabase SQL Editor 中依次执行：
1. `supabase/migrations/001_initial_schema.sql`
2. `supabase/migrations/002_optimize_schema.sql`

### 3. 配置环境变量

```bash
# backend/.env
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=sb_publishable_xxx      # Publishable Key
SUPABASE_SERVICE_ROLE_KEY=sb_secret_xxx   # Secret Key（绝不暴露到前端！）

# frontend/.env
VITE_SUPABASE_URL=https://your-project.supabase.co
VITE_SUPABASE_ANON_KEY=sb_publishable_xxx # Publishable Key（可以安全暴露）
VITE_API_URL=http://localhost:8080
```

### 4. MCP 连接（可选）

通过 PostgreSQL MCP 服务器连接 Supabase：

```bash
claude mcp add --transport stdio supabase -- npx -y @bytebase/dbhub \
  --dsn "postgresql://postgres:[密码]@[项目].supabase.co:5432/postgres"
```

## API 端点

### 认证

| 端点 | 方法 | 说明 |
|------|------|------|
| `/auth/signup` | POST | 用户注册 |
| `/auth/signin` | POST | 用户登录 |
| `/auth/signout` | POST | 用户登出 |
| `/auth/me` | GET | 获取当前用户 |
| `/auth/refresh` | POST | 刷新令牌 |

### 抖音视频

| 端点 | 方法 | 说明 |
|------|------|------|
| `/douyin/fetch` | POST | 获取单个视频 |
| `/douyin/fetch/batch` | POST | 批量获取视频 |
| `/douyin/videos` | GET | 获取视频列表 |
| `/douyin/videos/{aweme_id}` | GET | 获取视频详情 |
| `/douyin/videos/{aweme_id}` | DELETE | 删除视频 |
| `/douyin/videos/search` | POST | 搜索视频 |
| `/douyin/statistics` | GET | 获取统计信息 |
| `/douyin/retry/{aweme_id}` | POST | 重试下载 |

## 开发指南

### 添加新功能

1. 在 `backend/app/schemas/` 添加 Pydantic 模型
2. 在 `backend/app/repositories/` 添加数据访问方法
3. 在 `backend/app/services/` 添加业务逻辑
4. 在 `backend/app/api/` 添加 API 路由
5. 在 `frontend/services/parserService.ts` 添加 API 调用
6. 在 `frontend/components/` 添加 React 组件
7. 在 `frontend/App.tsx` 集成新组件

### 数据库变更

1. 在 `supabase/migrations/` 创建新的 SQL 文件（按序号命名）
2. 在 Supabase SQL Editor 执行
3. 运行 `npm run generate:types`（前端）更新 TypeScript 类型
