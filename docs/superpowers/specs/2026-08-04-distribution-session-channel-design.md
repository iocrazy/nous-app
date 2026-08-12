# 发布模块 · 会话通道（Session Channel）设计

- 日期：2026-08-04
- 分支：`feat/distribution-publish-flow`
- 状态：设计定稿，待实施
- 相关：[`2026-08-01-needs-input-first-class-design.md`](2026-08-01-needs-input-first-class-design.md)、migration 351 / 356

---

## 1. 背景与决策

### 1.1 为什么需要第三条通道

发布模块 D1/D2 已实现两条**官方**通道：

| 通道 | 机制 | 现状 |
|------|------|------|
| `official` | 抖音开放平台 OAuth + 服务端投稿 API | 前端硬锁（`PublishPage.tsx:905`），需「抖音业务能力」资质 |
| `h5` | `open.get.ticket` 签名 → Schema URL → 用户手机确认 | 依赖的 `h5.share` / `open.get.ticket` 能力**审核失败** |

两条官方通道都有一个共同的天花板：**用户必须在场**。H5 分享发到的是用户手机抖音当前登录的账号，服务端选不了号。这与本项目的实际使用形态（几十到上百个矩阵账号、定时发布、无人值守）根本不兼容。

同时快手、小红书等平台**不存在**第三方发布 API，官方路线在这些平台上不可能存在。

**结论**：新增 `session` 通道 —— 基于浏览器自动化 + 平台 web 会话（cookie）的发布路径。与官方通道**并存**，不替换。

### 1.2 通道分工

| 通道 | 适用场景 |
|------|----------|
| `official` / `h5` | 主账号、需要人工过目的精品内容、平台能力批下来后的首选 |
| `session` | 矩阵账号、定时发布、批量、无人值守；官方无 API 的平台 |

### 1.3 参考实现

`social-auto-upload`（本机 `github-repos/social-auto-upload`）是这条路线的开源参考，八平台覆盖。其价值在于**踩坑记录**而非代码本身——直接依赖它不可行（SQLite / Flask 同步 / 明文 cookie 落盘 / `while True` 无上界），但它的 DOM 考古笔记与失败模式必须继承（见 §7）。

它的三种执行档位值得记住，抽象设计要给档位 2 留位置：

| 档位 | 做法 | 稳定性 | 代表 |
|------|------|--------|------|
| 1 | 纯 DOM 木偶戏 | 低（改版即断） | 抖音、视频号、快手 |
| 2 | 浏览器只当签名机，上传走裸 HTTP | **高** | 小红书（`window._webmsxyw` 签 `x-s/x-t`） |
| 3 | 外包成熟专用 CLI | 高 | B 站（`biliup`） |

### 1.4 风险声明

cookie 会话 + 自动化发布违反抖音等平台的用户协议，矩阵规模是平台风控的重点对象。环境隔离（§5）降低的是被判定概率，不是归零。这是该路线的固有成本，已知悉并接受。

---

## 2. 架构

```
nous-backend (FastAPI + DBOS)
  │
  ├── OAuthAdapter    (现有)  ── official / h5
  └── SessionAdapter  (新)    ── session
         │  HTTP (内网)
         ▼
  nous-browser (新容器, patchright/Playwright)
         ├── /session/validate   会话有效性校验
         ├── /session/login/*    扫码登录（保活 session）
         └── /publish            DOM 自动化发布
              └─ 每账号一个 context:
                 storage_state + proxy + geolocation + UA + timezone
```

### 2.1 为什么浏览器必须是独立容器

1. **镜像体积**：Playwright + Chromium ≈ 1.5GB。塞进 `nous-backend` 会让 gpupc 每次后端部署都重装浏览器。
2. **资源隔离**：浏览器是内存黑洞。上百账号意味着要独立限流与扩缩容。
3. **崩溃隔离**：浏览器 OOM 不能带死 API 进程。
4. **既有接口**：`social-auto-upload` 的 `douyin_cookie_gen` 已经支持 `cdp_url` 参数（`uploader/douyin_uploader/main.py:229`），说明远程浏览器是这类方案的标准形态；S6 换成 AdsPower 时只需改连接方式。

