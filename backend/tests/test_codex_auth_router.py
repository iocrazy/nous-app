"""Tests for the admin codex auth router — provider health is stubbed.

Unlike jimeng there is no device-flow login here: the Codex OAuth session is
created on the HOST (`codex login`, browser flow) and bind-mounted in — see
docs/runbook/codex-image.md. The panel is a status probe only.
"""

from __future__ import annotations

import sys

import pytest

import app.api.admin  # noqa: F401 — ensure the package __init__ has run
from app.core.deps import AuthContext

m = sys.modules["app.api.admin.codex_auth_router"]

pytestmark = [pytest.mark.asyncio]


def _auth() -> AuthContext:
    return AuthContext(user_id="11111111-1111-1111-1111-111111111111", auth_type="jwt")


async def test_status_logged_in(monkeypatch):
    async def fake_health(self):
        return {"ok": True}

    monkeypatch.setattr(m.CodexCliProvider, "health", fake_health)
    assert await m.codex_status(_auth()) == {"logged_in": True}


async def test_status_not_logged_in_carries_error(monkeypatch):
    async def fake_health(self):
        return {"ok": False, "error": "not_logged_in"}

    monkeypatch.setattr(m.CodexCliProvider, "health", fake_health)
    assert await m.codex_status(_auth()) == {
        "logged_in": False,
        "error": "not_logged_in",
    }


async def test_status_missing_binary(monkeypatch):
    async def fake_health(self):
        return {"ok": False, "error": "cli_missing"}

    monkeypatch.setattr(m.CodexCliProvider, "health", fake_health)
    result = await m.codex_status(_auth())
    assert result["logged_in"] is False
    assert result["error"] == "cli_missing"
