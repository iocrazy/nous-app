# OpenAI Images (GPT Image 2.5) provider — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the canvas generate with `gpt-image-2.5-flare` / `gpt-image-2.5-sunburst` through an API-key path, with quality tiers `xhigh`/`max` and real 1K/2K/4K sizes, without touching the Codex subscription path's behaviour.

**Architecture:** One new protocol (`openai-images`) that reuses `CodexCliProvider` parametrised by `provider_kind`; the capability contract gains `quality_tiers`; `aspect.py` gains the `(ratio, resolution) → WxH` table; migration 465 seeds two disabled catalog rows and renames the codex rows; Dockerfile pins CLI 0.7.4.

**Tech Stack:** FastAPI + SQLAlchemy (backend), React/TS + vitest (frontend), `gpt-image-2-skill` 0.7.4 static binary, Supabase migrations.

**Spec:** `docs/superpowers/specs/2026-09-13-openai-images-25-provider-design.md`

## Global Constraints

- Worktree: `/Volumes/program/project-code/repos/nous-app/.worktrees/feat-openai-images-25` (branch `feat/openai-images-25`, based on origin/master 4be9bcf4). Work only there.
- The API key never appears on argv: `codex_cli.py` must not contain the string `--api-key`; the key travels via `safe_popen_kwargs(env_extra={"OPENAI_API_KEY": …})` (`app/agent_framework/process_lifecycle.py`). Never pass `env=` yourself.
- No `text()` raw SQL; `black --check` on touched Python; UI copy English via `t()` only where the file already uses `t()` (GenFooterControls labels are plain strings today — keep the file's convention); semantic colour tokens only.
- Existing codex behaviour is byte-for-byte unchanged on the `codex` kind: `CODEX_SIZES`, prose aspect hint, `--background opaque`, refs ≤ 9, `--auth-file`.
- Tests: backend `cd backend && uv run pytest -q <files>`; frontend `cd frontend && npx vitest run <paths>` (only the `Tests N passed` line counts). tsc: compare error count against baseline (`npx tsc --noEmit -p . 2>&1 | grep -c "error TS"` before/after; touched files must add zero).
- Migration number **465** (`git fetch origin master` first; if taken, renumber and update every reference in Task 5).
- Never `git add -A`; add named paths. No stash.

---

### Task 1 (A1): `quality_tiers` in the capability contract

**Files:**
- Modify: `backend/app/services/ai/provider_protocols/base.py` (dataclass + constants + `_NONE`)
- Modify: every protocol declaring `quality=True`: `provider_protocols/codex.py`, `codex_local.py`, and any other (`grep -rn "quality=True" backend/app/services/ai/provider_protocols/`) → add `quality_tiers=LEGACY_QUALITY_TIERS`
- Modify: `backend/app/services/generation/request.py` (`reconcile`)
- Modify: `backend/app/api/canvases_router.py` (`list_generation_capabilities` projection)
- Test: `backend/tests/test_provider_capabilities.py`, `backend/tests/services/generation/test_request.py`, `backend/tests/test_generation_capabilities_endpoint.py`

**Interfaces:**
- Produces: `LEGACY_QUALITY_TIERS`, `IMAGE_25_QUALITY_TIERS`, `QUALITY_TIER_ORDER` in `base.py`; `ProviderCapabilities.quality_tiers: frozenset[str]`; capabilities wire field `quality_tiers: list[str]` (ordered by `QUALITY_TIER_ORDER`).

- [ ] **Step 1: Failing tests**

In `backend/tests/test_provider_capabilities.py` add:

```python
@pytest.mark.unit
def test_every_quality_capable_protocol_declares_tiers():
    from app.services.ai.provider_protocols import PROTOCOLS
    from app.services.ai.provider_protocols.base import LEGACY_QUALITY_TIERS

    for p in PROTOCOLS:
        if p.generation_family is None:
            continue
        caps = p.capabilities
        if caps.quality:
            assert caps.quality_tiers >= LEGACY_QUALITY_TIERS, p.key
        else:
            assert caps.quality_tiers == frozenset(), p.key


@pytest.mark.unit
def test_none_capabilities_have_no_tiers():
    from app.services.ai.provider_protocols.base import ProviderCapabilities

    assert ProviderCapabilities.none().quality_tiers == frozenset()
```

In `backend/tests/services/generation/test_request.py` add (mirror the file's existing `_caps(...)` helper; extend it with `quality_tiers=frozenset({"low", "medium", "high"})` as default):

```python
def test_reconcile_drops_quality_outside_the_providers_tiers():
    req = GenerationRequest.from_params("image", "p", "m", {"quality": "xhigh"})
    eff, dropped = req.reconcile(_caps(quality=True, quality_tiers=frozenset({"low", "medium", "high"})))
    assert eff.quality is None
    assert dropped == ["quality"]


def test_reconcile_keeps_quality_inside_the_providers_tiers():
    req = GenerationRequest.from_params("image", "p", "m", {"quality": "xhigh"})
    eff, dropped = req.reconcile(_caps(quality=True, quality_tiers=frozenset({"low", "medium", "high", "xhigh", "max"})))
    assert eff.quality == "xhigh"
    assert dropped == []
```

In `backend/tests/test_generation_capabilities_endpoint.py` extend `test_capability_keys_match_the_picker_row_for_row` (or add a sibling) to assert the projected keys are exactly `{"ratios","quality","quality_tiers","resolution","max_refs","negative","video_modes"}` and that `quality_tiers` for the codex row equals `["low", "medium", "high"]` (order = `QUALITY_TIER_ORDER`).

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest -q tests/test_provider_capabilities.py tests/services/generation/test_request.py tests/test_generation_capabilities_endpoint.py`
Expected: FAIL — `quality_tiers` unknown field / attribute.

- [ ] **Step 3: Implement**

`base.py` — after `ALL_RATIOS`:

```python
# Quality tiers are a VOCABULARY, not a bool: gpt-image-2.5 added `xhigh`
# and `max` and only the API-key path honours them (the codex subscription
# backend rewrote `xhigh` to `medium` when measured 2026-09-09). A protocol
# declares the tiers it can honour; reconcile drops the rest loudly.
LEGACY_QUALITY_TIERS: frozenset[str] = frozenset({"low", "medium", "high"})
IMAGE_25_QUALITY_TIERS: frozenset[str] = LEGACY_QUALITY_TIERS | {"xhigh", "max"}
QUALITY_TIER_ORDER: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")
```

Dataclass: add `quality_tiers: frozenset[str]` right after `quality`. `_NONE` gets `quality_tiers=frozenset()`. Every protocol with `quality=True` gets `quality_tiers=LEGACY_QUALITY_TIERS` (import it).

`request.py` `reconcile` — replace the quality branch:

```python
        if eff.quality and (not caps.quality or eff.quality not in caps.quality_tiers):
            eff = replace(eff, quality=None)
            dropped.append("quality")
```

(`CapabilitiesLike` protocol/type in that module must gain `quality_tiers`.)

`canvases_router.py` projection — add after `"quality"`:

```python
            "quality_tiers": [t for t in QUALITY_TIER_ORDER if t in caps.quality_tiers],
```

(import `QUALITY_TIER_ORDER` next to `ProviderCapabilities`).

- [ ] **Step 4: Run to verify pass** — same command; also `uv run pytest -q tests/test_provider_protocols_contract.py tests/test_model_capabilities.py tests/test_model_capabilities_lookup.py`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/ai/provider_protocols/base.py backend/app/services/ai/provider_protocols/codex.py backend/app/services/ai/provider_protocols/codex_local.py backend/app/services/generation/request.py backend/app/api/canvases_router.py backend/tests/test_provider_capabilities.py backend/tests/services/generation/test_request.py backend/tests/test_generation_capabilities_endpoint.py
git commit -m "feat(generation): quality tiers join the capability contract — xhigh/max only where a provider honours them"
```

(add any other protocol file you touched in Step 3.)

---

### Task 2 (A2): `(ratio, resolution) → WxH` vocabulary

**Files:**
- Modify: `backend/app/services/generation/aspect.py`
- Test: `backend/tests/services/generation/test_aspect.py`

**Interfaces:**
- Produces: `IMAGE_SIZES: dict[tuple[str, str], str]`, `IMAGE_RESOLUTIONS = ("1k", "2k", "4k")`, `image_size_for(ratio: str | None, resolution: str | None) -> str`.

- [ ] **Step 1: Failing test**

```python
import math

from app.services.generation.aspect import (
    ASPECT_RATIOS,
    ASPECT_TOLERANCE,
    IMAGE_RESOLUTIONS,
    IMAGE_SIZES,
    image_size_for,
)

# OpenAI Images API constraints for gpt-image-2.5 (docs, 2026-09-13):
# edges multiples of 16, aspect 1:3..3:1, no edge > 3840, pixels in
# [655_360, 8_294_400].
def _dims(s: str) -> tuple[int, int]:
    w, h = s.split("x")
    return int(w), int(h)


def test_every_ratio_has_every_resolution():
    assert set(IMAGE_SIZES) == {(r, res) for r in ASPECT_RATIOS for res in IMAGE_RESOLUTIONS}


def test_sizes_obey_openai_constraints_and_the_ratio():
    for (ratio, res), size in IMAGE_SIZES.items():
        w, h = _dims(size)
        assert w % 16 == 0 and h % 16 == 0, size
        assert max(w, h) <= 3840, size
        assert 655_360 <= w * h <= 8_294_400, size
        assert 1 / 3 <= w / h <= 3, size
        want = ASPECT_RATIOS[ratio]
        assert abs((w / h) / want - 1) <= ASPECT_TOLERANCE, (ratio, size)


def test_resolution_tiers_are_monotonic_in_pixels():
    for ratio in ASPECT_RATIOS:
        px = [math.prod(_dims(IMAGE_SIZES[(ratio, res)])) for res in IMAGE_RESOLUTIONS]
        assert px == sorted(px) and len(set(px)) == 3, ratio


def test_image_size_for_defaults():
    assert image_size_for("16:9", "2k") == IMAGE_SIZES[("16:9", "2k")]
    assert image_size_for("16:9", None) == IMAGE_SIZES[("16:9", "1k")]
    assert image_size_for(None, "4k") == IMAGE_SIZES[("1:1", "4k")]
    assert image_size_for("nonsense", "nonsense") == "1024x1024"
```

- [ ] **Step 2: Run** — `uv run pytest -q tests/services/generation/test_aspect.py` → ImportError.

- [ ] **Step 3: Implement** (append to `aspect.py`)

```python
# Exact pixel sizes for providers that HONOUR --size (the OpenAI Images API;
# the codex subscription path does not, see CODEX_SIZES). Keyed by the
# canvas's (ratio, resolution) pills. Every value satisfies gpt-image-2.5's
# rules — multiples of 16, edge <= 3840, 655,360..8,294,400 pixels — which
# test_aspect.py re-derives from the numbers rather than trusting this comment.
IMAGE_RESOLUTIONS: tuple[str, ...] = ("1k", "2k", "4k")
IMAGE_SIZES: dict[tuple[str, str], str] = {
    ("21:9", "1k"): "1536x656",  ("21:9", "2k"): "2560x1104", ("21:9", "4k"): "3840x1648",
    ("16:9", "1k"): "1536x864",  ("16:9", "2k"): "2560x1440", ("16:9", "4k"): "3840x2160",
    ("3:2", "1k"): "1536x1024",  ("3:2", "2k"): "2304x1536",  ("3:2", "4k"): "3520x2352",
    ("4:3", "1k"): "1408x1056",  ("4:3", "2k"): "2304x1728",  ("4:3", "4k"): "3264x2448",
    ("1:1", "1k"): "1024x1024",  ("1:1", "2k"): "2048x2048",  ("1:1", "4k"): "2880x2880",
    ("3:4", "1k"): "1056x1408",  ("3:4", "2k"): "1728x2304",  ("3:4", "4k"): "2448x3264",
    ("2:3", "1k"): "1024x1536",  ("2:3", "2k"): "1536x2304",  ("2:3", "4k"): "2352x3520",
    ("9:16", "1k"): "864x1536",  ("9:16", "2k"): "1440x2560", ("9:16", "4k"): "2160x3840",
}
IMAGE_DEFAULT_SIZE = "1024x1024"


def image_size_for(ratio: Optional[str], resolution: Optional[str]) -> str:
    """The --size for a provider that honours it. Unknown ratio → 1:1, unknown
    or empty resolution → 1k; both unknown → IMAGE_DEFAULT_SIZE."""
    r = ratio if ratio in ASPECT_RATIOS else "1:1"
    res = resolution if resolution in IMAGE_RESOLUTIONS else "1k"
    return IMAGE_SIZES.get((r, res), IMAGE_DEFAULT_SIZE)
```

If any value fails Step 1's constraint test, fix the NUMBER (keep 16-multiples, respect the pixel budget) — do not loosen the test. Run `black`.

- [ ] **Step 4: Run** → pass. **Step 5: Commit**

```bash
git add backend/app/services/generation/aspect.py backend/tests/services/generation/test_aspect.py
git commit -m "feat(generation): exact (ratio, resolution) → WxH table for providers that honour --size"
```

---

### Task 3 (A3): `CodexCliProvider` learns `provider_kind="openai"`

**Files:**
- Modify: `backend/app/services/media/parsers/video_providers/codex_cli.py` (`__init__`, `_run_cli`, `generate_image`, `health`)
- Modify: `backend/app/services/ai/provider_protocols/codex.py` (`_CodexImageAdapter.generate` forwards `resolution`)
- Test: `backend/tests/test_codex_cli_provider.py` (extend), new `backend/tests/test_codex_cli_openai_kind.py`

**Interfaces:**
- Consumes: `image_size_for` (Task 2), `safe_popen_kwargs(env_extra=...)`.
- Produces: `CodexCliProvider(provider_kind: Literal["codex","openai"] = "codex", api_key: str | None = None)`; `generate_image(..., resolution: str | None = None)`.

- [ ] **Step 1: Failing tests** (`test_codex_cli_openai_kind.py`; copy the subprocess-stub pattern from `test_codex_cli_provider.py` — it monkeypatches `asyncio.create_subprocess_exec` and records `cmd` + `kwargs`)

```python
import pytest

from app.services.media.parsers.video_providers.codex_cli import CodexCliProvider


@pytest.mark.unit
async def test_openai_kind_uses_openai_provider_and_env_key(cli_stub):
    p = CodexCliProvider(provider_kind="openai", api_key="sk-test-123", bin_path="gis")
    await p.generate_image(prompt="a fox", aspect="16:9", model_version="gpt-image-2.5-flare", quality="xhigh", resolution="2k")
    cmd, kwargs = cli_stub.last
    assert cmd[:5] == ["gis", "--json", "--json-events", "--provider", "openai"]
    assert "--auth-file" not in cmd
    assert "--api-key" not in cmd and "sk-test-123" not in " ".join(cmd)
    assert kwargs["env"]["OPENAI_API_KEY"] == "sk-test-123"
    assert cmd[cmd.index("--size") + 1] == "2560x1440"
    assert cmd[cmd.index("--quality") + 1] == "xhigh"
    assert cmd[cmd.index("--model") + 1] == "gpt-image-2.5-flare"
    assert cmd[cmd.index("--background") + 1] == "opaque"
    prompt = cmd[cmd.index("--prompt") + 1]
    assert prompt == "a fox"  # no prose aspect hint on the API path


@pytest.mark.unit
async def test_codex_kind_is_unchanged(cli_stub):
    p = CodexCliProvider(bin_path="gis", auth_file="/x/auth.json")
    await p.generate_image(prompt="a fox", aspect="16:9", quality="high", resolution="2k")
    cmd, kwargs = cli_stub.last
    assert cmd[:7] == ["gis", "--json", "--json-events", "--provider", "codex", "--auth-file", "/x/auth.json"]
    assert cmd[cmd.index("--size") + 1] == "1536x1024"  # CODEX_SIZES, resolution ignored
    assert cmd[cmd.index("--prompt") + 1].startswith("a fox")
    assert "16:9" in cmd[cmd.index("--prompt") + 1]  # prose hint still appended
    assert "OPENAI_API_KEY" not in (kwargs.get("env") or {})


@pytest.mark.unit
def test_openai_kind_requires_a_key():
    with pytest.raises(ValueError):
        CodexCliProvider(provider_kind="openai", api_key="")


@pytest.mark.unit
def test_api_key_never_reaches_argv_source_guard():
    import inspect
    from app.services.media.parsers.video_providers import codex_cli

    assert "--api-key" not in inspect.getsource(codex_cli)
```

Also a `health()` case: stub `doctor` output `{"providers":{"openai":{"auth":{"api_key_present":true}}}}` → `{"ok": True}` for the openai kind, and `api_key_present:false` → `{"ok": False, "error": "api_key_missing"}`.

- [ ] **Step 2: Run** → FAIL (unexpected kwarg `provider_kind`).

- [ ] **Step 3: Implement**

`__init__`: add `provider_kind: Literal["codex", "openai"] = "codex", api_key: Optional[str] = None`; store both; `if provider_kind == "openai" and not (api_key or "").strip(): raise ValueError("openai kind needs an api_key")`.

`_run_cli`:

```python
        cmd = [self._bin, "--json", "--json-events", "--provider", self._provider_kind]
        if self._provider_kind == "codex" and self._auth_file:
            cmd += ["--auth-file", self._auth_file]
        cmd += args
        # The key rides in the child's environment, never on argv: the process
        # table is world-readable and `--api-key` would put it there. The CLI
        # reads OPENAI_API_KEY (doctor reports auth_source=env, verified 0.7.4).
        popen = (
            safe_popen_kwargs(env_extra={"OPENAI_API_KEY": self._api_key})
            if self._provider_kind == "openai"
            else safe_popen_kwargs()
        )
        ... create_subprocess_exec(*cmd, stdout=..., stderr=..., **popen)
```

`generate_image(..., resolution: Optional[str] = None)`:

```python
        if self._provider_kind == "openai":
            size = image_size_for(aspect or None, resolution)
            effective_prompt = prompt  # the API honours --size; no prose hint
        else:
            size = _ASPECT_TO_SIZE.get(aspect or "", _DEFAULT_SIZE)
            effective_prompt = prompt + _aspect_instruction(aspect)
```

(import `image_size_for` from `app.services.generation.aspect`.) Refs cap and `--background opaque` untouched.

`health()`: branch on kind — `openai`: `auth = providers.openai.auth`; `api_key_present is not True` → `{"ok": False, "error": "api_key_missing"}`.

`codex.py` adapter: pass `resolution=kwargs.get("resolution") or None` into `generate_image`.

- [ ] **Step 4: Run** — new file + `tests/test_codex_cli_provider.py tests/test_codex_cli_refusal.py tests/test_codex_cli_status.py tests/test_codex_content_refusal.py` → all pass; `black --check`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/media/parsers/video_providers/codex_cli.py backend/app/services/ai/provider_protocols/codex.py backend/tests/test_codex_cli_openai_kind.py backend/tests/test_codex_cli_provider.py
git commit -m "feat(codex-cli): provider_kind=openai — key via env, exact --size from (ratio, resolution), no prose hint"
```

---

### Task 4 (A4): protocol `openai-images` + registry

**Files:**
- Create: `backend/app/services/ai/provider_protocols/openai_images.py`
- Modify: `backend/app/services/ai/provider_protocols/_registry.py` (import + tuple entry after `CodexLocalProtocol()`)
- Modify: `backend/tests/test_image_model_probe.py` (`expected` dict += `"openai-images": False`)
- Test: new `backend/tests/test_openai_images_protocol.py`; extend `backend/tests/test_provider_protocols_package.py` if any asserted set changes (`chat_provider_keys` must NOT change).

**Interfaces:**
- Consumes: `IMAGE_25_QUALITY_TIERS`, `ALL_RATIOS`, `CodexCliProvider(provider_kind="openai", api_key=...)`, `_CodexImageAdapter`.
- Produces: protocol `key="openai-images"`, `generation_family="openai-images"`, `supports_http_image_probe=False`.

- [ ] **Step 1: Failing tests**

```python
import pytest

from app.services.ai.provider_protocols import PROTOCOLS, resolve_generation_protocol
from app.services.ai.provider_protocols.base import IMAGE_25_QUALITY_TIERS, ALL_RATIOS


@pytest.mark.unit
def test_openai_images_is_registered_with_25_capabilities():
    p = resolve_generation_protocol("openai-images")
    assert p is not None and p.model_types == ("image",)
    caps = p.capabilities
    assert caps.ratios == ALL_RATIOS
    assert caps.quality and caps.quality_tiers == IMAGE_25_QUALITY_TIERS
    assert caps.resolution is True
    assert caps.honours_ratio == "native"
    assert caps.max_refs == 9 and caps.negative is False


@pytest.mark.unit
def test_build_image_provider_threads_key_and_model(monkeypatch):
    from app.services.ai.provider_protocols import openai_images as mod

    seen = {}

    class FakeCli:
        def __init__(self, **kw):
            seen.update(kw)

    monkeypatch.setattr("app.services.media.parsers.video_providers.codex_cli.CodexCliProvider", FakeCli)
    proto = resolve_generation_protocol("openai-images")
    adapter, model = proto.build_image_provider({"api_key": "sk-x", "actual_model": "gpt-image-2.5-sunburst"})
    assert model == "gpt-image-2.5-sunburst"
    assert seen == {"provider_kind": "openai", "api_key": "sk-x"}


@pytest.mark.unit
def test_build_image_provider_refuses_empty_key():
    from app.services.ai.provider_protocols.base import ProtocolCapabilityError

    proto = resolve_generation_protocol("openai-images")
    with pytest.raises(ProtocolCapabilityError):
        proto.build_image_provider({"api_key": "", "actual_model": "gpt-image-2.5-flare"})


@pytest.mark.unit
def test_chat_keys_unchanged():
    from app.services.ai import provider_protocols as pp

    assert "openai-images" not in pp.chat_provider_keys()
```

- [ ] **Step 2: Run** → FAIL (`resolve_generation_protocol` returns None).

- [ ] **Step 3: Implement** `openai_images.py`

```python
"""OpenAI Images API (gpt-image-2.5) — the API-key path.

Same binary as the codex protocol, `--provider openai`. Unlike the subscription
path the API honours --size and the quality tiers `xhigh` / `max`, and the
image model is chosen by us (`actual_model` → `--model`). Billing is per token
on the operator's OpenAI account (the catalog row's api_key), which is why the
seeded display names carry "(OpenAI API)".
"""

from __future__ import annotations

from typing import Any

from app.services.ai.provider_protocols.base import (
    ALL_RATIOS,
    IMAGE_25_QUALITY_TIERS,
    ProtocolCapabilityError,
    ProviderCapabilities,
    ProviderProtocol,
)
from app.services.ai.provider_protocols.codex import _CodexImageAdapter


class OpenAIImagesProtocol(ProviderProtocol):
    key = "openai-images"
    label = "OpenAI Images API (GPT Image 2.5)"
    description = (
        "gpt-image-2-skill CLI over the OpenAI Images API with the row's api_key "
        "(pay-as-you-go). actual_model is the image model: gpt-image-2.5-flare "
        "or gpt-image-2.5-sunburst. Honours exact sizes and xhigh/max quality."
    )
    model_types = ("image",)
    aliases = ()
    generation_family = "openai-images"
    supports_http_image_probe = False  # shells out to the CLI, like codex
    capabilities = ProviderCapabilities(
        ratios=ALL_RATIOS,
        quality=True,
        quality_tiers=IMAGE_25_QUALITY_TIERS,
        resolution=True,
        max_refs=9,
        negative=False,
        video_modes=frozenset(),
        honours_ratio="native",
    )

    def build_image_provider(self, row: dict[str, Any]) -> Any:
        from app.services.media.parsers.video_providers.codex_cli import (
            CodexCliProvider,
        )

        key = (row.get("api_key") or "").strip()
        if not key:
            # A typed refusal at build time beats the CLI's `not_logged_in`
            # (which would read as a codex session problem, not a missing key).
            raise ProtocolCapabilityError(self.key, "image (api_key missing)")
        actual_model = row.get("actual_model") or ""
        return (
            _CodexImageAdapter(CodexCliProvider(provider_kind="openai", api_key=key)),
            actual_model,
        )
```

Register in `_registry.py`; add `"openai-images": False` to the probe `expected` dict with a comment. Check `_CodexImageAdapter` sets `provider="codex"` in `ImageGenResult` — `_stamp_provider_key` overrides `provider_key` afterwards, but the result's `provider` string should be the real one: give the adapter a `provider_name` ctor arg defaulting to `"codex"` and pass `"openai-images"` here.

- [ ] **Step 4: Run** — new file + `tests/test_image_model_probe.py tests/test_provider_protocols_package.py tests/test_provider_protocols_contract.py tests/test_provider_protocols.py tests/test_protocol_build_generation.py tests/test_mediahub_model_protocols_endpoint.py tests/test_admin_mediahub_model_response.py tests/test_generation_picker_readiness.py` → pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/ai/provider_protocols/openai_images.py backend/app/services/ai/provider_protocols/_registry.py backend/app/services/ai/provider_protocols/codex.py backend/tests/test_openai_images_protocol.py backend/tests/test_image_model_probe.py
git commit -m "feat(providers): openai-images protocol — gpt-image-2.5 through the API-key path"
```

---

### Task 5 (A5): migration 465, CLI pin 0.7.4, runbook

**Files:**
- Create: `supabase/migrations/465_openai_images_25_catalog.sql`
- Modify: `Dockerfile` (two ARG lines), `docs/runbook/codex-image.md` (new section), `tools/codex-daemon/README.md:38` (note 0.7.4)
- Test: `backend/tests/models/` has no ORM change; verify with `uv run pytest -q tests/db/test_schema_drift.py -k migration` only if that test enumerates files (read it first; skip if not).

- [ ] **Step 1: Migration**

```sql
-- 465: gpt-image-2.5 through the OpenAI Images API (spec 2026-09-13).
-- Two disabled rows; the operator pastes the api_key in Admin → AI Models and
-- enables them. "(OpenAI API)" in the display name is the billing signal in
-- the canvas picker (per-token, unlike the codex subscription rows).
-- The codex rows drop the version number: on the subscription path the image
-- model is OpenAI's rollout decision, not ours (measured 2026-09-09).
BEGIN;

INSERT INTO public.mediahub_models
    (name, display_name, type, actual_provider, actual_model, api_key,
     is_enabled, sort_order, description)
VALUES
    ('openai-image-flare', 'GPT Image 2.5 Flare (OpenAI API)', 'image',
     'openai-images', 'gpt-image-2.5-flare', '', FALSE, 21,
     'OpenAI Images API, pay-as-you-go. Fast default; honours exact sizes and xhigh/max quality.'),
    ('openai-image-sunburst', 'GPT Image 2.5 Sunburst (OpenAI API)', 'image',
     'openai-images', 'gpt-image-2.5-sunburst', '', FALSE, 22,
     'OpenAI Images API, pay-as-you-go. Edit precision; slower than Flare.')
ON CONFLICT (name) DO NOTHING;

UPDATE public.mediahub_models SET display_name = 'GPT Image (Codex)'
 WHERE name = 'codex-image' AND display_name = 'GPT Image 2 (Codex)';
UPDATE public.mediahub_models SET display_name = 'GPT Image (Codex, local)'
 WHERE name = 'codex-local-image' AND display_name LIKE 'GPT Image 2%';

COMMIT;
NOTIFY pgrst, 'reload schema';
```

- [ ] **Step 2: Dockerfile**

```
ARG GPT_IMAGE_2_SKILL_VERSION=0.7.4
ARG GPT_IMAGE_2_SKILL_SHA256=0488a72fa009cf69a1c0e57f9997de288da13f52673242826cae5420daffb575
```

Verify the pin locally: `curl -fsSL -o /tmp/g.tgz https://registry.npmmirror.com/gpt-image-2-skill-linux-x64-static/-/gpt-image-2-skill-linux-x64-static-0.7.4.tgz && shasum -a 256 /tmp/g.tgz` must print the value above (fall back to registry.npmjs.org if the mirror lags; the tarball is byte-identical).

- [ ] **Step 3: Docs** — `docs/runbook/codex-image.md`: add a section "API-key path (openai-images, 2026-09-13)": which rows, where the key goes (Admin → AI Models, per row), `docker exec nous-worker sh -c 'OPENAI_API_KEY=… gpt-image-2-skill --json --provider openai doctor'` as the probe, and the fact that `xhigh`/`max`/sizes are honoured here and NOT on the codex rows. `tools/codex-daemon/README.md:38`: append "(0.7.4 tested 2026-09-13)".

- [ ] **Step 4: Commit**

```bash
git add supabase/migrations/465_openai_images_25_catalog.sql Dockerfile docs/runbook/codex-image.md tools/codex-daemon/README.md
git commit -m "feat(db): mig 465 gpt-image-2.5 catalog rows + codex rows unversioned; pin gpt-image-2-skill 0.7.4"
```

---

### Task 6 (B1): frontend — quality tiers per model

**Files:**
- Modify: `frontend/features/canvas-core/services/canvasGenerationService.ts` (`ModelCapabilities.quality_tiers: string[]`)
- Modify: `frontend/features/canvas-core/smart/nodes/GenFooterControls.tsx` (`QUALITIES` + filter)
- Modify: `frontend/features/canvas-core/smart/nodes/useModelCapabilities.ts` only if it normalises fields
- Test: `frontend/features/canvas-core/smart/nodes/GenFooterControls.caps.test.tsx`, `useModelCapabilities.test.ts` (fixtures gain `quality_tiers`), `AssetNodeView.limits.test.tsx:~200` fixture

- [ ] **Step 1: Failing tests** (`GenFooterControls.caps.test.tsx`, using the file's existing render + caps-fixture helpers)

```tsx
it('offers only the tiers the model declares', async () => {
  primeCaps({ 'codex-image': { ...FULL, quality: true, quality_tiers: ['low', 'medium', 'high'] } });
  renderFooter({ model: 'codex-image', kind: 'image' });
  fireEvent.click(screen.getByTestId('pill-quality'));
  expect(screen.getByRole('option', { name: 'High' })).toBeInTheDocument();
  expect(screen.queryByRole('option', { name: 'Extra High' })).toBeNull();
  expect(screen.queryByRole('option', { name: 'Max' })).toBeNull();
});

it('offers xhigh and max on a gpt-image-2.5 row', async () => {
  primeCaps({ 'openai-image-flare': { ...FULL, quality: true, quality_tiers: ['low', 'medium', 'high', 'xhigh', 'max'] } });
  renderFooter({ model: 'openai-image-flare', kind: 'image' });
  fireEvent.click(screen.getByTestId('pill-quality'));
  expect(screen.getByRole('option', { name: 'Extra High' })).toBeInTheDocument();
  expect(screen.getByRole('option', { name: 'Max' })).toBeInTheDocument();
});

it('unknown capabilities keep every tier', async () => {
  primeCaps(null);
  renderFooter({ model: 'mystery', kind: 'image' });
  fireEvent.click(screen.getByTestId('pill-quality'));
  expect(screen.getByRole('option', { name: 'Max' })).toBeInTheDocument();
});
```

(Adapt `role`/`name` to how the quality popover renders its rows — read `GenFooterControls.popover.test.tsx` first; keep the assertions' intent.)

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement**

```ts
const QUALITIES: Array<{ label: string; value: string | undefined }> = [
  { label: 'Auto', value: undefined },
  { label: 'Low', value: 'low' },
  { label: 'Medium', value: 'medium' },
  { label: 'High', value: 'high' },
  { label: 'Extra High', value: 'xhigh' },
  { label: 'Max', value: 'max' },
];
// Auto always stays; a tier stays only if the model declares it (null caps =
// unknown = everything, same convention as the other pills).
const qualityOptions = QUALITIES.filter(
  (q) => q.value === undefined || caps === null || caps.quality_tiers.includes(q.value),
);
```

Use `qualityOptions` where the popover renders; `qualityLabel` must resolve a persisted `gen.quality` that the current model no longer offers to `Auto` display (the backend drops it anyway) — add one assertion for that.

- [ ] **Step 4: Run** `npx vitest run features/canvas-core/smart/nodes features/canvas-core/services` + tsc count + eslint on touched files. **Step 5: Commit**

```bash
git add frontend/features/canvas-core/services/canvasGenerationService.ts frontend/features/canvas-core/smart/nodes/GenFooterControls.tsx frontend/features/canvas-core/smart/nodes/GenFooterControls.caps.test.tsx frontend/features/canvas-core/smart/nodes/useModelCapabilities.test.ts frontend/features/canvas-core/smart/nodes/AssetNodeView.limits.test.tsx
git commit -m "feat(canvas): quality pill follows the model's tiers — Extra High / Max only on gpt-image-2.5 rows"
```

(add `useModelCapabilities.ts` if touched.)

---

### Task 7 (C1): real-stack acceptance (post-merge; controller)

No code. After deploy: operator pastes the OpenAI key into both rows in Admin → AI Models and enables Flare; run spec §4 items 1–6 with the debug account (Playwright against app.nous.ink, measure the PNG with PIL inside the worker); record results in the PR / memory. If the operator's key is not available, items 1, 3, 4 (`-V`), 6 are still verifiable and item 2 is reported as blocked-on-key.

---

## Self-review

- **Spec coverage:** §3.1 → T4; §3.2 → T3; §3.3 → T1 + T6; §3.5 → T5; §3.6 → T2; §3.7 → T5; §4 → T7. Non-goals untouched.
- **Type consistency:** `quality_tiers` is `frozenset[str]` server-side, `string[]` on the wire ordered by `QUALITY_TIER_ORDER`; `image_size_for(ratio, resolution)` signature identical in T2 and T3; `provider_kind` literal `"openai"` in T3 and T4; row names in T5 match the T6 test fixtures.
- **Ordering hazard:** T3 imports `image_size_for` from T2; T4 imports `IMAGE_25_QUALITY_TIERS` from T1 and `_CodexImageAdapter(provider_name=…)` from T3/T4 (T4 adds the arg — implementer must touch `codex.py` in T4 only for that arg, nothing else).
