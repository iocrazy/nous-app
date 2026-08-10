# 发布模块缺口收口计划（2026-08-09）

> 本文是**待办清单 + 实施规格**，不是设计文档。基础设计见
> [`2026-08-04-distribution-session-channel-design.md`](./2026-08-04-distribution-session-channel-design.md)。
>
> 每一条都带**实测证据**。没有证据的条目不写进来——"可能有问题"不是待办。
> 证据里的时间戳是 UTC，取自生产库（gpupc `nous-db`）与生产容器。

---

## 0. 现状基线：哪些是真的能用的

写缺口之前先钉住基线，否则下一个会话会重复验证已经验过的东西。

| 能力 | 状态 | 证据 |
|---|---|---|
| 抖音扫码绑定 | ✅ 可用 | `social_accounts` 有 active 行，MioPoo |
| 抖音视频发布（session 通道） | ✅ 端到端跑通 4 次 | probe 4/5/6/9，作品数 71→72 等 |
| 封面 / 自主声明 / 定时 | ✅ 已验 | probe 9：08:00 显示「继续编辑」= 已排期 |
| B 站扫码绑定 | ✅ 可用 | account `336407517237861` |
| 反检测（patchright + stealth） | ✅ 已验 | 容器内 `executable_path=/ms-playwright/chromium-1208/` |
| WebRTC 出口 IP 泄露 | ✅ 已堵 | 曾实测泄露 `38.175.103.177`，已修 |
| 发布→issue 单向镜像 | ⚠️ 部分 | 只有失败的进得去，见 P0-4 |

**没验过的一律不算基线**，见第 3 节。

---

## P0 — 数据正确性与安全（必须先做）

### P0-1 同一账号重复绑定，产生两行

**现象**：同一个抖音账号 MioPoo 绑了两次，产生两行独立账号，历史被劈成两半。

**证据**：
```
08-06  MioPoo  platform_user_id = 41cf16775ee3e9fdf5e021f9c1ddfc12   ← cookie uid_tt
08-09  MioPoo  platform_user_id = miopoo                              ← DOM 抖音号
```

**根因**：`upsert_session_account` 的唯一键是
`(scope_type, scope_id, platform, platform_user_id)`，而
`browser/app/platforms/douyin.py::_build_profile` 的 `platform_user_id` 有**两条
提取路径**：先试 DOM 选择器 `[class^="unique_id-"]`，取不到才退回 cookie
`uid_tt`。两条路径产出**不同命名空间**的值，谁赢取决于当次页面渲染。

那个函数的 docstring 写着：

> Everything is best-effort by design: a console redesign that breaks a
> display-name selector must degrade to a nameless account, never fail a login
> the user already completed.

这对**显示字段**（username / avatar_url）是对的，对**身份键**是错的。
显示降级 = 少个名字；**身份降级 = 多一个账号**，而且旧账号的发布历史就此断掉。

B 站反而没这个毛病：它的 `platform_user_id` 选择器是空元组，永远走
`DedeUserID` cookie 单一路径——**稳定但难看，好过好看但不稳定**。

**修复方向**：
1. `platform_user_id` 改为**单一权威来源**，每平台明确指定，不允许多路径回退。
   抖音应固定用 cookie `uid_tt`（跨登录稳定、与展示无关）；抖音号（handle）
   是**可改的展示信息**，移到独立列 `platform_handle`，参与显示不参与 upsert。
2. 提取失败时**不得静默降级**：拿不到身份键就让登录以类型化错误失败
   （`identity_unresolved`），而不是造一个新账号。这与「触发路径必须类型化失败
   回显」同族。
3. 加守卫测试：对每个平台断言 `platform_user_id` 的来源**有且只有一条路径**。

**数据修复**：本次重复行已被用户手动移除（库里现存 2 行）。上线新键之前要写
一次性迁移：把存量 `platform_user_id` 归一到新来源，且**必须先 dry-run 输出
将要合并/改写的行**，人工确认后再执行。

