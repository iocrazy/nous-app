# 出图生成请求契约 — P3（daemon 客户端 + 版本闸门）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 codex-local 的 `quality` 旋钮真正到达 `gpt-image-2-skill`（daemon 转发 `--quality`），并用版本闸门保证「能力声明为 True」只在 daemon 真能兑现时成立——老 daemon 收到的是类型化的「请升级」，而不是静默吞掉。

**Architecture:** daemon 0.4.0 把图片 argv 抽成纯函数 `buildImageArgs` 并转发 `--quality`；服务器把文本链已验证的版本判定（`_parse_version` / `_version_at_least` / `_reported_daemon_version`）抽到共享模块 `app/services/codex/daemon_version.py`，`dispatch_to_daemon` 对 **codex 引擎的 image/video job** 加 `MIN_IMAGE_DAEMON_VERSION = "0.4.0"` 闸门（`None` = 离线 → 跳过闸门让后面报真实的 offline；`UNVERSIONED`/低版本 → `DaemonUpdateRequiredError`）；最后把 `codex-local` 的 `quality` 翻回 `True`——**与闸门同一个 PR**，因为只有「<0.4.0 会被类型化拒绝」成立后它才是真话。

**Tech Stack:** Python 3.13 / FastAPI / pytest（后端 `cd backend && uv run pytest`）· Node 22 `node --test`（daemon `cd tools/codex-daemon && npm test`）。

**Spec:** `docs/superpowers/specs/2026-08-29-generation-request-contract-design.md` §5（兼容与版本）、§9 裁决「旧 daemon 降级双发一版再拒绝」——P1 是双发那一版，**本期是拒绝那一版**。

**前置条件（已满足，2026-09-02）**：用户账号真栈验收通过——codex-local 16:9 → `canvas_run` 归因 + 四键 + `honored=true`（1672×941）。P2 终审要求此验收先于 P3，理由：改 daemon payload 之前先证明现协议下记录链是通的，否则「没记录」与「新 payload 坏了」混淆。

## Global Constraints

- **P1/P2/P4 已上线，不重做**：`GenerationRequest.to_codex_daemon_payload` 已发 `ratio` + `size`（由 ratio 换算）+ `quality` + `model`（`engine_model`）+ 画幅短语并入 prompt；四键落库；UI 按能力渲染。
- **`size` 键保留，不删**——spec §5 字面说「下一版删旧键」，但删掉 `size` 会迫使比例→尺寸表在 JS 里复制一份（`index.mjs` 无法 import `aspect.py`），恰恰复活本契约要消灭的双表漂移。实质目标（quality 真转发 + 版本可判）不需要删它。`ratio` 继续随 payload 下发供 daemon 日志/归因，daemon **不消费**它。此偏离要在 PR 正文对着 spec 说明。
- **闸门只罩 `engine == "codex"` 的 job**——dreamina 的 payload 本期未变，罩它只会无理由挡老 daemon。
- **能力翻转与闸门同一 PR**（见 Architecture）。拆开发布会出现「能力说支持、老 daemon 静默吞」的窗口，即 P4 刚删掉的假开关原样复活。
- 版本判定**不写第二份**：抽共享模块，文本链改 import，行为零变化——「两个必须一致的判定」是本契约点名的 bug 族。
- 拒绝信息必须**用户可见且含升级指引**（英文，走 CLAUDE.md「触发路径必须类型化失败回显」）：它会经 DBOS 落到 `task_tracking.error_msg`。
- 后端 lint 门禁 **black / isort / flake8（无 ruff）**；daemon 测试 `node --test`；`index.test.mjs` 有「`DAEMON_VERSION` 与 `package.json` 一致」的既有测试，两处必须同改。
- 不改 schema；无 `text()` 裸 SQL。
- commit 用 `git -c user.email=ezufofoti59@gmail.com -c user.name=heygo`；分支从 `origin/master` 建、`git switch -c`、**不要 stash/pop**。
- 每个任务的关键测试**看着它红过**；闸门与 argv 各做一次突变验证。

