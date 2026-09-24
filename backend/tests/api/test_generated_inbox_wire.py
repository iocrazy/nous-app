"""``/generated`` (the Generated inbox) wire parity after it gained response models (P3).

The router already pushed every body through its Pydantic model with
``model_dump(mode="json")``; declaring ``response_model=Envelope[...]`` makes
FastAPI validate and dump it a SECOND time. These tests prove that second pass
changes no byte: each route is driven over real HTTP by the REAL
``GeneratedInboxService`` over a fake repository whose row carries every
projected column in its native type (``sample_row``), and the body must equal
what the router sent before (``jsonable_encoder`` of the handler's dict).

``test_generated_router.py`` owns the filter translation and refusal envelopes;
this file owns the declared shape.
"""

from __future__ import annotations

import sys

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app
from app.models.generated_media import GeneratedMedia
from app.schemas.generated import (
    GeneratedBatchResult,
    GeneratedCleanupResponse,
    GeneratedCounts,
    GeneratedDeleted,
    GeneratedItem,
    GeneratedPage,
    GeneratedSaveAsAssetResult,
    GeneratedSource,
)
from app.services.library.generated_inbox_service import GeneratedInboxService
from tests.api.wire_parity import assert_wire_unchanged, sample_row

gr = sys.modules["app.api.generated_router"]
repo_mod = sys.modules["app.repositories.generated_media_repository"]

USER = "00000000-0000-0000-0000-000000000042"
SCOPE = "727145299382534200"
PROJECTED = [col.key for col in repo_mod._GM_COLS]

# Projected by the repository, deliberately NOT on the inbox card: storage
# location, raw provider params, cost and lineage internals. ``_build_item``'s
# ``model_validate`` is what drops them. A column added to the projection must
# be classified here or on ``GeneratedItem`` — this set is pinned below.
INTERNAL_ONLY = {
    "creator_id",
    "file_path",
    "file_size_bytes",
    "origin_run_id",
    "agent_id",
    "params",
    "cost_cents",
    "parent_resource_id",
    "derivation_kind",
    "conversation_id",
}
DERIVED = {"source", "title"}


def _repo_row(**overrides) -> dict:
    """What ``GeneratedMediaRepository`` returns: the projection, normalized."""
    row = sample_row(GeneratedMedia, only=PROJECTED)
    row.update(review_state="unreviewed", origin_kind="canvas_run", **overrides)
    return repo_mod._normalize(row)


def _row_with_nulls() -> dict:
    row = _repo_row()
    for name, field in GeneratedItem.model_fields.items():
        if name in row and type(None) in getattr(field.annotation, "__args__", ()):
            row[name] = None
    return row


class _GenRepo:
    def __init__(self) -> None:
        self.row = _repo_row()

    async def list_inbox(self, scope_id, **kwargs):
        return {"items": [self.row, _row_with_nulls()], "next_cursor": "abc"}

    async def get(self, gen_id, scope_id):
        return self.row

    async def count_by_state(self, scope_id):
        return {"unreviewed": 4, "saved": 2, "in_assets": 1, "deleted": 9}

    async def delete(self, gen_id, scope_id):
        return True

    async def list_older_unreviewed(self, scope_id, older_than, **kwargs):
        return [self.row]


class _Canvases:
    async def names_by_ids(self, ids):
        return {str(i): f"Canvas {i}" for i in ids}


def _service() -> GeneratedInboxService:
    return GeneratedInboxService(gen_repo=_GenRepo(), canvases=_Canvases())


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=USER, auth_type="jwt")


@pytest.fixture(autouse=True)
def _wire(monkeypatch):
    app.dependency_overrides[get_auth] = _fake_auth

    async def _gate(scope_id, auth):
        return int(scope_id)

    monkeypatch.setattr(gr, "_gate", _gate)
    monkeypatch.setattr(gr, "_service", _service)
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _before(model, payload) -> dict:
    """The body the router built before it declared a response model."""
    return {
        "success": True,
        "data": model.model_validate(payload).model_dump(mode="json"),
    }


def test_card_declares_every_projected_column_or_says_why_not() -> None:
    card = set(GeneratedItem.model_fields) - DERIVED
    assert card | INTERNAL_ONLY == set(PROJECTED)
    assert not card & INTERNAL_ONLY


def test_response_models_emit_every_key_so_openapi_marks_them_required() -> None:
    """The routes dump each model in full, so a defaulted field is never
    absent; the output schema has to say so or the generated TS reads it as
    optional."""
    schemas = app.openapi()["components"]["schemas"]
    for model in (
        GeneratedSource,
        GeneratedItem,
        GeneratedPage,
        GeneratedCleanupResponse,
        GeneratedBatchResult,
    ):
        required = set(schemas[model.__name__].get("required", []))
        assert required == set(model.model_fields), model.__name__


