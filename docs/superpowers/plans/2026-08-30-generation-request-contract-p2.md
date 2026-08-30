# 出图生成请求契约 — P2（产出记录）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每一次生成落库时都记下「要求了什么 / 实际发出了什么 / 实际拿到了什么 / 是否相符」，服务器与 daemon 两个入库口一致，daemon 产物不再是无归因的 `canvas_upload`。

**Architecture:** 在唯一的入库咽喉点 `register_generated_media` 之上加一层测量：图片用 Pillow、视频用 ffprobe 量真实像素与时长，与 `GenerationRequest` 记下的要求对比，把四键 `requested / effective / measured / honored` 写进 `generated_media.params`。daemon 侧把归因塞进已有的一次性上传 ticket（Redis JSON），上传时取回来构造完整 `GenerationOrigin`——不新增表、不改 schema。

**Tech Stack:** Python 3.13 / FastAPI / DBOS workflow / Pillow 12 / ffprobe / Redis / pytest(asyncio)。后端目录 `backend/`，运行 `cd backend && uv run pytest`。

**Spec:** `docs/superpowers/specs/2026-08-29-generation-request-contract-design.md` §3.3（产出记录）、§6.2/§6.4（验收）、§7（P2 范围）

## Global Constraints

- **P1 已上线，不要重做**：`app/services/generation/{aspect,request}.py`、`ProviderCapabilities`、`canvas_generation.py` 的四条分支读同一个 `GenerationRequest`、`dropped_knobs` 进 `task_tracking.metadata`、`db_registry` 两个 resolver stamp `provider_key` —— 全部已合并（PR #2076）。
- 新代码禁止 `text()` 裸 SQL；本期**不改 schema、不加迁移**（`generated_media.params` 是 jsonb，够用）。
- 子进程一律经 `safe_popen_kwargs()`（`app/agent_framework/process_lifecycle.py`）splat，**不要自己传 `env=`**——与 splat 并存是重复关键字、spawn 时 TypeError，有源码扫描守卫钉住这一类。ffprobe 就是这样的子进程。
- **测量失败不得让生成失败**：图已经生成、已经付费。测不出来就记 `measured=null` 并 log warning，绝不 raise。这是 §3.3 的明文约定。
- 容差沿用 `aspect.ASPECT_TOLERANCE = 0.06`，不要另起一个数。
- `task_tracking` 的 `phase/status/progress/started_at/completed_at/error_msg` 由 trigger 全权同步，业务代码**禁止 PATCH 这些列**；只能写 `metadata` jsonb。
- 仓库 lint 门禁是 **black / isort / flake8**，**没有 ruff**。测试输出须干净（约 13 条既有 pyiceberg PydanticDeprecated warning 是基线）。
- commit 用 `git -c user.email=ezufofoti59@gmail.com -c user.name=heygo commit -m "..."`；分支从 `origin/master` 建、用 `git switch -c`，**不要 stash/pop**。
- 每个任务的 pinning 测试必须**看着它红过**：改坏被测的那一行、跑测试看它失败、还原、再跑绿，把两次输出贴进报告。P1 里有两个缺陷正是靠这条抓出来的。

---

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `backend/app/services/generation/measure.py` | `measure_image(path)` / `measure_video(path)` / `compare_aspect(requested, w, h)` —— 纯测量，不碰 DB | 新建 |
| `backend/app/services/generation/outcome.py` | `GenerationOutcome` 四键结构 + `build_outcome_params(req, eff, dropped, measured)` | 新建 |
| `backend/app/services/library/generated_media_service.py` | `register_generated_media` 接受可选 `outcome`，写进 `params` | 修改 |
| `backend/app/workflows/canvas_generation.py` | `persist_canvas_generation_step` 把 req/eff/dropped 传下去 | 修改 |
| `backend/app/api/codex_daemon_router.py` | ticket 携带归因；上传时构造完整 `GenerationOrigin` | 修改 |
| `backend/app/services/codex/daemon_dispatch.py` | `dispatch_to_daemon` 接受并转发归因 | 修改 |
| `backend/tests/services/generation/test_measure.py` | | 新建 |
| `backend/tests/services/generation/test_outcome.py` | | 新建 |
| `backend/tests/test_daemon_result_attribution.py` | | 新建 |
| `backend/tests/test_canvas_generation_workflow.py` | 落库参数断言 | 修改 |

---

### Task 1: 测量原语 `measure.py`

