# 出图生成请求契约 — P1（契约 + 后端三分支）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `canvas_generation` 的三条分支（服务器 / codex-daemon / dreamina-daemon）从同一个 `GenerationRequest` 取参，provider 自报能力，做不到的旋钮不再静默吞——P1 合并后 codex-local 的 16:9 应当正确。

**Architecture:** 新建 `app/services/generation/`（`aspect.py` 共享比例表、`request.py` 请求对象与 reconcile），`provider_protocols/base.py` 加 `ProviderCapabilities` 并由各 protocol 声明；`canvas_generation.generate_canvas_media_step` 入口一次解析成 `GenerationRequest`，三条分支只读它；`dropped_knobs` 随结果写进 `task_tracking.metadata`。daemon payload 本期**双发**（新键 + 旧 `size`），P3 再删旧键。

**Tech Stack:** Python 3.13 / FastAPI / DBOS workflow / pytest(asyncio) · 后端目录 `backend/`，运行 `cd backend && uv run pytest`。

**Spec:** `docs/superpowers/specs/2026-08-29-generation-request-contract-design.md`（§3 契约、§5 兼容、§6 验收、§8 决策）

## Global Constraints

- 新代码禁止 `text()` 裸 SQL（CLAUDE.md「裸 SQL 全量 ORM 化」）；本期不碰 DB。
- 不改 `mediahub_models` 表；capabilities 放代码（spec §8 决策 1）。
- 不支持的旋钮：**dispatch 时丢弃并记录**，永不静默（spec §3.2）；UI 隐藏归 P4。
- daemon payload 旧键 `size` 本期**保留并由 ratio 换算填值**（spec §5：旧 0.3.0 daemon 不崩），新键并行发送。
- jimeng 图片 `max_refs=0`：CLI 图片链路无 i2i（`build_image_args` 纯 text2image），这是诚实声明不是修复。
- 比例档位字面量唯一来源：`app/services/generation/aspect.py`；`codex_cli.py` 改为 import，不再自持表。
- 测试用 `unittest.mock.patch` + `SimpleNamespace`（沿用 `backend/tests/test_canvas_generation_workflow.py` 风格）；不依赖网络（conftest 已摘代理）。
- commit 用户：`git -c user.email=ezufofoti59@gmail.com -c user.name=heygo`；分支从 `origin/master` 建，用 `git switch -c`，**不要 stash/pop**。

---

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `backend/app/services/generation/__init__.py` | 包 | 新建（空） |
| `backend/app/services/generation/aspect.py` | 比例 ↔ 数值 / 短语 / codex size 三张表 + `aspect_instruction()` + `nearest_ratio()` | 新建（从 `codex_cli.py` 迁出） |
| `backend/app/services/generation/request.py` | `GenerationRequest`、`from_params`、`reconcile`、`to_codex_daemon_payload`、`to_dreamina_daemon_kwargs` | 新建 |
| `backend/app/services/ai/provider_protocols/base.py` | `ProviderCapabilities` 数据类 + `ProviderProtocol.capabilities` 默认 | 修改 |
| `backend/app/services/ai/provider_protocols/{codex,codex_local,jimeng,ark}.py` | 各自声明 capabilities；`codex.py` 透传 `result.raw` | 修改 |
| `backend/app/services/media/parsers/video_providers/codex_cli.py` | 删本地三张表，改 import | 修改 |
| `backend/app/workflows/canvas_generation.py` | 入口解析 `GenerationRequest`；三分支改读它；`dropped_knobs` 入结果与 metadata | 修改 |
| `backend/tests/services/generation/test_aspect.py` | | 新建 |
| `backend/tests/services/generation/test_request.py` | | 新建 |
| `backend/tests/test_provider_capabilities.py` | 所有出图 protocol 都声明了能力，且与 spec §3.2 表一致 | 新建 |
| `backend/tests/test_canvas_generation_workflow.py` | 三分支各加断言 | 修改 |

---

### Task 1: 比例表抽成共享模块 `aspect.py`

**Files:**
- Create: `backend/app/services/generation/__init__.py`
- Create: `backend/app/services/generation/aspect.py`
- Modify: `backend/app/services/media/parsers/video_providers/codex_cli.py:59-150`
- Test: `backend/tests/services/generation/test_aspect.py`

**Interfaces:**
- Produces:
  - `ASPECT_RATIOS: dict[str, float]`（8 档 → 数值）
  - `ASPECT_PHRASES: dict[str, str]`（8 档 → 英文短语）
  - `CODEX_SIZES: dict[str, str]`（8 档 → `WxH`）、`CODEX_DEFAULT_SIZE = "1024x1024"`
  - `ASPECT_TOLERANCE = 0.06`
  - `aspect_instruction(aspect: str) -> str`（未知/空 → `""`）
  - `nearest_ratio(width: int, height: int) -> str | None`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/services/generation/test_aspect.py
from app.services.generation.aspect import (
    ASPECT_PHRASES,
    ASPECT_RATIOS,
    CODEX_DEFAULT_SIZE,
    CODEX_SIZES,
    aspect_instruction,
    nearest_ratio,
)

UI_RATIOS = {"1:1", "2:3", "3:2", "3:4", "4:3", "9:16", "16:9", "21:9"}


def test_tables_cover_exactly_the_ui_ratios():
    # 前端 RATIO_LABELS 的 8 档（不含 'auto'，auto 在前端 dispatch 时已解析）
    assert set(ASPECT_RATIOS) == UI_RATIOS
    assert set(ASPECT_PHRASES) == UI_RATIOS
    assert set(CODEX_SIZES) == UI_RATIOS


def test_aspect_instruction_is_appended_sentence_for_known_aspect():
    text = aspect_instruction("16:9")
    assert text.startswith("\n\n")
    assert "16:9 landscape" in text


def test_aspect_instruction_is_empty_for_unknown_or_blank():
    # IC 自适应刻意传空：不能替它发明一个形状
    assert aspect_instruction("") == ""
    assert aspect_instruction("auto") == ""
    assert aspect_instruction("7:5") == ""


def test_nearest_ratio_snaps_to_offered_values():
    assert nearest_ratio(1920, 1080) == "16:9"
    assert nearest_ratio(1086, 1448) == "3:4"   # 0.75，codex 竖版真实产出
    assert nearest_ratio(1600, 1000) == "3:2"   # 1.6 更靠近 1.5 而非 1.78
    assert nearest_ratio(0, 100) is None


def test_codex_default_size_is_square():
    assert CODEX_DEFAULT_SIZE == "1024x1024"
