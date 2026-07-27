# Memory Provider Interface — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce a `MemoryProvider` abstraction and a settings-driven registry that wraps the existing Honcho (L2) and Graphiti (L3) memory services, with **zero behavior change** (defaults select exactly the current providers).

**Architecture:** Two memory *slots* — `l2` (user model, today Honcho) and `l3` (knowledge graph, today Graphiti) — run simultaneously. Each slot's active provider is chosen from `system_settings` (`memory.l2_provider` default `honcho`, `memory.l3_provider` default `graphiti`). The provider is a thin adapter over the existing `HonchoMemoryService` / `GraphMemoryService`; the write path (`write_memory.py`) and the read/inject path (`ai_library_chat_wiring.py`) acquire their service through the registry instead of the per-service factory. This is the Nous/Hermes "pluggable provider" pattern, adapted to two simultaneous slots.

**Tech Stack:** Python 3.13, FastAPI, DBOS workflows, pytest (asyncio), `unittest.mock`. Backend lint = black + isort + flake8 (NOT ruff).

## Global Constraints

- Backend lint gate before push: run `black` / `isort` / `flake8` on every changed `.py` (project uses flake8, not ruff).
- TDD: write the failing test first, watch it fail, implement, watch it pass.
- **Behavior must be provably unchanged.** Default registry config selects Honcho (l2) + Graphiti (l3); adapters call the exact same underlying service methods with the exact same arguments, in the same order, with the same short-circuit gates. The two gates that currently order as "cheap global `enabled` flag FIRST, then per-user `prefs.learn` settings read" MUST keep that order (a comment in `write_memory.py` documents why: a disabled deployment must never pay the settings read).
- Never dispatch a workflow inside a `@DBOS.step`. This plan does NOT change DBOS step structure — `write_graph_episode_step` / `write_honcho_turn_step` stay as-is; only the plain helper functions they call change internally.
- This is a pure refactor PR (no logic/feature changes). Per repo convention it must merge within 24h.
- Settings reads use `system_settings` via the existing async reader pattern (`app.db.engine.fetch_val` / the settings repo). Values come back JSONB-typed (a string here).

---

## File Structure

| File | Responsibility |
|------|----------------|
| `backend/app/services/ai/memory/provider.py` (new) | `MemoryLayer` enum, `MemoryTurn` dataclass, `MemoryProvider` ABC |
| `backend/app/services/ai/memory/providers/__init__.py` (new) | package marker |
| `backend/app/services/ai/memory/providers/graphiti_provider.py` (new) | `GraphitiProvider` — adapter over `GraphMemoryService` |
| `backend/app/services/ai/memory/providers/honcho_provider.py` (new) | `HonchoProvider` — adapter over `HonchoMemoryService` |
| `backend/app/services/ai/memory/registry.py` (new) | `l2_provider()` / `l3_provider()` — read settings, return the active adapter |
| `backend/app/workflows/write_memory.py` (modify) | `_write_graph_episode` / `_write_honcho_turn` route through the registry |
| `backend/app/services/ai/chat/ai_library_chat_wiring.py` (modify) | L2 inject path acquires provider via `registry.l2_provider()` |
| `backend/tests/memory/test_memory_provider_*.py` (new) | per-adapter + registry + routing tests |

---

### Task 1: `MemoryProvider` ABC + `MemoryTurn` + `MemoryLayer`

**Files:**
- Create: `backend/app/services/ai/memory/provider.py`
- Test: `backend/tests/memory/test_memory_provider_abc.py`

**Interfaces:**
- Produces:
  - `class MemoryLayer(str, Enum)` with members `L2 = "l2"`, `L3 = "l3"`.
  - `@dataclass(frozen=True) class MemoryTurn` with fields: `user_id: str`, `agent_id: str`, `session_id: str`, `run_id: Optional[str]`, `iteration: int`, `user_msgs: list[str]`, `asst_msgs: list[str]`.
  - `class MemoryProvider(ABC)` with: `name: str` (abstract property), `layer: MemoryLayer` (abstract property), `def enabled(self) -> bool` (abstract, cheap sync global-flag gate), `async def is_operative(self) -> bool` (abstract, enabled + reachable), `async def record_turn(self, turn: MemoryTurn) -> bool` (abstract), `async def get_context(self, *, user_id: str, query: str = "", workspace_id: Optional[str] = None, group_ids: Optional[list[str]] = None) -> Optional[str]` (abstract), `async def reload(self) -> None` (abstract — drop cached client/config), `async def health(self) -> bool` (abstract — Phase 1 returns `is_operative()`).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/memory/test_memory_provider_abc.py
from app.services.ai.memory.provider import MemoryLayer, MemoryProvider, MemoryTurn


def test_memory_layer_values():
    assert MemoryLayer.L2.value == "l2"
    assert MemoryLayer.L3.value == "l3"


def test_memory_turn_is_frozen_dataclass():
    turn = MemoryTurn(
        user_id="u1",
        agent_id="a1",
        session_id="s1",
        run_id="r1",
        iteration=0,
        user_msgs=["hi"],
        asst_msgs=["hello"],
    )
    assert turn.user_id == "u1"
    assert turn.user_msgs == ["hi"]


def test_memory_provider_is_abstract():
    # Cannot instantiate the ABC directly.
    import pytest

    with pytest.raises(TypeError):
        MemoryProvider()  # type: ignore[abstract]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/memory/test_memory_provider_abc.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.ai.memory.provider'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/services/ai/memory/provider.py