**验收**：同一账号连续绑定 3 次，`social_accounts` 始终 1 行且 `updated_at` 递增。

---

### P0-2 「移除」是硬删 + 级联抹掉发布记录，且无任何确认

**现象**：账号卡片上的 `Remove` 按钮点下去直接进库，没有确认对话框。

**证据**：
- `AccountsPage.tsx::onDelete` → `deleteAccount(id)` → `DELETE /accounts/{id}`
  → `sa_delete(SocialAccounts)`，全链**没有 confirm**
- 外键级联：

```
publish_task_accounts.account_id  →  ON DELETE CASCADE
account_environments.account_id   →  ON DELETE CASCADE
```

- 影响量化（删除前实测）：

```
 id               | username  | 会被连带删掉的发布记录
 335617669826935  | MioPoo    | 10
 336553799171178  | MioPoo    | 0
```

用户这次点掉的恰好是 0 条记录那行——**是运气，不是设计**。点左边那张卡，
10 条发布记录会无提示消失，且硬删无回收站。

**归属校验是有的**（`_authorize_account` 先跑），这层没问题，不要重复排查。

**修复方向**（两步，先止血后治本）：

1. **确认对话框，且必须显示影响范围**。不是"确定删除吗"，而是
   "将同时删除 N 条发布记录，不可恢复"。**N 从后端实时查**，不写死、不估算。
2. **改软删**：`social_accounts.deleted_at`，发布历史与账号解绑。
   发布记录是资产，不该被解绑连坐。改完后确认框可降级为
   "解绑后需要重新扫码"。

要改的读路径：`list_accounts` / `get_with_session` / `upsert_session_account`
（软删行应能被重新绑定唤醒，而不是撞唯一键）/ 统计口径。

⚠️ 与仓库里「内容寻址存储：删除必须查引用」同族——这次的引用不是文件是历史。

**验收**：删除前弹窗显示真实记录数；软删后 `记录` 页仍能看到历史；
重新绑定同一账号复用原行而非新建。

---

### P0-3 Realtime 仍然不刷新——加进 publication 是必要不充分

**现象**：绑定成功后账号卡片不出现，仍需手动刷新网页。**用户已反馈三次**。

**上一轮（PR #1753）的修复不完整**：把 `social_accounts` 加进
`supabase_realtime` publication 是对的，但只做了这一半，我验证了 publication
就宣布修好，**没有验证订阅端真能收到事件**。

**真因**：Supabase Realtime 的 `postgres_changes` **以订阅者的身份做 RLS 检查**。

```
authenticated 在 social_accounts 上能看到:  0 行
唯一策略:  "Service role full access"  →  auth.role() = 'service_role'

对照 task_tracking:  auth.uid() = user_id   ← 所以它的 realtime 一直好用
```

没有面向 `authenticated` 的 SELECT 策略 → Realtime 服务器看不到行 →
**事件根本不投递**。

**⚠️ 不能简单加个策略了事**——这是安全收口，必须同批做完：

```
当前表级 GRANT:
  anon           DELETE,INSERT,REFERENCES,SELECT,TRIGGER,TRUNCATE,UPDATE
  authenticated  DELETE,INSERT,REFERENCES,SELECT,TRIGGER,TRUNCATE,UPDATE
```

一旦加 RLS 策略放行，`session_state` / `access_token` / `refresh_token`
三列的**密文会经 PostgREST 被前端直接拉走**。现在挡住它们的**只有那条
service_role 策略**——策略一放宽，护栏就没了。

**修复方向（必须同一个迁移里做完，不许分批）**：
1. `REVOKE ALL ON public.social_accounts FROM anon`（匿名不该碰这张表）
2. `REVOKE ALL` 后按列 `GRANT SELECT (id, scope_type, scope_id, platform,
   platform_user_id, username, avatar_url, status, auth_type,
   session_checked_at, created_at, updated_at) TO authenticated`
   —— 三个凭证列**不在授权列表里**
