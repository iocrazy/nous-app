# P0 — GPU 服务器骨架 + Cloudflare Tunnel 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 GPU 服务器（NAT 后、无公网）建立一个 Cloudflare Tunnel，把 `api.nous.ink` / `sb.nous.ink` 经干净 443 打通到机上一个 health 容器；产出一套可提交的 IaC 骨架（compose + cloudflared ingress + health + runbook），为 P1 迁 backend/Supabase 铺地基。

**Architecture:** cloudflared 以 Docker 容器跑**本地管理式（locally-managed）** tunnel，ingress 规则写在仓库 `deploy/gpu-server/cloudflared/config.yml`（IaC，避免 dashboard 漂移）；tunnel 凭证（cert.pem + `<id>.json`）住**仓库树外** `/opt/nous/secrets/cloudflared/`（物理隔离于 git，`.gitignore` 仅作保险带）。P0 阶段 ingress 先全部指向一个 `nginx:alpine` health 容器，P1 再把 hostname 重映射到 backend/Kong。
**⚠️ 镜像事实（review 修正）**：官方 `cloudflare/cloudflared` 镜像以 nonroot(uid 65532) 运行，CLI 子命令（login/create/route）默认读写 `~/.cloudflared`；本计划的一次性 CLI 容器统一用 `--user root` + 把 secrets 目录挂到 `/root/.cloudflared`，规避 Linux bind-mount 写权限坑（macOS Docker Desktop 验不出此坑，Linux 真机会爆）。

**Tech Stack:** Docker Compose · `cloudflare/cloudflared` 容器 · `nginx:alpine` · Cloudflare Zero Trust Tunnel · Cloudflare DNS（zone `nous.ink`）

## Global Constraints

- **纯增量、零触碰 NAS**：P0 不改动 NAS 上任何在跑的服务；NAS 仍为唯一权威。回滚 = 删 tunnel + DNS 记录。
- **无 `:88`**：所有对外端点走 Cloudflare 443 + 自动证书；计划里任何 URL 不得出现 `:88`。
- **密钥不进仓库树**：tunnel 凭证 `cert.pem`、`<TUNNEL_ID>.json` 住服务器 `/opt/nous/secrets/cloudflared/`（仓库树外，物理不可能被 commit）；仅 `config.yml`（含非敏感 tunnel ID）+ compose + nginx.conf 进仓库；`cloudflared/.gitignore` 仅作有人误放文件时的保险带。
- **执行分工**：repo 产物（compose/config/nginx/runbook）由本计划提交；标注 `【在 GPU 机执行】` / `【在 Cloudflare 执行】` 的命令由用户在对应环境运行。
- **一切在 Docker 内**：不往 GPU 机宿主装 cloudflared 二进制，用官方容器完成 login/create/run。
- **仓库新目录**：所有 P0 产物落在 `deploy/gpu-server/`，不碰现有 `docker/`、`backend/`、`frontend/`。

---

## File Structure

```
deploy/gpu-server/
├── docker-compose.yml          # health + cloudflared 两个 service
├── cloudflared/
│   ├── config.yml              # tunnel ingress 规则（提交，含非敏感 tunnel ID）
│   └── .gitignore              # 保险带：万一有人误放凭证进此目录也不会被提交
（服务器本地，不在仓库树内）
/opt/nous/secrets/cloudflared/   # cert.pem + <TUNNEL_ID>.json 凭证常驻处
├── health/
│   └── nginx.conf              # /healthz → 200 "ok"，其余 → 404
└── README.md                   # P0 runbook（recon + tunnel 创建 + 验证 + 回滚）
```

- `docker-compose.yml`：声明 health（nginx:alpine 挂 nginx.conf）与 cloudflared（挂 `./cloudflared`，跑 `tunnel run`）两个 service，职责单一。
- `cloudflared/config.yml`：ingress 唯一真相；hostname→本地 service 映射。
- `health/nginx.conf`：最小 health 端点，P1 前的占位源站。
- `README.md`：把需在服务器/Cloudflare 手动跑的步骤固化成可照抄 runbook。

