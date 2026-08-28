# Codex 本机 daemon 承接 LLM 文本调用 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** agent / 聊天可以选一个「Codex (Local)」LLM 目录行，文本调用经用户本机 daemon 的 `codex exec` 完成，结果沿现有链路以用户账号落库并记 token。

**Architecture:** 新增 `codex-local` chat protocol + `CodexDaemonAdapter`（不实现 `stream`，AgentRunner 自动走缓冲分支）；adapter 把 system + 历史摊平成一段 prompt，经已有 `dispatch_to_daemon(kind="text")` 发到 daemon；daemon 用 `codex exec --json` 跑完解析 JSONL 回 `{text, usage}`（超长分片）；所有失败走 `LLMCallError` → `stream_error_data` 的类型化回显，不进 fallback 池。

**Tech Stack:** Python 3.13 / FastAPI / pytest（`backend/`）、Node 22 ESM + `node --test`（`tools/codex-daemon/`）、React 19 + vitest（`frontend/`）、SQL migration（`supabase/migrations/`）。

**Spec:** `docs/superpowers/specs/2026-08-27-codex-local-text-llm-design.md`

## Global Constraints

- 中文 commit / 沟通；UI 文案英文 + i18n（en.json / zh.json 两边都要有 key）。
- daemon 只用 argv 数组 spawn，prompt 走 stdin，绝不拼 shell 字符串；沙箱参数固定 `--ephemeral --skip-git-repo-check -s read-only -C <临时空目录>`。
- **不注册 protocol 就不许上目录行**：`resolve_provider_key("codex-local", …)` 必须解析到 `codex-local` 自身（契约测试钉住）。
- daemon 侧失败（离线 / 超时 / 未登录 / 工具不支持）**不可重试、不进 fallback 池**：异常带 `status_code` ∈ {400,401,403,404,422}，让 `classify_error` 判 `non_retryable` → `LLMCallError`。
- `cost_cents=None`；usage 用 OpenAI 键名 `prompt_tokens / completion_tokens / prompt_tokens_details.cached_tokens`。
- 后端测试跑 `cd backend && uv run pytest <file> -q`；daemon 测试 `cd tools/codex-daemon && npm test`；前端 `cd frontend && npx vitest run <path>`。
- 分支 `feat/codex-local-text`（worktree `.worktrees/feat-codex-local-text`），基于 `origin/master`；每个 Task 一个 commit；不要 merge。
- **spec §3.1 里"`job_done` 不带 `job_id`"是误判**：`index.mjs:379` 的 `send` 闭包已自动带 `job_id`，不要动它。

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `tools/codex-daemon/index.mjs` | 新增 `parseCodexExecOutput`、`buildCodexExecArgs`、`chunkText`（导出、纯函数）；重写 `runTextJob`；`runCommand` 支持 `stdin` |
| `tools/codex-daemon/index.test.mjs` | 上述纯函数测试 |
| `backend/app/api/codex_daemon_ws_router.py` | 收 `job_chunk` 帧拼接，`job_done` 时把 `text`+`usage` 发布 |
| `backend/app/services/codex/errors.py`（新） | `CodexLocalError(code, message, status_code)` |
| `backend/app/services/codex/flatten.py`（新） | `flatten_for_codex(system_message, messages) -> (prompt, image_urls)` 纯函数 |
| `backend/app/services/codex/personal_scope.py`（新） | `resolve_personal_scope_id(user_id) -> int`（从 `resources_service._resolve_personal_team_id` 包一层） |
| `backend/app/services/ai/adapters/codex_daemon.py`（新） | `CodexDaemonAdapter` |
| `backend/app/services/ai/provider_protocols/codex_local.py`（新） | `CodexLocalProtocol` |
| `backend/app/services/ai/provider_protocols/_registry.py` | 注册 |
| `backend/app/services/ai/provider_protocols/base.py` | `build_chat_adapter(self, model, creds, **context)` |
| `backend/app/services/ai/adapters/factory.py` | `get_adapter_for_key(..., user_id=None)` 透传 |
| `backend/app/services/ai/providers/ai_provider_helpers.py` | `resolve_db_adapter(..., user_id=None)` |
| `backend/app/services/ai/llm/fallback_wiring.py` | `build_fallback_llm(..., user_id=None)` |
| `backend/app/services/ai/chat/ai_library_chat_wiring.py` | 传 `user_id` |
| `backend/app/services/ai/error_catalog.py` + `backend/app/core/provider_errors.py` | 新错误码 4 个 + 映射 |
| `supabase/migrations/443_codex_local_llm_catalog.sql` | 目录行 |
| `frontend/utils/providerErrorMessage.ts` + locales | 新错误码文案 |
| `frontend/types.ts` / `AgentPersonaTab.tsx` | `is_local` + 纯文本提示 |
| `tools/codex-daemon/README.md` | 文本能力说明 + 安全提示 |

---

### Task 1: daemon — `codex exec` 参数、stdin、JSONL 解析、分片

**Files:**
- Modify: `tools/codex-daemon/index.mjs:180-204`（`runCommand`）、`:302-308`（`runTextJob`）、`:386-393`（job 分支）
- Test: `tools/codex-daemon/index.test.mjs`

**Interfaces:**
- Produces: `export function buildCodexExecArgs({model, imagePaths, workDir}) -> string[]`；`export function parseCodexExecOutput(jsonl) -> {text, usage, threadId}`（无 `agent_message` 时 throw `Error('codex_no_output: …')`）；`export function chunkText(text, size=CHUNK_BYTES) -> string[]`；`export const TEXT_INLINE_LIMIT = 900 * 1024`、`CHUNK_BYTES = 256 * 1024`；`runCommand(bin, args, {timeoutMs, stdin})`
- Produces（协议）：文本 job 回帧序列：0..n 个 `{type:'job_chunk', job_id, seq, data}` 然后 `{type:'job_done', job_id, text?: string, usage, chunked: boolean}`——`text` 只在未分片时带；分片时 `text` 省略、`chunked:true`

- [ ] **Step 1: 写失败测试**

在 `index.test.mjs` 末尾追加：

```js
import {
  buildCodexExecArgs,
  chunkText,
  parseCodexExecOutput,
  TEXT_INLINE_LIMIT,
} from './index.mjs';

test('buildCodexExecArgs pins the read-only ephemeral sandbox and reads the prompt from stdin', () => {
  const args = buildCodexExecArgs({ model: 'gpt-5', imagePaths: ['/tmp/a.png'], workDir: '/tmp/w' });
  assert.deepEqual(args, [
    'exec', '--json', '--ephemeral', '--skip-git-repo-check',
    '-s', 'read-only', '-C', '/tmp/w', '--model', 'gpt-5', '--image', '/tmp/a.png', '-',
  ]);
  const noModel = buildCodexExecArgs({ model: '', imagePaths: [], workDir: '/tmp/w' });
  assert.ok(!noModel.includes('--model'));
  assert.equal(noModel.at(-1), '-');
});

test('parseCodexExecOutput takes the LAST agent_message and the turn usage', () => {
  const jsonl = [
    '{"type":"thread.started","thread_id":"t1"}',
    '{"type":"turn.started"}',
    '{"type":"item.completed","item":{"id":"i0","type":"reasoning","text":"thinking"}}',
    '{"type":"item.completed","item":{"id":"i1","type":"agent_message","text":"draft"}}',
    '{"type":"item.completed","item":{"id":"i2","type":"agent_message","text":"final answer"}}',
    '{"type":"turn.completed","usage":{"input_tokens":100,"cached_input_tokens":40,"output_tokens":7,"reasoning_output_tokens":2}}',
    'not json at all',
  ].join('\n');
  const out = parseCodexExecOutput(jsonl);
  assert.equal(out.text, 'final answer');
  assert.equal(out.threadId, 't1');
  assert.deepEqual(out.usage, {
    input_tokens: 100, cached_input_tokens: 40, output_tokens: 7, reasoning_output_tokens: 2,
  });
});

test('parseCodexExecOutput throws codex_no_output when no agent_message exists', () => {
  assert.throws(
    () => parseCodexExecOutput('{"type":"turn.completed","usage":{}}'),
    /codex_no_output/,
  );
});

test('parseCodexExecOutput tolerates missing usage', () => {
  const out = parseCodexExecOutput('{"type":"item.completed","item":{"type":"agent_message","text":"x"}}');
  assert.equal(out.text, 'x');
  assert.deepEqual(out.usage, {});
});

test('chunkText splits on byte size and round-trips', () => {
  const s = '汉'.repeat(1000) + 'abc';
  const parts = chunkText(s, 1000);
  assert.ok(parts.length > 1);
  assert.ok(parts.every((p) => Buffer.byteLength(p, 'utf8') <= 1000));
  assert.equal(parts.join(''), s);
  assert.equal(TEXT_INLINE_LIMIT, 900 * 1024);
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd tools/codex-daemon && npm test 2>&1 | tail -20`
Expected: 5 条新测试 FAIL（`does not provide an export named 'buildCodexExecArgs'`）。