```

- [ ] **Step 2: 跑一遍确认失败**

Run: `cd backend && uv run pytest tests/services/generation/test_aspect.py -v`
Expected: `ModuleNotFoundError: No module named 'app.services.generation'`

- [ ] **Step 3: 新建模块（内容从 `codex_cli.py` 搬，名字去下划线）**

```python
# backend/app/services/generation/__init__.py
"""Provider-agnostic generation contract: aspect tables, request object,
capabilities reconciliation. Nothing here imports a concrete provider."""
```

```python
# backend/app/services/generation/aspect.py
"""Aspect ratio vocabulary shared by every image/video path.

Single source of truth for the eight ratios the canvas offers. Provider
modules import from here; none keeps its own copy (that is how the daemon
path ended up with a different key than the server path).
"""
from __future__ import annotations

from typing import Optional

ASPECT_RATIOS: dict[str, float] = {
    "21:9": 21 / 9,
    "16:9": 16 / 9,
    "3:2": 3 / 2,
    "4:3": 4 / 3,
    "1:1": 1.0,
    "3:4": 3 / 4,
    "2:3": 2 / 3,
    "9:16": 9 / 16,
}

# Sentence fragments appended to a prompt for providers that only honour
# shape through language (codex — measured 2026-08-23, `--size` is ignored).
ASPECT_PHRASES: dict[str, str] = {
    "21:9": "21:9 ultra-wide landscape (much wider than tall)",
    "16:9": "16:9 landscape (wider than tall)",
    "3:2": "3:2 landscape (wider than tall)",
    "4:3": "4:3 landscape (wider than tall)",
    "1:1": "1:1 square (equal width and height)",
    "3:4": "3:4 portrait (taller than wide)",
    "2:3": "2:3 portrait (taller than wide)",
    "9:16": "9:16 tall portrait (much taller than wide)",
}

# gpt-image-2-skill --size values. Kept for the daemon's `size` key (old
# daemons need it) even though the upstream does not honour it.
CODEX_SIZES: dict[str, str] = {
    "21:9": "1536x1024",
    "16:9": "1536x1024",
    "3:2": "1536x1024",
    "4:3": "1536x1024",
    "1:1": "1024x1024",
    "3:4": "1024x1536",
    "2:3": "1024x1536",
    "9:16": "1024x1536",
}
CODEX_DEFAULT_SIZE = "1024x1024"

# |got/want - 1| within this counts as honoured (P2 uses it when measuring).
ASPECT_TOLERANCE = 0.06


def aspect_instruction(aspect: str) -> str:
    """The sentence appended to a prompt to pin the output shape.

    Appended, never prepended, and only for a known aspect: the user's words
    stay first and intact. Unknown or empty adds nothing — "let the model
    choose" is a real request (IC 自适应 sends an empty aspect on purpose).
    """
    phrase = ASPECT_PHRASES.get((aspect or "").strip())
    if not phrase:
        return ""
    return (
        f"\n\nOutput image aspect ratio: {phrase}. "
        "The whole image must have this shape."
    )


def nearest_ratio(width: int, height: int) -> Optional[str]:
    """The offered ratio closest to width/height, or None for a degenerate size."""
    if width <= 0 or height <= 0:
        return None
    target = width / height
    return min(ASPECT_RATIOS, key=lambda r: abs(ASPECT_RATIOS[r] - target))
```

- [ ] **Step 4: `codex_cli.py` 改为 import（删除本地表与 `_aspect_instruction`）**

在 `codex_cli.py` 顶部 import 区加：

```python
from app.services.generation.aspect import (
    ASPECT_PHRASES as _ASPECT_TO_PHRASE,
    ASPECT_RATIOS as _ASPECT_TO_RATIO,
    ASPECT_TOLERANCE as _ASPECT_TOLERANCE,
    CODEX_DEFAULT_SIZE as _DEFAULT_SIZE,
    CODEX_SIZES as _ASPECT_TO_SIZE,
    aspect_instruction as _aspect_instruction,
)
```

然后删除 `codex_cli.py` 第 59-150 行里这些定义：`_ASPECT_TO_SIZE`、`_DEFAULT_SIZE`、`_ASPECT_TO_PHRASE`、`_ASPECT_TO_RATIO`、`_ASPECT_TOLERANCE`、`def _aspect_instruction`。**保留**它们上方的注释块（五级探针实测记录是知识，不是代码），把注释挪到 import 之下。别名保持原名，调用点零改动。

- [ ] **Step 5: 跑测试**

Run: `cd backend && uv run pytest tests/services/generation/test_aspect.py tests -k "codex" -v`
Expected: 新增 5 条 PASS；既有 codex 相关测试仍 PASS（表内容未变，只换了归属）。

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/generation backend/app/services/media/parsers/video_providers/codex_cli.py backend/tests/services/generation/test_aspect.py
git -c user.email=ezufofoti59@gmail.com -c user.name=heygo commit -m "refactor(gen): 比例表抽成 services/generation/aspect.py 共享模块 —— codex_cli 改 import，daemon 路径以后拿同一份"
```

---

### Task 2: `GenerationRequest` 与 `reconcile`

**Files:**
- Create: `backend/app/services/generation/request.py`
- Test: `backend/tests/services/generation/test_request.py`

**Interfaces:**
- Consumes: Task 1 的 `CODEX_SIZES`, `CODEX_DEFAULT_SIZE`, `aspect_instruction`
- Produces:
  ```python
  @dataclass(frozen=True)
  class GenerationRequest:
      kind: Literal["image", "video"]; prompt: str; model: str
      ratio: str | None; quality: str | None; resolution: str | None
      refs: tuple[str, ...]; negative: str | None
      video_mode: Literal["frames", "multimodal"] | None; duration: int | None
      @classmethod
      def from_params(cls, *, kind, prompt, model, params: dict, source_url: str | None) -> "GenerationRequest"
      def reconcile(self, caps: "ProviderCapabilities") -> tuple["GenerationRequest", list[str]]
      def to_codex_daemon_payload(self, *, engine_model: str, ref_urls: list[str]) -> dict
  ```
  `ProviderCapabilities` 由 Task 3 定义；Task 2 先用 `typing.Protocol` 形状避免循环 import（见代码）。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/services/generation/test_request.py
from dataclasses import dataclass, field

import pytest

from app.services.generation.request import GenerationRequest


@dataclass(frozen=True)
class Caps:
    ratios: frozenset = frozenset({"1:1", "16:9", "9:16"})
    quality: bool = False
    resolution: bool = False
    max_refs: int = 0
    negative: bool = False
    video_modes: frozenset = field(default_factory=frozenset)


def test_from_params_reads_the_frontend_keys_not_invented_ones():
    req = GenerationRequest.from_params(
        kind="image",
        prompt="a cat",
        model="codex-local-image",
        params={"ratio": "16:9", "quality": "high", "resolution": "2k",
                "source_urls": ["/api/v1/generated-media/1/cover"]},
        source_url=None,
    )
    assert req.ratio == "16:9"
    assert req.quality == "high"
    assert req.resolution == "2k"
    assert req.refs == ("/api/v1/generated-media/1/cover",)
    assert req.negative is None
    assert req.duration is None


