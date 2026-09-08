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

### 边界 mock 必须用真实 JSON 形状（2026-08-12 血泪）

任何模拟后端 HTTP 响应体的 mock（`page.route` fulfill、桩 `fetch` 等）必须照抄后端真实返回的 JSON 形状——字段类型（number vs string）也算，不能按前端书写习惯"美化"。典型陷阱：Snowflake BIGINT 主键/外键，`scenes`/`shots` 两个 router 原样返回 ORM dict 是 JSON **number**，`canvases` router 显式 `str(out["id"])` 是 JSON **string**——同一类 id 在不同资源上形状不同，是真实的、故意的，不是可以"统一"的漂移。

2026-08-12 分镜画布 P0 事故的根因之一就是这条纪律缺失：全仓库手写 fixture 一律用理想化字符串 id（`'shot1'`、`'200'`），从未有测试真正跑过数字 id 分支，导致"对账索引按字符串建、真实响应给数字"的类型不匹配在生产环境才第一次触发（102 个重复节点、画布空白）。区分口径见 `frontend/e2e/helpers/realShapes.ts` 顶部注释：模拟"HTTP 响应体"要用真实 wire 形状；模拟"已归一化的前端 service 函数"（如 `vi.mock('editor/sceneService')`）用该函数自己文档化的返回类型（通常是 string）才是对的——判断标准是你在模拟哪一层边界，不是"哪个更像 TypeScript"。

## 防御模式（2026-08-22 立约）

来源：对 `deepseek-harness` 的侦察借鉴（设计重写，非代码复制）。下面每一条都是**已经出过或差点出过的缺陷类**，写成阻止它复发的规则。写生命周期、并发、子进程、拆卸代码之前先读这一节。

### 正交的结果各自独立上报

一次执行可以同时是好几件事——进程可以**既超时又 exit 0**（它把信号捕获了）。`timed_out` / `signal` / `exit_code` 每个都要独立暴露，**绝不能把一个标志的上报嵌进另一个标志的分支里**，否则调用方会把一次被腰斩的运行读成干净的成功。

同族的既有教训：`reference-empty-output-is-not-a-negative-result`（超时不许当否定结论）、发布回读「页面是空的」不等于「作品没发出去」。

### 公共契约两侧都要遵守

一个实现如果会收到同一个结果的多种表示，**必须在公共 API 边界归一化后再返回**。典型：provider adapter 可能 raise，也可能返回 `finish{kind:"error"}`；调用方不该去猜「我捕获的这个异常是 provider 给的、还是中间件的 bug、还是我自己组装错了」。

- 归一化后的契约写在类型定义处，不写在某个调用点的注释里。
- 每一种来源形态都要**通过真实消费方**跑一遍测试。
- 与既有的「边界 mock 必须用真实 JSON 形状」是同一条纪律的两面。

### 异步状态不是同步状态

**不要把「整体空闲」当成「我那条消息的结果」。** 多个排队的 follow-up、steering、注入的工作可能共享同一段 `running` 区间；而取消或销毁会把还没启动的项直接丢掉。

- 真正需要对一次运行负责的调用方，**必须显式定义自己的区间**（例如：从这条消息落进持久收件箱起，到下一次整体 idle 为止），并且把选出来的输出描述成「这段区间内发生的」，而不是「这条消息导致的」。
- 反向同样要处理：**如果等待的那个状态转移永远不会发生，等待就会永久挂住**——「无事可等」这个分支必须显式写出来，不能靠超时兜底。

已在本仓踩过的同族：DBOS `phase=completed` 但 `subtitle` 停在 Initializing（返回 dict 被当成 SUCCESS）；转录→摘要 follow-up 的登记表只活在前端内存里，刷新即丢。

### Dispose 必须到达静止，而不只是发出请求

一个只负责「发 kill / abort 就返回」的拆卸会留下孤儿。正确顺序是：

1. **先摘监听器 / 注销通知**——这样迟到的完成回调是静默的；
2. 再 kill；
3. 再 `await` 子进程真正退出（`kill` → `await done`）。

清理必须是 async 的。参照 `backend/app/agent_framework/process_lifecycle.py` 与 `kill_tree.py`。

### 分发器要容纳回调异常

用户提供的监听器抛异常，**不能**让它所在的 promise/task 失败，也不能饿死排在它后面的监听器。分发循环整体包 try/except 并记日志；一个坏订阅者永远不该打断核心生命周期。

⚠️ 与「catch 静默吞错」不冲突：这里要求的是 `except Exception as e: logger.error(...)`，**容纳并记录**，不是 `except: pass`。

### 绝不把宿主环境和可预测路径交给不可信输出

- **子进程环境要擦洗**：spawn 出去的命令不该看到 `*KEY*` / `*SECRET*` / `*TOKEN*` / `*PASSWORD*`，否则凭证会从子进程的输出、`env` 转储、崩溃日志里漏出去。
- **临时文件用私有目录**：0700 目录 + 随机文件名 + 独占创建（`O_EXCL`，0600）。可预测的世界可读路径招来符号链接竞争和信息泄露。