---

## Task 1: 服务器 recon + 前置校验

**Files:** 无（纯信息收集，结果记入 Task 5 的 README「Recon 记录」小节）

**Interfaces:**
- Produces: 确认 `docker` / `docker compose` 可用、宿主 OS、现有 AI 容器清单、出站网络可达 Cloudflare —— 后续 Task 依赖这些前置成立。

- [ ] **Step 1：【在 GPU 机执行】采集环境事实**

Run:
```bash
uname -a
docker --version
docker compose version
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
```
Expected: 打印内核/发行版；`Docker version 20.10+`；`Docker Compose version v2.x`；列出现有 AI 容器（确认 backend/Supabase 未占用，后续 P1 再加）。
若 `docker compose` 不存在 → 装 Docker Engine + compose plugin 后再继续（记入 README 前置条件）。

- [ ] **Step 2：【在 GPU 机执行】确认出站可达 Cloudflare（tunnel 走出站，无需公网入站）**

Run:
```bash
curl -sS -o /dev/null -w "%{http_code}\n" https://api.cloudflare.com/client/v4/
docker run --rm cloudflare/cloudflared:latest --version
```
Expected: 第一条返回 HTTP 状态码（`400`/`401` 皆可，证明能出站到 Cloudflare）；第二条打印 `cloudflared version 2024.x` 证明能拉到官方镜像。

- [ ] **Step 3：确认 Cloudflare 侧 zone 就绪**

【在 Cloudflare 执行】在 Cloudflare Dashboard 确认 `nous.ink` zone 已激活（Status: Active），且你有 Zero Trust 权限。
Expected: zone active；能进 Zero Trust → Networks → Tunnels 页面。

---

## Task 2: 仓库 IaC 骨架（health 容器 + cloudflared service + ingress 占位）

**Files:**
- Create: `deploy/gpu-server/health/nginx.conf`
- Create: `deploy/gpu-server/docker-compose.yml`
- Create: `deploy/gpu-server/cloudflared/config.yml`
- Create: `deploy/gpu-server/cloudflared/.gitignore`

**Interfaces:**
- Produces：`health` service 暴露容器内 `:80/healthz`→200；`cloudflared` service 读 `/etc/cloudflared/config.yml` 跑 tunnel。`config.yml` 的 `tunnel:` / `credentials-file:` 字段在 Task 3 用真实 tunnel ID 填入。

- [ ] **Step 1：写 health 容器的 nginx 配置**

Create `deploy/gpu-server/health/nginx.conf`:
```nginx
server {
    listen 80;
    server_name _;

    location = /healthz {
        add_header Content-Type text/plain;
        return 200 "ok\n";
    }

    location / {
        return 404;
    }
}
```

- [ ] **Step 2：写凭证 gitignore（先于 config，确保后续放入的凭证不会被误提交）**

Create `deploy/gpu-server/cloudflared/.gitignore`:
```gitignore
# tunnel 凭证严禁进 git
cert.pem
*.json
```

- [ ] **Step 3：写 ingress config（tunnel ID 先留占位，Task 3 回填）**

Create `deploy/gpu-server/cloudflared/config.yml`:
```yaml
# Locally-managed tunnel ingress —— 唯一真相在此文件（非 dashboard）
# tunnel / credentials-file 由 Task 3 用真实 TUNNEL_ID 回填
# 凭证住服务器 /opt/nous/secrets/cloudflared/，compose 挂到容器 /etc/cloudflared/creds/
tunnel: REPLACE_WITH_TUNNEL_ID
credentials-file: /etc/cloudflared/creds/REPLACE_WITH_TUNNEL_ID.json

ingress:
  # P0：两个 hostname 先全部指向 health 占位源站
  # P1：api.nous.ink 改指 backend、sb.nous.ink 改指 Kong
  - hostname: api.nous.ink
    service: http://health:80
  - hostname: sb.nous.ink
    service: http://health:80
  # 兜底：未匹配 hostname 返回 404
  - service: http_status:404
```

