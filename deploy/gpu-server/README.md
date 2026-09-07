# GPU 服务器部署（P0：骨架 + Cloudflare Tunnel）

Epic spec：`docs/superpowers/specs/2026-07-20-nous-ink-infra-migration-epic.md`
P0 计划：`docs/superpowers/plans/2026-07-20-p0-gpu-server-cloudflare-tunnel.md`

## Recon 记录（2026-07-21 实测，Task 1）

- 机器：`10.0.0.10`（ZeroTier；Ubuntu，主机名 heygo-Ubuntu）
- 硬件：48 核 / 123G RAM / 根盘 937G（空闲 782G）/ RTX Pro 6000 Blackwell 96G + 双 RTX 3090
- 盘位：系统盘 = 970 PRO 1T（`/`）；**数据盘 = Predator GM7000 2T ext4 `/media/heygo/program`**；另有近空 970 PRO 1T `/media/heygo/cache`
- 出站 Cloudflare：✅（`api.cloudflare.com` 可达）
- Docker：2026-07-22 安装（29.6.2 + compose v5.3.1，heygo 已入 docker 组免 sudo）
- AI 栈跑法：**原生 venv、零容器**——`:8000` 为 nous-center uvicorn 网关 + vLLM（`/media/heygo/program/projects-code/repos/nous-center/`）。容器栈与其互不干扰
- sudo：需交互密码（远程 BatchMode 无法 sudo，需要 sudo 的步骤由用户在机上执行）

## 数据盘布局（唯一备份边界 = 这一个目录）

```
/media/heygo/program/datahub/nous/          # 2T NVMe，非系统盘
├── .mounted                        # nofail 挂载防护 marker（起栈前检查）
├── data/                           # P1 起的持久卷：postgres / redis / storage / runner
└── secrets/
    ├── cloudflared/                # cert.pem + <TUNNEL_ID>.json（owner 65532，mode 600）
    ├── backend.env                 # backend / worker / gateway 共用
    └── browser.env                 # 仅 nous-browser：只放 BROWSER_INTERNAL_TOKEN
```

**重装系统恢复路径**：装 Docker（下方前置条件）→ 恢复 fstab 数据盘条目 → clone 本仓库 → `docker compose up -d`。数据与密钥全程在 2T 盘上不动。

⚠️ 数据盘是 `nofail` 挂载：万一开机未挂上，bind mount 会在系统盘上生出影子目录。P1 的起栈脚本必须先检查 `/media/heygo/program/datahub/nous/.mounted` 存在才允许 `compose up`。

## 前置条件（需 sudo，用户在机上执行一次）

```bash
# 官方 apt 源方式安装 Docker Engine + compose plugin
sudo apt-get update && sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update && sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker heygo          # 免 sudo 用 docker，重新登录生效
mkdir -p /media/heygo/program/datahub/nous/secrets /media/heygo/program/datahub/nous/data
touch /media/heygo/program/datahub/nous/.mounted
```

其余前置：Cloudflare zone `nous.ink` active + Zero Trust 权限。

### ⚠️ `nous-browser` 一次性密钥前置（发布模块会话通道，S1 起）

`nous-browser` 的 `env_file` 指向 `secrets/browser.env`。**该文件不存在时 `docker compose up` 会直接拒绝起栈**，连带 backend 也发不出去 —— 所以 S1 合入前必须先在 gpupc 上执行一次：

```bash
TOKEN=$(openssl rand -hex 32)
umask 077
printf 'BROWSER_INTERNAL_TOKEN=%s\n' "$TOKEN" \
  > /media/heygo/program/datahub/nous/secrets/browser.env
# 同一个值给调用方（backend / worker）
printf 'BROWSER_INTERNAL_TOKEN=%s\n' "$TOKEN" \
  >> /media/heygo/program/datahub/nous/secrets/backend.env
```

**为什么不让 browser 直接共用 `backend.env`**（gateway 就是共用的）：`backend.env` 里有 `SUPABASE_SERVICE_ROLE_KEY`、`MEDIAHUB_TOKEN_ENCRYPTION_KEY`（解密所有账号 `session_state` 的主密钥）和带密码的 PG DSN。而 `nous-browser` 的全部工作就是驱动 Chromium 去加载抖音等外部站点。把主密钥注入这个进程，等于让一次渲染器逃逸把「单账号会话泄露」升级成「全库沦陷」，spec §7.6 的加密存储也就白做了。代价是这个 token 要在两个文件里各写一份；写歪了 browser 返回 401，是可见的类型化失败，比密钥过度暴露这种静默风险好诊断。