✅ **第一条已于 2026-08-26 收口（PR #2009）**。一期实测 35 处 spawn 零擦洗；修法不是 35 处补丁，而是骑在 `safe_popen_kwargs()`（`app/agent_framework/process_lifecycle.py`）这个 45 处 spawn 站点都 splat 的咽喉点上，每个站点零改动覆盖：

- 名模式（大小写不敏感）：`KEY / SECRET / TOKEN / PASSWORD / PASSWD / CREDENTIAL / DSN / DATABASE_URL`；**值模式**：任何 `scheme://user:pass@host` 形状的值不论名字一律丢（`REDIS_URL` 名上不命中，值上命中）
- 误报的回答是往 `SAFE_ENV_NAMES` 加一行（如 `TOKENIZERS_PARALLELISM`），**不是放宽模式**
- 子进程真需要某个密钥 → `safe_popen_kwargs(env_keep=("X",))`；子进程专属变量 → `env_extra={...}`。**别再自己传 `env=`**——与 splat 并存是重复关键字、spawn 时 TypeError，有源码扫描守卫钉住这一类
- 唯一例外：`services/workforce/isolated_runner.py` 跑的是**我们自己的** Python 代码、需要数据库，刻意保留完整环境且不走咽喉点（有测试钉住它不会悄悄改用）

真栈验证法（比端到端 fetch 更决定性——fetch 会撞抖音反爬）：`docker exec -i -w /app nous-backend /app/.venv/bin/python -` 喂脚本，经真实 `safe_popen_kwargs()` 拉 ffmpeg/ffprobe/yt-dlp/node，断言父进程有 `SUPABASE_SERVICE_ROLE_KEY` 而子进程 env 没有、四个二进制 rc=0。⚠️ `docker exec` 喂 stdin **必须 `-i`**，否则 heredoc 静默丢失、零输出还 exit 1。

### 形似链接的路径要用 unlink 删

可能是符号链接或 Windows junction 的路径，先 `os.path.islink()` 判定再 `os.unlink()`：unlink 只删链接本身、遇到真目录会拒绝，所以它永远不会顺着链接删到目标里去。递归删除只留给**已知是真目录**的路径。

---

## 提示词与模型可见面纪律（2026-08-22 立约）

### Model Experience 三问是 agent/prompt 模块 README 的强制段

任何会改变**模型能看到什么**的模块（prompt 组装、上下文注入、工具 schema、skill 装载、压缩），其 README 必须包含这三个小节，顺序固定：

| 小节 | 回答什么 | 常见写错 |
|------|----------|----------|
| `What the model sees` | 模型实际收到的文本/结构。稳定的字面量**原样贴出来**（markdown 围栏），数据驱动的部分才用概括 | 描述代码怎么写的，而不是模型收到什么 |
| `Token effect` | 这段内容占多少、增长是否有界、什么时候会被压缩掉 | 只说"很小"，不说边界条件 |
| `KV Cache effect` | 是 append-only、稳定前缀、替换了更早的 token，还是另起一次独立请求；**本模块的哪些改动会让复用失效** | 把"provider 缓存是否命中"写成本模块的承诺——那不在模块契约内 |

再加一节 `Known Limitations and Deferred Work`：记录**长期存在的消费方缺口**和不显然的维护约束；普通待办留在源码 TODO 里，不进 README。

示范见 `backend/app/services/ai/prompts/README.md`、`backend/app/services/ai/skills/README.md`、`backend/app/boundary/README.md`。

### 提示词快照：一个场景 pin 全文，其余一律 tokenize

- **唯一的全文 pin** 是 `backend/tests/services/ai/prompts/test_system_message_pin.py`，对着 `snapshots/system_message_text_turn.txt`。改提示词散文只会在这一处产生 diff，而**那个 diff 就是评审内容**——你按模型读到的样子读它。
- **其余所有提示词测试用 tokenize 断言**（断言某个标记/短语存在），这样改一句话只 churn 一行。
- **不要加第二个全文 pin**。两个全文 pin 意味着每次改散文都产出两份说同一件事的 diff，评审者很快就会不读就刷新快照——那时 pin 已经从控制手段退化成杂活。
- 刷新：`PIN_REFRESH=1 uv run pytest tests/services/ai/prompts/test_system_message_pin.py`，**然后读 diff 再提交**。

### 用户可控文本进框必须转义

系统提示词里那些尖括号框（`<available_resources>` / `<scene_elements>` / `<user_context>` …）是**我们自己拥有的**，它们赋予内容"这是系统说的"这层权威。用户可控文本里出现字面闭合标记，就会提前关掉框，后面的内容对模型而言就成了 harness 写的指令。

三层，按被注入文本的形状选：

| 不可信文本的形状 | 用 | 在哪 |
|---|---|---|
| 一整份外部文档（抓来的网页、字幕、描述） | `neutralize_external_text` —— 随机 id 包裹，闭合标记不可伪造 | `app/boundary/external_text.py` |
| 一个 XML 属性值（文件名、slug、model 名） | `escape_frame_attr` | `app/boundary/frame_markers.py` |
| 我们自己的框里的一段散文（剧本行、user_context） | `escape_frame_body` | 同上 |