def test_from_params_video_reads_aspect_and_duration_and_mode():
    req = GenerationRequest.from_params(
        kind="video", prompt="p", model="m",
        params={"aspect": "9:16", "duration": "5", "video_mode": "frames"},
        source_url="/api/v1/generated-media/2/cover",
    )
    assert req.ratio == "9:16"          # 视频用 aspect 键，统一到 ratio
    assert req.duration == 5
    assert req.video_mode == "frames"
    assert req.refs == ("/api/v1/generated-media/2/cover",)  # 无 source_urls 时回退单源


def test_refs_capped_at_nine():
    req = GenerationRequest.from_params(
        kind="image", prompt="p", model="m",
        params={"source_urls": [f"/u/{i}" for i in range(12)]}, source_url=None,
    )
    assert len(req.refs) == 9


def test_reconcile_drops_unsupported_knobs_and_names_them():
    req = GenerationRequest.from_params(
        kind="image", prompt="p", model="m",
        params={"ratio": "21:9", "quality": "high", "resolution": "4k",
                "source_urls": ["/u/1", "/u/2"]},
        source_url=None,
    )
    eff, dropped = req.reconcile(Caps())   # 不支持 21:9 / quality / resolution / refs
    assert eff.ratio is None
    assert eff.quality is None
    assert eff.resolution is None
    assert eff.refs == ()
    assert dropped == ["ratio", "quality", "resolution", "refs"]


def test_reconcile_keeps_supported_knobs_and_reports_nothing():
    req = GenerationRequest.from_params(
        kind="image", prompt="p", model="m",
        params={"ratio": "16:9", "source_urls": ["/u/1"]}, source_url=None,
    )
    eff, dropped = req.reconcile(Caps(max_refs=9))
    assert eff == req.__class__(**{**req.__dict__})
    assert dropped == []


def test_reconcile_truncates_refs_to_max_and_reports_partial_drop():
    req = GenerationRequest.from_params(
        kind="image", prompt="p", model="m",
        params={"source_urls": ["/u/1", "/u/2", "/u/3"]}, source_url=None,
    )
    eff, dropped = req.reconcile(Caps(max_refs=1))
    assert eff.refs == ("/u/1",)
    assert dropped == ["refs"]


def test_codex_daemon_payload_dual_sends_size_and_ratio_and_appends_aspect_phrase():
    req = GenerationRequest.from_params(
        kind="image", prompt="a cat", model="codex-local-image",
        params={"ratio": "16:9", "quality": "high"}, source_url=None,
    )
    payload = req.to_codex_daemon_payload(engine_model="gpt-image-2", ref_urls=["https://x/1.png"])
    assert payload["engine"] == "codex"
    assert payload["ratio"] == "16:9"
    assert payload["size"] == "1536x1024"        # 旧 daemon 靠这个键
    assert payload["quality"] == "high"
    assert payload["model"] == "gpt-image-2"     # 来自目录 row，不是 params.actual_model
    assert payload["ref_urls"] == ["https://x/1.png"]
    assert payload["prompt"].startswith("a cat")
    assert "16:9 landscape" in payload["prompt"]  # 画幅短语并入 prompt


def test_codex_daemon_payload_without_ratio_sends_default_size_and_bare_prompt():
    req = GenerationRequest.from_params(kind="image", prompt="a cat", model="m", params={}, source_url=None)
    payload = req.to_codex_daemon_payload(engine_model="", ref_urls=[])
    assert payload["size"] == "1024x1024"
    assert payload["ratio"] is None
    assert payload["prompt"] == "a cat"