---

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `tools/codex-daemon/index.mjs` | `buildImageArgs` 纯函数（导出）+ `runImageJob` 改用它 + `--quality` + `DAEMON_VERSION='0.4.0'` | 修改 |
| `tools/codex-daemon/package.json` | `version: 0.4.0` | 修改 |
| `tools/codex-daemon/index.test.mjs` | argv 测试 | 修改 |
| `backend/app/services/codex/daemon_version.py` | `parse_version` / `version_at_least` / `UNVERSIONED` / `reported_daemon_version` | 新建（从 adapters 迁出） |
| `backend/app/services/ai/adapters/codex_daemon.py` | 改 import，私有名保留为别名 | 修改 |
| `backend/app/services/codex/daemon_dispatch.py` | `MIN_IMAGE_DAEMON_VERSION`、`DaemonUpdateRequiredError`、闸门、`daemon_version` 注入点 | 修改 |
| `backend/app/services/ai/provider_protocols/codex_local.py` | `quality: True` + 注释改事实陈述 | 修改 |
| `backend/tests/services/codex/test_daemon_version.py` | | 新建 |
| `backend/tests/test_codex_daemon_dispatch.py` | 闸门测试 | 修改 |
| `backend/tests/test_provider_capabilities.py` | 断言跟改 | 修改 |
| `backend/tests/services/generation/test_request.py` | quality 端到端 | 修改 |

---

### Task 1: daemon 0.4.0 —— `buildImageArgs` 纯函数 + `--quality`

**Files:**
- Modify: `tools/codex-daemon/index.mjs:362-382`（`runImageJob`）、`:41`（`DAEMON_VERSION`）
- Modify: `tools/codex-daemon/package.json:3`
- Test: `tools/codex-daemon/index.test.mjs`

**Interfaces:**
- Produces: `export function buildImageArgs({ prompt, size, quality, model, refs, out }) -> string[]`；`runImageJob` 只做下载 refs + 调它 + `runCommand`。`DAEMON_VERSION === '0.4.0'`。

- [ ] **Step 1: 写失败测试（追加到 `index.test.mjs`，同文件已有 `test`/`assert` 导入）**

```js
import { buildImageArgs, DAEMON_VERSION } from './index.mjs';

test('buildImageArgs: forwards --quality when the server sends one', () => {
  const args = buildImageArgs({
    prompt: 'a cat', size: '1536x1024', quality: 'high', model: '', refs: [], out: '/w/out.png',
  });
  const i = args.indexOf('--quality');
  assert.notEqual(i, -1, 'quality never reached the CLI — the knob P4 shows is a fake switch');
  assert.equal(args[i + 1], 'high');
});

test('buildImageArgs: omits --quality entirely when absent — the CLI default is the honest choice', () => {
  const args = buildImageArgs({ prompt: 'a cat', size: '1024x1024', model: '', refs: [], out: '/w/out.png' });
  assert.equal(args.includes('--quality'), false);
  // null / '' are "not set", not "set to nothing"
  for (const q of [null, '']) {
    const a = buildImageArgs({ prompt: 'p', size: '1024x1024', quality: q, model: '', refs: [], out: '/o' });
    assert.equal(a.includes('--quality'), false, `quality=${JSON.stringify(q)}`);
  }
});

test('buildImageArgs: keeps the 0.3.0 contract for size/model/refs byte-for-byte', () => {
  const args = buildImageArgs({
    prompt: 'p', size: '1024x1536', model: 'gpt-image-2', refs: ['/w/ref0.png', '/w/ref1.png'], out: '/w/out.png',
  });
  assert.deepEqual(args.slice(0, 2), ['images', 'edit']);          // refs ⇒ edit
  assert.equal(args[args.indexOf('--size') + 1], '1024x1536');
  assert.equal(args[args.indexOf('--model') + 1], 'gpt-image-2');
  assert.deepEqual(args.filter((a, k) => args[k - 1] === '--ref-image'), ['/w/ref0.png', '/w/ref1.png']);
  const noRefs = buildImageArgs({ prompt: 'p', size: '1024x1024', model: '', refs: [], out: '/o' });
  assert.deepEqual(noRefs.slice(0, 2), ['images', 'generate']);   // no refs ⇒ generate
  assert.equal(noRefs.includes('--model'), false);                 // empty model ⇒ CLI default
});

test('buildImageArgs: falls back to 1024x1024 when size is missing (old-server safety)', () => {
  const args = buildImageArgs({ prompt: 'p', model: '', refs: [], out: '/o' });
  assert.equal(args[args.indexOf('--size') + 1], '1024x1024');
});

test('DAEMON_VERSION is 0.4.0 — image jobs are gated on it server-side', () => {
  assert.equal(DAEMON_VERSION, '0.4.0');
});
```

