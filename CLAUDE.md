# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 在此仓库中工作时提供指导。

## 沟通语言

**必须使用中文**与用户沟通。所有问题、解释、确认、讨论都用中文。代码和 UI 文本仍使用英文（见下方 UI 语言规范）。

## Worktree 端口隔离

**重要**：本项目使用 git worktree 多分支并行开发，每个 worktree 有独立端口。

**启动前必读**：检查当前 worktree 根目录的 `.worktree.env` 文件获取端口分配：
- `FRONTEND_PORT` — 前端 dev server 端口
- `BACKEND_PORT` — 后端 FastAPI 端口
- `REDIS_DB` — Redis 数据库编号（download progress KV）

端口已自动写入 `frontend/.env.local` 和 `backend/.env`，无需手动配置。

**管理工具**：`scripts/worktree-manager.sh`
```bash
./scripts/worktree-manager.sh list              # 查看所有 worktree 端口分配
./scripts/worktree-manager.sh create <branch>   # 创建新 worktree（自动分配端口）
./scripts/worktree-manager.sh destroy <name>    # 销毁 worktree（释放端口）
./scripts/worktree-manager.sh init              # 为当前目录初始化端口配置
```

## 分支与合并工作流

为了避免长寿分支演化成"几十个 commit 互不知情"导致的语义冲突大爆炸（参考 2026-04 的 dev/master 大分叉事件），下面这套规则是强制约定。

### 分支生命周期

| 分支类型 | 寿命 | 何时 PR | 备注 |
|---------|------|---------|------|
| `feature/*` | ≤3 天 | 完成后立即 | 长于 3 天必须每日 rebase 到 master |
| **纯 refactor PR** | **≤24 小时** | **第一时间** | 不允许夹带逻辑改动；24h 内必须 merge 入 master |
| `master` | 长期 | — | 唯一长寿分支，集成主线 |

`dev` 分支 **正在弃用**。新工作直接 `feature/* → master`，集成验证靠 Vercel per-PR preview，不靠共用 dev 环境。

### 每日同步纪律

每个活跃 feature 分支每天开工前先同步：

```bash
bash scripts/sync-worktree.sh           # fetch + rebase origin/master + 启用 rerere
bash scripts/branch-health.sh           # 列出所有 worktree 落后 master 多少 commits
```

`sync-worktree.sh` 第一次运行会自动启用 `git config rerere.enabled true`，之后冲突解过一次就会自动重放。

落后 ≥30 commits 的 PR 会被 CI 拒绝（`.github/workflows/pr-behind-check.yml`），≥15 commits 会发警告评论。

### Refactor 与 Feature 必须分开

把"拆大文件"和"加新功能"混进同一个分支是大冲突的根源。

- 准备做大重构（拆文件、改模块边界、重命名）→ 单独开 `refactor/*` 分支，**只**改结构，不引入逻辑改动
- 重构 PR 24 小时内必须 merge（如果触碰多人在写的代码，直接 ping owner 加速 review）
- feature 分支必须基于已经 merge 完所有相关 refactor 的 master，不允许"在自己 feature 分支里顺便重构"

### 提交、审查、发布

直接复用 gstack 已有的 skill，不要再造轮子：

| 场景 | 用 | 替代什么手工操作 |
|------|------|------------------|
| 想 push 代码并开 PR | `/ship` | `git add/commit/push + gh pr create`（自动跑 tests / review / VERSION / CHANGELOG / merge base） |
| PR 提交后做代码审查 | `/review` | 手工 diff 检查（自动跑 scope drift + 多维度评审） |
| 大功能想跑全套审查 | `/autoplan` | 手工调度 CEO/design/eng/DX 多 agent 评审 |
| 想知道整体代码健康度 | `/health` | 手工跑 lint/test/typecheck 然后汇总 |
| 已合并 PR 后想确认部署 | `/land-and-deploy` + `/canary` | 手工等 GitHub Actions / 手工跑 smoke test |

`/ship` 的 Step 2 会自动 `git merge origin/<base>` 再跑测试，所以基本不用担心忘记 sync。

### 当冲突真的发生时

按这个顺序处理（不要直接强解）：
1. 检查冲突文件是否在最近几天有 refactor PR 处理过 — 如果是，先把那个 refactor PR 合掉再回来 rebase
2. 简单文本冲突直接解 + `git add` + `git rebase --continue`
3. 语义冲突（一边重构、另一边在旧结构上改逻辑）→ 写 cherry-pick 或重新 port，参考 PR #15 的 7 个 port commit 范式

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

新代码用语义色 token（ok/warn/danger/info/agent，见 `frontend/index.css` `@theme`）表达状态色，不再引入旧色相类名（indigo/amber/red/emerald 等已在 K1 全站配色重映射中失去原本语义，见 `docs/superpowers/specs/2026-07-29-warm-paper-palette-design.md`）。

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
nous/
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
│   │   ├── tasks/            # 残留 helper 模块（utils / download_progress / download_strategies / download_helpers）— Celery decorators 已删
│   │   └── workflows/        # DBOS workflow 定义（@DBOS.workflow + @DBOS.scheduled）
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
| `unified_tasks` | 异步任务追踪（DBOS workflow / FastAPI 后台） | UUID |
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