```

- [ ] **Step 2: 跑一遍确认失败**

Run: `cd backend && uv run pytest tests/services/generation/test_request.py -v`
Expected: `ImportError: cannot import name 'GenerationRequest'`

- [ ] **Step 3: 实现**

```python
# backend/app/services/generation/request.py
"""The one request shape every generation path reads from.

Three dispatch branches used to hand-copy a dict each; they drifted (the
daemon branch sent `size` while the frontend only ever sends `ratio`, and
`params.actual_model` which nothing sets). Parse `params` ONCE into this
object at the workflow entrance; branches only read it.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal, Optional, Protocol

from app.services.generation.aspect import (
    CODEX_DEFAULT_SIZE,
    CODEX_SIZES,
    aspect_instruction,
)

MAX_REFS = 9  # IC caps references at 9

VideoMode = Literal["frames", "multimodal"]


class CapabilitiesLike(Protocol):
    """Structural view of ProviderCapabilities (defined in provider_protocols.base)
    so this module never imports a concrete provider."""

    ratios: frozenset[str]
    quality: bool
    resolution: bool
    max_refs: int
    negative: bool
    video_modes: frozenset[str]


def _clean(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


@dataclass(frozen=True)
class GenerationRequest:
    kind: Literal["image", "video"]
    prompt: str
    model: str
    ratio: Optional[str]
    quality: Optional[str]
    resolution: Optional[str]
    refs: tuple[str, ...]
    negative: Optional[str]
    video_mode: Optional[VideoMode]
    duration: Optional[int]

    @classmethod
    def from_params(
        cls,
        *,
        kind: str,
        prompt: str,
        model: str,
        params: dict[str, Any],
        source_url: Optional[str],
    ) -> "GenerationRequest":
        k: Literal["image", "video"] = "video" if kind == "video" else "image"
        raw_refs = params.get("source_urls")
        refs = [
            u for u in (raw_refs if isinstance(raw_refs, list) else [])
            if isinstance(u, str) and u
        ][:MAX_REFS] or ([source_url] if source_url else [])
        raw_duration = params.get("duration")
        mode = _clean(params.get("video_mode"))
        return cls(
            kind=k,
            prompt=prompt,
            model=model,
            # Video knobs arrive as `aspect`; images as `ratio`. One field here.
            ratio=_clean(params.get("aspect") if k == "video" else params.get("ratio")),
            quality=_clean(params.get("quality")),
            resolution=_clean(params.get("resolution")),
            refs=tuple(refs),
            negative=_clean(params.get("negative")),
            video_mode=mode if mode in ("frames", "multimodal") else None,  # type: ignore[arg-type]
            duration=int(raw_duration) if raw_duration else None,
        )

    def reconcile(self, caps: CapabilitiesLike) -> tuple["GenerationRequest", list[str]]:
        """Drop what this provider cannot honour and SAY which knobs went.

        The list is in a fixed order so metadata/tests read the same way.
        Never silent: an empty list means everything requested will be sent.
        """
        dropped: list[str] = []
        eff = self
        if eff.ratio and eff.ratio not in caps.ratios:
            eff = replace(eff, ratio=None)
            dropped.append("ratio")
        if eff.quality and not caps.quality:
            eff = replace(eff, quality=None)
            dropped.append("quality")
        if eff.resolution and not caps.resolution:
            eff = replace(eff, resolution=None)
            dropped.append("resolution")
        if len(eff.refs) > caps.max_refs:
            eff = replace(eff, refs=eff.refs[: caps.max_refs])
            dropped.append("refs")
        if eff.negative and not caps.negative:
            eff = replace(eff, negative=None)
            dropped.append("negative")
        if eff.video_mode and eff.video_mode not in caps.video_modes:
            eff = replace(eff, video_mode=None)
            dropped.append("video_mode")
        return eff, dropped

    def to_codex_daemon_payload(self, *, engine_model: str, ref_urls: list[str]) -> dict[str, Any]:
        """Payload for `tools/codex-daemon` image jobs.

        Dual-sends `size` (what a 0.3.0 daemon reads) and `ratio` (what the
        P3 daemon will read); `size` goes away once P3 ships. The aspect phrase
        is appended to the prompt here because codex only honours shape
        through language — the daemon must not have to know that.
        """
        return {
            "engine": "codex",
            "prompt": self.prompt + aspect_instruction(self.ratio or ""),
            "ratio": self.ratio,
            "size": CODEX_SIZES.get(self.ratio or "", CODEX_DEFAULT_SIZE),
            "quality": self.quality,
            "model": engine_model or "",
            "ref_urls": list(ref_urls),
        }
```

- [ ] **Step 4: 跑测试**

Run: `cd backend && uv run pytest tests/services/generation/ -v`
Expected: 全部 PASS（`test_reconcile_keeps_supported_knobs_and_reports_nothing` 里的 `eff == …` 用 dataclass 相等性）。

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/generation/request.py backend/tests/services/generation/test_request.py
git -c user.email=ezufofoti59@gmail.com -c user.name=heygo commit -m "feat(gen): GenerationRequest —— 唯一请求形状 + reconcile 按能力丢弃并点名 + codex daemon payload 双发 size/ratio"
```

---

### Task 3: `ProviderCapabilities` 与各 protocol 声明

**Files:**
- Modify: `backend/app/services/ai/provider_protocols/base.py`（`ProviderProtocol` 类内）
- Modify: `backend/app/services/ai/provider_protocols/codex.py`、`codex_local.py`、`jimeng.py`、`ark.py`
- Modify: `backend/app/services/ai/provider_protocols/codex.py:70-75`（`metadata` 透传 raw）
- Test: `backend/tests/test_provider_capabilities.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class ProviderCapabilities:
      ratios: frozenset[str]; quality: bool; resolution: bool
      max_refs: int; negative: bool; video_modes: frozenset[str]
      honours_ratio: Literal["native", "prompt_hint", "none"]
  ALL_RATIOS: frozenset[str]   # 8 档
  ProviderProtocol.capabilities: ProviderCapabilities  # 类属性，默认 = 什么都不支持
  ```

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_provider_capabilities.py
"""Every image/video protocol declares what it can honour, and the values
match the audited matrix in the spec (§1.2 / §3.2). A protocol that forgets
to declare gets the restrictive default — which would drop every knob and
show up as dropped_knobs immediately, not as a silently ignored ratio."""
from app.services.ai.provider_protocols import _registry
from app.services.ai.provider_protocols.base import ALL_RATIOS, ProviderCapabilities

UI_RATIOS = frozenset({"1:1", "2:3", "3:2", "3:4", "4:3", "9:16", "16:9", "21:9"})


def _proto(key: str):
    p = _registry.resolve_generation_protocol(key)
    assert p is not None, f"no protocol for {key}"
    return p


def test_all_ratios_equals_the_ui_vocabulary():
    assert ALL_RATIOS == UI_RATIOS


def test_every_generation_protocol_declares_capabilities_explicitly():
    for key in ("codex", "codex-local", "jimeng-cli", "jimeng-local", "doubao"):
        caps = _proto(key).capabilities
        assert isinstance(caps, ProviderCapabilities)
        assert caps is not ProviderCapabilities.none(), f"{key} still on the restrictive default"


def test_codex_family_matrix():
    for key in ("codex", "codex-local"):
        caps = _proto(key).capabilities
        assert caps.ratios == ALL_RATIOS
        assert caps.quality is True
        assert caps.resolution is False      # "尺寸由模型定"
        assert caps.max_refs == 9
        assert caps.negative is False
        assert caps.honours_ratio == "prompt_hint"


def test_jimeng_family_matrix():
    for key in ("jimeng-cli", "jimeng-local"):
        caps = _proto(key).capabilities
        assert caps.ratios == ALL_RATIOS
        assert caps.quality is False
        assert caps.resolution is True
        assert caps.max_refs == 0            # 图片 CLI 无 i2i（视频另有首尾帧）
        assert caps.video_modes == frozenset({"frames", "multimodal"})
        assert caps.honours_ratio == "native"


def test_ark_matrix_is_honest_about_five_ratios_and_no_refs():
    caps = _proto("doubao").capabilities
    assert caps.ratios == frozenset({"16:9", "9:16", "1:1", "4:3", "3:4"})
    assert caps.max_refs == 0
    assert caps.quality is False and caps.resolution is False
```

- [ ] **Step 2: 跑一遍确认失败**

Run: `cd backend && uv run pytest tests/test_provider_capabilities.py -v`
Expected: `ImportError: cannot import name 'ALL_RATIOS'`

- [ ] **Step 3: `base.py` 加数据类与默认值**

在 `base.py` 的 `class ProviderProtocol:` 之前加：

```python
from dataclasses import dataclass
from typing import Literal

ALL_RATIOS: frozenset[str] = frozenset(
    {"1:1", "2:3", "3:2", "3:4", "4:3", "9:16", "16:9", "21:9"}
)


@dataclass(frozen=True)
class ProviderCapabilities:
    """What a generation provider can actually honour.

    Declared in code next to the implementation — a capability is a property
    of the code, not configuration; putting it in the catalog table would
    invent a third place that can disagree with the other two.
    """

    ratios: frozenset[str]
    quality: bool
    resolution: bool
    max_refs: int
    negative: bool
    video_modes: frozenset[str]
    honours_ratio: Literal["native", "prompt_hint", "none"]

    @classmethod
    def none(cls) -> "ProviderCapabilities":
        """The restrictive default: supports nothing. A protocol that forgets to
        declare drops every knob loudly (dropped_knobs) instead of ignoring
        them quietly — the failure mode this whole contract exists to end."""
        return _NONE


_NONE = ProviderCapabilities(
    ratios=frozenset(),
    quality=False,
    resolution=False,
    max_refs=0,
    negative=False,
    video_modes=frozenset(),
    honours_ratio="none",
)
```

在 `class ProviderProtocol:` 的类属性区（`model_types` 旁）加：

```python
    capabilities: ProviderCapabilities = ProviderCapabilities.none()
```

- [ ] **Step 4: 各 protocol 声明（类属性，紧挨 `key`/`aliases`）**

`codex.py` 的 `class CodexProtocol` 与 `codex_local.py` 的 `class CodexLocalProtocol`：

```python
    capabilities = ProviderCapabilities(
        ratios=ALL_RATIOS,
        quality=True,
        resolution=False,
        max_refs=9,
        negative=False,
        video_modes=frozenset(),
        honours_ratio="prompt_hint",
    )
```

`jimeng.py` 的 `class JimengProtocol`（`jimeng-local` 若是同一类的 alias 则一处即可；若 `_registry.py` 里是独立类，同样声明一份）：

```python
    capabilities = ProviderCapabilities(
        ratios=ALL_RATIOS,
        quality=False,
        resolution=True,
        max_refs=0,   # image CLI is text2image only; video refs are handled by video_modes
        negative=False,
        video_modes=frozenset({"frames", "multimodal"}),
        honours_ratio="native",
    )
```

`ark.py` 的 `class ArkProtocol`：

```python
    capabilities = ProviderCapabilities(
        ratios=frozenset({"16:9", "9:16", "1:1", "4:3", "3:4"}),  # = ark_image._ASPECT_TO_SIZE keys
        quality=False,
        resolution=False,
        max_refs=0,   # /images/generations is pure text-to-image
        negative=False,
        video_modes=frozenset(),
        honours_ratio="native",
    )
```

各文件顶部 import：`from app.services.ai.provider_protocols.base import ALL_RATIOS, ProviderCapabilities, ProviderProtocol`。

- [ ] **Step 5: `codex.py` 透传 raw（一行）**

`codex.py` 约第 70 行：

```python
            metadata={"mime": result.mime, **(result.raw or {})},
```

（与 `jimeng.py` 一致；`measured_size / requested_aspect / aspect_honored` 从此进 metadata，P2 落库时直接可用。）

- [ ] **Step 6: 跑测试**

Run: `cd backend && uv run pytest tests/test_provider_capabilities.py tests -k "protocol or registry" -v`
Expected: 5 条新增 PASS；既有 registry 测试 PASS。若 `jimeng-local` 解析到的是另一个类，按 Step 4 补声明后再跑。

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/ai/provider_protocols backend/tests/test_provider_capabilities.py
git -c user.email=ezufofoti59@gmail.com -c user.name=heygo commit -m "feat(gen): ProviderCapabilities —— 5 个出图 protocol 按核实矩阵声明能力；默认值是'什么都不支持'让漏声明立刻可见；codex.py 透传 result.raw"
```

---

### Task 4: 服务器分支改读 `GenerationRequest`，`dropped_knobs` 入结果与 task metadata

**Files:**
- Modify: `backend/app/workflows/canvas_generation.py:78-108`（入口）、`240-290`（服务器图片分支）、`370-388`（record step）
- Test: `backend/tests/test_canvas_generation_workflow.py`

**Interfaces:**
- Consumes: Task 2 `GenerationRequest.from_params/reconcile`；Task 3 `provider.capabilities`（`resolve_image_provider` 返回的 `provider` 是 protocol 构建的 image provider 对象——`capabilities` 取自 **protocol**，见 Step 3 的 `resolve_generation_protocol`）
- Produces: `generate_canvas_media_step` 返回 dict 多一个键 `"dropped_knobs": list[str]`；`record_canvas_generation_result_step` 把它写进 `task_tracking.metadata.dropped_knobs`

- [ ] **Step 1: 写失败测试（追加到 `test_canvas_generation_workflow.py`）**

```python
@pytest.mark.asyncio
async def test_image_step_reports_dropped_knobs_for_ark_and_sends_only_supported_ones():
    """ark: 5 ratios, no quality/resolution, no refs. Asking for 21:9 + quality
    must NOT silently reach the provider — and must be named in the result."""
    provider = SimpleNamespace(
        generate=AsyncMock(
            return_value=SimpleNamespace(image_url="https://cdn/x.png", image_path=None)
        )
    )
    ark_caps = SimpleNamespace(
        ratios=frozenset({"16:9", "9:16", "1:1", "4:3", "3:4"}),
        quality=False, resolution=False, max_refs=0, negative=False, video_modes=frozenset(),
    )
    with patch(
        "app.services.media.parsers.video_providers.db_registry.resolve_image_provider",
        new=AsyncMock(return_value=(provider, "seedream-4")),
    ), patch(
        "app.workflows.canvas_generation._capabilities_for",
        new=AsyncMock(return_value=ark_caps),
    ):
        out = await generate_canvas_media_step(
            kind="image", prompt="a cat", model="",
            params={"ratio": "21:9", "quality": "high", "source_urls": ["/api/v1/generated-media/1/cover"]},
            source_url=None,
        )

    kw = provider.generate.await_args.kwargs
    assert kw["aspect_ratio"] == ""            # 21:9 dropped, nothing invented
    assert kw["quality"] is None
    assert kw["reference_image_paths"] is None
    assert out["dropped_knobs"] == ["ratio", "quality", "refs"]


@pytest.mark.asyncio
async def test_image_step_dropped_knobs_is_empty_when_everything_is_supported():
    provider = SimpleNamespace(
        generate=AsyncMock(return_value=SimpleNamespace(image_url="https://cdn/x.png", image_path=None))
    )
    full = SimpleNamespace(
        ratios=frozenset({"16:9"}), quality=True, resolution=True, max_refs=9,
        negative=False, video_modes=frozenset(),
    )
    with patch(
        "app.services.media.parsers.video_providers.db_registry.resolve_image_provider",
        new=AsyncMock(return_value=(provider, "m")),
    ), patch("app.workflows.canvas_generation._capabilities_for", new=AsyncMock(return_value=full)):
        out = await generate_canvas_media_step(
            kind="image", prompt="a cat", model="", params={"ratio": "16:9"}, source_url=None,
        )
    assert provider.generate.await_args.kwargs["aspect_ratio"] == "16:9"
    assert out["dropped_knobs"] == []


@pytest.mark.asyncio
async def test_record_step_writes_dropped_knobs_into_task_metadata():
    from app.workflows.canvas_generation import record_canvas_generation_result_step

    manager = SimpleNamespace(patch_metadata=AsyncMock())
    with patch("dbos.DBOS.workflow_id", "wf-1"), patch(
        "app.services.infra.unified_task_manager.get_task_manager", return_value=manager
    ):
        await record_canvas_generation_result_step(
            {"result_url": "/r", "generated_media_id": 1, "media_kind": "image", "dropped_knobs": ["quality"]}
        )
    patched = manager.patch_metadata.await_args.args[1]
    assert patched["dropped_knobs"] == ["quality"]
```

- [ ] **Step 2: 跑一遍确认失败**

Run: `cd backend && uv run pytest tests/test_canvas_generation_workflow.py -k "dropped" -v`
Expected: `AttributeError: … has no attribute '_capabilities_for'`（第一、二条）；第三条 `KeyError: 'dropped_knobs'`。

- [ ] **Step 3: 实现 —— 入口解析 + 能力查找 + 服务器分支改读**

`canvas_generation.py` 顶部 import：

```python
from app.services.generation.request import GenerationRequest
```

在 `_local_engine` 之后加：

```python
async def _capabilities_for(actual_provider: str):
    """Capabilities of the protocol serving `actual_provider`; restrictive
    default when unknown (drops loudly rather than ignoring quietly)."""
    from app.services.ai.provider_protocols import resolve_generation_protocol
    from app.services.ai.provider_protocols.base import ProviderCapabilities

    proto = resolve_generation_protocol((actual_provider or "").lower())
    return proto.capabilities if proto else ProviderCapabilities.none()
```

`generate_canvas_media_step` 开头（`if kind == "video":` 之前）加一行解析：

```python
    req = GenerationRequest.from_params(
        kind=kind, prompt=prompt, model=model, params=params, source_url=source_url
    )
```

服务器图片分支（原 `provider, actual_model = await db_registry.resolve_image_provider(...)` 之后）改为：

```python
    caps = await _capabilities_for(_actual_provider_of(provider))
    eff, dropped = req.reconcile(caps)
    ...
        result = await provider.generate(
            prompt,
            gen_model,
            aspect_ratio=eff.ratio or "",
            reference_image_url=source_url if eff.refs else None,
            reference_image_paths=local_refs or None,
            quality=eff.quality,
            resolution=eff.resolution,
        )
```

其中 `local_refs` 的材料化循环改为遍历 `eff.refs`（而不是原来的 `ref_urls`），这样 `max_refs=0` 时根本不下载参考图。加一个小 helper：

```python
def _actual_provider_of(provider: Any) -> str:
    """The catalog key a built image provider was resolved from. Providers
    built by a protocol carry it as `provider_key`; older ones fall back to
    their `provider` name (codex / jimeng-cli / ark ⇒ doubao)."""
    key = getattr(provider, "provider_key", None) or getattr(provider, "provider", "") or ""
    return {"ark": "doubao"}.get(str(key), str(key))
```

返回 dict 加键：`"dropped_knobs": dropped`（视频分支与两个 daemon 分支本 Task 先返回 `[]`，Task 5/6 再接）。

- [ ] **Step 4: `record_canvas_generation_result_step` 写 metadata**

```python
    await get_task_manager().patch_metadata(
        task_id,
        {
            "result_url": result.get("result_url"),
            "generated_media_id": result.get("generated_media_id"),
            "media_kind": result.get("media_kind"),
            "dropped_knobs": list(result.get("dropped_knobs") or []),
        },
    )
```

`persist_canvas_generation_step` 把 `media["dropped_knobs"]` 原样带进它返回的 `result`（在它组装返回 dict 处加 `"dropped_knobs": list(media.get("dropped_knobs") or [])`）。

- [ ] **Step 5: 跑测试**

Run: `cd backend && uv run pytest tests/test_canvas_generation_workflow.py -v`
Expected: 新增 3 条 PASS；既有 `test_image_step_returns_remote_url_from_ark` 等仍 PASS（它们没 patch `_capabilities_for`——默认走 `resolve_generation_protocol`，对 `SimpleNamespace` provider 解析不到 → `none()` → 会把 `16:9` 丢掉而让既有断言 `aspect_ratio == "16:9"` 失败）。**处理**：给既有测试的 provider 加 `provider_key="doubao"` 或在这些测试里 patch `_capabilities_for` 返回全支持——选后者，逐条加 `patch("app.workflows.canvas_generation._capabilities_for", new=AsyncMock(return_value=full))`。这是测试跟改（提供了此前不存在的能力信息），不是放宽断言。

- [ ] **Step 6: Commit**

```bash
git add backend/app/workflows/canvas_generation.py backend/tests/test_canvas_generation_workflow.py
git -c user.email=ezufofoti59@gmail.com -c user.name=heygo commit -m "feat(gen): 服务器出图分支改读 GenerationRequest，按 provider 能力 reconcile；dropped_knobs 进结果与 task metadata"
```

---

### Task 5: codex-daemon 分支 —— 比例/模型/质量真正发出去

**Files:**
- Modify: `backend/app/workflows/canvas_generation.py:156-240`（daemon 分支的 `else:` codex payload）
- Test: `backend/tests/test_canvas_generation_workflow.py`

**Interfaces:**
- Consumes: Task 2 `req.to_codex_daemon_payload(engine_model=..., ref_urls=...)`；Task 4 的 `req`/`_capabilities_for`
- Produces: `dispatch_to_daemon(payload=...)` 收到的 codex payload 含 `ratio/size/quality/model/prompt(带画幅短语)/ref_urls/engine`

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.asyncio
async def test_codex_daemon_branch_sends_ratio_model_quality_not_size_only():
    """Before: payload = {prompt, size: params.get('size') → '', model: params.get('actual_model') → ''}.
    The user's 16:9, model pick and quality never left the server."""
    captured = {}

    async def fake_dispatch(**kw):
        captured.update(kw)
        return {"gen_id": "99"}

    with patch(
        "app.workflows.canvas_generation._local_engine",
        new=AsyncMock(return_value=("codex", "gpt-image-2")),
    ), patch(
        "app.services.codex.daemon_dispatch.dispatch_to_daemon", new=fake_dispatch
    ), patch(
        "app.workflows.canvas_generation._resolve_personal_team_id", new=AsyncMock(return_value=7)
    ), patch(
        "app.workflows.canvas_generation._capabilities_for",
        new=AsyncMock(return_value=SimpleNamespace(
            ratios=frozenset({"16:9"}), quality=True, resolution=False, max_refs=9,
            negative=False, video_modes=frozenset())),
    ):
        out = await generate_canvas_media_step(
            kind="image", prompt="a cat", model="codex-local-image",
            params={"ratio": "16:9", "quality": "high", "source_urls": ["/api/v1/generated-media/1/cover"]},
            source_url=None, user_id="u1",
        )

    p = captured["payload"]
    assert p["engine"] == "codex"
    assert p["ratio"] == "16:9"
    assert p["size"] == "1536x1024"                 # old daemons keep working
    assert p["quality"] == "high"
    assert p["model"] == "gpt-image-2"              # from the catalog row, not params.actual_model
    assert "16:9 landscape" in p["prompt"]
    assert p["ref_urls"][0].startswith("http")      # absolutised for the daemon
    assert out["provider"] == "codex-local"
    assert out["dropped_knobs"] == []
