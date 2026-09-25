"""``/auth/*`` routes after they gained response models (P7): wire parity.

Each route runs over real HTTP through the real router. The GoTrue-backed
service is replaced by a stub returning the dicts
``app/services/infra/supabase_auth_service.py`` builds, key for key, so the
assertion is "the response equals ``jsonable_encoder`` of what the handler
returned" (``tests/api/wire_parity.py``). The GoTrue user / session objects
are ``extra="allow"``; one case adds an unknown key to prove it survives.
"""

from __future__ import annotations

import importlib
import json
from contextlib import asynccontextmanager
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.dialects import postgresql

from app.main import app
from app.models import Tags
from app.repositories.tags_repository import _tag_row
from tests.api.wire_parity import assert_wire_unchanged, sample_orm, sample_row

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000042"
BEARER = {"Authorization": "Bearer caller-jwt"}
USER_METADATA = {"username": "caller", "nested": {"k": [1, 2]}}
GOTRUE_TS = "2026-09-24 01:02:03.456789+00:00"

SIGN_UP = {
    "success": True,
    "user": {"id": USER, "email": "a@example.com", "created_at": GOTRUE_TS},
    "session": {
        "access_token": "at",
        "refresh_token": "rt",
        "expires_at": 1_790_000_000,
    },
}
SIGN_IN = {
    "success": True,
    "user": {"id": USER, "email": "a@example.com", "user_metadata": USER_METADATA},
    "session": {
        "access_token": "at",
        "refresh_token": "rt",
        "expires_at": 1_790_000_000,
        "token_type": "bearer",
    },
}
REFRESH = {
    "success": True,
    "session": {"access_token": "at2", "refresh_token": "rt2", "expires_at": None},
}
ME = {
    "id": USER,
    "email": None,
    "user_metadata": USER_METADATA,
    "app_metadata": {"provider": "email", "providers": ["email"]},
    "created_at": None,
}
UPDATED = {
    "success": True,
    "user": {"id": USER, "email": "b@example.com", "user_metadata": {}},
}


class _Service:
    """Stand-in for ``SupabaseAuthService``; ``result`` is what every call
    returns, ``user`` what ``get_user`` resolves."""

    result: dict[str, Any] = {}
    user: dict[str, Any] | None = ME

    async def sign_up(self, **_kw: Any) -> dict[str, Any]:
        return _Service.result

    sign_in = sign_up
    update_user = sign_up

    async def refresh_session(self, _token: str) -> dict[str, Any]:
        return _Service.result

    reset_password = refresh_session
    sign_out = refresh_session

    async def get_user(self, _token: str) -> dict[str, Any] | None:
        return _Service.user


@pytest.fixture(autouse=True)
def _wiring(monkeypatch):
    _Service.result = {}
    _Service.user = ME
    monkeypatch.setattr("app.api.supabase_auth_router.SupabaseAuthService", _Service)
    media_auth = importlib.import_module("app.api.media_auth")
    monkeypatch.setattr(media_auth, "SupabaseAuthService", _Service)

    async def _quiet(*_a: Any, **_kw: Any) -> None:
        return None

    monkeypatch.setattr("app.api.supabase_auth_router.log_user_action", _quiet)
    monkeypatch.setattr(
        "app.api.supabase_auth_router._create_team_quota_for_new_user", _quiet
    )
    monkeypatch.setattr("app.api.supabase_auth_router.revoke_media_tokens", _quiet)
    monkeypatch.setattr(media_auth, "revoke_media_tokens", _quiet)
    monkeypatch.setattr(media_auth, "_signing_secret", lambda: "secret")


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://test"
    ) as ac:
        yield ac


# --------------------------------------------------------------------------- #
# Password auth
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize("session", [SIGN_UP["session"], None])
async def test_signup_wire(client, session):
    _Service.result = {**SIGN_UP, "session": session}
    resp = await client.post(
        "/api/v1/auth/signup", json={"email": "a@example.com", "password": "pw"}
    )
    assert_wire_unchanged(resp, _Service.result)


