# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 在此仓库中工作时提供指导。

## 沟通语言

**必须使用中文**与用户沟通。所有问题、解释、确认、讨论都用中文。代码和 UI 文本仍使用英文（见下方 UI 语言规范）。

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
mediahub/
├── backend/                    # 后端服务（FastAPI + Supabase）
│   ├── app/
│   │   ├── api/              # API 路由
│   │   │   ├── media_router.py       # 解析/下载（parsed_media）
│   │   │   ├── resources_router.py   # 资源库 CRUD
│   │   │   ├── tags_router.py        # 标签系统
│   │   │   ├── projects_router.py    # 项目管理
│   │   │   ├── teams_router.py       # 团队管理
│   │   │   └── auth_router.py        # 认证
│   │   ├── core/             # 核心配置 + 依赖注入
│   │   ├── db/               # Supabase 客户端（同步/异步）
│   │   ├── repositories/     # 数据访问层（Repository Pattern）
│   │   ├── schemas/          # Pydantic 模型
│   │   ├── services/         # 业务逻辑层
│   │   │   └── task_tracker.py       # unified_tasks 任务追踪
│   │   └── tasks/            # Celery 异步任务
│   ├── config.yml            # 业务配置
│   └── pyproject.toml        # 后端依赖（uv 管理）
├── frontend/                   # 前端应用（React 19 + Vite 7）
│   ├── components/            # React 组件
│   ├── contexts/              # React Context（TaskManager, Toast 等）
│   ├── pages/                 # 页面组件（PlayerPage 等）
│   ├── services/              # API 服务层
│   │   ├── parserService.ts          # 解析/下载 API
│   │   ├── resourceService.ts        # 资源库 API
│   │   ├── unifiedTagService.ts      # 统一标签服务（Resources + Media）
│   │   ├── tagsService.ts            # 旧版标签服务（仅 media）
│   │   ├── teamService.ts            # 团队 API
│   │   └── projectService.ts         # 项目 API
│   ├── public/locales/        # i18n 翻译文件（en.json, zh.json）
│   ├── App.tsx               # 主应用 + 路由
│   ├── types.ts              # TypeScript 类型定义
│   └── supabaseClient.ts     # Supabase 客户端（含 bigIntSafeFetch）
├── supabase/
│   └── migrations/            # SQL 迁移（001-077+）
└── scripts/
    └── worktree-manager.sh    # Worktree 管理工具
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

项目使用 **Supabase**（PostgreSQL）+ **本地 Docker** 开发：
- Supabase Auth 用户认证
- 实时订阅（Task Center 监听 `unified_tasks` 表）
- 所有 ID 使用 Snowflake BIGINT（migration 051 迁移）

### 核心数据表

| 表名 | 用途 | 主键 |
|------|------|------|
| `parsed_media` | 解析的视频/媒体元数据 | BIGINT Snowflake |
| `resources` | 资源库文件（上传/下载的媒体） | BIGINT Snowflake |
| `resource_tags` | 资源-标签关联（junction table） | resource_id + tag_id |
| `tags` | 标签定义（system/user/time） | UUID |
| `resource_items` | 资源归属（文件夹/scope） | BIGINT |
| `resource_versions` | 资源版本历史 | BIGINT |
| `teams` | 团队 | BIGINT Snowflake |
| `team_members` | 团队成员 | BIGINT |
| `projects` | 项目 | BIGINT Snowflake |
| `unified_tasks` | 异步任务追踪（Celery/后台） | UUID |
| `folders` | 文件夹 | BIGINT Snowflake |
| `application_logs` | 后端应用日志（全量） | — |
| `frontend_error_logs` | 前端错误日志 | — |
| `api_request_logs` | API 请求日志 | — |

**重要关系**：
- `resources.media_id` → `parsed_media.id`（一个 resource 对应一个 parsed_media）
- `resource_tags.resource_id` → `resources.id`（标签关联到 resource，不是 parsed_media）
- 前端 MediaTagPicker 传入 `parsed_media.id`，后端自动解析为 `resources.id`

### 标签系统（Tags）

**演进历史**：`video_tags` → `media_tags`（migration 066）→ 合并到 `resource_tags`（migration 077，`media_tags` 已删除）