- 前端 `TaskManagerContext` 通过 Supabase Realtime 监听 `task_tracking` 表变化（表名是 `task_tracking`，旧名 `unified_tasks` 已 rename）
- DBOS workflow 是底层执行引擎，引擎私有表 `dbos.workflow_status` 通过 `mirror_dbos_lifecycle_to_tracking` trigger 把 `phase / status / progress / started_at / completed_at / error_msg` 同步到 `task_tracking`
- 任务状态：`queued` → `in_progress` → `completed` / `failed` / `cancelled` / `lost`

### 任务系统架构纪律（路线 C — 2026-05-05 立约）

为了避免 `task_tracking` 和 `dbos.workflow_status` 双表导致的数据源不一致（曾因此撞上 "Engine 108 queued 但 Settings 只 38 行" + "Parse Initializing... 但任务已下载完" 两类 bug），约定：

1. **`task_tracking` 是 UI 唯一数据源**。所有前端 query / 计数 / 列表 endpoint **必须**从 `task_tracking` 读取。**不得**直接 query `dbos.workflow_status`。即使是 system status badge / queue counter 也走 `SELECT COUNT(*) FROM task_tracking WHERE phase IN ('queued','in_progress')`。
2. **`phase / status / progress / started_at / completed_at / error_msg` 由 trigger 全权同步**。业务代码**禁止 PATCH 这些列**——让 `mirror_dbos_lifecycle_to_tracking` 单向负责，保证 DBOS 视角和 task_tracking 视角永远一致。
3. **业务装饰字段（subtitle / metadata / media_id / heartbeat_at / cost_cents / issue_id 等）由业务代码 PATCH**，DBOS workflow 不会动它们。这些字段属于 UI 显示和业务关联，跟执行引擎解耦。
4. **DBOS workflow 的 short-circuit 失败必须 raise 而不是 return failed dict**。返回 dict 会被 DBOS 视为 SUCCESS，trigger 把 task_tracking 误标 completed，业务字段（subtitle / metadata）却没机会更新——典型表现就是 UI 看到 "phase=completed but subtitle stuck on Initializing..."。
5. **DBOS internal queue（`_dbos_internal_queue`）孤儿 PENDING 由 startup reaper 清理**（`backend/app/main.py::_bg_reap_internal_queue`）。这些是 scheduled housekeeping workflow 在 worker 重启时遗留的，不属于用户业务任务，不进 `task_tracking`，也不该影响任何 UI 数据。
6. **加新 workflow 时清单**：
   - `manager.create()` 显式建 task_tracking 行
   - `manager.start()` / `update_progress()` / `complete()` / `fail()` 都通过 manager API（绝不直接 PATCH phase 列）
   - 失败路径用 `raise`，不用 `return {"status":"failed"}`
   - 任何额外业务字段写到 metadata jsonb，不写 DBOS workflow input/output（DBOS input freeze 后不可改）

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

### 部署验收纪律：必须做写入冒烟测试

**读正常 ≠ 服务正常。** 存储类故障几乎都是"读得到、写不进"的单向断裂 —— 而验收如果只抽样下载/播放，会全部通过，故障静默存在数天到数月。

2026-07 的 P1 迁移（NAS→gpupc，存储从群晖本地卷改为 CIFS 网络挂载）一次性埋了四个同模式故障：

| 故障 | 机理 | 静默时长 |
|------|------|----------|
| Supabase Storage 上传全 502 | file 后端用 xattr 存元数据，CIFS 不支持 setxattr（errno 95） | 3 天 |
| 视频下载全失败 | 容器 uid=1031 vs 目录 `755 heygo(1000)`，落到 other 无写位 | 未知 |
| honcho 向量库写不进 | 容器 uid=100 vs 目录 `775 heygo(1000)`，同上 | 1 个月+ |
| SeaweedFS 启动死循环 | 群晖 ACL 使 POSIX 位为空，`0200 & perm` 检查失败 | 部署当天 |

共同模式：**容器身份 ≠ 目录属主 / 文件系统能力不匹配 → 写失败、读正常 → 验收只测了读**。

**任何涉及存储路径、挂载、容器 user、文件系统类型变更的部署，验收清单必须包含：**

```bash
# 1) 逐容器写入探针（挂载点全覆盖，不只主要的那个）
for c in $(docker ps --format '{{.Names}}'); do
  for dst in $(docker inspect "$c" --format '{{range .Mounts}}{{.Destination}}{{println}}{{end}}'); do
    docker exec "$c" sh -c "touch $dst/.wprobe && rm -f $dst/.wprobe" \
      && echo "✅ $c $dst" || echo "❌ $c $dst"
  done
done

# 2) 身份/权限比对（uid 落在 owner / group / other 哪一档）
docker exec <容器> id                 # 容器实际 uid:gid
stat -c '%a %U:%G' <宿主机目录>        # 目录权限与属主

# 3) 网络文件系统额外验 xattr（CIFS/NFS 常不支持，Supabase Storage file 后端硬依赖）
python3 -c "import os; os.setxattr('<目录>/.probe','user.t',b'1')"

# 4) 端到端业务写入冒烟：真实走一遍上传/下载 API，不能只测读
```

