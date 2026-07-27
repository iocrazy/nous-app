# P1 — backend + Supabase 上 GPU 机 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 GPU 服务器（10.0.0.10）跑起完整的 nous 后端栈（backend + worker + redis + 自托管 Supabase），数据从 NAS 迁入，媒体库迁到同网段 heytime NAS，`api.nous.ink`/`sb.nous.ink` ingress 从 health 占位切到真服务，部署链换 self-hosted runner。**NAS 旧栈全程不动**（回滚锚点 + 生产仍指向它，直到 P2 才切前端）。

**Architecture:** 全容器化 compose 栈，扩展 `deploy/gpu-server/docker-compose.yml`。持久卷 → `/media/heygo/program/nous/data/`（2T 盘）；密钥 → `nous/secrets/`；媒体 → heytime NAS CIFS 挂载（87MB/s 实测）。镜像本机 build（不过 ACR）。JWT secret 沿用 NAS prod（现有 token/key 继续有效）。

**Tech Stack:** Docker Compose · Supabase self-hosted 套件 · FastAPI backend（现有镜像 Dockerfile）· Redis · GitHub self-hosted runner · cloudflared（已就位）

## Global Constraints

- **NAS 零改动**：不 stop/不改 NAS 上任何容器与数据；P1 期间生产（Vercel 前端）继续打 NAS。
- **数据双跑窗口**：P1 的 dump/restore 是**演练性迁移**——NAS 侧数据会继续变化；**P2 切换日做最终重同步**（re-dump/restore 后立即切前端）。P1 不追求数据零漂移。
- **JWT/keys 与 NAS prod 完全一致**（`JWT_SECRET`/`ANON_KEY`/`SERVICE_ROLE_KEY` 原样复制），保证 P2 切换时已登录用户/前端 key 零失效。
- **命名全用 nous-***：容器 `nous-db`/`nous-kong`/`nous-backend`…；禁止新增 mediahub 字样。
- **密钥不进 git**：`secrets/supabase.env`、`secrets/backend.env` 住 `/media/heygo/program/nous/secrets/`；repo 只放 `*.env.example` 模板。
- **起栈防护**：任何 `compose up` 前必须确认 `/media/heygo/program/nous/.mounted` 存在（nofail 影子目录坑），封装进 `deploy/gpu-server/up.sh`。
- **sudo 步骤**（heytime 挂载点无需 sudo，CIFS 已 uid=1000）标注【用户】；其余全部远程执行。

## 已核实事实（2026-07-22 实测）

| 项 | 值 |
|---|---|
| prod PG 体量 | 5.6G（DBOS 历史 3.5G + 日志 1.6G + 业务 ~200M） |
| 媒体库 | `192.168.50.9:/volume2/sources/MediaHub.library` = **113G** |
| 网络 | GPU 机(192.168.8.2/10.0.0.10) ⇄ nous NAS 仅 ZeroTier(10.0.0.9) **7.6MB/s**；⇄ heytime NAS(192.168.8.9) 本地 LAN **87MB/s** |
| GPU 机已挂 | `/home/heygo/mnt/nas-videos`(NFS→10.0.0.9) · `/mnt/heytime/*`(CIFS→192.168.8.9, uid=1000 可写) |
| NAS Supabase 栈 | db/kong/auth/rest/realtime/storage/imgproxy/meta/studio/analytics/vector/pooler/edge-functions（13 容器） |
| NAS backend 栈 | backend+worker（同镜像，`MEDIAHUB_ROLE`）+ nginx + redis(7-alpine) |
| backend 挂载 | 媒体→`/app/downloads`；`.env`/`config.yml`；dreamina-auth 卷(12K) |

## 拍板（勿翻）

- D-P1-1：媒体迁 **heytime NAS**，默认路径 `//192.168.8.9/Sources/nous/media`（即 GPU 机 `/mnt/heytime/Sources/nous/media`；用户可改 share，改了同步改 backend 挂载）。
- D-P1-2：Supabase 只起**核心 8 件**：db/kong/auth/rest/realtime/storage/imgproxy/meta（+studio 可选开）。analytics/vector/pooler/edge-functions 不迁（当前未被业务依赖；后续需要再加）。
- D-P1-3：迁移前先做**保留清理**（DBOS 历史 30 天、日志 60 天），dump 预期 <1.5G。
- D-P1-4：镜像**本机 build**（runner 跑 `docker compose build`），彻底摆脱 ACR+Watchtower 链路；NAS 的 deploy-backend.yml 保留不动（P4 退役）。
- D-P1-5：P1 验收用**临时前端 build**（本地 `VITE_API_URL=https://api.nous.ink npm run build` + preview）；不动 Vercel 生产 env（那是 P2）。

