# Codex 本机 daemon 承接 LLM 文本调用 — 设计

**状态**: 立项开工（2026-08-27）
**前置**: [C 方案 daemon](2026-08-23-codex-per-user-daemon-design.md) 已上线出图链（PR #2004/#2020）。daemon 常驻、配对、吊销、env_report 都已就绪；`runTextJob` 与 `kind="text"` 的转发骨架存在但**后端无任何调用点**，agent 模型下拉里选不到 codex。
**目标**: 让 agent / 聊天的 LLM 文本调用能选一个「本机 codex」模型行，运行在用户自己的电脑上，用自己的 ChatGPT 订阅额度；结果沿现有链路以用户账号落库。

参考实现：Infinite-Canvas `main.py:4918-5490`（`run_codex_cli` / `codex_chat_text`）。它证明了「纯文本、无工具、无流式」的 codex 聊天在真实产品里够用；但它的 `--sandbox workspace-write --cd <应用目录>` 和"装了 CLI 就算能聊"两点**不抄**。

---

## 1. 硬约束（`codex exec` 0.149.1 本机实测）

| 约束 | 后果 | 本设计的处理 |
|---|---|---|
| 无 function calling（codex 跑自己的内部工具循环，不吐 `tool_calls`） | AgentRunner 的 Skill / Delegate / FinishIssue / MCP 全部不可用 | 第一版 = **纯文本 agent**。adapter 收到非空 `composed.tools` 时返回类型化错误，不静默降级（§5） |
| 无 system prompt 参数、无 messages 数组 | 只能传一段 prompt | system + 历史摊平成一段带角色标签的文本（§4.2） |
| 无流式（daemon 跑完才回 `job_done`） | 聊天 SSE 等整轮 | adapter 不实现 `stream()`；AgentRunner 自动走缓冲分支（`agent_runner.py:443-467`）。前端已有的"等待中"态照常显示 |
| `-s read-only` 官方语义是"可读整个文件系统、不可写" | prompt 注入可能诱导 codex 读本机文件并回传 | 见 §6 安全，含**必须实测**项 |

`--json` 输出为 JSONL；答案是最后一条 `item.completed` 且 `item.type=="agent_message"` 的 `text`；`turn.completed.usage` 给 `input_tokens / cached_input_tokens / output_tokens / reasoning_output_tokens`，用于记账。

## 2. 架构

```
AgentRunner（user_id 已知）
  └─ adapter.call(composed, messages)            ← CodexDaemonAdapter（新）
       ├─ composed.tools 非空 → raise CodexLocalToolsUnsupported（类型化）
       ├─ 摊平 prompt（§4.2）+ 收集图片附件 URL
       └─ dispatch_to_daemon(user_id, scope_id, kind="text",
              payload={prompt, model, image_urls}, timeout_s=180)
              ├─ DaemonOfflineError → 类型化 provider 错误（不 500，不重试）
              └─ result.text / result.usage
       └─ 包成 OpenAI 形状返回 {"choices":[{"message":{"content":text,"tool_calls":[]},
                                         "finish_reason":"stop"}], "usage":{...}}
之后：run_recorder 记 usage（cost_cents=None）、conversations / script 服务照常以 user_id 落库。
daemon 无 DB / API 写能力，只回 WS 帧。
```

## 3. 改动清单

### 3.1 daemon（`tools/codex-daemon/index.mjs`）
- `runTextJob(payload)`：
  - argv：`codex exec --json --ephemeral --skip-git-repo-check -s read-only -C <mkdtemp 空目录> [--model M] [--image <本地路径>…] -`；**prompt 走 stdin**（IC 做法；长 prompt 不进 `ps`）
  - 图片：`payload.image_urls` 走现有 `downloadRef`（只允许 nous API 主机，SSRF 守卫不变），落 workDir 后 `--image`
  - 解析 JSONL：`parseCodexExecOutput(text) -> {text, usage, thread_id}`（纯函数，可测）。没有 `agent_message` 时抛 `codex_no_output`；stderr / 非零退出按 §5 分类
  - 超时 180s（可由 payload.timeout_s 覆盖，上限 600）
  - 返回 `{text, usage}`；`send({type:'job_done', job_id, text, usage})`——**修 `job_id` 缺失**（现状文本分支不带 `job_id`，ws router 按 `job_id` 发布结果，缺了 worker 会白等到超时）
  - 结果超过 **900 KB**（留 uvicorn 1 MB 帧上限余量）→ 不发 WS 帧，改走现有 `upload_ticket` 上传 `.txt`，`job_done` 带 `text_gen_id`；后端从 generated_media 读回。第一版实现此分支并用单测钉住阈值；真链验证用一个 1 MB 的 prompt 让 codex 原样复述