- [ ] **Step 2: RED** — `cd tools/codex-daemon && npm test`。Expected：`buildImageArgs` 不是导出（SyntaxError/undefined）；版本断言 `'0.3.0' !== '0.4.0'`。

- [ ] **Step 3: 实现**

```js
/** Pure argv for `gpt-image-2-skill images …`. Exported so the argv can be
 *  pinned without spawning anything. `size` stays the canonical shape key
 *  (the server derives it from `ratio`; this side never owns a ratio table).
 *  `quality` is forwarded only when set — null/'' mean "not requested", and
 *  the CLI default is the honest answer for that. */
export function buildImageArgs({ prompt, size, quality, model, refs, out }) {
  const args = [
    'images',
    refs.length ? 'edit' : 'generate',
    '--prompt', String(prompt ?? ''),
    '--out', out,
    '--size', String(size || '1024x1024'),
    '--format', 'png',
    '--background', 'opaque',
  ];
  if (quality) args.push('--quality', String(quality));
  if (model) args.push('--model', String(model));
  for (const ref of refs) args.push('--ref-image', ref);
  return args;
}

async function runImageJob(payload, workDir) {
  const out = path.join(workDir, 'out.png');
  const refs = [];
  for (const [i, url] of (payload.ref_urls ?? []).slice(0, 9).entries()) {
    refs.push(await downloadRef(url, workDir, i));
  }
  await runCommand('gpt-image-2-skill', buildImageArgs({
    prompt: payload.prompt, size: payload.size, quality: payload.quality,
    model: payload.model, refs, out,
  }));
  return out;
}
```

`DAEMON_VERSION = '0.4.0'`；`package.json` `"version": "0.4.0"`。`DAEMON_VERSION` 上方那段注释里提到 `MIN_TEXT_DAEMON_VERSION` 的地方，补一句「image jobs are gated by `MIN_IMAGE_DAEMON_VERSION` in `daemon_dispatch.py`」。

- [ ] **Step 4: GREEN + 突变** — `npm test` 全绿；把 `if (quality) args.push(...)` 那行删掉，quality 测试转红；还原再绿。贴两次输出。

- [ ] **Step 5: Commit**

```bash
git add tools/codex-daemon/index.mjs tools/codex-daemon/index.test.mjs tools/codex-daemon/package.json
git -c user.email=ezufofoti59@gmail.com -c user.name=heygo commit -m "feat(daemon): 0.4.0 —— 图片 argv 抽成纯函数并转发 --quality;size 仍是权威形状键"
```

---

### Task 2: 版本判定抽到共享模块

**Files:**
- Create: `backend/app/services/codex/daemon_version.py`
- Modify: `backend/app/services/ai/adapters/codex_daemon.py:78-130`（删定义，改 import）
- Test: `backend/tests/services/codex/test_daemon_version.py`（新建）；既有 adapters 测试不动、必须全绿

**Interfaces:**
- Produces（公开名）：`UNVERSIONED = "0.0.0"`、`parse_version(raw) -> tuple[int,int,int]`、`version_at_least(reported, minimum) -> bool`、`async reported_daemon_version(user_id) -> str | None`。
- `adapters/codex_daemon.py` 保留 `_parse_version = parse_version` 等私有别名，既有调用点零改动。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/services/codex/test_daemon_version.py
"""One version predicate for every daemon-gated path.

The text chain has carried these since 0.3.0; the image gate (P3) needs the
same semantics. Two copies that must agree is the bug class this contract
exists to end — so the module is shared, and the test pins the semantics
the text chain already relies on."""
from app.services.codex.daemon_version import (
    UNVERSIONED,
    parse_version,
    version_at_least,
)


def test_parse_version_reads_plain_and_v_prefixed():
    assert parse_version("0.4.0") == (0, 4, 0)
    assert parse_version("v1.2.3") == (1, 2, 3)


def test_parse_version_ignores_prerelease_tail():
    assert parse_version("0.4.0-rc1") == (0, 4, 0)


def test_unparseable_reads_as_older_than_everything():
    # A garbled report is refused, not waved through.
    assert parse_version("garbage") == (0, 0, 0)
    assert parse_version(None) == (0, 0, 0)
    assert parse_version(UNVERSIONED) == (0, 0, 0)