**Files:**
- Create: `backend/app/services/generation/measure.py`
- Test: `backend/tests/services/generation/test_measure.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) Measured: width: int | None; height: int | None; duration_s: float | None`
  - `measure_image(path: str) -> Measured | None`
  - `async measure_video(path: str) -> Measured | None`
  - `compare_aspect(requested_ratio: str | None, width: int, height: int) -> bool | None`（`None` = 无从判断：没要求比例，或尺寸退化）

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/services/generation/test_measure.py
"""Measuring what actually came back.

The contract's whole point is that "the provider returned a file" never
again gets read as "the provider did what was asked". These functions are
the only place that reads real pixels, so they must be honest about the
cases where they cannot: a missing file, a corrupt file and a degenerate
size are all `None`, never a guess and never an exception that would fail
an already-paid-for generation.
"""
import struct
import zlib

import pytest

from app.services.generation.measure import Measured, compare_aspect, measure_image


def _png(path, width: int, height: int) -> str:
    """A real, minimal PNG of the requested size (Pillow reads it)."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x00\x00\x00" * width for _ in range(height))
    body = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
    body += chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    path.write_bytes(body)
    return str(path)


def test_measure_image_reads_real_pixels(tmp_path):
    got = measure_image(_png(tmp_path / "a.png", 1536, 1024))
    assert got == Measured(width=1536, height=1024, duration_s=None)


def test_measure_image_returns_none_for_a_missing_file(tmp_path):
    assert measure_image(str(tmp_path / "nope.png")) is None


def test_measure_image_returns_none_for_a_corrupt_file_rather_than_raising(tmp_path):
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not a png at all")
    assert measure_image(str(bad)) is None


def test_compare_aspect_accepts_the_requested_shape():
    assert compare_aspect("16:9", 1536, 864) is True


def test_compare_aspect_rejects_a_different_shape():
    # 1199x1312 = 0.914 — the real codex-local output for a 16:9 request.
    assert compare_aspect("16:9", 1199, 1312) is False


def test_compare_aspect_honours_the_shared_six_percent_tolerance():
    # 1536x1024 is 1.5, which is 16 % off 1.778 — outside tolerance.
    assert compare_aspect("16:9", 1536, 1024) is False
    # 1520x864 is 1.759, ~1 % off — inside.
    assert compare_aspect("16:9", 1520, 864) is True


def test_compare_aspect_is_none_when_there_was_no_request():
    # "let the model choose" is a real request; inventing a verdict for it
    # would put a false `honored` in the record.
    assert compare_aspect(None, 100, 100) is None
    assert compare_aspect("", 100, 100) is None


def test_compare_aspect_is_none_for_an_unknown_ratio_or_degenerate_size():
    assert compare_aspect("7:5", 100, 100) is None
    assert compare_aspect("16:9", 0, 100) is None
```

- [ ] **Step 2: 跑一遍确认失败**

Run: `cd backend && uv run pytest tests/services/generation/test_measure.py -v`
Expected: `ModuleNotFoundError: No module named 'app.services.generation.measure'`

- [ ] **Step 3: 实现**

```python
# backend/app/services/generation/measure.py
"""What actually came back, measured — never inferred.

`ok: true` from a provider has already been shown to mean nothing about
shape: every one of the four production images whose ratio was wrong came
back with a success flag. So the record is built from real pixels.

Everything here degrades to None. A generation that already happened and
was already paid for must never fail because we could not measure it.
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Optional

from loguru import logger

from app.services.generation.aspect import ASPECT_RATIOS, ASPECT_TOLERANCE


@dataclass(frozen=True)
class Measured:
    width: Optional[int] = None
    height: Optional[int] = None
    duration_s: Optional[float] = None


def measure_image(path: str) -> Optional[Measured]:
    """Real pixel dimensions, or None when the file cannot be read."""
    if not path or not os.path.isfile(path):
        return None
    try:
        from PIL import Image

        with Image.open(path) as img:
            width, height = img.size
    except Exception as exc:  # corrupt, truncated, unsupported — all the same
        logger.warning("[measure] could not read image {}: {}", path[:200], exc)
        return None
    if width <= 0 or height <= 0:
        return None
    return Measured(width=width, height=height)


