"""``/auth/*`` password routes must not share GoTrue session state between callers.

They used to run on the process-wide anon Supabase client. GoTrue clients keep
the session they last saw: ``POST /auth/signin`` stored the signed-in user's
session (and refresh token, with an auto-refresh timer) on that shared
client, and ``POST /auth/signout`` — which needs no credentials — called
``sign_out()`` on it, revoking (scope ``global``: every device) the sessions
of whoever had signed in last. Now every call gets its own GoTrue client, and
sign-out revokes only the caller's own token.

GoTrue is a scripted ``httpx.MockTransport``; the routes, the service and the
real ``supabase_auth`` client run unchanged.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app

pytestmark = pytest.mark.unit

VICTIM = "11111111-1111-1111-1111-111111111111"
CALLER = "22222222-2222-2222-2222-222222222222"


def _b64(obj: dict[str, Any]) -> str:
    raw = json.dumps(obj).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _jwt(sub: str) -> str:
    """A JWT-shaped token GoTrue's client can decode (it never verifies)."""
    payload = {
        "sub": sub,
        "exp": int(time.time()) + 3600,
        "email": f"{sub}@example.com",
    }
    return f"{_b64({'alg': 'HS256', 'typ': 'JWT'})}.{_b64(payload)}.sig"


def _user(sub: str) -> dict[str, Any]:
    return {
        "id": sub,
        "aud": "authenticated",
        "email": f"{sub}@example.com",
        "app_metadata": {},
        "user_metadata": {},
        "created_at": "2026-09-24T01:02:03.456789Z",
    }


class _GoTrue:
    """Scripted GoTrue: password grant, logout, get / update user."""

    def __init__(self) -> None:
        self.logouts: list[str] = []
        self.user_updates: list[tuple[str, dict[str, Any]]] = []

    @staticmethod
    def _bearer(request: httpx.Request) -> str:
        return request.headers.get("authorization", "").removeprefix("Bearer ")

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/token"):
            body = json.loads(request.content)
            sub = VICTIM if body["email"].startswith("victim") else CALLER
            return httpx.Response(
                200,
                json={
                    "access_token": _jwt(sub),
                    "refresh_token": f"refresh-{sub}",
                    "expires_in": 3600,
                    "expires_at": int(time.time()) + 3600,
                    "token_type": "bearer",
                    "user": _user(sub),
                },
            )
        if path.endswith("/logout"):
            self.logouts.append(self._bearer(request))
            return httpx.Response(204)
        if path.endswith("/user"):
            sub = json.loads(
                base64.urlsafe_b64decode(self._bearer(request).split(".")[1] + "==")
            )["sub"]
            if request.method == "PUT":
                self.user_updates.append(
                    (self._bearer(request), json.loads(request.content))
                )
            return httpx.Response(200, json=_user(sub))
        return httpx.Response(404, json={"msg": f"unscripted {request.method} {path}"})


@pytest.fixture
def gotrue(monkeypatch) -> _GoTrue:
    from app.core.config import settings

    fake = _GoTrue()
    transport = httpx.MockTransport(fake.handle)
    monkeypatch.setattr(settings, "SUPABASE_URL", "http://gotrue.test")
    monkeypatch.setattr(settings, "SUPABASE_ANON_KEY", "anon-key")
    monkeypatch.setattr(
        "app.services.infra.supabase_auth_service._auth_http_client",
        lambda: httpx.AsyncClient(transport=transport),
    )

    async def _claims(token: str) -> dict[str, Any]:
        return json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))

    monkeypatch.setattr("app.core.deps.verify_jwt", _claims)

    async def _no_log(**_kw: Any) -> None:
        return None

    monkeypatch.setattr("app.api.supabase_auth_router.log_user_action", _no_log)
    return fake


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


async def _victim_signs_in(client: AsyncClient) -> str:
    resp = await client.post(
        "/api/v1/auth/signin", json={"email": "victim@example.com", "password": "pw"}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["session"]["access_token"]


@pytest.mark.asyncio
async def test_anonymous_signout_does_not_revoke_the_last_signed_in_user(
    client, gotrue
):
    await _victim_signs_in(client)

    resp = await client.post("/api/v1/auth/signout")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"success": True, "message": "登出成功"}
    assert gotrue.logouts == [], "sign-out without a token revoked someone's session"


@pytest.mark.asyncio
async def test_signout_revokes_only_the_callers_own_token(client, gotrue):
    await _victim_signs_in(client)
    caller_token = _jwt(CALLER)

    resp = await client.post(
        "/api/v1/auth/signout", headers={"Authorization": f"Bearer {caller_token}"}
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True
    assert gotrue.logouts == [caller_token]


@pytest.mark.asyncio
async def test_update_me_acts_on_the_callers_token_after_another_signin(client, gotrue):
    await _victim_signs_in(client)
    caller_token = _jwt(CALLER)

    resp = await client.put(
        "/api/v1/auth/me",
        json={"email": "new@example.com"},
        headers={"Authorization": f"Bearer {caller_token}"},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["user"]["id"] == CALLER
    assert gotrue.user_updates == [(caller_token, {"email": "new@example.com"})]
