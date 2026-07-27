# Nous.ink 基础设施迁移 — 分阶段 Epic Spec

日期：2026-07-20 ｜ 状态：已过用户评审拍板（结构层）｜ 类型：Infra Epic（跨子系统，分阶段独立 ship）

## 1. 背景与动机

三件纠缠的基础设施诉求，收敛成一个分阶段 epic：

1. **域名迁移**：前端用户入口 `mediahub.heygo.cn` → `app.nous.ink`（域名已持有，在 Cloudflare）。
2. **前端托管迁移**：Vercel → Cloudflare Pages。
3. **服务迁移**：backend + 自托管 Supabase 从 Synology NAS → GPU 服务器（RTX Pro 6000，AI 一系列已在该机）。

**为什么现在做 / 收益（针对代码维护 & PR 流程）**：
- 干掉 NAS 部署链的整类维护税：Watchtower 不读 compose env 的漂移事故（#172 mediahub-worker 从未创建 + admin 级联打不开）、`:88` 端口 + 自签证书（iOS Shortcuts TLS 报错、CORS 非标端口特例）、NAS reboot 手动恢复 runbook、docker 必 sudo 绝对路径。
- 后端从"只能合了看 prod"→ 有资源余量跑 **每 PR 后端 preview** + **self-hosted runner**（已有 `project_self_hosted_runner_plan`）。
- backend 与 AI 同机 → backend→AI 走 localhost，**干掉 zerotier 那一跳**（现 `10.0.0.10:8000`）。
- **商用条款**（见 §3）：Vercel Hobby 明文禁止商用，nous.ink 对外/计费后必须离开 Vercel Hobby；Cloudflare Pages 免费层允许商用。

## 2. 已拍板决策（勿翻）

| # | 决策 | 拍板 |
|---|------|------|
| D1 | 单域名方案：整个 app 挂 `app.nous.ink`；`nous.ink` apex → 301 → `app.nous.ink`。**不做** apex 独立落地站 / 跨子域登录态交接 | 2026-07-20 |
| D2 | 后端/Supabase 迁到 GPU 服务器；AI 一系列已在本机，不迁，backend 就近共处走 localhost | 2026-07-20 |
| D3 | 服务器在 NAT/家宽后、无公网 IP → **必走 Cloudflare Tunnel** 反向连出，暴露 `api.nous.ink` / `sb.nous.ink` 干净 443 | 2026-07-20 |
| D4 | 目前未上线、无外部用户 → **不做零停机工程**；数据迁移用 `pg_dump/restore` + 存储卷拷贝，可容忍维护窗口 | 2026-07-20 |
| D5 | 前端终态走 **Cloudflare Pages**（非 Vercel）；商用条款使其为必然终态而非可选优化 | 2026-07-20 |
| D6 | 每个 Phase 独立可 ship + 带回滚点；不允许一个分支/一次操作里全动（对齐 CLAUDE.md「refactor 与 feature 分开、避免大分叉」纪律在 infra 上的同构） | 2026-07-20 |
| D7 | 前端调后端从「Vercel 反代 rewrite（相对 `/api`）」→ **直连 `api.nous.ink` + CORS**；反代 rewrite 在 P2 退役（Cloudflare Pages 的 `_redirects` 无法反代外部源站，此坑因后端有独立域名而消解） | 2026-07-20 |

## 3. Cost & Constraints（托管额度，2026 官方核准）

| 维度 | Vercel Hobby（免费） | Cloudflare Pages（免费） |
|---|---|---|
| 带宽 | ~100GB/月 · **硬顶超了整站暂停 30 天** | **无限带宽 + 无限静态请求** |
| 边缘请求 | 100 万/月（硬顶→暂停） | 静态资源请求不计配额 |
| 构建/部署 | 100 部署/天（≈3000/月） | **500 构建/月（≈16/天）** |
| 构建超时 | — | 20 分钟 |
| 文件数/单文件 | — | 20,000 文件 · ≤25 MiB/文件 |
| 自定义域名 | 50/项目 | 100/项目 |
| **商用** | 🔴 **仅个人非商用** | ✅ **允许商用** |

