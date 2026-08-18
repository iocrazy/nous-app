# Codex (GPT Image 2) 生图 provider — 运维手册

CodexCliProvider（`backend/app/services/media/parsers/video_providers/codex_cli.py`）
以 subprocess 调 `gpt-image-2-skill` CLI 生图。凭证是 **gpupc 宿主机 heygo 用户的
Codex OAuth 会话**（`/home/heygo/.codex/auth.json`），不是 API Key——消耗的是
ChatGPT 订阅额度。catalog 行 `codex-image`（`mediahub_models`，migration 430）。

## 架构事实

- 二进制：`gpt-image-2-skill`（Rust 静态二进制，Dockerfile 从 npm registry 直接拉
  `…-linux-x64-static` tarball，版本 + sha256 双 pin，**不装 node/npm**）。
- 调用形态：`gpt-image-2-skill --json --provider codex --auth-file $CODEX_AUTH_FILE
  images generate|edit --prompt … --out … --size … --format png --quality high`。
  `--json` 模式 stdout 是单个纯 JSON 对象：成功 `{"ok":true,"output":{"path":…}}`，
  失败 `{"ok":false,"error":{"code":…,"message":…}}` 且 exit 1。
- 上游端点：`chatgpt.com/backend-api/codex/responses`（ChatGPT 私有后端）。这是
  ToS 灰色地带，账号风控风险自担——正因如此该 provider 是 owner 私有的，不开放
  给全站用户。
- 挂载：compose 把 `/home/heygo/.codex` **读写** bind-mount 到 backend/worker 的
  `/app/.codex`（CLI 会把刷新后的 access_token 写回 auth.json）。
- 超时：`CODEX_CLI_TIMEOUT`（秒，默认 900，clamp 30–3600）。

## 一次性安装 / 登录（宿主机）

宿主机已有 codex CLI（linuxbrew）。若重装：

```bash
curl -fsSL https://chatgpt.com/codex/install.sh | sh   # 或 npm i -g @openai/codex
codex login                                            # 浏览器 OAuth,写 ~/.codex/auth.json
```

容器 uid=1031 ≠ heygo(1000)，用 ACL 授权，**不要 chown/chgrp**（宿主机 codex CLI
还在用这个目录）。⚠️ `~/.codex` 是 symlink → `~/.config/codex`，`setfacl -R` 不跟
符号链接递归，必须打在真实目录上（2026-08-17 实测：打在 symlink 上只有目录本身
拿到 ACL，auth.json 仍 Permission denied）：

```bash
setfacl -R -m u:1031:rwX -m d:u:1031:rwX /home/heygo/.config/codex
```

（docker bind mount 会解析 symlink，compose 里写 `/home/heygo/.codex` 不受影响。）
已于 2026-08-17 在 gpupc 执行并用 `docker run -u 1031:100` 写入探针验证 PROBE-OK。

## 部署验收（读正常 ≠ 服务正常）

```bash
# 1) 容器内二进制 + 登录态探针(access_token_present 必须为 true)
docker exec nous-worker gpt-image-2-skill --json doctor | head -40

# 2) 写入探针 —— auth.json 必须可写(token 刷新要写回),只读挂载是静默断裂
docker exec nous-worker sh -c 'touch /app/.codex/.wprobe && rm /app/.codex/.wprobe' \
  && echo OK-writable || echo FAIL-readonly

# 3) 端到端业务冒烟:用 owner 账号在 UI 里真生成一张图(codex-image 模型)
```

⚠️ `doctor` 的 `access_token_present: true` 只证明文件里有 token，不证明 token
有效（不可证伪信号）——真验收必须走一次 3)。

## 排障

| 症状（错误 code） | 含义 | 处置 |
|---|---|---|
| `not_logged_in` | auth.json 缺失/过期/被吊销 | 宿主机 `codex login` 重登；确认 ACL 仍在（`getfacl /home/heygo/.codex`） |
| `no_credit` | 订阅额度耗尽 / 限流 | 等额度窗口刷新，或暂停使用 |
| `cli_missing` | 镜像里没有二进制 | Dockerfile 安装层被跳过/失败，重新 build |
| `timeout` | 生成超时（高质量约 3–10 分钟） | 调大 `CODEX_CLI_TIMEOUT`；检查网络 |
| `generation_failed` | 上游拒绝/内容策略/其他 | 看 `application_logs` 里 `[codex-cli]` 的 stderr 全文 |

## 已知边界

- **仅生图**（`images generate`；有本地参考图时走 `images edit`）。远程 URL 参考图
  会被丢弃并记 warning（`_CodexImageAdapter`）——CLI 的 `--ref-image` 只吃本地文件。
- 尺寸只有三档：`1024x1024` / `1536x1024` / `1024x1536`，catalog aspect 就近映射。
- 宿主机 codex CLI 与容器共用同一份 auth.json（刻意如此：refresh token 轮换时
  两边各持一份副本会互相踢下线）。
- 升级二进制：改 Dockerfile 的 `GPT_IMAGE_2_SKILL_VERSION` + `…_SHA256` 两个 ARG
  （新 sha 用 `curl <tarball> | sha256sum` 取），走正常 PR review。