改完 env 文件必须 `docker compose up -d`（不是 `restart`）才会重读，见 CLAUDE.md「部署陷阱」。

## 首次搭建（P0 Task 3-4；tunnel 名 nous-gpu）

1. tunnel login（授权 nous.ink；打印 URL 在任意浏览器打开授权）：
   ```bash
   docker run -it --rm --user root \
     -v /media/heygo/program/datahub/nous/secrets/cloudflared:/root/.cloudflared \
     cloudflare/cloudflared:latest tunnel login
   ```
2. 创建 tunnel，记下 `<TUNNEL_ID>`：
   ```bash
   docker run -it --rm --user root \
     -v /media/heygo/program/datahub/nous/secrets/cloudflared:/root/.cloudflared \
     cloudflare/cloudflared:latest tunnel create nous-gpu
   ```
3. 凭证移交给常驻 nonroot 容器：
   ```bash
   sudo chown -R 65532:65532 /media/heygo/program/datahub/nous/secrets/cloudflared
   sudo chmod 600 /media/heygo/program/datahub/nous/secrets/cloudflared/cert.pem /media/heygo/program/datahub/nous/secrets/cloudflared/*.json
   ```
4. **Mac Mini 上**把 `cloudflared/config.yml` 的 tunnel ID 换成新 `<TUNNEL_ID>` → commit+push；服务器 `git pull`
5. DNS 路由（`--overwrite-dns` 兼容已有记录）：
   ```bash
   docker run -it --rm --user root \
     -v /media/heygo/program/datahub/nous/secrets/cloudflared:/root/.cloudflared \
     cloudflare/cloudflared:latest tunnel route dns --overwrite-dns nous-gpu api.nous.ink
   docker run -it --rm --user root \
     -v /media/heygo/program/datahub/nous/secrets/cloudflared:/root/.cloudflared \
     cloudflare/cloudflared:latest tunnel route dns --overwrite-dns nous-gpu sb.nous.ink
   ```
6. 起栈：`docker compose up -d`（在本目录；容器 nous-health + nous-cloudflared）
7. 验收（任意联网机器）：
   ```bash
   curl -sS https://api.nous.ink/healthz     # → ok
   curl -sS -o /dev/null -w "%{http_code}\n" https://sb.nous.ink/healthz   # → 200
   ```
   全程 443、证书有效、无 `:88`。

## ⚠️ cloudflared 挂载纪律（血泪预防）

- CLI 子命令（login/create/route dns）读写 `~/.cloudflared`：一次性容器统一 `--user root` + 挂 `/media/heygo/program/datahub/nous/secrets/cloudflared:/root/.cloudflared`。挂 `/etc/cloudflared` 会让 cert.pem 丢在容器里。
- 常驻 tunnel run：config 从仓库挂单文件，凭证从 secrets 目录挂 `/etc/cloudflared/creds`（均 `:ro`）。
- Linux bind-mount 下 uid 65532 写不进用户属主 755 目录（macOS Docker Desktop 验不出此坑）——所以一次性 CLI 用 root 写、事后 chown 65532。
- tunnel login 轮询 `login.cloudflareaccess.org` 国内偶发 EOF（unexpected EOF）：重试即可，授权 URL 一次性作废需用新的。

## ⚠️ DBOS 直连端口：必须 `nous-db:55434`，不是 5432（2026-07-25 血泪）

`DBOS_DATABASE_URL`（在 `secrets/backend.env`，仓库外）必须是：

```
postgresql://postgres:<pw>@nous-db:55434/postgres
```

**为什么不是 5432**：自托管 Supabase 的 `supabase/.env` 里 `POSTGRES_PORT=55434`（为与同机 `sb-dev` 共存避端口冲突），而 compose 把它同时喂给了容器内的 `PGPORT` —— 所以 **PG 进程本身只监听 55434，5432 上没有监听者**。

```
sb-dev   supavisor=55433  PG-direct=55434
sb-prod  supavisor=55435  PG-direct=55436   ← 本栈
```

**为什么 NAS 时代没这个问题**：NAS 上 backend 走**宿主机端口**（`127.0.0.1:5543x`），docker 的 ports 映射会做 `55436 → 55434` 转换，容器内监听哪个端口对调用方完全透明。迁到 gpupc 后 backend 进了同一个 `nous-net`，顺理成章改成容器名直连 —— 而**容器名直连绕过 ports 映射**，直接打容器 IP:端口，这时必须用真实监听端口。`5432` 这个数字在 NAS 时代是"宿主机映射口"，被当成"PG 标准口"照搬到容器名后面就断了。