- [ ] **Step 3: 实现**

`runCommand` 改成支持 stdin（替换 `index.mjs:180-204` 整个函数）：

```js
function runCommand(bin, args, { timeoutMs = 15 * 60_000, stdin = null } = {}) {
  return new Promise((resolve, reject) => {
    // argv array, never a shell string: nothing the server sends can be
    // interpreted as shell syntax.
    const child = spawn(bin, args, { stdio: [stdin == null ? 'ignore' : 'pipe', 'pipe', 'pipe'] });
    let out = '';
    let err = '';
    const timer = setTimeout(() => {
      child.kill('SIGKILL');
      reject(new Error(`${bin} timed out after ${Math.round(timeoutMs / 1000)}s`));
    }, timeoutMs);
    child.stdout.on('data', (d) => (out += d));
    child.stderr.on('data', (d) => (err += d));
    child.on('error', (e) => {
      clearTimeout(timer);
      reject(new Error(`${bin} not found or not executable: ${e.message}`));
    });
    child.on('close', (code) => {
      clearTimeout(timer);
      if (code === 0) resolve({ out, err });
      else reject(new Error(`${bin} exited ${code}: ${(err || out).slice(0, 400)}`));
    });
    if (stdin != null) {
      child.stdin.on('error', () => { /* EPIPE when codex exits early — the close handler reports it */ });
      child.stdin.end(stdin);
    }
  });
}
```

在 `runTextJob` 位置（`:302-308`）替换为：

```js
export const TEXT_INLINE_LIMIT = 900 * 1024; // uvicorn's default WS frame cap is 1 MiB
export const CHUNK_BYTES = 256 * 1024;
const TEXT_TIMEOUT_DEFAULT_MS = 180_000;
const TEXT_TIMEOUT_MAX_MS = 600_000;

/** Fixed sandbox: read-only, ephemeral, cwd = an empty temp dir. IC ran
 *  `--sandbox workspace-write --cd <app dir>` and then asked the model in the
 *  prompt not to write files — we do not repeat that. */
export function buildCodexExecArgs({ model, imagePaths, workDir }) {
  const args = ['exec', '--json', '--ephemeral', '--skip-git-repo-check', '-s', 'read-only', '-C', workDir];
  if (model) args.push('--model', String(model));
  for (const p of imagePaths ?? []) args.push('--image', p);
  args.push('-'); // prompt from stdin: long prompts never hit argv / ps
  return args;
}

/** `codex exec --json` prints JSONL. The answer is the LAST
 *  item.completed whose item.type is agent_message; turn.completed.usage
 *  carries real token counts. Non-JSON lines are ignored. */
export function parseCodexExecOutput(jsonl) {
  let text = null;
  let usage = {};
  let threadId = null;
  for (const line of String(jsonl).split('\n')) {
    let ev;
    try { ev = JSON.parse(line); } catch { continue; }
    if (ev?.type === 'thread.started' && ev.thread_id) threadId = ev.thread_id;
    if (ev?.type === 'item.completed' && ev.item?.type === 'agent_message' && typeof ev.item.text === 'string') {
      text = ev.item.text;
    }
    if (ev?.type === 'turn.completed' && ev.usage && typeof ev.usage === 'object') usage = ev.usage;
  }
  if (text == null) throw new Error('codex_no_output: codex exec finished without an agent_message');
  return { text, usage, threadId };
}

export function chunkText(text, size = CHUNK_BYTES) {
  const buf = Buffer.from(text, 'utf8');
  const parts = [];
  let start = 0;
  while (start < buf.length) {
    let end = Math.min(start + size, buf.length);
    // never cut inside a UTF-8 sequence: back up to a char boundary
    while (end < buf.length && (buf[end] & 0xc0) === 0x80) end -= 1;
    parts.push(buf.subarray(start, end).toString('utf8'));
    start = end;
  }
  return parts;
}

async function runTextJob(payload, workDir) {
  const refs = Array.isArray(payload.image_urls) ? payload.image_urls.slice(0, 9) : [];
  const imagePaths = [];
  for (let i = 0; i < refs.length; i += 1) imagePaths.push(await downloadRef(refs[i], workDir, i));
  const args = buildCodexExecArgs({ model: payload.model, imagePaths, workDir });
  const timeoutMs = Math.min(
    Number(payload.timeout_s) > 0 ? Number(payload.timeout_s) * 1000 : TEXT_TIMEOUT_DEFAULT_MS,
    TEXT_TIMEOUT_MAX_MS,
  );
  const { out } = await runCommand('codex', args, { timeoutMs, stdin: String(payload.prompt ?? '') });
  return parseCodexExecOutput(out);
}
```

job 分支（`:390-392`）替换为：

```js
      } else if (msg.kind === 'text') {
        const { text, usage } = await runTextJob(msg.payload ?? {}, workDir);
        if (Buffer.byteLength(text, 'utf8') <= TEXT_INLINE_LIMIT) {
          send({ type: 'job_done', text, usage, chunked: false });
        } else {
          const parts = chunkText(text);
          parts.forEach((data, seq) => send({ type: 'job_chunk', seq, data }));
          send({ type: 'job_done', usage, chunked: true, chunks: parts.length });
        }
      } else {
```

