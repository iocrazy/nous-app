"""Cleanup routes: wire parity after they gained response models (P9).

Every route runs over real HTTP. ``/storage`` and the keep routes run the real
router / service code with a scripted database session; the action and batch
routes script ``CleanupService`` / ``_delete_user_media`` because the body is
built in the route itself. Bodies must equal what FastAPI sent for the same
dict with no model (``tests/api/wire_parity.py``).

Also pinned: the keep routes only touch media the caller owns a resource for.
``parsed_media`` is a global table (mig 083), so ownership is a ``resources``
row with ``creator_id`` = caller; a foreign media id answers 404 and nothing
is written.
"""

from __future__ import annotations

import datetime as dt
import sys
from contextlib import asynccontextmanager
from typing import Any, List

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import app.db.session as db_session_mod
from app.core.deps import AuthContext, get_auth
from app.main import app
from tests.api.wire_parity import SAMPLE_TS, assert_wire_unchanged

r = sys.modules["app.api.cleanup_router"]
svc_mod = sys.modules["app.services.infra.cleanup_service"]

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000042"
MEDIA_ID = 7300000000000000123


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=USER, auth_type="jwt")


class _Result:
    def __init__(self, rows: List[Any]):
        self._rows = rows

    def scalars(self):
        rows = self._rows

        class _S:
            def all(self):
                return list(rows)

        return _S()

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _Db:
    results: List[_Result] = []
    executed: int = 0


@pytest.fixture(autouse=True)
def _wiring(monkeypatch):
    app.dependency_overrides[get_auth] = _fake_auth
    _Db.results = []
    _Db.executed = 0

    class _Session:
        async def execute(self, stmt):
            _Db.executed += 1
            return _Db.results.pop(0)

    @asynccontextmanager
    async def _scope():
        yield _Session()

    monkeypatch.setattr(db_session_mod, "read_scope", _scope)
    monkeypatch.setattr(svc_mod, "read_scope", _scope)
    monkeypatch.setattr(svc_mod, "write_scope", _scope)
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ── /storage ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_storage_empty_wire(client) -> None:
    _Db.results = [_Result([])]
    resp = await client.get("/api/v1/cleanup/storage")
    assert_wire_unchanged(
        resp,
        {
            "by_type": {"video": 0, "image": 0, "other": 0},
            "by_month": [],
            "largest_videos": [],
            "total_bytes": 0,
            "total_videos": 0,
        },
    )


@pytest.mark.asyncio
async def test_storage_wire(client) -> None:
    rows = [
        {
            "id": MEDIA_ID,
            "storage_size": 5000,
            "media_type": "video",
            "created_at": SAMPLE_TS,
        },
        {
            "id": MEDIA_ID + 1,
            "storage_size": 700,
            "media_type": "carousel",
            "created_at": SAMPLE_TS - dt.timedelta(days=40),
        },
        # Historic dirty rows: NULL type / size / timestamp must not 500.
        {
            "id": MEDIA_ID + 2,
            "storage_size": None,
            "media_type": None,
            "created_at": None,
        },
        {
            "id": MEDIA_ID + 3,
            "storage_size": 30,
            "media_type": None,
            "created_at": None,
        },
    ]
    _Db.results = [_Result([MEDIA_ID, MEDIA_ID + 1, None]), _Result(rows)]
    month_a = SAMPLE_TS.isoformat()[:7]
    month_b = (SAMPLE_TS - dt.timedelta(days=40)).isoformat()[:7]
    largest = [
        {
            "id": MEDIA_ID,
            "storage_size": 5000,
            "media_type": "video",
            "created_at": SAMPLE_TS.isoformat(),
        },
        {
            "id": MEDIA_ID + 1,
            "storage_size": 700,
            "media_type": "carousel",
            "created_at": (SAMPLE_TS - dt.timedelta(days=40)).isoformat(),
        },
        {
            "id": MEDIA_ID + 3,
            "storage_size": 30,
            "media_type": None,
            "created_at": None,
        },
    ]
    raw = {
        "by_type": {"video": 5000, "image": 700, "other": 30},
        "by_month": [
            {"month": month_a, "count": 1, "bytes": 5000},
            {"month": month_b, "count": 1, "bytes": 700},
        ],
        "largest_videos": largest,
        "total_bytes": 5730,
        "total_videos": 4,
    }
    resp = await client.get("/api/v1/cleanup/storage")
    assert_wire_unchanged(resp, raw)
    # Snowflake media ids stay JSON numbers.
    assert resp.json()["largest_videos"][0]["id"] == MEDIA_ID