3. 加 SELECT 策略 `auth.uid() = created_by`（⚠️ 核对列名：该表是
   `created_by` 不是 `user_id`）
4. **REPLICA IDENTITY 保持 `default`**。改 FULL 会把整行旧值（含凭证密文）
   写进 WAL。已实测当前是 `d`，别动。

**替代方案（若列级授权在 Realtime 侧行为不确定）**：改用
「broadcast from database」——trigger 发广播消息，不依赖表的 RLS，
前端收到信号后**照旧走后端 API 拉列表**。这个方案的好处是凭证列
完全不进复制流，缺点是多一层 trigger。**做之前先实测哪条路可行**。

**验收（可证伪，必须做到）**：
- 用真实浏览器会话订阅，制造一次 UPDATE，确认**前端确实收到事件并触发 reload**
- 用 `authenticated` 角色 `SELECT session_state FROM social_accounts` →
  **必须报错或返回空**
- ⚠️ 不接受「publication 里有这张表」作为验收——那正是上次翻车的地方

---

### P0-4 会话健康巡检从未接线（写了 repo 和测试，没有生产调用方）

**现象**：账号会话失效是**静默的**。UI 一直显示 `已激活`，直到某次发布失败
才发现。

**证据**：
```
grep -rn "list_session_accounts_for_check" backend/ --include="*.py"
  → repositories/social_accounts_repository.py:408   （定义）
  → tests/repositories/…:226, 243, 264, 269          （测试）
  → 生产调用方：无
```

```
两个账号的 session_checked_at 都停在最后一次业务操作:
  douyin    2026-08-08 05:01  （最后一次发布）
  bilibili  2026-08-08 14:05  （绑定当时）
```

spec §4.4 的健康巡检写了 repo 方法、`SESSION_CHECK_BATCH=20`、还有两个单测，
**唯独没接调度**。这是「有测试 ≠ 接线了」的典型：测试全绿，功能不存在。

**修复方向**：加 `@DBOS.scheduled` 巡检 workflow，按
`list_session_accounts_for_check` 取批，逐个 `validate_session`，
把结果写 `status` / `session_checked_at`。

⚠️ 遵守路线 C：巡检属于 scheduled housekeeping，**不建 task_tracking 行**
（对齐 `stranded_issue_monitor`）。失败必须 `raise` 不能 `return`。

⚠️ 频率要克制：每次检查都要开真浏览器登一次平台，太频繁本身就是风控信号。
建议按 `session_checked_at` 最久未检的优先、每 tick 至多 N 个、单账号最小
间隔数小时。

**验收**：手动让一个账号会话失效（清 cookie），巡检下一轮把它标成过期，
UI 出现 `Reauthorize`——**不靠一次发布失败才发现**。

---

## P1 — 定时发布链（用户已拍板做「2+3」）

用户原话选择：**「2+3」**——加"已排期"状态 + 到点后回读校验。

### P1-1 成功批次进不了待办（2 分钟扫描窗口漏掉）

**证据**：
```
publish_task 336273441061932:
  scheduled_at = 08:00   published_at = 05:01   phase = completed
  task_tracking.issue_id = NULL          ← 从没建过 issue
  耗时 61 秒

库里 6 个镜像 issue 全是 blocked（清一色失败的）
```

**根因**：`publish_issue_mirror` 是 `@DBOS.scheduled("*/2 * * * *")` 的扫描器，
而 `issue_status_for_phase("completed")` 返回 `None`（注释：nothing to manage）。
批次只跑了 61 秒，**首次扫描时已经 completed**，于是一个 issue 都没建过。

设计意图是"建了再自动转 done"，但只要快过 2 分钟窗口就整个跳过。
定时发布尤其快——它不等上传转码完成。