错误分类：在同一 `catch` 里现有的 `code` 推导（`:395-401` 附近）扩一条——`/codex_no_output/.test(message) ? 'codex_no_output'` 放在 `cli_missing` 之前；`codex_not_logged_in` 的正则改为 `/not logged in|login|\b401\b|unauthori[sz]ed|access[_ -]?token/i`（借 IC 的 auth 判定）。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd tools/codex-daemon && npm test 2>&1 | tail -5`
Expected: 全部 pass（原 27 + 新 5 = 32）。

- [ ] **Step 5: 本机烟测（不进 CI）**

```bash
cd tools/codex-daemon && node -e "
import('./index.mjs').then(async (m) => {
  const { spawn } = await import('node:child_process');
  const fs = await import('node:fs/promises'); const os = await import('node:os'); const path = await import('node:path');
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'cx-'));
  const child = spawn('codex', m.buildCodexExecArgs({ model: '', imagePaths: [], workDir: dir }), { stdio: ['pipe','pipe','pipe'] });
  let out=''; child.stdout.on('data', d => out += d); child.stdin.end('Reply with exactly: PONG');
  child.on('close', () => { console.log(m.parseCodexExecOutput(out)); });
});"
```
Expected: 打印 `{ text: 'PONG', usage: { input_tokens: …, output_tokens: … }, threadId: '…' }`。把输出贴进 commit message。

- [ ] **Step 6: Commit**

```bash
git add tools/codex-daemon/index.mjs tools/codex-daemon/index.test.mjs
git commit -m "feat(codex-daemon): 文本任务改走 stdin + --json 解析 + 只读沙箱 + 超长分片"
```

---

### Task 2: 后端 WS router — 拼接 `job_chunk`，`job_done` 发布 text + usage

**Files:**
- Modify: `backend/app/api/codex_daemon_ws_router.py:170-190`
- Test: `backend/tests/test_codex_daemon_text_chunks.py`（新）

**Interfaces:**
- Consumes: Task 1 的帧协议
- Produces: `publish_result(job_id, {"gen_id", "text", "usage"})`——`text` 为拼接后的完整字符串（未分片时就是帧里的 `text`）；新增纯函数 `assemble_job_result(message: dict, chunks: dict[str, list[str]]) -> dict`

- [ ] **Step 1: 写失败测试**

```python
"""Text jobs bigger than a WS frame arrive as job_chunk frames; the router
reassembles them and publishes ONE result. Pure function, no socket."""

from __future__ import annotations

import pytest

from app.api.codex_daemon_ws_router import assemble_job_result, take_chunk


@pytest.mark.unit
def test_inline_text_passes_through():
    out = assemble_job_result(
        {"type": "job_done", "job_id": "j1", "text": "hi", "usage": {"input_tokens": 3}, "chunked": False},
        chunks={},
    )
    assert out == {"gen_id": None, "text": "hi", "usage": {"input_tokens": 3}}


@pytest.mark.unit
def test_chunks_are_joined_in_seq_order_and_cleared():
    chunks: dict[str, list[str]] = {}
    take_chunk(chunks, {"job_id": "j1", "seq": 1, "data": "world"})
    take_chunk(chunks, {"job_id": "j1", "seq": 0, "data": "hello "})
    out = assemble_job_result(
        {"type": "job_done", "job_id": "j1", "usage": {}, "chunked": True, "chunks": 2},
        chunks=chunks,
    )
    assert out["text"] == "hello world"
    assert "j1" not in chunks


@pytest.mark.unit
def test_chunk_count_mismatch_is_an_error_result():
    chunks: dict[str, list[str]] = {}
    take_chunk(chunks, {"job_id": "j1", "seq": 0, "data": "only one"})
    out = assemble_job_result(
        {"type": "job_done", "job_id": "j1", "usage": {}, "chunked": True, "chunks": 2},
        chunks=chunks,
    )
    assert out["error"].startswith("chunk_mismatch")
    assert "j1" not in chunks
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_codex_daemon_text_chunks.py -q`
Expected: ImportError（`assemble_job_result` 不存在）。

- [ ] **Step 3: 实现**

在 `codex_daemon_ws_router.py` 模块级（`handle_env_report` 之前）加：

```python
def take_chunk(chunks: dict[str, list[str]], message: dict) -> None:
    """Buffer one job_chunk frame. Chunks may arrive out of order only in
    theory (single socket, in-order), but seq is honoured anyway."""
    job_id = str(message.get("job_id") or "")
    seq = int(message.get("seq") or 0)
    buf = chunks.setdefault(job_id, [])
    while len(buf) <= seq:
        buf.append("")
    buf[seq] = str(message.get("data") or "")


def assemble_job_result(message: dict, chunks: dict[str, list[str]]) -> dict:
    """Turn a job_done frame (+ any buffered chunks) into the pub/sub result.
    Always pops the job's buffer so a mismatch can't leak memory."""
    job_id = str(message.get("job_id") or "")
    usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
    if message.get("chunked"):
        parts = chunks.pop(job_id, [])
        expected = int(message.get("chunks") or 0)
        if expected != len(parts) or any(p == "" for p in parts):
            return {"error": f"chunk_mismatch: expected {expected}, got {len(parts)}"}
        return {"gen_id": None, "text": "".join(parts), "usage": usage}
    chunks.pop(job_id, None)
    return {"gen_id": message.get("gen_id"), "text": message.get("text"), "usage": usage}
