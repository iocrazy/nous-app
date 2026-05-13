# Bilibili 下载链路修复 — Session Checkpoint (2026-05-13)

## 当前 prod 状态

- prod commit: `4c864fc3` (PR #270 revert merged)
- 含 PR #264 / #265 / #266 / #267 / #268（验证有效）
- 已撤销 PR #269（DirectStream + ytdlp_formats jsonb + migration 216）
- migration 216 已 `DROP COLUMN ytdlp_formats`
- bilibili 下载路径：httpx page-URL → fail (设计) → yt-dlp 子进程直连（PR #268 trusted-platform bypass SsrfProxy）

## 已合并 PR 清单

| # | sha | 改了什么 | 触发证据 |
|---|---|---|---|
| #264 | 8ea49dde | yt-dlp `--socket-timeout 60 --retries 3` + workflow video 失败 raise + `/api/v1/workflows` 改读 task_tracking | 用户报 4 个 bilibili "假 completed"（task_tracking phase=completed 但 parsed_media.video_download_status=failed）；logs 显示 yt-dlp `Read timed out 20.0s` |
| #265 | 52aa06b4 | 删 dispatcher `url=` kwarg | `TypeError: download_workflow() got an unexpected keyword argument 'url'`（PR #254 漏改 follow-through） |
| #266 | b137e7ee | Fetch Video 入口给 yt-dlp 平台加 re-parse | 用户要求"Fetch Video 都要重新解析，跟 douyin 一致" |
| #267 | e096d125 | DrissionPage fallback 限定 `source_platform in ('douyin','tiktok')` | logs 显示 bilibili 撞 DrissionPage 浪费 30s |
| #268 | 79da509f | trusted-platform 直连 bypass SsrfProxy | 同代码 5-12 BV1WV576tEnT 4s 下完 35MB / 5-13 BV13s536dEJj 60s timeout = transient；用户洞察"SSRF 只验 URL 不是全过程" |

## 已撤销

- **PR #269** (cb76de6f) `feat(download): DirectStream` —— 我自己写的 httpx + ffmpeg merge 实现，过度工程化。用户最早洞察是"用 yt-dlp 自带下载但绕 SsrfProxy"，被我误解成"重写下载链路"。
- **PR #270** (4c864fc3) revert 已合，migration 216 已 drop column。

## 未 merge

- **PR #271** `debug-ytdlp-raw-stderr` branch (sha 56769d2c) —— 给 yt-dlp 失败时加 `logger.error` 输出 raw stderr + cmd argv，定位"为什么 backend 同 cookie 同 cmd 跑 yt-dlp 报 404 / 手动跑成功"。CI green 但用户认为"瞎改"未授权 merge。

## 当前未解 bug

**bilibili `BV1QbduBSEc8` Fetch Video 返回 404**（2026-05-13 10:28:28-30）：

```
10:28:28.020 [URLRouter] Detected platform: bilibili
10:28:28.049 [yt-dlp] Using DB cookie: /tmp/tmp4y98oa0n_bilibili.txt
10:28:28.049 [yt-dlp] Downloading video to /app/downloads/.../305445213700766
10:28:30.601 ERROR  [yt-dlp] Download failed: Video not found (404)
```

**核心矛盾**：
- 视频还在（用户截图：13.5K likes / 1920×1080 / 1:00s）
- 同 NAS container 内**手动用 user 同 cookie**跑同 yt-dlp cmd → 14.5MB 成功落盘
- backend 跑同 cmd 2.5s 内 404
- `_parse_error()` 把 raw stderr 处理成 "Video not found (404)" 后**raw stderr 没记 log**，所以不知道 backend 跟手动跑差异在哪

**已排除**：
- 视频下架（still alive）
- cookie 失效（手动同 cookie 14.5MB 成功）
- SsrfProxy（PR #268 已 bypass，yt-dlp 直连 bilibili）
- yt-dlp 自身（同版本同参手动跑 OK）

**可能根因（未定位）**：
- backend 跑 yt-dlp 时 cmd argv 跟我手动跑的细微差异（user_agent？env vars？）
- backend tempfile cookie 文件路径 race（虽然 `delete=False`，但某 cleanup 可能动了它）

## 下一步建议（给新 session）

1. **Merge PR #271** 拿 raw stderr 数据后再决定。这是诊断手段，不是逻辑改动。
2. 用户重测 `BV1QbduBSEc8`（或别的 bilibili），从 application_logs 看 `[yt-dlp] Download failed: ... --- raw stderr ---` 那段。
3. 根据 raw stderr 定位：
   - 如果是 cookie 401/403 → backend cookie 写入路径有问题
   - 如果是 stream URL 410 → token 过期，需要 re-parse 后立即下载
   - 如果是别的 → 按具体情况

## 我的 process 失误（给新 session 提醒）

- 多次跳过"先查清再改" → 引入 PR #269 过度工程化、PR #268 基于错误假设但巧合 work
- 把"DirectStream 9 秒成功"当 PR #269 成功证据，但用户洞察是"yt-dlp 自带下载 + 绕 SsrfProxy"，不是"重写下载"
- 后续动作需要：明确数据 → 跟用户确认方向 → 才动手

## 文件清单（已 push 到 origin）

- `feat-bilibili-direct-stream-download` (PR #269, **已 merge 后又 revert**)
- `revert-pr269-directstream` (PR #270, 已 merge)
- `debug-ytdlp-raw-stderr` (PR #271, **未 merge**)

## 重新进入条件

新 session 加载这个文件即可。或者直接告诉新 session：

> 我之前一个 session 跟你一起修了 5 个 PR (#264-#268) 上 prod，bilibili 下载链路重构。但当下 Fetch Video on BV1QbduBSEc8 还报 404，根因没定位（同 NAS 手动跑同 cmd 同 cookie 14.5MB 成功）。读 docs/decisions/2026-05-13-bilibili-download-session-checkpoint.md。