- [ ] **Step 4：写 docker-compose 骨架**

Create `deploy/gpu-server/docker-compose.yml`:
```yaml
# GPU 服务器 P0 骨架：health 占位源站 + cloudflared tunnel
# 启动前置：/opt/nous/secrets/cloudflared/ 内已有 cert.pem 与 <TUNNEL_ID>.json（Task 3 产生）
services:
  health:
    image: nginx:alpine
    container_name: nous-health
    volumes:
      - ./health/nginx.conf:/etc/nginx/conf.d/default.conf:ro
    restart: unless-stopped

  cloudflared:
    image: cloudflare/cloudflared:latest
    container_name: nous-cloudflared
    command: tunnel --config /etc/cloudflared/config.yml run
    volumes:
      # 配置来自仓库（IaC），凭证来自仓库树外的服务器本地 secrets 目录
      - ./cloudflared/config.yml:/etc/cloudflared/config.yml:ro
      - /opt/nous/secrets/cloudflared:/etc/cloudflared/creds:ro
    restart: unless-stopped
    depends_on:
      - health
```

- [ ] **Step 5：本地校验 compose/nginx 语法（在 Mac Mini 提交前跑）**

Run:
```bash
docker compose -f deploy/gpu-server/docker-compose.yml config
```
Expected: 打印解析后的 compose（无 YAML 错）。此步只验语法，不启动（凭证尚未生成）。

- [ ] **Step 6：Commit**

```bash
git add deploy/gpu-server/health/nginx.conf \
        deploy/gpu-server/docker-compose.yml \
        deploy/gpu-server/cloudflared/config.yml \
        deploy/gpu-server/cloudflared/.gitignore
git commit -m "feat(deploy): P0 GPU 服务器 IaC 骨架 — health 容器 + cloudflared ingress"
```

---

## Task 3: 创建 Cloudflare Tunnel + 凭证 + 回填 config

**Files:**
- Modify: `deploy/gpu-server/cloudflared/config.yml`（回填 `tunnel` / `credentials-file` 的真实 ID，**在 Mac Mini 上改 + commit，服务器 pull**——不在服务器上手改仓库文件，避免两份拷贝漂移）
- 服务器本地（仓库树外）：`/opt/nous/secrets/cloudflared/cert.pem`、`/opt/nous/secrets/cloudflared/<TUNNEL_ID>.json`

**⚠️ 挂载路径纪律（review 修正 F1/F2）**：cloudflared 的 CLI 子命令（login/create/route dns）读写 `~/.cloudflared`，官方镜像默认 nonroot(65532) 时是 `/home/nonroot/.cloudflared`——挂到 `/etc/cloudflared` 会导致 cert.pem 写进未挂载路径、容器退出即丢。且 Linux 上 uid 65532 写不进属主为你的 755 目录。**统一解法：一次性 CLI 容器全部 `--user root` + 挂 secrets 目录到 `/root/.cloudflared`**；最后把凭证 chown 给 65532 供常驻 nonroot 容器读。

**Interfaces:**
- Consumes: Task 2 的 `config.yml` 占位。
- Produces: 一个名为 `nous-gpu` 的 tunnel + 其凭证；`config.yml` 内含真实 tunnel ID。

- [ ] **Step 1：【在 GPU 机执行】将仓库该目录同步到服务器**

Run（服务器上 clone 或 pull 本仓库后进入目录）:
```bash
cd <repo>/deploy/gpu-server
```
Expected: 目录含 Task 2 提交的 compose/config/nginx。
（说明：这是 P1 self-hosted runner 之前的一次性手动 pull；符合 §4.1「服务器经 GitHub 自己 pull」，非 scp 源码。）