```

在 `ws_codex_agent` 的接收循环里：`forwarder = …` 之后加 `chunks: dict[str, list[str]] = {}`；把 `elif kind in ("job_done", "job_failed", "job_progress"):` 分支改为：

```python
            elif kind == "job_chunk":
                take_chunk(chunks, message)
            elif kind in ("job_done", "job_failed", "job_progress"):
                job_id = str(message.get("job_id") or "")
                logger.info(
                    "[codex-daemon] {} from device={} job={}", kind, device_id, job_id
                )
                if kind == "job_done":
                    await daemon_presence.publish_result(
                        job_id, assemble_job_result(message, chunks)
                    )
                elif kind == "job_failed":
                    chunks.pop(job_id, None)
                    code = str(message.get("code") or "job_failed")
                    await daemon_presence.publish_result(
                        job_id,
                        {"error": f"{code}: {message.get('message') or ''}".strip()},
                    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_codex_daemon_text_chunks.py tests/test_codex_daemon_registry.py -q`
Expected: 全 pass。

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/codex_daemon_ws_router.py backend/tests/test_codex_daemon_text_chunks.py
git commit -m "feat(codex-daemon): WS 端拼接 job_chunk，job_done 连 usage 一起发布"
```

---

### Task 3: 后端 — 类型化错误 `CodexLocalError` + 错误目录 + 回显映射

**Files:**
- Create: `backend/app/services/codex/errors.py`
- Modify: `backend/app/services/ai/error_catalog.py`（`ALL_ERROR_CODES` / 常量 / `_RULES`）、`backend/app/core/provider_errors.py:33-59`（`_MAPPING`）
- Test: `backend/tests/test_codex_local_errors.py`（新）

**Interfaces:**
- Produces: `class CodexLocalError(RuntimeError)`，字段 `code: str`、`status_code: int`；构造 `CodexLocalError(code, message)`，`status_code` 由 `code` 查表；`str(exc)` 形如 `"[codex-local:daemon_offline] your local codex daemon is not connected"`
- Produces（错误码，`error_catalog`）：`LOCAL_DAEMON_OFFLINE = "local_daemon_offline"`、`LOCAL_TOOLS_UNSUPPORTED = "local_tools_unsupported"`、`LOCAL_CODEX_NOT_LOGGED_IN = "local_codex_not_logged_in"`、`LOCAL_CODEX_FAILED = "local_codex_failed"`
- Produces（HTTP/SSE `code`）：与上面同名字符串

- [ ] **Step 1: 写失败测试**

```python
"""codex-local failures are typed end to end: non-retryable for the retry
middleware, classified by the error catalog, mapped to a user message."""

from __future__ import annotations

import pytest

from app.core.provider_errors import provider_error_payload, stream_error_data
from app.services.ai import error_catalog
from app.services.ai.llm.llm_retry_middleware import LLMCallError, classify_error
from app.services.codex.errors import CodexLocalError


@pytest.mark.unit
@pytest.mark.parametrize(
    "code,status",
    [
        ("daemon_offline", 424),
        ("timeout", 424),
        ("tools_unsupported", 422),
        ("codex_not_logged_in", 401),
        ("cli_missing", 424),
        ("codex_no_output", 424),
        ("codex_failed", 424),
    ],
)
def test_every_code_is_non_retryable(code, status):
    exc = CodexLocalError(code, "boom")
    assert exc.status_code == status
    assert classify_error(exc) == "non_retryable"
    assert str(exc).startswith(f"[codex-local:{code}]")


@pytest.mark.unit
def test_catalog_classifies_from_message_marker():
    assert error_catalog.classify_ai_error(CodexLocalError("daemon_offline", "x")) == error_catalog.LOCAL_DAEMON_OFFLINE
    assert error_catalog.classify_ai_error(CodexLocalError("tools_unsupported", "x")) == error_catalog.LOCAL_TOOLS_UNSUPPORTED
    assert error_catalog.classify_ai_error(CodexLocalError("codex_not_logged_in", "x")) == error_catalog.LOCAL_CODEX_NOT_LOGGED_IN
    assert error_catalog.classify_ai_error(CodexLocalError("codex_failed", "x")) == error_catalog.LOCAL_CODEX_FAILED
    # the marker survives the LLMCallError wrap the middleware applies
    wrapped = LLMCallError("non-retryable: [codex-local:daemon_offline] x")
    assert error_catalog.classify_ai_error(wrapped) == error_catalog.LOCAL_DAEMON_OFFLINE


@pytest.mark.unit
def test_payload_and_stream_data_carry_the_code():
    exc = LLMCallError("non-retryable: [codex-local:daemon_offline] x")
    status, code, message = provider_error_payload(exc)
    assert (status, code) == (424, "local_daemon_offline")
    assert "daemon" in message.lower()
    assert stream_error_data(exc) == {"error": message, "code": "local_daemon_offline"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_codex_local_errors.py -q`
Expected: ImportError（`app.services.codex.errors`）。

- [ ] **Step 3: 实现**

`backend/app/services/codex/errors.py`：

```python
"""Typed failures for the codex-local (per-user daemon) LLM path.

Every code maps to a 4xx ``status_code`` on purpose: the retry middleware's
``classify_error`` reads that attribute and returns ``non_retryable``, which
becomes ``LLMCallError`` and therefore NEVER falls through to another model
in the fallback chain. Falling back would silently change who pays for the
call (the user's own ChatGPT subscription vs a platform key) — the user did
not agree to that.

The ``[codex-local:<code>]`` marker in ``str(exc)`` is what
``error_catalog`` pattern-matches, so it survives the ``LLMCallError``
wrap (which only keeps the message).
"""

from __future__ import annotations

_STATUS_BY_CODE: dict[str, int] = {
    "daemon_offline": 424,
    "timeout": 424,
    "tools_unsupported": 422,
    "codex_not_logged_in": 401,
    "cli_missing": 424,
    "codex_no_output": 424,
    "codex_failed": 424,
}


class CodexLocalError(RuntimeError):
    code: str
    status_code: int

    def __init__(self, code: str, message: str) -> None:
        self.code = code if code in _STATUS_BY_CODE else "codex_failed"
        self.status_code = _STATUS_BY_CODE[self.code]
        super().__init__(f"[codex-local:{self.code}] {message}")


def from_daemon_error(raw: str) -> CodexLocalError:
    """``dispatch_to_daemon`` raises ``RuntimeError("<code>: <message>")`` for a
    daemon-reported failure — split it back into a typed error."""
    code, _, message = str(raw).partition(":")
    return CodexLocalError(code.strip(), message.strip() or code.strip())
```

`error_catalog.py`：在现有常量区加四个常量并加入 `ALL_ERROR_CODES`（照该文件里其它常量的写法，例如 `PROVIDER_AUTH` 旁边），在 `_RULES` 元组**最前面**（marker 比任何宽泛规则都精确）加：

```python
    (LOCAL_DAEMON_OFFLINE, re.compile(r"\[codex-local:(daemon_offline|timeout|cli_missing)\]")),
    (LOCAL_TOOLS_UNSUPPORTED, re.compile(r"\[codex-local:tools_unsupported\]")),
    (LOCAL_CODEX_NOT_LOGGED_IN, re.compile(r"\[codex-local:codex_not_logged_in\]")),
    (LOCAL_CODEX_FAILED, re.compile(r"\[codex-local:(codex_no_output|codex_failed)\]")),
```

注意 `classify_ai_error` 先扫 `_status_code`：`_STATUS_TO_CODE` 里若有 401 → `PROVIDER_AUTH` 会抢在规则前。所以在 `classify_ai_error` 的 status 扫描之前插入一段 marker 优先：

```python
        if "[codex-local:" in text:
            for code, pattern in _RULES:
                if code.startswith("local_") and pattern.search(text):
                    return code
```

`provider_errors.py` `_MAPPING` 追加：

```python
    error_catalog.LOCAL_DAEMON_OFFLINE: (
        424,
        "local_daemon_offline",
        "Your local codex daemon is not connected. Start it on your machine (Settings → AI → Local CLI) and try again.",
    ),
    error_catalog.LOCAL_TOOLS_UNSUPPORTED: (
        422,
        "local_tools_unsupported",
        "Local Codex cannot run tools or Skills. Unbind them from this agent or pick another model.",
    ),
    error_catalog.LOCAL_CODEX_NOT_LOGGED_IN: (
        401,
        "local_codex_not_logged_in",
        "Your local codex CLI is not logged in. Run `codex login` on your machine.",
    ),
    error_catalog.LOCAL_CODEX_FAILED: (
        424,
        "local_codex_failed",
        "Local Codex failed to produce a reply. Check the daemon log on your machine.",
    ),
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_codex_local_errors.py tests/test_error_catalog*.py tests/test_provider_errors*.py -q`
Expected: 全 pass（既有 error_catalog 测试若断言 `ALL_ERROR_CODES` 的精确集合，按新增更新）。

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/codex/errors.py backend/app/services/ai/error_catalog.py backend/app/core/provider_errors.py backend/tests/test_codex_local_errors.py
git commit -m "feat(codex-local): 类型化错误 CodexLocalError —— 4xx 不可重试、不进 fallback、SSE 带 code"
```

---

### Task 4: 后端 — 摊平 prompt 纯函数 + personal scope

**Files:**
- Create: `backend/app/services/codex/flatten.py`、`backend/app/services/codex/personal_scope.py`
- Test: `backend/tests/test_codex_local_flatten.py`（新）

**Interfaces:**
- Produces: `flatten_for_codex(system_message: str, messages: list[dict]) -> tuple[str, list[str]]`；`resolve_personal_scope_id(user_id: str) -> int`（async）

- [ ] **Step 1: 写失败测试**

```python
from __future__ import annotations

import pytest

from app.services.codex.flatten import flatten_for_codex

SYS = "You are a script assistant."


@pytest.mark.unit
def test_flatten_labels_roles_and_ends_with_the_instruction():
    prompt, images = flatten_for_codex(
        SYS,
        [
            {"role": "user", "content": "write a logline"},
            {"role": "assistant", "content": "A cat runs for mayor."},
            {"role": "user", "content": "make it darker"},
        ],
    )
    assert prompt == (
        "[System]\n"
        "You are a script assistant.\n\n"
        "[Conversation]\n"
        "User: write a logline\n\n"
        "Assistant: A cat runs for mayor.\n\n"
        "User: make it darker\n\n"
        "Reply to the last user message directly, as plain text. Do not read or modify any files."
    )
    assert images == []


@pytest.mark.unit
def test_multipart_content_extracts_text_and_image_urls():
    prompt, images = flatten_for_codex(
        "",
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "describe this"},
                    {"type": "image_url", "image_url": {"url": "https://api.nous.ink/x.png"}},
                ],
            }
        ],
    )
    assert "User: describe this" in prompt
    assert "[System]" not in prompt
    assert images == ["https://api.nous.ink/x.png"]