注意事项：
- **CIFS 上 `chmod` 无效** —— 权限位由挂载参数 `file_mode` / `dir_mode` 固定，改目录权限看似成功实则不生效，必须改 `/etc/fstab` 后重新挂载
- **群晖共享文件夹用 ACL**，POSIX 位常显示为 `d---------`，容器只看 POSIX 位 → 新建数据目录后需显式 `chmod 755`
- 容器以 root 运行也**不保证能过检查** —— 部分程序（如 SeaweedFS）是读权限位判断而非真尝试写入

### 已知陷阱

- **Snowflake BIGINT 精度丢失**：PostgREST 返回 BIGINT 为 JSON number，JS 超过 2^53 精度丢失。已在 `supabaseClient.ts` 添加 `bigIntSafeFetch` 修复。
- **网络文件系统不能当本地盘用**：CIFS/NFS 缺 xattr、uid 映射固定、chmod 无效、锁与 rename 语义不可靠。存储引擎应贴着磁盘跑（进程与数据同机），跨机器走协议（S3/HTTP）而非文件系统挂载。Supabase Storage 已于 2026-07-25 迁到 S3 后端（SeaweedFS on nas-B），见 [`docs/superpowers/specs/2026-07-25-storage-s3-seaweedfs-migration-design.md`](docs/superpowers/specs/2026-07-25-storage-s3-seaweedfs-migration-design.md)。
- **catch 静默吞错**：前端 `catch { /* ignore */ }` 会隐藏错误，新代码应使用 `catch (err) { console.error(...) }`
- **media_id vs resource_id**：`MediaTagPicker` 传入 parsed_media ID，后端自动解析为 resource_id。如果 media 没有对应 resource，标签操作返回空/404。
- **`user_id=None` 在 Celery 链路里漂**：`scheduled_tasks.retry_failed_downloads` 会拉到 `parsed_media.user_id IS NULL` 的 orphan 行（legacy / 系统发起的下载），透传到下游会触发 `user_logs` 23502 + `user_settings` 22P02 错误风暴。修复：源头 skip + repo 防御性 early-return。任何新加的 Celery 任务都要先校验 user_id 不空再继续。
- **`libraries` 表没有 `team_id` 列**：scope 走 `scope_type` (`team`/`user`/`project`) + `scope_id` 两列。代码里 `select("..., team_id, ...")` 会拿 PG 42703 错。team 归属判断要先看 `scope_type='team'` 再用 `scope_id`。
- **`parsed_media` 没有 `transcript_status` 列**：AI 状态字段在 `resources` 表（`transcript_status` / `summary_status` / `visual_analysis_status`，information_schema 实测唯一持有表）；AI 生成的内容本体在 `resource_summaries` / `resource_transcripts`（按 resource_id 键控）。在 `parsed_media` 上查会 PG 42703；`videos` 表已于 migration 066 更名为 `parsed_media`，任何 `public.videos` 引用都是死的（2026-08 曾因此让 ResourceFetch 的视频/音频分支静默失败数月）。
- **gpupc 上 PG 容器内部监听 55434，不是 5432**：自托管 Supabase 的 `POSTGRES_PORT=55434` 会同时喂给容器内的 `PGPORT`（为与同机 `sb-dev` 共存），所以 **PG 进程本身只监听 55434**。走宿主机端口时无所谓（`ports: "127.0.0.1:55436:55434"` 映射层会转换），但**走 docker 内网容器名直连会绕过映射层**，必须写 `nous-db:55434` —— 写 5432 拿 `Connection refused`。`DBOS_DATABASE_URL` 尤其要注意：DBOS 依赖 LISTEN/NOTIFY，不能走 pooler，只能直连，所以它是唯一必须硬编码这个非标准端口的地方。血泪教训见 [`deploy/gpu-server/README.md`](deploy/gpu-server/README.md) 的「DBOS 直连端口」节。
- **触发路径必须类型化失败回显**：`attachment_failures` 字段后端早就在返回（`ai_library_chat_service.py`），前端却整整没读过——因为没有强制约定"新触发路径要连带写失败分支"。用户动作→agent 触发的每条路径必须返回类型化结果(成功/失败/原因),silent no-op 不可接受——与'DBOS 失败必须 raise'同族。新增触发路径时先写失败分支的用户可见回显。
- **裸 SQL 全量 ORM 化（2026-08-04 立约）**：新代码禁止新增 `text()` 裸 SQL（结构性例外：`dbos.*` schema 的两个文档化访问点、PG 系统目录诊断/schema 探针）。存量迁移期间（Phase B/C 未迁文件）如需改动裸 SQL，必须换用 `app/db/scoped_sql.py` 的 `scoped_sql()`（唯一新入口），显式声明 `scope=Scope(...)` 或 `system=True, reason="..."`——两者都不传会 raise。决策与分期见 [`docs/decisions/2026-08-04-raw-sql-to-orm-full-migration.md`](docs/decisions/2026-08-04-raw-sql-to-orm-full-migration.md)。

### Schema 迁移 / 代码漂移检查口径

加新代码前先用 information_schema 核列名（参考 `feedback_verify_columns_before_select.md` 的 reference）：

```sql
SELECT column_name FROM information_schema.columns
WHERE table_name='<table>' ORDER BY ordinal_position;
```

每次发版后跑一遍 `application_logs` 错误漏斗，看是否有新的 schema/代码漂移：

