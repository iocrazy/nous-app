"""``/api/v1/shares``: wire parity after the routes gained response models (P6),
and who may do what with a share.

Wire: each route runs over real HTTP with the database session scripted. Rows
come from the ORM mapper (``sample_row``) so every column is present with its
native type, and the body must equal ``jsonable_encoder`` of the dict the
handler builds (``tests/api/wire_parity.py``).

Access, pinned here:

- Owner routes answer a share someone else created exactly like a missing one
  (typed 404 ``not_found_or_out_of_scope``) and never reach the write.
- ``POST /shares`` only shares what the caller may read. It used to accept
  any id — and a share is a public read grant.
- The comment routes demand the share grant on a password-protected share;
  they used to take the bare code, so the password guarded nothing but the
  landing call. GET also served inactive and expired shares.
- ``POST /shares/code/{code}`` hands out that grant (``access_token``).
"""

from __future__ import annotations

import datetime as dt
import sys
import uuid
from contextlib import asynccontextmanager
from typing import Any, List

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.dialects import postgresql

import app.api.media_access_guard as guard_mod
import app.api.share_access as share_access
import app.core.scope_guards as scope_guards
import app.db.scope as scope_mod
import app.db.session as session_mod
from app.core.config import settings
from app.core.deps import AuthContext, get_auth, get_optional_auth
from app.main import app
from app.models import ReviewComments, Shares
from tests.api.wire_parity import SAMPLE_BIGINT, assert_wire_unchanged, sample_row

# ``app.api`` re-exports the router object under the module's name.
r = sys.modules["app.api.shares_router"]

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000042"
OTHER = "00000000-0000-0000-0000-000000000099"
SHARE_ID = SAMPLE_BIGINT + 900
RESOURCE_ID = SAMPLE_BIGINT + 500
CODE = "AbCd1234"
FUTURE = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=3)
PAST = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=3)


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=USER, auth_type="jwt")


class _Result:
    """One scripted ``execute`` result, readable every way the code reads it."""

    def __init__(self, value: Any):
        self._value = value

    def mappings(self):
        return self

    def first(self):
        if isinstance(self._value, list):
            return self._value[0] if self._value else None
        return self._value

    def all(self):
        return list(self._value or [])

    def scalar(self):
        return self._value


class _Db:
    results: List[Any] = []
    statements: List[Any] = []


def _bound(stmt) -> dict:
    return stmt.compile(dialect=postgresql.dialect()).params


def _kinds() -> List[str]:
    return [type(s).__name__ for s in _Db.statements]


@pytest.fixture(autouse=True)
def _wiring(monkeypatch):
    app.dependency_overrides[get_auth] = _fake_auth
    app.dependency_overrides[get_optional_auth] = _fake_auth

    async def _allow() -> None:
        return None

    gate = r.router.dependencies[0].dependency
    app.dependency_overrides[gate] = _allow
    _Db.results = []
    _Db.statements = []

    class _Session:
        async def execute(self, stmt, *args):
            if getattr(getattr(stmt, "table", None), "name", "") == "api_request_logs":
                return _Result(None)  # the request-logging middleware's write
            _Db.statements.append(stmt)
            return _Result(_Db.results.pop(0) if _Db.results else None)

    @asynccontextmanager
    async def _scope():
        yield _Session()

    monkeypatch.setattr(session_mod, "read_scope", _scope)
    monkeypatch.setattr(session_mod, "write_scope", _scope)
    monkeypatch.setattr(scope_mod, "is_enforced", lambda table: False)
    monkeypatch.setattr(settings, "MEDIA_TOKEN_SECRET", "test-media-secret")
    yield
    app.dependency_overrides.pop(get_auth, None)
    app.dependency_overrides.pop(get_optional_auth, None)
    app.dependency_overrides.pop(gate, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _share(**overrides: Any) -> dict:
    row = sample_row(Shares)
    row.update(
        id=SHARE_ID,
        shared_by=uuid.UUID(USER),
        share_code=CODE,
        status="active",
        share_type="review",
        resource_id=RESOURCE_ID,
        expires_at=FUTURE,
        view_count=0,
        max_views=None,
    )
    row.update(overrides)
    return row


def _owner_view(row: dict) -> dict:
    """What the handler builds from a ``shares`` row (unchanged legacy code)."""
    return r._enrich_share(r._row_to_dict(dict(row)))


def _assert_typed_404(resp) -> None:
    assert resp.status_code == 404, resp.text
    assert resp.json()["details"]["code"] == "not_found_or_out_of_scope"


# --------------------------------------------------------------------------- #
# Owner routes — wire
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_create_share_wire(client) -> None:
    row = _share()
    # resource ownership (guard) → code uniqueness → insert
    _Db.results = [(RESOURCE_ID, uuid.UUID(USER)), None, row]
    resp = await client.post(
        "/api/v1/shares",
        json={
            "resource_id": str(RESOURCE_ID),
            "share_type": "review",
            "share_name": "Cut 1",
        },
    )
    assert_wire_unchanged(resp, {"success": True, "data": _owner_view(row)})
    body = resp.json()["data"]
    assert "password" not in body and body["has_password"] is True
    assert _bound(_Db.statements[-1])["resource_id"] == RESOURCE_ID


@pytest.mark.asyncio
async def test_list_shares_wire_and_only_own(client) -> None:
    rows = [_share(), _share(id=SHARE_ID + 1, expires_at=None, password=None)]
    _Db.results = [rows]
    resp = await client.get("/api/v1/shares", params={"team_id": "personal"})
    raw = {"success": True, "data": [_owner_view(x) for x in rows], "count": 2}
    assert_wire_unchanged(resp, raw)
    assert USER in _bound(_Db.statements[0]).values()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "PUT"])
