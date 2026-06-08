# 前端版本可见性 + 手动刷新 + 部署管线加固 — 设计文档

- 日期: 2026-06-03
- 状态: 设计待评审
- 沟通语言: 中文 / 代码与 UI: 英文

## 1. 背景与目标

当前前端(Vercel SPA)：
- 版本号 `v{__APP_VERSION__}` 只显示构建烤进 bundle 的版本(= 用户当前在跑的代码)，**无法知道线上最新已部署版本**。
- 更新走 `registerType: 'autoUpdate'` 全自动，但用户**看不见也控制不了**——`PWAUpdatePrompt` 组件存在却因 `needRefresh` 在 autoUpdate 下不触发而是**死代码**。
- 部署走 Vercel 原生自动 promote，不在 CI 纪律内 → 撞过 **alias-stuck 竞态**(git `8048b0c0` "force Vercel to promote v0.23.11 (prod alias stuck on 0.23.10)")。

**核心诉求(用户确认)**：可见性/信任 + 主动刷新控制。并把"有新版 + 升级要点"展示到现有**通知中心(铃铛)**。

**本轮一并解决**：alias-stuck 部署时序竞态(用户选"全控串行 promote — 真修复")。

**本轮不做(已定)**：多 worktree 版本号碰撞 / merge queue(另开专题)、强制更新、Realtime 推送(focus 触发够用)。

## 2. 现状盘点(已核实)

| 资产 | 现状 | 位置 |
|------|------|------|
| 版本注入 | `pkg.version → __APP_VERSION__` | `frontend/vite.config.ts:112` |
| 版本显示 | `v{__APP_VERSION__}` | `SettingsModal.tsx:275`, `MobileProfilePage.tsx:168` |
| PWA 更新 | `autoUpdate + skipWaiting + clientsClaim`(有事故史，**不动**) | `vite.config.ts:33-90` |
| 更新弹窗 | `PWAUpdatePrompt`(已挂载于 `index.tsx:64`，但 `needRefresh` 死) | `components/PWAUpdatePrompt.tsx` |
| PWA hook | `usePWA` 暴露 `needRefresh/offlineReady/applyUpdate/dismissUpdate` | `hooks/usePWA.ts` |
| 部署日志表 | `deployment_logs`(service/version/commit_sha/commits/summary/release_notes/...)，schema 已预留 `service='frontend'` | `supabase/migrations/118,119` |
| 部署日志 RLS | `admin_read` 谓词实为 `EXISTS(auth.uid())` = 任何登录用户可读 | mig 118 |
| 通知表 | `notifications(type system\|team, title, content, team_id, created_at)` + `user_notifications`(每用户已读) | `supabase/migrations/009`(注意 051/078 动过 id 标准化) |
| 通知面板 | `NotificationPanel` 渲染 system/team 两组、已读/未读、未读 badge | `components/NotificationPanel.tsx`, `services/notificationService.ts` |
| system 通知插入 | RLS 无 client INSERT policy，只能 service role / SECURITY DEFINER | mig 009:269-275 |
| 后端部署管线 | 已用 `concurrency` 组 + post-deploy 轮询 `/api/version` 比对 `commit_sha`，对不上**大声 fail** | `.github/workflows/deploy-backend.yml:22,194-230` |
| admin 部署 | 已用 `concurrency:{group, cancel-in-progress:true}` | `.github/workflows/deploy-admin.yml` |
| 前端部署 | **无 CI 管线**，纯 Vercel 原生 git 集成自动 promote ← 洼地 | `frontend/vercel.json` |

## 3. 架构：三个数据面，各司其职

| 数据面 | 回答的问题 | 形态 / 驱动 |
|--------|-----------|------------|
| **`version.json`**(跟构建产物走，原子) | "**我这个标签页**是不是旧代码？" | 驱动 `PWAUpdatePrompt` + Settings "Update Now"(可操作) |
| **`deployment_logs`**(CI post-success 写，race-free) | "线上发布历史 + 这版改了啥" | admin 时间线 + `release_notes` 内容源 |
| **`notifications(system)`**(由 `deployment_logs` 触发器派生) | "广播给**所有用户**：有新版 + 升级要点" | 铃铛 feed + 未读 badge |

