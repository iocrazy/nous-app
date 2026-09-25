"""``GET /distribution/accounts/oauth/{platform}/callback``.

The platform redirects the user's browser here after consent. The route is a
redirect, never JSON (P7 declares it ``response_class=RedirectResponse``, 307),
and the OAuth ``state`` is the only thing that ties the callback to the user
who started ``POST /accounts/connect``:

- a missing / unknown / expired state binds nothing;
- a state issued for one platform is not spent on another platform's path
  (added in P7 — the state row records the platform it was issued for).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.distribution_router as dr
import app.services.distribution.module_config as mc
from app.core.config import settings

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000042"


class _FakeAdapter:
    def __init__(self, calls: List[str]) -> None:
        self.calls = calls

    async def exchange_token(self, code: str) -> Dict[str, Any]:
        self.calls.append("exchange")
        return {"access_token": "at", "open_id": "oid", "refresh_token": "rt"}

    async def get_user_info(self, access_token: str, open_id: str) -> Dict[str, Any]:
        return {"username": "creator", "avatar_url": None}


def _client(monkeypatch, state_row: Optional[Dict[str, Any]]):
    calls: List[str] = []

    async def enabled() -> bool:
        return True

    async def pop(state: str):
        calls.append(f"pop:{state}")
        return state_row

    async def creds():
        from app.services.distribution.douyin_adapter import DouyinCredentials

        return DouyinCredentials("ck", "cs", "https://x/cb")

    async def upsert(**fields):
        calls.append(f"upsert:{fields['platform']}:{fields['scope_id']}")
        return {}

    monkeypatch.setattr(mc, "is_module_enabled", enabled)
    monkeypatch.setattr(dr, "_pop_oauth_state", pop)
    monkeypatch.setattr(dr, "get_douyin_credentials", creds)
    # Any platform gets an adapter here, so a missing platform check would
    # reach the token exchange instead of failing on "unsupported platform".
    monkeypatch.setattr(dr, "get_adapter", lambda platform, c: _FakeAdapter(calls))
    monkeypatch.setattr(dr.accounts_repo, "upsert_account", upsert)
    monkeypatch.setattr(settings, "FRONTEND_URL", "https://app.example")
    test_app = FastAPI()
    test_app.include_router(dr.router, prefix="/api/v1")
    return TestClient(test_app, follow_redirects=False), calls


def _state(platform: str = "douyin") -> Dict[str, Any]:
    return {
        "state": "s1",
        "user_id": USER,
        "platform": platform,
        "scope_type": "user",
        "scope_id": USER,
    }


URL = "/api/v1/distribution/accounts/oauth/{}/callback"


def test_callback_binds_with_matching_state(monkeypatch):
    client, calls = _client(monkeypatch, _state("douyin"))
    resp = client.get(URL.format("douyin"), params={"code": "c", "state": "s1"})
    assert resp.status_code == 307
    assert resp.headers["location"] == (
        "https://app.example/distribution/accounts?connected=1"
    )
    assert calls == ["pop:s1", "exchange", f"upsert:douyin:{USER}"]


def test_state_issued_for_another_platform_binds_nothing(monkeypatch):
    client, calls = _client(monkeypatch, _state("douyin"))
    resp = client.get(URL.format("kuaishou"), params={"code": "c", "state": "s1"})
    assert resp.status_code == 307
    assert resp.headers["location"].endswith("?error=oauth_state")
    assert calls == ["pop:s1"]


def test_unknown_state_binds_nothing(monkeypatch):
    client, calls = _client(monkeypatch, None)
    resp = client.get(URL.format("douyin"), params={"code": "c", "state": "nope"})
    assert resp.headers["location"].endswith("?error=oauth_state")
    assert calls == ["pop:nope"]


def test_missing_state_is_never_looked_up(monkeypatch):
    client, calls = _client(monkeypatch, _state())
    resp = client.get(URL.format("douyin"), params={"code": "c"})
    assert resp.headers["location"].endswith("?error=oauth_state")
    assert calls == []


def test_exchange_failure_redirects_with_error(monkeypatch):
    client, calls = _client(monkeypatch, _state())

    async def boom(code):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(_FakeAdapter, "exchange_token", lambda self, code: boom(code))
    resp = client.get(URL.format("douyin"), params={"code": "c", "state": "s1"})
    assert resp.status_code == 307
    assert resp.headers["location"].endswith("?error=oauth_exchange")
