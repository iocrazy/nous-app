"""Authz wiring + repo behavior tests for the beat memos router / repository (M5).

Mirrors test_script_beats.py:

1. **Guard matrix**: every mutating memo route MUST declare a known access guard
   as a dependency (verify_script_access / verify_memo_access) — a future
   endpoint added without one fails here instead of shipping an IDOR. The image
   serve route is intentionally excluded: it authenticates the media-token / JWT
   dual channel itself (browser <img> loads cannot send an Authorization header)
   and bounds every read to a memo the caller's script owns.

2. **verify_memo_access chain**: memo → script → team, so a foreign caller gets
   403 and a missing memo 404 (before the 403).

3. **HTTP shape + 422 boundary**: create/patch payloads (anchor_sec range +
   string-number coercion, images > 4, bad image path) resolve at the schema
   boundary, never as an asyncpg 22003 → opaque 500.

4. **Repository behavior** (capture-emitted-SQL, no DSN): create binds script_id
   bigint + anchor_sec int; update whitelists + string-coerces images.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Dict
from unittest.mock import patch
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.dialects import postgresql

import app.repositories.beat_memo_repository as memo_mod
from app.api.beat_memos_router import router as memos_router
from app.core.deps import AuthContext, get_auth
from app.core.scope_guards import verify_memo_access, verify_script_access
from app.main import app
from app.models.scripts import BeatMemos
from app.repositories.beat_memo_repository import BeatMemoRepository

pytestmark = pytest.mark.unit

_SCRIPT_ID = 900000000000000001
_MEMO_ID = 900000000000000002


# --------------------------------------------------------------------------- #
# 1. Structural: every mutating route declares a guard
# --------------------------------------------------------------------------- #

KNOWN_GUARDS = {verify_script_access, verify_memo_access}
FAKE_USER_ID = str(uuid4())


def _flat_dependency_calls(dependant):
    for dep in dependant.dependencies:
        yield dep.call
        yield from _flat_dependency_calls(dep)


# The image serve route (`.../images/{idx}`) authenticates the dual channel
# itself — excluded from the declarative guard matrix (same pattern as
# inspiration_router.get_attachment).
_GUARDED_ROUTES = [
    r
    for r in list(memos_router.routes)
    if hasattr(r, "dependant") and "/images/" not in r.path
]


@pytest.mark.parametrize(
    "route",
    _GUARDED_ROUTES,
    ids=lambda r: f"{','.join(sorted(r.methods))} {r.path}",
)
def test_memo_route_declares_guard(route):
    calls = set(_flat_dependency_calls(route.dependant))
    assert (
        calls & KNOWN_GUARDS
    ), f"{sorted(route.methods)} {route.path} declares no access guard"


# --------------------------------------------------------------------------- #
# 2. Behavior: verify_memo_access / verify_script_access resolution chain
# --------------------------------------------------------------------------- #


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=FAKE_USER_ID, auth_type="jwt")


@pytest.fixture(autouse=True)
def _override_auth():
    app.dependency_overrides[get_auth] = _fake_auth
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def _foreign_team(monkeypatch):
    """Wire the guard chain so the caller's team never matches the memo's:
    memo → script_id → OWNER_TEAM, while the caller is a non-member."""
    import app.core.scope_guards as guards
    from app.repositories.script_repository import ScriptProjectRepository

    async def fake_memo_get(self, memo_id):
        return {"id": memo_id, "script_id": "9001"}

    async def fake_project_get(self, script_id):
        return {"id": script_id, "team_id": "OWNER_TEAM"}

    async def fake_member(team_id, user_id):
        return False

    monkeypatch.setattr(BeatMemoRepository, "get_by_id", fake_memo_get)
    monkeypatch.setattr(ScriptProjectRepository, "get_by_id", fake_project_get)
    monkeypatch.setattr(guards, "_is_team_member", fake_member)


@pytest.mark.asyncio
async def test_memo_patch_403_foreign_team(client, _foreign_team):
    resp = await client.patch("/api/v1/memos/123", json={"content": "X"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_memo_delete_403_foreign_team(client, _foreign_team):
    resp = await client.delete("/api/v1/memos/123")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_script_memos_list_403_foreign_team(client, _foreign_team):
    # The script-scoped route resolves via verify_script_access → same team gate.
    resp = await client.get("/api/v1/scripts/9001/memos")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_memo_missing_returns_404(client, monkeypatch):
    """A missing memo 404s before any team check (guard row-missing ordering)."""

    async def fake_memo_get(self, memo_id):
        return None

    monkeypatch.setattr(BeatMemoRepository, "get_by_id", fake_memo_get)
    resp = await client.delete("/api/v1/memos/123")
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# 3. HTTP shape + 422 boundary
# --------------------------------------------------------------------------- #


@pytest.fixture
def _same_team(monkeypatch):
    """Guard chain that always admits the caller (member of the memo's team)."""
    import app.core.scope_guards as guards
    from app.repositories.script_repository import ScriptProjectRepository

    async def fake_memo_get(self, memo_id):
        return {"id": memo_id, "script_id": "9001"}

    async def fake_project_get(self, script_id):
        return {"id": script_id, "team_id": "TEAM_1"}

    async def fake_member(team_id, user_id):
        return True

    monkeypatch.setattr(BeatMemoRepository, "get_by_id", fake_memo_get)
    monkeypatch.setattr(ScriptProjectRepository, "get_by_id", fake_project_get)
    monkeypatch.setattr(guards, "_is_team_member", fake_member)


def _memo_row(**over) -> Dict[str, Any]:
    row = {
        "id": _MEMO_ID,
        "script_id": _SCRIPT_ID,
        "anchor_sec": 30,
        "content": "a captured idea",
        "images": [],
        "created_at": "2026-07-19T00:00:00+00:00",
        "updated_at": "2026-07-19T00:00:00+00:00",
    }
    row.update(over)
    return row


@pytest.mark.asyncio
async def test_create_returns_memo_shape(client, _same_team, monkeypatch):
    """POST create returns the MemoOut envelope: bigint ids serialize to strings,
    anchor_sec stays an int, images stays a list."""
    captured: Dict[str, Any] = {}

    async def fake_create(self, data):
        captured.update(data)
        return _memo_row(anchor_sec=data["anchor_sec"], content=data["content"])

    monkeypatch.setattr(BeatMemoRepository, "create", fake_create)

    resp = await client.post(
        "/api/v1/scripts/9001/memos",
        json={"anchor_sec": 45, "content": "hi", "images": []},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["id"] == str(_MEMO_ID)  # bigint → string
    assert data["script_id"] == str(_SCRIPT_ID)
    assert data["anchor_sec"] == 45  # INTEGER stays int
    assert data["images"] == []
    # script_id is injected from the path, never the body.
    assert captured["script_id"] == "9001"


@pytest.mark.asyncio
async def test_create_accepts_string_number_anchor_sec(client, _same_team, monkeypatch):
    """anchor_sec sent as a numeric STRING ("30") is coerced to int at the schema
    boundary (pydantic lax) — never bound as a str to the INTEGER column."""
    captured: Dict[str, Any] = {}

    async def fake_create(self, data):
        captured.update(data)
        return _memo_row(anchor_sec=data["anchor_sec"])

    monkeypatch.setattr(BeatMemoRepository, "create", fake_create)

    resp = await client.post(
        "/api/v1/scripts/9001/memos", json={"anchor_sec": "30", "content": ""}
    )
    assert resp.status_code == 200
    assert captured["anchor_sec"] == 30
    assert isinstance(captured["anchor_sec"], int)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"content": "no anchor"},  # anchor_sec is required on create
        {"anchor_sec": -1},  # below 0
        {"anchor_sec": 2_147_483_648},  # PG INTEGER + 1 → 22003 if forwarded
        {"anchor_sec": 10, "images": ["a", "b", "c", "d", "e"]},  # > 4 images
        {"anchor_sec": 10, "images": ["../secret/x"]},  # path traversal
        {"anchor_sec": 10, "images": ["other-bucket/x"]},  # wrong prefix
    ],
)
async def test_create_rejects_invalid_payloads(client, _same_team, payload):
    """Missing/out-of-range anchor_sec, too many images, and unsafe image paths
    all 422 at the schema boundary, never reaching the repo."""
    resp = await client.post("/api/v1/scripts/9001/memos", json=payload)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_patch_content_only_leaves_images_untouched(
    client, _same_team, monkeypatch
):
    """A content-only PATCH must NOT forward images (exclude_unset) — forwarding
    an absent field would wipe the memo's images."""
    captured: Dict[str, Any] = {}

    async def fake_update(self, memo_id, data):
        captured.update(data)
        return _memo_row(content=data.get("content", "x"))

    monkeypatch.setattr(BeatMemoRepository, "update", fake_update)

    resp = await client.patch("/api/v1/memos/123", json={"content": "edited"})
    assert resp.status_code == 200
    assert captured == {"content": "edited"}