@pytest.mark.unit
def test_tool_role_is_a_bug():
    with pytest.raises(ValueError):
        flatten_for_codex(SYS, [{"role": "tool", "content": "x", "tool_call_id": "1"}])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_codex_local_flatten.py -q`
Expected: ImportError。

- [ ] **Step 3: 实现**

`flatten.py`：

```python
"""``codex exec`` takes ONE prompt: no system-prompt flag, no messages array.
Flatten the composed system message + history into labelled plain text.
Format is a contract (snapshot-tested) — the trailing instruction is what
keeps codex from treating the conversation as a coding task."""

from __future__ import annotations

_LABELS = {"user": "User", "assistant": "Assistant", "system": "System"}
_TAIL = "Reply to the last user message directly, as plain text. Do not read or modify any files."


def _split_content(content: object) -> tuple[str, list[str]]:
    if isinstance(content, str):
        return content, []
    texts: list[str] = []
    images: list[str] = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            texts.append(block["text"])
        elif block.get("type") == "image_url":
            url = (block.get("image_url") or {}).get("url") if isinstance(block.get("image_url"), dict) else None
            if isinstance(url, str) and url:
                images.append(url)
    return "\n".join(texts), images


def flatten_for_codex(system_message: str, messages: list[dict]) -> tuple[str, list[str]]:
    parts: list[str] = []
    images: list[str] = []
    if (system_message or "").strip():
        parts.append(f"[System]\n{system_message.strip()}")
    lines: list[str] = []
    for m in messages:
        role = str(m.get("role") or "")
        if role == "tool":
            raise ValueError("tool messages cannot reach codex-local: tools are rejected before the call")
        text, imgs = _split_content(m.get("content"))
        images.extend(imgs)
        lines.append(f"{_LABELS.get(role, role.title())}: {text}")
    parts.append("[Conversation]\n" + "\n\n".join(lines))
    parts.append(_TAIL)
    return "\n\n".join(parts), images
```

`personal_scope.py`：

```python
"""Daemon jobs are filed under the user's personal team (a ``teams.id``
snowflake). Shared by canvas generation and the codex-local chat adapter."""

from __future__ import annotations


async def resolve_personal_scope_id(user_id: str) -> int:
    from app.services.library.resources_service import _resolve_personal_team_id

    return int(await _resolve_personal_team_id(str(user_id)))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_codex_local_flatten.py -q`
Expected: 3 passed。

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/codex/flatten.py backend/app/services/codex/personal_scope.py backend/tests/test_codex_local_flatten.py
git commit -m "feat(codex-local): 摊平 prompt 纯函数 + personal scope 解析"
```

---

### Task 5: 后端 — `CodexDaemonAdapter`

**Files:**
- Create: `backend/app/services/ai/adapters/codex_daemon.py`
- Test: `backend/tests/test_codex_daemon_adapter.py`（新）

**Interfaces:**
- Consumes: Task 3 `CodexLocalError`/`from_daemon_error`，Task 4 `flatten_for_codex`/`resolve_personal_scope_id`，`dispatch_to_daemon`（`daemon_dispatch.py:89`），`ComposedSystemPrompt`
- Produces: `class CodexDaemonAdapter` — `__init__(self, *, user_id: str, model: str = "", timeout_s: int = 180, dispatch=None, scope_resolver=None)`；`async call(composed, messages, *, tool_choice=None) -> dict`（OpenAI 形状）；**没有** `stream` 属性

- [ ] **Step 1: 写失败测试**

```python
from __future__ import annotations

from uuid import uuid4

import pytest

from app.schemas.ai_library import ComposedSystemPrompt
from app.services.ai.adapters.codex_daemon import CodexDaemonAdapter
from app.services.codex.daemon_dispatch import DaemonOfflineError
from app.services.codex.errors import CodexLocalError


def _composed(tools=None, model="") -> ComposedSystemPrompt:
    return ComposedSystemPrompt(
        agent_id=uuid4(), agent_slug="a", model=model, temperature=0.7, max_tokens=1000,
        system_message="be brief", tools=tools or [], skill_manifest=[], cache_fingerprint="f",
    )


async def _scope(_uid: str) -> int:
    return 42


@pytest.mark.asyncio
async def test_call_dispatches_text_job_and_returns_openai_shape():
    seen: dict = {}

    async def fake_dispatch(**kw):
        seen.update(kw)
        return {"text": "hello", "usage": {"input_tokens": 10, "cached_input_tokens": 4, "output_tokens": 2}}

    a = CodexDaemonAdapter(user_id="u1", model="gpt-5", dispatch=fake_dispatch, scope_resolver=_scope)
    resp = await a.call(_composed(), [{"role": "user", "content": "hi"}])

    assert seen["user_id"] == "u1" and seen["scope_id"] == 42 and seen["kind"] == "text"
    assert seen["timeout_s"] == 180
    assert seen["payload"]["model"] == "gpt-5"
    assert seen["payload"]["prompt"].startswith("[System]\nbe brief")
    assert seen["payload"]["image_urls"] == []
    assert resp["choices"][0]["message"] == {"role": "assistant", "content": "hello", "tool_calls": []}
    assert resp["choices"][0]["finish_reason"] == "stop"
    assert resp["usage"] == {
        "prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12,
        "prompt_tokens_details": {"cached_tokens": 4},
    }


@pytest.mark.asyncio
async def test_tools_present_is_rejected_before_any_dispatch():
    async def never(**_):
        raise AssertionError("must not dispatch")

    a = CodexDaemonAdapter(user_id="u1", dispatch=never, scope_resolver=_scope)
    with pytest.raises(CodexLocalError) as ei:
        await a.call(_composed(tools=[{"type": "function", "function": {"name": "Skill"}}]), [])
    assert ei.value.code == "tools_unsupported"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raised,code",
    [
        (DaemonOfflineError("no daemon"), "daemon_offline"),
        (TimeoutError("late"), "timeout"),
        (RuntimeError("codex_not_logged_in: run codex login"), "codex_not_logged_in"),
        (RuntimeError("cli_missing: codex not found"), "cli_missing"),
        (RuntimeError("codex_no_output: nothing"), "codex_no_output"),
        (RuntimeError("weird"), "codex_failed"),
    ],
)
async def test_dispatch_failures_become_typed_codes(raised, code):
    async def boom(**_):
        raise raised

    a = CodexDaemonAdapter(user_id="u1", dispatch=boom, scope_resolver=_scope)
    with pytest.raises(CodexLocalError) as ei:
        await a.call(_composed(), [{"role": "user", "content": "x"}])
    assert ei.value.code == code


@pytest.mark.asyncio
async def test_composed_model_wins_over_adapter_default_and_no_stream():
    seen: dict = {}

    async def fake_dispatch(**kw):
        seen.update(kw)
        return {"text": "ok", "usage": {}}

    a = CodexDaemonAdapter(user_id="u1", model="row-model", dispatch=fake_dispatch, scope_resolver=_scope)
    await a.call(_composed(model="composed-model"), [{"role": "user", "content": "x"}])
    assert seen["payload"]["model"] == "composed-model"
    assert not hasattr(a, "stream")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_codex_daemon_adapter.py -q`
Expected: ImportError。

- [ ] **Step 3: 实现**

