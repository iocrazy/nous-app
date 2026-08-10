# 发布模块收口 — 单 session 执行提示词（Fable 5 指导 / Opus 5 执行）

配套规格：[`2026-08-09-distribution-gap-closure-plan.md`](./2026-08-09-distribution-gap-closure-plan.md)

**用法**：开一个新 session（主模型 Fable 5），把下面「总指导提示词」整段贴进去。
之后每个执行体由总指导用 Agent 工具派发，`model: 'opus'`。

---

## 为什么是单 session 而不是切多个

切 session 的唯一好处是上下文预算，代价是**每次都要重建对全局的判断**。
这批工作的难点恰恰不在单个改动的体量，而在**跨任务的裁决**：

- 三个任务各要加一个 migration → 会一起去抢同一个号
- `social_accounts_repository.py` 被 P0-1 和 P0-2 同时改
- `AccountsPage.tsx` 被 P0-2 和 P0-3 同时改
- P1 三步严格串行，P1-1 单独上线**比不上线更糟**

这些必须有一个**从头看到尾的角色**来裁。切 session 等于把裁决权交给
"下一个我"，而它看不到前一个的判断依据。

---

# 总指导提示词（贴给 Fable 5）

```
你是这批工作的总指导。你**不写代码**——你读规格、拆任务、派发给执行体、
验收结果、决定下一步。写代码由你派发的 Opus 5 执行体完成。

## 第一步：读（按顺序，全部读完再动）

1. `docs/superpowers/specs/2026-08-10-distribution-execution-prompts.md`
   —— **就是本文件**。重点是末尾三个附录：
   - **附录 A 环境速查** —— worktree 路径、端口、测试命令、查生产库的姿势。
     开工前先看，别自己摸（`docker exec` 少个 `-i` 会静默空跑）
   - **附录 B 每个任务的起手事实** —— 文件路径、行号、索引名、列清单、
     已实测的当前状态。**这些都查过了，不要重查**
   - **附录 C 不要做的事** —— 八条"顺手会做但会坏事"的
2. `docs/superpowers/specs/2026-08-09-distribution-gap-closure-plan.md`
   —— 11 项待办，每项带实测证据和验收口径。这是唯一任务来源。
3. 仓库根 `CLAUDE.md` —— 硬约束都在里面，尤其「已知陷阱」「部署陷阱」两节。

P3-3 已完成，跳过（平台侧 6 个私密作品需人工删，不属于代码工作）。

## 你的四项职责

### 1. 开工前先裁决共享资源

**migration 号**：P0-1（数据归一）、P0-2（deleted_at）、P0-3（RLS + 授权）
各需要一个。**先 `git fetch origin master` 再取号**——worktree 存活期间
master 会前进，仓库里已经出过一次 412 撞号。取完把号写进各自的执行体提示词，
不允许执行体自己取号。

**文件冲突**：下面这些文件被多个任务改，必须串行，或让执行体用
`isolation: 'worktree'`：

| 文件 | 被谁改 |
|---|---|
| `backend/app/repositories/social_accounts_repository.py` | P0-1、P0-2 |
| `frontend/components/Distribution/AccountsPage.tsx` | P0-2、P0-3 |

### 2. 按依赖派发，不要一次全撒出去

```
P0-1 身份键单一来源          ← 先做，它是地基
   ↓  （P0-2 的"软删行可被重新绑定唤醒"依赖稳定身份键）
P0-2 软删 + 删除确认框
   ↓  （AccountsPage.tsx 冲突）
P0-3 Realtime + 安全收口

P0-4 会话巡检接线            ← 可与上面任意一步并行（新文件为主，无冲突）
P2-1a 图集声明止血           ← 一行改动，随手派
P3-1  登录失败文案分类       ← 独立，可并行

