"""Sprint 6 — ContextEngineRegistry."""
from __future__ import annotations

from typing import Any

import pytest

from app.agent_framework.context_engine import (
    ContextEngine,
    ContextEngineRegistry,
    ContextPayload,
    DuplicateContextEngineError,
)


class _StubEngine:
    """Bare-minimum engine satisfying the Protocol — used to test the
    registry without spinning up real composers."""

    def __init__(self, name: str, system: str = "system") -> None:
        self._name = name
        self._system = system

    @property
    def name(self) -> str:
        return self._name

    async def assemble(self, request: dict[str, Any]) -> ContextPayload:
        return ContextPayload(
            system_message=self._system,
            user_messages=[{"role": "user", "content": request.get("q", "")}],
            cache_fingerprint="fp-" + self._name,
            metadata={},
        )


@pytest.mark.unit
def test_register_and_get():
    reg = ContextEngineRegistry()
    chat = _StubEngine("chat")
    reg.register(chat)

    assert reg.get("chat") is chat
    assert reg.get("missing") is None
    assert "chat" in reg
    assert "missing" not in reg
    assert len(reg) == 1


@pytest.mark.unit
def test_register_rejects_duplicates_by_default():
    reg = ContextEngineRegistry()
    reg.register(_StubEngine("chat"))
    with pytest.raises(DuplicateContextEngineError, match="chat"):
        reg.register(_StubEngine("chat"))


@pytest.mark.unit
def test_register_replaces_when_allowed():
    """allow_replace=True is for hot-reload / tests — confirm it works."""
    reg = ContextEngineRegistry(allow_replace=True)
    first = _StubEngine("chat", system="v1")
    second = _StubEngine("chat", system="v2")
    reg.register(first)
    reg.register(second)
    assert reg.get("chat") is second


@pytest.mark.unit
def test_require_raises_for_missing():
    reg = ContextEngineRegistry()
    reg.register(_StubEngine("chat"))
    reg.register(_StubEngine("search"))
    with pytest.raises(KeyError, match="storyboard"):
        reg.require("storyboard")
    err_msg = ""
    try:
        reg.require("storyboard")
    except KeyError as e:
        err_msg = str(e)
    assert "chat" in err_msg
    assert "search" in err_msg


@pytest.mark.unit
def test_require_returns_registered():
    reg = ContextEngineRegistry()
    chat = _StubEngine("chat")
    reg.register(chat)
    assert reg.require("chat") is chat


@pytest.mark.unit
def test_register_rejects_empty_name():
    reg = ContextEngineRegistry()
    with pytest.raises(ValueError, match="non-empty"):
        reg.register(_StubEngine(""))


@pytest.mark.unit
def test_unregister():
    reg = ContextEngineRegistry()
    reg.register(_StubEngine("chat"))
    assert reg.unregister("chat") is True
    assert reg.unregister("chat") is False
    assert "chat" not in reg


@pytest.mark.unit
def test_names_returns_sorted():
    """Sorted output keeps admin UI / docs deterministic."""
    reg = ContextEngineRegistry()
    reg.register(_StubEngine("storyboard"))
    reg.register(_StubEngine("chat"))
    reg.register(_StubEngine("search"))
    assert reg.names() == ["chat", "search", "storyboard"]


@pytest.mark.unit
async def test_assemble_through_registry():
    """End-to-end: register → require → assemble produces payload."""
    reg = ContextEngineRegistry()
    reg.register(_StubEngine("chat", system="hello"))
    engine = reg.require("chat")
    payload = await engine.assemble({"q": "hi"})

    assert isinstance(payload, ContextPayload)
    assert payload.system_message == "hello"
    assert payload.user_messages == [{"role": "user", "content": "hi"}]
    assert payload.cache_fingerprint == "fp-chat"


@pytest.mark.unit
def test_protocol_runtime_check():
    """Confirm the Protocol is runtime-checkable so duck-typed engines
    can be sanity-tested in callers (especially during hot-reload)."""
    e = _StubEngine("x")
    assert isinstance(e, ContextEngine)
