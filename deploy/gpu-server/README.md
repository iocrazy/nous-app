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
/media/heygo/program/nous/          # 2T NVMe，非系统盘
├── .mounted                        # nofail 挂载防护 marker（起栈前检查）
├── data/                           # P1 起的持久卷：postgres / redis / storage / runner
└── secrets/
    └── cloudflared/                # cert.pem + <TUNNEL_ID>.json（owner 65532，mode 600）
```

**重装系统恢复路径**：装 Docker（下方前置条件）→ 恢复 fstab 数据盘条目 → clone 本仓库 → `docker compose up -d`。数据与密钥全程在 2T 盘上不动。

⚠️ 数据盘是 `nofail` 挂载：万一开机未挂上，bind mount 会在系统盘上生出影子目录。P1 的起栈脚本必须先检查 `/media/heygo/program/nous/.mounted` 存在才允许 `compose up`。

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
mkdir -p /media/heygo/program/nous/secrets /media/heygo/program/nous/data
touch /media/heygo/program/nous/.mounted
```

其余前置：Cloudflare zone `nous.ink` active + Zero Trust 权限。

## 首次搭建（P0 Task 3-4；tunnel 名 nous-gpu）

1. tunnel login（授权 nous.ink；打印 URL 在任意浏览器打开授权）：
   ```bash
   docker run -it --rm --user root \
     -v /media/heygo/program/nous/secrets/cloudflared:/root/.cloudflared \
     cloudflare/cloudflared:latest tunnel login
   ```
2. 创建 tunnel，记下 `<TUNNEL_ID>`：
   ```bash
   docker run -it --rm --user root \
     -v /media/heygo/program/nous/secrets/cloudflared:/root/.cloudflared \
     cloudflare/cloudflared:latest tunnel create nous-gpu
   ```
3. 凭证移交给常驻 nonroot 容器：
   ```bash
   sudo chown -R 65532:65532 /media/heygo/program/nous/secrets/cloudflared
   sudo chmod 600 /media/heygo/program/nous/secrets/cloudflared/cert.pem /media/heygo/program/nous/secrets/cloudflared/*.json
   ```
4. **Mac Mini 上**把 `cloudflared/config.yml` 的 tunnel ID 换成新 `<TUNNEL_ID>` → commit+push；服务器 `git pull`
5. DNS 路由（`--overwrite-dns` 兼容已有记录）：
   ```bash
   docker run -it --rm --user root \
     -v /media/heygo/program/nous/secrets/cloudflared:/root/.cloudflared \
     cloudflare/cloudflared:latest tunnel route dns --overwrite-dns nous-gpu api.nous.ink
   docker run -it --rm --user root \
     -v /media/heygo/program/nous/secrets/cloudflared:/root/.cloudflared \
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

- CLI 子命令（login/create/route dns）读写 `~/.cloudflared`：一次性容器统一 `--user root` + 挂 `/media/heygo/program/nous/secrets/cloudflared:/root/.cloudflared`。挂 `/etc/cloudflared` 会让 cert.pem 丢在容器里。
- 常驻 tunnel run：config 从仓库挂单文件，凭证从 secrets 目录挂 `/etc/cloudflared/creds`（均 `:ro`）。
- Linux bind-mount 下 uid 65532 写不进用户属主 755 目录（macOS Docker Desktop 验不出此坑）——所以一次性 CLI 用 root 写、事后 chown 65532。
- tunnel login 轮询 `login.cloudflareaccess.org` 国内偶发 EOF（unexpected EOF）：重试即可，授权 URL 一次性作废需用新的。

## 回滚

`docker compose down` + Cloudflare 删 `api`/`sb` 两条 CNAME + `tunnel delete nous-gpu`。NAS 全程未动，无需恢复。

## P1 衔接

- ingress `api.nous.ink` 改指 backend、`sb.nous.ink` 改指 Kong
- 持久卷落 `/media/heygo/program/nous/data/{postgres,redis,storage,runner}`；媒体根 = NAS 挂载点（机上已挂 `192.168.8.9` heytime CIFS 与 `10.0.0.9` NFS，share 待点名）
- 部署机制换 self-hosted runner（epic spec §6 P1 与 §4.1 拓扑）
- backend→AI 走 localhost:8000（nous-center 网关原生跑在本机，零容器，互不干扰）
- 起栈脚本加 `.mounted` marker 检查（nofail 防护）