@pytest.mark.asyncio
async def test_patch_rejects_too_many_images(client, _same_team):
    resp = await client.patch(
        "/api/v1/memos/123", json={"images": ["beats/memos/a"] * 5}
    )
    assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# 4. Repository behavior (capture-emitted-SQL, no DSN)
# --------------------------------------------------------------------------- #


class _FakeResult:
    def __init__(self, *, scalar_first=None, all_rows=None):
        self._scalar_first = scalar_first
        self._all_rows = all_rows or []

    def scalars(self):
        return self

    def first(self):
        return self._scalar_first

    def all(self):
        return self._all_rows


class _CaptureSession:
    def __init__(self, results):
        self.statements: list = []
        self._results = list(results)

    async def execute(self, stmt):
        self.statements.append(stmt)
        return self._results.pop(0)


class _ScopeCtx:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


def _memo_obj(anchor_sec=30, content="idea", images=None):
    return BeatMemos(
        id=_MEMO_ID,
        script_id=_SCRIPT_ID,
        anchor_sec=anchor_sec,
        content=content,
        images=images if images is not None else [],
        created_at=_dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc),
        updated_at=_dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc),
    )


def _rendered(stmt) -> tuple[str, dict]:
    compiled = stmt.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


@pytest.mark.asyncio
async def test_create_binds_script_bigint_and_anchor_int():
    """create() bigint-coerces script_id and int-coerces anchor_sec (asyncpg
    strict); images string-coerce; read-back keeps ids native + list intact."""
    session = _CaptureSession([_FakeResult(scalar_first=_memo_obj())])
    with patch.object(memo_mod, "write_scope", lambda: _ScopeCtx(session)):
        out = await BeatMemoRepository().create(
            {
                "script_id": str(_SCRIPT_ID),
                "anchor_sec": "30",  # str at the boundary → int
                "content": "idea",
                "images": ["beats/memos/a", 5],
            }
        )
    ins_sql, ins_params = _rendered(session.statements[0])
    assert ins_sql.strip().upper().startswith("INSERT INTO PUBLIC.BEAT_MEMOS")
    assert _SCRIPT_ID in ins_params.values()  # bigint-coerced script_id
    assert 30 in ins_params.values()  # int-coerced anchor_sec
    assert "30" not in ins_params.values()  # never a string in the INTEGER bind
    assert ["beats/memos/a", "5"] in ins_params.values()  # images → strings
    assert out["anchor_sec"] == 30
    assert out["images"] == []