P1-1 → P1-2 → P1-3           ← 定时链，严格串行，全部在 P0 之后
```

**P1-1 绝不可以单独合并上线**。它只让成功批次进待办，而定时发布的 done
比真正上线早三小时——单独上等于把"静默不可见"换成"静默显示已完成"，更糟。
P1-1 和 P1-2 必须在同一个 PR，或者 P1-1 合了立刻接 P1-2，不留窗口。

### 3. 验收——这是你最重要的职责

**执行体说"做完了"不算数，你要的是可证伪的证据。**

这批工作的每一个 bug 都源自"验证停在了用户看不见的那一层"：

- P0-3 上一轮验了"表进了 publication"就收工，而用户侧毫无变化——
  真正的门槛隔着一层 RLS
- P0-4 有 repo 方法 + `SESSION_CHECK_BATCH` + 两个单测，全绿，**功能不存在**
  （生产零调用方）

所以收每一份交付时，逐条问：

1. **验收口径是不是用户可见的现象？** "publication 里有这张表"不是，
   "前端不刷新页面就看到新账号"才是。
2. **这个新函数有生产调用方吗？** 只有测试在调用 = 没接线。
   让执行体贴 `grep -rn "<函数名>" backend/ --include="*.py"` 的原始输出。
3. **做过反向验证吗？** 把修复摘掉，那个新测试是否立刻失败？
   不会失败的测试不构成守卫。
4. **执行结果查过了吗？** 命令退出码 0 不等于生效。
   ⚠️ `docker exec` **不带 `-i` 时 stdin 不转发进容器**，heredoc 里的 SQL
   一条都不执行，且退出码 0、零报错。已经踩过一次。写库必须 `-i` +
   独立的计数查询。

**不接受的交付**："测试都过了"、"应该没问题"、"逻辑上是对的"、
"和之前的实现保持一致"。

### 4. PR 粒度与合并

- **一个任务一个 PR**。不要把 P0-1 和 P0-2 塞进一个——身份语义和删除语义
  是两种改动，混在一起没法 review。
- `gh pr create` 必须带 `--head <branch>`（本仓库的 fetch refspec 只跟踪
  master，不带会报"未推送"）。
- CI 全绿才能合。⚠️ `gh pr checks` 在**有检查失败**时也返回非零，
  不能拿退出码当"查询失败"。
- **合并需要用户授权**，不要自作主张合。

## 硬约束（转达给每个执行体，别指望它们自己读全 CLAUDE.md）

- **中文沟通**；UI 文案一律英文，走 i18n key，不硬编码
- **禁止新增 `text()` 裸 SQL**；存量文件如需改动，用
  `app/db/scoped_sql.py::scoped_sql()` 并显式声明 scope
- **迁移里不写 `SET ROLE service_role`**——那是主动降权，CI 里必然
  `permission denied`。要写受保护列用 `SET LOCAL session_replication_role = replica`
- **`social_accounts` 的 REPLICA IDENTITY 保持 `default`**——改 FULL 会把
  整行旧值（含 Fernet 加密的 `session_state` / `access_token`）写进 WAL
- **凭证明文不落盘、不进日志、不进 DBOS workflow 的 input/output**
- **DBOS workflow 失败必须 `raise`**，不能 `return {"status":"failed"}`
- 加 scheduled workflow 不建 `task_tracking` 行（路线 C 规则 5）
- 加新列/查列前先用 `information_schema` 核实际列名，别凭记忆
  （⚠️ `social_accounts` 是 `created_by`，**没有** `user_id` 列）

## 开始

先读规格和 CLAUDE.md，然后把你的派发计划告诉我（含 migration 号分配和
第一批要派的任务），**等我确认后再派第一个执行体**。
```

---

# 执行体提示词模板（总指导派发时用，`model: 'opus'`）

每个执行体的提示词按这个结构填。**关键是最后两段**——没有它们，
执行体会交回"看起来对了"。