### 2.2 部署

- `deploy/gpu-server/docker-compose.yml` 新增 `nous-browser` service
- 基础镜像：`mcr.microsoft.com/playwright/python`（含浏览器依赖）
- **必须装 Xvfb 并以有头模式运行**（见 §2.4）
- 仅暴露 docker 内网，**不映射宿主机端口**
- `deploy-gpu.yml` 的 paths 需加入对应目录

### 2.4 有头 + Xvfb（已决策，不再实测）

「浏览器在服务器上跑」与「浏览器无头」是**两个独立维度**，不可混为一谈：

| 维度 | 选项 | 本方案 |
|------|------|--------|
| 浏览器在哪跑 | 本机 / 服务器 | 服务器（`nous-browser` 容器） |
| 有没有 GUI | 有头 / 无头 | **有头**（Xvfb 虚拟帧缓冲） |
| 用户看不看得见画面 | 投屏(noVNC) / 只推关键图 | 只推二维码图片 |

**为什么不用 headless**：headless 模式存在大量可检测特征（`navigator.webdriver`、缺失的 Chrome runtime 对象、WebGL renderer 字符串异常、空字体/插件列表、部分 CSS 动画行为差异）。`headless=new` 改善明显但未消除。抖音级别的风控会查这些。

sau 的一线记录印证了这一点（`uploader/douyin_uploader/main.py:67`）：

> 抖音无头会撞反爬墙→content/upload 跳登录→误判 cookie 失效（间歇性）。校验必须有头。

**旁证**：蚁小二（云授权，纯 Web 产品，用户本地不弹任何窗口）同样是服务器端浏览器，且作为商业矩阵工具不可能使用裸 headless。它也**没有做画面投屏**，只把二维码图片抓出来推给前端 —— 与 §4.1 的设计一致。

**代价**：镜像增加数十 MB，每个 context 内存开销略高于 headless。可接受。

noVNC 投屏是可选的调试增强（排查 DOM 问题时能看到实际画面），S6 之后再评估。

### 2.3 浏览器运行时选型（已决策）

**S1–S5 自建**：用 Playwright 原生参数做环境隔离（`proxy` / `geolocation` / `user_agent` / `locale` / `timezone_id`）。

**S6 再接 AdsPower / 比特浏览器**：`account_environments.fingerprint_profile_id` 字段预留，届时把 `chromium.launch()` 换成 `chromium.connect_over_cdp(ads_ws_url)`，业务代码不动。

理由：先跑通主链路，不被第三方 license / 无头部署可行性阻塞。

---

## 3. 数据模型

### 3.1 `social_accounts` 扩展（migration N）

```sql
ALTER TABLE public.social_accounts
  ADD COLUMN IF NOT EXISTS auth_type VARCHAR(10) NOT NULL DEFAULT 'oauth'
      CHECK (auth_type IN ('oauth', 'session')),
  ADD COLUMN IF NOT EXISTS session_state TEXT,          -- Fernet 密文的 storage_state JSON
  ADD COLUMN IF NOT EXISTS session_checked_at TIMESTAMPTZ;

-- status 增加 needs_relogin
ALTER TABLE public.social_accounts DROP CONSTRAINT IF EXISTS social_accounts_status_check;
ALTER TABLE public.social_accounts ADD CONSTRAINT social_accounts_status_check
  CHECK (status IN ('active', 'expired', 'needs_relogin'));
```

`session_state` 用与 `access_token` 完全相同的 Fernet 加密（`app.core.secret_box`）。**明文 storage_state 只允许存在于 nous-browser 的进程内存中**，绝不落盘、绝不进日志。

### 3.2 新表 `account_environments`（migration N+1）

对应蚁小二的「环境配置」tab。一个账号一份环境，**长期钉死不变**。

