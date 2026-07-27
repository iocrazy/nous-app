# Provider Protocol Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Nous's AI provider protocols a single explicit registry — collapsing the two code-internal provider lists (chat factory + image/video db_registry) into one source, exposing it over an admin API, and turning the admin `actual_provider` free-text box into a documented dropdown.

**Architecture:** A new pure-data module `provider_protocols.py` is the single source of truth. `factory._PROVIDER_KEYS` and `db_registry._ARK_PROVIDERS`/`_JIMENG_PROVIDERS` are DERIVED from it (behavior byte-identical, only the source converges). A read-only admin endpoint serves the registry; the admin Models UI renders a Select with `allowCreate` from it. No DB migration, no data change, no new rejecting validation (dispatch already degrades safely, fail-open).

**Tech Stack:** Python 3.13 / FastAPI / pytest (backend); React + Arco Design + Vite (admin).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-13-provider-protocol-registry-design.md`.
- Zero DB migration, zero data change, no new rejecting validation on POST/PATCH — unknown `actual_provider` labels MUST keep working (they degrade to the OpenAI-compatible adapter; contract pinned by #1313/#1319 tests).
- Derivations must be behavior-identical: `chat_provider_keys()` == `{claude, deepseek, doubao, openai, modelscope, qwen}`; `generation_keys_for("ark")` == `{doubao, ark}`; `generation_keys_for("jimeng-cli")` == `{jimeng-cli, jimeng}`.
- Backend lint gate before any push: `cd backend && uv run black <files> && uv run isort <files> && uv run flake8 <files>`.
- Admin UI copy in English (Title Case labels), per repo UI rules.
- Admin endpoint tests call the endpoint function directly (repo convention), NOT via TestClient.

**Design note (UI, resolved during planning):** `actual_provider` is set in the **"new provider" modal** at the provider-group level, where no single `type` exists yet (a provider spans types — e.g. `doubao` serves llm+embedding, `jimeng-cli` serves image+video). Therefore the dropdown shows ALL protocols (no hard type filter); each option's sub-text lists its applicable `model_types` as a hint. This deviates from the spec's "filter by row.type" line, which assumed a single-type context that the real modal flow doesn't have. `allowCreate` is preserved so any custom label still works.

---

### Task 1: Protocol registry module

**Files:**
- Create: `backend/app/services/ai/provider_protocols.py`
- Test: `backend/tests/test_provider_protocols.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class ProviderProtocol` with fields
    `key: str`, `label: str`, `description: str`, `model_types: tuple[str, ...]`,
    `aliases: tuple[str, ...] = ()`, `is_chat_key: bool = False`,
    `generation_family: Optional[str] = None`, `is_default: bool = False`.
  - `PROTOCOLS: tuple[ProviderProtocol, ...]` — the registry.
  - `all_protocols() -> tuple[ProviderProtocol, ...]`
  - `chat_provider_keys() -> frozenset[str]` — `{p.key for p in PROTOCOLS if p.is_chat_key}`
  - `generation_keys_for(family: str) -> frozenset[str]` — union of `key`+`aliases` for entries whose `generation_family == family`
  - `default_chat_key() -> str` — the single `is_chat_key and is_default` entry's key

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_provider_protocols.py
"""Registry is the single source of truth for provider protocols (2026-07-13)."""

from __future__ import annotations

import pytest

from app.services.ai import provider_protocols as pp


@pytest.mark.unit
def test_chat_provider_keys_match_expected_set():
    # Behavior-identical to factory._PROVIDER_KEYS pre-refactor.
    assert pp.chat_provider_keys() == frozenset(
        {"claude", "deepseek", "doubao", "openai", "modelscope", "qwen"}
    )


@pytest.mark.unit
def test_generation_keys_for_ark():
    assert pp.generation_keys_for("ark") == frozenset({"doubao", "ark"})


@pytest.mark.unit
def test_generation_keys_for_jimeng():
    assert pp.generation_keys_for("jimeng-cli") == frozenset({"jimeng-cli", "jimeng"})


@pytest.mark.unit
def test_exactly_one_chat_default_and_it_is_qwen():
    defaults = [p for p in pp.all_protocols() if p.is_chat_key and p.is_default]
    assert len(defaults) == 1
    assert pp.default_chat_key() == "qwen"


@pytest.mark.unit
def test_every_protocol_has_label_and_model_types():
    for p in pp.all_protocols():
        assert p.label.strip()
        assert p.model_types  # non-empty
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_provider_protocols.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.ai.provider_protocols'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/services/ai/provider_protocols.py
"""Single source of truth for AI provider *protocols* (dispatch surfaces).

Before this module, two code-internal lists drifted independently:
  - chat/embedding/asr: ``factory._PROVIDER_KEYS`` (adapter keys)
  - image/video: ``db_registry._ARK_PROVIDERS`` / ``_JIMENG_PROVIDERS``

Admins typed ``actual_provider`` as free text against those hidden lists —
the exact drift that took AI Chat down for a week (#1313). This registry
makes the set explicit; the two lists are now DERIVED from it (see
``chat_provider_keys`` / ``generation_keys_for``), and the admin UI renders
its dropdown from ``all_protocols``.

Adding a protocol = one row here (+ its adapter). The contract tests in
``tests/test_provider_protocols.py`` fail if a dispatch surface and this
registry disagree.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ProviderProtocol:
    """One provider protocol the platform can dispatch a catalog row to.

    ``key`` is the canonical ``actual_provider`` value; ``aliases`` are
    accepted equivalents (image/video dispatch matches key OR alias).
    ``is_chat_key`` marks a buildable chat adapter key (factory). A non-None
    ``generation_family`` marks an image/video dispatch family
    (``db_registry``). ``model_types`` drives the admin dropdown hint only.
    """

    key: str
    label: str
    description: str
    model_types: tuple[str, ...]
    aliases: tuple[str, ...] = ()
    is_chat_key: bool = False
    generation_family: Optional[str] = None
    is_default: bool = False


PROTOCOLS: tuple[ProviderProtocol, ...] = (
    ProviderProtocol(
        key="qwen",
        label="OpenAI-Compatible (generic)",
        description=(
            "Standard OpenAI /chat/completions contract. The fail-open "
            "default: any self-hosted or aggregated endpoint (vLLM, Nous, "
            "etc.) works here — the base_url + key is the whole credential."
        ),
        model_types=("llm", "embedding", "asr"),
        is_chat_key=True,
        is_default=True,
    ),
    ProviderProtocol(
        key="openai",
        label="OpenAI (native)",
        description="Native OpenAI API (multimodal gpt-*/o1/o3).",
        model_types=("llm", "embedding", "asr"),
        is_chat_key=True,
    ),
    ProviderProtocol(
        key="claude",
        label="Claude (Anthropic)",
        description="Native Anthropic Messages API (claude-*).",
        model_types=("llm",),
        is_chat_key=True,
    ),
    ProviderProtocol(
        key="deepseek",
        label="DeepSeek",
        description="DeepSeek chat-completions endpoint.",
        model_types=("llm",),
        is_chat_key=True,
    ),
    ProviderProtocol(
        key="doubao",
        label="Doubao (chat)",
        description="Volcengine Doubao chat-completions (doubao-*/ep-*).",
        model_types=("llm", "embedding", "asr"),
        is_chat_key=True,
    ),
    ProviderProtocol(
        key="modelscope",
        label="ModelScope",
        description="ModelScope org/name models (BYO key).",
        model_types=("llm",),
        is_chat_key=True,
    ),
    ProviderProtocol(
        key="ark",
        label="Ark image/video (方舟)",
        description="Volcengine Ark task protocol for image/video generation.",
        model_types=("image", "video"),
        aliases=("doubao",),
        generation_family="ark",
    ),
    ProviderProtocol(
        key="jimeng-cli",
        label="Jimeng CLI (即梦)",
        description=(
            "Subprocess dreamina CLI (OAuth session is the credential; no "
            "api_key). Primary image/video generator."
        ),
        model_types=("image", "video"),
        aliases=("jimeng",),
        generation_family="jimeng-cli",
    ),
)


def all_protocols() -> tuple[ProviderProtocol, ...]:
    return PROTOCOLS


def chat_provider_keys() -> frozenset[str]:
    """Buildable chat adapter keys — the derived ``factory._PROVIDER_KEYS``."""
    return frozenset(p.key for p in PROTOCOLS if p.is_chat_key)


def generation_keys_for(family: str) -> frozenset[str]:
    """All accepted ``actual_provider`` strings (key + aliases) for an
    image/video dispatch family (``ark`` / ``jimeng-cli``)."""
    out: set[str] = set()
    for p in PROTOCOLS:
        if p.generation_family == family:
            out.add(p.key)
            out.update(p.aliases)
    return frozenset(out)


def default_chat_key() -> str:
    """The fail-open chat protocol key (must equal resolve_provider_key's
    final fallback)."""
    for p in PROTOCOLS:
        if p.is_chat_key and p.is_default:
            return p.key
    raise RuntimeError("no default chat protocol configured")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_provider_protocols.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Lint + commit**

```bash
cd backend && uv run black app/services/ai/provider_protocols.py tests/test_provider_protocols.py && uv run isort app/services/ai/provider_protocols.py tests/test_provider_protocols.py && uv run flake8 app/services/ai/provider_protocols.py tests/test_provider_protocols.py
git add backend/app/services/ai/provider_protocols.py backend/tests/test_provider_protocols.py
git commit -m "feat(ai): provider protocol registry — single source of truth"
```

---

### Task 2: Derive factory `_PROVIDER_KEYS` from the registry

**Files:**
- Modify: `backend/app/services/ai/adapters/factory.py:106-108`
- Test: `backend/tests/test_provider_protocols_contract.py`

**Interfaces:**
- Consumes: `provider_protocols.chat_provider_keys()`, `provider_protocols.default_chat_key()` (Task 1).
- Produces: no signature change — `factory._PROVIDER_KEYS` stays a `frozenset[str]`, `resolve_provider_key` unchanged.

- [ ] **Step 1: Write the failing contract test**

```python
# backend/tests/test_provider_protocols_contract.py
"""Contract: the registry and every dispatch surface agree. If someone adds
a protocol to factory/db_registry without the registry (or vice-versa), CI
goes red here — the guard the 2026-07-13 chat outage lacked."""