@pytest.mark.asyncio
async def test_update_whitelists_and_coerces():
    """update() accepts only anchor_sec/content/images, int-coercing anchor_sec
    and string-coercing images; script_id/id are never bound."""
    session = _CaptureSession([_FakeResult(scalar_first=_memo_obj(anchor_sec=80))])
    with patch.object(memo_mod, "write_scope", lambda: _ScopeCtx(session)):
        await BeatMemoRepository().update(
            str(_MEMO_ID),
            {
                "anchor_sec": "80",
                "content": "new",
                "images": [1, "beats/memos/b"],
                "script_id": 999,  # not in the whitelist
                "id": 5,  # not in the whitelist
            },
        )
    upd_sql, upd_params = _rendered(session.statements[0])
    assert upd_sql.strip().upper().startswith("UPDATE PUBLIC.BEAT_MEMOS")
    assert 80 in upd_params.values()
    assert "new" in upd_params.values()
    assert ["1", "beats/memos/b"] in upd_params.values()
    assert 999 not in upd_params.values()


@pytest.mark.asyncio
async def test_list_orders_by_anchor_sec():
    session = _CaptureSession([_FakeResult(all_rows=[_memo_obj()])])
    with patch.object(memo_mod, "read_scope", lambda: _ScopeCtx(session)):
        out = await BeatMemoRepository().list_by_script(str(_SCRIPT_ID))
    sql, params = _rendered(session.statements[0])
    assert sql.strip().upper().startswith("SELECT")
    assert "FROM PUBLIC.BEAT_MEMOS" in sql.upper()
    assert "ORDER BY" in sql.upper() and "ANCHOR_SEC" in sql.upper()
    assert _SCRIPT_ID in params.values()
    assert len(out) == 1