@pytest.mark.asyncio
async def test_list_wire_unchanged(client) -> None:
    page = await _service().list(int(SCOPE), SCOPE)
    resp = await client.get(f"/api/v1/generated?scope_id={SCOPE}")
    assert_wire_unchanged(resp, _before(GeneratedPage, page))
    items = resp.json()["data"]["items"]
    assert len(items) == 2
    # Pydantic's own rendering (``…Z``), unlike /generated-media's
    # ``isoformat()`` — that is what this route has always sent.
    assert items[0]["created_at"].endswith("Z")
    assert items[1]["mime"] is None and "mime" in items[1]


@pytest.mark.asyncio
async def test_get_wire_unchanged(client) -> None:
    row = await _service().get_item(1, int(SCOPE))
    resp = await client.get(f"/api/v1/generated/1?scope_id={SCOPE}")
    assert_wire_unchanged(resp, _before(GeneratedItem, row))


@pytest.mark.asyncio
async def test_counts_wire_unchanged(client) -> None:
    resp = await client.get(f"/api/v1/generated/counts?scope_id={SCOPE}")
    assert_wire_unchanged(
        resp, {"success": True, "data": {"unreviewed": 4, "saved": 2, "in_assets": 1}}
    )
    assert set(GeneratedCounts.model_fields) == {"unreviewed", "saved", "in_assets"}


@pytest.mark.asyncio
async def test_save_wire_unchanged(monkeypatch, client) -> None:
    async def _promoted(self, gen_id, scope_id, user_id):
        return {"id": 5}

    monkeypatch.setattr(GeneratedInboxService, "_promoted_resource", _promoted)
    row = await _service().get_item(1, int(SCOPE))
    resp = await client.post(f"/api/v1/generated/1/save?scope_id={SCOPE}")
    assert_wire_unchanged(resp, _before(GeneratedItem, row))


@pytest.mark.asyncio
async def test_save_as_asset_wire_unchanged(monkeypatch, client) -> None:
    card = await _service().get_item(1, int(SCOPE))
    out = {"generation": card, "asset_id": "700000000000000001", "resource_id": "5"}

    async def _save_as_asset(self, gen_id, scope_id, user_id, req):
        return out

    monkeypatch.setattr(GeneratedInboxService, "save_as_asset", _save_as_asset)
    resp = await client.post(
        f"/api/v1/generated/1/save-as-asset?scope_id={SCOPE}",
        json={"asset_id": "700000000000000001"},
    )
    raw = {
        "success": True,
        "data": {
            "generation": GeneratedItem.model_validate(card).model_dump(mode="json"),
            "asset_id": out["asset_id"],
            "resource_id": out["resource_id"],
        },
    }
    assert_wire_unchanged(resp, raw, status=201)
    assert set(GeneratedSaveAsAssetResult.model_fields) == set(raw["data"])


@pytest.mark.asyncio
async def test_delete_wire_unchanged(client) -> None:
    resp = await client.delete(f"/api/v1/generated/1?scope_id={SCOPE}")
    assert_wire_unchanged(resp, {"success": True, "data": {"deleted": True}})
    assert set(GeneratedDeleted.model_fields) == {"deleted"}


@pytest.mark.asyncio
async def test_batch_wire_unchanged(client) -> None:
    resp = await client.post(
        f"/api/v1/generated/batch?scope_id={SCOPE}",
        json={"ids": ["1", "2"], "action": "delete"},
    )
    assert_wire_unchanged(
        resp, {"success": True, "data": {"ok": ["1", "2"], "failed": []}}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("dry_run", [True, False])
async def test_cleanup_wire_unchanged(client, dry_run) -> None:
    from app.schemas.generated import CleanupRequest

    out = await _service().cleanup(CleanupRequest(dry_run=dry_run), int(SCOPE))
    resp = await client.post(
        f"/api/v1/generated/cleanup?scope_id={SCOPE}", json={"dry_run": dry_run}
    )
    assert_wire_unchanged(resp, _before(GeneratedCleanupResponse, out))
    assert set(resp.json()["data"]) == set(GeneratedCleanupResponse.model_fields)


def test_every_generated_route_declares_a_response_model() -> None:
    paths = app.openapi()["paths"]
    ops = [
        (path, method, op)
        for path, item in paths.items()
        if path == "/api/v1/generated" or path.startswith("/api/v1/generated/")
        for method, op in item.items()
    ]
    assert len(ops) == 8
    for path, method, op in ops:
        success = min(code for code in op["responses"] if code.startswith("2"))
        schema = op["responses"][success]["content"]["application/json"]["schema"]
        assert "$ref" in schema, (method, path)