def test_version_at_least_is_inclusive():
    assert version_at_least("0.4.0", "0.4.0") is True
    assert version_at_least("0.4.1", "0.4.0") is True
    assert version_at_least("0.3.9", "0.4.0") is False
    assert version_at_least(UNVERSIONED, "0.4.0") is False
```

- [ ] **Step 2: RED** — `cd backend && uv run pytest tests/services/codex/test_daemon_version.py -v` → `ModuleNotFoundError`.

- [ ] **Step 3: 实现** — 新模块内容即 `adapters/codex_daemon.py:76-127` 原文搬入并去下划线（docstring 原样保留，它们是文本链的语义说明）。`adapters/codex_daemon.py` 改为：

```python
from app.services.codex.daemon_version import (
    UNVERSIONED,
    parse_version as _parse_version,
    reported_daemon_version as _reported_daemon_version,
    version_at_least as _version_at_least,
)
```

`MIN_TEXT_DAEMON_VERSION` 留在 adapters（它是文本链自己的策略值）。若 `tests/services/codex/` 目录不存在，新建含空 `__init__.py`（照 `tests/services/generation/` 的做法）。

- [ ] **Step 4: 跑测试** — `uv run pytest tests/services/codex tests -k "codex_daemon or daemon" -q` 全绿（文本链既有测试是本次搬迁的回归网）。

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/codex/daemon_version.py backend/app/services/ai/adapters/codex_daemon.py backend/tests/services/codex
git -c user.email=ezufofoti59@gmail.com -c user.name=heygo commit -m "refactor(codex): 版本判定抽到 daemon_version.py —— 文本链改 import 零行为变化,图片闸门将复用同一份"
```

---

### Task 3: 图片派发的版本闸门

**Files:**
- Modify: `backend/app/services/codex/daemon_dispatch.py`（常量、错误类、`dispatch_to_daemon` 签名与闸门）
- Test: `backend/tests/test_codex_daemon_dispatch.py`

**Interfaces:**
- Produces：`MIN_IMAGE_DAEMON_VERSION = "0.4.0"`；`class DaemonUpdateRequiredError(RuntimeError)`；`dispatch_to_daemon(..., daemon_version: Optional[Callable[[str], Awaitable[Optional[str]]]] = None)`（默认 `reported_daemon_version`）。

- [ ] **Step 1: 写失败测试（追加，沿用文件里的 `_FakeTransport`）**

```python
from app.services.codex.daemon_dispatch import DaemonUpdateRequiredError


async def _v(version):
    async def resolver(_user_id: str):
        return version
    return resolver


@pytest.mark.asyncio
async def test_old_daemon_is_refused_with_a_typed_update_error_before_any_send():
    t = _FakeTransport(online=True, result={"gen_id": "1"})
    with pytest.raises(DaemonUpdateRequiredError) as exc:
        await dispatch_to_daemon(
            user_id="u1", scope_id=1, kind="image",
            payload={"engine": "codex", "prompt": "x"},
            transport=t, mint_ticket=lambda **_: "t", timeout_s=1,
            daemon_version=await _v("0.3.0"),
        )
    assert t.sent == []                      # refused BEFORE the job left
    assert "0.3.0" in str(exc.value) and "0.4.0" in str(exc.value)
    assert "install.sh" in str(exc.value)    # the user can act on it


@pytest.mark.asyncio
async def test_current_daemon_passes_the_gate():
    t = _FakeTransport(online=True, result={"gen_id": "1"})
    out = await dispatch_to_daemon(
        user_id="u1", scope_id=1, kind="image",
        payload={"engine": "codex", "prompt": "x"},
        transport=t, mint_ticket=lambda **_: "t", timeout_s=1,
        daemon_version=await _v("0.4.0"),
    )
    assert out == {"gen_id": "1"} and len(t.sent) == 1


@pytest.mark.asyncio
async def test_unversioned_daemon_is_refused():
    t = _FakeTransport(online=True, result={"gen_id": "1"})
    with pytest.raises(DaemonUpdateRequiredError):
        await dispatch_to_daemon(
            user_id="u1", scope_id=1, kind="image",
            payload={"engine": "codex", "prompt": "x"},
            transport=t, mint_ticket=lambda **_: "t", timeout_s=1,
            daemon_version=await _v("0.0.0"),
        )
    assert t.sent == []


@pytest.mark.asyncio
async def test_unknown_version_skips_the_gate_so_offline_stays_the_truthful_error():
    # None = "no daemon / could not find out". Telling someone to update a
    # daemon that is not running is the worse of the two wrong answers.
    t = _FakeTransport(online=False)
    with pytest.raises(DaemonOfflineError):
        await dispatch_to_daemon(
            user_id="u1", scope_id=1, kind="image",
            payload={"engine": "codex", "prompt": "x"},
            transport=t, mint_ticket=lambda **_: "t", timeout_s=1,
            daemon_version=await _v(None),
        )


@pytest.mark.asyncio
async def test_dreamina_jobs_are_not_gated_this_release():
    # Its payload did not change; gating it would refuse working daemons for nothing.
    t = _FakeTransport(online=True, result={"gen_id": "1"})
    out = await dispatch_to_daemon(
        user_id="u1", scope_id=1, kind="image",
        payload={"engine": "dreamina", "submit_args": ["text2image"]},
        transport=t, mint_ticket=lambda **_: "t", timeout_s=1,
        daemon_version=await _v("0.3.0"),
    )
    assert out == {"gen_id": "1"}
```

