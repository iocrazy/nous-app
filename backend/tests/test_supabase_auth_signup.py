"""SupabaseAuthService.sign_up — gotrue payload shape (prod bug 2026-07-04).

``{"options": None}`` crashes inside gotrue-py: the client chains ``.get()``
off the value of the "options" key, and ``None.get`` raises AttributeError.
Every signup WITHOUT a username hit that path and 500'd with the opaque
"'NoneType' object has no attribute 'get'". The key must be OMITTED when
there is no metadata, not sent as None. Found by the P3 prod probe.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.infra.supabase_auth_service import SupabaseAuthService


def _fake_client(captured: dict):
    client = MagicMock()

    async def fake_sign_up(credentials):
        captured["credentials"] = credentials
        # Mirror gotrue-py's crash on {"options": None} so a regression
        # fails this test the same way it failed prod.
        options = credentials.get("options", {})
        options.get("data")  # AttributeError when options is None
        resp = MagicMock()
        resp.user.id = "u-1"
        resp.user.email = credentials["email"]
        resp.user.created_at = None
        resp.session = None
        return resp

    client.auth.sign_up = AsyncMock(side_effect=fake_sign_up)
    return client


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sign_up_without_metadata_omits_options_key(monkeypatch):
    captured: dict = {}
    svc = SupabaseAuthService()
    monkeypatch.setattr(
        svc, "_get_client", AsyncMock(return_value=_fake_client(captured))
    )

    result = await svc.sign_up(email="a@example.com", password="secret123")

    assert result["success"] is True, result
    assert "options" not in captured["credentials"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sign_up_with_metadata_sends_options_data(monkeypatch):
    captured: dict = {}
    svc = SupabaseAuthService()
    monkeypatch.setattr(
        svc, "_get_client", AsyncMock(return_value=_fake_client(captured))
    )

    result = await svc.sign_up(
        email="b@example.com", password="secret123", metadata={"username": "bee"}
    )

    assert result["success"] is True, result
    assert captured["credentials"]["options"] == {"data": {"username": "bee"}}