async def measure_video(path: str) -> Optional[Measured]:
    """Dimensions + duration via ffprobe, or None when it cannot be read."""
    if not path or not os.path.isfile(path):
        return None
    from app.agent_framework.process_lifecycle import safe_popen_kwargs

    args = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height:format=duration",
        "-of", "json", path,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **safe_popen_kwargs(),
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=30)
    except Exception as exc:
        logger.warning("[measure] ffprobe failed for {}: {}", path[:200], exc)
        return None
    if proc.returncode != 0:
        logger.warning("[measure] ffprobe rc={} for {}", proc.returncode, path[:200])
        return None
    try:
        data = json.loads(out.decode() or "{}")
        stream = (data.get("streams") or [{}])[0]
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
        raw_duration = (data.get("format") or {}).get("duration")
        duration = float(raw_duration) if raw_duration else None
    except Exception as exc:
        logger.warning("[measure] unreadable ffprobe output for {}: {}", path[:200], exc)
        return None
    if width <= 0 or height <= 0:
        return None
    return Measured(width=width, height=height, duration_s=duration)


def compare_aspect(
    requested_ratio: Optional[str], width: int, height: int
) -> Optional[bool]:
    """Did the product match the shape that was asked for?

    None means the question does not apply — no ratio was requested (IC
    自适应 sends an empty aspect on purpose), the ratio is not one we know,
    or the size is degenerate. A None must never be stored as False: "we
    did not ask" and "they ignored us" are different facts.
    """
    want = ASPECT_RATIOS.get((requested_ratio or "").strip())
    if not want or width <= 0 or height <= 0:
        return None
    got = width / height
    return abs(got - want) <= want * ASPECT_TOLERANCE
```

- [ ] **Step 4: 跑测试**

Run: `cd backend && uv run pytest tests/services/generation/test_measure.py -v`
Expected: 9 条全 PASS。

- [ ] **Step 5: 突变验证（看着它红过）**

把 `compare_aspect` 的 `abs(got - want) <= want * ASPECT_TOLERANCE` 改成 `return True`，跑同一文件，确认 `test_compare_aspect_rejects_a_different_shape` 与 `test_compare_aspect_honours_the_shared_six_percent_tolerance` 转红；还原后再跑绿。两次输出贴进报告。

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/generation/measure.py backend/tests/services/generation/test_measure.py
git -c user.email=ezufofoti59@gmail.com -c user.name=heygo commit -m "feat(gen): 产物测量原语 —— Pillow 量图片/ffprobe 量视频/按 6% 容差比对比例，测不出一律 None 不抛"
```

---

### Task 2: `GenerationOutcome` 四键记录

**Files:**
- Create: `backend/app/services/generation/outcome.py`
- Test: `backend/tests/services/generation/test_outcome.py`

**Interfaces:**
- Consumes: Task 1 的 `Measured`, `compare_aspect`；P1 的 `GenerationRequest`
- Produces: `build_outcome_params(req, eff, dropped, measured) -> dict` —— 返回将要合并进 `generated_media.params` 的四键

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/services/generation/test_outcome.py
"""The four keys every generation record carries.

`requested` is what the user asked for, `effective` is what we actually
sent after reconciling against the provider, `measured` is what came back,
`honored` is whether those last two agree. Keeping requested and effective
apart is the point: without it "the user asked for 21:9 and we never sent
it" is indistinguishable from "we sent it and the model ignored it".
"""
from app.services.generation.measure import Measured
from app.services.generation.outcome import build_outcome_params
from app.services.generation.request import GenerationRequest


def _req(**over):
    base = dict(
        kind="image", prompt="a cat", model="m",
        params={"ratio": "16:9", "quality": "high"}, source_url=None,
    )
    base.update(over)
    return GenerationRequest.from_params(**base)


def test_records_all_four_keys():
    req = _req()
    eff, dropped = req, []
    out = build_outcome_params(req, eff, dropped, Measured(width=1536, height=864))
    assert out["requested"] == {"ratio": "16:9", "quality": "high"}
    assert out["effective"] == {"ratio": "16:9", "quality": "high"}
    assert out["measured"] == {"width": 1536, "height": 864}
    assert out["honored"] is True


def test_requested_and_effective_differ_when_a_knob_was_dropped():
    req = _req(params={"ratio": "21:9", "quality": "high"})
    eff = req.__class__(**{**req.__dict__, "ratio": None, "quality": None})
    out = build_outcome_params(req, eff, ["ratio", "quality"], Measured(1024, 1024))
    assert out["requested"]["ratio"] == "21:9"
    assert out["effective"].get("ratio") is None
    assert out["dropped"] == ["ratio", "quality"]
    # We never sent a ratio, so there is nothing to have honoured.
    assert out["honored"] is None