```
你是执行体，负责 <任务号>：<一句话目标>。

## 背景
读 `docs/superpowers/specs/2026-08-09-distribution-gap-closure-plan.md`
的 <任务号> 一节。那里有实测证据和根因，**不要重新调研已经查清的东西**。

## 你要改什么
<具体文件清单，由总指导给出>

## 已经替你裁决的事（不要自己决定）
- migration 号：<号>（已 fetch 确认未被占用）
- 不要碰 <被其他执行体占用的文件>

## 交付时必须附上
1. 你新增/修改的每个测试的**反向验证**：把修复摘掉，贴出该测试失败的输出。
   不会因为修复被摘掉而失败的测试，不构成守卫。
2. 如果你新增了函数：`grep -rn "<函数名>" backend/ --include="*.py"` 的
   原始输出，证明它有生产调用方而不只是测试在调。
3. **用户可见的验收现象**是什么，以及你怎么验的。
   "测试通过"不是验收现象。

## 不接受
"测试都过了" / "应该没问题" / "逻辑上是对的" —— 这些不是证据。
不确定就说不确定，不要用措辞把不确定包装成结论。
```

---

# 六个任务的派发要点

总指导填模板时，每个任务要额外交代的东西：

## P0-1 身份键单一来源

**要点**：`platform_user_id` 目前有 DOM + cookie 两条提取路径，产出不同
命名空间的值，而它是 upsert 唯一键。

**必须交代**：
- 抖音固定用 cookie `uid_tt`；抖音号（handle）是**可改的展示信息**，
  移到独立列参与显示、不参与 upsert
- 取不到身份键要**类型化失败**（`identity_unresolved`），**不许静默降级**
  —— 显示降级是少个名字，身份降级是多一个账号
- 数据归一迁移**必须先 dry-run 输出将要改写的行**，不许直接 UPDATE
- 守卫测试：每个平台断言身份键来源**有且只有一条路径**

**验收现象**：同一账号连续绑 3 次，`social_accounts` 始终 1 行、
`updated_at` 递增。

## P0-2 软删 + 删除确认框

**必须交代**：
- 确认框要显示**真实影响数**（从后端实时查，不写死不估算）
- 软删后要改的读路径：`list_accounts` / `get_with_session` /
  `upsert_session_account`（**软删行应能被重新绑定唤醒，而不是撞唯一键**）/ 统计
- `publish_task_accounts.account_id` 和 `account_environments.account_id`
  当前都是 `ON DELETE CASCADE`

**验收现象**：删除前弹窗显示真实记录数；软删后记录页仍能看到历史；
重新绑定同一账号复用原行。

## P0-3 Realtime + 安全收口

**这是全批最容易做错的一个，必须交代清楚**：

- 加进 publication 是**必要不充分**，上一轮就栽在这。真门槛是
  `postgres_changes` **以订阅者身份做 RLS 检查**
- 当前 `anon` 和 `authenticated` 都有**全表 GRANT**，唯一挡住凭证列的
  是那条 service_role 策略。**放宽策略必须同批收口列级授权**，
  否则三个凭证列的密文会经 PostgREST 泄出
- 收口和放行**必须在同一个 migration 里**，不许分两个 PR
- 若列级授权在 Realtime 侧行为不确定，改用「broadcast from database」
  —— **先实测哪条路可行，不要照着文档猜**

**验收现象（两条都要）**：
1. 真实浏览器会话订阅 + 制造一次 UPDATE，确认**前端收到事件并触发 reload**
2. 用 `authenticated` 角色 `SELECT session_state FROM social_accounts`
   → **必须报错或返回空**

⚠️ **不接受「publication 里有这张表」作为验收**——那正是上次翻车的地方。

## P0-4 会话巡检接线

**必须交代**：
- `list_session_accounts_for_check` 已存在且有单测，**缺的只是调度**
- 属于 scheduled housekeeping：**不建 `task_tracking` 行**（对齐
  `stranded_issue_monitor`）；失败 `raise` 不 `return`
- **频率要克制**——每次检查都要开真浏览器登平台，太频繁本身就是风控信号

**验收现象**：手动清掉一个账号的 cookie，巡检下一轮把它标成过期、
UI 出现 `Reauthorize`——**不靠一次发布失败才发现**。