```

- [ ] **Step 2: 跑一遍确认失败**

Run: `cd backend && uv run pytest tests/test_canvas_generation_workflow.py -k codex_daemon_branch -v`
Expected: FAIL —— `p["ratio"]` KeyError（旧 payload 没这个键）。

- [ ] **Step 3: 实现**

daemon 分支开头（`if local:` 之后）加 reconcile；`_local_engine` 返回的 `engine` 映射回 provider key 查能力：

```python
    if local:
        engine, engine_model = local
        caps = await _capabilities_for("codex-local" if engine == "codex" else "jimeng-local")
        eff, dropped = req.reconcile(caps)
        ref_urls = [_absolute_media_url(u) for u in eff.refs]
```

删除原来那段手写的 `raw_refs/ref_urls` 推导。`else:`（codex）分支的 payload 改为（直接用 reconcile 后的 `eff`，**不要**再从 `params` 解析一次）：

```python
        else:
            payload = eff.to_codex_daemon_payload(engine_model=engine_model, ref_urls=ref_urls)
```

返回 dict 加 `"dropped_knobs": dropped`。

- [ ] **Step 4: 跑测试**

Run: `cd backend && uv run pytest tests/test_canvas_generation_workflow.py tests/test_codex_daemon_dispatch.py -v`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/app/workflows/canvas_generation.py backend/tests/test_canvas_generation_workflow.py
git -c user.email=ezufofoti59@gmail.com -c user.name=heygo commit -m "fix(gen): codex-daemon 分支发 ratio/size(双发)/quality/model —— 此前 ratio 在离开服务器前就被丢成空串"
```