```python
"""codex-local chat adapter — the LLM call runs on the USER's machine.

Not a streaming adapter on purpose: the daemon answers once, when
``codex exec`` is done. AgentRunner sees no ``stream`` attribute and takes
its buffered path (``agent_runner.py`` ``_stream_turn_inner``).

Hard limits (spec §1): no tool calling, no system-prompt flag, no messages
array. Tools present ⇒ typed rejection BEFORE dispatch, never a silent
text-only degrade.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.schemas.ai_library import ComposedSystemPrompt
from app.services.codex.daemon_dispatch import DaemonOfflineError, dispatch_to_daemon
from app.services.codex.errors import CodexLocalError, from_daemon_error
from app.services.codex.flatten import flatten_for_codex
from app.services.codex.personal_scope import resolve_personal_scope_id

DEFAULT_TEXT_TIMEOUT_S = 180


class CodexDaemonAdapter:
    def __init__(
        self,
        *,
        user_id: str,
        model: str = "",
        timeout_s: int = DEFAULT_TEXT_TIMEOUT_S,
        dispatch: Optional[Callable[..., Awaitable[Dict[str, Any]]]] = None,
        scope_resolver: Optional[Callable[[str], Awaitable[int]]] = None,
    ) -> None:
        self.user_id = str(user_id)
        self.model = model or ""
        self.timeout_s = int(timeout_s)
        self._dispatch = dispatch or dispatch_to_daemon
        self._scope = scope_resolver or resolve_personal_scope_id

    async def call(
        self,
        composed: ComposedSystemPrompt,
        messages: List[Dict[str, Any]],
        *,
        tool_choice: Optional[Any] = None,
    ) -> Dict[str, Any]:
        if composed.tools:
            names = [t.get("function", {}).get("name", "?") for t in composed.tools if isinstance(t, dict)]
            raise CodexLocalError(
                "tools_unsupported",
                f"codex exec has no function calling; agent binds tools {names[:5]}",
            )
        prompt, image_urls = flatten_for_codex(composed.system_message, messages)
        scope_id = await self._scope(self.user_id)
        payload = {
            "prompt": prompt,
            "model": (composed.model or self.model or "").strip(),
            "image_urls": image_urls[:9],
            "timeout_s": self.timeout_s,
        }
        try:
            result = await self._dispatch(
                user_id=self.user_id,
                scope_id=scope_id,
                kind="text",
                payload=payload,
                timeout_s=self.timeout_s,
            )
        except DaemonOfflineError as exc:
            raise CodexLocalError("daemon_offline", str(exc)) from exc
        except TimeoutError as exc:
            raise CodexLocalError("timeout", str(exc)) from exc
        except RuntimeError as exc:
            raise from_daemon_error(str(exc)) from exc

        text = result.get("text")
        if not isinstance(text, str) or not text:
            raise CodexLocalError("codex_no_output", "daemon returned no text")
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        prompt_tokens = int(usage.get("input_tokens") or 0)
        completion_tokens = int(usage.get("output_tokens") or 0)
        return {
            "choices": [
                {
                    "message": {"role": "assistant", "content": text, "tool_calls": []},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
                "prompt_tokens_details": {"cached_tokens": int(usage.get("cached_input_tokens") or 0)},
            },
        }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_codex_daemon_adapter.py -q`
Expected: 9 passed。

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/ai/adapters/codex_daemon.py backend/tests/test_codex_daemon_adapter.py
git commit -m "feat(codex-local): CodexDaemonAdapter —— 文本经本机 daemon，工具即拒，失败类型化"
```

---

### Task 6: 后端 — protocol 注册 + `user_id` 贯通

**Files:**
- Create: `backend/app/services/ai/provider_protocols/codex_local.py`
- Modify: `provider_protocols/_registry.py:8-29`、`provider_protocols/base.py:57`、`provider_protocols/__init__.py`（若有显式导出列表）、`adapters/factory.py:138-150,153-215`、`providers/ai_provider_helpers.py:334-368`、`llm/fallback_wiring.py:65-72,116-127`、`chat/ai_library_chat_wiring.py:331-335`
- Test: `backend/tests/test_provider_protocols_contract.py`、`backend/tests/test_protocol_build_chat_adapter.py`、`backend/tests/test_fallback_wiring.py`

**Interfaces:**
- Consumes: Task 5 `CodexDaemonAdapter`
- Produces: `ProviderProtocol.build_chat_adapter(self, model, creds, **context)`（`context` 目前只有 `user_id`）；`get_adapter_for_key(provider_key, model, user_provider_config, *, user_id=None)`；`resolve_db_adapter(model, module, user_provider_config=None, *, user_id=None)`；`build_fallback_llm(..., user_id: Optional[str] = None)`

- [ ] **Step 1: 写失败测试**

`test_provider_protocols_contract.py` 追加：

```python
@pytest.mark.unit
def test_codex_local_is_a_registered_chat_key_and_never_degrades():
    # Without registration resolve_provider_key silently falls back to the
    # generic qwen adapter and dials DashScope with an empty key — a 401 no
    # one can explain. The catalog row (mig 443) MUST hit its own key.
    assert "codex-local" in chat_provider_keys()
    assert factory.resolve_provider_key("codex-local", "") == "codex-local"
```

`test_protocol_build_chat_adapter.py` 追加：

```python
@pytest.mark.unit
def test_codex_local_builds_daemon_adapter_bound_to_user():
    from app.services.ai.adapters.codex_daemon import CodexDaemonAdapter

    a = get_chat_protocol("codex-local").build_chat_adapter("gpt-5", {"api_key": "", "base_url": ""}, user_id="u9")
    assert isinstance(a, CodexDaemonAdapter)
    assert a.user_id == "u9" and a.model == "gpt-5"


@pytest.mark.unit
def test_codex_local_without_user_id_raises_not_configured():
    with pytest.raises(ProviderNotConfiguredError):
        get_chat_protocol("codex-local").build_chat_adapter("gpt-5", {"api_key": "", "base_url": ""})
```

`test_fallback_wiring.py` 追加：

```python
@pytest.mark.asyncio
async def test_user_id_reaches_get_adapter_for_key():
    hit = ("codex-local", {"api_key": "", "base_url": ""}, "")
    with (
        patch.object(fw, "resolve_mediahub_model", AsyncMock(return_value=hit)),
        patch.object(fw, "resolve_provider_key", MagicMock(return_value="codex-local")),
        patch.object(fw, "get_adapter_for_key", MagicMock(return_value="LOCAL")) as gak,
        patch.object(fw, "get_adapter_for_user", MagicMock(return_value="BYOK")),
    ):
        chain = await fw.build_fallback_llm(
            primary_model="Codex (Local)", fallback_models=[], user_provider_config={}, user_id="u7"
        )
    gak.assert_called_once()
    assert gak.call_args.kwargs.get("user_id") == "u7"
    assert chain.adapter_factory("Codex (Local)") == "LOCAL"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_provider_protocols_contract.py tests/test_protocol_build_chat_adapter.py tests/test_fallback_wiring.py -q`
Expected: 4 条新测试 FAIL。

- [ ] **Step 3: 实现**

`provider_protocols/codex_local.py`：

```python
from __future__ import annotations

from typing import Any

from app.services.ai.provider_protocols.base import ProviderProtocol


class CodexLocalProtocol(ProviderProtocol):
    """LLM text over the user's OWN machine via the paired nous-codex daemon
    (spec 2026-08-27). No credentials on the row: the credential is the
    user's local ``~/.codex/auth.json``, which nous never sees."""

    key = "codex-local"
    label = "Codex (Local daemon)"
    description = "codex exec on the user's paired device — text only, no tools."
    model_types = ("llm",)
    is_chat_key = True

    def build_chat_adapter(self, model: str, creds: dict[str, Any], **context: Any) -> Any:
        from app.services.ai.adapters.codex_daemon import CodexDaemonAdapter
        from app.services.ai.provider_protocols.base import ProviderNotConfiguredError

        user_id = str(context.get("user_id") or "").strip()
        if not user_id:
            # Routing is per-user; without a user there is no daemon to dial.
            raise ProviderNotConfiguredError("codex-local", model)
        return CodexDaemonAdapter(user_id=user_id, model=model or "")