def test_honored_is_false_when_the_shape_came_back_wrong():
    req = _req()
    out = build_outcome_params(req, req, [], Measured(width=1199, height=1312))
    assert out["honored"] is False


def test_measured_is_null_when_measurement_failed_and_honored_stays_unknown():
    req = _req()
    out = build_outcome_params(req, req, [], None)
    assert out["measured"] is None
    assert out["honored"] is None


def test_video_measurement_carries_duration():
    req = _req(kind="video", params={"aspect": "16:9", "duration": "5"})
    out = build_outcome_params(req, req, [], Measured(1920, 1080, duration_s=5.02))
    assert out["measured"] == {"width": 1920, "height": 1080, "duration_s": 5.02}
    assert out["requested"]["duration"] == 5


def test_omits_knobs_that_were_never_set_rather_than_writing_nulls():
    req = _req(params={"ratio": "1:1"})
    out = build_outcome_params(req, req, [], Measured(1024, 1024))
    assert "quality" not in out["requested"]
    assert "negative" not in out["requested"]
```

- [ ] **Step 2: 跑一遍确认失败**

Run: `cd backend && uv run pytest tests/services/generation/test_outcome.py -v`
Expected: `ImportError: cannot import name 'build_outcome_params'`

- [ ] **Step 3: 实现**

```python
# backend/app/services/generation/outcome.py
"""What a generation record says about itself.

Four keys, written on every generation by both the server and the daemon
upload path:

  requested — the knobs the user set
  effective — what survived reconciliation and actually went out
  measured  — real pixels/duration of the product, or null
  honored   — did `measured` match `effective`'s shape (null = no verdict)

`requested` vs `effective` is the distinction that makes the record worth
keeping: it separates "we never sent it" from "they ignored it", which is
exactly the pair that was indistinguishable before this contract.
"""
from __future__ import annotations

from typing import Any, Optional

from app.services.generation.measure import Measured, compare_aspect
from app.services.generation.request import GenerationRequest

_KNOBS = ("ratio", "quality", "resolution", "negative", "video_mode", "duration")


def _knobs_of(req: GenerationRequest) -> dict[str, Any]:
    """Set knobs only. A null in the record would read as "asked for nothing
    in particular", which is a different claim from "did not ask"."""
    out: dict[str, Any] = {}
    for name in _KNOBS:
        value = getattr(req, name, None)
        if value not in (None, ""):
            out[name] = value
    if req.refs:
        out["refs"] = len(req.refs)
    return out


def _measured_of(measured: Optional[Measured]) -> Optional[dict[str, Any]]:
    if measured is None:
        return None
    out: dict[str, Any] = {"width": measured.width, "height": measured.height}
    if measured.duration_s is not None:
        out["duration_s"] = measured.duration_s
    return out


def build_outcome_params(
    req: GenerationRequest,
    eff: GenerationRequest,
    dropped: list[str],
    measured: Optional[Measured],
) -> dict[str, Any]:
    """The outcome block merged into ``generated_media.params``."""
    honored: Optional[bool] = None
    if measured is not None and measured.width and measured.height:
        # Judge against what we SENT, not what was asked: a ratio we dropped
        # was never the provider's to honour.
        honored = compare_aspect(eff.ratio, measured.width, measured.height)
    return {
        "requested": _knobs_of(req),
        "effective": _knobs_of(eff),
        "dropped": list(dropped),
        "measured": _measured_of(measured),
        "honored": honored,
    }
```

- [ ] **Step 4: 跑测试**

Run: `cd backend && uv run pytest tests/services/generation/ -v`
Expected: 全 PASS（Task 1 的 9 条 + 本任务 6 条 + P1 既有的）。

- [ ] **Step 5: 突变验证**

把 `build_outcome_params` 里 `compare_aspect(eff.ratio, ...)` 改成 `compare_aspect(req.ratio, ...)`，跑测试确认 `test_requested_and_effective_differ_when_a_knob_was_dropped` 转红（它会把 `honored` 从 `None` 变成 `False`——把「我们没发」误记成「它没做到」）。还原后再跑绿。贴两次输出。

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/generation/outcome.py backend/tests/services/generation/test_outcome.py
git -c user.email=ezufofoti59@gmail.com -c user.name=heygo commit -m "feat(gen): GenerationOutcome 四键 —— requested/effective 分开记，才能区分'我们没发'与'它没做到'"
```

---

### Task 3: 服务器路径落库