```sql
CREATE TABLE IF NOT EXISTS public.account_environments (
    id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    account_id BIGINT NOT NULL UNIQUE
        REFERENCES public.social_accounts (id) ON DELETE CASCADE,
    proxy_url TEXT,                    -- Fernet 密文（含账密），NULL = 直连
    user_agent TEXT,
    locale VARCHAR(20) DEFAULT 'zh-CN',
    timezone_id VARCHAR(50) DEFAULT 'Asia/Shanghai',
    geo_lat DOUBLE PRECISION,
    geo_lng DOUBLE PRECISION,
    fingerprint_profile_id TEXT,       -- S6: AdsPower profile id
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

RLS 与 351 相同：service_role only。

**自洽性要求**：proxy 出口 IP 的归属地、`timezone_id`、`geo_lat/lng` 三者必须互相匹配。北京 IP 配美国时区是明确的风控特征。

### 3.3 `publish_task_accounts.channel` 扩展（migration N+2）

```sql
ALTER TABLE public.publish_task_accounts DROP CONSTRAINT IF EXISTS publish_task_accounts_channel_check;
ALTER TABLE public.publish_task_accounts ADD CONSTRAINT publish_task_accounts_channel_check
  CHECK (channel IN ('official', 'h5', 'session'));
```

`status` 枚举不变 —— `session` 通道走 `pending → publishing → success/failed`，不产生 `pending_share`。

---

## 4. 核心流程

### 4.1 扫码绑定账号

**关键约束（与 issue 的 needs_input 模式不同）**：二维码属于某个**活着的**浏览器 context。若照搬 `issue_lifecycle` 的「workflow 终止 → 状态标记 → 回复驱动重启」模式，workflow 一结束 context 就销毁，二维码立即作废。

因此扫码登录采用**browser 侧保活 session + workflow 轮询**：

```
POST /distribution/accounts/session/login   (backend)
  └─ DBOS workflow: session_login_workflow(account_id | new_account_draft)
       step1  POST nous-browser /session/login/start
              → browser 起 context（带环境配置），goto creator.douyin.com，
                抓二维码 data:image，返回 {login_session_id, qrcode_data_url}
                context 在 browser 容器内**保活**（TTL 5min，超时自毁）
       step2  manager.update_progress(subtitle=..., metadata_patch={"login": {...}})
              → 二维码进 task_tracking.metadata.login，前端 Realtime 渲染
       step3  循环轮询 GET /session/login/{id}/status（有上界！见 §7.2）
              ├─ waiting_scan / scanned → patch metadata.login.status
              ├─ qrcode_expired     → browser 侧自动点刷新，返回新二维码 → 回 step2
              ├─ identity_challenge → browser 侧自动点「接收短信验证码」（下一屏若有
              │                       「获取验证码」再点它），点不到/点了不动在 5 轮内
              │                       翻成 failed + detail.reason，绝不静默干等
              ├─ sms_required       → patch metadata.login，等前端 POST 验证码
              └─ success            → 继续
       step4  GET /session/login/{id}/state → storage_state JSON
              → Fernet 加密 → social_accounts.session_state
              → auth_type='session', status='active', session_checked_at=now()
       step5  POST /session/login/{id}/close 释放 context
       失败 raise（绝不 return failed dict）
```

#### ⚠️ 更正（2026-08-05，S2 实施前核对发现）

本节初稿写的「manager 标 needs_input」**在 `task_tracking` 上不存在**。`needs_input` 是 issues 侧的机制（`issues.status='needs_followup'` + `execution_state.agent_outcome`），任务表的 `phase` 只有 `queued / in_progress / completed / failed / cancelled / lost`，且由 trigger 全权同步、**业务代码禁止 PATCH**（路线 C 纪律 2）。

正确做法（也更简单）：**workflow 全程保持 `in_progress`，扫码状态只写 `metadata` jsonb**（业务装饰字段，路线 C 纪律 3 明确允许业务代码写）。用：

- `manager.patch_metadata(task_id, patch)` —— 纯写 metadata
- `manager.update_progress(task_id, ..., subtitle=..., metadata_patch=...)` —— 同时更新 subtitle；**该方法不改 phase**，注释里写明了

注意 `update_progress` 对同一 task 有 **1 write/sec 节流**。轮询间隔 ≥2s 时无影响，但不要把轮询压到亚秒级，否则状态更新会被丢弃。

#### `metadata.login` 结构（前后端契约）

```json
{
  "login": {
    "platform": "douyin",
    "status": "waiting_scan",
    "qrcode_data_url": "data:image/png;base64,...",
    "expires_at": "2026-08-05T01:30:00Z",
    "message": "Scan with the Douyin app"
  }
}
```

前端订阅 `task_tracking` 的 Realtime，读 `metadata.login` 渲染二维码与状态；不新建 SSE 通道。

**浏览器容器侧的 login session 必须有 TTL 自毁**，否则用户放弃扫码会永久泄漏一个 context。

### 4.2 发布

`publish_distribution.py::decide_channel()` 增加一档：

```python
def decide_channel(task_channel: str, account: dict) -> str:
    if task_channel == 'session' and account.get('auth_type') == 'session':
        return 'session'
    if task_channel == 'official' and account.get('access_token'):
        return 'official'
    return 'h5'