```sql
SELECT module, message, COUNT(*) FROM application_logs
WHERE level='ERROR' AND logged_at >= NOW() - INTERVAL '7 days'
  AND (message ILIKE '%does not exist%' OR message ILIKE '%violates%not-null%')
GROUP BY module, message ORDER BY count DESC;
```

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

- **Tables** (migration 138 + 139 + 140): `ai_agents` (slug / identity_md / soul_md / agent_md / is_system_preset), `skills` (slug / body_md / frontmatter_json), `skill_files` (multi-file support: path / content / file_type / binary_url), `agent_skills` (M:N binding), `conversation_ai_meta.agent_id` (direct_agent conversation → agent binding; `ai_sessions` retired in Phase 3 mig 333, superseded by `conversations`/`conversation_ai_meta`).
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

2026-07-25 起发布已收敛到 **nous 单线（gpupc）**，NAS/mediahub 老线退役。下面的表是唯一权威口径。

### 三条活跃链

| 目标 | workflow | runner | 触发 paths | 落到哪 |
|------|----------|--------|-----------|--------|
| 后端 | `deploy-gpu.yml` | **self-hosted `[self-hosted, gpu]`** | `backend/**`、`Dockerfile`、`deploy/gpu-server/**`、`nous-core/**`、`browser/**` | gpupc 本机 `nous-backend` + `nous-worker` + `nous-browser` |
| 前端 | `deploy-pages.yml` | `ubuntu-latest` | `frontend/**` | Cloudflare Pages（GHA 构建 + wrangler 直传，不吃 Pages 的 500 构建/月配额） |
| DB migration | `run-migration.yml` | **self-hosted `[self-hosted, gpu]`** | `supabase/migrations/**` | gpupc 本机 `nous-db` |

**为什么后端与 migration 必须 self-hosted**：gpupc 无公网 IP（CGNAT），GitHub 云端 runner 既不能 SSH 进来也收不到 webhook，只能反过来让 gpupc 主动连出去拉任务。附带好处：省掉 ACR 跨境推拉、build 用本机 48 核、不消耗 Actions 分钟数。

**`ci.yml` 也在 self-hosted 上（2026-08-06 起）**，理由不同：账户付款失败让托管 runner 的 job 全部 2 秒内被拦，CI 完全失去守卫能力。计费恢复后可以切回 `ubuntu-latest`（三处 `runs-on`）。

⚠️ **public repo + self-hosted runner 必须带 fork 守卫**。本仓库是 public，而 runner 就是生产部署机（以 `heygo` 身份跑，workdir 在 `datahub` 盘），fork 里的任意代码在上面执行等于把机器交出去 —— 这是 GitHub 官方对该组合的明确警告。`ci.yml` 三个 job 都有：

```yaml
if: github.event.pull_request.head.repo.full_name == github.repository
```

fork PR 因此**没有 CI**（显示 skipped 而非 failed）。这是刻意的取舍：宁可 fork PR 无守卫，也不开这个口子。往 self-hosted 上加任何 `pull_request` 触发的 workflow，都要同步加这一行。

runner 的 workspace 与生产数据同盘（`/media/heygo/program`），`actions/checkout` 默认 `clean: true` 会 `git clean -ffdx`，所以 `node_modules` / `target/` 不累积；代价是每次重装依赖。

### 前端链的关键设计（改之前先读）

- **构建期配置的唯一来源是 `frontend/.env.production`**，不在本文档里重复写域名与 flag 值。`deploy-pages.yml` 的构建 env 必须与该文件一致；CI 里 GHA 环境变量优先级高于 `.env` 文件（Vite 不覆盖已存在的环境变量），所以线上以 workflow 为准，而该文件保证**本地构建**产出同样的包。
- **`VITE_API_URL` 不能留空**。Vercel 时代靠 `vercel.json` 的 rewrite 把相对路径 `/api/*` 代理到后端；Cloudflare Pages 没有等价机制，`public/_redirects` 的 `/* /index.html 200` 反而会把 `/api/*` 吞掉返回 HTML。必须是绝对地址。
- **`vercel.json` 已删除，它独家承担的行为拆到两个文件**：rewrites → `frontend/public/_redirects`，响应头（`X-Frame-Options` / `nosniff`、`sw.js` 与 `version.json` 的缓存策略）→ `frontend/public/_headers`。⚠️ `.gitignore` 有 `frontend/public/*` 通配，往该目录加文件必须同时加 `!` 白名单，否则文件进不了仓库、CF Pages 永远拿不到。
- **`version.json` 的 `commitSha` 依赖构建环境变量**。当前是 GHA 构建 + wrangler 直传（不是 Pages 的 git 集成），所以 `CF_PAGES_COMMIT_SHA` 不存在，实际取的是 `GITHUB_SHA`。只留 Vercel 那个变量会静默产出空字符串，让任何"轮询 SHA 确认部署"的校验永远等不到。
- **应急部署**（托管 runner 不可用时）：`cd frontend && npm run build && npx wrangler pages deploy dist --project-name nous-app --branch master`。本地构建读不到 GHA 的 env，走的就是 `.env.production` —— 这也是上面第一条为什么重要。

**self-hosted 仍然是 GitHub Actions**：触发、编排、日志、PR 状态全在 GitHub，只是执行机器换成本机。别把它理解成"不走 CI"。