**为什么只有 DBOS 踩这个坑**：其它服务走 Kong（`SUPABASE_URL=http://nous-kong:8000`）或 supavisor，只有 DBOS 依赖 LISTEN/NOTIFY——任何 pooler 都不支持——必须直连 PG，所以它是唯一必须硬编码这个非标准端口的地方。

**故障表现（静默 3 天）**：

| | |
|---|---|
| 起点 | 2026-07-22 14:36（迁移当天首次起栈） |
| 发现 | 2026-07-25 05:21，用户手点一次 transcribe 才暴露 |
| 直接症状 | `DBOS orchestrator is not enabled (DBOS_DATABASE_URL missing or init failed)` → **所有**走 DBOS 的功能 HTTP 500 |
| 真实影响面 | `app.startup.stall_detector` 7 天内 **1915 条 ERROR**（同一个 DSN），`_bg_reap_internal_queue` 每 2 分钟刷一次，DBOS scheduled workflow 全部停摆 |
| 为什么没人发现 | `docker ps` 一路显示 `healthy` —— healthcheck 只探 `/healthz`，**完全不覆盖 DBOS** |

**改动后必须重建容器**：`DBOS_DATABASE_URL` 在 `env_file` 里，`docker restart` 不会重读 —— 必须 `docker compose up -d backend worker`。（同 CLAUDE.md 里 Watchtower 那条教训：Watchtower 只拉新 image + 用容器已有 env 重启，不读 compose 也不重读 env_file。）

**验收口径**（别只看 `docker ps` 的 healthy）：

```bash
# worker 必须出现 "DBOS launched!" 且列出 5 个队列
docker logs nous-worker --since 3m 2>&1 | grep -E "DBOS launched|Initializing DBOS system database"
# backend(gateway) 必须是 constructed 而不是 construction failed
docker logs nous-backend --since 3m 2>&1 | grep -i dbos
# 引擎侧必须有新 workflow 在跑（scheduled 的 inbox/outbox_dispatch 每 5s 一轮）
docker exec nous-db psql -U postgres -p 55434 -d postgres -c \
  "SELECT status, count(*) FROM dbos.workflow_status
   WHERE created_at > (extract(epoch from now())*1000 - 300000) GROUP BY 1"
```

## 连接预算：`max_connections=200` + `idle_in_transaction_session_timeout=10min`（2026-08-21）

声明在 `deploy/gpu-server/supabase/docker-compose.yml` 的 `db.command`（`-c` 命令行参数优先级高于 `config_file` 指向的 `postgresql.conf`，不必改那个文件）。

### 为什么 100 不够

**自托管 Supabase 全家桶的固定底噪就有 60-70 条，那不是负载，是开机就有的。** 2026-08-21 实测 67/100：

| 来源 | 条数 | 说明 |
|---|---|---|
| `supabase_admin` | 41 | realtime 的订阅管理/RLS/cluster_node、`supavisor_meta` 5 条等，全是常驻 |
| `dbos_transact` | 7 | worker 的 DBOS sys + app 两个池（各 `DBOS_DB_POOL_SIZE`，默认 5，`max_overflow=0`） |
| `Supavisor` → PG | 6 | 服务端池自己连 PG 的那一层 |
| `PostgREST` (`authenticator`) | 5 | |
| `dbos_transact_client` | 1 | gateway 的 enqueue-only 句柄，SDK 写死 `pool_size=2` |

留给业务的余量只剩 30 出头。**部署窗里新旧连接池并存就直接撞顶——已实测到 106/100**，而那种状态最坏的一点是「连 psql 都进不去」：排查手段和服务一起没了。

200 是给底噪之上留出真正的业务余量，**不是为了跑满**。真正逼近 200 说明有泄漏，不是该继续加码的信号——所以同一批改动把连接压力接进了健康线和 admin 面板（见下）。

### 为什么加 `idle_in_transaction_session_timeout`

默认是 `0` = **永不超时**。事务开着不动的连接会一直占着槽位，而这个项目有前科：临时 DB 脚本 idle-in-transaction 把池饿死，表现是「所有业务请求超时但 `/readyz` 正常」。

10 分钟远长于任何正常事务（发布链的 advisory-lock 事务是分钟级，见 `backend/app/services/distribution/session_lock.py`，它自己就把这个代价写在注释里），却能兜住跑飞的脚本和断线后残留的事务。**踩到这个超时的是 bug，不是正常负载**——被它杀掉的连接应该去查调用方，而不是调大这个值。

### ⚠️ 改这两个参数不会自动生效

`deploy-gpu.yml` 的 paths 含 `deploy/gpu-server/**`，所以改 compose **会触发一次部署**——但那条链跑的是：