**Files:**
- Modify: `backend/app/services/library/generated_media_service.py`（`register_generated_media` 接受可选 `outcome: dict | None`）
- Modify: `backend/app/workflows/canvas_generation.py`（`generate_canvas_media_step` 返回 `req`/`eff`/`dropped` 所需信息；`persist_canvas_generation_step` 测量并组装）
- Test: `backend/tests/test_canvas_generation_workflow.py`

**Interfaces:**
- Consumes: Task 1/2
- Produces: `generated_media.params` 含 `requested/effective/dropped/measured/honored` 五键

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.asyncio
async def test_persist_writes_the_outcome_block_for_a_server_image():
    """The record must say what was asked, what was sent, and what arrived."""
    captured = {}

    async def fake_register(**kw):
        captured.update(kw)
        return {"id": 1}

    media = {
        "media_kind": "image",
        "local_path": _real_png(1536, 864),   # helper writes a real PNG
        "remote_url": None,
        "provider": "codex",
        "model": "gpt-image-2",
        "dropped_knobs": [],
        "requested_params": {"ratio": "16:9"},
        "effective_params": {"ratio": "16:9"},
    }
    with patch(
        "app.services.library.generated_media_service.register_generated_media",
        new=fake_register,
    ):
        await persist_canvas_generation_step(
            media=media, user_id="u1", canvas_id=1, node_id="n1",
            prompt="a cat", params={"ratio": "16:9"},
        )

    outcome = captured["origin"].params
    assert outcome["requested"]["ratio"] == "16:9"
    assert outcome["measured"] == {"width": 1536, "height": 864}
    assert outcome["honored"] is True


@pytest.mark.asyncio
async def test_persist_records_a_dishonoured_shape_without_failing_the_run():
    """A wrong shape is recorded, never raised: the image is already paid for."""
    captured = {}

    async def fake_register(**kw):
        captured.update(kw)
        return {"id": 1}

    media = {
        "media_kind": "image",
        "local_path": _real_png(1199, 1312),   # the real codex-local output
        "remote_url": None,
        "provider": "codex-local",
        "model": "gpt-image-2",
        "dropped_knobs": [],
        "requested_params": {"ratio": "16:9"},
        "effective_params": {"ratio": "16:9"},
    }
    with patch(
        "app.services.library.generated_media_service.register_generated_media",
        new=fake_register,
    ):
        out = await persist_canvas_generation_step(
            media=media, user_id="u1", canvas_id=1, node_id="n1",
            prompt="a cat", params={"ratio": "16:9"},
        )

    assert out["generated_media_id"] == 1        # the run still succeeded
    assert captured["origin"].params["honored"] is False


@pytest.mark.asyncio
async def test_persist_still_registers_when_measurement_fails():
    captured = {}

    async def fake_register(**kw):
        captured.update(kw)
        return {"id": 1}

    media = {
        "media_kind": "image",
        "local_path": "/nonexistent/never.png",
        "remote_url": None,
        "provider": "codex", "model": "m", "dropped_knobs": [],
        "requested_params": {"ratio": "16:9"},
        "effective_params": {"ratio": "16:9"},
    }
    with patch(
        "app.services.library.generated_media_service.register_generated_media",
        new=fake_register,
    ):
        out = await persist_canvas_generation_step(
            media=media, user_id="u1", canvas_id=1, node_id="n1",
            prompt="a cat", params={"ratio": "16:9"},
        )

    assert out["generated_media_id"] == 1
    assert captured["origin"].params["measured"] is None
    assert captured["origin"].params["honored"] is None
```

配套 helper（放在同文件测试区顶部）：

```python
def _real_png(width: int, height: int) -> str:
    """A real PNG on disk at the requested size, cleaned up by tmp reuse."""
    import struct, tempfile, zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x00\x00\x00" * width for _ in range(height))
    body = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
    body += chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    fd, path = tempfile.mkstemp(suffix=".png")
    with os.fdopen(fd, "wb") as fh:
        fh.write(body)
    return path
```

- [ ] **Step 2: 跑一遍确认失败**

Run: `cd backend && uv run pytest tests/test_canvas_generation_workflow.py -k "outcome or dishonoured or measurement_fails" -v`
Expected: FAIL —— `media` 里没有 `requested_params`/`effective_params` 键，`origin.params` 里没有四键。

- [ ] **Step 3: 实现**

`generate_canvas_media_step` 的四条 return 各加两个键（`req`/`eff` 都在手上）：

```python
        "requested_params": req.knobs_dict(),   # 见下
        "effective_params": eff.knobs_dict(),