**当前架构**：
- 全局标签 CRUD：`GET/POST /api/v1/tags`
- Media 标签关联：`/api/v1/tags/media/{media_id}/tags`（后端自动将 media_id 解析为 resource_id）
- Resource 标签关联：`/api/v1/resources/{resource_id}/tags`（直接使用 resource UUID/Snowflake）
- 前端两套服务：
  - `unifiedTagService.ts` — 推荐使用，支持 resource 和 media 两种 entity type
  - `tagsService.ts` — 旧版，仅用于 media

### 任务系统（Task Center）

- 前端 `TaskManagerContext` 通过 Supabase Realtime 监听 `unified_tasks` 表变化
- Celery 任务和 FastAPI 后台任务均通过 `TaskTracker` 创建 `unified_tasks` 记录
- 任务状态：`pending` → `running` → `completed` / `failed`

### 前端技术栈

- **React 19** + **TypeScript** + **Vite 7**
- **TailwindCSS** 样式
- **Recharts** 数据可视化
- **Lucide React** 图标库
- **Supabase JS** 客户端（含 `bigIntSafeFetch` 处理 BIGINT 精度）
- **i18next** 多语言支持

### 前后端对接

**认证方式**：JWT Token（从 Supabase session 获取，`Authorization: Bearer <token>`）

**服务层分工**：
| 前端服务 | 后端路由 | 用途 |
|----------|----------|------|
| `parserService.ts` | `/api/v1/media/*` | 媒体解析/下载 |
| `resourceService.ts` | `/api/v1/resources/*` | 资源库 CRUD |
| `unifiedTagService.ts` | `/api/v1/tags/*` + `/api/v1/resources/*/tags` | 标签管理 |
| `teamService.ts` | `/api/v1/teams/*` | 团队管理 |
| `projectService.ts` | `/api/v1/projects/*` | 项目管理 |

### 配置系统

双层配置，优先级：环境变量 > .env > config.yml > 默认值

- **config.yml**: 业务配置（USER_AGENTS、CORS、超时时间）
- **.env**: 敏感配置（SUPABASE_URL、API Keys）

### 已知陷阱

- **Snowflake BIGINT 精度丢失**：PostgREST 返回 BIGINT 为 JSON number，JS 超过 2^53 精度丢失。已在 `supabaseClient.ts` 添加 `bigIntSafeFetch` 修复。
- **catch 静默吞错**：前端 `catch { /* ignore */ }` 会隐藏错误，新代码应使用 `catch (err) { console.error(...) }`
- **media_id vs resource_id**：`MediaTagPicker` 传入 parsed_media ID，后端自动解析为 resource_id。如果 media 没有对应 resource，标签操作返回空/404。

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

所有端点前缀：`/api/v1`

### 认证 (`/auth`)

| 端点 | 方法 | 说明 |
|------|------|------|
| `/auth/signup` | POST | 用户注册 |
| `/auth/signin` | POST | 用户登录 |
| `/auth/signout` | POST | 用户登出 |
| `/auth/me` | GET | 获取当前用户 |
| `/auth/refresh` | POST | 刷新令牌 |

### 媒体解析/下载 (`/media`)

| 端点 | 方法 | 说明 |
|------|------|------|
| `/media/fetch` | POST | 解析并下载媒体 |
| `/media/fetch/batch` | POST | 批量解析 |
| `/media` | GET | 获取 parsed_media 列表 |
| `/media/{id}` | GET | 获取 parsed_media 详情 |
| `/media/{id}` | DELETE | 删除 |
| `/media/search` | POST | 搜索 |
| `/media/{id}/slides` | GET | 获取图集 slides 列表 |
| `/media/{id}/slides/{filename}` | GET | 获取单张 slide 文件 |
| `/media/{id}/audio` | GET | 获取背景音频 |

### 资源库 (`/resources`)

| 端点 | 方法 | 说明 |
|------|------|------|
| `/resources` | GET | 资源列表（支持 folder_id 筛选） |
| `/resources/{id}` | GET | 资源详情 |
| `/resources/{id}` | PATCH | 更新资源（filename/notes/url/rating） |
| `/resources/{id}` | DELETE | 删除（软删除/is_trashed） |
| `/resources/{id}/tags` | GET/POST | 资源标签关联 |
| `/resources/{id}/tags/{tag_id}` | DELETE | 移除标签 |
| `/resources/folders` | GET/POST | 文件夹 CRUD |
| `/resources/upload` | POST | 上传文件 |