设计原则(对齐大厂范式 & 用户讨论)：**产物自带版本，不靠 side-channel 抢着宣告"即将上线"；"线上版本"在部署成功后才记录(观测事实)。** 详见 §5。

**操作面边界(用户订正)**：
- **写/发布 `release_notes`(广播内容)= 运营动作 → 在 admin 应用做**(admin `/deployment-logs` 页加编辑+发布)。
- **用户前端 = 纯消费**：铃铛只渲染派生出来的 system 通知，不做任何发布操作。
- **触发器 write-source 无关**：无论 release_notes 由 admin UI 还是现有 Claude Code MCP 写入，都同样触发派生。

## 4. Part 1 — 前端自检(version.json)

### 4.1 构建盖章 `version.json`
- `vite.config.ts` 加内联插件，在 build 时 emit `dist/version.json`：
  ```json
  { "version": "<pkg.version>", "commitSha": "<short sha>", "buildTime": "<iso>" }
  ```
  - `commitSha` 取 `process.env.VERCEL_GIT_COMMIT_SHA`(Vercel 注入；本地/CI fallback 到 git rev-parse 或空串)。取前 7 位，与 backend `build-info.json` 口径一致，供 CI verify 比对。
- 单一真相源仍是 `package.json` → 与 `__APP_VERSION__` 同源，永不漂移。

### 4.2 防缓存(针对 SW 漂移史，三重保险)
- workbox `globIgnores: ['**/version.json']` → SW 永不预缓存。
- 前端 `fetch('/version.json?t=' + Date.now(), { cache: 'no-store' })`。
- `vercel.json` 给 `version.json` 加 `Cache-Control: no-cache`(照抄现有 `sw.js` 那条 header)，且确认 `version.json` 不被 SPA fallback rewrite 吞掉(rewrite 的 negative-lookahead 里排除它)。

### 4.3 `useVersionCheck` hook(无轮询)
- 触发时机：**挂载时** + **`visibilitychange`(标签页切回可见)**。**不设 timer。**
- 暴露 `{ currentVersion, latestVersion, updateAvailable, loading, error }`，`updateAvailable = !!latestVersion && latestVersion !== __APP_VERSION__`。
- 错误处理：fetch 失败仅 `console.error` + 保持 `updateAvailable=false`(不打扰用户)，不 throw。

### 4.4 接现成 UI(不造轮子)
- `PWAUpdatePrompt`：显示条件 `needRefresh || updateAvailable`；文案带真实版本号(`v0.23.10 → v0.23.11`)；"Update Now" 走现成 `applyUpdate`(`updateSW(true)`)，并以 `window.location.reload()` 兜底。
- `SettingsModal` + `MobileProfilePage`：`updateAvailable` 时在现有 `v{version}` 旁追加 "· latest vX.Y.Z" + 小 Update 链接(点击 = applyUpdate)。
- i18n：新增 `notifications`/`pwa` 相关 key 到 `en.json` + `zh.json`(英文为准)。

## 5. Part 2 — 部署管线加固(全控串行 promote)

### 5.1 新增 `.github/workflows/deploy-frontend.yml`
- 触发：`push: master, paths: ['frontend/**']` + `workflow_dispatch`。
- 串行单写者：
  ```yaml
  concurrency:
    group: deploy-frontend-prod
    cancel-in-progress: true
  ```
- **关闭 Vercel 自动 promote**(Vercel 项目设置：production branch 不自动 deploy / 或 Git ignored build)，改由 CI 驱动：
  ```
  vercel pull --environment=production
  vercel build --prod
  vercel deploy --prebuilt --prod   # 始终部署当前 HEAD，幂等
  ```
  → 即使旧 commit 的 build 后完成也无法把 prod 拉回旧版(promote 永远瞄 HEAD)。
- 所需 GH secrets：`VERCEL_TOKEN`、`VERCEL_ORG_ID`、`VERCEL_PROJECT_ID`(待补)。

