"""Beats view routes (beats + timeline memos): wire parity after they gained
response models (P6).

Every JSON route runs over real HTTP through the real router. The repository
below the handler is a fake that returns rows built from the ORM mapper and
passed through the repository's own ``_row`` (``sample_orm``), so each row
carries every column in its real native type. The body must equal what
FastAPI sent for the dict the handler built with no model
(``tests/api/wire_parity.py``).

Also pinned here: a beat / memo that vanished between the access guard and the
write is a typed 404 (``not_found_or_out_of_scope``), not ``200 {"data": {}}``
or a 500.
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import inspect

from app.core.deps import AuthContext, get_auth
from app.core.scope_guards import (
    verify_beat_access,
    verify_memo_access,
    verify_script_access,
    verify_script_read_access,
)
from app.main import app
from app.models import BeatMemos, ScriptBeats
from app.repositories.beat_memo_repository import _row as memo_row_of
from app.repositories.script_beat_repository import _row as beat_row_of
from app.schemas.script_beat_responses import BeatMemoOut, ScriptBeatRow
from tests.api.wire_parity import (
    SAMPLE_BIGINT,
    assert_wire_unchanged,
    column_names,
    sample_orm,
)

pytestmark = pytest.mark.unit

beats_mod = importlib.import_module("app.api.script_beats_router")
memos_mod = importlib.import_module("app.api.beat_memos_router")

USER = "00000000-0000-0000-0000-000000000042"
SID = SAMPLE_BIGINT + 11
GUARDS = (
    verify_script_access,
    verify_script_read_access,
    verify_beat_access,
    verify_memo_access,
)


def _nullable_none(model: Any) -> dict[str, Any]:
    return {
        prop.key: None
        for prop in inspect(model).column_attrs
        if prop.columns[0].nullable and not prop.columns[0].primary_key
    }


def beat_row(**over: Any) -> dict:
    values = {"script_id": SID, "scene_ids": ["7300000000000000901", "902"]}
    values.update(over)
    return beat_row_of(sample_orm(ScriptBeats, **values))


def memo_row(**over: Any) -> dict:
    values = {"script_id": SID, "images": ["beats/memos/a.png"]}
    values.update(over)
    return memo_row_of(sample_orm(BeatMemos, **values))


def memo_wire(row: dict) -> dict:
    """What the handler built before the model: bigint ids as strings."""
    return {
        "id": str(row["id"]),
        "script_id": str(row["script_id"]),
        "anchor_sec": row["anchor_sec"],
        "content": row["content"],
        "images": row["images"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


class FakeRepo:
    """Stands in for both repositories; every method returns ``result``."""

    result: Any = None
    calls: list = []

    def __getattr__(self, name):
        async def _method(*args, **kwargs):
            FakeRepo.calls.append((name, args, kwargs))
            return FakeRepo.result

        return _method


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=USER, auth_type="jwt")


@pytest.fixture(autouse=True)
def _wiring(monkeypatch):
    app.dependency_overrides[get_auth] = _fake_auth
    for guard in GUARDS:
        app.dependency_overrides[guard] = lambda: None
    FakeRepo.result = None
    FakeRepo.calls = []
    monkeypatch.setattr(beats_mod, "get_script_beat_repository", FakeRepo)
    monkeypatch.setattr(memos_mod, "get_beat_memo_repository", FakeRepo)
    yield
    for dep in (get_auth, *GUARDS):
        app.dependency_overrides.pop(dep, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _assert_typed_404(resp) -> None:
    assert resp.status_code == 404, resp.text
    assert resp.json()["details"]["code"] == "not_found_or_out_of_scope"


# --------------------------------------------------------------------------- #
# Model pins
# --------------------------------------------------------------------------- #


def test_row_models_declare_every_column() -> None:
    assert set(ScriptBeatRow.model_fields) == column_names(ScriptBeats)
    assert set(BeatMemoOut.model_fields) == column_names(BeatMemos)


# --------------------------------------------------------------------------- #
# Beats
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_list_beats_wire_unchanged(client) -> None:
    rows = [beat_row(), beat_row(id=SAMPLE_BIGINT + 5, **_nullable_none(ScriptBeats))]
    FakeRepo.result = rows
    resp = await client.get(f"/api/v1/scripts/{SID}/beats")
    assert_wire_unchanged(resp, {"success": True, "data": rows})
    # Snowflake ids stay JSON numbers on this surface.
    assert resp.json()["data"][0]["id"] == rows[0]["id"]


@pytest.mark.asyncio
async def test_create_beat_wire_unchanged(client) -> None:
    FakeRepo.result = beat_row()
    resp = await client.post(f"/api/v1/scripts/{SID}/beats", json={"title": "Open"})
    assert_wire_unchanged(resp, {"success": True, "data": FakeRepo.result})


@pytest.mark.asyncio
async def test_update_beat_wire_unchanged(client) -> None:
    FakeRepo.result = beat_row()
    resp = await client.patch("/api/v1/beats/5", json={"summary": None})
    assert_wire_unchanged(resp, {"success": True, "data": FakeRepo.result})


@pytest.mark.asyncio
async def test_move_beat_wire_unchanged(client) -> None:
    FakeRepo.result = beat_row()
    resp = await client.post("/api/v1/beats/5/move", json={"after_beat_id": "6"})
    assert_wire_unchanged(resp, {"success": True, "data": FakeRepo.result})


@pytest.mark.asyncio
async def test_delete_beat_wire_unchanged(client) -> None:
    FakeRepo.result = True
    resp = await client.delete("/api/v1/beats/5")
    assert_wire_unchanged(resp, {"success": True})


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [None, {}])
async def test_update_beat_vanished_is_typed_404(client, result) -> None:
    FakeRepo.result = result
    _assert_typed_404(await client.patch("/api/v1/beats/5", json={"title": "x"}))


@pytest.mark.asyncio
async def test_move_beat_vanished_is_typed_404(client) -> None:
    FakeRepo.result = {}
    _assert_typed_404(await client.post("/api/v1/beats/5/move", json={}))


# --------------------------------------------------------------------------- #
# Memos
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_list_memos_wire_unchanged(client) -> None:
    rows = [memo_row(), memo_row(id=SAMPLE_BIGINT + 5, images=[])]
    FakeRepo.result = rows
    resp = await client.get(f"/api/v1/scripts/{SID}/memos")
    assert_wire_unchanged(resp, {"success": True, "data": [memo_wire(r) for r in rows]})
    # Memo ids are strings on this surface (unlike beats).
    assert resp.json()["data"][0]["id"] == str(rows[0]["id"])


@pytest.mark.asyncio
async def test_create_memo_wire_unchanged(client) -> None:
    FakeRepo.result = memo_row()
    resp = await client.post(f"/api/v1/scripts/{SID}/memos", json={"anchor_sec": 3})
    assert_wire_unchanged(resp, {"success": True, "data": memo_wire(FakeRepo.result)})


@pytest.mark.asyncio
async def test_update_memo_wire_unchanged(client) -> None:
    FakeRepo.result = memo_row()
    resp = await client.patch("/api/v1/memos/5", json={"content": "hi"})
    assert_wire_unchanged(resp, {"success": True, "data": memo_wire(FakeRepo.result)})


@pytest.mark.asyncio
async def test_delete_memo_wire_unchanged(client) -> None:
    FakeRepo.result = True
    resp = await client.delete("/api/v1/memos/5")
    assert_wire_unchanged(resp, {"success": True})


@pytest.mark.asyncio
async def test_upload_memo_image_wire_unchanged(client, monkeypatch) -> None:
    class _Store:
        async def store(self, script_id, filename, mime, data):
            return f"beats/memos/{script_id}/abc.png"

    monkeypatch.setattr(memos_mod, "get_memo_image_service", lambda: _Store())
    resp = await client.post(
        f"/api/v1/scripts/{SID}/memos/upload",
        files={"file": ("a.png", b"\x89PNG", "image/png")},
    )
    assert_wire_unchanged(
        resp, {"success": True, "data": {"path": f"beats/memos/{SID}/abc.png"}}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [None, {}])
async def test_update_memo_vanished_is_typed_404(client, result) -> None:
    FakeRepo.result = result
    _assert_typed_404(await client.patch("/api/v1/memos/5", json={"content": "x"}))


@pytest.mark.asyncio
async def test_create_memo_empty_row_is_typed_404(client) -> None:
    FakeRepo.result = {}
    _assert_typed_404(
        await client.post(f"/api/v1/scripts/{SID}/memos", json={"anchor_sec": 1})
    )