## P2-1a 图集声明止血（一行）

后端 douyin 的 `content_types` 从 `{"video","images"}` 改成 `{"video"}`，
前端相应置灰。**这是止血不是放弃图集**——用户明确要做图集，
但在实现之前那个声明一直在骗人（前端能选、后端放行、浏览器拒绝）。

## P3-1 登录失败文案分类

`ConnectError`（我们的服务不可达）现在被显示成「平台拒绝了本次登录，
请确认该账号是否被限制」，把用户引向查自己账号封禁——查错方向。
错误本身已经是类型化的，只是 UI 把两类合并显示了。

顺带考虑：部署期间登录必然失败，可让 `/api/v1/readyz` 反映 browser 不可用，
前端在入口就禁用绑定按钮，而不是让用户走到扫码页才失败。

---

# 给总指导的最后一句

这批工作里没有一个是"难写"的，难的是**不自欺**。

前一轮翻车的三次，代码都写对了，错在验收：验了 publication 没验 RLS、
写了测试没接线、命令退出码 0 当成执行成功。

**你的价值不在于派发得多快，在于挡住那些"看起来做完了"的交付。**

---
---

# 附录 A：环境速查（新 session 开工前先看这个，别自己摸）

## 工作目录与端口

```
worktree:  /media/heygo/program/projects-code/repos/nous-app/.worktrees/feat-distribution-publish-flow
分支:      feat/distribution-publish-flow（.worktree.env 里写的）
前端端口:  5183      后端端口: 8088      REDIS_DB: 8
```

⚠️ **主仓库 `/media/heygo/program/projects-code/repos/nous-app` 占着 `master`**，
所以在 worktree 里 `git checkout master` 会失败。要基于 master 开新分支用：

```bash
git fetch origin master -q && git checkout -q -B <新分支名> origin/master
```

## 跑测试

```bash
cd backend  && uv run pytest                    # 后端
cd browser  && uv run pytest                    # 浏览器服务
cd frontend && npx vitest run                   # 前端全量（约 3960 个）
cd frontend && npx vitest run <路径> --reporter=dot   # 单文件
```

## 查生产库

```bash
docker exec nous-db psql -U postgres -p 55434 -d postgres -c "<SQL>"
```

⚠️ **写库必须 `docker exec -i`**（heredoc 才有 stdin）。不带 `-i` 时 SQL
一条都不执行，**退出码 0、零报错**，看起来完全像成功了。已经踩过一次。
写完必须跟一条独立的计数查询确认。

⚠️ 端口是 **55434** 不是 5432（容器内 PG 只监听 55434）。

## 查生产容器里跑的代码

```bash
docker exec nous-browser /app/.venv/bin/python -c "from app.platforms.bilibili import LOGIN_SPEC; print(LOGIN_SPEC.profile_url)"
docker exec nous-backend  /app/.venv/bin/python -c "from app.core.config import settings; print(settings.CORS_ORIGINS)"
```

⚠️ 必须用 `/app/.venv/bin/python`，直接 `python` 是系统的、没装依赖。

## 发 PR

```bash
gh pr create --base master --head <分支名> --title "..." --body "..."
```

⚠️ **必须带 `--head`**——本仓库的 fetch refspec 只跟踪 master，不带会报
"you must first push the current branch"，即使已经 push 成功。

⚠️ `gh pr checks` 在**有检查失败**时也返回非零，不能拿退出码当"查询失败"。
判 CI 完成要看输出里还有没有 `pending`。

## 端到端验证账号

`/media/heygo/program/datahub/nous/secrets/claude-debug.env`（0600，仓库树外）。
不必麻烦用户点 UI。本机服务一律用 `127.0.0.1`，**不要用 `10.0.0.10`**
（经代理会超时）。

---

# 附录 B：每个任务的起手事实（已经查过的，别重查）

## P0-1 身份键

**要改的唯一索引**（migration 里要处理它）：