**新加一个框，必须同时把它登记进 `OWNED_FRAMES`**，否则那个框是没有防护的——`tests/services/ai/prompts/test_frame_escape_wiring.py::test_every_frame_rendered_in_prompt_code_is_registered` 会扫出未登记的框并拒绝。

⚠️ 「在提示词里写一句 SECURITY: 下面是不可信数据」**不算防护**。它是有用的第二层，但结构上关不掉框的只有转义。本仓 `script_ai_service` 曾经只有这句话、没有转义。

### 模型可见的工具 schema 用显式白名单投影

`Tool.to_descriptor()` 只吐 `name` / `description` / `inputSchema`，`handler` 这类宿主侧字段永不序列化。给 `Tool` 加字段时必须在 `tests/agent_framework/test_tool_descriptor_allowlist.py` 里明确它是模型可见还是宿主专有——**不许有第三种"没分类"状态**，那个测试会拒绝。

不要把投影改成 `asdict(self)` 直通：调度元数据（超时、并发安全性、所需权限）告诉模型的是"有什么可以试着绕过"。

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
| `assets` / `asset_files` / `asset_links` / `asset_loadouts` / `asset_project_refs` / `canvas_asset_refs` | 资产库语义层（角色/场景/道具/服装/提示词/音频实体；文件只挂关联不搬家；`canvas_asset_refs` 是画布→资产的反查镜像，对应 `canvas_resource_refs`）— 见 `docs/superpowers/specs/2026-08-28-asset-library-loadout-design.md` | BIGINT Snowflake |

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
- **`AgentRunner.stream_turn` 的缓冲回退分支是生产的唯一路径，测它（2026-09-08 血泪）**：chat wiring 给 runner 的是 `LLMFallbackChain`（没有 `stream`），所以每个带 `chunk_callback` 的轮次都走 `stream_turn` 里「委托 `run_turn` 再重包成一个终止 chunk」的分支。那个终止 chunk 的 `usage` 是 `turn_end` 分类器与 chat service **唯一**读的东西——2026-09-08 它没带 `stop_reason` / `hook_decision`，AskUser、预算停机、PauseHook 三条链在真栈上全被记成 `completed`，回答一律 409，而 `run_turn` 直连与真流式两条路径的单测全绿。给 run_turn 结果加任何新标志时，必须在 `tests/runner/test_turn_end_reasons.py` 用「adapter 无 `stream` 属性」的用例证明它穿过了这个分支（PR #2188）。
- **触发路径必须类型化失败回显**：`attachment_failures` 字段后端早就在返回（`ai_library_chat_service.py`），前端却整整没读过——因为没有强制约定"新触发路径要连带写失败分支"。用户动作→agent 触发的每条路径必须返回类型化结果(成功/失败/原因),silent no-op 不可接受——与'DBOS 失败必须 raise'同族。新增触发路径时先写失败分支的用户可见回显。
- **裸 SQL 全量 ORM 化（2026-08-04 立约）**：新代码禁止新增 `text()` 裸 SQL（结构性例外：`dbos.*` schema 的两个文档化访问点、PG 系统目录诊断/schema 探针）。存量迁移期间（Phase B/C 未迁文件）如需改动裸 SQL，必须换用 `app/db/scoped_sql.py` 的 `scoped_sql()`（唯一新入口），显式声明 `scope=Scope(...)` 或 `system=True, reason="..."`——两者都不传会 raise。决策与分期见 [`docs/decisions/2026-08-04-raw-sql-to-orm-full-migration.md`](docs/decisions/2026-08-04-raw-sql-to-orm-full-migration.md)。
- **"版本统一"有边界：先问这个版本是不是我们能选的（2026-08-06 血泪）**。`browser/` 的 `requires-python` 必须是 `>=3.12`，**刻意**与 backend（`>=3.13`）和根 `.python-version` 不同 —— 它的基础镜像是 `mcr.microsoft.com/playwright/python:v1.52.0-noble`，noble 自带 **Python 3.12.3**，版本由上游决定，而那个 tag 又必须跟 `playwright` pin 一起动。
  把它"统一"成 `>=3.13` 的后果（已在生产复现）：镜像里找不到 3.13，uv 转而下载 managed python 装进 **root 的 home**，venv 解释器指过去；而 Dockerfile 只 `chown -R pwuser:pwuser /app` 然后 `USER pwuser` —— 非 root 碰不到那个解释器，容器起不来，报 `/app/.venv/bin/uvicorn: /app/.venv/bin/python: bad interpreter: Permission denied`。smoke 拦下并自动回滚。
  同一次改动还踩了第二个：把 `[project.optional-dependencies]` 改成 `[dependency-groups]`。**`browser/Dockerfile` 那层是裸 `uv sync`，靠 optional extras 不被默认安装来让生产镜像不含 pytest/httpx**（原因就写在该 Dockerfile 第 37-38 行的注释里），而 dependency-groups 的 dev 组默认就装。实测镜像里 `PYTEST-PRESENT`。backend 侧不受影响 —— 它原本的 `[tool.uv] dev-dependencies` 同样默认安装，行为没变。
  **教训**：改"看起来不一致"的声明前，先读它旁边的注释，再确认那个值是不是我们能自由选的。声明不一致 ≠ 漂移，有时正是上游约束的忠实记录。验证手段：拿基础镜像 + 改后的 `pyproject.toml`/`uv.lock` 构建一层最小 Dockerfile，看 `pyvenv.cfg` 的 `home =` 落在哪、`.venv/bin/` 里有没有 pytest、`su pwuser -c '.venv/bin/python -V'` 能否执行。