**约束 C1（决定性）**：Vercel Hobby 禁商用 → 有 billing/points/Nous Models 计费模块，对外即违 TOS。→ Cloudflare Pages 为必然终态。
**约束 C2（P3 必须处理）**：本仓库发版极频繁，Cloudflare 500 构建/月 比 Vercel 100/天 更易撞顶。**缓解**：GitHub Actions 内构建 + `wrangler pages deploy` **直传预构建产物**（direct upload 不消耗 Pages 构建配额）。
**约束 C3**：媒体文件（视频/图片）从后端 `nousserver` 出、不走前端源站；前端托管只发 SPA bundle，带宽压力小。

来源：[Vercel Hobby docs](https://vercel.com/docs/plans/hobby) · [Cloudflare Pages limits](https://developers.cloudflare.com/pages/platform/limits/)

## 4. 北极星终态

```
                    Cloudflare DNS (nous.ink)
                            │
   nous.ink ──301──▶ app.nous.ink ──▶ Cloudflare Pages (SPA, 静态)
                            │
                     直连 + CORS
                            ▼
   ┌─────────────── GPU 服务器 (NAT 后, 无公网) ───────────────┐
   │  cloudflared (Tunnel) ──▶ api.nous.ink  → backend (443)   │
   │                       ──▶ sb.nous.ink   → Supabase/Kong    │
   │  backend ──localhost──▶ AI 一系列 (已在本机)               │
   │  自托管 Supabase (Postgres + Kong + Realtime + Storage)    │
   │  部署: compose-up CI (无 Watchtower 漂移)                   │
   └────────────────────────────────────────────────────────────┘
```

### 4.1 源码 / 部署拓扑（勿搞混）

**代码唯一真相 = GitHub**；Mac Mini 和 GPU 服务器都只是它的克隆，靠 GitHub 同步，**谁都不共享工作目录**。

| 东西 | 放哪 | 说明 |
|---|---|---|
| 代码唯一真相 | **GitHub** | push/pull 都走它 |
| 写代码 (dev workspace) | **Mac Mini（现状不变）** | worktree 端口隔离（`.worktree.env`）留这儿；`/ship` 照旧 |
| 服务器上的源码 | runner **自己的一份 checkout** | 部署时 `git pull` 合并后 commit，跟 dev 工作目录互不共享 |
| 数据/密钥 | **服务器本地持久化** | Postgres 数据、Storage 卷、`.env`(SUPABASE keys/CORS) 在 named volume/持久 `.env`，**不进 git**（同现 NAS 模型） |

**原则**：源码不是"搬"到服务器，是服务器经 GitHub 自己 pull。🔴 **禁止** 把 Mac Mini 工作目录 NFS/挂载给服务器或 scp 源码过去（状态漂移温床，与 compose 漂移血泪同源）——**git 当传输层**。
**流程零改变**：Mac Mini `/ship` → PR 绿 → 合 master → GPU 机 runner 自动 pull+build+up。落地机从 NAS 换成 GPU 机，开发姿势不变。

## 5. 现状事实（代码核验，作为迁移基线）

- 前端单个 React Router SPA；`/login` = `LoginPage`（public），登录后 `/team/:teamId/...`；登录用 `signInWithPassword`（邮箱+密码），**无 OAuth / magic-link / `redirectTo` 硬编码**。
- 前端调后端：`VITE_API_URL=`（空）→ 相对 `/api/*`、`/media/*` → **Vercel rewrites 反代**到 `mediahubserver.heygo.cn:88`（`frontend/vercel.json`）。
- Supabase：`VITE_SUPABASE_URL=https://sb-mediahub.heygo.cn:88`（自托管 Kong）；`/auth/v1/*` 亦有 Vercel rewrite。
- 分享/邀请链接全走 `window.location.origin`（自动跟随新域名，无硬编码自域名）。
- 用户访问域名 `mediahub.heygo.cn` 硬编码仅出现在 CORS 默认列表：`docker/config.yml`、`backend/config.yml`；prod 实际 CORS 走 NAS host env `CORS_ORIGINS`（Watchtower 不读 compose env）。
- 后端 CORS：`allow_origins`（env list）+ `allow_origin_regex`（`backend/app/main.py` `_CORS_ALLOW_REGEX` 与 `backend/app/core/exceptions.py` 双处，覆盖 vercel preview + localhost）。

## 6. 分阶段计划

> 每阶段：**目标 → 动作 → 验证 → 回滚点**。各阶段独立 ship，前一阶段稳定后再进下一阶段。detailed spec 于进入该阶段时另立。

### Phase 0 — 地基：服务器栈骨架 + Cloudflare Tunnel

- **目标**：GPU 机上 docker compose 骨架就绪，Cloudflare Tunnel 打通，一个 health 端点经 tunnel 可达。
- **动作**：核对/补齐 Docker + compose；安装 `cloudflared`、建 Tunnel、Cloudflare DNS 建 `api.nous.ink`/`sb.nous.ink`（先指占位/health）；跑一个 hello/health 容器验证经 tunnel 443 可达。
- **验证**：`curl https://api.nous.ink/healthz`（经 tunnel）返回 200，证书有效、无 `:88`。
- **回滚点**：纯增量，NAS 仍为唯一权威；删 tunnel/DNS 即回滚。

### Phase 1 — backend + Supabase 上 GPU 机

- **目标**：backend + 自托管 Supabase 在 GPU 机跑通，经 tunnel 暴露 `api.nous.ink`/`sb.nous.ink`。
- **动作**：
  - 在机上起自托管 Supabase 全套（Postgres/Kong/Realtime/Storage/auth），env 对齐（JWT secret、`SITE_URL`、Kong 路由、`ANON`/`SERVICE_ROLE` key）。
  - 数据迁移：NAS Supabase `pg_dump` → 机 restore；拷贝 Storage 卷（对齐存储统一 epic）。
  - 起 backend；`backend→AI` 配置改 localhost（去 zerotier）。
  - Tunnel 增 `api.nous.ink`→backend、`sb.nous.ink`→Kong 路由。
  - **部署机制（正式定为 P1 内容，从 §9 上提）**：在 GPU 机上装 **GitHub self-hosted runner**（`project_self_hosted_runner_plan`）。部署 = runner 本机 `git pull` 合并后的 commit → `docker compose up -d --build` **就地构建**，**不过 registry、不经 Watchtower**。runner 出站轮询 GitHub，NAT 无公网不影响。→ 部署从"分钟级 + registry 往返 + Watchtower 漂移坑"降到"十几秒级、一条命令、无漂移"（地板是 backend 冷启动：DBOS recovery + 连接池预热）。
- **验证（关键风险）**：
  - ⚠️ **Supabase Realtime/WebSocket 经 Cloudflare Tunnel 是否通**（Task Center 靠 Realtime 监听 `task_tracking`，必验）。
  - ⚠️ **Storage 卷迁移完整性**（文件可下载、路径一致）。
  - backend `/healthz`、一条真实 `/api/v1/*` 读写、DBOS workflow 起停、Kong `/auth/v1` 登录。
  - 用一个临时前端 build（env 指向新后端）跑通登录→读写→媒体。
- **回滚点**：NAS 栈保持在跑；前端 env 翻回 NAS 即回滚。

### Phase 2 — 前端指向新后端（前端仍在 Vercel）

- **目标**：现 Vercel 前端切到新后端域名，验证跨域直连路径。
- **动作**：
  - `frontend/.env.production`：`VITE_API_URL=https://api.nous.ink`、`VITE_SUPABASE_URL=https://sb.nous.ink`，去掉 `:88`。
  - 前端服务层从相对 `/api` 直连 `api.nous.ink`（`getApiUrl()` 已抽象，改 env 即可）；`vercel.json` 反代 rewrite 退役（保留 SPA fallback + headers）。
  - 后端 `CORS_ORIGINS` + Kong CORS 放行 `https://mediahub.heygo.cn`（过渡）与后续 `https://app.nous.ink`；`_CORS_ALLOW_REGEX` 视需要放行 preview。
  - `index.html` preconnect/dns-prefetch 从 `sb-mediahub.heygo.cn:88` 改 `sb.nous.ink`。
- **验证**：登录、`/api` 读写、`/media` 播放、Realtime、分享链接全绿（Vercel preview 上真机走查）。
- **回滚点**：env revert 回 NAS。

### Phase 3 — 前端上 Cloudflare Pages @ app.nous.ink

- **目标**：前端托管从 Vercel 切 Cloudflare Pages，绑 `app.nous.ink`。
- **动作**：
  - 建 Cloudflare Pages 项目；**GitHub Actions 构建 + `wrangler pages deploy` 直传预构建产物**（绕 500 构建/月，见 C2）。
  - `_redirects`：只做 SPA fallback（反代坑已在 P2 消掉）。
  - `_headers`：迁移 `vercel.json` 的安全头（`X-Content-Type-Options`/`X-Frame-Options`）+ `sw.js`/`version.json` 缓存策略。
  - 绑自定义域名 `app.nous.ink`（Cloudflare 自动证书）。
  - 验 PWA/Service Worker、`version.json` no-cache、`manifest.webmanifest`、20,000 文件上限（核 dist 文件数）。
  - CORS 追加放行 `https://app.nous.ink`。
- **验证**：`app.nous.ink` 全流程真机走查 + PWA 更新提示（对齐 `bug_pwa_update_button` / version 显示）。
- **回滚点**：`app.nous.ink` DNS 切回 Vercel。

### Phase 4 — apex 重定向 + 下线旧栈

- **目标**：收口域名，退役 Vercel + NAS。
- **动作**：
  - Cloudflare Redirect Rule：`nous.ink/*` → `https://app.nous.ink/$1`（301）；apex 占位橙云代理记录让规则可拦截。
  - 老 `mediahub.heygo.cn` → 301 → `app.nous.ink`（保书签）。
  - 稳定观察期后：下线 Vercel 项目 + NAS backend/Supabase 栈。
- **验证**：输 `nous.ink`、`mediahub.heygo.cn` 均 301 到 `app.nous.ink`；旧栈下线后功能无回归。
- **回滚点**：保留 NAS 栈至观察期结束再下线。

## 7. 关键风险登记

| # | 风险 | 阶段 | 缓解 |
|---|------|------|------|
| R1 | Supabase Realtime/WebSocket 经 Cloudflare Tunnel 不通 → Task Center 挂 | P1 | 早验；必要时 Realtime 单独走 tunnel WS 配置或独立 hostname |
| R2 | Storage 卷迁移丢文件/路径漂移 | P1 | 校验清单 + 抽样下载比对；对齐存储统一 epic |
| R3 | 自托管 Supabase env 对齐不全（JWT secret/SITE_URL/Kong 路由）→ 登录/PostgREST 挂 | P1 | 逐项 checklist；先核 information_schema 与 env |
| R4 | Cloudflare 500 构建/月撞顶 | P3 | CI 构建 + wrangler 直传（C2） |
| R5 | 一次动多层难定位故障 | 全 | D6 分阶段 + 回滚点；每阶段单独 ship |
| R6 | NAT 后 tunnel 稳定性/带宽（视频经 tunnel 出）| P1/P4 | 评估媒体是否仍从后端出经 tunnel；必要时媒体走 Cloudflare 缓存/独立通道 |

## 8. 非目标（本 epic 不做）

- apex 独立营销/落地站、跨子域登录态交接（D1）。
- 零停机活体迁移（D4）。
- AI/memory stack 迁移（已在本机，D2）。
- 后端业务逻辑改动（纯基础设施；CORS/env/域名之外不碰业务代码）。

## 9. 待后续 detailed spec 抠的开放项

- GPU 机现有服务清单 & 资源占用（AI 现占多少，backend+Supabase 余量）。
- Supabase 数据量级 & Storage 卷大小（定迁移窗口）。
- 媒体文件是否继续从后端经 tunnel 出，还是接入 Cloudflare 缓存（R6）。
- 后端 PR preview 是否纳入本 epic 还是另立（self-hosted runner 已上提为 P1 部署机制，见 §6 P1；PR preview 可在其上延后实施）。