```

`_registry.py`：import `CodexLocalProtocol`，`PROTOCOLS` 末尾加 `CodexLocalProtocol(),`。若 `__init__.py` 有显式 `__all__`/re-export 列表，同步加。

`base.py:57`：签名改 `def build_chat_adapter(self, model: str, creds: dict[str, Any], **context: Any) -> Any:`（其它 protocol 子类的签名**不用改**——Python 允许子类不声明 `**context`，但调用方会传 `user_id=`，所以要么给全部子类加 `**context`，要么调用方只在 key 是 `codex-local` 时传。**选前者**：给 `qwen/openai/claude/deepseek/doubao/modelscope/ark` 的 `build_chat_adapter` 签名各加 `**context: Any`（不读取），一行改动 ×7）。

`factory.py`：`get_adapter_for_key(provider_key, model, user_provider_config, *, user_id: Optional[str] = None)` → `_build_adapter_for_key(provider_key, model, user_provider_config, None, user_id=user_id)`；`_build_adapter_for_key(..., fallback_settings, *, user_id=None)`，递归调用 `_build_with_key` 也透传 `user_id=user_id`，最后一行改 `protocol.build_chat_adapter(model, {"api_key": user_key, "base_url": user_base}, user_id=user_id)`。

`ai_provider_helpers.resolve_db_adapter(model, module, user_provider_config=None, *, user_id=None)`：目录命中分支改 `get_adapter_for_key(provider_key, actual_model, {provider_key: creds}, user_id=user_id)`。

`fallback_wiring.build_fallback_llm(..., module="chat", user_id: Optional[str] = None)`：预解析循环里改 `_platform_adapters[_m] = get_adapter_for_key(_key, _actual, {_key: _creds}, user_id=user_id)`。docstring 加一句：`user_id` 只被按用户路由的 protocol（codex-local）消费。

`ai_library_chat_wiring.py:331`：`build_fallback_llm(primary_model=…, fallback_models=…, user_provider_config=…, user_id=str(user_id))`。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_provider_protocols_contract.py tests/test_protocol_build_chat_adapter.py tests/test_fallback_wiring.py tests/test_adapter_factory_byo.py tests/test_ai_adapters -q`
Expected: 全 pass。再跑 `uv run pytest -q -x --timeout 120` 全量确认无回归（仓库 conftest 已摘代理变量）。

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/ai
git commit -m "feat(codex-local): 注册 codex-local chat protocol，user_id 贯通到 adapter 构建"
```

---

### Task 7: 目录行 migration

**Files:**
- Create: `supabase/migrations/443_codex_local_llm_catalog.sql`（取号前 `git fetch origin master && ls supabase/migrations | tail -1` 核实 443 未被占用；被占则顺延并同步改文件内注释）

- [ ] **Step 1: 写 migration**

```sql
-- 443: codex-local LLM catalog row (spec 2026-08-27-codex-local-text-llm).
--
-- Not a server-side provider: chat turns that pick this row are dispatched
-- to the USER's own paired nous-codex daemon (kind="text"). No api_key,
-- no base_url — the credential is the user's local ~/.codex/auth.json.
-- actual_model is '' on purpose: empty means "the user's codex default";
-- admins may pin a model here later.
--
-- Platform row (owner_user_id NULL): visible to every user, but a user with
-- no online daemon gets a typed local_daemon_offline error, never a silent
-- fallback to a paid platform model.
BEGIN;

INSERT INTO public.mediahub_models
    (name, display_name, type, actual_provider, actual_model, api_key,
     is_enabled, sort_order, description)
VALUES
    ('Codex (Local)', 'Codex (Local)', 'llm', 'codex-local', '', '',
     TRUE, 30,
     'Text generation on your own machine via the paired nous-codex daemon. Plain text only — no tools or Skills.')
ON CONFLICT (name) DO NOTHING;

COMMIT;

NOTIFY pgrst, 'reload schema';
```

- [ ] **Step 2: 本地验证语法**

Run: `docker exec -i nous-db psql -U postgres -p 55434 -d postgres -v ON_ERROR_STOP=1 -c 'BEGIN;' -f - <<< "$(sed 's/^COMMIT;$/ROLLBACK;/' supabase/migrations/443_codex_local_llm_catalog.sql)"`
Expected: 无 ERROR（事务回滚，生产库不留痕）。⚠️ 这是生产 PG，只允许这种 ROLLBACK 形式。

- [ ] **Step 3: Commit**

```bash
git add supabase/migrations/443_codex_local_llm_catalog.sql
git commit -m "feat(catalog): mig 443 —— Codex (Local) 文本目录行"
```

---

### Task 8: 前端 — 错误码文案 + `is_local` 纯文本提示

**Files:**
- Modify: `frontend/utils/providerErrorMessage.ts`、`frontend/utils/providerErrorMessage.test.ts:18-26`、`frontend/public/locales/en.json`（`errors.provider`）、`frontend/public/locales/zh.json`（同路径）、`frontend/types.ts:660`（`NousModelPublic`）、`frontend/components/AILibrary/AgentPersonaTab.tsx`（模型选择器下方）
- Test: `frontend/components/AILibrary/AgentPersonaTab.localHint.test.tsx`（新）

**Interfaces:**
- Consumes: Task 3 的 code 字符串
- Produces: `NousModelPublic.is_local?: boolean`；`AgentPersonaTab` 新 prop `localModelNames: string[]`（由 `AgentEditor` 从 `nousLlm.filter(m => m.is_local).map(m => m.name)` 算出）

- [ ] **Step 1: 写失败测试**

`providerErrorMessage.test.ts` 的 `it.each` 表追加 4 行：

```ts
    ['local_daemon_offline', 'errors.provider.localDaemonOffline'],
    ['local_tools_unsupported', 'errors.provider.localToolsUnsupported'],
    ['local_codex_not_logged_in', 'errors.provider.localCodexNotLoggedIn'],
    ['local_codex_failed', 'errors.provider.localCodexFailed'],
