"""Every vector writer stamps the space; every reader compares within it; a
dimension mismatch surfaces at every writer instead of reading as "skip".

Companion of ``tests/test_embedding_space.py`` (the module + the guard).
Sessions are stubbed here; the SQL itself runs for real in the migration
replay (``supabase/migrations/490_*``) and the schema-drift gate.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

import pytest

from app.core.embedding_space import EmbeddingDimensionMismatch

_MISMATCH = EmbeddingDimensionMismatch(expected=2048, got=2560, model="new-model")


def _scope_of(session):
    @asynccontextmanager
    async def _scope():
        yield session

    return _scope


class _Result:
    def __init__(self, *, row=None, rows=None, scalar=None, orm_row=None):
        self._row, self._rows, self._scalar, self._orm_row = row, rows, scalar, orm_row

    def mappings(self):
        return self

    def scalars(self):
        return self

    def first(self):
        return self._orm_row if self._orm_row is not None else self._row

    def all(self):
        return self._rows or []

    def scalar(self):
        return self._scalar


class _Session:
    def __init__(self, result: _Result | None = None, fail_first: Any = None):
        self.calls: List[Dict[str, Any]] = []
        self._result = result or _Result()
        self._fail_first = fail_first

    async def execute(self, stmt, params=None):
        self.calls.append({"sql": str(stmt), "params": params, "stmt": stmt})
        if self._fail_first is not None:
            exc, self._fail_first = self._fail_first, None
            raise exc
        return self._result

    async def flush(self):
        return None


# ---------------------------------------------------------------------------
# resource_analysis: writer + RPC reader
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_update_embedding_stamps_the_space(monkeypatch) -> None:
    import app.repositories.analysis_repository as mod

    row = SimpleNamespace(content_embedding=None, embedding_model=None)
    session = _Session(_Result(orm_row=row))
    monkeypatch.setattr(mod, "write_scope", _scope_of(session))
    monkeypatch.setattr(
        mod.AnalysisRepository, "_row_to_dict", staticmethod(lambda r: {"ok": 1})
    )

    await mod.AnalysisRepository().update_embedding(
        7, [0.1], "text", embedding_model="doubao-embedding-vision-251215"
    )
    assert row.embedding_model == "doubao-embedding-vision-251215"
    assert row.content_embedding == [0.1]


@pytest.mark.asyncio
async def test_search_by_embedding_sends_the_query_space(monkeypatch) -> None:
    import app.repositories.analysis_repository as mod

    session = _Session()
    monkeypatch.setattr(mod, "read_scope", _scope_of(session))
    await mod.AnalysisRepository().search_by_embedding(
        [0.1], user_id="u-1", embedding_model="m-1"
    )
    call = session.calls[0]
    assert "CAST(:m AS text)" in call["sql"]
    assert call["params"]["m"] == "m-1"


@pytest.mark.asyncio
async def test_search_by_embedding_falls_back_to_4_args_before_mig_490(
    monkeypatch,
) -> None:
    """Code can land before the migration. The 4-arg RPC still answers (no
    row carries another space yet), instead of 503-ing every search."""
    from sqlalchemy.exc import ProgrammingError

    import app.repositories.analysis_repository as mod

    class _Orig(Exception):
        sqlstate = "42883"

    session = _Session(fail_first=ProgrammingError("stmt", {}, _Orig()))
    monkeypatch.setattr(mod, "read_scope", _scope_of(session))
    out = await mod.AnalysisRepository().search_by_embedding(
        [0.1], user_id="u-1", embedding_model="m-1"
    )
    assert out == []
    assert len(session.calls) == 2
    assert "CAST(:m AS text)" not in session.calls[1]["sql"]
    assert "m" not in session.calls[1]["params"]


# ---------------------------------------------------------------------------
# analyze_l1 + backfill: writers
# ---------------------------------------------------------------------------
def test_analyze_l1_passes_the_space_to_the_writer() -> None:
    import inspect

    import app.workflows.analyze_l1 as mod

    src = inspect.getsource(mod)
    assert "embedding_model=embedding_service.model" in src


@pytest.mark.asyncio
async def test_backfill_reembed_stamps_the_space() -> None:
    from app.services.library import embedding_backfill as bf

    class _Repo:
        def __init__(self):
            self.kwargs: Dict[str, Any] = {}

        async def get_analysis(self, rid, analysis_level=None):
            return {"visual_description": "x"}

        async def update_embedding(self, rid, vec, text, **kw):
            self.kwargs = kw
            return {"resource_id": rid}

    class _Tags:
        async def get_resource_tags(self, rid):
            return []

    class _Emb:
        model = "m-9"

        def build_embedding_text(self, **kw):
            return "t"

        async def try_embed(self, text):
            return [0.1], None

    repo = _Repo()
    cand = bf.BackfillCandidate(1, 10, "p", "T", "", "u", has_analysis=True)
    ok, _ = await bf.reembed_existing(cand, _Emb(), repo, _Tags())
    assert ok and repo.kwargs["embedding_model"] == "m-9"


@pytest.mark.asyncio
async def test_backfill_endpoint_stops_and_reports_a_dimension_mismatch() -> None:
    """Every later row — and every dispatched VLM run — would end the same
    way, so the batch stops and every untouched row says why."""
    from app.api.ai_router import BackfillEmbeddingsBody, backfill_embeddings
    from app.services.library.embedding_backfill import BackfillCandidate

    def _cand(rid, has_analysis=True):
        return BackfillCandidate(rid, rid * 10, f"p{rid}", "t", "", "c", has_analysis)

    fetched = [_cand(1), _cand(2), _cand(3, has_analysis=False)]
    reembed = AsyncMock(return_value=(False, "dimension_mismatch: 2560 != 2048"))
    dispatch = AsyncMock()
    with (
        patch(
            "app.services.ai.providers.embedding_config.resolve_embedding_config",
            AsyncMock(return_value=object()),
        ),
        patch(
            "app.services.library.embedding_backfill.list_candidates",
            AsyncMock(return_value=(fetched, 3)),
        ),
        patch(
            "app.api.ai_router._resources_with_active_l1",
            AsyncMock(return_value=set()),
        ),
        patch("app.services.library.embedding_backfill.reembed_existing", reembed),
        patch("app.api.ai_router._dispatch_l1_analysis", dispatch),
        patch("app.services.ai.providers.embedding_service.EmbeddingService"),
        patch("app.api.ai_router.get_analysis_repository"),
        patch("app.repositories.tags_repository.get_tags_repository"),
    ):
        out = await backfill_embeddings(
            SimpleNamespace(user_id="u-1"), None, BackfillEmbeddingsBody(limit=10)
        )

    assert reembed.await_count == 1
    dispatch.assert_not_awaited()
    assert {s["resource_id"]: s["reason"] for s in out["skipped"]} == {
        1: "dimension_mismatch",
        2: "dimension_mismatch",
        3: "dimension_mismatch",
    }


# ---------------------------------------------------------------------------
# hotspots: writer (embed pass) + content refresh
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_patch_embedding_stamps_the_space(monkeypatch) -> None:
    from app.repositories import hotspots_repository as mod

    session = _Session()
    monkeypatch.setattr(mod, "write_scope", _scope_of(session))
    await mod.HotspotsRepository().patch_embedding("42", [0.5], embedding_model="m")
    call = session.calls[0]
    assert "embedding_model = :model" in call["sql"]
    assert call["params"]["model"] == "m"


@pytest.mark.asyncio
async def test_patch_content_clears_the_space_with_the_vector(monkeypatch) -> None:
    from app.repositories import hotspots_repository as mod

    session = _Session()
    monkeypatch.setattr(mod, "write_scope", _scope_of(session))
    await mod.HotspotsRepository().patch_content("42", "body")
    params = session.calls[0]["stmt"].compile().params
    assert params["embedding"] is None and params["embedding_model"] is None


class _Hotspots:
    def __init__(self, rows):
        self.rows = rows
        self.patched: list = []

    async def list_unembedded(self, limit=40):
        return self.rows

    async def patch_embedding(self, hotspot_id, embedding, *, embedding_model=None):
        self.patched.append((hotspot_id, embedding_model))


class _Embedder:
    def __init__(self, result, model="m-1"):
        self.result, self.model, self.calls = result, model, 0

    async def embed_text(self, text):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.mark.asyncio
async def test_embed_pass_stamps_the_space() -> None:
    from app.workflows.topic_inspiration import embed_unembedded_once

    repo = _Hotspots([{"id": "1", "title": "A"}])
    await embed_unembedded_once(hotspots_repo=repo, embedder=_Embedder([0.1]))
    assert repo.patched == [("1", "m-1")]


@pytest.mark.asyncio
async def test_embed_pass_stops_and_reports_a_dimension_mismatch() -> None:
    from app.workflows.topic_inspiration import embed_unembedded_once

    repo = _Hotspots([{"id": "1", "title": "A"}, {"id": "2", "title": "B"}])
    emb = _Embedder(_MISMATCH)
    with patch("app.workflows.topic_inspiration.logger") as log:
        out = await embed_unembedded_once(hotspots_repo=repo, embedder=emb)
    assert out == {"unembedded": 2, "embedded": 0, "embed_error": "dimension_mismatch"}
    assert emb.calls == 1, "every later row would mismatch the same way"
    assert repo.patched == []
    log.error.assert_called()


@pytest.mark.asyncio
async def test_topic_embedder_names_the_space_and_lets_the_mismatch_through() -> None:
    from app.services.topics.embedding_service import TopicEmbeddingService

    gen = "app.services.ai.providers.embedding_service.EmbeddingService"
    with patch(f"{gen}.generate_embedding", AsyncMock(side_effect=_MISMATCH)):
        with pytest.raises(EmbeddingDimensionMismatch):
            await TopicEmbeddingService().embed_text("hello")

    svc = TopicEmbeddingService()

    async def _gen(self, text):
        self.model = "m-7"
        return [0.1]

    with patch(f"{gen}.generate_embedding", _gen):
        assert await svc.embed_text("hello") == [0.1]
    assert svc.model == "m-7"


# ---------------------------------------------------------------------------
# topic clustering: reader + writer
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_list_unclustered_returns_the_space(monkeypatch) -> None:
    from app.repositories import topic_groups_repository as mod

    session = _Session()
    monkeypatch.setattr(mod, "read_scope", _scope_of(session))
    await mod.TopicGroupRepository().list_unclustered()
    assert "embedding_model" in session.calls[0]["sql"]


@pytest.mark.asyncio
async def test_nearest_group_only_compares_within_the_space(monkeypatch) -> None:
    from app.repositories import topic_groups_repository as mod

    session = _Session()
    monkeypatch.setattr(mod, "read_scope", _scope_of(session))
    await mod.TopicGroupRepository().nearest_group("[0.1]", embedding_model="m")
    call = session.calls[0]
    assert (
        "(embedding_model IS NULL OR CAST(:model AS text) IS NULL "
        "OR embedding_model = CAST(:model AS text))"
    ) in call["sql"]
    assert call["params"]["model"] == "m"


@pytest.mark.asyncio
async def test_create_group_stamps_the_seed_space(monkeypatch) -> None:
    from app.repositories import topic_groups_repository as mod

    session = _Session(_Result(scalar=5))
    monkeypatch.setattr(mod, "write_scope", _scope_of(session))
    await mod.TopicGroupRepository().create_group(
        label="x", vec="[0.1]", embedding_model="m"
    )
    call = session.calls[0]
    assert "embedding_model" in call["sql"] and call["params"]["model"] == "m"


@pytest.mark.asyncio
async def test_cluster_pass_carries_the_hotspot_space() -> None:
    from app.workflows.topic_inspiration import cluster_unassigned_once

    seen: Dict[str, Any] = {}

    class _Repo:
        async def list_unclustered(self, *, window_hours=48, limit=60):
            return [{"id": "1", "title": "t", "vec": "[0.1]", "embedding_model": "m"}]

        async def nearest_group(self, vec, *, window_hours=48, embedding_model=None):
            seen["nearest"] = embedding_model
            return None

        async def create_group(self, *, label, vec, embedding_model=None):
            seen["create"] = embedding_model
            return "9"

        async def assign_hotspot(self, hid, gid):
            return None

    await cluster_unassigned_once(repo=_Repo())
    assert seen == {"nearest": "m", "create": "m"}


# ---------------------------------------------------------------------------
# user interest: writer + reader
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_set_interest_stamps_the_space(monkeypatch) -> None:
    from app.repositories import user_topic_interest_repository as mod

    session = _Session()
    monkeypatch.setattr(mod, "write_scope", _scope_of(session))
    await mod.UserTopicInterestRepository().set_interest(
        "u1", interest_text="x", vec="[0.1]", embedding_model="m"
    )
    call = session.calls[0]
    assert "embedding_model" in call["sql"] and call["params"]["model"] == "m"


def test_set_interest_types_every_vec_parameter() -> None:
    """A bare ``:vec IS NULL`` is untypeable for asyncpg
    (AmbiguousParameterError): PUT /topics/interest failed on every call,
    found by replaying against a real Postgres. Every ``:vec`` must be cast."""
    import inspect
    import re

    from app.repositories import user_topic_interest_repository as mod

    src = inspect.getsource(mod.UserTopicInterestRepository.set_interest)
    binds = len(re.findall(r":vec\b", src))
    assert binds >= 2
    assert src.count("CAST(:vec AS text)") == binds


@pytest.mark.asyncio
async def test_interest_rank_only_compares_within_the_space(monkeypatch) -> None:
    from app.repositories import user_topic_interest_repository as mod

    session = _Session()
    monkeypatch.setattr(mod, "read_scope", _scope_of(session))
    await mod.UserTopicInterestRepository().rank_hotspot_ids("u1")
    assert (
        "(h.embedding_model IS NULL OR me.embedding_model IS NULL "
        "OR h.embedding_model = me.embedding_model)"
    ) in session.calls[0]["sql"]


def _interest_client(monkeypatch, embedder):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import app.api.topics_router as tr

    saved: Dict[str, Any] = {}

    class _Repo:
        async def set_interest(self, user_id, *, interest_text, vec, embedding_model):
            saved.update(text=interest_text, vec=vec, model=embedding_model)

    monkeypatch.setattr(tr, "UserTopicInterestRepository", lambda: _Repo())
    monkeypatch.setattr(tr, "TopicEmbeddingService", lambda: embedder)
    app = FastAPI()
    app.dependency_overrides[tr.get_auth] = lambda: SimpleNamespace(user_id="u1")
    app.include_router(tr.router, prefix="/api/v1")
    return TestClient(app), saved


def test_put_interest_stamps_the_space(monkeypatch) -> None:
    client, saved = _interest_client(monkeypatch, _Embedder([0.1], model="m-3"))
    r = client.put("/api/v1/topics/interest", json={"interest_text": "ai"})
    assert r.status_code == 200 and r.json()["has_embedding"] is True
    assert saved["model"] == "m-3"


def test_put_interest_reports_a_dimension_mismatch(monkeypatch) -> None:
    """The text is still saved (the keyword filter works without a vector),
    but the response says why there is no vector instead of looking like an
    unconfigured provider."""
    client, saved = _interest_client(monkeypatch, _Embedder(_MISMATCH))
    r = client.put("/api/v1/topics/interest", json={"interest_text": "ai"})
    assert r.status_code == 200
    body = r.json()
    assert body["has_embedding"] is False
    assert body["embed_error"] == "dimension_mismatch"
    assert saved["vec"] is None and saved["model"] is None


# ---------------------------------------------------------------------------
# search: query side
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_hybrid_vector_leg_reports_dimension_mismatch() -> None:
    from app.services.library.search_service import (
        VECTOR_LEG_OUTCOMES,
        SearchService,
    )

    assert "dimension_mismatch" in VECTOR_LEG_OUTCOMES
    svc = SearchService()
    svc.embedding_service = SimpleNamespace(
        try_embed=AsyncMock(return_value=(None, "dimension_mismatch: 2560")),
        model="m",
    )
    hits, outcome = await svc._vector_hits("q", "u-1", 10, 0.4)
    assert hits == [] and outcome == "dimension_mismatch"


@pytest.mark.asyncio
async def test_hybrid_vector_leg_sends_the_query_space() -> None:
    from app.services.library.search_service import SearchService

    svc = SearchService()
    svc.embedding_service = SimpleNamespace(
        try_embed=AsyncMock(return_value=([0.1], None)), model="m-5"
    )
    repo = SimpleNamespace(search_by_embedding=AsyncMock(return_value=[]))
    svc.analysis_repo = repo
    await svc._vector_hits("q", "u-1", 10, 0.4)
    assert repo.search_by_embedding.await_args.kwargs["embedding_model"] == "m-5"


@pytest.mark.asyncio
async def test_semantic_search_sends_the_query_space() -> None:
    from app.services.library.search_service import SearchService

    svc = SearchService()
    svc.embedding_service = SimpleNamespace(
        generate_embedding=AsyncMock(return_value=[0.1]), model="m-6"
    )
    repo = SimpleNamespace(search_by_embedding=AsyncMock(return_value=[]))
    svc.analysis_repo = repo
    await svc.semantic_search("q", user_id="u-1")
    assert repo.search_by_embedding.await_args.kwargs["embedding_model"] == "m-6"


@pytest.mark.asyncio
async def test_find_similar_media_compares_within_the_source_space() -> None:
    from app.services.library.search_service import SearchService

    svc = SearchService()
    repo = SimpleNamespace(
        get_analysis=AsyncMock(
            return_value={"content_embedding": [0.1], "embedding_model": "m-8"}
        ),
        search_by_embedding=AsyncMock(return_value=[]),
    )
    svc.analysis_repo = repo
    await svc.find_similar_media(media_id=1, user_id="u-1")
    assert repo.search_by_embedding.await_args.kwargs["embedding_model"] == "m-8"


@pytest.mark.asyncio
async def test_semantic_router_turns_a_mismatch_into_a_typed_503(monkeypatch) -> None:
    import importlib

    from fastapi import HTTPException

    # ``app.api`` re-exports the APIRouter under the module's name.
    sr = importlib.import_module("app.api.search_router")
    from app.schemas.search import SemanticSearchRequest

    async def _boom(self, **kwargs):
        raise _MISMATCH

    monkeypatch.setattr(sr.SearchService, "semantic_search", _boom)
    with pytest.raises(HTTPException) as ei:
        await sr.semantic_search(
            SemanticSearchRequest(query="q"), SimpleNamespace(user_id="u")
        )
    assert ei.value.status_code == 503
    assert ei.value.detail["code"] == "embedding_dimension_mismatch"