### 5.2 post-deploy verify(1:1 镜像 backend)
- 轮询 prod `https://mediahub.heygo.cn/version.json` 的 `commitSha == github.sha[:7]`，15 分钟预算，超时 `::error::` 大声 fail。
- 用 `vars.PROD_FRONTEND_VERSION_URL` gate，未设则跳过(对 fork/PR 安全)。

### 5.3 post-success 写 `deployment_logs`(race-free)
- verify 通过后，CI `curl` PostgREST `POST /rest/v1/deployment_logs`，service role key：
  ```json
  { "service":"frontend", "version":"v<pkg.version>", "commit_sha":"<sha7>",
    "deployed_by":"<github.actor>", "status":"success", "metadata":{...} }
  ```
- 因为是 post-success 写 → 彻底不在竞态里。`release_notes` 仍由现有 Claude Code 推送后流程补写(UPDATE)。
- 所需 GH secret：`SUPABASE_SERVICE_ROLE_KEY` + `SUPABASE_REST_URL`(待确认是否已有)。

## 6. Part 3 — 通知派生 + admin 发布 + 用户消费

> 三个角色：**admin 写内容** → **DB 触发器派生通知** → **用户前端消费展示**。

### 6.0 admin 应用：release_notes 编辑 + 发布(运营操作面)
- `admin/src/pages/deployment-logs/index.tsx` 目前**纯只读**(渲染 release_notes/commits/published_by)。新增：
  - 每行一个 "Edit / Publish notes" 入口 → 编辑器(markdown textarea，复用现有 `ReleaseNotes` 预览组件)→ 保存。
- **写路径(admin 用 anon key，deployment_logs RLS 只读无 client 写策略 → 不能直写)**：经**后端 admin 端点**：
  - `PATCH /api/v1/admin/deployment-logs/{id}` body `{ release_notes, published_by }`，后端持 service role 更新(绕 RLS)，带 **admin 鉴权**(机制待规划期确认，见 §12)。
  - Router→Service→Repository 分层。
- 现有 Claude Code via MCP 写 release_notes 的流程**保留**，与 admin UI 并存(都只是 UPDATE release_notes 的写者)。

### 6.1 触发器派生 system 通知(write-source 无关)
- 新 migration：`AFTER UPDATE OF release_notes ON deployment_logs`，`SECURITY DEFINER` 函数，条件：
  - `NEW.service = 'frontend'` AND `NEW.status = 'success'`
  - `OLD.release_notes IS NULL/''` AND `NEW.release_notes` 非空(从空→非空 transition)
  - → `INSERT INTO notifications(type='system', title, content)`：
    - `title` = `'New version ' || NEW.version`(英文)
    - `content` = `NEW.release_notes`(markdown 摘要；面板按纯文本/截断渲染)
- 幂等：防重复——同 `deployment_logs.id` 只派生一次(用 `metadata->>'notification_id'` 记账，或唯一约束 on a derived key)。
- **决策(用户确认)**：只在 `release_notes` 发布时生成 → 用户看到的是有内容的"What's new"，纯重部署/无 notes 不打扰(避免 `8048b0c0` 这类 force-redeploy 刷屏)。

### 6.2 用户前端：铃铛条目的可操作刷新(纯消费)
- `NotificationPanel`(用户前端，frontend/)对 `type='system'` 且 title 含版本号的条目，当本地 `useVersionCheck().updateAvailable` 为真时，额外渲染 "Update Now" 按钮(= `applyUpdate`)。
- 若 `updateAvailable` 为假(用户已在最新)，纯信息展示。
- 用户前端**不做发布**，只渲染触发器已派生好的通知。

## 7. 数据 / 迁移清单

1. `supabase/migrations/NNN_deployment_logs_notify_trigger.sql`：§6.1 触发器 + 函数 + 幂等账。
2. **落地前先核**(遵循 `feedback_verify_columns_before_select`)：
   ```sql
   SELECT column_name, data_type FROM information_schema.columns
   WHERE table_name IN ('notifications','deployment_logs') ORDER BY table_name, ordinal_position;
   ```
   重点确认 `notifications.id`/`team_id` 经 051/078 后的**实际类型**(UUID vs bigint)，触发器 INSERT 不要写错类型。