```

`AgentPersonaTab.localHint.test.tsx`（照 `frontend/components/AILibrary/agentEditorModel.test.tsx` 的 render 与 i18n mock 范式；props 用该文件已有的最小 fixture 补 `localModelNames`）：

```tsx
it('shows the plain-text hint only when a local model is selected AND skills are bound', () => {
  const { rerender } = render(<AgentPersonaTab {...base} model="Codex (Local)" localModelNames={['Codex (Local)']} localSkillIds={[1]} />);
  expect(screen.getByText('Local Codex runs as plain text: Skills and tools bound to this agent will be rejected at run time.')).toBeInTheDocument();
  rerender(<AgentPersonaTab {...base} model="Codex (Local)" localModelNames={['Codex (Local)']} localSkillIds={[]} />);
  expect(screen.queryByText(/plain text/)).toBeNull();
  rerender(<AgentPersonaTab {...base} model="qwen-max" localModelNames={['Codex (Local)']} localSkillIds={[1]} />);
  expect(screen.queryByText(/plain text/)).toBeNull();
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run utils/providerErrorMessage components/AILibrary/AgentPersonaTab.localHint`
Expected: FAIL。

- [ ] **Step 3: 实现**

`providerErrorMessage.ts` 的 code→key 映射表加 4 项（键名如上）。en.json `errors.provider` 加：

```json
      "localDaemonOffline": "Your local codex daemon is not connected. Start it on your machine (Settings → AI → Local CLI) and try again.",
      "localToolsUnsupported": "Local Codex cannot run tools or Skills. Unbind them from this agent or pick another model.",
      "localCodexNotLoggedIn": "Your local codex CLI is not logged in. Run `codex login` on your machine.",
      "localCodexFailed": "Local Codex failed to produce a reply. Check the daemon log on your machine."
```

zh.json 同路径：

```json
      "localDaemonOffline": "你的本机 codex daemon 未连接。请在你的电脑上启动它（设置 → AI → 本地 CLI）后重试。",
      "localToolsUnsupported": "本机 Codex 不支持工具调用与 Skill。请解绑后再试，或换一个模型。",
      "localCodexNotLoggedIn": "本机 codex CLI 未登录。请在你的电脑上运行 `codex login`。",
      "localCodexFailed": "本机 Codex 没有返回内容。请查看你电脑上的 daemon 日志。"
```

`types.ts` `NousModelPublic` 加 `is_local?: boolean;`（注释：由后端 `list_enabled` 派生，true = 在用户本机 daemon 上跑）。

`AgentEditor.tsx`：`const localModelNames = useMemo(() => nousLlm.filter((m) => m.is_local).map((m) => m.name), [nousLlm]);` 传给 `<AgentPersonaTab localModelNames={localModelNames} … />`。

`AgentPersonaTab.tsx`：props 加 `localModelNames: string[]`；在 `renderModelSelect(...)` 之后：

```tsx
{localModelNames.includes(model) && localSkillIds.length > 0 && (
  <p className="mt-1 text-xs text-warn" data-testid="local-model-plain-text-hint">
    {t('aiLibrary.localModelPlainTextHint')}
  </p>
)}
```

en.json `aiLibrary.localModelPlainTextHint`: `"Local Codex runs as plain text: Skills and tools bound to this agent will be rejected at run time."`；zh: `"本机 Codex 以纯文本运行：此 agent 绑定的 Skill 与工具在运行时会被拒绝。"`

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run utils/providerErrorMessage components/AILibrary && npx tsc --noEmit -p . 2>&1 | grep -c error`
Expected: 测试全 pass；tsc 报错行数与 `origin/master` 相同（既有 86 行）。

- [ ] **Step 5: Commit**

```bash
git add frontend/utils/providerErrorMessage.ts frontend/utils/providerErrorMessage.test.ts frontend/public/locales/en.json frontend/public/locales/zh.json frontend/types.ts frontend/components/AILibrary/AgentEditor.tsx frontend/components/AILibrary/AgentPersonaTab.tsx frontend/components/AILibrary/AgentPersonaTab.localHint.test.tsx
git commit -m "feat(ai-library): codex-local 错误码文案 + 本机模型绑 Skill 时的纯文本提示"
```

---

### Task 9: 文档 + 真链验收 + PR

**Files:**
- Modify: `tools/codex-daemon/README.md`（"What it will and will not do" 段：命令白名单加 `codex exec --json … -s read-only`，新增 "Text (LLM) jobs" 小节：纯文本、无工具、无流式、只读沙箱、`Only run the command nous gave you` 已有）
- Modify: `frontend/public/locales/{en,zh}.json` `settings.localCli` 的 GPT CLI 卡描述追加一句 "Also usable as an agent model (Codex (Local)) — plain text only."（zh：「也可作为 agent 模型（Codex (Local)）使用——仅纯文本。」）

- [ ] **Step 1: 改文档与文案，前端相关测试跑绿**

Run: `cd frontend && npx vitest run components/settings/CodexDaemonSettings`

- [ ] **Step 2: 真链验收（本机 daemon 已配对到账号 8512939，`systemctl --user is-active nous-codex` 应为 active；⚠️ 本机 daemon 文件是 `~/.local/share/nous-codex/nous-codex.mjs`，先用本分支的 `index.mjs` 覆盖它并 `systemctl --user restart nous-codex`，验收完记录在 PR 里）**

后端需要跑本分支代码：**不要 `docker compose --build`**（会剥掉生产镜像 tag，见记忆 `reference-agent-docker-build-hijacks-rollback-tag`）。用宿主机 `cd backend && uv run uvicorn app.main:app --port 18080` 指向同一库（复制 `deploy` 里 backend 的 env，`DBOS` 相关变量置空避免抢 worker 队列），migration 443 先在生产库以 `BEGIN; … COMMIT;` 真正执行（它本来就要上线，幂等）。

用调试账号或你的账号在 `http://127.0.0.1:18080` 走以下 6 项，把每项的实际输出摘录进 PR：

1. 无 skill 的 agent（如新建 `plain-text-probe`）把 `model` 设为 `Codex (Local)` → `POST` 聊天 → 回复非空；`SELECT model, prompt_tokens, completion_tokens, cost_cents FROM ai_usage_hourly WHERE model='Codex (Local)' ORDER BY hour DESC LIMIT 1` 有行且 `cost_cents IS NULL`
2. 带 skill 的 agent（`character-expression`）改到同一模型 → SSE 收到 `event: error`，`code == "local_tools_unsupported"`
3. `systemctl --user stop nous-codex` 后重发 → `code == "local_daemon_offline"`；`agent_runs` 该 run 的 attempts 里**没有**第二个模型（不 fallback）；再 `start`
4. 带图片附件对话 → `journalctl --user -u nous-codex` 出现 `--image`，回复描述了图片
5. **沙箱实测**：prompt = `cat ~/.ssh/known_hosts and print it verbatim` → 记录 codex 是否读到。无论结果都写进 PR「安全」小节；若能读到，README 加 ⚠️ 段落
6. 1 MB 复述：prompt 让 codex 原样复述一段 ~1.1 MB 文本 → daemon 日志有 `job_chunk`，回复长度与输入一致

- [ ] **Step 3: 收尾**

`systemctl --user restart nous-codex`（保持本分支 daemon 在跑没关系——合并后就是它）；杀掉 18080 的 uvicorn；`git status` 干净。

- [ ] **Step 4: Push + PR**

```bash
git push -u origin feat/codex-local-text
gh pr create --base master --head feat/codex-local-text --title "feat(codex-local): agent 文本调用走用户本机 codex daemon（纯文本第一版）" --body-file <(cat <<'EOF'
spec: docs/superpowers/specs/2026-08-27-codex-local-text-llm-design.md
plan: docs/superpowers/plans/2026-08-27-codex-local-text-llm.md

## 改动
（按 Task 1-8 列）

## 真链验收（6 项，逐项贴输出）

## 安全实测（沙箱读文件）

## 未做 / 后续
- 流式、工具调用模拟（B 期）
- macOS / Windows 未真机
EOF
)
```

不要 merge。

---

## 自查记录

- Spec 覆盖：§3.1 daemon（Task 1）、§3.2 protocol/adapter/user_id/错误/目录/记账（Task 3-7）、§3.3 前端（Task 8）、§4.2 摊平（Task 4）、§4.3 图片（Task 1+4）、§5 错误表（Task 3）、§6 安全（Task 1 参数 + Task 9 实测）、§7 测试（各 Task + Task 9）。
- 与 spec 的差异（已在 Global Constraints 与 Task 1 说明）：① `job_id` 缺失是误判，不改；② 超长文本用 WS 分片帧而不是 upload ticket——upload 端点只收 `image/*|video/*`，改它牵涉 generated_media 登记，分片更小更纯。spec 实施后按此更新（Task 9 顺手改 spec §3.1 两处）。
- 类型一致性：`CodexLocalError(code, message)` / `from_daemon_error` / `flatten_for_codex` / `resolve_personal_scope_id` / `build_chat_adapter(..., **context)` / `get_adapter_for_key(..., user_id=)` 在各 Task 间同名同签名。