---

## Task 1: 迁移前清理（NAS prod 库瘦身）

在 NAS prod DB 上执行保留策略（业务未上线，用户已知悉）：

- [ ] Step 1：清 DBOS 历史（30 天前）+ 日志（60 天前）：
```bash
ssh -i ~/.ssh/nas_deploy_key 192.168.50.9 "sudo /usr/local/bin/docker exec mediahub-sb-prod-db psql -U postgres -c \"
DELETE FROM dbos.operation_outputs WHERE workflow_uuid IN (SELECT workflow_uuid FROM dbos.workflow_status WHERE created_at < (EXTRACT(EPOCH FROM now()-INTERVAL '30 days')*1000)::bigint);
DELETE FROM dbos.workflow_status WHERE created_at < (EXTRACT(EPOCH FROM now()-INTERVAL '30 days')*1000)::bigint;
DELETE FROM application_logs WHERE logged_at < now()-INTERVAL '60 days';
DELETE FROM api_request_logs WHERE created_at < now()-INTERVAL '60 days';
VACUUM FULL dbos.workflow_status; VACUUM FULL dbos.operation_outputs; VACUUM FULL application_logs; VACUUM FULL api_request_logs;\""
```
（⚠️ `dbos.workflow_status.created_at` 是 epoch-ms bigint，非 timestamptz——执行前先 `\d dbos.workflow_status` 核对列类型再定 WHERE 写法。）
- [ ] Step 2：核体量降幅：`SELECT pg_size_pretty(pg_database_size('postgres'));` 预期 ≤2G。

## Task 2: Supabase 栈 IaC（repo 产物）

**Files:** `deploy/gpu-server/docker-compose.yml`（扩展）、`deploy/gpu-server/supabase/`（kong.yml 等配置）、`deploy/gpu-server/secrets-templates/{supabase,backend}.env.example`、`deploy/gpu-server/up.sh`

- [ ] Step 1：从 NAS 拉当前 prod 的 supabase compose + `.env`（只读参考）：`/volume1/docker/datahub/mediahub-sb-prod/`，提取 JWT_SECRET/ANON_KEY/SERVICE_ROLE_KEY/POSTGRES_PASSWORD 写入 GPU 机 `/media/heygo/program/nous/secrets/supabase.env`（**不进 git**）。
- [ ] Step 2：compose 增 8 服务（名字 nous-db/nous-kong/nous-auth/nous-rest/nous-realtime/nous-storage/nous-imgproxy/nous-meta），持久卷：
  - `nous-db` → `/media/heygo/program/nous/data/postgres:/var/lib/postgresql/data`
  - `nous-storage` → `nous/data/storage`
  - env 全部 `env_file: /media/heygo/program/nous/secrets/supabase.env`
- [ ] Step 3：`up.sh`：`[ -f /media/heygo/program/nous/.mounted ] || { echo "数据盘未挂载"; exit 1; }; docker compose up -d "$@"`
- [ ] Step 4：`docker compose config` 语法过 → commit（分支 `feature/p1-gpu-stack`，PR，auto-merge）。
- [ ] Step 5：GPU 机 pull + `./up.sh nous-db nous-kong …` 起 Supabase 8 件 → 验：`docker ps` 全 Up；`curl localhost:8000/auth/v1/health`（kong 内网口）。

## Task 3: 数据迁移（演练轮）

- [ ] Step 1：NAS dump：`pg_dump -U postgres -Fc -f /tmp/nous-p1.dump postgres`（容器内跑，docker cp 出）。
- [ ] Step 2：传输：NAS→GPU 走 ZeroTier scp（~2G 清理后 ≈5min）。
- [ ] Step 3：GPU restore：`pg_restore -U postgres -d postgres --clean --if-exists /tmp/nous-p1.dump`。
- [ ] Step 4：验收：关键表行数对比 NAS vs GPU（`parsed_media`/`resources`/`teams`/`task_tracking`/`tags` 五表 count 一致）；`information_schema` 表数一致。

## Task 4: 媒体迁移（113G，后台跑）

- [ ] Step 1：`mkdir -p /mnt/heytime/Sources/nous/media`
- [ ] Step 2：GPU 机后台 rsync（两侧都已挂载，瓶颈 ZeroTier 7.6MB/s ≈ 4.5h）：
```bash
nohup rsync -a --info=progress2 /home/heygo/mnt/nas-videos/../MediaHub.library/ /mnt/heytime/Sources/nous/media/ > /tmp/media-rsync.log 2>&1 &
```
（⚠️ 源路径按 NFS 实挂点修正——现挂的是 `/video/Tutorial.Library`，需在 nous NAS 侧把 `/volume2/sources` 也 NFS 导出或改走 rsync-over-ssh `-e "ssh -i …" heygo@10.0.0.9:/volume2/sources/MediaHub.library/`。执行时二选一。）
- [ ] Step 3：完成后校验：两侧 `du -s` 差 <1%；抽样 5 文件 md5 一致。