"""Memory provider abstraction (Phase 1 — Nous/Hermes pluggable-provider model).

nous runs two memory *slots* simultaneously:

  L2 — user model (today Honcho): "who is this user / what do they prefer".
  L3 — knowledge graph (today Graphiti): "what facts/entities were discussed".

Each slot's active provider is chosen from system_settings; a provider is a
thin adapter over the underlying service. Unlike Hermes (one external provider
at a time) the two slots are independent and both active.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional


class MemoryLayer(str, Enum):
    L2 = "l2"
    L3 = "l3"


@dataclass(frozen=True)
class MemoryTurn:
    """One chat exchange to persist, in provider-neutral form. Each adapter
    derives its own provider-specific shape (Honcho workspace / Graphiti
    group_id, episode body, etc.) from these raw fields."""

    user_id: str
    agent_id: str
    session_id: str
    run_id: Optional[str]
    iteration: int
    user_msgs: List[str]
    asst_msgs: List[str]


class MemoryProvider(ABC):
    """Adapter over one memory backend for one slot."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short id, e.g. 'honcho', 'graphiti'."""

    @property
    @abstractmethod
    def layer(self) -> MemoryLayer:
        """Which slot this provider serves."""

    @abstractmethod
    def enabled(self) -> bool:
        """Cheap, sync global-flag gate. Checked BEFORE any settings read so a
        disabled deployment never pays for one."""

    @abstractmethod
    async def is_operative(self) -> bool:
        """enabled AND reachable/configured (read path uses this)."""

    @abstractmethod
    async def record_turn(self, turn: MemoryTurn) -> bool:
        """Persist one exchange. Returns True on success, False on any failure
        or when not operative. Never raises."""

    @abstractmethod
    async def get_context(
        self,
        *,
        user_id: str,
        query: str = "",
        workspace_id: Optional[str] = None,
        group_ids: Optional[List[str]] = None,
    ) -> Optional[str]:
        """Return a memory-context block to inject, or None."""

    @abstractmethod
    async def reload(self) -> None:
        """Drop cached client/config so the NEXT call re-reads current config.
        For in-process providers (Graphiti, future Mem0) this is how an admin
        config change takes effect without a process restart. Never raises."""

    @abstractmethod
    async def health(self) -> bool:
        """Liveness for the admin control plane's green/red dot. Phase 1 returns
        is_operative(); later phases may do a real probe. Never raises."""