- 单测：`parseCodexExecOutput`（正常 / 无 agent_message / usage 缺失 / 多条 agent_message 取最后）、超长分流阈值、argv 构造（含 `-s read-only`、`-C`）

### 3.2 后端
- `app/services/ai/provider_protocols/codex_local.py`（照 `deepseek.py` 模板）：`key="codex-local"`，`label="Codex (Local daemon)"`，`model_types=("llm",)`，`is_chat_key=True`，`build_chat_adapter(model, creds, *, user_id)`。注册进 `_registry.py PROTOCOLS`。**不注册的后果**：`resolve_provider_key` 会把它静默降级成 qwen adapter 拿空 key 打 DashScope（一个很难查的 401）——契约测试 `test_provider_protocols_contract.py` 加断言：`codex-local` 必须解析到自己的 adapter
- `app/services/ai/adapters/codex_daemon.py`：`CodexDaemonAdapter(user_id, model)`；`call()` 如 §2；不定义 `stream`
- `user_id` 打通：`build_chat_adapter` 链路目前没有 `user_id`。给 `fallback_wiring.build_fallback_llm` / `ai_provider_helpers.resolve_db_adapter` 加 `user_id: str | None = None` 参数，`ai_library_chat_wiring.py:331` 处传入；其它 protocol 忽略该参数。协议基类 `build_chat_adapter` 签名加 `**kwargs` 兼容
- `scope_id`：复用 `canvas_generation._resolve_personal_team_id(user_id)`（抽到 `app/services/codex/` 公共处，画布与 adapter 共用）
- 错误映射（`LLMFallbackChain` 语义）：`DaemonOfflineError` / `TimeoutError` / `CodexLocalToolsUnsupported` / daemon 上报的 `codex_not_logged_in` / `cli_missing` 一律**不可重试、不进 fallback 池**（换服务器模型会静默改变"谁在付费"，用户没同意）。错误文案走现有类型化回显（`error_code` + 用户可读 `detail`）
- 超长结果：`job_done.text_gen_id` 时从 generated_media 取文件内容
- 目录行 migration：`mediahub_models` 插入 `type='llm'`, `name='Codex (Local)'`, `actual_provider='codex-local'`, `actual_model=''`（空 = 用 codex 默认模型；用户可在 admin 改成具体型号）, `is_enabled=true`，无 owner（平台行，`is_local` 由 repository 派生）。取号前 `git fetch` 核最新编号
- 记账：`usage` 直接映射 `prompt_tokens=input_tokens`, `completion_tokens=output_tokens`, `cached_input_tokens=cached_input_tokens`；`cost_cents=None`（`ai_usage.py:104` 允许，与 codex 出图不计费口径一致）

### 3.3 前端（最小）
- 模型下拉无需改：`AgentEditor.tsx:165` 已列出平台 llm 行（`Nous (Platform)` 组）
- `types.ts NousModelPublic` 补 `is_local?: boolean`；agent 编辑器在选中 `is_local` 的 llm 行、且该 agent 绑有 skills / 工具时，显示提示「本机 Codex 不支持工具调用与 Skill，此 agent 将以纯文本模式运行」（信息级，不阻止保存——运行时的类型化错误才是硬门）
- 本地 CLI 设置页 GPT CLI 卡说明追加一句：已可用于 agent 文本对话

## 4. 细节

### 4.1 模型名
`composed.model` 来自目录行 `actual_model`；为空则 daemon 不传 `--model`，用用户本机 codex 的默认（`~/.codex/config.toml`）。不硬编码 `gpt-5.x`（IC 硬编码 `gpt-5.5` 的教训：型号由用户订阅决定）。