- [ ] **Step 2: RED** — `uv run pytest tests/test_codex_daemon_dispatch.py -v` → `ImportError: DaemonUpdateRequiredError` / `TypeError: unexpected keyword 'daemon_version'`.

- [ ] **Step 3: 实现**

```python
from app.services.codex.daemon_version import reported_daemon_version, version_at_least

# Image/video jobs on the codex engine need a daemon that forwards --quality
# (0.4.0). Below that, `quality` is silently discarded one layer down — the
# exact fake switch P4 removed from the UI — so the honest answer is a typed
# refusal that tells the user how to update, not a quiet degrade.
MIN_IMAGE_DAEMON_VERSION = "0.4.0"


class DaemonUpdateRequiredError(RuntimeError):
    """The connected daemon is too old for this job; message says how to update."""
```

在 `dispatch_to_daemon` 的 `is_online` 检查**之后**、`job_id` 生成之前：

```python
    if payload.get("engine") == "codex" and kind in ("image", "video"):
        resolver = daemon_version or reported_daemon_version
        reported = await resolver(user_id)
        # None = could not find out (offline / presence-vs-table disagreement):
        # not a verdict — skip, and let a later step raise the truthful error.
        if reported is not None and not version_at_least(reported, MIN_IMAGE_DAEMON_VERSION):
            raise DaemonUpdateRequiredError(
                f"your local codex daemon is {reported}; image generation needs "
                f">= {MIN_IMAGE_DAEMON_VERSION} — re-run tools/codex-daemon/install.sh "
                "(or install.ps1 on Windows) and restart it"
            )
```

签名加 `daemon_version: Optional[Callable[[str], Awaitable[Optional[str]]]] = None`（`typing` 已导入 `Callable`/`Optional`，补 `Awaitable`）。docstring 加一行 `Raises DaemonUpdateRequiredError ...`。

- [ ] **Step 4: GREEN + 突变** — 5 条新增 + 既有全绿；把闸门条件改成 `if False:`，前三条转红；还原再绿。贴输出。

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/codex/daemon_dispatch.py backend/tests/test_codex_daemon_dispatch.py
git -c user.email=ezufofoti59@gmail.com -c user.name=heygo commit -m "feat(codex): 图片派发版本闸门 MIN_IMAGE_DAEMON_VERSION=0.4.0 —— 老 daemon 拿到类型化的'请升级',不再静默吞 quality"
```

---

### Task 4: 能力翻转 —— codex-local 的 `quality` 改回真话

**Files:**
- Modify: `backend/app/services/ai/provider_protocols/codex_local.py`（`quality: False → True` + 注释）
- Modify: `backend/tests/test_provider_capabilities.py:48-59`
- Modify: `backend/tests/services/generation/test_request.py`（端到端 quality）

- [ ] **Step 1: 写失败测试**

`test_provider_capabilities.py` 把 `test_codex_quality_is_declared_per_transport_not_per_cli` 改名为 `test_codex_quality_is_true_on_both_transports_now_that_the_gate_exists`，docstring 说明：daemon 0.4.0 转发 `--quality`，`MIN_IMAGE_DAEMON_VERSION` 保证更老的 daemon 收到类型化拒绝而不是任务，所以两侧 `True` 都是真话；断言改为两个 `is True`。

`test_request.py` 追加：

```python
def test_quality_survives_reconcile_and_reaches_the_codex_daemon_payload():
    """P1 declared codex-local quality=False (honest then: nothing forwarded it).
    With the 0.4.0 gate that is no longer true — quality must now pass
    reconcile untouched and land in the payload."""
    from app.services.ai.provider_protocols import resolve_generation_protocol

    req = GenerationRequest.from_params(
        kind="image", prompt="a cat", model="codex-local-image",
        params={"ratio": "16:9", "quality": "high"}, source_url=None,
    )
    caps = resolve_generation_protocol("codex-local").capabilities
    eff, dropped = req.reconcile(caps)
    assert dropped == []                                   # P1 had ["quality"] here
    payload = eff.to_codex_daemon_payload(engine_model="", ref_urls=[])
    assert payload["quality"] == "high"