```

给 `GenerationRequest` 加一个薄方法（`request.py`），复用 `outcome._knobs_of` 的语义、避免两处各写一遍：

```python
    def knobs_dict(self) -> dict[str, Any]:
        """The set knobs, for the record. Thin wrapper so callers do not each
        re-derive which fields count as knobs."""
        from app.services.generation.outcome import _knobs_of

        return _knobs_of(self)
```

`persist_canvas_generation_step` 在 `register_generated_media` 之前测量并组装：

```python
        from app.services.generation.measure import measure_image, measure_video
        from app.services.generation.outcome import build_outcome_params

        measured = None
        if local_path:
            measured = (
                measure_image(local_path)
                if media_kind == "image"
                else await measure_video(local_path)
            )
        # Remote-url products (ark) are not on disk here; P2 measures what it
        # can reach. `measured: null` is an honest record, not a gap.
        outcome = build_outcome_params_from_dicts(
            requested=media.get("requested_params") or {},
            effective=media.get("effective_params") or {},
            dropped=list(media.get("dropped_knobs") or []),
            measured=measured,
        )
        params = {**params, **outcome}
```

（`build_outcome_params_from_dicts` 是 `outcome.py` 的第二个入口，接受已序列化的两个 dict 而非 `GenerationRequest` —— workflow step 之间只能传 primitives，DBOS 会把返回值持久化。签名：`build_outcome_params_from_dicts(*, requested: dict, effective: dict, dropped: list[str], measured: Measured | None) -> dict`，内部与 `build_outcome_params` 共用同一段组装逻辑，`honored` 用 `effective.get("ratio")` 判。Task 2 实现时一并写出来并加测试。）

⚠️ **DBOS step 的返回值会被持久化**，所以 `requested_params`/`effective_params` 必须是 JSON-safe 的 dict（`knobs_dict()` 已保证：只含 str/int/bool）。不要把 `GenerationRequest` 对象塞进返回值。

- [ ] **Step 4: 跑测试**

Run: `cd backend && uv run pytest tests/test_canvas_generation_workflow.py -q`
Expected: 全 PASS（P1 的 28 条 + 本任务 3 条）。

- [ ] **Step 5: 突变验证**

把 `persist_canvas_generation_step` 里的 `measured = ...` 直接改成 `measured = None`，确认 `test_persist_writes_the_outcome_block_for_a_server_image` 与 `..._records_a_dishonoured_shape...` 双双转红；还原再跑绿。贴输出。

- [ ] **Step 6: Commit**

```bash
git add backend/app/workflows/canvas_generation.py backend/app/services/generation/ backend/tests/
git -c user.email=ezufofoti59@gmail.com -c user.name=heygo commit -m "feat(gen): 服务器路径落库四键 —— 量真实像素、比对已发出的比例、不符只记录不拦截"
```

---

### Task 4: daemon 产物归因（ticket 携带上下文）

**Files:**
- Modify: `backend/app/api/codex_daemon_router.py`（`mint_upload_ticket` 存归因；`_consume_upload_ticket` 取回；`_register_daemon_result` 构造完整 `GenerationOrigin`）
- Modify: `backend/app/services/codex/daemon_dispatch.py`（`dispatch_to_daemon` 接受 `attribution` 并转发给 mint）
- Modify: `backend/app/workflows/canvas_generation.py`（daemon 分支传 attribution）
- Test: `backend/tests/test_daemon_result_attribution.py`

**Interfaces:**
- Produces: daemon 产出的 `generated_media` 行 `origin_kind='canvas_run'`，带 `canvas_id/node_id/prompt/model/provider` 与四键 outcome

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_daemon_result_attribution.py
"""A daemon-produced image must be distinguishable from an uploaded one.

Today every daemon product lands as `origin_kind='canvas_upload'` with
`params={produced_by, job_id}` — prompt, model, ratio, canvas and node all
empty. In the database a generated image and a hand-uploaded one look
identical, so nothing about daemon generations can be audited at all.

The attribution rides on the upload ticket, which already exists, is
already one-shot, and already carries the owner. No new table.
"""
import json

import pytest


@pytest.mark.asyncio
async def test_ticket_carries_the_job_attribution(fake_redis):
    from app.api.codex_daemon_router import mint_upload_ticket

    ticket = await mint_upload_ticket(
        user_id="u1", scope_id=7, job_id="j1",
        attribution={
            "canvas_id": 42, "node_id": "n1", "prompt": "a cat",
            "model": "gpt-image-2", "provider": "codex-local",
            "requested": {"ratio": "16:9"}, "effective": {"ratio": "16:9"},
            "dropped": [],
        },
    )
    stored = json.loads(await fake_redis.get(f"codex_upload:{ticket}"))
    assert stored["attribution"]["canvas_id"] == 42
    assert stored["attribution"]["requested"]["ratio"] == "16:9"


@pytest.mark.asyncio
async def test_upload_files_the_product_as_a_canvas_run_not_an_upload(fake_redis):
    """origin_kind must say what this is; params must carry the four keys."""
    captured = {}

    async def fake_register(**kw):
        captured.update(kw)
        return {"id": 99}

    ...  # mint a ticket with attribution, POST a real 1536x864 PNG
    origin = captured["origin"]
    assert origin.kind == "canvas_run"
    assert origin.canvas_id == 42 and origin.node_id == "n1"
    assert origin.prompt == "a cat" and origin.model == "gpt-image-2"
    assert origin.params["honored"] is True
    assert origin.params["produced_by"] == "codex-daemon"   # kept as a note


@pytest.mark.asyncio
async def test_a_ticket_without_attribution_still_uploads(fake_redis):
    """Older daemons / other callers keep working; the record is just thinner."""
    ...
    assert captured["origin"].kind == "canvas_upload"
```