@pytest.mark.asyncio
async def test_signin_wire_keeps_unknown_gotrue_keys(client):
    _Service.result = {
        **SIGN_IN,
        "user": {**SIGN_IN["user"], "phone": "+86"},
        "session": {**SIGN_IN["session"], "expires_in": 3600},
    }
    resp = await client.post(
        "/api/v1/auth/signin", json={"email": "a@example.com", "password": "pw"}
    )
    assert_wire_unchanged(resp, _Service.result)


@pytest.mark.asyncio
async def test_refresh_wire(client):
    _Service.result = REFRESH
    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": "rt"})
    assert_wire_unchanged(resp, REFRESH)


@pytest.mark.asyncio
async def test_reset_password_wire(client):
    _Service.result = {"success": True, "message": "密码重置邮件已发送"}
    resp = await client.post(
        "/api/v1/auth/reset-password", json={"email": "a@example.com"}
    )
    assert_wire_unchanged(resp, _Service.result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [{"success": True, "message": "登出成功"}, {"success": False, "message": "boom"}],
)
async def test_signout_wire(client, result):
    _Service.result = result
    resp = await client.post("/api/v1/auth/signout", headers=BEARER)
    assert_wire_unchanged(resp, result)


@pytest.mark.asyncio
async def test_get_me_wire(client):
    resp = await client.get("/api/v1/auth/me", headers=BEARER)
    assert_wire_unchanged(resp, {"success": True, "user": ME})


@pytest.mark.asyncio
async def test_update_me_wire(client):
    _Service.result = UPDATED
    resp = await client.put(
        "/api/v1/auth/me", json={"email": "b@example.com"}, headers=BEARER
    )
    assert_wire_unchanged(resp, UPDATED)


# --------------------------------------------------------------------------- #
# Media auth
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_media_session_wire(client):
    created = await client.post("/api/v1/auth/media-session", headers=BEARER)
    assert_wire_unchanged(created, {"success": True})
    assert "media_session=" in created.headers["set-cookie"]

    cleared = await client.delete("/api/v1/auth/media-session")
    assert_wire_unchanged(cleared, {"success": True})


@pytest.mark.asyncio
async def test_media_token_wire(client, monkeypatch):
    media_auth = importlib.import_module("app.api.media_auth")
    monkeypatch.setattr(media_auth.time, "time", lambda: 1_790_000_000.5)

    resp = await client.post("/api/v1/auth/media-token", headers=BEARER)

    now = 1_790_000_000
    expires_at = now + media_auth.MEDIA_TOKEN_MAX_AGE
    assert_wire_unchanged(
        resp,
        {
            "token": media_auth._sign_token(USER, now, expires_at),
            "expires_at": expires_at,
        },
    )


# --------------------------------------------------------------------------- #
# Temp token
# --------------------------------------------------------------------------- #

TOKEN_DATA = {"user_id": USER, "scopes": ["tags:read", "tags:write"], "selection": []}


class _Redis:
    def __init__(self, data: dict[str, Any], ttl: int = 120) -> None:
        self.data = json.dumps(data)
        self._ttl = ttl
        self.writes: list[str] = []

    async def get(self, _key: str) -> str:
        return self.data

    async def ttl(self, _key: str) -> int:
        return self._ttl

    async def setex(self, _key: str, _ttl: int, value: str) -> None:
        self.writes.append(value)


class _Result:
    def __init__(self, row: Any = None):
        self._row = row

    def first(self):
        return self._row


class _Db:
    results: list[_Result] = []
    statements: list[Any] = []