- [ ] **Step 2：【在 GPU 机执行】建 secrets 目录 + tunnel login（授权 zone nous.ink）**

Run:
```bash
sudo mkdir -p /opt/nous/secrets/cloudflared
docker run -it --rm --user root \
  -v /opt/nous/secrets/cloudflared:/root/.cloudflared \
  cloudflare/cloudflared:latest tunnel login
```
Expected: 终端打印一个授权 URL → 在任意浏览器（Mac 上即可）打开、选择 `nous.ink` zone 授权 → 容器把 `cert.pem` 写入 `/opt/nous/secrets/cloudflared/`。
Verify: `sudo ls -l /opt/nous/secrets/cloudflared/cert.pem` 存在。

- [ ] **Step 3：【在 GPU 机执行】创建 tunnel 并记录 TUNNEL_ID**

Run:
```bash
docker run -it --rm --user root \
  -v /opt/nous/secrets/cloudflared:/root/.cloudflared \
  cloudflare/cloudflared:latest tunnel create nous-gpu
```
Expected: 打印 `Created tunnel nous-gpu with id <TUNNEL_ID>`，并在 `/opt/nous/secrets/cloudflared/` 生成 `<TUNNEL_ID>.json`。
Verify: `sudo ls /opt/nous/secrets/cloudflared/*.json` 存在；记下 `<TUNNEL_ID>`。

- [ ] **Step 4：【在 GPU 机执行】凭证 chown 给 nonroot 常驻容器**

常驻 `cloudflared` service 以 nonroot(65532) 跑、经 `:ro` 读凭证，而凭证由 root 容器创建（root-owned 0600），必须移交属主：

Run:
```bash
sudo chown -R 65532:65532 /opt/nous/secrets/cloudflared
sudo chmod 600 /opt/nous/secrets/cloudflared/cert.pem /opt/nous/secrets/cloudflared/*.json
```
Verify: `sudo ls -ln /opt/nous/secrets/cloudflared/` 显示属主 uid `65532`、权限 `600`。

- [ ] **Step 5：【在 Mac Mini 执行】回填 config.yml 的真实 tunnel ID 并 commit，服务器 pull**

把 `deploy/gpu-server/cloudflared/config.yml` 里两处 `REPLACE_WITH_TUNNEL_ID` 换成真实 `<TUNNEL_ID>`（tunnel ID 非敏感）：
```yaml
tunnel: <TUNNEL_ID>
credentials-file: /etc/cloudflared/creds/<TUNNEL_ID>.json
```

Run（Mac Mini）:
```bash
git add deploy/gpu-server/cloudflared/config.yml
git commit -m "feat(deploy): 回填 nous-gpu tunnel ID 到 ingress config"
git push
```
Run（GPU 机）: `git pull`
Verify: `! grep -q REPLACE_WITH_TUNNEL_ID deploy/gpu-server/cloudflared/config.yml && echo OK` 输出 `OK`（两台机各跑一遍）。

---

## Task 4: DNS 路由 + 起栈 + 经 tunnel 验证 health

**Files:** 无（DNS 在 Cloudflare 侧生成，服务在服务器起）

**Interfaces:**
- Consumes: Task 3 的 tunnel + 凭证 + 回填后的 config；Task 2 的 compose。
- Produces: `api.nous.ink` / `sb.nous.ink` 经 tunnel 可达 health（P0 验收点）。

- [ ] **Step 1：【在 GPU 机执行】为两个 hostname 建 DNS 路由（自动生成 CNAME→tunnel）**