### 后端链的关键设计（改之前先读）

- **`actions/checkout`，绝不 `cd` 到开发目录**。曾经是 `cd /media/.../repos/nous-app && git reset --hard origin/master`，三个后果：抹掉该目录未提交改动（触发者可能是另一台机器的 merge，人在 gpupc 上写代码毫无预警）；反过来未提交改动与 master 冲突时 `git checkout` 被 git 拒绝导致部署失败；reset 到的是"执行那一刻的 master HEAD"而非触发本 job 的 commit，两次 merge 挨得近时前一个 job 会部署后一个的代码。
- **smoke 失败自动回滚**。`up.sh` 在 smoke 之前就换好了容器，所以 smoke 失败 == 生产此刻是坏的。`Tag current images as rollback point` 先把 `nous-backend:local` / `nous-browser:local` 打成 `:rollback`，smoke 失败时退回并重启（不带 `--build`）。只在 `steps.smoke.outcome == 'failure'` 时触发 —— build 阶段失败容器根本没换。
- **往这条链加服务，必须同时改四处，漏一处就是静默缺口**（清单都是显式的，漏了不会报错）：① compose 加 service；② workflow 的 `Tag current images as rollback point` 加锚点；③ `./up.sh --build <清单>` 加服务名（2026-07-27 的 gateway 事故就是漏了这处，入口 502 五分钟）；④ smoke 步骤加该服务的真探针，且回滚步骤的 `./up.sh <清单>` 同步加。**只加 ①③ 而不加 ④ 的后果是最坏的**：新服务挂了 smoke 照样全绿，等于把"探针探真信号"的纪律又破一次。
- **回滚只回镜像，不回配置**。锚点是 docker image tag，而 compose 文件、`nginx-gateway/` 模板来自本次 checkout。所以「compose/配置改错导致 smoke 失败」这一类，回滚后仍会用同一份坏配置重启，需人工介入。`gateway`（上游 nginx 镜像 + 仓库配置）完全不在回滚覆盖内。
- **验收口径必须探 `/api/v1/readyz`，不是 `/health` 也不是 `docker ps`**。见下方「验收纪律」。

### 构建可复现性与磁盘回收（2026-08-05 立约）

同一天两次部署接连挂在 `Build & restart backend stack`，失败点不同（一次 crates.io `SSL_ERROR_SYSCALL`，一次 apt 跑满 986 秒），病根是同一个：**构建要从国外源下载几百 MB，而这台机器拉不动**。平时不出事只是因为那些层一直命中缓存。

三条现在是硬约定：

1. **基础镜像必须按 digest pin，不用浮动 tag**。`FROM python:3.13-slim` 这类写法意味着上游一推安全快照，**下面每一层缓存全部失效** —— 包括装 chromium + ffmpeg + CJK 字体那层。升级基础镜像应该是一次明确的、可 review 的改动，而不是某天悄悄发生。取新 digest：
   ```bash
   docker buildx imagetools inspect python:3.13-slim --format '{{.Manifest.Digest}}'
   ```

2. **外网源走国内镜像，且用 `ARG` 可覆盖**。实测（gpupc 直连）：`deb.debian.org` 0.29 MB/s vs `mirrors.aliyun.com` 8.1 MB/s（28×）；crates.io **拉不动真实 crate**（返回 277 字节错误体），rsproxy.cn 正常。Dockerfile 里是 `APT_MIRROR` / `CARGO_MIRROR` 两个 ARG，海外构建传空值或官方域名即可回退。注意 Debian 13 用 deb822 格式，要改的是 `/etc/apt/sources.list.d/debian.sources` 而非经典 `sources.list`。

3. **磁盘回收是部署链的一环，不是想起来才做的运维动作**。`Tag current images as rollback point` 会让**上上个**版本失去全部 tag 变成 dangling，而在此之前没有任何人回收它 —— 增长率就是「部署频率 × 镜像大小(~6GB)」。2026-08-05 实测已积到 166GB dangling 镜像 + 62GB 可回收缓存。`deploy-gpu.yml` 现在有两步兜住：
   - **`Disk guard`**（构建前）：可用 <150GB 就先回收。空间见底的表现**不是**"磁盘满"这种好认的错，而是 buildkit 悄悄 GC 掉构建缓存，于是下次构建从零重下 —— 正是上面那两次超时的成因。
   - **`Reclaim disk`**（`if: success()`）：清 48h 以上的 dangling 镜像 + 缓存上限 60GB。只在成功后跑，失败时保留现场；全部 `|| true`，回收失败不该把一次成功的部署判成失败。

   保留 48h 而不是全清，是为了「`:rollback` 本身也坏了」时还能手动退到更早一版。`:local` / `:rollback` 带 tag，天然不在 dangling 之列，不会被误清（已验证）。

