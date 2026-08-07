"""Shared test fixtures for the MediaHub backend test suite."""

import os

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