### 标签 (`/tags`)

| 端点 | 方法 | 说明 |
|------|------|------|
| `/tags` | GET | 所有标签列表 |
| `/tags` | POST | 创建标签 |
| `/tags/{id}` | PUT/DELETE | 更新/删除标签 |
| `/tags/media/{media_id}/tags` | GET/POST | Media 标签关联（自动解析 media_id → resource_id） |
| `/tags/media/{media_id}/tags/{tag_id}` | DELETE | 移除 media 标签 |
| `/tags/statistics` | GET | 标签使用统计 |

### 团队 (`/teams`) 和项目 (`/projects`)

| 端点 | 方法 | 说明 |
|------|------|------|
| `/teams` | GET/POST | 团队列表/创建 |
| `/teams/{id}` | GET/PATCH/DELETE | 团队 CRUD |
| `/teams/{id}/members` | GET/POST | 团队成员 |
| `/projects` | GET/POST | 项目列表/创建 |
| `/projects/{id}` | GET/PATCH/DELETE | 项目 CRUD |
| `/projects/{id}/files` | GET/POST | 项目文件 |

## AI Library (Phase 1) — Agent Framework

- **Tables** (migration 138 + 139 + 140): `ai_agents` (slug / identity_md / soul_md / agent_md / is_system_preset), `skills` (slug / body_md / frontmatter_json), `skill_files` (multi-file support: path / content / file_type / binary_url), `agent_skills` (M:N binding), `ai_sessions.agent_id` FK.
- **Seed**: `backend/seeds/agents/<slug>/{IDENTITY,SOUL,AGENT}.md` + `backend/seeds/skills/<slug>/SKILL.md` (with optional `references/`, `scripts/`, `assets/` subdirs). Loaded on startup via `SeedLoader`.
- **Composer**: `backend/app/services/prompt_composer.py` — assembles system message (IDENTITY → SOUL → AGENT → `<available_skills>` XML → cache boundary → request instructions → runtime line) with SHA1 cache fingerprint.
- **Skill tool** (lazy-readable): `backend/app/services/skill_tool_service.py` — `Skill(skill=..., file=...)` returns body_md or sub-file content. Loop driven by `AgentRunner`.
- **Providers**: `backend/app/services/ai_provider.py::QwenAdapter` — OpenAI-compatible chat-completions. Multi-provider adapters deferred to Phase 2.
- **Pilot**: `script_ai` agent (migrated `script_ai_service.py` — no hardcoded prompts left). Bound to 3 skills: `script-outline`, `script-expand`, `script-branch`.
- **REST**: `/api/v1/ai-library/{agents,skills}/*` — GET list, GET by slug, PATCH (blocked for system presets in Phase 1), PUT/DELETE for skill files.
- **Settings UI**: Settings → AI Library → Agents / Skills tabs. Agent editor has Overview / Files / Skills sub-tabs. Skill editor has multi-file tabs (SKILL.md + references/ / scripts/ / assets/).

## 开发指南

### 后端分层架构（Router → Service → Repository）

```
Router (api/)          — HTTP 层，参数校验，调用 Service
  ↓
Service (services/)    — 业务逻辑编排（可选层，简单 CRUD 可跳过）
  ↓
Repository (repos/)    — 数据访问，Supabase 查询
  ↓
Schema (schemas/)      — Pydantic 请求/响应模型
```

### 添加新功能

1. `supabase/migrations/` — 数据库迁移（三位数编号，如 `078_xxx.sql`）
2. `backend/app/schemas/` — Pydantic 模型
3. `backend/app/repositories/` — 数据访问层
4. `backend/app/api/` — API 路由（在 `main.py` 注册）
5. `frontend/types.ts` — TypeScript 类型
6. `frontend/services/` — API 服务层
7. `frontend/components/` — React 组件
8. `frontend/public/locales/` — i18n 翻译

### 数据库变更

1. 在 `supabase/migrations/` 创建新的 SQL 文件（按序号命名）
2. 本地执行：`psql -h 127.0.0.1 -p 54322 -U postgres -d postgres -f migrations/xxx.sql`
3. 更新 `frontend/types.ts` 中对应的 TypeScript 接口



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