**修复方向**：`completed` 也补建 issue 直接落 done。但**单独做这一条会让情况
更糟**（把"静默不可见"换成"静默显示已完成"），必须和 P1-2 同批上。

---

### P1-2 定时发布的 done 是假的：加「已排期」状态

**根因**：session 通道的定时**不是我们在等**——浏览器任务只是把时间填进抖音
UI 然后点确认，真正发布是抖音三小时后自己做的。所以：

- `published_at = 05:01` 记的是"我们的活干完了"，不是"内容上线了"
- 待办会在真正上线前**三小时**显示 done

**修复方向**：镜像 issue 在 `scheduled_at` 到达前停在 `in_progress`（或新增
`scheduled` 状态），到点之后才允许转 done。待办上显示"这条 08:00 上线"。

⚠️ 路线 C 约束：镜像是**单向**的（读 task_tracking，写 issues），
不得反向驱动执行。"已排期"状态的判定应基于 `publish_tasks.scheduled_at`
这个业务列，不去改 DBOS 那套状态机。

---

### P1-3 到点回读校验 + 回填 published_url

**现象**：`published_url` **8 条记录无一有值**。

到点后跑一次浏览器任务去创作中心确认作品真的上线，回填 `published_url` /
`platform_item_id`，**然后才**把 issue 转 done。这才是可证伪的信号——
与仓库里「探针必须探真信号」一致。按时间推的 done 只是把假设当结论。

代价：每条定时发布多一次浏览器任务。可接受。

⚠️ 平台可能审核不过 / 限流 / 定时被取消。回读**必须能表达"到点了但没上线"**
这个状态，让 issue 走 blocked 而不是 done。这正是定时发布最需要管理的场景。

**验收**：排一条 10 分钟后的定时，到点后待办自动从"已排期"变 done 且
`published_url` 有值；再排一条然后在平台手动删稿，确认待办变 blocked。

---

## P2 — 未实现的功能

### P2-1 图集发布（用户明确要做：抖音和小红书都要）

**当前状态是三层声明打架**：

| 层 | 声明 |
|---|---|
| 前端 `PublishPage.tsx` | `ContentKind = 'video' \| 'images'`，**能选** |
| 后端 `session_adapter.py` | douyin `content_types = {"video","images"}`，**放行** |
| 浏览器 `publish.py` | `SUPPORTED_CONTENT_TYPES = ("video",)`，**拒绝** |

结果：用户走完全程，最后一步拿 `unsupported_content_type`。

失败是**类型化的**（不会在真实账号上瞎点，这层守卫是好的），但拒绝得太晚，
而且后端那个 `"images"` 声明**当前是错的**——它宣称了一个不存在的能力。

**修复顺序**：
1. **立刻**把后端 douyin 的 `content_types` 改成 `{"video"}`，让拒绝发生在
   最前面（一次字典查找），前端相应置灰。这是**止血**，不是放弃图集。
2. 再实现图集：`douyin_publish.py` 目前是 `job.assets[VIDEO_ROLE]` 单文件上传，
   图集要多文件 + 排序 + 封面语义不同。小红书还要先实现 publisher（见 P2-2）。

⚠️ 别跳过第 1 步直接做第 2 步——图集实现周期长，期间这个假声明一直在骗人。

---

### P2-2 小红书 / B 站发布未实现

当前 `supports_publishing=False` + 浏览器侧不注册 publisher，**两层拦截**。
这是**刻意的、正确的**，不是缺陷——但要记着能力边界：这两个平台目前只能
"绑定账号 + 会话保活"。

`session_adapter.py:273` 那段注释已经标明它们的 `content_types` / 扩展名是
**占位值**，真正实现发布时要对着平台实测填准。别把占位当事实。

---

### P2-3 official / h5 通道从未验证

代码在 `douyin_adapter.py`，但：
- 两个账号 `access_token` 均为空，从没走通过
- **Client Secret 曾在截图里明文泄露，用户尚未在平台重置**
- `douyin_adapter.py:123` 有 `TODO(distribution): switch to Douyin chunked upload`