__all__ = ["MemoryLayer", "MemoryTurn", "MemoryProvider"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/memory/test_memory_provider_abc.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Lint + commit**

```bash
cd backend && uv run black app/services/ai/memory/provider.py tests/memory/test_memory_provider_abc.py && uv run isort app/services/ai/memory/provider.py tests/memory/test_memory_provider_abc.py && uv run flake8 app/services/ai/memory/provider.py tests/memory/test_memory_provider_abc.py
cd .. && git add backend/app/services/ai/memory/provider.py backend/tests/memory/test_memory_provider_abc.py
git commit -m "refactor(memory): add MemoryProvider ABC + MemoryTurn (Phase 1)"
```

---

### Task 2: `GraphitiProvider` adapter

**Files:**
- Create: `backend/app/services/ai/memory/providers/__init__.py` (empty)
- Create: `backend/app/services/ai/memory/providers/graphiti_provider.py`
- Test: `backend/tests/memory/test_graphiti_provider.py`

**Interfaces:**
- Consumes: `MemoryProvider`, `MemoryLayer`, `MemoryTurn` from Task 1; `get_graph_memory_service()` and `GraphMemoryService` from `app.services.ai.memory.graph_memory`; `_build_turn_episode` from `app.workflows.write_memory`.
- Produces: `class GraphitiProvider(MemoryProvider)`; `name == "graphiti"`, `layer == MemoryLayer.L3`. `record_turn` mirrors `_write_graph_episode`'s service-side logic EXACTLY: `enabled()` returns `service.config.enabled`; `record_turn` builds `body = _build_turn_episode(turn.user_msgs, turn.asst_msgs)`, returns False if falsy, else `service.add_chat_episode(group_id=f"user-{turn.user_id}", name=f"chat-{turn.session_id}-{turn.run_id or turn.iteration}", body=body, source_description="nous chat turn")`. `get_context` wraps `service.search(query, group_ids=group_ids or [], limit=10)` and joins fact strings, or None.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/memory/test_graphiti_provider.py
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.memory.provider import MemoryLayer, MemoryTurn
from app.services.ai.memory.providers.graphiti_provider import GraphitiProvider


def _turn():
    return MemoryTurn(
        user_id="u1", agent_id="a1", session_id="s1", run_id="r1",
        iteration=0, user_msgs=["hi"], asst_msgs=["hello"],
    )


@pytest.mark.asyncio
async def test_record_turn_calls_add_chat_episode_with_exact_args():
    svc = SimpleNamespace(
        config=SimpleNamespace(enabled=True),
        add_chat_episode=AsyncMock(return_value=True),
    )
    prov = GraphitiProvider()
    assert prov.name == "graphiti"
    assert prov.layer == MemoryLayer.L3
    assert prov.enabled() is False  # no service bound yet → reads via factory

    with patch(
        "app.services.ai.memory.providers.graphiti_provider.get_graph_memory_service",
        return_value=svc,
    ):
        assert prov.enabled() is True
        ok = await prov.record_turn(_turn())

    assert ok is True
    svc.add_chat_episode.assert_awaited_once_with(
        group_id="user-u1",
        name="chat-s1-r1",
        body=svc.add_chat_episode.await_args.kwargs["body"],  # body built internally
        source_description="nous chat turn",
    )
    assert svc.add_chat_episode.await_args.kwargs["body"]  # non-empty


@pytest.mark.asyncio
async def test_record_turn_returns_false_when_disabled():
    svc = SimpleNamespace(config=SimpleNamespace(enabled=False), add_chat_episode=AsyncMock())
    with patch(
        "app.services.ai.memory.providers.graphiti_provider.get_graph_memory_service",
        return_value=svc,
    ):
        ok = await GraphitiProvider().record_turn(_turn())
    assert ok is False
    svc.add_chat_episode.assert_not_awaited()


@pytest.mark.asyncio
async def test_reload_clears_cached_config_and_health_uses_is_enabled():
    svc = SimpleNamespace(config=SimpleNamespace(enabled=True), is_enabled=AsyncMock(return_value=True))
    with patch(
        "app.services.ai.memory.providers.graphiti_provider.get_graph_memory_service",
        return_value=svc,
    ):
        prov = GraphitiProvider()
        await prov.reload()
        assert svc.config is None  # next call re-reads from_settings
        assert await prov.health() is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/memory/test_graphiti_provider.py -q`
Expected: FAIL with `ModuleNotFoundError: ...graphiti_provider`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/services/ai/memory/providers/__init__.py
```
(empty file)

```python
# backend/app/services/ai/memory/providers/graphiti_provider.py
"""GraphitiProvider — L3 adapter over GraphMemoryService (Phase 1).

Behaviour-preserving: record_turn reproduces the service-side body of the
existing ``write_memory._write_graph_episode`` (minus the cross-provider
prefs.learn gate, which stays at the call site). get_context wraps search().
"""

from __future__ import annotations

from typing import List, Optional

from loguru import logger

from app.services.ai.memory.graph_memory import get_graph_memory_service
from app.services.ai.memory.provider import MemoryLayer, MemoryProvider, MemoryTurn


class GraphitiProvider(MemoryProvider):
    @property
    def name(self) -> str:
        return "graphiti"

    @property
    def layer(self) -> MemoryLayer:
        return MemoryLayer.L3

    def enabled(self) -> bool:
        try:
            return bool(get_graph_memory_service().config.enabled)
        except Exception:  # noqa: BLE001
            return False

    async def is_operative(self) -> bool:
        try:
            return await get_graph_memory_service().is_enabled()
        except Exception:  # noqa: BLE001
            return False

    async def record_turn(self, turn: MemoryTurn) -> bool:
        from app.workflows.write_memory import _build_turn_episode

        service = get_graph_memory_service()
        if not service.config.enabled:
            return False
        body = _build_turn_episode(turn.user_msgs, turn.asst_msgs)
        if not body:
            return False
        return await service.add_chat_episode(
            group_id=f"user-{turn.user_id}",
            name=f"chat-{turn.session_id}-{turn.run_id or turn.iteration}",
            body=body,
            source_description="nous chat turn",
        )

    async def get_context(
        self,
        *,
        user_id: str,
        query: str = "",
        workspace_id: Optional[str] = None,
        group_ids: Optional[List[str]] = None,
    ) -> Optional[str]:
        try:
            facts = await get_graph_memory_service().search(
                query, group_ids=group_ids or [], limit=10
            )
        except Exception:  # noqa: BLE001
            logger.exception("[graphiti_provider] search failed (user=%s)", user_id)
            return None
        if not facts:
            return None
        rendered = "\n".join(str(getattr(f, "fact", f)) for f in facts)
        return rendered or None

    async def reload(self) -> None:
        # In-process: drop the cached config so the next call re-reads
        # GraphMemoryConfig.from_settings(). GraphMemoryService._ensure_config
        # rebuilds when config is None.
        try:
            get_graph_memory_service().config = None  # type: ignore[assignment]
        except Exception:  # noqa: BLE001
            logger.warning("[graphiti_provider] reload failed")

    async def health(self) -> bool:
        return await self.is_operative()


__all__ = ["GraphitiProvider"]
```

> Note for the implementer: `_build_turn_episode` lives in `write_memory.py` and is imported lazily inside `record_turn` to avoid a circular import (write_memory imports providers transitively in Task 5). Confirm the exact `GraphFact` attribute used by `search()` — read `graph_memory.py:273` (`class GraphFact`); use its fact-text attribute. The test only asserts non-empty, so adjust the `getattr(f, "fact", f)` to the real attribute name if different.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/memory/test_graphiti_provider.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Lint + commit**

```bash
cd backend && uv run black app/services/ai/memory/providers/ tests/memory/test_graphiti_provider.py && uv run isort app/services/ai/memory/providers/ tests/memory/test_graphiti_provider.py && uv run flake8 app/services/ai/memory/providers/ tests/memory/test_graphiti_provider.py
cd .. && git add backend/app/services/ai/memory/providers/ backend/tests/memory/test_graphiti_provider.py
git commit -m "refactor(memory): GraphitiProvider L3 adapter (Phase 1)"
```

---

### Task 3: `HonchoProvider` adapter

**Files:**
- Create: `backend/app/services/ai/memory/providers/honcho_provider.py`
- Test: `backend/tests/memory/test_honcho_provider.py`

**Interfaces:**
- Consumes: `MemoryProvider`, `MemoryLayer`, `MemoryTurn` from Task 1; `get_honcho_memory_service()` from `app.services.ai.memory.honcho_memory`; `_resolve_team_workspace` from `app.workflows.write_memory`.
- Produces: `class HonchoProvider(MemoryProvider)`; `name == "honcho"`, `layer == MemoryLayer.L2`. `enabled()` returns `service.config.enabled`. `record_turn` mirrors `_write_honcho_turn`'s service-side logic EXACTLY: take `user_message = turn.user_msgs[-1] if turn.user_msgs else ""`, `assistant_message = turn.asst_msgs[-1] if turn.asst_msgs else ""`, return False if both blank, resolve `workspace_id = await _resolve_team_workspace(turn.session_id)`, call `service.add_chat_turn(user_id=..., agent_id=..., session_id=..., user_message=..., assistant_message=..., workspace_id=...)`. `get_context` wraps `service.get_user_representation(user_id=user_id, workspace_id=workspace_id)` (the per-turn inject uses representation, NOT the slow dialectic `get_user_context`).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/memory/test_honcho_provider.py
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.memory.provider import MemoryLayer, MemoryTurn
from app.services.ai.memory.providers.honcho_provider import HonchoProvider


def _turn():
    return MemoryTurn(
        user_id="u1", agent_id="a1", session_id="s1", run_id="r1",
        iteration=0, user_msgs=["hi", "still me"], asst_msgs=["hello", "yo"],
    )


@pytest.mark.asyncio
async def test_record_turn_posts_latest_exchange_with_resolved_workspace():
    svc = SimpleNamespace(
        config=SimpleNamespace(enabled=True),
        add_chat_turn=AsyncMock(return_value=True),
    )
    prov = HonchoProvider()
    assert prov.name == "honcho"
    assert prov.layer == MemoryLayer.L2

    with patch(
        "app.services.ai.memory.providers.honcho_provider.get_honcho_memory_service",
        return_value=svc,
    ), patch(
        "app.services.ai.memory.providers.honcho_provider._resolve_team_workspace",
        new=AsyncMock(return_value="team-42"),
    ):
        ok = await prov.record_turn(_turn())

    assert ok is True
    svc.add_chat_turn.assert_awaited_once_with(
        user_id="u1", agent_id="a1", session_id="s1",
        user_message="still me", assistant_message="yo", workspace_id="team-42",
    )


@pytest.mark.asyncio
async def test_get_context_wraps_user_representation():
    svc = SimpleNamespace(
        config=SimpleNamespace(enabled=True),
        get_user_representation=AsyncMock(return_value="the user likes brevity"),
    )
    with patch(
        "app.services.ai.memory.providers.honcho_provider.get_honcho_memory_service",
        return_value=svc,
    ):
        out = await HonchoProvider().get_context(user_id="u1", workspace_id="team-42")
    assert out == "the user likes brevity"
    svc.get_user_representation.assert_awaited_once_with(user_id="u1", workspace_id="team-42")


@pytest.mark.asyncio
async def test_reload_closes_and_clears_client():
    closed = {"v": False}

    class _Client:
        async def aclose(self):
            closed["v"] = True

    svc = SimpleNamespace(config=SimpleNamespace(enabled=True), client=_Client())
    with patch(
        "app.services.ai.memory.providers.honcho_provider.get_honcho_memory_service",
        return_value=svc,
    ):
        await HonchoProvider().reload()
    assert closed["v"] is True
    assert svc.client is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/memory/test_honcho_provider.py -q`
Expected: FAIL with `ModuleNotFoundError: ...honcho_provider`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/services/ai/memory/providers/honcho_provider.py
"""HonchoProvider — L2 adapter over HonchoMemoryService (Phase 1).

Behaviour-preserving: record_turn reproduces the service-side body of the
existing ``write_memory._write_honcho_turn`` (minus the cross-provider
prefs.learn gate, which stays at the call site). get_context wraps the
per-turn inject path's ``get_user_representation`` (NOT the slow dialectic
``get_user_context``).
"""

from __future__ import annotations

from typing import List, Optional

from loguru import logger

from app.services.ai.memory.honcho_memory import get_honcho_memory_service
from app.services.ai.memory.provider import MemoryLayer, MemoryProvider, MemoryTurn


class HonchoProvider(MemoryProvider):
    @property
    def name(self) -> str:
        return "honcho"

    @property
    def layer(self) -> MemoryLayer:
        return MemoryLayer.L2

    def enabled(self) -> bool:
        try:
            return bool(get_honcho_memory_service().config.enabled)
        except Exception:  # noqa: BLE001
            return False

    async def is_operative(self) -> bool:
        try:
            return bool(get_honcho_memory_service().config.operative())
        except Exception:  # noqa: BLE001
            return False

    async def record_turn(self, turn: MemoryTurn) -> bool:
        from app.workflows.write_memory import _resolve_team_workspace

        service = get_honcho_memory_service()
        if not service.config.enabled:
            return False
        user_message = turn.user_msgs[-1] if turn.user_msgs else ""
        assistant_message = turn.asst_msgs[-1] if turn.asst_msgs else ""
        if not (user_message.strip() or assistant_message.strip()):
            return False
        workspace_id = await _resolve_team_workspace(turn.session_id)
        return await service.add_chat_turn(
            user_id=turn.user_id,
            agent_id=turn.agent_id,
            session_id=turn.session_id,
            user_message=user_message,
            assistant_message=assistant_message,
            workspace_id=workspace_id,
        )

    async def get_context(
        self,
        *,
        user_id: str,
        query: str = "",
        workspace_id: Optional[str] = None,
        group_ids: Optional[List[str]] = None,
    ) -> Optional[str]:
        return await get_honcho_memory_service().get_user_representation(
            user_id=user_id, workspace_id=workspace_id
        )

    async def reload(self) -> None:
        # Drop the cached httpx client so a changed connection (Phase 2: when
        # base_url/workspace move to system_settings) takes effect on the next
        # call. The HonchoMemoryService singleton lazily rebuilds its client.
        try:
            service = get_honcho_memory_service()
            if getattr(service, "client", None) is not None:
                try:
                    await service.client.aclose()  # type: ignore[union-attr]
                except Exception:  # noqa: BLE001
                    pass
                service.client = None  # type: ignore[assignment]
        except Exception:  # noqa: BLE001
            logger.warning("[honcho_provider] reload failed")

    async def health(self) -> bool:
        return await self.is_operative()


__all__ = ["HonchoProvider"]
```

> Note: `_resolve_team_workspace` is imported lazily to avoid a circular import (write_memory imports the registry in Task 5).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/memory/test_honcho_provider.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Lint + commit**

```bash
cd backend && uv run black app/services/ai/memory/providers/honcho_provider.py tests/memory/test_honcho_provider.py && uv run isort app/services/ai/memory/providers/honcho_provider.py tests/memory/test_honcho_provider.py && uv run flake8 app/services/ai/memory/providers/honcho_provider.py tests/memory/test_honcho_provider.py
cd .. && git add backend/app/services/ai/memory/providers/honcho_provider.py backend/tests/memory/test_honcho_provider.py
git commit -m "refactor(memory): HonchoProvider L2 adapter (Phase 1)"
```

---

### Task 4: `memory_registry` — settings-driven slot selection

**Files:**
- Create: `backend/app/services/ai/memory/registry.py`
- Test: `backend/tests/memory/test_memory_registry.py`

**Interfaces:**
- Consumes: `MemoryProvider` from Task 1; `HonchoProvider` (Task 3); `GraphitiProvider` (Task 2); a settings reader.
- Produces:
  - `async def l2_provider() -> Optional[MemoryProvider]` — reads setting `memory.l2_provider` (default `"honcho"`); returns `HonchoProvider()` when `"honcho"`, `None` when `"none"`, else `HonchoProvider()` (unknown → safe default = current). (Mem0 added in Phase 3.)
  - `async def l3_provider() -> Optional[MemoryProvider]` — reads `memory.l3_provider` (default `"graphiti"`); `GraphitiProvider()` when `"graphiti"`, `None` when `"none"`, else `GraphitiProvider()`.
  - `async def _read_provider_setting(key: str, default: str) -> str` — internal; reads `system_settings` via `app.db.engine.fetch_val`, returns `default` on any miss/error.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/memory/test_memory_registry.py
from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.memory import registry
from app.services.ai.memory.providers.graphiti_provider import GraphitiProvider
from app.services.ai.memory.providers.honcho_provider import HonchoProvider


@pytest.mark.asyncio
async def test_defaults_select_current_providers():
    # No row → default honcho / graphiti (zero behavior change).
    with patch.object(registry, "_read_provider_setting", new=AsyncMock(side_effect=lambda k, d: d)):
        l2 = await registry.l2_provider()
        l3 = await registry.l3_provider()
    assert isinstance(l2, HonchoProvider)
    assert isinstance(l3, GraphitiProvider)


@pytest.mark.asyncio
async def test_none_disables_slot():
    with patch.object(registry, "_read_provider_setting", new=AsyncMock(return_value="none")):
        assert await registry.l2_provider() is None
        assert await registry.l3_provider() is None


@pytest.mark.asyncio
async def test_unknown_value_falls_back_to_current_provider():
    with patch.object(registry, "_read_provider_setting", new=AsyncMock(return_value="mem0")):
        # Mem0 not implemented in Phase 1 → safe fallback to current default.
        assert isinstance(await registry.l2_provider(), HonchoProvider)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/memory/test_memory_registry.py -q`
Expected: FAIL with `AttributeError: module 'app.services.ai.memory.registry' has no attribute ...` / ModuleNotFoundError

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/services/ai/memory/registry.py
"""Settings-driven memory slot selection (Phase 1).

Two independent slots:
  memory.l2_provider — user model   (default 'honcho')
  memory.l3_provider — knowledge graph (default 'graphiti')

Phase 1 ships only the current providers; an unknown value falls back to the
current default so a stray setting can never silently disable memory. 'none'
explicitly disables a slot. Mem0/Hindsight arrive in Phase 3.
"""

from __future__ import annotations

from typing import Optional

from loguru import logger

from app.services.ai.memory.provider import MemoryProvider
from app.services.ai.memory.providers.graphiti_provider import GraphitiProvider
from app.services.ai.memory.providers.honcho_provider import HonchoProvider


async def _read_provider_setting(key: str, default: str) -> str:
    """Read a memory.* provider setting; ``default`` on any miss/error."""
    try:
        from app.db import engine as db_engine

        value = await db_engine.fetch_val(
            "SELECT value FROM public.system_settings WHERE key = :k",
            {"k": key},
        )
        if value is None:
            return default
        # system_settings.value is JSONB — a quoted string here. asyncpg may
        # hand back the raw JSON text ('"honcho"') or the decoded str; strip
        # surrounding quotes/whitespace defensively.
        text = str(value).strip().strip('"').strip()
        return text or default
    except Exception:  # noqa: BLE001
        logger.warning("[memory_registry] read %s failed; using default %r", key, default)
        return default


async def l2_provider() -> Optional[MemoryProvider]:
    choice = await _read_provider_setting("memory.l2_provider", "honcho")
    if choice == "none":
        return None
    if choice != "honcho":
        logger.info("[memory_registry] l2 '%s' not available in Phase 1; using honcho", choice)
    return HonchoProvider()


async def l3_provider() -> Optional[MemoryProvider]:
    choice = await _read_provider_setting("memory.l3_provider", "graphiti")
    if choice == "none":
        return None
    if choice != "graphiti":
        logger.info("[memory_registry] l3 '%s' not available in Phase 1; using graphiti", choice)
    return GraphitiProvider()


__all__ = ["l2_provider", "l3_provider"]
```

> Implementer: confirm `app.db.engine.fetch_val` exists with this signature (it is used in `scheduled_health.py:112` as `await db_engine.fetch_val("SELECT ...")`). If the project's settings repo (`app.repositories.admin.system_settings_repository`) is the canonical reader, prefer it — but `fetch_val` keeps the registry dependency-light and matches the existing health-check pattern.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/memory/test_memory_registry.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Lint + commit**

```bash
cd backend && uv run black app/services/ai/memory/registry.py tests/memory/test_memory_registry.py && uv run isort app/services/ai/memory/registry.py tests/memory/test_memory_registry.py && uv run flake8 app/services/ai/memory/registry.py tests/memory/test_memory_registry.py
cd .. && git add backend/app/services/ai/memory/registry.py backend/tests/memory/test_memory_registry.py
git commit -m "refactor(memory): settings-driven provider registry (Phase 1)"
```

---

### Task 5: Route the write path through the registry

**Files:**
- Modify: `backend/app/workflows/write_memory.py` (`_write_graph_episode` lines 77-115, `_write_honcho_turn` lines 142-180)
- Test: `backend/tests/memory/test_write_memory_routing.py` (new) — plus the existing `write_memory` tests must still pass.

**Interfaces:**
- Consumes: `registry.l2_provider()` / `registry.l3_provider()` (Task 4); `MemoryTurn` (Task 1).
- Produces: unchanged public signatures of `_write_graph_episode` / `_write_honcho_turn` / their DBOS step wrappers. Internals now: acquire provider via registry → `if provider is None or not provider.enabled(): return False` → `prefs = await get_memory_prefs(...)` → `if not prefs.learn: return False` → `provider.record_turn(MemoryTurn(...))`. **Gate order preserved** (cheap `enabled()` before the `prefs.learn` settings read).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/memory/test_write_memory_routing.py
from unittest.mock import AsyncMock, patch

import pytest

from app.workflows import write_memory


@pytest.mark.asyncio
async def test_graph_write_routes_through_l3_provider():
    provider = AsyncMock()
    provider.enabled.return_value = True  # note: enabled() is sync; see impl
    provider.record_turn = AsyncMock(return_value=True)
    provider.enabled = lambda: True

    with patch(
        "app.workflows.write_memory.memory_registry.l3_provider",
        new=AsyncMock(return_value=provider),
    ), patch(
        "app.workflows.write_memory.get_memory_prefs",
        new=AsyncMock(return_value=type("P", (), {"learn": True})()),
    ):
        ok = await write_memory._write_graph_episode(
            user_id="u1", session_id="s1", run_id="r1", iteration=0,
            user_msgs=["hi"], asst_msgs=["hello"],
        )

    assert ok is True
    turn = provider.record_turn.await_args.args[0]
    assert turn.user_id == "u1" and turn.user_msgs == ["hi"]


@pytest.mark.asyncio
async def test_graph_write_skips_prefs_read_when_provider_disabled():
    provider = type("P", (), {"enabled": lambda self: False})()
    prefs_reader = AsyncMock()
    with patch(
        "app.workflows.write_memory.memory_registry.l3_provider",
        new=AsyncMock(return_value=provider),
    ), patch("app.workflows.write_memory.get_memory_prefs", new=prefs_reader):
        ok = await write_memory._write_graph_episode(
            user_id="u1", session_id="s1", run_id="r1", iteration=0,
            user_msgs=["hi"], asst_msgs=["hello"],
        )
    assert ok is False
    prefs_reader.assert_not_awaited()  # gate order: disabled → no settings read
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/memory/test_write_memory_routing.py -q`
Expected: FAIL (`memory_registry` not imported in write_memory yet → AttributeError on the patch target)

- [ ] **Step 3: Write minimal implementation**

At the top of `backend/app/workflows/write_memory.py`, add the import (module-level, so the patch target `write_memory.memory_registry` resolves):

```python
from app.services.ai.memory import registry as memory_registry
from app.services.ai.memory.provider import MemoryTurn
```

Replace the body of `_write_graph_episode` (keep the signature + docstring) with:

```python
    provider = await memory_registry.l3_provider()
    if provider is None or not provider.enabled():
        return False
    prefs = await get_memory_prefs(user_id)
    if not prefs.learn:
        return False
    return await provider.record_turn(
        MemoryTurn(
            user_id=user_id,
            agent_id="",
            session_id=session_id,
            run_id=run_id,
            iteration=iteration,
            user_msgs=user_msgs,
            asst_msgs=asst_msgs,
        )
    )
```

Replace the body of `_write_honcho_turn` (keep the signature + docstring) with:

```python
    provider = await memory_registry.l2_provider()
    if provider is None or not provider.enabled():
        return False
    prefs = await get_memory_prefs(user_id)
    if not prefs.learn:
        return False
    return await provider.record_turn(
        MemoryTurn(
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            run_id=None,
            iteration=0,
            user_msgs=user_msgs,
            asst_msgs=asst_msgs,
        )
    )
```

Remove the now-unused per-function imports of `get_graph_memory_service` / `get_honcho_memory_service` from inside those two functions (they moved into the adapters). Keep `get_memory_prefs` imported (still used). `_build_turn_episode` and `_resolve_team_workspace` STAY in this module (the adapters import them).

> Behavior check: `_write_honcho_turn` previously had no `run_id`/`iteration`; HonchoProvider.record_turn ignores both, so passing `None`/`0` is inert. `agent_id` was unused on the graph path; passing `""` is inert there.

- [ ] **Step 4: Run tests to verify they pass (new + existing)**

Run: `cd backend && uv run pytest tests/memory/test_write_memory_routing.py tests/ -k "write_memory or honcho or graph_memory" -q`
Expected: PASS — the new routing tests pass AND every pre-existing `write_memory` test still passes (proves behavior preserved). If any existing test patched `write_memory.get_graph_memory_service` / `get_honcho_memory_service` directly, update it to patch `write_memory.memory_registry.l3_provider` / `l2_provider` (or the adapter's factory) — note this in the commit.

- [ ] **Step 5: Lint + commit**

```bash
cd backend && uv run black app/workflows/write_memory.py tests/memory/test_write_memory_routing.py && uv run isort app/workflows/write_memory.py tests/memory/test_write_memory_routing.py && uv run flake8 app/workflows/write_memory.py tests/memory/test_write_memory_routing.py
cd .. && git add backend/app/workflows/write_memory.py backend/tests/memory/test_write_memory_routing.py
git commit -m "refactor(memory): route write path through provider registry (Phase 1)"
```

---

### Task 6: Route the L2 read/inject path through the registry

**Files:**
- Modify: `backend/app/services/ai/chat/ai_library_chat_wiring.py` (the inject helper around lines 475-500 that calls `get_honcho_memory_service().get_user_representation`)
- Test: `backend/tests/memory/test_chat_inject_routing.py` (new) — plus existing chat-wiring tests must still pass.

**Interfaces:**
- Consumes: `registry.l2_provider()` (Task 4).
- Produces: the inject helper now acquires the L2 provider via `memory_registry.l2_provider()`, checks `await provider.is_operative()` (mirrors the old `service.config.operative()` gate), keeps the `prefs.inject` gate and the workspace resolution, and calls `await provider.get_context(user_id=user_id, workspace_id=workspace)`. Output identical when the provider is Honcho (default).

- [ ] **Step 1: Read the exact current helper, then write the failing test**

First read `ai_library_chat_wiring.py` lines 460-520 to capture the helper's exact name, signature, and the lines after `get_user_representation` (the "About me" card merge). The test asserts routing without changing the card-merge logic.

```python
# backend/tests/memory/test_chat_inject_routing.py
from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.chat import ai_library_chat_wiring as wiring


@pytest.mark.asyncio
async def test_inject_uses_l2_provider_get_context():
    provider = AsyncMock()
    provider.is_operative = AsyncMock(return_value=True)
    provider.get_context = AsyncMock(return_value="user likes brevity")

    with patch(
        "app.services.ai.chat.ai_library_chat_wiring.memory_registry.l2_provider",
        new=AsyncMock(return_value=provider),
    ), patch(
        "app.services.ai.chat.ai_library_chat_wiring.get_memory_prefs",
        new=AsyncMock(return_value=type("P", (), {"inject": True})()),
    ):
        # Call the inject helper by its real name (fill in after reading the file).
        out = await wiring.<INJECT_HELPER_NAME>(user_id="u1", session_id="s1")

    assert out is not None
    provider.get_context.assert_awaited_once()
```

> The implementer MUST replace `<INJECT_HELPER_NAME>` with the actual helper name found at Step 1 and align the call args with its real signature. This is the only placeholder in the plan and exists because the helper name was not captured at plan time; resolve it by reading lines 460-520 before writing the test.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/memory/test_chat_inject_routing.py -q`
Expected: FAIL (`memory_registry` not imported in the wiring module)

- [ ] **Step 3: Write minimal implementation**

Add a module-level import to `ai_library_chat_wiring.py`:

```python
from app.services.ai.memory import registry as memory_registry
```

In the inject helper, replace:

```python
        service = get_honcho_memory_service()
        if not service.config.operative():
            return None
        prefs = await get_memory_prefs(user_id)
        if not prefs.inject:
            return None
        workspace = None
        if session_id:
            from app.workflows.write_memory import _resolve_team_workspace
            workspace = await _resolve_team_workspace(str(session_id))
        representation = await service.get_user_representation(
            user_id=user_id, workspace_id=workspace
        )
```

with:

```python
        provider = await memory_registry.l2_provider()
        if provider is None or not await provider.is_operative():
            return None
        prefs = await get_memory_prefs(user_id)
        if not prefs.inject:
            return None
        workspace = None
        if session_id:
            from app.workflows.write_memory import _resolve_team_workspace
            workspace = await _resolve_team_workspace(str(session_id))
        representation = await provider.get_context(
            user_id=user_id, workspace_id=workspace
        )
```

Leave everything after `representation = ...` (the "About me" card merge and return) unchanged. Drop the now-unused local `from ...honcho_memory import get_honcho_memory_service` import in this helper if it is no longer referenced elsewhere in the function.

- [ ] **Step 4: Run tests (new + existing chat wiring)**

Run: `cd backend && uv run pytest tests/memory/test_chat_inject_routing.py tests/ -k "chat_wiring or inject or honcho" -q`
Expected: PASS — routing test passes and existing wiring tests still pass (behavior preserved). Update any existing test that patched `ai_library_chat_wiring.get_honcho_memory_service` to patch `ai_library_chat_wiring.memory_registry.l2_provider` instead.

- [ ] **Step 5: Lint + commit**

```bash
cd backend && uv run black app/services/ai/chat/ai_library_chat_wiring.py tests/memory/test_chat_inject_routing.py && uv run isort app/services/ai/chat/ai_library_chat_wiring.py tests/memory/test_chat_inject_routing.py && uv run flake8 app/services/ai/chat/ai_library_chat_wiring.py tests/memory/test_chat_inject_routing.py
cd .. && git add backend/app/services/ai/chat/ai_library_chat_wiring.py backend/tests/memory/test_chat_inject_routing.py
git commit -m "refactor(memory): route L2 inject path through provider registry (Phase 1)"
```

---

### Task 7: Full regression + PR

**Files:** none (verification only).

- [ ] **Step 1: Run the whole memory + chat + workflow test surface**

Run: `cd backend && uv run pytest tests/ -k "memory or honcho or graph or write_memory or chat_wiring or inject" -q`
Expected: PASS (all green). This is the behavior-preservation proof — every pre-existing memory test passes against the routed code with default settings.

- [ ] **Step 2: Lint the full changed set**

Run: `cd backend && uv run black --check app/services/ai/memory app/workflows/write_memory.py app/services/ai/chat/ai_library_chat_wiring.py tests/memory && uv run isort --check-only app/services/ai/memory app/workflows/write_memory.py app/services/ai/chat/ai_library_chat_wiring.py tests/memory && uv run flake8 app/services/ai/memory app/workflows/write_memory.py app/services/ai/chat/ai_library_chat_wiring.py tests/memory`
Expected: clean (no output).

- [ ] **Step 3: Open the refactor PR**

```bash
git push -u origin refactor/memory-provider-interface
gh pr create --base master --head refactor/memory-provider-interface \
  --title "refactor(memory): MemoryProvider interface + registry (Phase 1, behavior-neutral)" \
  --body "Phase 1 of the two-slot pluggable memory architecture (Nous/Hermes pattern). Wraps existing Honcho (L2) + Graphiti (L3) behind a MemoryProvider ABC + settings-driven registry; defaults select today's providers so behavior is unchanged. Phase 2 = migrate Honcho connection config to system_settings + admin slot dropdowns."
```

Per repo convention this refactor PR must merge within 24h (no logic changes ride along).

---

## Self-Review

**Spec coverage:**
- Two-slot model (L2/L3 independent) → Tasks 1 (layer enum), 4 (separate `l2_provider`/`l3_provider`). ✓
- Wrap existing Honcho + Graphiti → Tasks 2, 3. ✓
- Provider selection via system_settings → Task 4 (`memory.l2_provider` / `memory.l3_provider`). ✓
- Behavior-neutral → defaults select current providers (Task 4 test), existing tests still pass (Tasks 5-7), gate order preserved (Task 5 test `test_graph_write_skips_prefs_read_when_provider_disabled`). ✓
- Phase 2/3 explicitly out of scope (no settings migration, no admin UI, no Mem0/Hindsight here). ✓
- DBOS step structure untouched → Task 5 changes only the plain helper internals. ✓

**Placeholder scan:** One intentional, flagged placeholder: `<INJECT_HELPER_NAME>` in Task 6, with explicit instructions to resolve it by reading the file first (the helper name wasn't captured at plan time). All other code is concrete.

**Type consistency:** `MemoryTurn` fields (Task 1) match construction in Task 5 and field reads in Tasks 2/3. `MemoryProvider` methods (`enabled` sync, `is_operative`/`record_turn`/`get_context` async) are used consistently in Tasks 5 (`provider.enabled()` sync, `provider.record_turn(...)` await) and 6 (`await provider.is_operative()`, `await provider.get_context(...)`). Registry returns `Optional[MemoryProvider]`; call sites null-check. ✓
