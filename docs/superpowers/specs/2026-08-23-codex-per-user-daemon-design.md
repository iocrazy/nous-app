# Codex per-user daemon (C 方案) — 设计

**状态**: 立项开工（2026-08-23）
**背景**: 画布的 codex 出图链（A 方案）已上线，但用的是**服务器上一份 owner 的 codex 登录态** —— 别的用户跑生成烧的是 owner 的订阅额度，且 owner 的 token 是单点。C 方案让每个用户用**自己本机的 codex 登录态**，token 永不离开用户设备。

三个候选方案的取舍（2026-08 对话中拍板，此前未落文档，本文补上）：

| 方案 | 机制 | 状态 |
|---|---|---|
| A | 服务器一份 owner codex 登录态，全员共用 | ✅ 已上线（出图） |
| B | 每用户填自己的 OpenAI API key（BYOK） | ❌ 不做 —— 用户要另付 API 费，享受不到已有的 ChatGPT 订阅额度 |
| C | 每用户本机跑伴生 daemon，用自己的 codex 登录态 | 本文档 |

---

## 1. 核心约束

1. **token 不出本机**：nous 服务端永远拿不到用户的 `~/.codex/auth.json`；服务端只发任务、收产物。
2. **不要求用户有公网 IP / 开端口**：daemon **主动出站**连 nous（WSS），穿透 NAT 与家宽 CGNAT。
3. **服务端不得获得任意命令执行**：daemon 只跑白名单命令，参数结构化传递，绝不拼接 shell。
4. **失败必须类型化可见**：daemon 不在线 / 超时 / codex 未登录，都要变成用户看得懂的提示，不能是 silent no-op（见 CLAUDE.md「触发路径必须类型化失败回显」）。

## 2. 架构

```
浏览器 ──选 "GPT CLI(本机)" 引擎──► nous 后端
                                      │ 查该 user 有无在线 daemon
                                      ├─无 → 类型化错误 daemon_offline
                                      └─有 → 经 WS 下发 job
                                                │
用户本机 daemon ◄──出站 WSS 长连──────────────┘
   │ 白名单执行 codex exec / gpt-image-2-skill images
   │ 产物 → 一次性上传票据 → POST /generated-media/import
   └─► 回报 job 完成（含 gen_id）
```

**为什么产物走 HTTP 上传而不是 WS 二进制帧**：图片/视频几 MB 到几十 MB，走长连会阻塞心跳与其他 job 的调度；HTTP 分片上传天然带断点与超时语义，且**直接复用现成的 `/generated-media/import` 入库链**（object-store/文件系统双写、Snowflake id、durable url 全部现成）。

## 3. 配对（pairing）

复用 `ws_ticket_router.py` 已验证的一次性凭证范式（Redis GETDEL 防重放）：

1. 用户在 nous「设置 → 本地 codex」点「配对」→ 后端生成 **8 位配对码**，Redis 存 `codex_pair:<code> → user_id`，TTL 10 分钟，一次性消费。
2. 用户在 daemon 终端 `nous-codex pair <code>`。
3. daemon POST `/api/v1/codex-daemon/pair {code, device_name, platform}` → 后端 GETDEL 校验 → 生成 **device_token**（随机 32 字节，仅哈希入库，明文只返回一次），落 `codex_daemons` 表：`id / user_id / device_name / platform / token_hash / created_at / last_seen_at / revoked_at`。
4. daemon 把 device_token 写到本机 `~/.config/nous-codex/config.json`（600）。

**吊销**：设置页列出该用户所有已配对设备 + 最后在线时间，可单独吊销（写 `revoked_at`，长连立即断开）。

## 4. 连接与心跳

- daemon 连 `wss://api.nous.ink/api/v1/ws/codex-agent?token=<device_token>`（**device token 走 header 更好，但浏览器 WS 无 header —— daemon 不是浏览器，故用 `Authorization` header，避免 URL 泄漏**）。
- 服务端认证后把连接登记进内存表 `user_id → {ws, device_id, capabilities}`；多设备同用户时按最近心跳择一（后续可做轮询/亲和）。
- 心跳：daemon 每 30s 发 `{"type":"ping"}`，服务端回 `pong`；90s 无心跳判定掉线。
- 断线重连：daemon 指数退避（1s→30s 上限），重连后自动续。