```

- [ ] **Step 2: RED** — 两处：`quality is False` 与 `dropped == ["quality"]`。

- [ ] **Step 3: 实现** — `codex_local.py` 的 `quality=True`，把「P3 flips it back」那段注释改成事实：`--quality` 由 daemon ≥0.4.0 转发（`tools/codex-daemon/index.mjs buildImageArgs`），更老的 daemon 被 `daemon_dispatch.MIN_IMAGE_DAEMON_VERSION` 类型化拒绝，因此此处 `True` 不是承诺是事实。

- [ ] **Step 4: GREEN** — `uv run pytest tests/test_provider_capabilities.py tests/services/generation tests/test_canvas_generation_workflow.py -q` 全绿。

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/ai/provider_protocols/codex_local.py backend/tests/test_provider_capabilities.py backend/tests/services/generation/test_request.py
git -c user.email=ezufofoti59@gmail.com -c user.name=heygo commit -m "feat(gen): codex-local quality 翻回 True —— 闸门担保下才是真话,与闸门同 PR 不留假开关窗口"
```

---

### Task 5: 全量回归 + 真栈验收（含正向对照）

- [ ] **Step 1**：`cd backend && uv run pytest -q`；black/isort/flake8；`cd tools/codex-daemon && npm test`。
- [ ] **Step 2**：合并部署后（**用户操作**）：
  1. **先不升级 daemon**，画布上 codex-local 生成一张 → 预期任务失败，`task_tracking.error_msg` 含 `DaemonUpdateRequiredError` 与 `install.sh`（闸门的正向对照——证明它真的会拦）。
  2. 重跑 `tools/codex-daemon/install.sh` 升到 0.4.0，重启 daemon；日志 `[codex-daemon] device online`。
  3. 生成一张 **16:9 + High**：
     ```sql
     SELECT params->'requested'->>'quality' AS want_q, params->'effective'->>'quality' AS sent_q,
            params->>'honored', params->>'dropped', origin_kind
     FROM generated_media ORDER BY created_at DESC LIMIT 1;
     ```
     预期 `want_q='high'`、`sent_q='high'`、`dropped='[]'`、`origin_kind='canvas_run'`。
- [ ] **Step 3**：PR 正文引用 spec §5/§9，**明确写出「不删 `size`」对 spec 字面的偏离及理由**。

---

## Self-Review

**Spec 覆盖（P3）**：§5 daemon 协议改动 → T1 ✅；版本闸门（`MIN_IMAGE_DAEMON_VERSION`，沿 `MIN_TEXT_DAEMON_VERSION` 机制）→ T2/T3 ✅；§9「双发一版再拒绝」的拒绝版 → T3 ✅；`quality` 能力回真 → T4 ✅；§6.5 真栈含正向对照 → T5 ✅。**偏离**：不删 `size`（Global Constraints 第 2 条，理由已写）。
**占位扫描**：无 TBD；T2 的「若目录不存在则建 `__init__.py`」是条件步骤不是占位。
**类型一致性**：`daemon_version` 注入签名 `Callable[[str], Awaitable[Optional[str]]]` 与 `reported_daemon_version(user_id) -> Optional[str]` 一致；`buildImageArgs` 的 `refs` 是已下载的本地路径数组，与 `runImageJob` 现有 `refs` 变量同义。
**已知风险**：用户是当前唯一 daemon 用户——合并即意味着**在升级前 codex-local 出图会被拒绝**，这是刻意的（拒绝信息可行动）；PR 正文与合并前的提醒里都要说清。`dispatch_to_daemon` 之外若还有别的 codex 图片派发入口（grep `dispatch_to_daemon(` 只应在 `canvas_generation.py` 一处），闸门要罩全。