```

`SessionAdapter.publish_video()` → `POST nous-browser /publish`：

```
1. 解密 storage_state + account_environments
2. 起 context（storage_state + proxy + geo + UA + timezone + locale）
3. 会话校验（唯一函数，见 §7.1）—— 失败直接返回 needs_relogin，不尝试发布
4. 取素材：backend 签发 S3 presigned URL → browser 容器自行下载到 /tmp
   （浏览器容器**不挂载任何存储卷**，保持无状态）
5. DOM 自动化上传（继承 §7.4 的坑清单）
6. 成功后 context.storage_state() → 回传 → backend 重新加密入库【关键】
7. 清理 /tmp 素材 + 关闭 context
```

**第 6 步是会话寿命的决定因素**：平台会话是滑动续期的，每次使用后服务端下发新 cookie。不回写等于每次都在消耗初始那份的剩余寿命——这一行决定账号是「两周扫一次码」还是「三个月扫一次码」。

### 4.3 前端接入（现有 UI 的增量改造）

现有 `AccountsPage.tsx` 的页面骨架（PageHeader / stats 三卡 / `acct-grid` 卡片网格 / 平台面板）**全部复用**，session 通道是增量改造，不重写。唯一的新组件是扫码 modal。

| # | 位置 | 改动 |
|---|------|------|
| 1 | `AccountsPage.tsx:41-51` `onConnect` | 现在硬编码走 OAuth（取 `auth_url` → `window.location.href`）。改为先弹「绑定方式」modal：官方授权 / 扫码登录。绑定入口有三处（header 按钮 `:108`、ghost 卡 `:190`、平台面板 `:201`），统一收敛到同一个 modal |
| 2 | `:159-170` chips 区 | 新增 `auth_type` chip（`官方授权` / `扫码会话`）。两者能力不同（session 支持定时/无人值守/服务端选号，oauth 不支持）、失效语义不同、续期方式不同，**必须一眼可辨** |
| 3 | `:177-180` 失效动作 | 按 `auth_type` 分流：`oauth + expired` → Reauthorize（跳 OAuth）；`session + needs_relogin` → 打开扫码 modal 重新扫码 |
| 4 | `:75-78` `expiredCount` | **必须扩展为 `['expired','needs_relogin'].includes(a.status)`**。不改则「需要处理」stat 对 session 账号恒为 0 —— 账号早已掉线而 UI 显示一切正常，属 CLAUDE.md「silent no-op 不可接受」的典型违例 |
| 5 | 新组件 `SessionLoginModal` | 二维码图片 + 状态回显（等待扫码 / 已扫码待确认 / 二维码失效已刷新 / 需短信验证码）+ 验证码输入。**数据源走 `task_tracking` 的 Supabase Realtime**（二维码在 `metadata` jsonb），不新建 SSE 通道 —— 与 sau 的 Flask SSE 方案不同，复用既有基建 |
| 6 | `:201-204` Douyin 平台卡 | 现在标「Ready」实指 OAuth 可用，而 OAuth 因能力审核失败实为半残。改为表达两种绑定方式各自的可用性 |

**其他文件**：

- `types.ts` — `SocialAccount` 增加 `auth_type`、`session_checked_at`；`status` 联合类型增加 `'needs_relogin'`
- `services/distributionService.ts` — 新增 `startSessionLogin()` / `submitSmsCode()` / `cancelSessionLogin()`

**S4 增量**：账号卡片增加「环境配置」入口 → 侧栏编辑 proxy / geo / UA / timezone（对标蚁小二的「环境配置」tab）。

### 4.4 会话健康巡检

新增 `@DBOS.scheduled` workflow，周期性（建议 6h）扫描 `auth_type='session' AND status='active'` 的账号：

- 调 `/session/validate`
- 有效 → 更新 `session_checked_at`
- 失效 → `status='needs_relogin'` + 发 inbox 通知

**目的是把「发布时才发现会话死了」提前到「巡检时发现」**，给用户留出重新扫码的时间窗口。

巡检本身要限流（每轮最多 N 个账号），避免上百账号同时起浏览器。

---

## 5. 代理架构

### 5.1 双池隔离（强制）

```
Parse 池   ── 轮换住宅 IP，短租即弃
   └─ 抓取行为容易把 IP 打脏；脏 IP 绝不能是任何账号的「家」