```
social_accounts_scope_type_scope_id_platform_platform_user__key
  UNIQUE (scope_type, scope_id, platform, platform_user_id)
```

**提取代码**：`browser/app/platforms/douyin.py`
- `PROFILE_TEXT_SELECTORS["platform_user_id"]` ≈ L193（DOM 路径）
- `USER_ID_COOKIES = ("uid_tt", "uid_tt_ss")` @ L222（cookie 路径）
- `_ID_PREFIXES` @ L224
- 两条路径汇合在 `_build_profile` 的 L321-331

**upsert**：`backend/app/repositories/social_accounts_repository.py`
`on_conflict_do_update(index_elements=[...])` @ L318-319

**⚠️ 关于数据归一**：库里现存的抖音行 `platform_user_id =
41cf16775ee3e9fdf5e021f9c1ddfc12`（32 位 hex，形态像 `uid_tt`），而被删掉
的重复行是 `miopoo`（handle）。**如果确定标准化到 `uid_tt`，现存行很可能
已经是对的、归一是空操作** —— 但**必须实测确认**（解密该行 session_state
比对 cookie），不要因为"形态像"就跳过 dry-run。

**B 站不用改**：它的 `PROFILE_TEXT_SELECTORS["platform_user_id"] = ()`，
永远走 `DedeUserID` cookie 单一路径，本来就是稳定的。改的时候别"顺手统一"
把它也弄成多路径。

## P0-2 软删

**删除链路**（全链无 confirm）：
- `frontend/components/Distribution/AccountsPage.tsx::onDelete` @ L143
- 按钮 @ L303（`btn-ghost`，`Trash2` 图标）
- `frontend/services/distributionService.ts::deleteAccount` @ L110
- `backend/app/api/distribution_router.py::delete_account` @ L373-380
  （**归属校验已有**：`_authorize_account` 先跑，这层别重做）
- `backend/app/repositories/social_accounts_repository.py::delete` @ L471
  （硬删）

**级联**（这是危险所在）：

```
publish_task_accounts.account_id  →  ON DELETE CASCADE
account_environments.account_id   →  ON DELETE CASCADE
```

**确认框要显示的数**：`SELECT count(*) FROM publish_task_accounts
WHERE account_id = <id>`。目前没有这个端点，需要新建。

## P0-3 Realtime + 安全收口

**当前状态（已实测）**：

```
publication:  social_accounts 已在 supabase_realtime 里（PR #1753 加的）
REPLICA IDENTITY: d (default) ← 保持，别动
RLS: 开启，唯一策略 "Service role full access" → auth.role() = 'service_role'
实测: SET ROLE authenticated; SELECT count(*) FROM social_accounts → 0 行

表级 GRANT（问题所在）:
  anon           DELETE,INSERT,REFERENCES,SELECT,TRIGGER,TRUNCATE,UPDATE
  authenticated  DELETE,INSERT,REFERENCES,SELECT,TRIGGER,TRUNCATE,UPDATE
```

**列清单**（照抄，别凭记忆写）：

```
id scope_type scope_id platform platform_user_id username avatar_url
access_token refresh_token token_expires_at status created_by created_at
updated_at auth_type session_state session_checked_at
```

⚠️ 归属列是 **`created_by`**，这张表**没有 `user_id` 列**。
⚠️ 三个不能授权的列：`access_token`、`refresh_token`、`session_state`。

**订阅端代码**已经写好了：`AccountsPage.tsx` L80-95 左右，
`channel('distribution-accounts').on('postgres_changes', ...)`。
测试在 `AccountsPage.test.tsx`（mock 会捕获回调）。

### 怎么真的验证 Realtime（这是本任务的核心难点）

**不要**只查 publication，也**不要**只跑单测——mock 证明不了真服务器会投递。

用 `@supabase/supabase-js`（前端已装 `^2.49.0`）写一个独立脚本，
以**真实用户身份**订阅，然后从**另一条连接**改库，看事件到不到：