## 5. 派活协议（JSON over WS）

服务端 → daemon：
```json
{"type":"job","job_id":"<uuid>","kind":"image|text",
 "payload":{"prompt":"…","model":"gpt-image-2","size":"1024x1024",
            "ref_urls":["https://api.nous.ink/api/v1/generated-media/…"],
            "upload_ticket":"<one-shot>"}}
```
daemon → 服务端：
```json
{"type":"job_progress","job_id":"…","phase":"running"}
{"type":"job_done","job_id":"…","gen_id":"991"}
{"type":"job_failed","job_id":"…","code":"codex_not_logged_in","message":"…"}
```

**参考图**：daemon 用 job 里的 `ref_urls` + 上传票据同源凭证**自行下载**到临时目录再传给 CLI（不经 WS 传大文件）。

**白名单执行**（daemon 端硬编码）：
- `kind=image` → `gpt-image-2-skill images generate|edit --prompt … --out … --size … [--ref-image …]`
- `kind=text` → `codex exec --json --model … <prompt>`
- 除上述参数外一律拒绝；prompt 作为**单个 argv 元素**传入，不进 shell。

## 6. 后端改动点

1. `codex_daemons` 表 + 迁移（含 `token_hash` 唯一索引、`user_id` 索引）。
2. `codex_daemon_router.py`：`POST /pair`、`GET /devices`、`DELETE /devices/{id}`、`WS /ws/codex-agent`。
3. 连接注册表（内存 + Redis 存活标记，供多 worker 判断"该用户是否有在线 daemon"）。
4. `resolve_image_provider` 增加一条分支：catalog 行 `actual_provider='codex-local'` → 走 daemon 派活；无在线 daemon 时抛 `DaemonOfflineError`（类型化，前端显示"你的本地 codex 未连接"）。
5. 派活等待用 DBOS workflow step（超时 10 分钟 → 标记 failed，可重试）。

## 7. 前端改动点

- 设置页新增「本地 codex」区块：配对码生成、已配对设备列表（含在线状态与最后在线时间）、吊销、安装引导（`npx @nous/codex-daemon`）。
- 画布引擎下拉多一项 **GPT CLI (本机)**；选中但无在线 daemon 时，pill 旁显示离线徽标并在 Run 时给出可读错误。

## 8. daemon 形态

单文件 Node 脚本（用户装了 codex 就有 node），`npx @nous/codex-daemon pair <code>` / `run`。理由：零编译、跨平台、易审计（用户能读源码确认 token 不外传）。后续可选 Rust 单二进制。

## 9. 分期

| 阶段 | 内容 | 可验收 |
|---|---|---|
| **C1** | 表 + 配对 API + 设备列表/吊销 | 配对码换出 device_token，设置页看得到设备 |
| **C2** | WS 长连 + 心跳 + 在线注册表 | 设置页显示"在线"，断网 90s 转"离线" |
| **C3** | daemon 骨架（连、心跳、白名单执行、产物上传） | 手动构造 job 能在本机跑出图并入库 |
| **C4** | 派活链打通（catalog 行 + provider 分支 + DBOS 等待） | 画布选 GPT CLI(本机) 真出图 |
| **C5** | 前端设置页 + 引擎项 + 离线态回显 | 端到端可用，离线有可读提示 |

## 10. 安全清单（实现时逐条自查）

- [ ] device_token 只存哈希；明文仅返回一次
- [ ] daemon 侧命令白名单 + argv 数组传参（无 shell）
- [ ] 上传票据一次性、短 TTL、绑 job_id
- [ ] `ref_urls` 只允许 nous 自身域名（防 SSRF 让 daemon 去抓内网）
- [ ] 吊销后长连立即断开
- [ ] daemon 配置文件 600 权限