---

### Task 6: dreamina-daemon 分支 —— `video_mode` 首尾帧/多模态到达 daemon

**Files:**
- Modify: `backend/app/workflows/canvas_generation.py`（daemon 分支 `if engine == "dreamina":` 的视频 payload）
- Test: `backend/tests/test_canvas_generation_workflow.py`

**Interfaces:**
- Consumes: `jimeng_cli.build_video_args(first_frame=, last_frame=, image_paths=, image_path=)`（已存在，见签名）；Task 4/5 的 `eff`
- Produces: dreamina 视频 payload 的 `submit_args` 按 `eff.video_mode` 选 `frames2video` / 多参考 / 单参考

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.asyncio
async def test_dreamina_daemon_video_frames_mode_uses_first_and_last_placeholders():
    captured = {}

    async def fake_dispatch(**kw):
        captured.update(kw)
        return {"gen_id": "5"}

    caps = SimpleNamespace(
        ratios=frozenset({"16:9"}), quality=False, resolution=True, max_refs=9,
        negative=False, video_modes=frozenset({"frames", "multimodal"}),
    )
    with patch("app.workflows.canvas_generation._local_engine", new=AsyncMock(return_value=("dreamina", "3.0"))), \
         patch("app.services.codex.daemon_dispatch.dispatch_to_daemon", new=fake_dispatch), \
         patch("app.workflows.canvas_generation._resolve_personal_team_id", new=AsyncMock(return_value=7)), \
         patch("app.workflows.canvas_generation._capabilities_for", new=AsyncMock(return_value=caps)):
        await generate_canvas_media_step(
            kind="video", prompt="walk", model="jimeng-local-video",
            params={"aspect": "16:9", "video_mode": "frames",
                    "source_urls": ["/api/v1/generated-media/1/cover", "/api/v1/generated-media/2/cover"]},
            source_url=None, user_id="u1",
        )

    args = captured["payload"]["submit_args"]
    assert args[0] == "frames2video"
    assert "--first={ref:0}" in args
    assert "--last={ref:1}" in args