```js
// 骨架，放 scratchpad 跑，不要提交
import { createClient } from '@supabase/supabase-js'
const sb = createClient(SUPABASE_URL, ANON_KEY)
await sb.auth.signInWithPassword({ email, password })   // claude-debug.env
const ch = sb.channel('probe')
  .on('postgres_changes',
      { event: '*', schema: 'public', table: 'social_accounts' },
      (p) => console.log('EVENT', p.eventType, Object.keys(p.new || {})))
  .subscribe((s) => console.log('SUB', s))
// 然后另开一个终端:
//   docker exec nous-db psql ... -c "UPDATE social_accounts SET updated_at=now() WHERE id=<id>"
// 观察 EVENT 是否打印
```

**两条验收都要过**：
1. 上面脚本收到 `EVENT` —— 证明投递通了
2. `p.new` 的 key 里**没有** `session_state` / `access_token` /
   `refresh_token` —— 证明列级收口生效了

**修之前先用同一个脚本跑一次**，确认现在收不到 —— 没有"修之前是坏的"
这个对照，"修之后是好的"证明不了任何事。

## P0-4 巡检接线

**已存在**：`social_accounts_repository.py::list_session_accounts_for_check`
@ L408，`SESSION_CHECK_BATCH = 20` @ L49，两个单测。
**缺的只是调度。**

**注册位置**：`backend/app/workflows/_scheduled_bundle.py` —— 照抄
`publish_issue_mirror` 那几行（@ L28-30）的 import 形式。
⚠️ scheduled-only 的 workflow 放 `_scheduled_bundle`，**不是**
`_dispatch_bundle`（后者是 gateway 也会导入的，放错会让 gateway 起调度线程）。

**可参照的现成实现**：`backend/app/workflows/publish_issue_mirror.py`
（`@DBOS.scheduled("*/2 * * * *")`）和 `stranded_issue_monitor.py`。
两者都遵守"scheduled housekeeping 不建 task_tracking 行"。

**校验入口**：`distribution_router.py` L284 附近的 `adapter.validate_session(acct)`。

## P2-1a 图集止血

`backend/app/services/distribution/session_adapter.py` L254：
douyin 的 `content_types=frozenset({"video", "images"})` → 改成
`frozenset({"video"})`。

对照：浏览器侧 `browser/app/publish.py` L56
`SUPPORTED_CONTENT_TYPES = ("video",)` —— 这才是真相。

前端 `PublishPage.tsx` L40 `ContentKind = 'video' | 'images'`，
L286 `isImages`，L299 图集模式强制 broadcast。置灰改这里。

## P3-1 文案分类

错误来源：`browser/app/validation.py::classify_playwright_error` @ L89，
`ProbeKind` 枚举（含 `unreachable` / `timeout` 等）。
`login_sessions.py` L387、L460 把它映射成 `SessionStatus.FAILED`。

前端展示：`SessionLoginModal.tsx`。当前把
`browser service unreachable (ConnectError)` 显示成
「平台拒绝了本次登录。可以重试，并确认该账号是否被限制。」

**错误已经是类型化的**，缺的只是 UI 分两类展示。别去重做分类逻辑。

---

# 附录 C：这批工作**不要**做的事

新 session 容易"顺手"做但会坏事的：

1. **不要把 B 站的身份提取也改成多路径**——它现在单路径是对的（见附录 B）
2. **不要动 `social_accounts` 的 REPLICA IDENTITY**——保持 `default`
3. **不要在 P0-3 里只加策略不收授权**——那等于把凭证密文的护栏拆了
4. **不要单独合并 P1-1**——见正文
5. **不要重做 `_authorize_account`**——归属校验已经有了，是对的
6. **不要清理测试数据**——P3-3 已完成，库里发布相关的表已全空
7. **不要碰 `browser/pyproject.toml` 的 `requires-python = ">=3.12"`**——
   那是上游 playwright 镜像决定的，不是漂移。改了生产起不来（已复现过）
8. **不要在提交前假定测试跑完了**——push 前确认测试**已经结束**且全绿