4. **工具链版本只能有一个来源，CI 不许自己抄一份**（2026-08-06 补）。`.python-version`（3.13）/ `.nvmrc`（22）是唯一真相，所有 `setup-python` / `setup-node` 一律写 `python-version-file` / `node-version-file`，**不写版本号字面量**。

   在此之前 `ci.yml` 和 `schema-drift.yml` 写死 `"3.12"`、`ci.yml` 写死 node `20`，而生产是 3.13（Dockerfile pin 的 digest）和 22（`deploy-pages.yml` 出货构建）。**后果不是报错，是静默失去保证** —— lint、依赖解析、前端 build 全在生产从不使用的版本上跑绿，与「探针必须可证伪」是同一族的问题。没被拦住是因为 `requires-python = ">=3.12"` 是**下限不是锁**，而 node 侧此前既无 `.nvmrc` 也无 `package.json` engines，压根没有可对齐的来源。

   ⚠️ 版本文件的路径相对 **workspace 根**（写 `.python-version`），`defaults.run.working-directory` 只作用于 `run` 步骤，不影响 action 输入。

### 红 CI 的诊断顺序（先读日志，再谈假设）

同一批红 CI 曾被连着误诊两次（先判"计费假红"、再判"要迁 self-hosted"），真相是第三种。**第一步永远是 `gh run view --job <id> --log-failed` 看首个 error**，再套下面的表：

| 首个 error | 含义 | 处置 |
|---|---|---|
| `runner_name` 为空 + `steps=0` + 2 秒 fail | 账户计费失败（托管 runner 被拦） | 走 self-hosted（不计费）。⚠️ 别指望"切 public"，见下 |
| `Failed to resolve action download info: Service Unavailable` | **GitHub Actions 侧 outage**，job 死在准备阶段 | 只能等 + 重跑。**迁 self-hosted 无效** —— runner 一样要向 GitHub API 取 action 元数据 |
| 有真实步骤日志与耗时 | 代码/配置真的挂了 | 正常修 |

中间那档最容易误判成前一档：两者都是"一行业务代码没跑"，但一个是计费、一个是 GitHub 故障，处置**完全相反**（一个换 runner 有用，一个换了也没用）。区别在**有没有真实耗时** —— 计费拦截 2 秒就死，outage 会重试到几分钟甚至十几分钟。

⚠️ **「切 public 就能解」已被推翻**（2026-08-06）：repo 当时**已经是 public**，托管 runner 仍被全部拦下。那条旧经验（2026-07-26）适用的是**免费额度用尽**触发的强制回退；付款方式本身失败时公私有无关。所以判断顺序是先 `gh repo view --json visibility` 确认可见性，**如果已经是 public 还被拦，就不是额度问题，只能换 runner 或修账单**。

### 已退役的 NAS 老线（⚠️ 扳手当前是坏的）

`deploy-backend.yml`（ACR + watchtower → `mediahub-app-backend/worker`）与 `deploy-admin.yml` 已去掉 push 自动触发，只留 `workflow_dispatch`。

⚠️ **2026-07-26 起 `gh workflow run deploy-backend.yml` 已不能真正部署**，别把它当可用的回滚扳手。它在两个层面都断了：

1. **落地端不存在**：NAS 老栈已整体拆除，`mediahub-app-backend` / `worker` 连 `docker ps -a` 里都没有了。
2. **触发链已关闭**：nas-A 的 Watchtower HTTP API（token + 8083 端口）已整块移除，轮询改 24h，且没有任何容器带 `watchtower.enable` 标签（日志每轮 `Scanned=0`）。workflow 里那步 "Trigger Watchtower update" 现在必然打空，而它的兜底提示"will auto-poll in 5min"是错的。

起因：仓库切 public 后，旧版 `scripts/deploy.sh` 里硬编码的 `WATCHTOWER_TOKEN` 变成世界可读（git 历史永久）。只删 token 而保留 `HTTP_API_UPDATE=true` 会留下无鉴权端点，所以整条路径拆掉。

保留这两个 workflow 只是为了将来真要恢复 NAS 双轨时不用从零重写。恢复步骤见 [`docs/runbook/watchtower-config.md`](docs/runbook/watchtower-config.md) 的「若将来要恢复 NAS 作为回滚锚点」。

⚠️ 若真要恢复双轨，注意两边连的是**不同的 Supabase**，`backend/**` 一次改动会同时部署到两套互不相干的数据库；且 NAS supabase 容器 force-recreate 会让烙在容器里的 legacy JWT key 失效。

**当前真正的回滚手段**是 `deploy-gpu.yml` 的 smoke 失败自动回滚（`nous-backend:rollback` 镜像），见上方「后端链的关键设计」。

### 验收纪律（2026-07-22 血泪）

**永远不要用 `docker ps` 的 `healthy` 当发布成功的依据。** DBOS 曾整整挂 3 天而全部探针绿灯：`/health` 返回硬编码 `{"status":"healthy"}`；`/api/v1/healthz` 只探进程活着；`/api/v1/readyz` 早期只探 daemon 是否*存活*（而 reap/stall daemon 确实活着，只是每 tick 都失败，刷了 1915 条 ERROR）。最后靠人手点一次 transcribe 才发现。

现在的口径：

```bash
# 唯一可信探针 —— dbos 字段有三态,configured_but_disabled 即故障
docker exec nous-worker curl -sS http://localhost:8080/api/v1/readyz
# 引擎侧应持续有新 workflow(scheduled 的 inbox/outbox_dispatch 每 5s 一轮)
docker exec nous-db psql -U postgres -p 55434 -d postgres -c \
  "SELECT status, count(*) FROM dbos.workflow_status
   WHERE created_at > (extract(epoch from now())*1000 - 300000) GROUP BY 1"
```