async def test_single_share_read_and_update_routes_are_gone(client, method) -> None:
    """No caller in frontend / admin / browser / scripts / nous-core, and no
    API-key scope maps to them (P6)."""
    kwargs = {"json": {"share_name": "x"}} if method == "PUT" else {}
    resp = await client.request(method, f"/api/v1/shares/{SHARE_ID}", **kwargs)
    assert resp.status_code == 405


@pytest.mark.asyncio
async def test_list_shares_bad_team_id_is_400_not_500(client) -> None:
    resp = await client.get("/api/v1/shares", params={"team_id": "abc"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_toggle_share_wire(client) -> None:
    existing = {"id": SHARE_ID, "shared_by": uuid.UUID(USER), "status": "active"}
    _Db.results = [existing, SHARE_ID]
    resp = await client.delete(f"/api/v1/shares/{SHARE_ID}")
    raw = {"success": True, "message": "Share inactive", "status": "inactive"}
    assert_wire_unchanged(resp, raw)


@pytest.mark.asyncio
async def test_delete_permanent_wire(client) -> None:
    _Db.results = [{"id": SHARE_ID, "shared_by": uuid.UUID(USER)}, None]
    resp = await client.delete(f"/api/v1/shares/{SHARE_ID}/permanent")
    assert_wire_unchanged(resp, {"success": True, "message": "Share deleted"})
    assert _kinds()[-1] == "Delete"


# --------------------------------------------------------------------------- #
# Owner routes — someone else's share
# --------------------------------------------------------------------------- #

FOREIGN_CALLS = [
    ("delete", f"/api/v1/shares/{SHARE_ID}", None),
    ("delete", f"/api/v1/shares/{SHARE_ID}/permanent", None),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("method,url,body", FOREIGN_CALLS)
async def test_foreign_share_is_typed_404_and_never_written(
    client, method, url, body
) -> None:
    _Db.results = [_share(shared_by=uuid.UUID(OTHER))]
    kwargs = {"json": body} if body else {}
    resp = await client.request(method.upper(), url, **kwargs)
    _assert_typed_404(resp)
    assert _kinds() == ["Select"]


@pytest.mark.asyncio
@pytest.mark.parametrize("method,url,body", FOREIGN_CALLS)
async def test_non_numeric_share_id_is_typed_404(client, method, url, body) -> None:
    kwargs = {"json": body} if body else {}
    resp = await client.request(
        method.upper(), url.replace(str(SHARE_ID), "x"), **kwargs
    )
    _assert_typed_404(resp)
    assert _Db.statements == []


# --------------------------------------------------------------------------- #
# POST /shares — what may be shared
# --------------------------------------------------------------------------- #


async def _post_share(client, **target: str):
    return await client.post(
        "/api/v1/shares",
        json={"share_type": "link", "share_name": "S", **target},
    )


@pytest.mark.asyncio
async def test_create_share_of_foreign_resource_is_404(client) -> None:
    # resource exists, created by OTHER; not filed into any team of ours
    _Db.results = [(RESOURCE_ID, uuid.UUID(OTHER)), None]
    resp = await _post_share(client, resource_id=str(RESOURCE_ID))
    _assert_typed_404(resp)
    assert "Insert" not in _kinds()


@pytest.mark.asyncio
async def test_create_share_of_team_filed_resource_is_allowed(client) -> None:
    row = _share(shared_by=uuid.UUID(USER))
    _Db.results = [(RESOURCE_ID, uuid.UUID(OTHER)), (1,), None, row]
    resp = await _post_share(client, resource_id=str(RESOURCE_ID))
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_create_share_of_missing_resource_is_404(client) -> None:
    _Db.results = [None]
    resp = await _post_share(client, resource_id=str(RESOURCE_ID))
    _assert_typed_404(resp)


@pytest.mark.asyncio
async def test_create_share_of_foreign_folder_is_404(client) -> None:
    # folder (scope team 5, created by OTHER) → team membership miss
    _Db.results = [(5, uuid.UUID(OTHER)), None]
    resp = await _post_share(client, folder_id="77")
    _assert_typed_404(resp)
    assert "Insert" not in _kinds()


@pytest.mark.asyncio
async def test_create_share_of_own_folder_is_allowed(client) -> None:
    _Db.results = [(5, uuid.UUID(USER)), None, _share(folder_id=77)]
    resp = await _post_share(client, folder_id="77")
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_create_share_of_unreadable_project_file_is_404(
    client, monkeypatch
) -> None:
    async def _no_access(project_id, user_id):
        return scope_guards.ProjectAccess(can_read=False, can_write=False)

    monkeypatch.setattr(scope_guards, "_resolve_project_access", _no_access)
    _Db.results = [42]  # project_files.project_id
    resp = await _post_share(client, project_file_id="9001")
    _assert_typed_404(resp)


@pytest.mark.asyncio
async def test_create_share_of_readable_project_file_is_allowed(
    client, monkeypatch
) -> None:
    async def _read(project_id, user_id):
        return scope_guards.ProjectAccess(can_read=True, can_write=False)

    monkeypatch.setattr(scope_guards, "_resolve_project_access", _read)
    _Db.results = [42, None, _share(project_file_id=9001)]
    resp = await _post_share(client, project_file_id="9001")
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_create_share_with_version_of_another_file_is_404(
    client, monkeypatch
) -> None:
    async def _read(project_id, user_id):
        return scope_guards.ProjectAccess(can_read=True, can_write=True)

    monkeypatch.setattr(scope_guards, "_resolve_project_access", _read)
    _Db.results = [42, 1234]  # project id; the version belongs to file 1234
    resp = await _post_share(client, project_file_id="9001", version_id="55")
    _assert_typed_404(resp)


@pytest.mark.asyncio
async def test_create_share_for_foreign_team_is_404(client) -> None:
    _Db.results = [(RESOURCE_ID, uuid.UUID(USER)), None]  # own resource; no team
    resp = await _post_share(client, resource_id=str(RESOURCE_ID), team_id="12")
    _assert_typed_404(resp)
    assert "Insert" not in _kinds()


def test_share_target_check_goes_through_the_guard() -> None:
    import inspect

    source = inspect.getsource(r._require_share_targets)
    assert "caller_can_read_resource" in source
    assert hasattr(guard_mod, "caller_can_read_resource")


# --------------------------------------------------------------------------- #
# POST /shares/code/{code} — the visitor
# --------------------------------------------------------------------------- #


def _visitor_raw(row: dict, grant: str, meta: dict | None = None) -> dict:
    share = r._row_to_dict(dict(row))
    data = {
        "id": share["id"],
        "share_type": share["share_type"],
        "share_name": share["share_name"],
        "share_code": share["share_code"],
        "allow_download": share["allow_download"],
        "watermark": share["watermark"],
        "view_count": 1,
        "resource_id": share["resource_id"],
        "project_file_id": share["project_file_id"],
        "folder_id": share["folder_id"],
        "version_id": share["version_id"],
        "created_at": share["created_at"],
        "access_token": grant,
        **(meta or {}),
    }
    return {"success": True, "data": data}


@pytest.fixture
def fixed_grant(monkeypatch):
    monkeypatch.setattr(r, "sign_share_grant", lambda share: "GRANT")
    return "GRANT"


@pytest.mark.asyncio
async def test_access_share_wire_with_resource(client, fixed_grant) -> None:
    app.dependency_overrides[get_optional_auth] = lambda: None
    row = _share(password=None)
    resource = {
        "mime_type": "video/mp4",
        "file_type": "video",
        "filename": "clip.mp4",
        "cover_image_path": "sb://library/c.jpg",
        "thumbnail_path": None,
        "media_id": SAMPLE_BIGINT + 7,
    }
    # share → view_count update → share_views insert → resource meta
    _Db.results = [row, None, None, resource]
    resp = await client.post(f"/api/v1/shares/code/{CODE}", json={})
    meta = {**resource, "media_id": str(SAMPLE_BIGINT + 7)}
    assert_wire_unchanged(resp, _visitor_raw(row, fixed_grant, meta))
    assert "password" not in resp.text and "shared_by" not in resp.json()["data"]


@pytest.mark.asyncio
async def test_access_share_wire_without_resource_keeps_keys_absent(
    client, fixed_grant
) -> None:
    app.dependency_overrides[get_optional_auth] = lambda: None
    row = _share(password=None, resource_id=None, folder_id=77)
    _Db.results = [row, None, None]
    resp = await client.post(f"/api/v1/shares/code/{CODE}", json={})
    assert_wire_unchanged(resp, _visitor_raw(row, fixed_grant))
    assert "mime_type" not in resp.json()["data"]


@pytest.mark.asyncio
async def test_access_share_signed_in_viewer_updates_view_row(
    client, fixed_grant
) -> None:
    row = _share(password=None)
    _Db.results = [row, None, {"id": uuid.uuid4(), "view_count": 2}, None, None]
    resp = await client.post(f"/api/v1/shares/code/{CODE}", json={})
    assert resp.status_code == 200, resp.text
    assert _kinds()[:4] == ["Select", "Update", "Select", "Update"]


@pytest.mark.asyncio
async def test_access_share_password(client) -> None:
    row = _share(password="s3cret")
    _Db.results = [row]
    resp = await client.post(f"/api/v1/shares/code/{CODE}", json={})
    assert resp.status_code == 401 and resp.headers["X-Share-Password-Required"]

    _Db.results = [row]
    resp = await client.post(f"/api/v1/shares/code/{CODE}", json={"password": "nope"})
    assert resp.status_code == 401

    _Db.results = [row, None, None, None, None]
    resp = await client.post(f"/api/v1/shares/code/{CODE}", json={"password": "s3cret"})
    assert resp.status_code == 200, resp.text
    grant = resp.json()["data"]["access_token"]
    assert grant.startswith("sg1.") and CODE not in grant


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({"status": "cancelled"}, "no longer available"),
        ({"status": "inactive"}, "no longer available"),
        ({"expires_at": PAST}, "expired"),
        ({"max_views": 3, "view_count": 3}, "expired"),
    ],
)
async def test_access_closed_share_is_410(client, overrides, expected) -> None:
    _Db.results = [_share(password=None, **overrides), None]
    resp = await client.post(f"/api/v1/shares/code/{CODE}", json={})
    assert resp.status_code == 410
    assert expected in resp.json()["error"]


# --------------------------------------------------------------------------- #
# /shares/code/{code}/comments
# --------------------------------------------------------------------------- #

COMMENT_COLS = [
    "id",
    "content",
    "timecode",
    "frame_number",
    "status",
    "author_id",
    "parent_id",
    "created_at",
]


@pytest.mark.asyncio
async def test_get_comments_wire(client) -> None:
    comments = [
        sample_row(ReviewComments, only=COMMENT_COLS),
        {**sample_row(ReviewComments, only=COMMENT_COLS), "timecode": None},
    ]
    _Db.results = [_share(password=None), comments]
    resp = await client.get(f"/api/v1/shares/code/{CODE}/comments")
    raw = {"success": True, "data": [r._row_to_dict(c) for c in comments]}
    assert_wire_unchanged(resp, raw)


@pytest.mark.asyncio
async def test_post_comment_wire(client) -> None:
    row = sample_row(ReviewComments)
    _Db.results = [_share(password=None), row]
    resp = await client.post(
        f"/api/v1/shares/code/{CODE}/comments", json={"content": "Nice", "timecode": 3}
    )
    assert_wire_unchanged(resp, {"success": True, "data": r._row_to_dict(row)})


def _grant_for(share: dict) -> str:
    return share_access.sign_share_grant(share)


@pytest.fixture
def shares_table(monkeypatch):
    """``share_access.load_share`` answers from this dict (by code and id)."""
    table: dict = {}

    async def _load(*, code=None, share_id=None):
        for s in table.values():
            if (code is not None and s["share_code"] == code) or (
                share_id is not None and s["id"] == share_id
            ):
                return dict(s)
        return None

    monkeypatch.setattr(share_access, "load_share", _load)
    monkeypatch.setattr(r, "load_share", _load)
    return table


def _protected(**overrides: Any) -> dict:
    base = {
        "id": SHARE_ID,
        "share_code": CODE,
        "share_type": "review",
        "resource_id": RESOURCE_ID,
        "status": "active",
        "expires_at": FUTURE,
        "max_views": None,
        "view_count": 0,
        "password": "s3cret",
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_protected_comments_refuse_without_the_grant(
    client, shares_table, method
) -> None:
    shares_table[SHARE_ID] = _protected()
    other = _protected(id=SHARE_ID + 1, share_code="Other123", password=None)
    shares_table[SHARE_ID + 1] = other
    url = f"/api/v1/shares/code/{CODE}/comments"
    body = {"json": {"content": "hi"}} if method == "POST" else {}
    for token in (None, CODE, _grant_for(other), "sg1.1.2.3"):
        params = {"share_token": token} if token else {}
        resp = await client.request(method, url, params=params, **body)
        assert resp.status_code == 401, (token, resp.text)
    assert _Db.statements == []


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_protected_comments_open_with_the_grant(
    client, shares_table, method
) -> None:
    share = _protected()
    shares_table[SHARE_ID] = share
    _Db.results = [[] if method == "GET" else sample_row(ReviewComments)]
    url = f"/api/v1/shares/code/{CODE}/comments"
    body = {"json": {"content": "hi"}} if method == "POST" else {}
    resp = await client.request(
        method, url, params={"share_token": _grant_for(share)}, **body
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_password_change_revokes_old_grants(client, shares_table) -> None:
    share = _protected()
    shares_table[SHARE_ID] = share
    grant = _grant_for(share)
    shares_table[SHARE_ID] = {**share, "password": "changed"}
    resp = await client.get(
        f"/api/v1/shares/code/{CODE}/comments", params={"share_token": grant}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides,status",
    [
        ({"status": "cancelled"}, 410),
        ({"status": "inactive"}, 410),
        ({"status": "expired"}, 410),
        ({"expires_at": PAST}, 410),
        ({"share_type": "link"}, 400),
    ],
)
async def test_comments_on_closed_share(
    client, shares_table, overrides, status
) -> None:
    shares_table[SHARE_ID] = _protected(password=None, **overrides)
    resp = await client.get(f"/api/v1/shares/code/{CODE}/comments")
    assert resp.status_code == status
    assert _Db.statements == []


@pytest.mark.asyncio
async def test_comments_ignore_view_limit(client, shares_table) -> None:
    """The visitor spent the last view on the page itself."""
    shares_table[SHARE_ID] = _protected(password=None, max_views=1, view_count=1)
    _Db.results = [[]]
    resp = await client.get(f"/api/v1/shares/code/{CODE}/comments")
    assert resp.status_code == 200
