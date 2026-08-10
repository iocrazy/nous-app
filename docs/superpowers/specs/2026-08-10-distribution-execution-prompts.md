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

## 第一步：读

1. `docs/superpowers/specs/2026-08-09-distribution-gap-closure-plan.md`
   —— 11 项待办，每项带实测证据和验收口径。这是唯一任务来源。
2. 仓库根 `CLAUDE.md` —— 硬约束都在里面，尤其「已知陷阱」「部署陷阱」两节。

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
