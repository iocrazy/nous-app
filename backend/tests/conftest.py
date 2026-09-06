"""Shared test fixtures for the MediaHub backend test suite."""

import os
from contextlib import asynccontextmanager as _asynccontextmanager
from unittest.mock import MagicMock as _MagicMock

import pytest

_PROXY_ENV_VARS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)


@pytest.fixture(scope="session", autouse=True)
def _isolate_proxy_env():
    """Strip inherited proxy env vars for the whole test session.

    Unit tests must not depend on the developer's network. They did: with a
    mihomo-style shell environment (`ALL_PROXY=socks5://...`), httpx picks the
    proxy up from the environment and dies at construction time with
    `ImportError: Using SOCKS proxy, but the 'socksio' package is not
    installed` — 87 failures across 19 files, none of them a real defect. On CI
    no proxy is set, so the same suite passed there. A suite whose result
    depends on whose machine it runs on is not a gate.

    This is environment isolation, not "unset the proxy to make it work" — the
    standing rule is that real proxy problems get solved at the mihomo layer,
    and that still holds for anything talking to an actual service. A unit test
    reaching the network at all is the bug.

    Session-scoped and autouse so it lands before any client is built. Tests
    that deliberately exercise proxy behaviour still work: they set the vars
    themselves via monkeypatch, which applies after this fixture and is undone
    per-test.
    """
    saved = {k: os.environ.pop(k) for k in _PROXY_ENV_VARS if k in os.environ}
    try:
        yield
    finally:
        os.environ.update(saved)


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_caller_scope: use the real app.db.session.caller_scope (needs a "
        "SUPAVISOR_DATABASE_URL); the default autouse fixture stubs it inert for "
        "the engine-less unit suite.",
    )


@pytest.fixture(autouse=True)
def _inert_caller_scope(request, monkeypatch):
    """RLS 第三层 PR-2b: the screenwriting handlers wrap their tenant scene/shot
    reads+writes in ``caller_scope(user_id)``, which opens a REAL authenticated
    DB session (``SET LOCAL ROLE authenticated`` + injected
    ``request.jwt.claims``). Its privilege-drop mechanism is unit-tested in
    ``test_caller_scope.py`` and its RLS enforcement against nous-db in
    ``test_screenwriting_tools_rls.py``. The rest of the (engine-less) suite
    only exercises handler logic, so stub the binding the handlers call to an
    inert async context manager — otherwise every handler test that reaches the
    gateway would raise ``RuntimeError: SUPAVISOR_DATABASE_URL is not
    configured``. Tests that need the real thing (or their own recording spy)
    opt out with ``@pytest.mark.real_caller_scope`` or override the attribute
    themselves inside the test."""
    if request.node.get_closest_marker("real_caller_scope"):
        return
    try:
        import app.services.ai.tools.screenwriting_tools as _tools
    except Exception:  # pragma: no cover — module import is a hard dep in practice
        return

    @_asynccontextmanager
    async def _inert(user_id):
        yield _MagicMock(name="authenticated_session")

    monkeypatch.setattr(_tools, "caller_scope", _inert, raising=False)


@pytest.fixture
def sample_project_data():
    """Sample project creation data for tests."""
    return {
        "name": "Test Project",
        "description": "A test project",
        "project_type": "internal",
    }


@pytest.fixture
def sample_script_data():
    """Sample script project creation data."""
    return {
        "name": "Test Script",
        "description": "A test script",
    }


@pytest.fixture(autouse=True)
def _codex_provider_card_on(monkeypatch):
    """Default: the user's Codex provider card is ON.

    Since 2026-09-06 the Providers page is the one management entry — the
    canvas daemon branch and the picker refuse Codex when the card is off
    (``services/codex/provider_card.card_enabled``). Without this default every
    daemon-branch test would hit a real settings read, fail it, and be refused
    for a reason it is not about. The gate's own tests set the value they need
    on top of this (a test-level monkeypatch wins over an autouse fixture).
    """
    from unittest.mock import AsyncMock

    from app.services.codex import provider_card

    monkeypatch.setattr(provider_card, "card_enabled", AsyncMock(return_value=True))