- **测试进程必须与本机网络环境隔离（2026-08-06 立约）**：`backend/tests/conftest.py` 与 `browser/tests/conftest.py` 各有一个 session 级 autouse fixture，把继承来的 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` / `NO_PROXY` 全部摘掉。
  起因：本机 shell 带 `ALL_PROXY=socks5://127.0.0.1:7891`（mihomo），httpx 从环境读到 socks 代理就在**构造时**抛 `ImportError: Using SOCKS proxy, but the 'socksio' package is not installed`——**87 个失败、跨 19 个文件，没有一个是真缺陷**。CI 上没有代理变量，同一套测试全绿。**一套结果取决于谁的机器在跑的测试，不构成门禁。**
  ⚠️ 这跟「代理问题一律在 mihomo 层解决、不要 unset 绕过」不冲突：那条针对的是**真的要连服务**的场景，依然有效。单元测试**碰到网络本身就是 bug**，摘代理是环境隔离不是绕过。故意验证代理行为的测试不受影响——它们自己 `monkeypatch.setenv`，在 fixture 之后生效且逐测试还原。

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

## Asset Library (P0 数据层) — mig 445/446

- 语义实体在 `assets`，文件通过 `asset_files` 的 slot 挂上来（`resources` 行不动，见 `app/services/assets/slots.py` 的 slot 表与 readiness 派生）；`asset_loadouts` 是角色的「造型」子集，必须是 `asset_links` 的子集，解链时 `strip_from_loadouts` 同步剔除。
- `scope_id` 指向 `teams`，**仅系统预设为 NULL**（`assets_scope_or_preset` CHECK），预设对所有 scope 可读且只读。两个唯一索引都是 partial（`WHERE deleted_at IS NULL` / `WHERE is_default`）—— 软删除会释放同名占用。
- 两个 repository 的 ORM 语句唯一的真执行覆盖是 `backend/tests/db/test_assets_repository_integration.py`（挂在 `schema-drift.yml` 上，需 `INTEGRATION_DATABASE_URL`）；单测里的 session 是桩的，跑绿不代表 Postgres 接受。
- slot 表在前后端各存一份（`app/services/assets/slots.py` 与 `frontend/components/assets/assetSlots.ts`）。改一边必须改另一边 —— `backend/tests/services/assets/test_slots_frontend_mirror.py` 直接读 TS 文件做比对（含顺序），前端自己那个测试是把同样的值又硬编码了一遍，单侧修改它照样绿。
- P0 只到数据层 + `/api/v1/assets` 基础 API，无 UI；P1 已补上 Generated 收件箱（见下），其余分期见 spec §9。
- **Generated 收件箱**（P1, 2026-08-29）= `generated_media` + `review_state`；API `/api/v1/generated`；Project Assets / Temp 视图已退役；temp sweeper 已停调度，清理走 `/generated/cleanup`（两步：先 dry-run 预览再确认，不自动、不静默 —— 这正是当年退役 TTL 的原因）。
- **资产库 UI**（P2, 2026-08-30）：`资源库 → Assets` 三条路由 —— `resources/assets`（全部）/ `resources/assets/:assetType`（单类型货架）/ `resources/assets/item/:assetId`（实体页）。⚠️ 实体页侧栏的 `Send To Canvas` / `Send To Agent` 是**刻意 disabled** 的占位（title 写着 Arrives with P4 / P5），不是坏按钮——别当缺陷去修。
- **项目工作区素材页**（P3, 2026-08-31）：侧栏 Characters / Locations / Props / Costumes 四个模块已改读 `GET /api/v1/projects/{pid}/assets`（`asset_project_refs` 的项目视图），一键导入落 `POST /projects/{pid}/assets/import-from-script`（`source='script_import'`）。`project_characters` / `project_lib_entities` 两张项目本地表**已于 mig 447 改名 `_legacy_*`**（生产迁移 2026-09-02 跑完并对账全中），`/projects/{id}/characters*` 与 `/projects/{id}/lib/*` 全部端点、两个 repository、两份 schema 同批删除；**当时**唯一剩下的读方是迁移 workflow（窗口期内可应急重跑）。**两张表已于 mig 451（P6）DROP**，迁移 workflow、两个 ORM 模型与 `_BACKFILLS` 注册项同批删除（schema-drift 门禁两向零容忍，拆不开）。⚠️ `app/services/assets/legacy_refs.py` 与 `GET /assets/resolve-legacy` **不在删除之列**——`attrs.legacy_ids` 里是保留改名前拼写的 provenance 字符串标签，不是 SQL 标识符，表没了照常映射。
- **画布集成**（P4, 2026-09-02）：画布上新增 `asset` 节点（引用 `assets`，自带 loadout 与参考文件勾选）；生成走 `GET /assets/{id}/bundle?model=&loadout_id=` 按 provider 能力裁剪（能力表只在代码里，`config.yml` 无此键），参考图以 `/api/v1/resources/{id}/cover` 投递并由**资源参考桥**在后端物化（与 `/api/v1/generated-media/` 并列，解析不了的进 `dropped_refs` 绝不静默）；`canvas_asset_refs` 由画布保存路径维护（失败只记日志不阻塞保存），支撑 `GET /assets/{id}` 的 `used_in.canvases` 与两个反查端点；旧的 character / location / prop 智能卡**加载时**经 `GET /assets/resolve-legacy` 就地迁成 asset 节点，查不到的打 `Unmigrated` 标记而不靠名字猜。
- **统一提示词库**（画布 Library 面板 P3, 2026-09-05）：提示词 = 模板资产 ∪ 带 `gen_prompt` 的图片 ∪ 带 `slide_prompts` 的图集，后端 `GET /api/v1/prompts` 归一成 PromptEntry（`app/services/prompts/`），资源库「提示词」tab 与画布面板 Prompts 页都只读它，浏览器不再直查提示词。`resources.prompt_origin`（mig 455）记录正向文字的最后写入方 typed / extracted / captioned，六个写入方都要 `stamp_origin`（`tests/services/prompts/test_origin_wiring.py` 钉住）。图集逐张插入；「存为模板」把图挂 `examples` 槽、文件不搬。spec：`docs/superpowers/specs/2026-09-05-unified-prompts-library-design.md`。

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