（`fake_redis` fixture：本仓已有的 redis 桩，按 `tests/` 现有用法接；若无则用 `monkeypatch` 替换 `get_async_redis` 返回一个 dict 支撑的桩，`GETDEL` 需自行实现。实施者按现状选，并在报告里写明选了哪种。）

- [ ] **Step 2: 跑一遍确认失败**

Run: `cd backend && uv run pytest tests/test_daemon_result_attribution.py -v`
Expected: `TypeError: mint_upload_ticket() got an unexpected keyword argument 'attribution'`

- [ ] **Step 3: 实现**

`mint_upload_ticket` 加可选 `attribution: dict | None = None`，存进同一个 JSON；`_consume_upload_ticket` 原样返回。`_register_daemon_result` 改为按 attribution 构造：

```python
    attribution = kwargs.pop("attribution", None) or {}
    if attribution:
        origin = GenerationOrigin(
            kind="canvas_run",
            canvas_id=attribution.get("canvas_id"),
            node_id=attribution.get("node_id"),
            prompt=attribution.get("prompt"),
            model=attribution.get("model"),
            provider=attribution.get("provider"),
            params={**outcome, "produced_by": "codex-daemon", "job_id": job_id},
            derivation_kind=f"{media_kind}_gen",
        )
    else:
        # No attribution: an older daemon, or a caller that is genuinely an
        # upload. A thinner record beats a wrong one.
        origin = GenerationOrigin(kind="canvas_upload", params=params)
```

上传端点在 `_register_daemon_result` 之前测量（文件已在 `tmp_path`），用 Task 2 的 `build_outcome_params_from_dicts` 组装。

`dispatch_to_daemon` 加 `attribution: dict | None = None` 转发给 `mint_upload_ticket`；`canvas_generation.py` 的 daemon 分支传入 `{canvas_id, node_id, prompt, model, provider, requested, effective, dropped}`。

⚠️ **不要把整个 `payload` 塞进 attribution** —— 它含 `ref_urls` 与完整 prompt，Redis 里存一次、TTL 内可读，没必要放大暴露面。只放上面那八个键。

- [ ] **Step 4: 跑测试**

Run: `cd backend && uv run pytest tests/test_daemon_result_attribution.py tests/test_codex_daemon_upload.py tests/test_codex_daemon_dispatch.py -q`
Expected: 全 PASS，既有 daemon 测试不回归。

- [ ] **Step 5: 突变验证**