⚠️ **新 Secret 不要贴进对话**，由用户从 admin UI 录入。

在拿到新凭证之前这条链无从验证。**当前唯一验证过的通道是 session。**

---

### P2-4 每账号环境隔离（`account_environments` 建了表但 0 行）

```
列: id account_id proxy_url user_agent locale timezone_id
    geo_lat geo_lng fingerprint_profile_id created_at updated_at
行数: 0
```

矩阵账号防关联依赖每账号独立的代理 + 指纹 + 时区。表建好了，**从没写过数据**，
也没有任何读取方把它接进 `build_context_kwargs`。

现状 = 所有账号共用同一出口 IP 和同一套指纹。单账号无所谓，**一旦上多账号
就是最强的关联信号**——比任何 CDP 特征都强。

P1（A4 多账号并发）真正开跑之前必须先做这个，否则并发本身就在制造关联证据。

---

### P2-5 从 issue 反向发起发布

目前只有 发布 → issue 的单向镜像。从一个 issue 直接发起发布还没有。

⚠️ 做的时候注意别把单向镜像变成双向驱动——路线 C 的整个要点就是
**只有一个数据源在驱动执行**。反向应该是"issue 上的一个动作触发新的发布
workflow"，而不是"改 issue 状态去影响已有批次"。

---

## P3 — 文案、卫生、遗留

### P3-1 登录失败文案误导

**现象**：`browser service unreachable (ConnectError)` 时，UI 显示
「平台拒绝了本次登录。可以重试，并确认该账号是否被限制。」

`ConnectError` 是**我们自己的服务连不上**，跟平台、跟用户账号都没关系。
这句文案会让用户去查自己账号是不是被封——查错方向。

**本次实测的触发场景**（值得记录，因为会反复发生）：
```
nous-browser 启动于  23:55:23   （B6 部署 #1752/#1754 重建容器）
用户登录失败于        23:54:53   （早 30 秒，正落在部署窗口里）
退出码 0，OOMKilled=false，RestartCount=0  →  是 compose 重建，不是崩溃
6 分钟后抖音登录成功
```

**修复方向**：把"基础设施不可达"与"平台拒绝"分成两类文案。
前者应提示"服务暂时不可用，请稍后重试"，而不是引导用户怀疑账号。
这与「触发路径必须类型化失败回显」同族——错误已经是类型化的
（`ConnectError` vs 平台判定），只是 UI 把两类合并显示了。

**顺带**：部署期间的登录必然失败。可考虑部署时让 `/api/v1/readyz` 反映
browser 不可用，前端在入口就禁用绑定按钮，而不是让用户走到扫码页才失败。

---

### P3-2 migration 412 撞号

```
supabase/migrations/412_retire_canvas_stage_node.sql      （#1752）
supabase/migrations/412_social_accounts_realtime.sql      （#1753）
```

**这次没出事**：`run-migration.yml` 按"本次 push 新增的文件"检测
（`git diff --diff-filter=A HEAD~1 HEAD`），两次 push 各跑各的；
重建时按文件名排序也是确定的，且两者无依赖关系。Schema Drift 已验证通过。

但号重复本身是隐患，应把其中一个改名为 413。
⚠️ 改名要连带改注释里的编号引用，且**先 fetch 确认 413 没被占**
（worktree 存活期间 master 会前进）。

---

### P3-3 测试遗留数据 — ✅ 已完成（2026-08-10 01:04 UTC）

**数据库侧已清空**，删前逐条核实过，删后计数全部对上：