**`ci.yml` 已于 2026-09-07 迁回 `ubuntu-latest`（五个 job 全部）**。它曾在 2026-08-06 迁去 self-hosted，理由是账户付款失败让托管 runner 的 job 全部 2 秒内被拦；那个理由已失效（同日 `lint-workflows` / `deploy-pages` / `schema-drift` 在托管 runner 上都拿到真实 runner 并跑绿），而代价在累积：单 runner 让五个 job 串行且与 `deploy-gpu` 争用（2026-09-06 两次 PR 分别排队 3h26m / 3h30m）、gpupc 走 5G 计费网络每个 job 都在烧流量、CI 构建与生产 Postgres 同盘会拖慢后者**且没有任何探针会告诉你**。

⚠️ **要再迁回 self-hosted 是五处 `runs-on`，不是三处** —— 旧注释写的"三处"写于 `rust` 与 `codex-daemon` 两个 job 加入之前，照它改会漏两个。同时要把三处云端缓存重新关掉（`setup-node` 的 npm、两处 `setup-uv` 的 `enable-cache`、rust 的 `Swatinem/rust-cache`）。

⚠️ **self-hosted 安全性的真正依据不是 fork 守卫，是触发器**。留在 gpupc 的三条链（`deploy-gpu` / `run-migration` / `config-drift`）**没有一个吃 `pull_request`** —— 全是 `push: branches:[master]` + `schedule` + `workflow_dispatch`，所以 fork 代码没有任何路径能到达那台机器。

`ci.yml` 五个 job 的 fork 守卫**刻意保留**：

```yaml
if: github.event.pull_request.head.repo.full_name == github.repository
```

仓库现在是 **private**（外部 fork PR 本就不会发生），删掉换不回任何东西；而万一将来再迁回 self-hosted，少这一行就等于把生产部署机交给任何人。**往 self-hosted 上加任何 `pull_request` 触发的 workflow，都必须带这一行。**

runner 的 workspace 与生产数据同盘（`/media/heygo/program`），`actions/checkout` 默认 `clean: true` 会 `git clean -ffdx`，所以 `node_modules` / `target/` 不累积；代价是每次重装依赖。

⚠️ **CI job 会写 runner 用户的 home，必须逐个隔离**。runner 以 `heygo` 跑，任何往 `~` 写东西的 action 都在动**活人正在用的开发环境** —— 托管 runner 上这类破坏无所谓（机器用完即销毁），self-hosted 上是真的破坏。

已经栽过一次：迁过来第一次跑 rust job，`~/.cargo/bin/rustup` 二进制就没了。那目录下 `cargo` / `cargo-clippy` 全是指向 `rustup` 的符号链接，rustup 一丢，本机 `cargo` 直接 `command not found`（而 `~/.rustup/toolchains/` 里的实体完好，纯粹是 shim 断了）。修复：`curl https://rsproxy.cn/rustup/dist/x86_64-unknown-linux-gnu/rustup-init` 拿到二进制直接放回 `~/.cargo/bin/rustup`（`rustup-init` 和 `rustup` 是同一个程序，靠 argv[0] 区分），**不要跑它的安装流程** —— 那会改 shell 配置文件。

预防：往 self-hosted 加任何装工具链的 step，先确认它写哪里，把它导到 runner 的 tool cache。⚠️ 用 step 写 `$GITHUB_ENV`，**不要**写成 job 级 `env:` 配 `${{ runner.tool_cache }}` —— `runner` context 在 job 级 env 里不可用（那里只认 `github`/`inputs`/`matrix`/`needs`/`secrets`/`strategy`/`vars`），actionlint 会直接拒绝：