把 `_register_daemon_result` 的 `if attribution:` 改成 `if False:`，确认 `test_upload_files_the_product_as_a_canvas_run_not_an_upload` 转红；还原再跑绿。贴输出。

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/codex_daemon_router.py backend/app/services/codex/daemon_dispatch.py backend/app/workflows/canvas_generation.py backend/tests/
git -c user.email=ezufofoti59@gmail.com -c user.name=heygo commit -m "feat(gen): daemon 产物带归因入库 —— 不再与手工上传在库里长得一模一样"
```

---

### Task 5: 全量回归 + 真栈验收

**Files:** 无新增；回归与验收

- [ ] **Step 1: 全量与 lint**

```bash
cd backend
uv run pytest -q 2>&1 | tail -3
uv run black --check app tests && uv run isort --check-only app tests && uv run flake8 app tests
```
Expected: 全过；三个 lint rc=0。

- [ ] **Step 2: 真栈验收（P1 欠下的那一次，现在有数据可查了）**

部署后，用调试账号在画布上各生成一张：

| provider | 请求 | 期望 |
|---|---|---|
| codex-local | 16:9 | `honored` 有值（true/false 都算通过——**关键是有记录**） |
| jimeng-cli 或 jimeng-local | 16:9 | `honored=true` |
| doubao/ark | 16:9 | `honored=true` |

```bash
docker exec nous-db psql -U postgres -p 55434 -d postgres -Atc "
SELECT id, provider, params->>'honored' AS honored,
       params->'requested'->>'ratio' AS want,
       params->'measured'->>'width' || 'x' || (params->'measured'->>'height') AS got,
       origin_kind
FROM generated_media
WHERE created_at > now() - interval '1 hour' AND params ? 'honored'
ORDER BY created_at DESC;"
```

Expected：每行 `honored` 非空、`origin_kind='canvas_run'`（含 daemon 那张）、`got` 与 `want` 对得上或被如实记为 false。

**对照（改动前的地面真值，2026-08-29 实测）**：同样的查询返回 **0 行**——`aspect_honored` 从未落过库，daemon 产物一律 `canvas_upload` 且 prompt/model/ratio 全空。

- [ ] **Step 3: 回填口径（不做，只记录）**

既有历史行没有这四键。**不回填** —— 测量需要重新读取每个产物文件，而 `honored` 对历史行的价值低于回填风险。查询一律带 `WHERE params ? 'honored'` 过滤。把这条写进 PR 正文。

- [ ] **Step 4: Commit + PR**

```bash
git push -u origin <branch>
gh pr create --base master --title "feat(gen): 出图生成请求契约 P2 —— 产出记录（量-比-记）" --body-file <正文引用 spec §3.3 与本节验收表>
```

---

## Self-Review

**Spec 覆盖（P2 范围）**
- §3.3 量（Pillow/ffprobe）→ Task 1 ✅；比（6% 容差，沿用同一常数）→ Task 1 ✅；记（四键进 `params`）→ Task 2/3 ✅
- §3.3 daemon 归因反查 → Task 4 ✅（走 ticket，不新增表）
- §3.3「记录，不拦截」→ Task 3 Step 1 的第二、三条测试直接钉住 ✅
- §6.2 真栈逐 provider 量像素 → Task 5 Step 2 ✅
- §6.4 daemon 归因用 SQL 断言、以旧的 `canvas_upload` 空归因作反例 → Task 5 Step 2 ✅
- `codex.py` 的 `result.raw` 透传已在 P1 Task 3 完成，本期不重做 ✅

**占位扫描**：Task 4 Step 1 的第二、三条测试体里有 `...` 省略号——那是**故意的**，因为 redis 桩的形状取决于本仓现有 fixture，实施者需按现状选一种并在报告里写明。其余步骤均有可直接运行的代码。⚠️ 执行者注意：这两处必须补全为真实测试，不得留 `...`。

**类型一致性**：`Measured` 三字段在 Task 1 定义、Task 2/3 消费，名字一致 ✅；`build_outcome_params`（吃 `GenerationRequest`）与 `build_outcome_params_from_dicts`（吃 dict，供 DBOS step 之间传递）是两个入口共用一段逻辑，Task 2 一并实现 ✅；`compare_aspect` 的三态返回（True/False/None）在 Task 2 的 `honored` 与 Task 3 的断言里一致 ✅。

**已知风险**
- **远端 URL 产物（ark）不在本地磁盘**，`persist_canvas_generation_step` 拿不到文件，`measured` 会是 null。Task 3 的注释写明了这是诚实缺口而非遗漏；若要覆盖需先下载，成本与收益留给 P2 之后评估。
- ffprobe 超时设了 30s；视频文件很大时可能不够。测不出按 null 记，不影响生成成功。
- `_knobs_of` 被 `outcome.py` 和 `request.knobs_dict()` 共用，后者 import 前者的私有名——实施时若觉得别扭，可把 `_knobs_of` 提升为公开 `knobs_of`，两处同步改，**不要各写一份**。
