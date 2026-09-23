"""GET /search/vectors/status — what the library UI reads to show vector
coverage (PR 2 / mig 497). Called as a coroutine with patched collaborators,
like ``test_visual_analysis_read_endpoint``. Three statuses, one test each,
plus the not_built layer rule."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any, List

import pytest

from app.core.embedding_space import SEMANTIC_LAYER, SpaceSpec
from app.repositories.resource_embeddings_repository import EmbeddingStoreMissing

# app.api rebinds ``search_router`` to the APIRouter; take the module.
search_router = importlib.import_module("app.api.search_router")

_SPEC = SpaceSpec(
    actual_model="doubao-embedding-vision-251215",
    dims=2048,
    protocol="ark_multimodal",
    modalities=("image", "text", "video"),
)
_SPACE = {
    "id": 3,
    "actual_model": "doubao-embedding-vision-251215",
    "protocol": "ark_multimodal",
    "dims": 2048,
    "modalities": ["image", "text", "video"],
    "instruction_version": "en_keyword_v1",
    "created_at": "2026-09-23T00:00:00+00:00",
}


class _Embedder:
    def __init__(self, spec):
        self.spec = spec

    async def space_spec(self):
        return self.spec


class _SpaceRepo:
    def __init__(self, fail: Exception | None = None):
        self.fail = fail

    async def get_or_create(self, spec):
        if self.fail is not None:
            raise self.fail
        return _SPACE


class _EmbRepo:
    def __init__(self, covered: int, total: int, fail: Exception | None = None):
        self.covered, self.total, self.fail = covered, total, fail
        self.calls: List[dict] = []

    async def coverage(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail is not None:
            raise self.fail
        return self.covered, self.total


def _wire(monkeypatch, *, spec=_SPEC, space_repo=None, emb_repo=None) -> Any:
    emb_repo = emb_repo or _EmbRepo(0, 0)
    monkeypatch.setattr(search_router, "EmbeddingService", lambda: _Embedder(spec))
    monkeypatch.setattr(
        search_router,
        "get_embedding_space_repository",
        lambda: space_repo or _SpaceRepo(),
    )
    monkeypatch.setattr(
        search_router, "get_resource_embeddings_repository", lambda: emb_repo
    )
    return emb_repo


_AUTH = SimpleNamespace(user_id="u-1")


@pytest.mark.asyncio
async def test_ok_reports_space_and_coverage(monkeypatch):
    emb = _wire(monkeypatch, emb_repo=_EmbRepo(12, 1409))
    resp = await search_router.vectors_status(_AUTH)
    body = resp.model_dump()
    assert body["status"] == "ok"
    # Snowflake id goes out as a string (JS precision past 2^53).
    assert body["space"] == {
        **{k: v for k, v in _SPACE.items() if k != "created_at"},
        "id": str(_SPACE["id"]),
    }
    assert body["layers"] == [
        {"layer": "semantic", "status": "ok", "covered": 12, "total": 1409},
        {"layer": "transcript", "status": "not_built", "covered": 0, "total": 1409},
    ]
    assert emb.calls == [{"user_id": "u-1", "space_id": 3, "layer": SEMANTIC_LAYER}]


@pytest.mark.asyncio
async def test_empty_semantic_layer_is_not_built(monkeypatch):
    _wire(monkeypatch, emb_repo=_EmbRepo(0, 5))
    body = (await search_router.vectors_status(_AUTH)).model_dump()
    assert body["status"] == "ok"
    assert body["layers"][0] == {
        "layer": "semantic",
        "status": "not_built",
        "covered": 0,
        "total": 5,
    }


@pytest.mark.asyncio
async def test_unconfigured_still_reports_the_callers_total(monkeypatch):
    emb = _wire(monkeypatch, spec=None, emb_repo=_EmbRepo(0, 7))
    body = (await search_router.vectors_status(_AUTH)).model_dump()
    assert body["status"] == "unconfigured" and body["space"] is None
    assert [
        (x["layer"], x["status"], x["covered"], x["total"]) for x in body["layers"]
    ] == [
        ("semantic", "not_built", 0, 7),
        ("transcript", "not_built", 0, 7),
    ]
    # No space exists to count against; the count must not match any real one.
    assert emb.calls[0]["space_id"] == search_router.NO_SPACE_ID


@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["space", "coverage"])
async def test_store_missing(monkeypatch, where):
    missing = EmbeddingStoreMissing("migration 497 not applied")
    _wire(
        monkeypatch,
        space_repo=_SpaceRepo(fail=missing if where == "space" else None),
        emb_repo=_EmbRepo(0, 0, fail=missing if where == "coverage" else None),
    )
    body = (await search_router.vectors_status(_AUTH)).model_dump()
    assert body == {"space": None, "status": "store_missing", "layers": []}


def test_route_is_registered_before_the_similar_route():
    paths = [r.path for r in search_router.router.routes]
    assert "/search/vectors/status" in paths
    assert paths.index("/search/vectors/status") < paths.index(
        "/search/similar/{media_id}"
    )