```yaml
- name: Keep the rust toolchain out of the runner user's home
  run: |
    echo "CARGO_HOME=$RUNNER_TOOL_CACHE/rust/cargo" >> "$GITHUB_ENV"
    echo "RUSTUP_HOME=$RUNNER_TOOL_CACHE/rust/rustup" >> "$GITHUB_ENV"
```

`$RUNNER_TOOL_CACHE` 是 runner 自动导出的环境变量（托管 runner 上是 `/opt/hostedtoolcache`），所以这样写切回 `ubuntu-latest` 也不会坏。`setup-node` / `setup-python` / `setup-uv` 自带隔离（装进 `_work/_tool/`），**`dtolnay/rust-toolchain` 不自带** —— 它认 `RUSTUP_HOME`/`CARGO_HOME`，不设就用 home。

**改 workflow 前先在本地跑 actionlint，别拿 CI 当语法检查器**（一轮 CI 十几分钟，actionlint 一秒）：

```bash
curl -sSL -o /tmp/al.tgz https://github.com/rhysd/actionlint/releases/download/v1.7.7/actionlint_1.7.7_linux_amd64.tar.gz
tar xzf /tmp/al.tgz -C /tmp actionlint && /tmp/actionlint   # 无输出 + exit 0 即干净
```

CI 里那步用的是 `docker://rhysd/actionlint:latest`，本机 docker pull 常被网络打断，二进制更稳。

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

### 环境版本一览（怀疑"是不是被人偷偷改了"时先看这张表）

| 位置 | 版本 | 谁决定 |
|------|------|--------|
| `.python-version` | **3.13** | 我们（唯一真相，CI 三处都读它） |
| `backend/pyproject.toml` | **>=3.13** | 我们 |
| `nous-core/pyproject.toml` | **>=3.13** | 我们（pyo3 无 abi3，wheel 钉死 cp313） |
| `Dockerfile`（pin 的 digest） | **3.13**-slim | 我们 |
| `browser/pyproject.toml` | **3.12** | ⚠️ **上游** —— 见下 |
| `.nvmrc` | **22** | 我们（CI 与 `deploy-pages` 都读它） |
| `frontend/` `admin/` Dockerfile | node **22** | 我们 |

⚠️ **`browser/` 是 3.12，这是刻意的，不是漂移**：它的基础镜像是 `mcr.microsoft.com/playwright/python:v1.52.0-noble`，noble 自带 **Python 3.12.3**，版本由上游 playwright 镜像决定，而那个 tag 又必须跟 `dependencies` 里的 `playwright` pin 一起动。2026-08-07 曾把它"统一"成 `>=3.13`，结果 uv 找不到 3.13 就下载一个装进 **root 家目录**，而 Dockerfile 只 `chown /app` 后切 `USER pwuser` —— 容器起不来（`bad interpreter: Permission denied`），生产 smoke 拦下自动回滚。要真统一，是换基础镜像（自建 python:3.13 + 自装 chromium），不是改这一行。

⚠️ **gpupc 的系统 python 是 3.14**（`/usr/bin/python3`），跟本项目无关 —— uv 管的项目一律看 `.python-version`。但它会从 PATH 漏进构建：pyo3 的 build script 就是这么抓到 3.14 并报 "newer than PyO3's maximum supported version (3.13)" 的，所以 `ci.yml` 的 rust job 显式钉 `PYO3_PYTHON`。**诊断时别拿 `python3 -V` 当项目环境**。

### 红 CI 的诊断顺序（先读日志，再谈假设）

同一批红 CI 曾被连着误诊两次（先判"计费假红"、再判"要迁 self-hosted"），真相是第三种。**第一步永远是 `gh run view --job <id> --log-failed` 看首个 error**，再套下面的表：

| 首个 error | 含义 | 处置 |
|---|---|---|
| `runner_name` 为空 + `steps=0` + 2 秒 fail | 账户计费失败（托管 runner 被拦） | 临时走 self-hosted（不计费）。⚠️ 别指望"切 public"，见下。**2026-09-07 实测计费已恢复**，托管 runner 正常 |
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

**前端上线验收 = version.json SHA + 真栈走查，二者缺一不可（2026-08-12 血泪）**：分镜画布上线（2026-08-11）后 `version.json`/`readyz`/CI 全绿过，用户真机点开却是整片空白——验收从来没有拿真数据把 UI 真正点一遍，直到用户自己撞上才发现。"测试绿 ≠ 真栈正常" 是上面「读正常 ≠ 服务正常」的前端版。

现在的口径：每次前端发布后跑一遍

```bash
cd frontend && npm run e2e:prod
```