@pytest.mark.asyncio
async def test_dreamina_daemon_video_mode_dropped_when_provider_lacks_it():
    captured = {}

    async def fake_dispatch(**kw):
        captured.update(kw)
        return {"gen_id": "5"}

    caps = SimpleNamespace(
        ratios=frozenset({"16:9"}), quality=False, resolution=True, max_refs=9,
        negative=False, video_modes=frozenset(),   # no frames support
    )
    with patch("app.workflows.canvas_generation._local_engine", new=AsyncMock(return_value=("dreamina", "3.0"))), \
         patch("app.services.codex.daemon_dispatch.dispatch_to_daemon", new=fake_dispatch), \
         patch("app.workflows.canvas_generation._resolve_personal_team_id", new=AsyncMock(return_value=7)), \
         patch("app.workflows.canvas_generation._capabilities_for", new=AsyncMock(return_value=caps)):
        out = await generate_canvas_media_step(
            kind="video", prompt="walk", model="jimeng-local-video",
            params={"aspect": "16:9", "video_mode": "frames",
                    "source_urls": ["/api/v1/generated-media/1/cover", "/api/v1/generated-media/2/cover"]},
            source_url=None, user_id="u1",
        )

    assert captured["payload"]["submit_args"][0] != "frames2video"
    assert "video_mode" in out["dropped_knobs"]
```

- [ ] **Step 2: 跑一遍确认失败**

Run: `cd backend && uv run pytest tests/test_canvas_generation_workflow.py -k dreamina_daemon -v`
Expected: 第一条 FAIL（`args[0]` 不是 `frames2video`——旧代码从不传 first/last）；第二条 FAIL（`dropped_knobs` 里没有 `video_mode`）。

- [ ] **Step 3: 实现（dreamina 分支的视频 payload）**

```python
            placeholders = [f"{{ref:{i}}}" for i in range(len(ref_urls))]
            if eff.kind == "video":
                frame_kwargs: dict = {}
                if eff.video_mode == "frames" and len(placeholders) >= 2:
                    frame_kwargs = {"first_frame": placeholders[0], "last_frame": placeholders[1]}
                elif eff.video_mode == "multimodal" and placeholders:
                    frame_kwargs = {"image_paths": placeholders}
                else:
                    frame_kwargs = {"image_path": placeholders[0] if placeholders else None}
                submit_args = build_video_args(
                    prompt=eff.prompt,
                    aspect=eff.ratio or "",
                    poll=90,
                    duration=eff.duration,
                    model_version=engine_model or None,
                    resolution=eff.resolution,
                    **frame_kwargs,
                )
            else:
                submit_args = build_image_args(
                    prompt=eff.prompt,
                    aspect=eff.ratio or "",
                    poll=60,
                    resolution_type=eff.resolution,
                    model_version=engine_model or None,
                )
```

（这段与服务器视频分支的 `frames/multimodal/单源` 三选一逻辑同构——服务器那段本 Task 不动，P2 之后统一。）

- [ ] **Step 4: 跑测试**

Run: `cd backend && uv run pytest tests/test_canvas_generation_workflow.py -v`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/app/workflows/canvas_generation.py backend/tests/test_canvas_generation_workflow.py
git -c user.email=ezufofoti59@gmail.com -c user.name=heygo commit -m "fix(gen): dreamina-daemon 视频分支按 video_mode 选 frames2video/多参考/单参考 —— 此前首尾帧模式在 daemon 上静默丢失"
```

---

### Task 7: 服务器视频分支改读 `eff` + 全量回归 + 真栈验收