Publish 池 ── 独享/长效住宅 IP，每账号钉死一个（sticky session）
   └─ 换 IP 是明确的风控红旗
```

**两池绝对不能混用。** parse 侧走代理是独立收益（避免 gpupc 家庭 IP 因抓取被限流），可以先于 publish 侧落地。

### 5.2 供应商

- 国内平台：青果网络 / 快代理 / 芝麻代理（长效独享住宅用于 publish，短效轮换用于 parse）。国内代理商需企业实名，采购走公司主体。
- 海外平台（将来 TikTok / YouTube）：Bright Data / Oxylabs / Decodo。

### 5.3 与本机 mihomo 的关系

无关。`account_environments.proxy_url` 是**业务出口代理**，per-context 配置，走 Playwright 的 `proxy` 参数；mihomo 是开发机的系统代理层。两者不互相影响。

---

## 6. 分阶段实施

| PR | 内容 | 验收 |
|----|------|------|
| **S1** | 3 个 migration + `SessionAdapter` 骨架 + `nous-browser` 容器（`/healthz` + `/session/validate`）+ compose/CI 接线 | 容器起得来；对一份手工导入的 storage_state 能正确判定有效/失效 |
| **S2** | `session_login_workflow` + browser 侧保活 login session + needs_input 二维码回显 + Accounts 页改造（§4.3 全部 6 项） | 从 UI 点击 → 扫码 → 账号出现在列表，`session_state` 已加密入库；卡片能区分 auth_type；手工置 `needs_relogin` 后「需要处理」stat 正确 +1（§4.3 #4） |
| **S3** | 发布链路（单账号、无代理、抖音视频）+ storage_state 回写 | 从 PublishPage 选 session 账号 → 视频真实发布成功，回写生效 |
| **S4** | `account_environments` CRUD + 环境配置 UI（对标蚁小二那个 tab）+ proxy/geo/UA 生效 | 配置的代理确实改变出口 IP（容器内 curl ip 探针验证） |
| **S5** | 会话巡检 scheduled workflow + `needs_relogin` inbox 通知 | 手工作废一个 cookie，6h 内收到通知 |
| **S6** | 规模化：指纹方案接入（AdsPower CDP）、并发限流、账号级串行锁、批量调度 | 10+ 账号并发发布不互相踢线 |

**S3 之前不接代理**，避免把「发布失败」和「代理配置错误」两类问题搅在一起。

图文（`DouYinNote`）、定时发布均为独立增量，不在本 spec 范围。

### 6.1 平台推进顺序（已决策）

**S1–S5 只做抖音。** 第一个平台约 80% 的工作量是与平台无关的基建（容器 / Xvfb / 会话加密存储 / 扫码 workflow / needs_input 投递 / presigned 素材传递 / 账号级锁 / 巡检）。基建打通后，新增一个平台只剩「写 uploader + 写校验函数」，成本约 1–2 天。

同期并行两个平台的坏处：跑不通时无法区分是基建问题还是平台适配问题。

**但抽象现在就要防止抖音特性焊死进基建**，三条硬要求：

1. 会话校验的返回契约必须通用（§7.8 的 status 枚举），不得出现平台专属状态
2. 素材传递、账号锁、巡检、加密存储等平台无关逻辑，不得出现在 `douyin_*` 命名的模块中
3. **必须给「档位 2」留位置**（§1.3）。小红书不走 DOM —— 它是「浏览器只当签名机（`window._webmsxyw` 计算 `x-s`/`x-t`）+ 上传走裸 HTTP」。若抽象假设「发布 == DOM 操作序列」，接入小红书就要返工基建。

**第二个平台建议选小红书**，正因为它与抖音最不同：档位 2 能跑通，说明抽象成立；若第二个平台选快手（同为档位 1 的 DOM 自动化），等于没有验证过抽象。

---

## 7. 必须遵守的纪律

这些全部来自 `social-auto-upload` 的实际失败，或本项目 CLAUDE.md 的既有纪律。

### 7.1 每平台的会话校验必须是**唯一一个函数**

反面教材：sau 的 `myUtils/auth.py:15` 与 `uploader/douyin_uploader/main.py:66` 是同一功能的两份实现。CLI 那份修过「无头误判 cookie 失效」，web 那份至今是老的 5 秒裸超时版——**同一个 bug 在同一个仓库里只修了一半**，web 端间歇性把好账号判死。

该函数本身必须包含三件套（sau 用血换来的）：

```
# 无头会撞反爬墙 → 被弹回登录页 → 误判 cookie 失效（间歇性）
1. 有头浏览器（或 headless=new + 充分伪装，需实测）
2. 重试 3 次
3. 宽松判定：URL 命中目标路径 且 页面无登录文案 → 判有效
   （不要用 wait_for_url(精确URL, 5s)，页面慢/瞬时跳转会骗你）
