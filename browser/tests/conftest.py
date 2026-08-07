import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402

TEST_TOKEN = "test-internal-token"

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
    """Strip inherited proxy env vars — see backend/tests/conftest.py for why.

    Short version: a shell with `ALL_PROXY=socks5://...` makes httpx raise at
    construction (`socksio` not installed), so the suite's result depends on
    whose machine runs it. Mirrored here because this service builds httpx
    clients too and would fail the same way.
    """
    saved = {k: os.environ.pop(k) for k in _PROXY_ENV_VARS if k in os.environ}
    try:
        yield
    finally:
        os.environ.update(saved)


@pytest.fixture(autouse=True)
def _clean_settings(monkeypatch):
    """Settings are env-derived and cached; give every test a known baseline."""
    monkeypatch.setenv("BROWSER_INTERNAL_TOKEN", TEST_TOKEN)
    monkeypatch.setenv("DISPLAY", ":99")
    for name in list(os.environ):
        if name.startswith("BROWSER_") and name != "BROWSER_INTERNAL_TOKEN":
            monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