## Task 5: backend + worker + redis 上机

**Files:** compose 增 `nous-backend`/`nous-worker`/`nous-redis`；`secrets/backend.env`

- [ ] Step 1：`backend.env` 基于 NAS `/volume1/docker/mediahub/docker/.env` 改四类值：
  - `SUPABASE_URL=http://nous-kong:8000`（栈内直连）；keys 同 NAS
  - AI 端点 → `http://host.docker.internal:8000`（nous-center 原生进程；compose 加 `extra_hosts: host-gateway`）
  - `REDIS_URL=redis://nous-redis:6379`
  - `CORS_ORIGINS` 加 `https://app.nous.ink`（为 P2/P3 预热）
- [ ] Step 2：compose：`nous-backend`/`nous-worker` 用 `build: ../../backend`（本机 build）；媒体挂载 `/mnt/heytime/Sources/nous/media:/app/downloads`；`MEDIAHUB_ROLE` 分 gateway/worker。
- [ ] Step 3：起三件 → 验：`curl localhost:<port>/healthz` = 200；backend 日志无 DB/Redis/AI 连接错误；DBOS 启动 recovery 正常。

## Task 6: Tunnel ingress 切真服务

- [ ] Step 1：`cloudflared/config.yml`：`api.nous.ink → http://nous-backend:8080`、`sb.nous.ink → http://nous-kong:8000`；health 容器改只挂 `health.nous.ink` 或直接移除。commit+pull+`docker compose up -d cloudflared`。
- [ ] Step 2：验收：`curl https://api.nous.ink/healthz` 打到真 backend；`curl https://sb.nous.ink/auth/v1/health` = 200。

## Task 7: ⚠️ Realtime/WS 经 tunnel 必验（epic R1，一票否决项）

- [ ] Step 1：`wscat -c "wss://sb.nous.ink/realtime/v1/websocket?apikey=<ANON_KEY>&vsn=1.0.0"` 握手成功。
- [ ] Step 2：真实订阅验证：临时前端连 `sb.nous.ink`，Task Center 触发一次任务，确认 `task_tracking` 变更实时推到（不是轮询兜底）。
- [ ] 若不通：排查顺序 = kong websocket upgrade 配置 → cloudflared（默认支持 WS，无需开关）→ realtime 容器 `WS_...` env。**此项不过 = P2 冻结**。

## Task 8: self-hosted runner + 部署链

- [ ] Step 1：GitHub repo Settings→Actions→Runners 加 self-hosted runner（Linux x64），装到 `/media/heygo/program/nous/data/runner/`，`./config.sh --unattended` + `svc.sh install`（systemd 常驻,label `gpu`）。
- [ ] Step 2：新 workflow `.github/workflows/deploy-gpu.yml`：`on: push(master, paths: backend/** deploy/gpu-server/**)` → `runs-on: [self-hosted, gpu]` → `git pull && ./up.sh --build nous-backend nous-worker` → healthz 冒烟。
- [ ] Step 3：验收：推一个 backend 空改动 commit → runner 数十秒内完成本机 build+重启 → healthz 通过。（NAS deploy-backend.yml 不动，双轨到 P4。）

## Task 9: 端到端验收（P1 完成线）

临时前端 build 指向新栈（D-P1-5），真机走查：
- [ ] 登录（密码登录，走 sb.nous.ink auth）
- [ ] 资源列表/详情/**媒体播放**（读 heytime 挂载）
- [ ] 发起一次解析下载 → 文件落 heytime；Task Center **实时**进度（Realtime）
- [ ] AI 调用一次（走 localhost nous-center）
- [ ] `docker compose ps` 全栈健康；`data/postgres` 落在 2T 盘（`du` 核）

**回滚点**：全程 NAS 未动——生产前端始终指 NAS；GPU 栈任何问题 = `compose down`，无生产影响。

## 开放项（进 P2 前）

- P2 切换日：最终 re-dump/restore + Vercel env 切 `api/sb.nous.ink` + 老域名兼容期
- heytime share 若不想放 `Sources` 下，用户点名新 share（改一处挂载+rsync 目标）
- nginx 媒体直出/HLS（NAS 现有 8081 能力）→ 并入 P3 或百万文件 P3 runbook 时机