```

这与 CLAUDE.md 已有的「`is_enabled()` 不能当健康依据，要用 `is_launched()`」同族：**探针必须探真信号**。

### 7.2 所有轮询必须有上界 + heartbeat

sau 的 `while True`（`main.py:693` / `718` / `762`）没有任何上界，页面卡住即永久挂起。本项目所有轮询循环：

- 必须有最大轮次
- 必须周期性写 `heartbeat_at`
- 超时 `raise`，不 `return`

### 7.3 DBOS 失败必须 raise

沿用 CLAUDE.md 路线 C 纪律。返回 `{"status": "failed"}` 会被 DBOS 判为 SUCCESS，trigger 把 `task_tracking` 误标 completed，业务字段却没机会更新。

`publish_distribution` 现有的 `classify_batch()`（all_failed / partial → raise）已经是正确范式，session 通道复用。

### 7.4.0 定位优先级：中文文案 > class（2026-08-06 实测立约）

写任何新的 selector 之前先读这条。

抖音控制台的 class 是 CSS Modules 产物 —— `name-_lSSDc`、`unique_id-EuH8eA`、`img-PeynF_`。**hash 后缀每次发布都变**，只有角色前缀稳定。按 `[class*="nickname"]` 这类猜测写的 selector，三个全部失效过一次，后果是绑定成功的账号显示成一串 open_id（2026-08-06）。

同一天在真实发布页上逐个验证了中文文案定位，下列每一个都**恰好 1 个匹配**，无歧义：

```
自主声明 / 请选择自主声明 / 添加合集 / 关联热点
定时发布 / 立即发布 / 谁可以看 / 保存权限
允许 / 不允许 / 公开 / 好友可见 / 仅自己可见 / 设置封面 / 发布
```

参考项目里**唯一没失效**的那段代码（`set_self_declaration`），恰恰是唯一用 `get_by_text` 的。

所以：

1. **首选** `get_by_text(..., exact=True)`；`exact` 不可省 —— 「允许」是「不允许」的子串
2. class 只作为后备候选，且按**前缀**匹配（`[class^="name-"]`），永远不要匹配 hash
3. 需要 class 时优先用**角色容器**收窄（`[class^="header-"] [class^="name-"]`）：`name-` / `img-` 都够通用，页面别处也有 —— `img-pos9sN` 是作品缩略图，把最新视频封面当成头像比没有头像更糟
4. 文案也会变（改版、A/B、国际化），所以仍然保留兜底链；只是排序反过来

**怎么拿到真实 DOM**：不要猜。用一个已绑定账号的活会话去看，发布页需要先传一个 1 秒的测试视频（`ffmpeg -f lavfi -i color=c=black:s=720x1280:d=1`）才会渲染表单。看完关掉即可，不点发布就不会产生作品。

### 7.4 DOM 考古笔记（抖音，2026-06 前后实测）

来自 sau，接入时逐条验证是否仍然成立：

| 坑 | 出处 |
|----|------|
| 封面弹窗有 4 个隐藏 file input，`[0][1]` 是「AI 生成参考图」，`[2][3]` 才是真封面。用 `.first` → 「传了却没封面」 | `main.py:641-645` |
| shepherd 新手引导浮层拦截点击，操作前需 `evaluate` 移除 | `main.py:632`、`765` |
| Semi 的 `.semi-radio-addon` 常带 `pointer-events:none`，直接点卡 30s 超时，要点外层 `.semi-radio` | `main.py:434-441` |
| BGM「使用」按钮 `visibility:hidden`，普通 click 无效，需 `el.evaluate("el => el.click()")` | `main.py:487-489` |
| 发布页 version_1 / version_2 两套 URL 灰度并存，**必须两个都轮询** | `main.py:693-711` |
| 上传失败自愈：检测 `progress-div` 内「上传失败」→ 重新 `set_input_files` | `main.py:601-603` |
| 发布页表单要等视频传完才渲染（实测约 40s），等待超时须给到 120s | `main.py:322-326` |

### 7.5 账号级串行锁

同一账号同时开两个浏览器会话会互相踢下线。发布/校验/登录任何操作前必须获取该账号的排他锁（DB 层，`pg_advisory_lock` 或状态列 CAS），拿不到就排队。

### 7.6 凭证不落盘

`session_state` 明文只允许存在于 nous-browser 进程内存。禁止写临时文件、禁止进日志、禁止进 DBOS workflow input/output（DBOS input freeze 后不可改，且会被持久化）。

### 7.7 发布前 fail fast

参数校验必须在**起浏览器之前**完成（参考 `uploader/base_video.py`）：文件存在性、格式白名单、标题长度、图片数量上限、定时提前量。起一次浏览器 + 传一个几百 MB 的视频要几十秒到几分钟，参数错误不能等到那时才发现。

**此项与通道无关，OAuth 通道同样需要** —— 建议提前抽到 `platform_base.py` 作为抽象方法，独立于本 spec 落地。

### 7.8 类型化失败回显

沿用 CLAUDE.md「触发路径必须类型化失败回显」。所有 session 操作返回结构化结果，参考 sau 的 `_build_login_result`（`main.py:41-49`）：

```python
{"success": bool, "status": str, "message": str, "detail": dict}
```

#### status 枚举 —— 本节是权威定义

`SessionStatus` 在 backend 与 browser 两个服务里**各有一份实现**（跨服务无法共享 Python 包），因此 **本表是唯一权威来源，两侧实现必须与它逐值一致**。

| 值 | 首次使用阶段 | 含义 |
|----|-------------|------|
| `session_valid` | S1 | 会话有效 |
| `session_invalid` | S1 | 会话失效，需重新扫码 |
| `proxy_failed` | S1 | 代理不通（**不是**账号问题，不得据此标 needs_relogin） |
| `timeout` | S1 | 操作超时 |
| `failed` | S1 | 其它失败，看 message / detail |
| `waiting_scan` | S2 | 二维码已展示，等待用户扫描 |
| `scanned` | S2 | 已扫描，等待用户在手机上确认 |
| `qrcode_expired` | S2 | 二维码失效（browser 侧已自动刷新，同响应带回新码） |
| `identity_challenge` | S2 | 平台插了一屏身份验证，正在等我们选验证方式（browser 侧已自动点「接收短信验证码」，同响应带回点完之后的状态）。**此刻一条短信都没发** —— 它跟 `sms_required` 分家的唯一理由就是后者授权的 UI 文案是「把收到的码填进来」（2026-08-11：合并两者让用户对着一个从未被请求的验证码干等到 TTL 用完） |
| `sms_required` | S2 | 需要短信验证码。`detail.code_requested=true` 时才代表**我们的点击真的向平台请求过发码**，前端据此才可以说「已代你请求发送」；没有它只能说中性文案 |
| `success` | S2 | 登录完成，可取 storage_state |
| `published` | S3 | 发布成功 |

每个操作只使用其中一个子集（如 `/session/validate` 只用前 5 个），但**枚举定义两侧必须全集对齐** —— 否则某一侧提前返回了对方不认识的值，会被降级成 `failed`，把可操作的信息丢掉。

> 已知待办：S1 落地时 browser 侧尚无 `published`（发布在 S3 才实现）。**S3 第一件事就是补齐它**，否则发布成功会被 backend 判成未知状态。

**不得加入平台专属值**（§6.1 硬要求 a）。抖音的 `private_status` 之类平台原生枚举，翻译成通道语义是各平台 uploader 的职责。

#### `detail` 里必须区分「基建失败」与「业务失败」

这是两类完全不同的东西，混用会造成误伤：

- `detail["reason"]` = **业务原因**（`no_session_state` / `malformed_session_state` / …）→ 账号确实需要用户处理，可以标 `needs_relogin`
- `detail["error_kind"]` = **基建失败**（`decrypt_failed` / `not_configured` / 容器不可达 / 超时）→ **账号状态一律不动**

典型误伤场景：Fernet 密钥错配（轮换没做完）会让所有 `session_state` 解密失败。若不区分，一次巡检就能把**全部账号**误标为掉线，而 cookie 其实好得很，用户被迫全部重扫。

调用方（尤其是 S5 巡检）判断基建失败用 `browser_client.is_infra_failure()`，不要自己拼字符串。

调用方按 `status` 分支，UI 按 `status` 给出可操作的提示。silent no-op 不可接受。

---

## 8. 开放问题

1. ~~**`headless` 与反爬的实际关系**~~ → **已决策，见 §2.4**：有头 + Xvfb，不实测 headless。
2. **patchright vs playwright**：sau 用 patchright（反检测 fork，`pyproject.toml` 里是 `patchright==1.58.2`）。需评估其维护活跃度与是否值得作为生产依赖。注意 sau 自身的 `requirements.txt` 仍写着 `playwright==1.52.0`，两种装法得到不同环境 —— 我们必须单一来源。
3. **`nous-browser` 的鉴权**：内网调用是否需要 token。倾向需要 —— 该服务持有解密后的会话，不能裸奔。
4. **S6 并发模型**：上百账号的调度策略（队列 + 并发上限 + 每账号最小发布间隔）需单独设计。
5. ~~**素材传递**~~ → **已验证（2026-08-05，S3 实施前实测）**：可达，且无需新设计。

   `PublishTasksRepository.get_resource_media_url()` 已经是 official / h5 两个通道的**共用单一入口**，它按 `resources.file_path` 的形态派生 URL：文件系统路径 → 短 TTL HMAC 签名的 `/media/` URL；对象存储路径（`sb://bucket/key`）→ Supabase Storage 签名 URL。

   实测在 `nous-browser` 容器内直接下载成功：

   ```
   host = nous-kong:8000   （docker 内网，不出公网）
   HTTP 200  content-type=video/mp4  首块 65536 字节
   ```

   两个容器同在 `nous-net`，所以 S3 的素材传递**直接复用该方法**，不引入新的签发逻辑 —— 也就自然继承了它的 TTL 与鉴权口径。
6. **Xvfb 下的并发密度**：有头模式每 context 内存开销高于 headless，需实测 gpupc 上单容器能承载多少并发 context，作为 S6 限流参数的依据。