@pytest.fixture
def temp_token(monkeypatch):
    module = importlib.import_module("app.api.temp_token_router")
    state: dict[str, Any] = {"redis": _Redis(TOKEN_DATA)}

    async def _redis():
        return state["redis"]

    monkeypatch.setattr(module, "get_async_redis", _redis)
    _Db.results = []
    _Db.statements = []

    class _Session:
        async def execute(self, stmt, *a, **kw):
            _Db.statements.append(stmt)
            return _Db.results.pop(0) if _Db.results else _Result()

    @asynccontextmanager
    async def _scope():
        yield _Session()

    monkeypatch.setattr("app.db.session.read_scope", _scope)
    monkeypatch.setattr("app.db.session.write_scope", _scope)

    class _Repo:
        created = _tag_row(sample_orm(Tags))

        async def get_tag_by_name(self, *_a: Any) -> None:
            return None

        async def create_tag(self, **_kw: Any) -> dict[str, Any]:
            return dict(_Repo.created)

    monkeypatch.setattr(module, "get_tags_repository", lambda: _Repo())
    state["repo"] = _Repo
    return state


def _tag_sql() -> list[str]:
    """Statements on ``tags`` / ``tag_groups`` (the request-log middleware
    also writes through the scoped session; that is not the route's)."""
    out = [str(s.compile(dialect=postgresql.dialect())) for s in _Db.statements]
    return [s for s in out if "public.tag" in s]


def _url(tail: str) -> str:
    return f"/api/v1/auth/temp-token/tok/{tail}"


@pytest.mark.asyncio
async def test_selection_wire(client, temp_token):
    temp_token["redis"] = _Redis(
        {**TOKEN_DATA, "selection": ["a"], "options": {"rating": 4, "analyze": True}}
    )
    resp = await client.get(_url("selection"))
    assert_wire_unchanged(
        resp,
        {
            "rating": 4,
            "transcribe": False,
            "summarize": False,
            "analyze": True,
            "tags": ["a"],
        },
    )

    text = await client.get(_url("selection"), params={"format": "text"})
    assert text.status_code == 200
    assert text.text == "a"


@pytest.mark.asyncio
async def test_save_selection_wire(client, temp_token):
    resp = await client.post(_url("selection"), json={"tags": ["a"]})
    assert_wire_unchanged(resp, {"success": True})
    assert len(temp_token["redis"].writes) == 1


@pytest.mark.asyncio
async def test_save_selection_on_a_token_expiring_mid_request_is_refused(
    client, temp_token
):
    """It used to answer ``{"success": true}`` without saving anything."""
    temp_token["redis"] = _Redis(TOKEN_DATA, ttl=-2)

    resp = await client.post(_url("selection"), json={"tags": ["a"]})

    assert resp.status_code == 401, resp.text
    assert temp_token["redis"].writes == []


@pytest.mark.asyncio
async def test_create_tag_wire_with_requested_group(client, temp_token):
    group = str(sample_row(Tags)["group_id"] + 7)
    _Db.results = [_Result((int(group),))]  # the group exists

    resp = await client.post(_url("tags"), json={"name": "Cats", "group_id": group})

    raw = {"success": True, "data": {**temp_token["repo"].created, "group_id": group}}
    assert_wire_unchanged(resp, raw)


@pytest.mark.asyncio
async def test_create_tag_wire_without_any_group(client, temp_token):
    """No group asked for and no "Uncategorized" group: ``group_id`` stays the
    row's own value (a number)."""
    resp = await client.post(_url("tags"), json={"name": "Cats"})

    assert_wire_unchanged(resp, {"success": True, "data": temp_token["repo"].created})


@pytest.mark.asyncio
async def test_create_tag_with_unknown_group_writes_nothing(client, temp_token):
    """It used to create the tag, then 500 on the group foreign key."""
    resp = await client.post(_url("tags"), json={"name": "Cats", "group_id": "123"})

    assert resp.status_code == 404, resp.text
    assert resp.json()["details"]["code"] == "not_found_or_out_of_scope"
    assert len(_tag_sql()) == 1  # the existence probe only
    assert _tag_sql()[0].startswith("SELECT")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [{"name": "Cats", "group_id": "abc"}, {"name": "x" * 51}, {"name": ""}],
)
async def test_create_tag_rejects_bad_input_before_the_database(
    client, temp_token, body
):
    resp = await client.post(_url("tags"), json=body)

    assert resp.status_code == 422, resp.text
    assert _tag_sql() == []
