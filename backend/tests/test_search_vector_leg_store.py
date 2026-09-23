"""The three vector read paths go through ``resource_embeddings`` (mig 499).

hybrid (``_vector_hits``), ``semantic_search`` and ``find_similar_media`` all
name the embedder's space and the semantic layer. Deploy window: when the new
table / RPC does not exist yet (``EmbeddingStoreMissing``) they fall back to
the legacy ``resource_analysis.content_embedding`` RPC; when that is gone too
the hybrid leg says ``store_missing`` instead of reading as "no match".
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.core.embedding_space import SEMANTIC_LAYER, SpaceSpec
from app.repositories.analysis_repository import EmbeddingSearchUnavailable
from app.repositories.resource_embeddings_repository import EmbeddingStoreMissing
from app.services.library import semantic_store as store_mod
from app.services.library.search_service import (
    VECTOR_LEG_OUTCOMES,
    SearchResult,
    SearchService,
)

_SPEC = SpaceSpec(
    actual_model="m-1", dims=2048, protocol="ark_multimodal", modalities=("text",)
)


def _row(media_id: int, resource_id: int | None = None, sim: float = 0.8) -> dict:
    return {
        "resource_id": resource_id if resource_id is not None else media_id * 10,
        "media_id": media_id,
        "platform_id": f"p{media_id}",
        "title": f"t{media_id}",
        "description": None,
        "cover_urls": None,
        "author": None,
        "view_count": 0,
        "created_at": None,
        "similarity": sim,
    }


class _Embedder:
    model = "m-1"

    def __init__(self, spec=_SPEC):
        self.spec = spec

    async def try_embed(self, text):
        return [0.1, 0.2], None

    async def generate_embedding(self, text):
        return [0.1, 0.2]

    async def space_spec(self):
        return self.spec


class _SpaceRepo:
    def __init__(self, fail: Exception | None = None):
        self.fail = fail
        self.calls = 0

    async def get_or_create(self, spec):
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        return {"id": 5}


class _EmbRepo:
    def __init__(self, rows=None, stored=None, fail: Exception | None = None):
        self.rows = rows or []
        self.stored = stored
        self.fail = fail
        self.searches: List[Dict[str, Any]] = []
        self.gets: List[tuple] = []

    async def search(self, **kwargs):
        self.searches.append(kwargs)
        if self.fail is not None:
            raise self.fail
        return self.rows

    async def get(self, resource_id, layer, space_id):
        self.gets.append((resource_id, layer, space_id))
        if self.fail is not None:
            raise self.fail
        return self.stored


class _LegacyRepo:
    def __init__(self, rows=None, analysis=None, fail: Exception | None = None):
        self.rows = rows or []
        self.analysis = analysis
        self.fail = fail
        self.calls: List[Dict[str, Any]] = []

    async def search_by_embedding(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail is not None:
            raise self.fail
        return self.rows

    async def get_analysis(self, media_id):
        return self.analysis


@pytest.fixture(autouse=True)
def _clear_space_cache():
    store_mod._SPACE_ID_CACHE.clear()
    yield
    store_mod._SPACE_ID_CACHE.clear()


def _svc(*, emb_repo, space_repo=None, legacy=None, embedder=None) -> SearchService:
    svc = SearchService(space_repo=space_repo or _SpaceRepo(), embeddings_repo=emb_repo)
    svc.embedding_service = embedder or _Embedder()
    svc.analysis_repo = legacy or _LegacyRepo()
    return svc


def test_store_missing_is_a_declared_outcome():
    assert "store_missing" in VECTOR_LEG_OUTCOMES


@pytest.mark.asyncio
async def test_hybrid_vector_leg_uses_space_and_new_repo():
    emb = _EmbRepo(rows=[_row(1)])
    legacy = _LegacyRepo()
    svc = _svc(emb_repo=emb, legacy=legacy)
    hits, outcome = await svc._vector_hits("cat", "u-1", limit=7, threshold=0.4)
    assert outcome == "ok" and [h.media_id for h in hits] == [1]
    assert emb.searches == [
        {
            "embedding": [0.1, 0.2],
            "space_id": 5,
            "layer": SEMANTIC_LAYER,
            "user_id": "u-1",
            "limit": 7,
            "threshold": 0.4,
        }
    ]
    assert legacy.calls == []


@pytest.mark.asyncio
async def test_space_is_resolved_once_per_process():
    space_repo = _SpaceRepo()
    for _ in range(3):
        svc = _svc(emb_repo=_EmbRepo(), space_repo=space_repo)
        await svc._vector_hits("cat", "u-1", limit=5, threshold=0.4)
    assert space_repo.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["space", "search"])
async def test_hybrid_vector_leg_falls_back_to_legacy_rpc_when_store_missing(where):
    missing = EmbeddingStoreMissing("mig 499 not applied")
    emb = _EmbRepo(fail=missing if where == "search" else None)
    space_repo = _SpaceRepo(fail=missing if where == "space" else None)
    legacy = _LegacyRepo(rows=[_row(2)])
    svc = _svc(emb_repo=emb, space_repo=space_repo, legacy=legacy)
    hits, outcome = await svc._vector_hits("cat", "u-1", limit=5, threshold=0.4)
    assert outcome == "ok" and [h.media_id for h in hits] == [2]
    assert legacy.calls[0]["user_id"] == "u-1"
    assert legacy.calls[0]["embedding_model"] == "m-1"


@pytest.mark.asyncio
async def test_hybrid_vector_leg_reports_store_missing_when_both_paths_gone():
    svc = _svc(
        emb_repo=_EmbRepo(fail=EmbeddingStoreMissing("499")),
        legacy=_LegacyRepo(fail=EmbeddingSearchUnavailable("gone")),
    )
    hits, outcome = await svc._vector_hits("cat", "u-1", limit=5, threshold=0.4)
    assert (hits, outcome) == ([], "store_missing")


@pytest.mark.asyncio
async def test_semantic_search_reads_the_new_store():
    emb = _EmbRepo(rows=[_row(3)])
    svc = _svc(emb_repo=emb)
    out = await svc.semantic_search("cat", limit=4, threshold=0.5, user_id="u-2")
    assert [r.media_id for r in out.results] == [3]
    assert out.results[0].layer == "semantic"
    assert emb.searches[0]["space_id"] == 5
    assert emb.searches[0]["user_id"] == "u-2"


@pytest.mark.asyncio
async def test_semantic_search_raises_when_both_stores_are_gone():
    svc = _svc(
        emb_repo=_EmbRepo(fail=EmbeddingStoreMissing("499")),
        legacy=_LegacyRepo(fail=EmbeddingSearchUnavailable("gone")),
    )
    with pytest.raises(EmbeddingSearchUnavailable):
        await svc.semantic_search("cat", user_id="u-2")


@pytest.mark.asyncio
async def test_find_similar_prefers_new_table_vector():
    emb = _EmbRepo(
        rows=[_row(9, resource_id=77), _row(8, resource_id=88)],
        stored={"embedding": [0.3, 0.4]},
    )
    legacy = _LegacyRepo(analysis={"content_embedding": "[0.9]"})
    svc = _svc(emb_repo=emb, legacy=legacy)
    out = await svc.find_similar_media(media_id=77, user_id="u-3", limit=5)
    assert emb.gets == [(77, SEMANTIC_LAYER, 5)]
    assert emb.searches[0]["embedding"] == [0.3, 0.4]
    assert emb.searches[0]["limit"] == 6 and emb.searches[0]["user_id"] == "u-3"
    # The source itself (resource 77) is excluded.
    assert [r.media_id for r in out.results] == [8]
    assert out.source_embedded is True
    assert legacy.calls == []


@pytest.mark.asyncio
async def test_find_similar_falls_back_to_the_legacy_column():
    emb = _EmbRepo(stored=None)
    legacy = _LegacyRepo(rows=[_row(4)], analysis={"content_embedding": "[0.5, 0.6]"})
    svc = _svc(emb_repo=emb, legacy=legacy)
    out = await svc.find_similar_media(media_id=77, user_id="u-3", limit=5)
    assert legacy.calls[0]["embedding"] == [0.5, 0.6]
    assert [r.media_id for r in out.results] == [4]
    assert out.source_embedded is True


@pytest.mark.asyncio
async def test_find_similar_says_when_the_source_has_no_vector():
    svc = _svc(emb_repo=_EmbRepo(stored=None), legacy=_LegacyRepo(analysis=None))
    out = await svc.find_similar_media(media_id=77, user_id="u-3")
    assert out.total == 0 and out.source_embedded is False


# ---------------------------------------------------------------------------
# Step 5b: per-hit layer + per-leg counts (UI legs chip)
# ---------------------------------------------------------------------------
def _hit(mid: int, sim: float = 1.0) -> SearchResult:
    return SearchResult(
        media_id=mid,
        platform_id=f"p{mid}",
        title="t",
        description=None,
        cover_url=None,
        similarity=sim,
    )


def test_merge_labels_each_hit_with_its_layer():
    merged = SearchService._merge_text_and_vector(
        [_hit(1), _hit(2)], [_hit(2, 0.9), _hit(3, 0.7)], limit=10
    )
    assert [(h.media_id, h.layer) for h in merged] == [
        (1, "text"),
        (2, "text"),
        (3, "semantic"),
    ]


@pytest.mark.asyncio
async def test_hybrid_reports_legs_and_reranked():
    emb = _EmbRepo(rows=[_row(2, sim=0.9), _row(3, sim=0.7)])
    svc = _svc(emb_repo=emb)

    async def _text(**kwargs):
        return [
            {"id": 1, "platform_id": "p1", "title": "a"},
            {"id": 2, "platform_id": "p2", "title": "b"},
        ]

    svc.search_user_media_text = _text  # type: ignore[method-assign]
    out = await svc.hybrid_search("cat", user_id="u-1", limit=10)
    assert out.vector_leg == "ok"
    assert [(r.media_id, r.layer) for r in out.results] == [
        (1, "text"),
        (2, "text"),
        (3, "semantic"),
    ]
    assert out.legs == {"text": 2, "semantic": 1}
    assert out.reranked is False


@pytest.mark.asyncio
async def test_hybrid_router_carries_layer_legs_and_reranked(monkeypatch):
    import importlib

    # app.api rebinds ``search_router`` to the APIRouter; take the module.
    search_router = importlib.import_module("app.api.search_router")
    from app.schemas.search import HybridSearchRequest
    from app.services.library.search_service import SearchResponse as SvcResponse

    async def _fake_hybrid(self, **kwargs):
        return SvcResponse(
            results=[
                _hit(1),
                SearchResult(**{**_hit(3).__dict__, "layer": "semantic"}),
            ],
            total=2,
            query="cat",
            search_type="hybrid",
            vector_leg="ok",
            legs={"text": 1, "semantic": 1},
            reranked=False,
        )

    async def _no_hydrate(platform_ids, user_id):
        return []

    monkeypatch.setattr(SearchService, "hybrid_search", _fake_hybrid)
    monkeypatch.setattr(search_router, "_hydrate_media_by_platform_ids", _no_hydrate)

    class _Auth:
        user_id = "u-1"

    resp = await search_router.hybrid_search(HybridSearchRequest(query="cat"), _Auth())
    body = resp.model_dump()
    assert [r["layer"] for r in body["results"]] == ["text", "semantic"]
    assert body["legs"] == {"text": 1, "semantic": 1}
    assert body["reranked"] is False