`frontend/e2e-prod/walkthrough.spec.ts`（独立 Playwright project，`testDir` 与常规 `e2e/` 套件分开，不进 CI）用专用调试账号真登录，对 `PROD_BASE_URL`（默认 `https://app.nous.ink`）走一遍核心路径：总览手风琴展开 → 侧栏进分镜模块 → 场次卡可见 → 画布 tab 真实节点可见 → 分镜列表 → 剧本编辑器加载。全部断言用可见性（`toBeVisible`/`:visible`），不用裸 `toHaveCount`——分镜画布那次事故的教训就是重复 id 节点在 DOM 里"存在"但永久 `visibility:hidden`，`toHaveCount` 在坏版本上照样通过。详见 `frontend/e2e-prod/README.md`（凭证约定、fixture 数据、"个人项目"URL scope 与 `team_id` 列的坑）。

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
- **托管 runner 依赖账户付款正常**（**2026-09-07 复查：已恢复正常，`ci.yml` 已迁回托管**）。付款失败时所有 `ubuntu-latest` job 会在 2 秒内 failure 且**零步骤执行**（`runner_name` 为空），annotation 里写着 `recent account payments have failed`。此时 `CI`/`actionlint`/`pr-behind-check` 全红、前端链也发不出去，但 **self-hosted 的后端链不受影响**（不计费）。
  ⚠️ **仓库现在是 private + GitHub Free**，所以 Actions 分钟数是计费资源（2000 分钟/月），也不支持 branch protection。按现有 job 时长估算每次 PR push 约 13–16 计费分钟。额度不够时的下一手是给 `ci.yml` 加 path 过滤（Free+private 无 required check，不存在"skipped 卡住 PR"的风险），但那条注释指出的"门禁静默跳过"语义风险仍然成立。
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
| 角色 | **只做部署**（生产栈 + GPU 推理 + 三条运维链的 self-hosted runner） | **唯一的开发机** |
| 主管 | 生产运行时。**不在这台机器上编辑代码** | 全部代码 —— `backend/**`、`frontend/**`、`admin/**`、`deploy/**`、`.github/workflows/**`、`supabase/migrations/**` |

⚠️ **2026-09-07 起 gpupc 不再是开发机**。`ci.yml` 迁回托管 runner、`nous-admin` 接进 `deploy-gpu.yml` 之后，gpupc 上不再需要开发工作树。

那个目录（`/media/heygo/program/projects-code/repos/nous-app`）降级为**部署 checkout**：只读、没人在上面编辑、由 `nous deploy` 自己 `fetch + reset --hard` 同步。区别是语义上的（有没有人在上面写代码），不是路径上的。`deploy/gpu-server/nous` 的 dirty 守卫把这条约定变成可执行检查 —— 目录一脏，`nous deploy` 就拒绝并逐行列出脏文件，而不是静默 `reset --hard` 抹掉。

从 Mac mini 做运维的通道是 **ZeroTier + SSH**（`heygo@10.0.0.10`，免密已通）。SSH 进去看日志/探针/重启**不等于**在那编辑代码，两者不冲突。完整闭环：本机编辑 → `gh pr create` → CI 在 GitHub → 合并 → `deploy-gpu.yml` 自动在 gpupc 部署 → `gh run watch` 看 smoke → `ssh gpupc 'nous status'` 验真栈 → 本机 `npm run e2e:prod` 走查。

铁律：**同一时刻只有一台机器往 master 推**，谁先推谁赢，另一台 `git rebase origin/master`。

同机多个 Claude 会话必须各用一个 worktree，否则会互相提交到对方分支（2026-07-25 实际发生过：另一个会话的前端 commit 落进了 DBOS 修复的 PR）：

```bash
./scripts/worktree-manager.sh create feat/xxx   # 自动分配独立端口
bash scripts/sync-worktree.sh                   # rebase + 首次运行会启用 rerere
```

### 生产 Supabase 栈的配置有两份，靠 drift 检查兜（2026-08-22 立约）

同一份 compose 配置在 gpupc 上存在**两个互不同步的副本**：

| | 路径 | 谁在用 |
|---|---|---|
| 仓库副本 | `deploy/gpu-server/supabase/` | 没有任何部署链读它 |
| 生产活文件 | `/media/heygo/program/datahub/nous/supabase/` | `mediahub-sb-prod` 项目，**真正在跑的** |

活文件**不在任何 git 仓库里**（`git rev-parse` 报 not a repository），也没有任何机制让两侧保持一致。

⚠️ **改仓库那份不会生效。** 2026-08-22 已实证代价：PR #1964 把 `max_connections` 100→200 写进仓库副本，PR 合了、CI 全绿、`deploy-gpu.yml` 也绿 —— 而生产至今是 100。整条链上没有一处会说出"这个改动其实没生效"，与「`backend.env` 静默盖掉 `config.yml`」同族。

**为什么部署链不管它**：`deploy-gpu.yml` 的 paths 含 `deploy/gpu-server/**`（所以改这个目录**会**触发一次部署，更容易误以为生效了），但那条链跑的是 `up.sh --build backend worker gateway browser` —— 那是 `gpu-server` 项目，根本不含 db。

**守卫**：`.github/workflows/config-drift.yml`（self-hosted gpu，push 到 master 碰该目录 / 每日 02:00 UTC / 手动 dispatch），跑 `scripts/check-config-drift.sh`。比对集是仓库侧被 git 跟踪的文件，排除 `.env.example` 与 `README.md`。