from __future__ import annotations

import pytest

from app.services.ai import provider_protocols as pp
from app.services.ai.adapters import factory


@pytest.mark.unit
def test_factory_provider_keys_are_registry_derived():
    assert factory._PROVIDER_KEYS == pp.chat_provider_keys()


@pytest.mark.unit
def test_registry_default_matches_resolve_provider_key_fallback():
    # Unknown label + unknown prefix must land on the registry's default.
    assert factory.resolve_provider_key("totally-unknown", "no-prefix-model") == (
        pp.default_chat_key()
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_provider_protocols_contract.py::test_factory_provider_keys_are_registry_derived -q`
Expected: currently PASSES only by luck of equal literals — to prove the wiring, first confirm it PASSES, then the refactor in Step 3 keeps it passing while removing the literal. (If it FAILS, the registry set diverged — fix Task 1 first.)

Note: this task's real safety is that the literal is DELETED in Step 3, so the test becomes the only definition of truth.

- [ ] **Step 3: Replace the literal with a registry derivation**

Replace `backend/app/services/ai/adapters/factory.py:106-108`:

```python
# Every provider key get_adapter_for_user can build. Used to validate an
# admin-named actual_provider before dispatching on it.
_PROVIDER_KEYS = frozenset(
    {"claude", "deepseek", "doubao", "openai", "modelscope", "qwen"}
)
```

with:

```python
# Every provider key get_adapter_for_user can build. Used to validate an
# admin-named actual_provider before dispatching on it. DERIVED from the
# provider-protocol registry (single source of truth, 2026-07-13) — the
# contract test in tests/test_provider_protocols_contract.py fails if this
# and the registry disagree.
from app.services.ai.provider_protocols import chat_provider_keys as _chat_keys

_PROVIDER_KEYS = _chat_keys()
```

Place the import with the other module-level imports at the top of `factory.py` if the file's style prefers top imports; a local import adjacent to the assignment is acceptable and avoids any import cycle. Verify no cycle: `provider_protocols` imports nothing from `adapters`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_provider_protocols_contract.py tests/test_catalog_provider_dispatch.py tests/test_adapter_factory_byo.py -q`
Expected: PASS (all)

- [ ] **Step 5: Lint + commit**

```bash
cd backend && uv run black app/services/ai/adapters/factory.py tests/test_provider_protocols_contract.py && uv run isort app/services/ai/adapters/factory.py tests/test_provider_protocols_contract.py && uv run flake8 app/services/ai/adapters/factory.py tests/test_provider_protocols_contract.py
git add backend/app/services/ai/adapters/factory.py backend/tests/test_provider_protocols_contract.py
git commit -m "refactor(ai): derive factory _PROVIDER_KEYS from protocol registry"
```

---

### Task 3: Derive db_registry image/video sets from the registry

**Files:**
- Modify: `backend/app/services/media/parsers/video_providers/db_registry.py:35-36`
- Test: `backend/tests/test_provider_protocols_contract.py` (append)

**Interfaces:**
- Consumes: `provider_protocols.generation_keys_for(family)` (Task 1).
- Produces: `_ARK_PROVIDERS` / `_JIMENG_PROVIDERS` stay module-level `frozenset[str]` (were `set`; `in` checks unaffected). `resolve_image_provider` / `resolve_video_provider` unchanged.

- [ ] **Step 1: Append the failing contract test**

```python
# append to backend/tests/test_provider_protocols_contract.py
from app.services.media.parsers.video_providers import db_registry


@pytest.mark.unit
def test_db_registry_ark_set_is_registry_derived():
    assert set(db_registry._ARK_PROVIDERS) == pp.generation_keys_for("ark")


@pytest.mark.unit
def test_db_registry_jimeng_set_is_registry_derived():
    assert set(db_registry._JIMENG_PROVIDERS) == pp.generation_keys_for("jimeng-cli")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_provider_protocols_contract.py -q`
Expected: PASS by equal literals for now — as in Task 2, the guarantee comes from deleting the literals in Step 3.

- [ ] **Step 3: Replace the literals with registry derivations**

Replace `backend/app/services/media/parsers/video_providers/db_registry.py:35-36`:

```python
_ARK_PROVIDERS = {"doubao", "ark"}
_JIMENG_PROVIDERS = {"jimeng-cli", "jimeng"}
```

with:

```python
# Accepted actual_provider strings per generation family. DERIVED from the
# provider-protocol registry (single source of truth, 2026-07-13); the
# contract test in tests/test_provider_protocols_contract.py fails if these
# and the registry disagree.
from app.services.ai.provider_protocols import generation_keys_for as _gen_keys

_ARK_PROVIDERS = _gen_keys("ark")
_JIMENG_PROVIDERS = _gen_keys("jimeng-cli")
```

Keep this import grouped with the file's other imports (after the existing `from app.services.media.parsers.video_providers...` imports). Verify no cycle: `provider_protocols` imports nothing from `media`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_provider_protocols_contract.py -q && cd backend && uv run pytest tests/ -k "db_registry or image_provider or video_provider" -q`
Expected: PASS (contract tests pass; any existing db_registry tests still pass)

- [ ] **Step 5: Lint + commit**

```bash
cd backend && uv run black app/services/media/parsers/video_providers/db_registry.py tests/test_provider_protocols_contract.py && uv run isort app/services/media/parsers/video_providers/db_registry.py tests/test_provider_protocols_contract.py && uv run flake8 app/services/media/parsers/video_providers/db_registry.py tests/test_provider_protocols_contract.py
git add backend/app/services/media/parsers/video_providers/db_registry.py backend/tests/test_provider_protocols_contract.py
git commit -m "refactor(media): derive image/video provider sets from protocol registry"
```

---

### Task A: Convert `provider_protocols.py` into a package with a behavior-capable base class

**Goal:** Restructure the flat module into a package where each protocol is a
class instance, WITHOUT changing any public function's behavior. Chat/image/video
build hooks are added to the base (default: raise) but not yet wired to
factory/db_registry — that is Tasks B and C. Phase 1's contract tests + all
existing AI tests stay green.

**Files:**
- Delete: `backend/app/services/ai/provider_protocols.py`
- Create: `backend/app/services/ai/provider_protocols/__init__.py`
- Create: `backend/app/services/ai/provider_protocols/base.py`
- Create: `backend/app/services/ai/provider_protocols/_registry.py`
- Create: `backend/app/services/ai/provider_protocols/qwen.py` `openai.py` `claude.py` `deepseek.py` `doubao.py` `modelscope.py` `ark.py` `jimeng.py`
- Test: `backend/tests/test_provider_protocols_package.py`

**Interfaces:**
- Consumes: nothing new.
- Produces (re-exported from `__init__.py`, same names as Phase 1 so every existing importer and the Task 1-3 contract tests keep working):
  `ProviderProtocol`, `all_protocols()`, `chat_provider_keys()`,
  `generation_keys_for(family)`, `default_chat_key()`. NEW:
  `get_chat_protocol(key) -> ProviderProtocol` (unknown key → the default/qwen
  protocol, mirroring factory's current `else` branch),
  `resolve_generation_protocol(actual_provider) -> Optional[ProviderProtocol]`,
  and `ProviderProtocol.build_chat_adapter/build_image_provider/build_video_provider`
  (base raises `ProtocolCapabilityError`). `ProviderNotConfiguredError` is NOT
  introduced here — it stays in factory until Task B.

- [ ] **Step 1: Write the failing package test**

```python
# backend/tests/test_provider_protocols_package.py
"""provider_protocols is now a package; public API is unchanged and each
protocol is a class instance with (default-raising) build hooks."""

from __future__ import annotations

import pytest

from app.services.ai import provider_protocols as pp
from app.services.ai.provider_protocols.base import (
    ProviderProtocol,
    ProtocolCapabilityError,
)


@pytest.mark.unit
def test_public_api_unchanged():
    assert pp.chat_provider_keys() == frozenset(
        {"claude", "deepseek", "doubao", "openai", "modelscope", "qwen"}
    )
    assert pp.generation_keys_for("ark") == frozenset({"doubao", "ark"})
    assert pp.generation_keys_for("jimeng-cli") == frozenset({"jimeng-cli", "jimeng"})
    assert pp.default_chat_key() == "qwen"
    assert all(isinstance(p, ProviderProtocol) for p in pp.all_protocols())


@pytest.mark.unit
def test_get_chat_protocol_known_and_unknown():
    assert pp.get_chat_protocol("claude").key == "claude"
    # Unknown key falls back to the default (qwen) — mirrors factory's else.
    assert pp.get_chat_protocol("totally-unknown").key == "qwen"


@pytest.mark.unit
def test_resolve_generation_protocol_by_key_and_alias():
    assert pp.resolve_generation_protocol("ark").generation_family == "ark"
    assert pp.resolve_generation_protocol("doubao").generation_family == "ark"  # alias
    assert pp.resolve_generation_protocol("jimeng").generation_family == "jimeng-cli"
    assert pp.resolve_generation_protocol("nope") is None


@pytest.mark.unit
def test_base_build_hooks_raise_by_default():
    class _Bare(ProviderProtocol):
        key = "bare"

    with pytest.raises(ProtocolCapabilityError):
        _Bare().build_chat_adapter("m", {})
    with pytest.raises(ProtocolCapabilityError):
        _Bare().build_image_provider({})
    with pytest.raises(ProtocolCapabilityError):
        _Bare().build_video_provider({})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_provider_protocols_package.py -q`
Expected: FAIL — the package/base module and `get_chat_protocol`/`resolve_generation_protocol` don't exist yet.

- [ ] **Step 3: Create the base module**

```python
# backend/app/services/ai/provider_protocols/base.py
"""Base class for provider protocols (Phase 2 — behavior-capable).

A concrete protocol subclasses this, sets the metadata class attributes, and
overrides only the build hook(s) its family supports. The build hooks import
their heavy adapter classes LAZILY (inside the method) so the registry can be
imported at startup without dragging in every adapter — and so factory can
import this package without a load-time cycle.
"""

from __future__ import annotations

from typing import Any, Optional


class ProtocolCapabilityError(RuntimeError):
    """A protocol was asked to build a capability it does not support
    (e.g. a chat-only protocol asked for an image provider)."""

    def __init__(self, key: str, capability: str) -> None:
        self.key = key
        self.capability = capability
        super().__init__(f"protocol {key!r} does not support {capability}")


class ProviderProtocol:
    """One provider protocol. Metadata mirrors Phase 1's dataclass fields;
    build hooks own the per-protocol adapter/provider construction."""

    key: str = ""
    label: str = ""
    description: str = ""
    model_types: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    is_chat_key: bool = False
    generation_family: Optional[str] = None
    is_default: bool = False

    # ---- capability hooks (default: unsupported) --------------------
    def build_chat_adapter(self, model: str, creds: dict[str, Any]) -> Any:
        """Build a chat/embedding/asr AIAdapter from a single-provider cred
        dict ``{"api_key": str, "base_url": str}``."""
        raise ProtocolCapabilityError(self.key, "chat")

    def build_image_provider(self, row: dict[str, Any]) -> Any:
        """Build ``(BaseImageProvider, actual_model)`` from a catalog row."""
        raise ProtocolCapabilityError(self.key, "image")

    def build_video_provider(self, row: dict[str, Any]) -> Any:
        """Build ``(provider, actual_model)`` from a catalog row."""
        raise ProtocolCapabilityError(self.key, "video")
```

- [ ] **Step 4: Create the eight protocol modules (metadata only in this task)**

Each carries the SAME metadata as Phase 1's dataclass entries. Build hooks are
added in Tasks B/C — in this task the chat/image/video modules only set
metadata (they inherit the base's raising hooks). Copy the metadata verbatim
from the current `provider_protocols.py` entries.

`qwen.py`:
```python
# backend/app/services/ai/provider_protocols/qwen.py
from __future__ import annotations

from app.services.ai.provider_protocols.base import ProviderProtocol


class QwenProtocol(ProviderProtocol):
    key = "qwen"
    label = "OpenAI-Compatible (generic)"
    description = (
        "Standard OpenAI /chat/completions contract. The fail-open default: "
        "any self-hosted or aggregated endpoint (vLLM, Nous, etc.) works here "
        "— the base_url + key is the whole credential."
    )
    model_types = ("llm", "embedding", "asr")
    is_chat_key = True
    is_default = True
```

`openai.py`:
```python
from __future__ import annotations

from app.services.ai.provider_protocols.base import ProviderProtocol


class OpenAIProtocol(ProviderProtocol):
    key = "openai"
    label = "OpenAI (native)"
    description = "Native OpenAI API (multimodal gpt-*/o1/o3)."
    model_types = ("llm", "embedding", "asr")
    is_chat_key = True
```

`claude.py`:
```python
from __future__ import annotations

from app.services.ai.provider_protocols.base import ProviderProtocol


class ClaudeProtocol(ProviderProtocol):
    key = "claude"
    label = "Claude (Anthropic)"
    description = "Native Anthropic Messages API (claude-*)."
    model_types = ("llm",)
    is_chat_key = True
```

`deepseek.py`:
```python
from __future__ import annotations

from app.services.ai.provider_protocols.base import ProviderProtocol


class DeepSeekProtocol(ProviderProtocol):
    key = "deepseek"
    label = "DeepSeek"
    description = "DeepSeek chat-completions endpoint."
    model_types = ("llm",)
    is_chat_key = True
```

`doubao.py`:
```python
from __future__ import annotations

from app.services.ai.provider_protocols.base import ProviderProtocol


class DoubaoProtocol(ProviderProtocol):
    key = "doubao"
    label = "Doubao (chat)"
    description = "Volcengine Doubao chat-completions (doubao-*/ep-*)."
    model_types = ("llm", "embedding", "asr")
    is_chat_key = True
```

`modelscope.py`:
```python
from __future__ import annotations

from app.services.ai.provider_protocols.base import ProviderProtocol


class ModelScopeProtocol(ProviderProtocol):
    key = "modelscope"
    label = "ModelScope"
    description = "ModelScope org/name models (BYO key)."
    model_types = ("llm",)
    is_chat_key = True
```

`ark.py`:
```python
from __future__ import annotations

from app.services.ai.provider_protocols.base import ProviderProtocol


class ArkProtocol(ProviderProtocol):
    key = "ark"
    label = "Ark image/video (方舟)"
    description = "Volcengine Ark task protocol for image/video generation."
    model_types = ("image", "video")
    aliases = ("doubao",)
    generation_family = "ark"
```

`jimeng.py`:
```python
from __future__ import annotations

from app.services.ai.provider_protocols.base import ProviderProtocol


class JimengProtocol(ProviderProtocol):
    key = "jimeng-cli"
    label = "Jimeng CLI (即梦)"
    description = (
        "Subprocess dreamina CLI (OAuth session is the credential; no "
        "api_key). Primary image/video generator."
    )
    model_types = ("image", "video")
    aliases = ("jimeng",)
    generation_family = "jimeng-cli"
```

- [ ] **Step 5: Create the registry**

```python
# backend/app/services/ai/provider_protocols/_registry.py
"""Assembles the protocol instances and the query functions. The public
names re-exported by __init__ preserve Phase 1's API exactly."""

from __future__ import annotations

from typing import Optional

from app.services.ai.provider_protocols.ark import ArkProtocol
from app.services.ai.provider_protocols.base import ProviderProtocol
from app.services.ai.provider_protocols.claude import ClaudeProtocol
from app.services.ai.provider_protocols.deepseek import DeepSeekProtocol
from app.services.ai.provider_protocols.doubao import DoubaoProtocol
from app.services.ai.provider_protocols.jimeng import JimengProtocol
from app.services.ai.provider_protocols.modelscope import ModelScopeProtocol
from app.services.ai.provider_protocols.openai import OpenAIProtocol
from app.services.ai.provider_protocols.qwen import QwenProtocol

PROTOCOLS: tuple[ProviderProtocol, ...] = (
    QwenProtocol(),
    OpenAIProtocol(),
    ClaudeProtocol(),
    DeepSeekProtocol(),
    DoubaoProtocol(),
    ModelScopeProtocol(),
    ArkProtocol(),
    JimengProtocol(),
)


def all_protocols() -> tuple[ProviderProtocol, ...]:
    return PROTOCOLS


def chat_provider_keys() -> frozenset[str]:
    return frozenset(p.key for p in PROTOCOLS if p.is_chat_key)


def generation_keys_for(family: str) -> frozenset[str]:
    out: set[str] = set()
    for p in PROTOCOLS:
        if p.generation_family == family:
            out.add(p.key)
            out.update(p.aliases)
    return frozenset(out)


def default_chat_key() -> str:
    for p in PROTOCOLS:
        if p.is_chat_key and p.is_default:
            return p.key
    raise RuntimeError("no default chat protocol configured")


def _default_chat_protocol() -> ProviderProtocol:
    for p in PROTOCOLS:
        if p.is_chat_key and p.is_default:
            return p
    raise RuntimeError("no default chat protocol configured")


def get_chat_protocol(key: str) -> ProviderProtocol:
    """The chat protocol for a provider key. An unknown key falls back to the
    default (qwen) protocol — mirrors factory's historical ``else`` branch,
    which routed anything non-matching to the OpenAI-compatible QwenAdapter."""
    for p in PROTOCOLS:
        if p.is_chat_key and p.key == key:
            return p
    return _default_chat_protocol()


def resolve_generation_protocol(actual_provider: str) -> Optional[ProviderProtocol]:
    """The image/video protocol whose key OR alias matches ``actual_provider``
    (case-insensitive), or None."""
    label = (actual_provider or "").lower()
    for p in PROTOCOLS:
        if p.generation_family is None:
            continue
        if label == p.key or label in p.aliases:
            return p
    return None
```

- [ ] **Step 6: Create the package __init__ (re-export the public API)**

```python
# backend/app/services/ai/provider_protocols/__init__.py
"""Provider-protocol registry (Phase 2 package). Single source of truth for
provider protocols — metadata AND per-protocol adapter/provider construction.

Public API preserved from the Phase 1 flat module so every existing
``from app.services.ai.provider_protocols import ...`` keeps working."""

from __future__ import annotations

from app.services.ai.provider_protocols._registry import (
    PROTOCOLS,
    all_protocols,
    chat_provider_keys,
    default_chat_key,
    generation_keys_for,
    get_chat_protocol,
    resolve_generation_protocol,
)
from app.services.ai.provider_protocols.base import (
    ProtocolCapabilityError,
    ProviderProtocol,
)

__all__ = [
    "PROTOCOLS",
    "ProviderProtocol",
    "ProtocolCapabilityError",
    "all_protocols",
    "chat_provider_keys",
    "generation_keys_for",
    "default_chat_key",
    "get_chat_protocol",
    "resolve_generation_protocol",
]
```

- [ ] **Step 7: Delete the old flat module + run all affected tests**

```bash
cd backend
git rm app/services/ai/provider_protocols.py
uv run pytest tests/test_provider_protocols_package.py tests/test_provider_protocols.py tests/test_provider_protocols_contract.py tests/test_catalog_provider_dispatch.py tests/test_adapter_factory_byo.py -q
```
Expected: ALL PASS — Phase 1 tests still green (the public API is byte-identical), plus the new package tests.

- [ ] **Step 8: Lint + commit**

```bash
cd backend && uv run black app/services/ai/provider_protocols/ tests/test_provider_protocols_package.py && uv run isort app/services/ai/provider_protocols/ tests/test_provider_protocols_package.py && uv run flake8 app/services/ai/provider_protocols/ tests/test_provider_protocols_package.py
git add -A backend/app/services/ai/provider_protocols backend/tests/test_provider_protocols_package.py
git commit -m "refactor(ai): provider_protocols becomes a package with behavior-capable base"
```

---

### Task B: Move chat adapter construction into the protocol classes

**Goal:** Each chat protocol's `build_chat_adapter` owns its adapter
construction (moved verbatim from `factory._build_adapter_for_key`). Factory's
per-provider `if provider_key == ...` chain is deleted; the shared RotatingAdapter
logic stays and delegates the single-key build to the protocol. Behavior
byte-identical — proven by the existing dispatch tests.

**Files:**
- Modify: `backend/app/services/ai/provider_protocols/base.py` (move `ProviderNotConfiguredError` here)
- Modify: `backend/app/services/ai/provider_protocols/{qwen,openai,claude,deepseek,doubao,modelscope}.py` (add `build_chat_adapter`)
- Modify: `backend/app/services/ai/adapters/factory.py` (delete per-provider branches; delegate; re-export `ProviderNotConfiguredError`)
- Test: `backend/tests/test_protocol_build_chat_adapter.py`

**Interfaces:**
- Consumes: `get_chat_protocol(key)` (Task A); the adapter classes (lazy-imported inside each build hook).
- Produces: `build_chat_adapter(model, creds)` on the 6 chat protocols, where
  `creds = {"api_key": str, "base_url": str}` (single key, already extracted by
  factory). `ProviderNotConfiguredError` now lives in
  `provider_protocols.base` and is re-exported by `factory` and by the package
  `__init__`. `factory.get_adapter_for_key/get_adapter_for_user/get_adapter/`
  `resolve_provider_key/provider_key_for_model/_PROVIDER_KEYS` signatures
  UNCHANGED.

**Reference — the exact construction to move** (current `factory.py:224-284`):
claude→`ClaudeAdapter(api_key=key, default_model=model or "claude-opus-4-5")`;
deepseek→`DeepSeekAdapter(api_url=base or DEEPSEEK_DEFAULT_URL, api_key=key, default_model=model or "deepseek-chat")`;
doubao→`DoubaoAdapter(api_url=base or DOUBAO_DEFAULT_URL, api_key=key, default_model=model)`;
openai→`OpenAIAdapter(api_key=key, default_model=model or "gpt-4o", api_url=base or None)`;
modelscope→`ModelScopeAdapter(api_url=base or MODELSCOPE_DEFAULT_URL, api_key=key, default_model=model)` (import `MODELSCOPE_DEFAULT_URL` lazily);
qwen→`QwenAdapter(api_url=base, api_key=key, default_model=model)` — qwen requires `base`, the others require `key`.
`DEEPSEEK_DEFAULT_URL` / `DOUBAO_DEFAULT_URL` currently live in `factory.py`; keep them there and lazy-import them into `deepseek.py`/`doubao.py` build hooks (`from app.services.ai.adapters.factory import DEEPSEEK_DEFAULT_URL`), OR move both constants into the respective protocol modules. Choose the move (cleaner — the constant belongs with the protocol that uses it); update factory to import them back from the protocol modules if any other factory code references them (grep first).

- [ ] **Step 1: Write the failing per-protocol build test**

```python
# backend/tests/test_protocol_build_chat_adapter.py
"""Each chat protocol builds its own adapter from a single-provider cred dict,
and enforces its own required-field check — the logic moved out of factory."""

from __future__ import annotations

import pytest

from app.services.ai.adapters.claude import ClaudeAdapter
from app.services.ai.adapters.deepseek import DeepSeekAdapter
from app.services.ai.adapters.doubao import DoubaoAdapter
from app.services.ai.adapters.openai import OpenAIAdapter
from app.services.ai.adapters.qwen import QwenAdapter
from app.services.ai.provider_protocols import get_chat_protocol
from app.services.ai.provider_protocols.base import ProviderNotConfiguredError

_KEY = {"api_key": "k", "base_url": "https://x/v1"}


@pytest.mark.unit
def test_each_chat_protocol_builds_its_adapter():
    assert isinstance(get_chat_protocol("claude").build_chat_adapter("claude-x", _KEY), ClaudeAdapter)
    assert isinstance(get_chat_protocol("deepseek").build_chat_adapter("deepseek-chat", _KEY), DeepSeekAdapter)
    assert isinstance(get_chat_protocol("doubao").build_chat_adapter("doubao-x", _KEY), DoubaoAdapter)
    assert isinstance(get_chat_protocol("openai").build_chat_adapter("gpt-4o", _KEY), OpenAIAdapter)
    assert isinstance(get_chat_protocol("qwen").build_chat_adapter("qwen3-6-35b", _KEY), QwenAdapter)


@pytest.mark.unit
def test_missing_key_raises_not_configured():
    with pytest.raises(ProviderNotConfiguredError):
        get_chat_protocol("claude").build_chat_adapter("claude-x", {"api_key": "", "base_url": ""})


@pytest.mark.unit
def test_qwen_requires_base_url_not_key():
    with pytest.raises(ProviderNotConfiguredError):
        get_chat_protocol("qwen").build_chat_adapter("m", {"api_key": "k", "base_url": ""})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_protocol_build_chat_adapter.py -q`
Expected: FAIL — `ProviderNotConfiguredError` not importable from `provider_protocols.base`; build hooks still raise `ProtocolCapabilityError`.

- [ ] **Step 3: Move `ProviderNotConfiguredError` into base.py**

Cut the class from `factory.py:48-64` and paste into `provider_protocols/base.py` (after `ProtocolCapabilityError`):

```python
class ProviderNotConfiguredError(ValueError):
    """No credential resolved for the provider serving ``model``.

    Raised instead of building a keyless adapter (opaque upstream 401) or —
    the pre-2026-07-07 behavior — silently falling back to env vars."""

    def __init__(self, provider: str, model: str):
        self.provider = provider
        self.model = model
        super().__init__(
            f"AI provider '{provider}' is not configured for model {model!r}. "
            "Add your API key in Settings → AI Providers, or ask the admin "
            "to enable a platform model (Admin → AI Models)."
        )
```

In `factory.py`, replace the class definition with a re-export so existing
importers (`from app.services.ai.adapters.factory import ProviderNotConfiguredError`)
keep working:
```python
from app.services.ai.provider_protocols.base import (  # noqa: F401
    ProviderNotConfiguredError,
)
```
Add `ProviderNotConfiguredError` to the package `__init__.py`'s imports + `__all__`.

- [ ] **Step 4: Add `build_chat_adapter` to each of the 6 chat protocol modules**

Example — `claude.py` (add the method; keep the lazy import inside):
```python
    def build_chat_adapter(self, model, creds):
        from app.services.ai.adapters.claude import ClaudeAdapter
        from app.services.ai.provider_protocols.base import ProviderNotConfiguredError

        if not (creds.get("api_key") or "").strip():
            raise ProviderNotConfiguredError("claude", model)
        return ClaudeAdapter(api_key=creds["api_key"], default_model=model or "claude-opus-4-5")
```
Do the same for `openai/deepseek/doubao/modelscope/qwen` using the constructors
in the Reference block above. `qwen.build_chat_adapter` checks `base_url` (not
`api_key`) and raises `ProviderNotConfiguredError("qwen", model)` when blank.
`deepseek`/`doubao` lazy-import their default URL constant; `modelscope`
lazy-imports `MODELSCOPE_DEFAULT_URL` from `app.services.ai.adapters.modelscope`.

- [ ] **Step 5: Rewire `factory._build_adapter_for_key` to delegate**

Keep everything up to and including the RotatingAdapter block and the
`user_key`/`user_base` computation (factory.py:186-224). Replace the six
`if provider_key == ...` branches (factory.py:226-284) with:
```python
    from app.services.ai.provider_protocols import get_chat_protocol

    protocol = get_chat_protocol(provider_key)
    return protocol.build_chat_adapter(
        model, {"api_key": user_key, "base_url": user_base}
    )
```
`resolve_provider_key`, `provider_key_for_model`, `_PROVIDER_KEYS`,
`get_adapter_for_user`, `get_adapter_for_key`, `get_adapter` are UNCHANGED.

- [ ] **Step 6: Run the full chat-dispatch regression set**

```bash
cd backend && uv run pytest tests/test_protocol_build_chat_adapter.py tests/test_catalog_provider_dispatch.py tests/test_adapter_factory_byo.py tests/test_provider_protocols_contract.py tests/test_ai_library_chat_wiring.py tests/test_ai_library_chat_wiring_byok.py tests/test_script_ai_adapter_dispatch.py tests/test_qwen_adapter.py -q
```
Expected: ALL PASS — behavior byte-identical.

- [ ] **Step 7: Verify the branches are gone + lint + commit**

```bash
cd backend
grep -n "if provider_key ==" app/services/ai/adapters/factory.py   # expect: no output
uv run black app/services/ai/adapters/factory.py app/services/ai/provider_protocols/ tests/test_protocol_build_chat_adapter.py && uv run isort app/services/ai/adapters/factory.py app/services/ai/provider_protocols/ tests/test_protocol_build_chat_adapter.py && uv run flake8 app/services/ai/adapters/factory.py app/services/ai/provider_protocols/ tests/test_protocol_build_chat_adapter.py
git add -A backend/app/services/ai backend/tests/test_protocol_build_chat_adapter.py
git commit -m "refactor(ai): chat adapter construction moves into protocol classes"
```

---

### Task C: Move image/video construction into the ark + jimeng protocols

**Goal:** `ark.build_image_provider`, `jimeng.build_image_provider`, and
`jimeng.build_video_provider` own the construction moved from `db_registry`.
`db_registry` keeps row selection and delegates building via
`resolve_generation_protocol`. `_ARK_PROVIDERS`/`_JIMENG_PROVIDERS` are retired.

**Files:**
- Modify: `backend/app/services/ai/provider_protocols/ark.py` `jimeng.py`
- Modify: `backend/app/services/media/parsers/video_providers/db_registry.py`
- Test: `backend/tests/test_protocol_build_generation.py`

**Interfaces:**
- Consumes: `resolve_generation_protocol(actual_provider)` (Task A); the image
  provider classes (lazy-imported).
- Produces: `ArkProtocol.build_image_provider(row) -> (BaseImageProvider, str)`;
  `JimengProtocol.build_image_provider(row) -> (BaseImageProvider, str)` and
  `build_video_provider(row) -> (JimengCliProvider, str)`. `db_registry`'s
  `resolve_image_provider`/`resolve_video_provider` signatures UNCHANGED.

**Reference — the exact construction to move** (current `db_registry.py`):
- jimeng image: `_JimengImageAdapter(JimengCliProvider()), actual_model` (the
  `_JimengImageAdapter` class stays in db_registry OR moves into jimeng.py — move
  it into jimeng.py, it's jimeng-specific).
- ark image: `ArkImageProvider(api_key=row.get("api_key") or "", base_url=row.get("base_url") or "", default_model=actual_model), actual_model`.
- jimeng video: `JimengCliProvider(), actual_model`.
- `actual_model = row.get("actual_model") or ""`.

- [ ] **Step 1: Write the failing generation-build test**

```python
# backend/tests/test_protocol_build_generation.py
"""ark/jimeng protocols build their own image/video providers from a row."""

from __future__ import annotations

import pytest

from app.services.ai.provider_protocols import resolve_generation_protocol
from app.services.ai.provider_protocols.base import ProtocolCapabilityError
from app.services.media.parsers.video_providers.ark_image import ArkImageProvider
from app.services.media.parsers.video_providers.jimeng_cli import JimengCliProvider


@pytest.mark.unit
def test_ark_builds_image_provider():
    row = {"api_key": "k", "base_url": "https://ark/v1", "actual_model": "seedream"}
    provider, model = resolve_generation_protocol("ark").build_image_provider(row)
    assert isinstance(provider, ArkImageProvider)
    assert model == "seedream"


@pytest.mark.unit
def test_ark_has_no_video():
    with pytest.raises(ProtocolCapabilityError):
        resolve_generation_protocol("ark").build_video_provider({})


@pytest.mark.unit
def test_jimeng_builds_image_and_video():
    row = {"actual_model": "v3"}
    img, m1 = resolve_generation_protocol("jimeng-cli").build_image_provider(row)
    assert m1 == "v3"
    vid, m2 = resolve_generation_protocol("jimeng").build_video_provider(row)  # alias
    assert isinstance(vid, JimengCliProvider)
    assert m2 == "v3"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_protocol_build_generation.py -q`
Expected: FAIL — ark/jimeng build hooks still raise `ProtocolCapabilityError`.

- [ ] **Step 3: Add build hooks to `ark.py` and `jimeng.py`**

`ark.py` — add:
```python
    def build_image_provider(self, row):
        from app.services.media.parsers.video_providers.ark_image import ArkImageProvider

        actual_model = row.get("actual_model") or ""
        provider = ArkImageProvider(
            api_key=row.get("api_key") or "",
            base_url=row.get("base_url") or "",
            default_model=actual_model,
        )
        return provider, actual_model
```

`jimeng.py` — move the `_JimengImageAdapter` class here (verbatim from
`db_registry.py:39-70`) and add:
```python
    def build_image_provider(self, row):
        from app.services.media.parsers.video_providers.jimeng_cli import JimengCliProvider

        actual_model = row.get("actual_model") or ""
        return _JimengImageAdapter(JimengCliProvider()), actual_model

    def build_video_provider(self, row):
        from app.services.media.parsers.video_providers.jimeng_cli import JimengCliProvider

        actual_model = row.get("actual_model") or ""
        return JimengCliProvider(), actual_model
```
(`_JimengImageAdapter` lazy-imports `ImageGenResult`/`TaskStatus`/`BaseImageProvider`
inside its methods, OR imports them at jimeng.py top — top import of
`video_providers.base` is safe, no cycle. Verify with a focused import check.)

- [ ] **Step 4: Rewire `db_registry` to delegate**

Replace the dispatch bodies of `resolve_image_provider` (db_registry.py:130-152)
and `resolve_video_provider` (db_registry.py:175-184) to delegate:
```python
    # resolve_image_provider, after row/actual_model are picked:
    from app.services.ai.provider_protocols import resolve_generation_protocol

    protocol = resolve_generation_protocol(actual_provider)
    if protocol is None:
        raise RuntimeError(
            f"No image provider implementation for actual_provider="
            f"{actual_provider!r} (catalog row name={row.get('name')!r})"
        )
    return protocol.build_image_provider(row)
```
```python
    # resolve_video_provider:
    from app.services.ai.provider_protocols import resolve_generation_protocol

    protocol = resolve_generation_protocol(actual_provider)
    if protocol is None or protocol.generation_family != "jimeng-cli":
        raise RuntimeError(
            f"No video provider implementation for actual_provider="
            f"{actual_provider!r} (catalog row name={row.get('name')!r})"
        )
    return protocol.build_video_provider(row)
```
Update `_pick_row`'s jimeng-first check to use the protocol
(`resolve_generation_protocol(r.get("actual_provider") or "")` has
`generation_family == "jimeng-cli"`) instead of `_JIMENG_PROVIDERS`. Delete the
now-unused `_ARK_PROVIDERS`/`_JIMENG_PROVIDERS` and the moved `_JimengImageAdapter`.
Remove the two now-unused contract tests in
`test_provider_protocols_contract.py` that asserted `db_registry._ARK_PROVIDERS`/
`_JIMENG_PROVIDERS` equal the derived sets (those symbols no longer exist) — OR,
if simpler, keep the sets as thin derived aliases used only by those tests; prefer
DELETING both the symbols and the two tests, since dispatch no longer uses them.

- [ ] **Step 5: Run the image/video regression set + full contract tests**

```bash
cd backend && uv run pytest tests/test_protocol_build_generation.py tests/test_provider_protocols_contract.py -q && uv run pytest tests/ -k "db_registry or image_provider or video_provider or generation" -q
```
Expected: ALL PASS.

- [ ] **Step 6: Verify + lint + commit**

```bash
cd backend
grep -n "_ARK_PROVIDERS\|_JIMENG_PROVIDERS" app/services/media/parsers/video_providers/db_registry.py   # expect: no output
uv run black app/services/media/parsers/video_providers/db_registry.py app/services/ai/provider_protocols/ tests/test_protocol_build_generation.py tests/test_provider_protocols_contract.py && uv run isort <same files> && uv run flake8 <same files>
git add -A backend
git commit -m "refactor(media): image/video construction moves into ark + jimeng protocols"
```

---

### Task 4: Admin `protocols` endpoint

**Files:**
- Modify: `backend/app/api/admin/nous_model_router.py` (add one route + one response schema)
- Modify: `backend/app/schemas/nous_model.py` (add response schema)
- Test: `backend/tests/test_nous_model_protocols_endpoint.py`

**Interfaces:**
- Consumes: `provider_protocols.all_protocols()` (Task 1); `AdminAuthDep`.
- Produces: `GET /api/v1/admin/mediahub-models/protocols` → `ProviderProtocolListResponse{ protocols: list[ProviderProtocolItem] }` where `ProviderProtocolItem` = `{key: str, label: str, description: str, model_types: list[str], aliases: list[str], is_default: bool}`. Endpoint function name `list_provider_protocols(auth)`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_nous_model_protocols_endpoint.py
"""GET protocols endpoint returns the registry. Called directly (admin
endpoint convention), not via TestClient."""

from __future__ import annotations

import pytest

from app.api.admin.nous_model_router import list_provider_protocols


@pytest.mark.asyncio
async def test_protocols_endpoint_returns_registry():
    resp = await list_provider_protocols(auth=object())  # AdminAuthDep unused in body
    keys = {p.key for p in resp.protocols}
    # Every chat + generation protocol is present.
    assert {"qwen", "openai", "claude", "deepseek", "doubao", "modelscope",
            "ark", "jimeng-cli"} <= keys
    qwen = next(p for p in resp.protocols if p.key == "qwen")
    assert qwen.is_default is True
    assert "llm" in qwen.model_types
    jimeng = next(p for p in resp.protocols if p.key == "jimeng-cli")
    assert "jimeng" in jimeng.aliases
    assert "image" in jimeng.model_types
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_nous_model_protocols_endpoint.py -q`
Expected: FAIL — `ImportError: cannot import name 'list_provider_protocols'`

- [ ] **Step 3: Add the response schema**

Append to `backend/app/schemas/nous_model.py`:

```python
class ProviderProtocolItem(BaseModel):
    """One provider protocol for the admin dropdown."""

    key: str
    label: str
    description: str
    model_types: List[str]
    aliases: List[str]
    is_default: bool


class ProviderProtocolListResponse(BaseModel):
    protocols: List[ProviderProtocolItem]
```

Ensure `from typing import List` (or `list[...]`) is imported at the top of the file — check the existing style there and match it (the file already uses `Optional`, so `from typing import List, Optional` is the safe edit).

- [ ] **Step 4: Add the route**

In `backend/app/api/admin/nous_model_router.py`, add the import and route. Add to the schema import block (lines 15-21):

```python
from app.schemas.nous_model import (
    MediahubModelCreate,
    MediahubModelProbeRequest,
    MediahubModelResponse,
    MediahubModelTestResponse,
    MediahubModelUpdate,
    ProviderProtocolItem,
    ProviderProtocolListResponse,
)
```

Add this route immediately after the `list_mediahub_models` route (after line 72):

```python
@router.get("/protocols", response_model=ProviderProtocolListResponse)
async def list_provider_protocols(auth: AdminAuthDep):
    """Provider protocols the platform can dispatch to (single source of
    truth for the admin ``actual_provider`` dropdown). Read-only."""
    from app.services.ai.provider_protocols import all_protocols

    return ProviderProtocolListResponse(
        protocols=[
            ProviderProtocolItem(
                key=p.key,
                label=p.label,
                description=p.description,
                model_types=list(p.model_types),
                aliases=list(p.aliases),
                is_default=p.is_default,
            )
            for p in all_protocols()
        ]
    )
```

Route-ordering check: FastAPI matches in declaration order, but `/protocols` (static) and `/{model_id}` (dynamic) don't collide for GET because `/{model_id}` is only declared for PUT/DELETE/POST-test, and the GET list is at `""`. So `GET /protocols` is unambiguous. No reordering needed.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_nous_model_protocols_endpoint.py -q`
Expected: PASS

- [ ] **Step 6: Lint + commit**

```bash
cd backend && uv run black app/api/admin/nous_model_router.py app/schemas/nous_model.py tests/test_nous_model_protocols_endpoint.py && uv run isort app/api/admin/nous_model_router.py app/schemas/nous_model.py tests/test_nous_model_protocols_endpoint.py && uv run flake8 app/api/admin/nous_model_router.py app/schemas/nous_model.py tests/test_nous_model_protocols_endpoint.py
git add backend/app/api/admin/nous_model_router.py backend/app/schemas/nous_model.py backend/tests/test_nous_model_protocols_endpoint.py
git commit -m "feat(admin): GET /mediahub-models/protocols endpoint"
```

---

### Task 5: Admin UI — protocol dropdown

**Files:**
- Modify: `admin/src/pages/ai/index.tsx`

**Interfaces:**
- Consumes: `GET /api/v1/admin/mediahub-models/protocols` (Task 4).
- Produces: the "new provider" modal's Actual Provider field becomes an Arco `Select` with `allowCreate`, options from the fetched protocols; each option shows label + a model-types/description hint. Custom values still allowed.

- [ ] **Step 1: Add a protocols type + state + fetch**

Below the `NousModel` interface (after line 33), add:

```tsx
interface ProviderProtocol {
  key: string
  label: string
  description: string
  model_types: string[]
  aliases: string[]
  is_default: boolean
}
```

Add state near the other `useState` calls (after line 138, `fetchedModels`):

```tsx
const [protocols, setProtocols] = useState<ProviderProtocol[]>([])
```

Add a fetch effect after the existing `useEffect(() => { fetchModels() }, [fetchModels])` (line 176):

```tsx
useEffect(() => {
  // Best-effort: a failure leaves protocols=[] and the field falls back to
  // a plain text input (see the Actual Provider FormItem).
  const loadProtocols = async () => {
    try {
      const res = await fetch(`${apiBase}/api/v1/admin/mediahub-models/protocols`, { headers })
      if (res.ok) {
        const data = await res.json()
        setProtocols(data.protocols || [])
      }
    } catch {
      // silent — the field degrades to a text input
    }
  }
  loadProtocols()
  // eslint-disable-next-line react-hooks/exhaustive-deps
}, [token])
```

- [ ] **Step 2: Build the options memo**

Add near the other `useMemo` (after the `groups` memo). Each option label carries the applicable types as a hint:

```tsx
const protocolOptions = useMemo(
  () =>
    protocols.map((p) => ({
      label: `${p.label} — ${p.model_types.join('/')}`,
      value: p.key,
    })),
  [protocols],
)
```

- [ ] **Step 3: Swap the Actual Provider Input for a Select**

Replace the "new" modal's Actual Provider FormItem (lines 680-682):

```tsx
              <FormItem label="Actual Provider" field="actual_provider" rules={[{ required: true }]}>
                <Input placeholder="e.g. openai, deepseek, doubao" />
              </FormItem>
```

with:

```tsx
              <FormItem
                label="Actual Provider"
                field="actual_provider"
                rules={[{ required: true }]}
                extra="Dispatch protocol. Unknown/custom values fall back to the generic OpenAI-compatible adapter."
              >
                <Select
                  allowCreate
                  showSearch
                  placeholder="Select a protocol or type a custom value"
                  options={protocolOptions}
                />
              </FormItem>
```

`Select` is already imported (line 3). No other change needed — form value semantics are identical (a string in `actual_provider`).

- [ ] **Step 4: Verify the build**

Run: `cd admin && npm run build`
Expected: build succeeds (TypeScript compiles, no unused-var errors). If `ProviderProtocol` or `protocolOptions` is flagged unused, re-check Steps 2-3 wired them in.

- [ ] **Step 5: Commit**

```bash
git add admin/src/pages/ai/index.tsx
git commit -m "feat(admin): Actual Provider dropdown from protocol registry"
```

---

### Task 6: Version bump + ship

**Files:**
- Modify: `frontend/package.json` (version bump — the repo's single version source)

- [ ] **Step 1: Read current master version, bump patch**

```bash
git fetch origin master --quiet
git show origin/master:frontend/package.json | grep '"version"'
```

Set `frontend/package.json` `"version"` to the next patch above whatever master shows (do NOT assume a number — read it first; parallel merges move it).

- [ ] **Step 2: Full changed-file lint gate**

```bash
cd backend && uv run flake8 app/services/ai/provider_protocols.py app/services/ai/adapters/factory.py app/services/media/parsers/video_providers/db_registry.py app/api/admin/nous_model_router.py app/schemas/nous_model.py
```
Expected: no output (clean).

- [ ] **Step 3: Run the full affected test set**

```bash
cd backend && uv run pytest tests/test_provider_protocols.py tests/test_provider_protocols_contract.py tests/test_nous_model_protocols_endpoint.py tests/test_catalog_provider_dispatch.py tests/test_adapter_factory_byo.py -q
```
Expected: all PASS.

- [ ] **Step 4: Commit, push, PR**

```bash
git add frontend/package.json
git commit -m "chore: bump version for provider protocol registry"
git push -u origin <branch>
gh pr create --base master --title "feat(ai): provider protocol registry — explicit admin protocol management" --body "<summary + test plan>"
```

PR body should cover: single-source registry, derived factory/db_registry sets, contract tests as the anti-drift guard, read-only protocols endpoint, admin dropdown with allowCreate, zero migration / zero data change / fail-open preserved.

---

## Notes for the executor

- **Deploy is gated by GitHub runners** (2026-07-13: private-repo billing blocks runners; a maintainer flips the repo public, reruns `deploy-backend`, then flips back). Do NOT attempt to fix runners in code. Merge with `--admin` if CI is infra-blocked and the local test+lint gate is green.
- **No hot-cp needed** for this PR — it ships normally once the image builds; there is no prod outage to patch around.
- The contract test (`test_provider_protocols_contract.py`) is the point of the whole PR: it is the guard that would have caught the #1313 drift. Do not weaken it to "pass" — if it fails, a real dispatch surface disagrees with the registry.
