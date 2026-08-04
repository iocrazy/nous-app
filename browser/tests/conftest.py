import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402

TEST_TOKEN = "test-internal-token"


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