加新的健康信号时：**`is_enabled()` 不能当健康依据**，它只表示句柄对象存在；worker 角色下 `init_dbos()` 在碰 DB 之前就赋值了 `_dbos`，launch 失败后它仍为 True。用 `is_launched()`。

### 部署陷阱

- **`secrets/backend.env` 会静默盖掉 `backend/config.yml`**（优先级见「配置系统」：环境变量 > .env > config.yml）。症状极具迷惑性：**改了 git 里的配置、PR 合了、CI 绿了、容器也重建了，但配置没生效** —— 因为真相在仓库外那个文件里。2026-07-25 实例：#1566 把 `CORS_ORIGINS` 换成 nous.ink 系并加了 `nous-app.pages.dev`，部署全绿，而 `backend.env` 里一行 `CORS_ORIGINS=["https://app.nous.ink","https://admin.nous.ink"]` 让实际生效的只有 2 个，从 `nous-app.pages.dev` 访问会被 CORS 拒绝。
  **原则**：能放 `config.yml` 的一律不放 `backend.env`；后者只该有密钥和机器特定值（DSN、API key、路径）。发现被覆盖就删掉 env 里那行，让 `config.yml` 成为唯一来源，而不是在两处同步维护。
  **怀疑配置没生效时这样查**（注意必须用 venv 解释器，`python` 是系统的、没装依赖）：
  ```bash
  docker exec nous-backend printenv | grep -E '^(CORS|MEDIA|DBOS)'      # env 层(赢的那个)
  docker exec nous-backend /app/.venv/bin/python -c \
    "from app.core.config import settings; print(settings.CORS_ORIGINS)"  # 实际生效值
  ```
- **迁移里不要写 `SET ROLE service_role`** —— 它是**主动降权**，不是提权。所有 runner（`run-migration.yml`、`schema-drift.yml`）都以 `psql -U postgres` 超管连接，切到 service_role 只会把权限交出去。之所以在 prod 看着能用，是因为那边的 service_role 有 Supabase 平台发的真 grant；而 `supabase/ci_bootstrap.sql` 把它裸建成 `CREATE ROLE ... NOLOGIN NOINHERIT`（**只有角色名，没有任何 GRANT**），所以同一句在 schema-drift 门禁里必然 `permission denied`。
  ⚠️ **仓库里 175 / 176 / 177 那三个 `SET ROLE service_role;` 是反面教材，别照抄**（166 只在注释里描述过这个模式，没有真的执行）。baseline watermark 是 364，它们早被烤进 `schema_baseline.sql`，**在 CI 里一次都没执行过** —— "有先例"不等于"验证过"。watermark 之上唯一相关的先例 365 结论正好相反，原文：*"This migration therefore does NOT `SET ROLE`. It runs as the connecting role (postgres in CI = the owner). That single difference is the fix."*
  **要写受保护的列**（`issues.execution_state` 等被 mig 170 allowlist trigger 挡住的），用事务级 trigger 抑制，不要切角色：
  ```sql
  BEGIN;
  SET LOCAL session_replication_role = replica;  -- 不碰系统目录，COMMIT 时自动恢复
  UPDATE public.issues SET ... ;
  COMMIT;
  ```
  必须是 `SET **LOCAL**`：`run-migration.yml` 把待跑迁移拼成一个批次文件喂给同一个 psql session，session 级 SET 会泄漏到后续每一条迁移（176 就是这么静默失败了几个月）。比 `ALTER TABLE ... DISABLE TRIGGER` 安全 —— 后者要改系统目录、拿 ACCESS EXCLUSIVE 锁，中途失败还可能把 trigger 留在关闭状态。副作用是同时抑制 `issues_touch_updated_at`（被修的行保留原 `updated_at`），对内部数据修复而言是想要的：修复不该伪装成用户编辑。
  **改完自查**：跑完确认 `SHOW session_replication_role` 回到 `origin`、目标表 `pg_trigger.tgenabled` 仍是 `'O'`，再以 postgres 跑一次同样的 UPDATE 确认**被拦**（正向对照，证明抑制只限于那一个事务）。两次撞墙记录：365（176 的 SET ROLE 导致 DROP TABLE 权限不足）、405（PR #1695，同一句在 ephemeral 库 `permission denied for table issues`）。
- **`env_file` 改动必须 `docker compose up -d` 重建容器**，`docker restart` 不会重读。同理 compose 的 service/env/volume/ports 改动也必须 `up -d`。
- **self-hosted runner 会僵死**。网络抖动导致 session 失效后 runner 不会自愈（日志里刷 `broker.actions.githubusercontent.com` 500 或 `unexpected EOF`），GitHub 侧显示 `offline` 而进程还活着。修：`sudo systemctl restart actions.runner.iocrazy-nous-app.gpu-runner.service`。查状态：`gh api /repos/iocrazy/nous-app/actions/runners`。
- **托管 runner 依赖账户付款正常**。付款失败时所有 `ubuntu-latest` job 会在 2 秒内 failure 且**零步骤执行**（`runner_name` 为空），annotation 里写着 `recent account payments have failed`。此时 `CI`/`actionlint`/`pr-behind-check` 全红、前端链也发不出去，但 **self-hosted 的后端链不受影响**（不计费）。
  **判别法**：`gh api repos/iocrazy/nous-app/actions/runs/<id>/jobs --jq '.jobs[] | "\(.name) runner=\(.runner_name) steps=\(.steps|length)"'` —— `runner` 为空 + `steps=0` 就是这种假红，不是代码问题。
  **切 public 是解**（2026-07-26 实测：private 下重跑两轮都被拦，切 public 后立刻拿到真实 runner，全套 6 分钟跑绿）。但 ⚠️ **repo 会自己弹回 private**（免费额度用尽时 GitHub 强制回退，2026-05-29 一天触发 5 次，见 [[reference_github_repo_visibility_revert]]），所以"CI 突然又假红"要先复查 `gh repo view --json visibility`。