| 对象 | 删前 | 删后 |
|---|---|---|
| `publish_tasks` | 10（**全部**是 probe，无真实任务混入） | 0 |
| `publish_task_accounts` | 10（级联） | 0 |
| `issues` publish 镜像 | 6 | 0 |
| `issue_messages` | 1（级联，`kind=system_status`、`meta={"source":"trigger"}`，无用户内容） | 0 |
| `task_tracking` publish | 10 | 0 |
| `social_accounts` | 2 | **2**（未触碰） |
| `issues` 总数 | 41 | **35**（正好少 6，无误伤） |

测试视频 `nous-publish-probe.mp4`（8235 字节 / 2 秒，`file_hash` 无共指）
走**软删**（`is_trashed=true`），存储对象保留、可恢复。

删除前的完整行备份为 JSON（五张表，26KB，不含任何凭证）。

**⏳ 仍未完成——平台侧**：抖音创作者中心上还有 **6 个私密测试作品**。
它们在平台服务器上，数据库清空影响不到。需要人工在创作者中心删除，
或等实现删稿浏览器任务时顺带清。

---

⚠️ **踩到的坑，写下来避免重复**：`docker exec` **不带 `-i` 时 stdin 不会
转发进容器**，heredoc 里的 SQL 一条都不会执行，而且**退出码是 0、没有任何
报错**——看起来完全像成功了。第一次执行就是这样空跑的，靠事后计数才发现
（10/10/6/10 原封不动）。

教训与「探针必须可证伪」同族：**执行完必须查计数，不能拿"命令没报错"当
执行成功**。写库的脚本一律 `docker exec -i`，且跟一条独立的验证查询。

---

## 卡在用户侧的验证项（做不做代码都不影响）

这几项**不是代码缺口**，是缺验证条件。列在这里避免下个会话重复排查。

| 项 | 卡在什么 |
|---|---|
| 合集（A3） | 抖音账号里没有合集，平台提示"你没有合集，点击创建合集" |
| 多账号并发（A4） | 只有 MioPoo 一个抖音账号；且应先做 P2-4 |
| 公开发布（A5） | 用户选择用小号验，小号未绑 |
| 小红书短信绑定 | **库里从没有过小红书账号**，这条链一次都没跑通 |
| official 通道 | Client Secret 待用户在平台重置 |

---

## 建议执行顺序

```
P3-3 清理遗留          ← ✅ 已完成 2026-08-10，DB 侧已清空
                          （平台侧 6 个私密作品仍需人工删）
  ↓
P0-1 身份键单一来源     ← 数据正确性，越晚修重复行越多
P0-2 软删 + 确认框      ← 与 P0-1 都动 social_accounts，可同批
  ↓
P0-3 Realtime + 安全收口 ← 独立，可并行
P0-4 会话巡检接线        ← 独立，可并行
  ↓
P2-1 步骤 1（图集声明止血）← 一行改动，随手做掉
P3-1 登录文案分类
  ↓
P1-1 → P1-2 → P1-3      ← 定时链，必须按序，P1-1 不可单独上
  ↓
P2-4 环境隔离           ← A4 多账号并发的前置
  ↓
P2-1 步骤 2（图集实现）、P2-2、P2-5
```

**P0 组之间可以并行**（分属不同文件），**P1 组内部严格串行**。

---

## 三条贯穿始终的纪律（本轮踩过，写下来别再踩）

1. **验证要验到用户看得见的那一层**。P0-3 上一轮验了"表进了 publication"
   就宣布修好，而用户侧毫无变化——真正的门槛在 RLS，隔着一层。
   验收口径必须是"用户能看到的现象变了"，不是"我改的那个对象状态对了"。

2. **有测试 ≠ 接线了**。P0-4 有 repo 方法、有 `SESSION_CHECK_BATCH`、
   有两个单测，全绿，功能不存在。加守卫时要问：**这个函数有生产调用方吗**。

3. **降级要分清对象**。P0-1 的 best-effort 对显示字段是对的，对身份键是灾难。
   "永不失败"作为原则要问：失败的替代品是什么？如果替代品是"造一个新账号"，
   那失败才是对的。
