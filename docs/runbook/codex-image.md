# Codex (GPT Image 2) 生图 provider — 运维手册

CodexCliProvider（`backend/app/services/media/parsers/video_providers/codex_cli.py`）
以 subprocess 调 `gpt-image-2-skill` CLI 生图。凭证是 **gpupc 宿主机 heygo 用户的
Codex OAuth 会话**（`/home/heygo/.codex/auth.json`），不是 API Key——消耗的是
ChatGPT 订阅额度。catalog 行 `codex-image`（`mediahub_models`，migration 430）。

## 架构事实

- 二进制：`gpt-image-2-skill`（Rust 静态二进制，Dockerfile 从 npm registry 直接拉
  `…-linux-x64-static` tarball，版本 + sha256 双 pin，**不装 node/npm**）。
- 调用形态：`gpt-image-2-skill --json --json-events --provider codex --auth-file
  $CODEX_AUTH_FILE images generate|edit --prompt … --out … --size … --format png
  --quality high`。`--json` 模式 stdout 是单个纯 JSON 对象：成功
  `{"ok":true,"output":{"path":…}}`，失败 `{"ok":false,"error":{"code":…,"message":…}}`
  且 exit 1。
- `--json-events` 只影响 **stderr**（stdout 形状不变），把上游 SSE 事件流以 NDJSON
  逐行吐出。加它的唯一理由：模型按内容策略**拒绝**时不会调用 image_generation 工具，
  CLI 只报 `missing_image_result`（描述管道形状，用户无从下手），而模型写的拒绝理由
  + 它主动给出的可用改写**只存在于这条流里**。`split_skill_events()` 在 `_run_cli`
  里一次性把流与 CLI 自身的诊断分开：**只有解析不出 JSON 的行**才会进日志、进
  `CodexCliError.stderr`、参与分类——流里全是模型可控文本，让它参与判定等于允许模型
  伪造一个 auth 失败，把用户支去重新登录。拒绝走 `code="content_refused"`，模型原话
  走 `detail`（再经 workflow 落 `task_tracking.metadata.failure`，jsonb 才存得住中文）。
- 上游端点：`chatgpt.com/backend-api/codex/responses`（ChatGPT 私有后端）。这是
  ToS 灰色地带，账号风控风险自担——正因如此该 provider 是 owner 私有的，不开放
  给全站用户。
- 挂载：compose 把 `/home/heygo/.codex` **读写** bind-mount 到 backend/worker 的
  `/app/.codex`（CLI 会把刷新后的 access_token 写回 auth.json）。
- 超时：`CODEX_CLI_TIMEOUT`（秒，默认 900，clamp 30–3600）。

## 编排模型是目录里的 `actual_model`，不是 skill 的默认值（2026-09-05 血泪）

Codex 出图走的是 Responses API：一个**编排模型**（LLM）决定是否调用 `image_generation` 工具，
工具内的出图模型固定是 `gpt-image-2`。`gpt-image-2-skill` 的 `--model` 在 codex provider 上
改的是**编排模型**（实测：`-m gpt-6-astra` 返回体 `request.model=gpt-6-astra`、
`delegated_image_model=gpt-image-2`）。

- 服务端路径：`mediahub_models.codex-image.actual_model` → `codex_cli.py` 的 `--model`
- daemon 路径：`mediahub_models.codex-local-image.actual_model` → payload.model → daemon 的 `--model`
- 两者都为空时 skill 用**写死**的默认 `gpt-5.4`（0.7.3 二进制里硬编码，无配置项、无环境变量可改）

2026-09-05 OpenAI 收掉了 ChatGPT 账号走 Codex 时对 `gpt-5.4` 的支持（`HTTP 400: The 'gpt-5.4'
model is not supported when using Codex with a ChatGPT account`），两条路径同时全挂、每个请求 1 秒
即败。处置就是把两行 `actual_model` 改成账号当前被接受的模型（当时选 `gpt-6-astra`——codex CLI
自己的默认；历史会话里 `gpt-5.6-sol` / `gpt-5.5` 也被接受）。**不需要发版、不需要升 daemon。**

怎么查账号现在接受哪些编排模型（每次 1 秒、不出图不花钱）：