- **`pr-behind-check.yml` 两档行为不同，别一概而论**（2026-07-26 查清）：
  - **≥30 硬拒绝档是好的** —— 只用 `echo ::error:: + exit 1`，不调 GitHub API，不依赖写权限。
  - **≥15 软警告档曾长期是坏的** —— 它用 `peter-evans/create-or-update-comment` 发 PR 评论（写操作），但仓库 `default_workflow_permissions` 是 `read` 且该 workflow 没声明 `permissions:`，于是 403 `Resource not accessible by integration`，把提醒变成红 CI。因为触发条件是 `15 ≤ behind < 30`、平时 PR 都更新，这一步长期被 skip，所以从没成功过一次也没人发现。已加 job 级 `permissions: pull-requests: write` 修复（不动仓库全局默认，按需提权更安全）。
  - 顺带修了 `if:` 里的字符串比较（step output 恒为字符串，`>= '15'` 边界会骗人），改用 `fromJSON()` 强制数字比较。
  - 托管 runner 不可用时两档都发不出，落后检测只能靠本地 `bash scripts/branch-health.sh`。

### 多机协作（gpupc + Mac mini）

两台机器并行开发时按物理角色分工，能从源头消掉大部分冲突：

| | gpupc | Mac mini |
|---|---|---|
| 角色 | 部署机（self-hosted runner + 生产栈 + GPU 推理） | 开发机 |
| 主管 | `backend/**`、`deploy/**`、`.github/workflows/**`、`supabase/migrations/**` | `frontend/**`、`admin/**`、浏览器扩展、iOS Shortcut |

铁律：**同一时刻只有一台机器往 master 推**，谁先推谁赢，另一台 `git rebase origin/master`。

同机多个 Claude 会话必须各用一个 worktree，否则会互相提交到对方分支（2026-07-25 实际发生过：另一个会话的前端 commit 落进了 DBOS 修复的 PR）：

```bash
./scripts/worktree-manager.sh create feat/xxx   # 自动分配独立端口
bash scripts/sync-worktree.sh                   # rebase + 首次运行会启用 rerere
```

### 已知缺口

- **admin 没有自动部署**。gpupc 的 `nous-admin` 是 compose 本机 build（`context: ../../admin`），但 `deploy-gpu.yml` 的 paths 不含 `admin/**`。补齐前提是先决定 build arg `NOUS_ANON_KEY` 怎么进 CI（缺了会 build 出空 anon key 的 admin）。当前只能手动：`cd deploy/gpu-server && NOUS_ANON_KEY=<key> docker compose up -d --build admin`。
- **`deploy-frontend.yml` 只是校验、不部署**，且它轮询的 `version.json` `commitSha` 依赖构建环境变量（见「前端链的关键设计」）。目前仅在设了 `PROD_FRONTEND_VERSION_URL` 仓库变量时才跑，未配置即 no-op。要么指向 `https://app.nous.ink/version.json` 让它真正生效，要么退役 —— 现在这样"存在但不生效"最容易误以为有守卫。
- **migration 与代码部署无顺序保证**。`run-migration.yml` 与 `deploy-gpu.yml` 独立触发，同一个 PR 里既加 migration 又改依赖它的代码时，两者谁先完成不确定。

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

## GBrain Configuration (configured by /setup-gbrain)
- Engine: pglite
- Config file: ~/.gbrain/config.json (mode 0600)
- Setup date: 2026-05-05
- MCP registered: yes (Claude Code, user scope)
- Memory sync: off
- Current repo policy: read-write
- Imported pages: 149 (nous markdown / docs)

## GBrain Search Guidance (configured by /setup-gbrain)
<!-- gstack-gbrain-search-guidance:start -->

GBrain is configured locally (PGLite). Prefer it over Grep when the question is
semantic or you don't yet know the exact identifier. Two indexed corpora:
- This repo's docs/markdown (149 pages, source registered as gstack-code-nous).
- ~/.gstack/ curated memory (when artifacts accumulate).

Prefer gbrain when:
- "Where is X handled?" / semantic intent: `gbrain search "<terms>"` or `gbrain query "<question>"`
- Symbol-aware code questions: `gbrain code-def <symbol>` / `gbrain code-refs <symbol>` / `gbrain code-callers` / `gbrain code-callees`
- "What did we decide last time?": `gbrain search "<terms>" --source gstack-brain-<user>`

Grep is still right for known exact strings, regex, multiline patterns, and file globs.
Run `/sync-gbrain` to refresh; `/sync-gbrain --full` for a full reindex.

<!-- gstack-gbrain-search-guidance:end -->