```
./up.sh --build backend worker gateway browser
```

`up.sh` 作用在 `deploy/gpu-server/docker-compose.yml`（compose 项目 `gpu-server`），而 **db 属于另一个 compose 项目 `mediahub-sb-prod`**（`deploy/gpu-server/supabase/docker-compose.yml`），压根不在清单里。db 刻意不进自动重启链——重启数据库不该由一次代码合并顺手触发。

所以合并后**必须人工执行**，挑业务空窗：

```bash
cd deploy/gpu-server/supabase
docker compose up -d db          # 必须 up -d：command 改动 docker restart 不重读
```

`max_connections` 不是可 reload 的参数（要重启 postmaster），`docker restart` 也不够——改的是 compose 的 `command`，只有 `up -d` 会用新参数重建容器。重启期间全栈（auth / rest / realtime / storage / DBOS）会短暂断连。

### 验收

```bash
# 1) 参数真的生效了（不是只改了文件）
docker exec nous-db psql -U postgres -p 55434 -d postgres -Atc \
  "SELECT current_setting('max_connections'), current_setting('idle_in_transaction_session_timeout');"
# 期望: 200|10min

# 2) 当前用量与余量
docker exec nous-db psql -U postgres -p 55434 -d postgres -c \
  "SELECT count(*) FILTER (WHERE backend_type = 'client backend') AS used,
          current_setting('max_connections')::int AS max,
          count(*) = count(backend_type) AS readings_trustworthy
   FROM pg_stat_activity;"
# 只数 client backend：PG 12 起 walsender / bgworker / autovacuum worker 各有
# 独立预算，不吃 max_connections。与后端 SLOT_BACKEND_TYPES 同一口径。
# readings_trustworthy=f 表示当前角色看不到别人的列(缺 pg_monitor)，此时 used
# 会塌到 1 左右 —— 是假读数，不是"很空闲"。

# 3) 全栈重新连上了（重启会断连，别只看 db 自己）
docker exec nous-worker curl -sS http://localhost:8080/api/v1/readyz
```

**内存代价可忽略**：同镜像实测 `shared_memory_size` 143MB（`max_connections=100`）→ 148MB（200），+5MB。真正的成本是每条连接的 backend 进程 RSS（约 5-10MB），只有在真跑满 200 时才是 1-2GB——这台机 123GB 内存，不构成约束。

### 连接压力的监控（同一批改动）

参数调大只是把悬崖往后挪，没人看就还会再撞。三处共用 `backend/app/services/infra/pg_connection_monitor.py` 的同一套阈值（80% WARNING / 95% ERROR），所以它们不可能对「多高算高」有分歧：

| 位置 | 行为 |
|---|---|
| `pg_connection_pressure_workflow`（每 5 分钟） | 采样 → 超阈值打 WARNING/ERROR 进 `application_logs` → 写 Redis 缓存 |
| `/api/v1/readyz` 的 `connections` 字段 | 读上面那个缓存（**不查库**）。⚠️ **纯信息，不门控** —— 见下 |
| Admin → Monitoring 的 Database Connections 面板 | 现查现算，带 application_name/usename/state 分组与最老 idle-in-transaction |

⚠️ **`/readyz` 里的 `connections` 永远不能参与判定**。readyz 返 503 会触发 `deploy-gpu.yml` 的自动回滚，回滚要重启容器，重启会开**更多**连接池——让告警的处置动作去喂养故障本身。这跟 `long_running` / `gates_readiness` 是同一族教训：值得上报 ≠ 值得判失败。

## 回滚

`docker compose down` + Cloudflare 删 `api`/`sb` 两条 CNAME + `tunnel delete nous-gpu`。NAS 全程未动，无需恢复。

## P1 衔接

- ingress `api.nous.ink` 改指 backend、`sb.nous.ink` 改指 Kong
- 持久卷落 `/media/heygo/program/datahub/nous/data/{postgres,redis,storage,runner}`；媒体**中转区**（`/app/downloads`：yt-dlp 落盘 / HLS 转码 / 缩略图暂存，上传 S3 后即删）自 2026-09-07 起在本机 NVMe `/media/heygo/cache/nous-cache`（ext4，带 `.mounted` marker，ACL 授 uid 1031），不再走 nas-B 的 CIFS；成品一律在 nas-B 的 SeaweedFS（S3）
- 部署机制换 self-hosted runner（epic spec §6 P1 与 §4.1 拓扑）
- backend→AI 走 localhost:8000（nous-center 网关原生跑在本机，零容器，互不干扰）
- 起栈脚本加 `.mounted` marker 检查（nofail 防护）
