"""User settings routes: wire parity after they gained response models (P5).

Each route runs over real HTTP through the real router and repository code;
only the database session is scripted. Reads hand back ``UserCookies`` ORM
objects with every column set (``sample_orm``), so the dict the handler
builds is the one production builds, and the body must equal what FastAPI
sent for that dict with no model (``tests/api/wire_parity.py``).

Also pinned here (fixed in P5):

- Saving custom headers no longer goes through the cookie ``upsert``, which
  forced ``is_valid=True`` / ``error_message=NULL`` / a fresh ``updated_at``:
  a headers save used to clear an expired cookie's "invalid" mark.
- A headers-only row is not a cookie: the cookie list and
  ``YtdlpService.user_has_cookie`` used to report it as a valid cookie.
- Deleting a cookie keeps the platform's custom headers (the whole row used to
  be deleted).
- A failed headers save is a 500, not ``{"success": true}``.
- Every statement is bound to the caller's own user id.
- ``DELETE /settings`` had no caller and is gone.
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from typing import Any, List

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.dialects import postgresql

from app.core.deps import AuthContext, get_auth
from app.main import app
from app.models import UserCookies
from app.schemas.user_settings_responses import (
    SettingsPlatformHeaders,
    SettingsPlatformWriteResult,
)
from tests.api.wire_parity import assert_wire_unchanged, sample_orm

r = sys.modules["app.api.user_settings_router"]
repo_mod = sys.modules["app.repositories.cookies_repository"]

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000042"
OTHER_USER = "00000000-0000-0000-0000-000000000099"


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=USER, auth_type="jwt")


class _Result:
    def __init__(self, objs: List[Any]):
        self._objs = objs

    def scalars(self):
        objs = self._objs

        class _S:
            def all(self):
                return list(objs)

            def first(self):
                return objs[0] if objs else None

        return _S()


class _Db:
    """Scripted session: each ``execute`` records the statement and pops the
    next result (an empty result when the script runs out)."""

    results: List[_Result] = []
    statements: List[Any] = []


def _compiled(stmt: Any) -> tuple[str, dict]:
    c = stmt.compile(dialect=postgresql.dialect())
    return str(c), dict(c.params)


@pytest.fixture(autouse=True)
def _wiring(monkeypatch):
    app.dependency_overrides[get_auth] = _fake_auth
    _Db.results = []
    _Db.statements = []

    class _Session:
        async def execute(self, stmt):
            _Db.statements.append(stmt)
            return _Db.results.pop(0) if _Db.results else _Result([])

    @asynccontextmanager
    async def _scope():
        yield _Session()

    monkeypatch.setattr(repo_mod, "read_scope", _scope)
    monkeypatch.setattr(repo_mod, "write_scope", _scope)
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _cookie_row(**overrides: Any) -> UserCookies:
    return sample_orm(UserCookies, user_id=USER, platform="douyin", **overrides)


def _assert_all_bound_to_caller() -> None:
    assert _Db.statements, "no statement ran"
    for stmt in _Db.statements:
        _, params = _compiled(stmt)
        users = [v for k, v in params.items() if k.startswith("user_id")]
        assert users and all(str(u) == USER for u in users), params


# --------------------------------------------------------------------------- #
# Model pins
# --------------------------------------------------------------------------- #


def test_models_declare_exactly_the_keys_the_handlers_build() -> None:
    assert set(SettingsPlatformWriteResult.model_fields) == {"success", "platform"}
    assert set(SettingsPlatformHeaders.model_fields) == {"platform", "headers_text"}


def test_delete_settings_route_is_gone() -> None:
    ops = {
        (method, route.path)
        for route in app.routes
        for method in getattr(route, "methods", ()) or ()
    }
    assert ("DELETE", "/api/v1/settings") not in ops
    assert ("GET", "/api/v1/settings") in ops


# --------------------------------------------------------------------------- #
# Cookies
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_put_cookie_wire_unchanged(client) -> None:
    _Db.results = [_Result([_cookie_row()])]
    resp = await client.put(
        "/api/v1/settings/cookies/douyin", json={"cookie_text": "sid=SECRET"}
    )
    assert_wire_unchanged(resp, {"success": True, "platform": "douyin"})
    # The response never echoes the cookie.
    assert "SECRET" not in resp.text
    _assert_all_bound_to_caller()


@pytest.mark.asyncio
async def test_put_cookie_repo_failure_is_500(client, monkeypatch) -> None:
    async def _fail(*a, **kw):
        return None

    monkeypatch.setattr(repo_mod.CookiesRepository, "upsert", _fail)
    resp = await client.put(
        "/api/v1/settings/cookies/douyin", json={"cookie_text": "x"}
    )
    assert resp.status_code == 500


@pytest.mark.asyncio
async def test_delete_cookie_wire_unchanged_and_keeps_headers(client) -> None:
    resp = await client.delete("/api/v1/settings/cookies/douyin")
    assert_wire_unchanged(resp, {"success": True, "platform": "douyin"})
    _assert_all_bound_to_caller()

    (update_sql, update_params), (delete_sql, _) = map(_compiled, _Db.statements)
    # First the cookie columns are nulled …
    assert update_sql.startswith("UPDATE public.user_cookies SET")
    assert "cookie_text" in update_sql and "cookie_file" in update_sql
    assert "custom_headers" not in update_sql.split("WHERE")[0]
    # … then the row goes only if it carries no headers.
    assert delete_sql.startswith("DELETE FROM public.user_cookies")
    assert "custom_headers IS NULL" in delete_sql


@pytest.mark.asyncio
async def test_unsupported_platform_is_400(client) -> None:
    for method, path in [
        ("put", "/api/v1/settings/cookies/myspace"),
        ("delete", "/api/v1/settings/cookies/myspace"),
        ("get", "/api/v1/settings/headers/myspace"),
        ("put", "/api/v1/settings/headers/myspace"),
    ]:
        kwargs = {"json": {"cookie_text": "x", "headers_text": "x"}}
        resp = await getattr(client, method)(
            path, **(kwargs if method == "put" else {})
        )
        assert resp.status_code == 400, (method, path)
    assert _Db.statements == []


@pytest.mark.asyncio
async def test_cookie_list_does_not_count_a_headers_only_row(client) -> None:
    headers_only = _cookie_row(cookie_text=None, cookie_file=None)
    bili = sample_orm(UserCookies, user_id=USER, platform="bilibili")
    _Db.results = [_Result([headers_only, bili])]
    resp = await client.get("/api/v1/settings/cookies")
    assert resp.status_code == 200
    by_platform = {c["platform"]: c for c in resp.json()["cookies"]}
    assert by_platform["douyin"] == {
        "platform": "douyin",
        "has_cookie": False,
        "is_valid": False,
        "error_message": None,
        "updated_at": None,
    }
    assert by_platform["bilibili"]["has_cookie"] is True
    # Content never leaves the server.
    assert "cookie_text" not in resp.text


@pytest.mark.asyncio
async def test_user_has_cookie_ignores_a_headers_only_row() -> None:
    from app.services.media.parsers.ytdlp_service import YtdlpService

    _Db.results = [_Result([_cookie_row(cookie_text=None, cookie_file=None)])]
    assert await YtdlpService.user_has_cookie(USER, "douyin") is False
    _Db.results = [_Result([_cookie_row(cookie_file=None)])]
    assert await YtdlpService.user_has_cookie(USER, "douyin") is True
    _Db.results = [_Result([])]
    assert await YtdlpService.user_has_cookie(USER, "douyin") is False


# --------------------------------------------------------------------------- #
# Headers
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stored, expected",
    [
        ("Referer: https://www.douyin.com/", "Referer: https://www.douyin.com/"),
        # A saved cookie whose headers were never set: the column is NULL.
        (None, None),
    ],
)
async def test_get_headers_wire_unchanged(client, stored, expected) -> None:
    obj = _cookie_row(custom_headers=stored)
    _Db.results = [_Result([obj])]
    resp = await client.get("/api/v1/settings/headers/douyin")
    raw_row = repo_mod._row(obj)
    assert_wire_unchanged(
        resp,
        {"platform": "douyin", "headers_text": raw_row.get("custom_headers", "")},
    )
    assert resp.json()["headers_text"] == expected
    _assert_all_bound_to_caller()


@pytest.mark.asyncio
async def test_get_headers_without_row_is_empty_string(client) -> None:
    resp = await client.get("/api/v1/settings/headers/douyin")
    assert_wire_unchanged(resp, {"platform": "douyin", "headers_text": ""})
    _assert_all_bound_to_caller()


@pytest.mark.asyncio
async def test_put_headers_wire_unchanged_and_touches_only_headers(client) -> None:
    _Db.results = [_Result([_cookie_row(custom_headers="X-A: 1")])]
    resp = await client.put(
        "/api/v1/settings/headers/douyin", json={"headers_text": "X-A: 1"}
    )
    assert_wire_unchanged(resp, {"success": True, "platform": "douyin"})
    _assert_all_bound_to_caller()

    (stmt,) = _Db.statements
    sql, params = _compiled(stmt)
    assert sql.startswith("INSERT INTO public.user_cookies")
    conflict_set = sql.split("DO UPDATE SET", 1)[1].split("RETURNING")[0]
    assert "custom_headers" in conflict_set
    for untouched in ("is_valid", "error_message", "updated_at", "cookie_text"):
        assert untouched not in conflict_set, untouched
        assert untouched not in params, untouched


@pytest.mark.asyncio
async def test_put_headers_repo_failure_is_500(client, monkeypatch) -> None:
    class _Boom:
        async def execute(self, stmt):
            raise RuntimeError("db down")

    @asynccontextmanager
    async def _scope():
        yield _Boom()

    monkeypatch.setattr(repo_mod, "write_scope", _scope)
    resp = await client.put(
        "/api/v1/settings/headers/douyin", json={"headers_text": "X-A: 1"}
    )
    assert resp.status_code == 500


@pytest.mark.asyncio
async def test_other_users_rows_are_never_addressed(client) -> None:
    """Nothing in the request can name another user: every statement of every
    route binds the authenticated id, and OTHER_USER never appears."""
    _Db.results = [_Result([_cookie_row()])]
    await client.put(
        f"/api/v1/settings/cookies/douyin?user_id={OTHER_USER}",
        json={"cookie_text": "x", "user_id": OTHER_USER},
    )
    await client.put(
        f"/api/v1/settings/headers/douyin?user_id={OTHER_USER}",
        json={"headers_text": "x", "user_id": OTHER_USER},
    )
    await client.get(f"/api/v1/settings/headers/douyin?user_id={OTHER_USER}")
    await client.delete(f"/api/v1/settings/cookies/douyin?user_id={OTHER_USER}")
    _assert_all_bound_to_caller()
    for stmt in _Db.statements:
        assert OTHER_USER not in str(_compiled(stmt)[1])


# --------------------------------------------------------------------------- #
# GET /settings
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_get_settings_with_null_download_path_is_the_default(
    client, monkeypatch
) -> None:
    """``user_settings.download_path`` is nullable. A NULL used to reach
    ``download_path: str`` and turn the whole GET into a 500."""
    row = {
        "id": "11111111-1111-1111-1111-111111111111",
        "user_id": USER,
        "download_path": None,
        "settings_json": {"maxConcurrentDownloads": 3},
        "created_at": "2026-09-24T01:02:03.456789+00:00",
        "updated_at": "2026-09-24T01:02:04.456789+00:00",
    }

    async def _get(self, user_id):
        assert user_id == USER
        return row

    monkeypatch.setattr(r.UserSettingsRepository, "get_by_user_id", _get)
    resp = await client.get("/api/v1/settings")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {**row, "download_path": r.DEFAULT_DOWNLOAD_PATH}
