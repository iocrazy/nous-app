# GPU 服务器部署（P0：骨架 + Cloudflare Tunnel）

Epic spec：`docs/superpowers/specs/2026-07-20-nous-ink-infra-migration-epic.md`
P0 计划：`docs/superpowers/plans/2026-07-20-p0-gpu-server-cloudflare-tunnel.md`

## Recon 记录（2026-07-21 实测，Task 1）

- 机器：`10.0.0.10`（ZeroTier；Ubuntu，主机名 heygo-Ubuntu）
- 硬件：48 核 / 123G RAM / 根盘 937G（空闲 782G）/ RTX Pro 6000 Blackwell 96G + 双 RTX 3090
- 出站 Cloudflare：✅（`api.cloudflare.com` 可达）
- **Docker：初始未安装**（非 PATH 问题）→ 按下方前置条件安装
- AI 栈跑法：**原生 venv、零容器**——`:8000` 为 nous-center uvicorn 网关 + vLLM（`/media/heygo/program/projects-code/repos/nous-center/`）。装 Docker 不影响任何现有 AI 服务
- sudo：需交互密码（远程 BatchMode 无法 sudo，需要 sudo 的步骤由用户在机上执行）

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
sudo mkdir -p /opt/mediahub/secrets/cloudflared
```

其余前置：Cloudflare zone `nous.ink` active + Zero Trust 权限。

## 首次搭建（P0 Task 3-4）

1. `sudo mkdir -p /opt/mediahub/secrets/cloudflared`（前置条件已含）
2. tunnel login（授权 nous.ink；打印 URL 在任意浏览器打开授权）：
   ```bash
   docker run -it --rm --user root \
     -v /opt/mediahub/secrets/cloudflared:/root/.cloudflared \
     cloudflare/cloudflared:latest tunnel login
   ```
3. 创建 tunnel，记下 `<TUNNEL_ID>`：
   ```bash
   docker run -it --rm --user root \
     -v /opt/mediahub/secrets/cloudflared:/root/.cloudflared \
     cloudflare/cloudflared:latest tunnel create mediahub-gpu
   ```
4. 凭证移交给常驻 nonroot 容器：
   ```bash
   sudo chown -R 65532:65532 /opt/mediahub/secrets/cloudflared
   sudo chmod 600 /opt/mediahub/secrets/cloudflared/cert.pem /opt/mediahub/secrets/cloudflared/*.json
   ```
5. **Mac Mini 上**回填 `cloudflared/config.yml` 两处 `REPLACE_WITH_TUNNEL_ID` → commit+push；服务器 `git pull`
6. DNS 路由（自动建橙云 CNAME）：
   ```bash
   docker run -it --rm --user root \
     -v /opt/mediahub/secrets/cloudflared:/root/.cloudflared \
     cloudflare/cloudflared:latest tunnel route dns mediahub-gpu api.nous.ink
   docker run -it --rm --user root \
     -v /opt/mediahub/secrets/cloudflared:/root/.cloudflared \
     cloudflare/cloudflared:latest tunnel route dns mediahub-gpu sb.nous.ink
   ```
7. 起栈：`docker compose up -d`（在本目录）
8. 验收（任意联网机器）：
   ```bash
   curl -sS https://api.nous.ink/healthz     # → ok
   curl -sS -o /dev/null -w "%{http_code}\n" https://sb.nous.ink/healthz   # → 200
   ```
   全程 443、证书有效、无 `:88`。

## ⚠️ cloudflared 挂载纪律（血泪预防）

- CLI 子命令（login/create/route dns）读写 `~/.cloudflared`：一次性容器统一 `--user root` + 挂 `/opt/mediahub/secrets/cloudflared:/root/.cloudflared`。挂 `/etc/cloudflared` 会让 cert.pem 丢在容器里。
- 常驻 tunnel run：config 从仓库挂单文件，凭证从 secrets 目录挂 `/etc/cloudflared/creds`（均 `:ro`）。
- Linux bind-mount 下 uid 65532 写不进用户属主 755 目录（macOS Docker Desktop 验不出此坑）——所以一次性 CLI 用 root 写、事后 chown 65532。

## 回滚

`docker compose down` + Cloudflare 删 `api`/`sb` 两条 CNAME + `tunnel delete mediahub-gpu`。NAS 全程未动，无需恢复。

## P1 衔接

- ingress `api.nous.ink` 改指 backend、`sb.nous.ink` 改指 Kong
- 部署机制换 self-hosted runner（epic spec §6 P1 与 §4.1 拓扑）
- 凭证常驻 `/opt/mediahub/secrets/cloudflared/`（仓库树外），物理不可能进 git
- backend→AI 走 localhost:8000（nous-center 网关原生跑在本机，零容器，互不干扰）