```bash
printf '{"model":"%s","input":"hi"}' gpt-6-astra > /tmp/b.json
gpt-image-2-skill --json --provider codex request create --request-operation responses --body-file /tmp/b.json
# 被接受 → 报 "Input must be a list"（进到了下一层校验）；不被接受 → "model is not supported"
```

⚠️ 别用 `config add-provider` 去试：它不校验 `--type`、会把任何东西写成默认 provider，而且它建出的
`~/.codex/gpt-image-2-skill/config.json` 是 0600 —— 容器以 uid 1031 挂载读不了，服务端路径立刻
`config_read_failed`。探针前那个文件本来不存在。

## API-key 路径（`openai-images`，2026-09-13）

同一个二进制的第二条路径：`--provider openai`，凭证是**目录行自己的 api_key**
（OpenAI 平台 key，按 token 计费到组织账单），跟上面那条烧 ChatGPT 订阅额度的
Codex 会话互不相干。协议实现在
`backend/app/services/ai/provider_protocols/openai_images.py`。

目录行（migration 465，两行都**默认 disabled**，`owner_user_id` 为 NULL 即平台级，
不像 codex 行绑定 owner）：

| `name` | `actual_model` | 画布里显示 |
|---|---|---|
| `openai-image-flare` | `gpt-image-2.5-flare` | GPT Image 2.5 Flare (OpenAI API) |
| `openai-image-sunburst` | `gpt-image-2.5-sunburst` | GPT Image 2.5 Sunburst (OpenAI API) |

显示名里的 "(OpenAI API)" 是给用户的**计费信号**——选它花的是组织的钱，不是订阅额度。
同一个 migration 把 codex 两行的显示名去掉了版本号（`GPT Image (Codex)` /
`GPT Image (Codex, local)`）：订阅路径上工具内的出图模型是 OpenAI 的灰度决定，
不是我们能选的，写死版本号只会过期。

**key 怎么进去**：Admin → AI Models，**逐行**粘贴后保存（那里会加密存储），再把
`is_enabled` 打开。migration 只种 `api_key = ''`（该列 NOT NULL）——迁移文件里写明文
key 等于把凭证提交进 git，还会绕过 Admin 的加密。key 为空时协议在 build 期就抛类型化
拒绝（`ProtocolCapabilityError`），不会退化成 CLI 的 `not_logged_in`——后者读起来像
Codex 会话坏了，会把人支去重新登录。

**探针**（`auth_source` 应为 `env`，证明 key 真的走到了 CLI）：

```bash
docker exec nous-worker sh -c \
  'OPENAI_API_KEY=sk-... gpt-image-2-skill --json --provider openai doctor' | head -40
```

服务端路径不依赖这个环境变量：`codex_cli.py` 用 `safe_popen_kwargs(env_extra=
{"OPENAI_API_KEY": …})` 把目录行的 key 显式注进子进程（默认擦洗会丢掉一切 `*KEY*`，
见 CLAUDE.md「子进程环境要擦洗」）。⚠️ 同「读正常 ≠ 服务正常」：`doctor` 只证明 key
存在且形状可用，不证明它有额度——真验收要在 UI 里用这两行真出一张图。

**两条路径的能力不一样，结论不许互相搬运**：

| | API-key（`openai-images`） | Codex 订阅（`codex` / `codex-local`） |
|---|---|---|
| 尺寸 | `--size` 按写的来 | 只有三档，catalog aspect 就近映射 |
| quality | 多 `xhigh` / `max` 两档 | 仅 low/medium/high；`xhigh` 被**静默降级成 medium**（2026-09-09 实测） |

⚠️ API-key 那一列的依据是 OpenAI 文档（2026-09-13 读）+ 0.7.4 的 `--quality` 枚举，
**尚未在真 API 上实测**——按「文档化的预期」读它，别当观测结果。真实测在生图验收里。

升级二进制后先把枚举打出来再改代码里的 tier 表，别假设它没变（`high` 与 `xhigh` 在
二进制里是重叠存储的，`strings` 看不出 `high` 还在不在）：

```bash
docker exec nous-worker gpt-image-2-skill images generate --help | grep -A3 'possible values'
```

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