退出码三态，**2 与 0 必须分开**：`0` 一致 / `1` 漂移 / `2` 检查本身没跑起来（活目录不存在、一个文件都没比到）。"没比到"绝不能读作"没漂移"——那会让守卫在自己坏掉时报平安。`scripts/check-config-drift.selftest.sh` 在真检查之前先跑，四场景覆盖两向；已用两次突变验过（摘掉 diff 判定、把退出码 2 改成 0）都会让自测转红。

**同步操作（仓库 → 生产）**，必须在活目录里跑：

```bash
D=/media/heygo/program/datahub/nous/supabase
cp -p "$D/docker-compose.yml" "$D/docker-compose.yml.bak-$(date +%Y%m%d-%H%M%S)"
git -C <仓库> show origin/master:deploy/gpu-server/supabase/docker-compose.yml > "$D/docker-compose.yml"
cd "$D" && docker compose up -d db     # 挑业务空窗；restart 不重读 command
```

⚠️ **不要照 #1964 commit message 里那条 `cd deploy/gpu-server/supabase && docker compose up -d db`**：仓库那个目录没有 `.env`（只有 `.env.example`），项目名会变成 `supabase` 而非 `mediahub-sb-prod`，而 compose 里 PGDATA 是硬编码绝对路径 —— 等于对同一个数据目录再起一个 postmaster，靠 `postmaster.pid` 自保而不是靠命令正确。

### 容器的配置来源不止一处（查"改了为什么没生效"时先看这张表）

```bash
docker inspect <容器> --format '{{index .Config.Labels "com.docker.compose.project"}} | {{index .Config.Labels "com.docker.compose.project.config_files"}}'
```

2026-08-22 实测：

| 容器 | 项目 | 配置来自 | 漂移风险 |
|---|---|---|---|
| `nous-db` 及整个 supabase 栈 | `mediahub-sb-prod` | datahub 活目录（不在 git） | 有，靠上面的 drift 检查兜 |
| `nous-backend` / `worker` / `browser` / `gateway` | `gpu-server` | **runner 工作区 checkout** | 无——每次部署从 git 重出 |
| `nous-admin` | `gpu-server` | **runner 工作区 checkout**（2026-09-07 起） | 无——每次部署从 git 重出 |

`nous-admin` 曾经是从**开发工作树**构建的，那是个真缺口：那棵树可以挂在任意分支上，而它与另外四个容器共用项目名 `gpu-server` 却指向不同的 compose 文件，从开发树 `up` 有可能顺带影响生产容器。2026-09-07 已接进 `deploy-gpu.yml`（paths / 回滚锚点 / `up.sh --build` 清单 / smoke 探针 / 回滚清单五处同改），与另外四个容器同源。

### 已知缺口

- ~~**admin 没有自动部署**~~ —— **2026-09-07 已补齐**。当时记的阻塞项「`NOUS_ANON_KEY` 怎么进 CI」实测**不成立**：那是 **publishable** key（本来就烤进浏览器能下载的 JS 包，与 `frontend/.env.production` 明文提交 anon key 同一性质），所以直接在 compose 里写成默认值 `${NOUS_ANON_KEY:-sb_publishable_...}`，既不需要 CI secret，又从根上消掉"变量未设 → compose 只警告不失败 → build 出空 key 的 admin"这一整类静默故障。
- ~~**`deploy-frontend.yml` 存在但不生效**~~ —— **2026-09-07 查清并修复，但结论与原记载相反**。原文说"未配置即 no-op"，实际上 `PROD_FRONTEND_VERSION_URL` **是配了的**，只是指向 `https://mediahub.heygo.cn/version.json` —— 2026-07-25 迁移前的 NAS 时代域名，早已不存在。于是每次前端改动它都连拿 60 次 `<unreachable>`，然后把"探针自己够不着"解读成"部署没上线"，红 15 分钟。**部署一直是好的**（`app.nous.ink/version.json` 的 `commitSha` 与 push 的 SHA 一致，`buildTime` 在 push 后 54 秒）。已把变量改指 `https://app.nous.ink/version.json`。
  教训与「空输出不是否定结论」同族：探针够不着目标 ≠ 目标是坏的。**新加轮询型探针时，必须能区分"拿到值且不匹配"与"一次都没拿到值"，后者应该报"探针失效"而不是报"被测对象失败"。**
- **migration 与代码部署无顺序保证**。`run-migration.yml` 与 `deploy-gpu.yml` 独立触发，同一个 PR 里既加 migration 又改依赖它的代码时，两者谁先完成不确定。

## Discord 通知规则（可选，当前不可用）

⚠️ **2026-09-07：Discord MCP 未登录**（`discord_send` 返回 `Discord client not logged in.`），所以这一节描述的通知发不出去。原文写的是"**必须**发送"，实测每次尝试都失败 —— 一条永远做不到的强制要求只会让每个会话都白撞一次墙，还容易让人以为通知已经发了。

因此降级为**可选**：MCP 可用时按下面的模板发；不可用时**不要重试、不要绕道**，在回复里说明即可。恢复登录后可以把"可选"改回强制。

（同日已移除 `~/.claude/settings.json` 里那个每条消息都注入「强制规则」横幅的 `UserPromptSubmit` hook —— 那是用户级全局设置，不在本仓库。）

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
