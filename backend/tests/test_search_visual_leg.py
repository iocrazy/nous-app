"""The visual (shot frame) leg of the library search (mig 507, PR 3b).

Pinned: the leg embeds the query under its OWN instruction, reads the shot
store in the embedder's space with ``kind='frame'``, yields ``layer='visual'``
hits that carry the shot; its outcome codes mirror ``vector_leg``
(``unconfigured`` / ``store_missing`` / ``error`` / ``timeout``); the hybrid
merge keeps text first, then the better of semantic / visual per video with
its layer and shot; ``legs`` grows a ``visual`` key only when the leg ran;
``visual_only`` runs the leg by itself.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest

from app.core.embedding_space import SpaceSpec
from app.repositories.resource_embeddings_repository import EmbeddingStoreMissing
from app.services.library import semantic_store as store_mod
from app.services.library.search_service import (
    VISUAL_QUERY_INSTRUCTION,
    SearchResult,
    SearchService,
    _leg_counts,
    visual_query_text,
)

_SPEC = SpaceSpec(
    actual_model="doubao-embedding-vision-251215",
    dims=2048,
    protocol="ark_multimodal",
    modalities=("text", "image", "video"),
)


def _shot_row(media_id: int, sim: float, start: int = 41000, end: int = 52000) -> dict:
    return {
        "resource_id": media_id * 10,
        "media_id": media_id,
        "platform_id": f"p{media_id}",
        "title": f"t{media_id}",
        "description": None,
        "cover_urls": None,
        "author": None,
        "view_count": 0,
        "created_at": None,
        "shot_id": 9_007_199_254_740_993 + media_id,
        "shot_index": 4,
        "start_ms": start,
        "end_ms": end,
        "similarity": sim,
    }


def _doc_row(media_id: int, sim: float) -> dict:
    return {
        "resource_id": media_id * 10,
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
    model = "doubao-embedding-vision-251215"

    def __init__(self, reason=None, slow=False):
        self.texts: List[str] = []
        self.reason = reason
        self.slow = slow

    async def try_embed(self, text):
        self.texts.append(text)
        if self.slow:
            await asyncio.sleep(10)
        if self.reason is not None:
            return None, self.reason
        return [0.1, 0.2], None

    async def space_spec(self):
        return _SPEC


class _SpaceRepo:
    async def get_or_create(self, spec):
        return {"id": 5}


class _Repo:
    def __init__(self, rows=None, fail: Exception | None = None):
        self.rows = rows or []
        self.fail = fail
        self.searches: List[Dict[str, Any]] = []

    async def search(self, **kwargs):
        self.searches.append(kwargs)
        if self.fail is not None:
            raise self.fail
        return self.rows


@pytest.fixture(autouse=True)
def _clear_space_cache():
    store_mod._SPACE_ID_CACHE.clear()
    yield
    store_mod._SPACE_ID_CACHE.clear()


def _svc(*, shots, docs=None, embedder=None) -> SearchService:
    svc = SearchService(
        space_repo=_SpaceRepo(),
        embeddings_repo=docs or _Repo(),
        shot_embeddings_repo=shots,
    )
    svc.embedding_service = embedder or _Embedder()
    return svc


def test_visual_query_carries_its_own_instruction():
    assert (
        visual_query_text("  night street ")
        == VISUAL_QUERY_INSTRUCTION + "night street"
    )
    assert "frame" in VISUAL_QUERY_INSTRUCTION.lower()


@pytest.mark.asyncio
async def test_visual_hits_read_frames_in_the_space_and_carry_the_shot():
    shots = _Repo(rows=[_shot_row(1, 0.62)])
    emb = _Embedder()
    svc = _svc(shots=shots, embedder=emb)
    hits, outcome = await svc._visual_hits(
        "night street", "u-1", limit=7, threshold=0.4
    )
    assert outcome == "ok"
    assert emb.texts == [visual_query_text("night street")]
    assert shots.searches == [
        {
            "embedding": [0.1, 0.2],
            "space_id": 5,
            "kind": "frame",
            "user_id": "u-1",
            "limit": 7,
            "threshold": 0.4,
        }
    ]
    (h,) = hits
    assert h.layer == "visual" and h.media_id == 1 and h.similarity == 0.62
    assert h.shot == {
        "shot_id": 9_007_199_254_740_993 + 1,
        "start_ms": 41000,
        "end_ms": 52000,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "shots, embedder, expected",
    [
        (_Repo(fail=EmbeddingStoreMissing("no 507")), None, "store_missing"),
        (_Repo(fail=RuntimeError("boom")), None, "error"),
        (_Repo(), _Embedder(reason="unconfigured"), "unconfigured"),
        (_Repo(), _Embedder(reason="provider_error: 502"), "embed_failed"),
    ],
)
async def test_visual_leg_outcomes_mirror_the_vector_leg(shots, embedder, expected):
    svc = _svc(shots=shots, embedder=embedder)
    hits, outcome = await svc._visual_hits("q", "u-1", limit=5, threshold=0.4)
    assert hits == [] and outcome == expected


@pytest.mark.asyncio
async def test_visual_leg_timeout_is_typed(monkeypatch):
    from app.services.library import search_service as ss

    monkeypatch.setattr(ss, "HYBRID_EMBED_TIMEOUT_S", 0.01)
    svc = _svc(shots=_Repo(), embedder=_Embedder(slow=True))
    hits, outcome = await svc._visual_hits("q", "u-1", limit=5, threshold=0.4)
    assert hits == [] and outcome == "timeout"


def test_merge_keeps_the_better_leg_per_video_and_its_shot():
    text = [
        SearchResult(
            media_id=1,
            platform_id="p1",
            title="t1",
            description=None,
            cover_url=None,
            similarity=0.0,
        )
    ]
    semantic = [
        SearchResult(
            media_id=2,
            platform_id="p2",
            title="t2",
            description=None,
            cover_url=None,
            similarity=0.55,
            layer="semantic",
        ),
        SearchResult(
            media_id=3,
            platform_id="p3",
            title="t3",
            description=None,
            cover_url=None,
            similarity=0.70,
            layer="semantic",
        ),
    ]
    visual = [
        SearchResult(
            media_id=2,
            platform_id="p2",
            title="t2",
            description=None,
            cover_url=None,
            similarity=0.80,
            layer="visual",
            shot={"shot_id": 9, "start_ms": 1000, "end_ms": 2000},
        ),
        SearchResult(
            media_id=1,
            platform_id="p1",
            title="t1",
            description=None,
            cover_url=None,
            similarity=0.99,
            layer="visual",
            shot={"shot_id": 8, "start_ms": 0, "end_ms": 500},
        ),
    ]
    merged = SearchService._merge_text_and_vector(text, semantic + visual, 10)
    assert [(m.media_id, m.layer) for m in merged] == [
        (1, "text"),  # text always first, even against a 0.99 visual
        (2, "visual"),  # 0.80 visual beats 0.55 semantic for the same video
        (3, "semantic"),
    ]
    assert merged[1].shot == {"shot_id": 9, "start_ms": 1000, "end_ms": 2000}
    assert merged[0].shot is None and merged[0].similarity == 1.0


def test_legs_key_for_visual_only_when_it_ran():
    r = SearchResult(
        media_id=1,
        platform_id="p",
        title="t",
        description=None,
        cover_url=None,
        similarity=0.5,
        layer="visual",
    )
    assert _leg_counts([r]) == {"text": 0, "semantic": 0, "visual": 1}
    assert _leg_counts([], visual_ran=True) == {"text": 0, "semantic": 0, "visual": 0}
    assert _leg_counts([]) == {"text": 0, "semantic": 0}


@pytest.mark.asyncio
async def test_hybrid_runs_both_vector_legs_and_reports_each(monkeypatch):
    shots = _Repo(rows=[_shot_row(2, 0.8)])
    docs = _Repo(rows=[_doc_row(3, 0.7)])
    svc = _svc(shots=shots, docs=docs)

    async def no_text(**kw):
        return []

    monkeypatch.setattr(svc, "search_user_media_text", no_text)
    resp = await svc.hybrid_search("night street", user_id="u-1", limit=10)
    assert resp.vector_leg == "ok" and resp.visual_leg == "ok"
    assert [(r.media_id, r.layer) for r in resp.results] == [
        (2, "visual"),
        (3, "semantic"),
    ]
    assert resp.results[0].shot["start_ms"] == 41000
    assert resp.legs == {"text": 0, "semantic": 1, "visual": 1}


@pytest.mark.asyncio
async def test_hybrid_skip_rules_apply_to_the_visual_leg_too(monkeypatch):
    shots = _Repo(rows=[_shot_row(2, 0.8)])
    svc = _svc(shots=shots)

    async def no_text(**kw):
        return []

    monkeypatch.setattr(svc, "search_user_media_text", no_text)
    resp = await svc.hybrid_search("q", user_id="u-1", limit=10, author="someone")
    assert resp.vector_leg == "skipped_filters" and resp.visual_leg == "skipped_filters"
    assert shots.searches == []
    assert "visual" not in resp.legs
    empty = await svc.hybrid_search("", user_id="u-1", limit=10)
    assert empty.visual_leg == "skipped_no_query"


@pytest.mark.asyncio
async def test_visual_only_runs_the_leg_alone():
    shots = _Repo(rows=[_shot_row(2, 0.8), _shot_row(4, 0.6)])
    svc = _svc(shots=shots)
    resp = await svc.visual_only("night street", user_id="u-1", limit=1)
    assert resp.search_type == "visual" and resp.visual_leg == "ok"
    assert resp.vector_leg is None
    assert [r.media_id for r in resp.results] == [2] and resp.legs == {"visual": 1}
    none = await svc.visual_only("q", user_id=None, limit=5)
    assert none.visual_leg == "skipped_no_scope" and none.results == []