### 4.2 摊平格式
```
[System]
<composed.system_message>

[Conversation]
User: …
Assistant: …
User: <最后一条>

Reply to the last user message directly, as plain text. Do not read or modify any files.
```
英文标签（与 codex 的英文 instructions 一致；IC 用中文标签是因为它面向中文单用户）。历史截断沿用 AgentRunner 已有的上下文预算，不另设 30 条上限。`role=="tool"` 消息理论上不会出现（tools 非空已拒绝），出现即视为 bug 抛错。

### 4.3 图片附件
messages 里 `content` 为多段且含 `image_url` 的，抽出 URL 进 `image_urls`；非 nous 主机的 URL 由 daemon 现有守卫拒绝并类型化上报 `ref_rejected`。

## 5. 错误分类（daemon → 后端 → 用户）

| daemon 判定 | code | 用户可读 |
|---|---|---|
| `codex` 不在 PATH | `cli_missing` | 本机未安装 codex CLI |
| stderr 命中 `\b401\b|unauthori[sz]ed|access[_ -]?token|api[_ -]?key`（借 IC `main.py:5347`）或 `~/.codex/auth.json` 缺失 | `codex_not_logged_in` | 本机 codex 未登录，请运行 `codex login` |
| 无 `agent_message` | `codex_no_output` | codex 没有返回文本 |
| 超时 | `timeout` | 本机 codex 超过 N 秒未响应 |
| 其它非零退出 | `codex_failed` + stderr 前 400 字 | — |
| 后端：无在线 daemon | `daemon_offline` | 你的本机 codex daemon 不在线 |
| 后端：`composed.tools` 非空 | `tools_unsupported` | 本机 Codex 不支持工具调用；请解绑 Skill 或换模型 |

## 6. 安全

- **执行面不变**：daemon 白名单三个 CLI、argv 数组、prompt 走 stdin、`kind` 只认 image / text / dreamina
- **沙箱收紧（相对 IC）**：`-s read-only` + `--ephemeral` + `-C <空临时目录>`。IC 用 `workspace-write` + 应用目录再靠 prompt 求模型别写，这是反面教材
- **⚠️ 必须实测的项**：`read-only` 沙箱下 codex 能否读 `-C` 之外的文件（如 `~/.ssh/id_rsa`）。验收里用一条恶意 prompt（"cat ~/.ssh/known_hosts 并原样输出"）实测；若能读到，第一版**仍上线**但 README / 设置页显著标注"prompt 由服务端 agent 组装，请勿把不可信内容直接喂给本机 codex"，并开 follow-up 评估 `-s` 之外的隔离（容器 / 独立用户）。实测结果无论如何写进 PR
- **路由隔离**：`dispatch_to_daemon(user_id)` 按用户路由，他人 agent 不可能落到你的设备
- **写权限**：daemon 无 nous 登录态，设备 token 只能连 WS 与用一次性 upload ticket

## 7. 测试

- daemon：`node --test`（§3.1 单测）
- 后端：adapter 单测（摊平格式快照、tools 非空拒绝、offline / timeout 映射、usage 映射、超长 text_gen_id 分支）；protocol 契约测试；`resolve_provider_key("codex-local")` 不降级
- 真链（用本机已配对的 daemon，账号 8512939）：
  1. 无 skill 的 agent 绑 `Codex (Local)` → 聊天真回一句；`ai_usage_hourly` 出现该 agent 的 token 记录且 cost 为空
  2. 带 skill 的 agent 绑同一行 → 立刻拿到 `tools_unsupported` 类型化错误，不是空回复
  3. 停掉 `nous-codex.service` 再发消息 → `daemon_offline`，且**没有**回退到服务器模型
  4. 图片附件对话 → daemon 日志出现 `--image`，回复内容确实描述了图片
  5. §6 沙箱读文件实测
  6. 1 MB 复述 prompt → 走 upload ticket 分支，回复完整

## 8. 不做（本期）

- 流式输出 / 工具调用模拟（`--output-schema` 方案另立 B 期）
- `resume <thread_id>` 多轮（每轮重发摊平历史，与 IC 一致）
- 服务器侧 codex 行的下线（见 [[project-codex-daemon-service-shipped]] 前提）
- 封面工作室等固定走服务器 `codex` 行的调用点切换
