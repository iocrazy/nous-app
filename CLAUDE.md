# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 在此仓库中工作时提供指导。

## Worktree 端口隔离

**重要**：本项目使用 git worktree 多分支并行开发，每个 worktree 有独立端口。

**启动前必读**：检查当前 worktree 根目录的 `.worktree.env` 文件获取端口分配：
- `FRONTEND_PORT` — 前端 dev server 端口
- `BACKEND_PORT` — 后端 FastAPI 端口
- `REDIS_DB` — Celery Redis 数据库编号

端口已自动写入 `frontend/.env.local` 和 `backend/.env`，无需手动配置。

**管理工具**：`scripts/worktree-manager.sh`
```bash
./scripts/worktree-manager.sh list              # 查看所有 worktree 端口分配
./scripts/worktree-manager.sh create <branch>   # 创建新 worktree（自动分配端口）
./scripts/worktree-manager.sh destroy <name>    # 销毁 worktree（释放端口）
./scripts/worktree-manager.sh init              # 为当前目录初始化端口配置
```

## 开发规范

### UI 语言规范

**重要**：所有用户界面元素必须使用英文。

| 元素类型 | 示例 |
|----------|------|
| 菜单项 | Settings, Dashboard, My Library |
| 按钮 | Submit, Cancel, Save, Delete |
| 标签页 | Overview, Analytics, Reports |
| 表单标签 | Username, Password, Email |
| 提示文字 | Loading..., No data found |
| 导航 | Home, Back, Next |

### 多语言支持（i18n）

- 界面文案通过 i18n 实现多语言
- 代码中使用英文 key，翻译文件提供中文值

```tsx
// ✅ 正确 - 使用翻译 key
{t('common.submit')}

// ❌ 错误 - 硬编码中文
提交
```

### 命名风格

| 类型 | 风格 | 示例 |
|------|------|------|
| UI 文本 | Title Case | `My Library` |
| 翻译 key | camelCase | `myLibrary` |
| 文件名 | kebab-case | `my-library.tsx` |

### 测试数据

创建测试内容时必须用英文：
- ✅ `Test Team`、`My Collection`
- ❌ `测试团队`、`我的集合`

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



## CI/CD 部署

### 前端部署 (Vercel)

**自动部署**: 推送到 `master` 分支自动触发

| 配置项 | 值 |
|--------|-----|
| Framework | Vite |
| Root Directory | `frontend` |
| Build Command | `npm run build` |
| Output Directory | `dist` |

**环境变量** (Vercel Dashboard 配置):
- `VITE_SUPABASE_URL`
- `VITE_SUPABASE_ANON_KEY`
- `VITE_API_URL` → `https://mediahub.heygo.cn`

### 后端部署 (GitHub Actions + SSH)

**触发条件**: 推送到 `master` 且修改了 `backend/**` 文件

**工作流文件**: `.github/workflows/deploy-backend.yml`

```yaml
- name: Deploy to NAS via SSH
  uses: appleboy/ssh-action@v1.0.3
  with:
    host: ${{ secrets.NAS_HOST }}
    port: ${{ secrets.NAS_PORT }}
    username: ${{ secrets.NAS_USER }}
    key: ${{ secrets.NAS_SSH_KEY }}
    script: |
      cd ${{ secrets.NAS_PROJECT_PATH }}
      git pull origin master
      docker-compose build --no-cache backend
      docker-compose up -d --force-recreate backend celery-worker
```

**GitHub Secrets 配置**:

| Secret | 说明 |
|--------|------|
| `NAS_HOST` | 公网 IP 或 DDNS 域名 |
| `NAS_PORT` | SSH 端口 (如 2222) |
| `NAS_USER` | SSH 用户名 |
| `NAS_SSH_KEY` | 完整私钥 (含 BEGIN/END 行) |
| `NAS_PROJECT_PATH` | 项目路径 |

### 手动部署

```bash
# 前端 - 推送代码即可
git push origin master

# 后端 - SSH 到 NAS 执行
ssh user@nas-ip -p 2222
cd /path/to/mediahub
git pull && docker-compose up -d --build backend
```

## Discord 通知规则

当以下场景发生时，**必须**通过 Discord MCP 发送通知：

### 触发条件

| 场景 | 通知内容 |
|------|----------|
| ✅ 任务完成 | 任务摘要 + 主要改动 |
| ❌ 执行出错 | 错误信息 + 需要的操作 |
| 🚀 部署完成 | 部署状态 + 访问地址 |
| ⏸️ 需要人工确认 | 问题描述 + 选项 |

### 配置信息

- **Channel ID**: `1462033865911832628`
- **Guild ID**: `1462033865299329180`

### 发送方式

使用 `discord_send` 工具，参数：
- `channelId`: `1462033865911832628`
- `message`: 消息内容

### 消息格式模板

**任务完成：**
```
✅ **任务完成**: [任务名称]
📝 改动: [简要说明]
⏱️ 耗时: [时间]
```

**执行失败：**
```
❌ **执行失败**: [任务名称]
🔴 错误: [错误信息]
👉 需要: [下一步操作]
```

**部署完成：**
```
🚀 **部署完成**: [项目名称]
🌐 地址: [访问URL]
📦 版本: [版本号]
```

**需要确认：**
```
⏸️ **需要确认**: [问题描述]
🔹 选项1: [选项内容]
🔹 选项2: [选项内容]
```