3. 无需新 RLS：`deployment_logs` 已允许登录用户读；`notifications` system 行由 SECURITY DEFINER 触发器插(绕 RLS)。

## 8. 非目标 / YAGNI

- 不碰 `autoUpdate` SW 策略(有事故史)。
- 不做 Realtime 订阅(focus 触发已覆盖现实场景；autoUpdate 下次导航兜底)。
- 不做强制更新 / 倒计时自动刷新。
- 不做版本号碰撞 / merge queue(另开专题)。
- 不新增后端"期望前端版本"接口。

## 9. 风险与缓解

| 风险 | 缓解 |
|------|------|
| `version.json` 被 SW/CDN 缓存 → 拿到旧版本号失效 | globIgnores + no-store + cache-bust + vercel no-cache header(四重) |
| 关闭 Vercel auto-promote 配错 → 前端不部署 | 先在 preview/PR 验证 CI 部署链路；保留 `workflow_dispatch` 手动兜底；分阶段(§10)先加 verify-only 再切串行 |
| 触发器把 system 通知插重复 | 幂等账(per deployment_logs.id 只派生一次) |
| `notifications.id` 类型踩 42703/类型不匹配 | 落地前 information_schema 核类型 |
| Vercel secrets 缺失 | §5.1/§5.3 列"待补 secret"清单，开工前确认 |

## 10. 落地顺序(分阶段，降风险)

- **P1 功能层(纯前端，零基础设施)**：version.json 插件 + vercel header + `useVersionCheck` + 接 `PWAUpdatePrompt`/Settings。可独立上线、马上能用、可在 Vercel preview 验证。
- **P2 通知派生(DB + admin + 用户前端)**：(a) deployment_logs notify 触发器 migration；(b) **admin** 应用加 release_notes 编辑/发布 UI + 后端 admin 写端点；(c) **用户前端** 铃铛条目 Update 按钮。三者：admin 写 → 触发器派生 → 前端消费。
- **P3 管线加固(CI/Vercel)**：先加 `deploy-frontend.yml` 的 **verify-only**(不关 auto-promote，先抓 alias-stuck)；验证稳定后再切**全控串行 promote**(关 Vercel auto-promote)。post-success 写 deployment_logs 随 P3 上。

> 分阶段让"马上能用的可见性"与"高风险的 Vercel 配置改动"解耦；每阶段走 per-PR Vercel preview 验证(遵循 `feedback_verify_via_vercel_preview_not_local`)。

## 11. 测试计划

- **单元**：`useVersionCheck`(mount/visibility 触发、updateAvailable 计算、fetch 失败不打扰)。
- **构建产物**：build 后断言 `dist/version.json` 存在且 version == package.json、不在 SW precache manifest 内。
- **集成(DB)**：插入 frontend `deployment_logs` 行后 UPDATE `release_notes` → 断言派生出 1 条 system `notifications`(且重复 UPDATE 不再派生)。
- **E2E / preview**：Vercel preview 上模拟版本不一致 → 断言 PWAUpdatePrompt + Settings badge + 铃铛条目出现；点击 Update 触发 reload。
- **CI verify**：故意让 prod 落后 → 断言 deploy-frontend verify step fail（大声）。

## 12. 待补清单(开工前确认)

- [ ] GH secrets：`VERCEL_TOKEN` / `VERCEL_ORG_ID` / `VERCEL_PROJECT_ID` / `SUPABASE_SERVICE_ROLE_KEY` / `SUPABASE_REST_URL` 是否已有。
- [ ] repo vars：`PROD_FRONTEND_VERSION_URL`。
- [ ] Vercel 项目设置：如何关闭 production branch 自动 promote(Git → Ignored Build Step / Deploy Hooks)。
- [ ] `information_schema` 核 `notifications`/`deployment_logs` 列类型。
- [ ] 下一个 migration 序号。
- [ ] **admin 鉴权机制**：后端 `PATCH /api/v1/admin/deployment-logs/{id}` 如何判定"是 admin"(现有 admin role / claim / 白名单？)——决定写端点的 authz。