Run（`route dns` 需读 cert.pem，沿用 Task 3 的 `--user root` + secrets 挂载；凭证已 chown 65532 但 root 不受权限限制，可正常读）:
```bash
docker run -it --rm --user root \
  -v /opt/nous/secrets/cloudflared:/root/.cloudflared \
  cloudflare/cloudflared:latest tunnel route dns nous-gpu api.nous.ink
docker run -it --rm --user root \
  -v /opt/nous/secrets/cloudflared:/root/.cloudflared \
  cloudflare/cloudflared:latest tunnel route dns nous-gpu sb.nous.ink
```
Expected: 各打印 `Added CNAME api.nous.ink / sb.nous.ink which will route to this tunnel`。
Verify:【在 Cloudflare 执行】DNS 页出现 `api` / `sb` 两条 CNAME → `<TUNNEL_ID>.cfargotunnel.com`（橙云代理）。

- [ ] **Step 2：【在 GPU 机执行】起栈**

Run:
```bash
docker compose -f deploy/gpu-server/docker-compose.yml up -d
docker compose -f deploy/gpu-server/docker-compose.yml ps
```
Expected: `nous-health` 与 `nous-cloudflared` 均 `Up`。

- [ ] **Step 3：【在 GPU 机执行】看 cloudflared 是否已注册连接**

Run:
```bash
docker logs nous-cloudflared 2>&1 | grep -iE "Registered tunnel connection|Connection .* registered" | head
```
Expected: 出现 `Registered tunnel connection`（通常 4 条到不同 Cloudflare 边缘）。

- [ ] **Step 4：P0 验收 —— 从任意联网机器经 tunnel 443 打 health**

Run（Mac Mini 或任意外网机）:
```bash
curl -sS https://api.nous.ink/healthz
curl -sS -o /dev/null -w "%{http_code}\n" https://api.nous.ink/healthz
curl -sS -o /dev/null -w "%{http_code}\n" https://sb.nous.ink/healthz
curl -sSI https://api.nous.ink/healthz | grep -i "^server:"
```
Expected:
- 第一条输出 `ok`
- 第二、三条输出 `200`
- URL 全程无 `:88`，走 443，TLS 证书有效（curl 无 `-k` 也成功）
- `server:` 头含 `cloudflare`（证明经 Cloudflare 边缘）

- [ ] **Step 5：回滚演练确认（不实际回滚，仅记录命令到 README）**

回滚 = `docker compose -f deploy/gpu-server/docker-compose.yml down` +【Cloudflare】删 `api`/`sb` 两条 CNAME + 删 tunnel（`tunnel delete nous-gpu`）。NAS 全程未动，无需恢复。

---

## Task 5: P0 Runbook 文档 + 收尾提交

**Files:**
- Create: `deploy/gpu-server/README.md`

**Interfaces:**
- Consumes: Task 1-4 的全部命令与验收点。
- Produces: 可照抄的 P0 runbook；含 Recon 记录、前置条件、创建步骤、验收、回滚、P1 衔接说明。

- [ ] **Step 1：写 runbook**

Create `deploy/gpu-server/README.md`，内容至少覆盖：
```markdown
# GPU 服务器部署（P0：骨架 + Cloudflare Tunnel）

## Recon 记录（Task 1 实测填入）
- OS: <uname -a 结果>
- Docker: <版本> / Compose: <版本>
- 现有 AI 容器: <docker ps 摘要>

## 前置条件
- Docker Engine 20.10+ 与 compose v2 plugin
- 能出站访问 Cloudflare（无需公网入站）
- Cloudflare zone `nous.ink` active + Zero Trust 权限

## 首次搭建（照抄 Task 3-4 命令）
1. `sudo mkdir -p /opt/nous/secrets/cloudflared`
2. tunnel login（`--user root`，挂 secrets 目录到 `/root/.cloudflared`，授权 nous.ink）
3. tunnel create nous-gpu → 记 TUNNEL_ID
4. `sudo chown -R 65532:65532` secrets 目录 + `chmod 600` 凭证（常驻容器是 nonroot）
5. Mac Mini 回填 config.yml 的 TUNNEL_ID 并 commit+push，服务器 git pull
6. tunnel route dns：api.nous.ink / sb.nous.ink（同 --user root 挂载）
7. docker compose up -d
8. 验收：curl https://api.nous.ink/healthz → ok（200，无 :88）

## ⚠️ cloudflared 挂载纪律（血泪预防）
- CLI 子命令（login/create/route dns）读写 `~/.cloudflared`：一次性容器统一 `--user root` + 挂 `/opt/nous/secrets/cloudflared:/root/.cloudflared`。挂 `/etc/cloudflared` 会让 cert.pem 丢在容器里。
- 常驻 tunnel run：config 从仓库挂单文件，凭证从 secrets 目录挂 `/etc/cloudflared/creds`（均 :ro）。

## 回滚
docker compose down + 删两条 CNAME + tunnel delete nous-gpu；NAS 未动。

## P1 衔接
- ingress `api.nous.ink` 改指 backend、`sb.nous.ink` 改指 Kong
- 部署机制换 self-hosted runner（见 epic spec §6 P1 与 §4.1 拓扑）
- 凭证常驻 /opt/nous/secrets/cloudflared/（仓库树外），物理不可能进 git
```

