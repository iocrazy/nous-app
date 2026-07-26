# Watchtower configuration runbook（已退役）

Last reviewed: 2026-07-26

> **状态：Watchtower 已退役，本文档只保留背景与教训。**
>
> 本文原先是一份「怎么把 Watchtower 修好」的操作手册（生成
> `WATCHTOWER_HTTP_API_TOKEN`、暴露 8083、轮询缩到 300 秒）。**那些步骤
> 现在不要执行** —— 前提已经不存在，照做会重新打开一个已经关掉的
> 无人使用的公网触发面。当前权威口径见 `CLAUDE.md` 的「CI/CD 部署」节。

## 现状（2026-07-26）

发布收敛到 **gpupc 单线**：`deploy-gpu.yml` + self-hosted runner，本机
build + `docker compose up -d`，不经 ACR、不经 Watchtower。

nas-A 上的 `heygo_watchtower` 容器仍在，但已实质休眠：

| 项 | 值 | 说明 |
|---|---|---|
| `WATCHTOWER_POLL_INTERVAL` | `86400` | 24h。原为 300 |
| `WATCHTOWER_HTTP_API_TOKEN` | **已移除** | 见下方「为什么整块关掉」 |
| `WATCHTOWER_HTTP_API_UPDATE` | **已移除** | |
| `WATCHTOWER_HTTP_API_PERIODIC_POLLS` | **已移除** | |
| `ports: 8083:8080` | **已移除** | 外部探测应 `Failed to connect` |
| `WATCHTOWER_LABEL_ENABLE` | `true` | 只管带 enable 标签的容器 |

容器日志每轮 `Failed=0 Scanned=0 Updated=0` —— 原先带
`com.centurylinklabs.watchtower.enable=true` 标签的
`mediahub-app-backend` / `mediahub-admin` / `mediahub-worker` 随老栈一起
拆除（`docker ps -a` 里已不存在），所以它现在什么都不管。

compose 快照：[`deploy/nas/watchtower-docker-compose.yml`](../../deploy/nas/watchtower-docker-compose.yml)，
与 nas-A 活文件逐行一致。

## 为什么整块关掉 HTTP API，而不是只换 token

仓库 2026-07-25 切 public 后，旧版 `scripts/deploy.sh` 里硬编码的
`WATCHTOWER_TOKEN` 变成世界可读 —— 而且 **git 历史是永久的**，从 HEAD
删掉并不能收回。

只删 token 而保留 `WATCHTOWER_HTTP_API_UPDATE=true` 是错的：要么容器起
不来，要么留下一个**无鉴权**的更新端点，比原状更糟。既然没有任何东西再
调这个 webhook，正确做法是拆掉整条路径 —— 三个 env + 端口映射一起移除。

## 已失效的调用方（别指望它们还能用）

- **`deploy-backend.yml` 的 "Trigger Watchtower update" 步骤** —— curl
  `secrets.WATCHTOWER_URL`，现在必然拿不到响应。它的兜底提示写着
  "will auto-poll in 5min"，但轮询已改 24h 且 `Scanned=0`，那句话是错的。
- **`scripts/deploy.sh` 的 `WATCHTOWER_URL`** —— 默认值指向 `:8777`，而
  API 当年映射的是 `:8083`。**这条路径从来就是死的**，属于 2026-05-12
  那次事故的残留。

## 值得带走的教训（这才是本文档留存的理由）

**2026-05-12 事故**：deploy workflow 把每个 PR 的部署都报成 ✅ 成功，实际
上 Watchtower webhook 返回 200/504 却压根不是真的 Watchtower HTTP API ——
新镜像留在 ACR 里，prod 一直跑旧版。数小时耗在「下载功能坏了」上，而看的
是错误版本的代码。

教训：

1. **触发返回 200 不等于事情发生了。** 必须独立断言*结果*，不是断言*调用
   成功*。当时的修法是 PR #253 加 `GET /api/version` + CI "Verify deploy"
   步骤轮询它。现在 `deploy-gpu.yml` 的对应机制是 smoke 探
   `/api/v1/readyz` + 失败自动回滚镜像。
2. **best-effort 的步骤会把失败伪装成成功。** 那个 webhook 步骤当年写着
   `|| echo "000"` 加一句温和的 ⚠️ 提示，于是永远不红。同类问题在本仓库
   反复出现（CI 漂移闸门缺 secret 就 exit 0、actionlint 因账单假红盖住真
   失败）—— **凡「没有坏消息」，先证明报信通道是活着的。**
3. **配置的真相之源要唯一。** 这份 runbook 与 NAS 活文件曾各说各话
   （文档说 `POLL_INTERVAL=3600`，活文件是 `300`）。现在 compose 快照与
   活文件逐行对齐，本文档只描述状态、不再重复具体值。

## 若将来要恢复 NAS 作为回滚锚点

老栈已整体拆除，恢复不只是打开 Watchtower：

1. 先重建 `mediahub-app-backend` / `worker` 与它们连的 `mediahub-sb-prod-*`
   栈（注意 ⚠️ NAS supabase 容器 force-recreate 会让烙在容器里的 legacy
   JWT key 失效）
2. 给需要自动更新的容器加回 `watchtower.enable=true` 标签
3. 按 `deploy/nas/watchtower-docker-compose.yml` 文件头的步骤重新启用
   HTTP API（**新 token、写 `.env`、compose 用变量引用、绝不内联**）
4. 更新本文档与 `CLAUDE.md`