# ── keep / unkeep (real service, scripted session) ──────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "message"),
    [
        ("POST", "Media marked to keep forever"),
        ("DELETE", "Keep forever mark removed"),
    ],
)
async def test_keep_owner_wire(client, method, message) -> None:
    # ownership lookup → the owner's resource; UPDATE ... RETURNING → one row
    _Db.results = [_Result([(1,)]), _Result([(MEDIA_ID,)])]
    resp = await client.request(method, f"/api/v1/cleanup/media/{MEDIA_ID}/keep")
    assert_wire_unchanged(resp, {"message": message, "media_id": MEDIA_ID})
    assert _Db.executed == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "DELETE"])
async def test_keep_foreign_media_is_404_and_writes_nothing(client, method) -> None:
    # The caller owns no resource for this media: no UPDATE may run.
    _Db.results = [_Result([])]
    resp = await client.request(method, f"/api/v1/cleanup/media/{MEDIA_ID}/keep")
    assert resp.status_code == 404, resp.text
    assert _Db.executed == 1


@pytest.mark.asyncio
async def test_action_keep_forever_foreign_media_is_404(client) -> None:
    _Db.results = [_Result([])]
    resp = await client.post(
        f"/api/v1/cleanup/media/{MEDIA_ID}/action", json={"action": "keep_forever"}
    )
    assert resp.status_code == 404, resp.text
    assert _Db.executed == 1


# ── action / batch (route-built bodies) ─────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "message"),
    [
        ("keep_forever", "Media marked to keep forever"),
        ("dismiss", "Suggestion dismissed"),
        ("delete", "Media deleted"),
    ],
)
async def test_action_wire(client, monkeypatch, action, message) -> None:
    async def _ok(*_a: Any, **_k: Any) -> bool:
        return True

    monkeypatch.setattr(svc_mod.CleanupService, "mark_keep_forever", _ok)
    monkeypatch.setattr(r, "_delete_user_media", _ok)
    resp = await client.post(
        f"/api/v1/cleanup/media/{MEDIA_ID}/action", json={"action": action}
    )
    assert_wire_unchanged(resp, {"message": message, "media_id": MEDIA_ID})


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["keep_forever", "delete", "dismiss"])
async def test_batch_wire(client, monkeypatch, action) -> None:
    owned = {MEDIA_ID}

    async def _keep(self, media_id: int, user_id: str) -> bool:
        return media_id in owned

    async def _delete(media_id: int, user_id: str) -> bool:
        if media_id == MEDIA_ID + 2:
            raise RuntimeError("boom")
        return media_id in owned

    monkeypatch.setattr(svc_mod.CleanupService, "mark_keep_forever", _keep)
    monkeypatch.setattr(r, "_delete_user_media", _delete)
    ids = [MEDIA_ID, MEDIA_ID + 1, MEDIA_ID + 2]
    resp = await client.post(
        "/api/v1/cleanup/batch", json={"media_ids": ids, "action": action}
    )
    if action == "dismiss":
        failed: list[int] = []
    elif action == "keep_forever":
        failed = [MEDIA_ID + 1, MEDIA_ID + 2]
    else:
        failed = [MEDIA_ID + 1, MEDIA_ID + 2]
    raw = {
        "message": "Processed 3 media items",
        "action": action,
        "success_count": 3 - len(failed),
        "failed_count": len(failed),
        "failed_ids": failed,
    }
    assert_wire_unchanged(resp, raw)