- [ ] **Step 2：Commit**

```bash
git add deploy/gpu-server/README.md
git commit -m "docs(deploy): P0 GPU 服务器 + Cloudflare Tunnel runbook"
```

---

## Self-Review

- **Spec coverage（对 epic §6 P0）**：目标"compose 骨架 + Tunnel + health 经 tunnel 可达"→ Task 2（骨架）/Task 3（tunnel）/Task 4（DNS+验收）；验证"curl api.nous.ink/healthz 200、无 :88、证书有效"→ Task 4 Step 4；回滚"纯增量 NAS 不动"→ Global Constraints + Task 4 Step 5。全覆盖。
- **Placeholder scan**：`config.yml` 的 `REPLACE_WITH_TUNNEL_ID` 是**有意占位**，Task 3 Step 5 显式回填并以 `! grep -q … && echo OK` 验证；非计划漏洞。无 TBD/TODO。
- **Type/命名一致**：service 名 `health` / `cloudflared`、容器名 `nous-health` / `nous-cloudflared`、tunnel 名 `nous-gpu`、hostname `api.nous.ink` / `sb.nous.ink`、secrets 路径 `/opt/nous/secrets/cloudflared`（容器内 `/etc/cloudflared/creds`）全计划一致。
- **约束一致**：全文无 `:88`；凭证住仓库树外（`.gitignore` 仅保险带）；NAS 零触碰。

### Review 修正记录（2026-07-20 对抗性复审）
- **F1(blocker)**：CLI 子命令挂载 `/etc/cloudflared` → cert.pem 实际写 `~/.cloudflared`（官方镜像 nonroot 时为 `/home/nonroot/.cloudflared`），容器退出即丢。修正：一次性 CLI 容器 `--user root` + 挂 `/root/.cloudflared`。
- **F2**：Linux bind-mount 下 uid 65532 写不进属主为用户的 755 目录（macOS Docker Desktop 验不出，真机会爆）。修正：同 F1（root 写）+ 事后 chown 65532 供常驻 nonroot 容器读。
- **F3(设计)**：凭证从"仓库树内靠 .gitignore"改为"仓库树外 /opt/nous/secrets/"，物理隔离；compose 分挂 config（repo）与 creds（本地）。
- **F4(流程)**：回填 tunnel ID 改为 Mac Mini 改+commit → 服务器 pull，消除双拷贝漂移。
- **F5(小)**：verify 从 `grep -c` 改 `! grep -q`（匹配为零时 grep -c exit 1，会误导链式执行）。

## 开放依赖（不阻塞 P0，进 P1 前需用户答）

- GPU 机现有服务资源占用（backend+Supabase 余量）——影响 P1 编排。
- Supabase 数据量 + Storage 卷大小——影响 P1 迁移窗口。
- 媒体是否经 tunnel 出——影响 P1/P4 的 R6 决策。