**Files:**
- Modify: `backend/app/workflows/canvas_generation.py:108-150`（服务器视频分支）
- Test: 既有 `tests/test_canvas_generation_workflow.py` 视频用例

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.asyncio
async def test_server_video_branch_drops_frames_mode_when_unsupported_and_reports_it():
    provider = SimpleNamespace(
        generate_video=AsyncMock(return_value=SimpleNamespace(local_path="/tmp/v.mp4", mime="video/mp4"))
    )
    caps = SimpleNamespace(
        ratios=frozenset({"16:9"}), quality=False, resolution=True, max_refs=9,
        negative=False, video_modes=frozenset(),
    )
    with patch(
        "app.services.media.parsers.video_providers.db_registry.resolve_video_provider",
        new=AsyncMock(return_value=(provider, "3.0")),
    ), patch("app.workflows.canvas_generation._capabilities_for", new=AsyncMock(return_value=caps)), \
         patch("app.workflows.canvas_generation.generated_media_local_path", new=_fake_local_path_cm("/tmp/ref.png")):
        out = await generate_canvas_media_step(
            kind="video", prompt="walk", model="",
            params={"aspect": "16:9", "video_mode": "frames",
                    "source_urls": ["/api/v1/generated-media/1/cover", "/api/v1/generated-media/2/cover"]},
            source_url=None,
        )
    kw = provider.generate_video.await_args.kwargs
    assert "first_frame" not in kw
    assert out["dropped_knobs"] == ["video_mode"]
```

- [ ] **Step 2: 跑一遍确认失败**

Run: `cd backend && uv run pytest tests/test_canvas_generation_workflow.py -k server_video_branch -v`
Expected: FAIL —— `first_frame` 仍在 kwargs（旧分支直接读 `params["video_mode"]`）。

- [ ] **Step 3: 实现**

服务器视频分支里把 `video_mode = str(params.get("video_mode") or "")`、`raw_duration`、`ref_urls` 推导、`kwargs["aspect"]/["duration"]/["resolution"]` 全部改为读 `eff`（`caps = await _capabilities_for(_actual_provider_of(provider)); eff, dropped = req.reconcile(caps)` 放在 `resolve_video_provider` 之后），材料化循环遍历 `eff.refs`，三选一逻辑判 `eff.video_mode`。返回 dict 加 `"dropped_knobs": dropped`。

- [ ] **Step 4: 全量回归 + 类型/格式**

Run:
```bash
cd backend && uv run pytest -q 2>&1 | tail -5
uv run black --check app tests && uv run ruff check app tests
```
Expected: 全过；black/ruff 零报错（CI 的 Backend Lint 会拦）。

- [ ] **Step 5: 真栈验收（spec §6.2 的 codex-local 一条，其余 provider 待 P2 有落库后再量）**

部署后，用调试账号在画布上以 `codex-local-image` 生成一张 **16:9**，然后：

```bash
docker exec nous-db psql -U postgres -p 55434 -d postgres -Atc "
SELECT id, metadata->>'dropped_knobs' FROM task_tracking
WHERE task_type='canvas_gen' ORDER BY created_at DESC LIMIT 1;"
# 取最新 generated_media id 后量像素（cover 无鉴权）
docker exec nous-backend sh -c "curl -s http://localhost:8080/api/v1/generated-media/<id>/cover | /app/.venv/bin/python -c 'import sys,io;from PIL import Image;w,h=Image.open(io.BytesIO(sys.stdin.buffer.read())).size;print(w,h,round(w/h,3))'"
```

Expected: `dropped_knobs` 为 `[]`；像素比 ≈ 1.78（±6%）。**对照**：改动前同一操作得到 0.914（2026-08-29 实测）。若仍不符但 daemon 日志显示收到了 `size=1536x1024` 与画幅短语，则问题回到 codex 上游（spec 非目标），记录像素值即可。

- [ ] **Step 6: Commit + PR**

```bash
git add backend/app/workflows/canvas_generation.py backend/tests/test_canvas_generation_workflow.py
git -c user.email=ezufofoti59@gmail.com -c user.name=heygo commit -m "feat(gen): 服务器视频分支改读 GenerationRequest —— 三条分支至此全部从同一对象取参"
git push -u origin <branch>
gh pr create --base master --title "feat(gen): 出图生成请求契约 P1 —— GenerationRequest + ProviderCapabilities + 三分支拉齐" --body-file <PR 正文引用 spec §1.2 矩阵与 §6 验收>
```

---

## Self-Review

**Spec 覆盖（P1 范围）**
- §3.1 `GenerationRequest` → Task 2 ✅；三分支只读它 → Task 4/5/6/7 ✅
- §3.2 `ProviderCapabilities` 声明 → Task 3 ✅；`reconcile` + `dropped_knobs` 进 metadata → Task 2/4 ✅；UI 隐藏与徽章 → **P4**（不在本计划）
- §3.3 产出记录 → **P2**（不在本计划）；`codex.py` raw 透传是 §3.3 最后一行，因只有一行且 P2 依赖它，提前放进 Task 3 ✅
- §5 daemon 双发 `size` → Task 2/5 ✅；`MIN_IMAGE_DAEMON_VERSION` 与 daemon 客户端 → **P3**
- §6.1 矩阵 ❌ 各一条单测：codex-local ratio/model/quality（Task 5）、dreamina video_mode（Task 6）、jimeng 图片 refs 改为 `max_refs=0` 声明（Task 3）✅；负向提示词：`from_params` 读 `negative` 且所有 caps 为 ✗ → 必进 `dropped_knobs`，UI 下架归 P4 ✅
- §6.2 真栈量像素 → Task 7 Step 5（仅 codex-local；其余需 P2 落库）✅
- §6.5 dropped 可见 → metadata 侧 Task 4 ✅，徽章 P4

**占位扫描**：无 TBD/TODO；每个代码步骤都给出了实际代码。Task 5 Step 3 曾有一段"反例"片段，已删除，只保留正确的一行。

**类型一致性**：`_capabilities_for` 返回对象在测试里用 `SimpleNamespace` 六字段，与 `CapabilitiesLike` Protocol 一致（不含 `honours_ratio`，reconcile 不读它）✅；`dropped` 顺序固定 `ratio, quality, resolution, refs, negative, video_mode`，Task 4 测试断言 `["ratio","quality","refs"]` 与之相符 ✅；`to_codex_daemon_payload` 的 `model` 来自 `engine_model`（Task 5 断言 `gpt-image-2`）✅。

**已知风险**
- `_actual_provider_of` 依赖 provider 对象带 `provider_key` 或 `provider` 属性；若某 provider 两者皆无 → `none()` → 全丢且 `dropped_knobs` 非空，**可见**而非静默，符合契约意图，但要在 Task 4 Step 5 的既有测试跟改里注意。
- 服务器视频分支与 dreamina-daemon 分支的三选一逻辑同构未合并，留待 P2 之后。
