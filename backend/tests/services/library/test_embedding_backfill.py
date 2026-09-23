"""Backfill the semantic layer of ``resource_embeddings`` in place.

Since PR 2 (mig 499) the semantic document no longer depends on the VLM
(``embedding_document``), so every candidate is embedded inline: one
embedding call, no analyze_l1 dispatch, no Task Center row. An unchanged
document (same ``source_hash``) is skipped without paying for a call.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.core.embedding_space import SEMANTIC_LAYER
from app.repositories.resource_embeddings_repository import (
    BackfillRow,
    EmbeddingStoreMissing,
)
from app.services.ai.providers.embedding_service import (
    EMBED_REASON_CODES,
    classify_embed_reason,
)
from app.services.library import embedding_backfill as bf
from app.services.library.embedding_document import compose_semantic_document

_INPUTS: Dict[str, Any] = {
    "title": "T",
    "description": "D",
    "tags": ["a"],
    "summary_text": None,
    "transcript_text": None,
    "analysis": None,
}


def _row(rid: int = 7) -> BackfillRow:
    return BackfillRow(
        resource_id=rid,
        media_id=rid * 10,
        platform_id=f"p{rid}",
        title="T",
        description="D",
        has_analysis=False,
    )


class _Repo:
    def __init__(self, existing: dict | None = None, fail: Exception | None = None):
        self.existing = existing
        self.fail = fail
        self.upserts: List[dict] = []
        self.gets: List[tuple] = []

    async def get(self, resource_id, layer, space_id):
        self.gets.append((resource_id, layer, space_id))
        if self.fail is not None:
            raise self.fail
        return self.existing

    async def upsert(self, **kwargs):
        if self.fail is not None:
            raise self.fail
        self.upserts.append(kwargs)


class _Embedder:
    def __init__(self, result=([0.1, 0.2], None)):
        self.result = result
        self.texts: List[str] = []

    async def try_embed(self, text):
        self.texts.append(text)
        return self.result


@pytest.fixture
def inputs(monkeypatch):
    box: Dict[str, Any] = {"value": dict(_INPUTS), "calls": []}

    async def _load(resource_id, **kwargs):
        box["calls"].append((resource_id, kwargs))
        return box["value"]

    monkeypatch.setattr(bf, "load_semantic_inputs", _load)
    return box


@pytest.mark.asyncio
async def test_embed_candidate_writes_the_semantic_layer_of_the_space(inputs):
    repo, emb = _Repo(), _Embedder()
    ok, reason = await bf.embed_candidate(_row(), embedder=emb, space_id=3, repo=repo)
    assert (ok, reason) == (True, None)
    text, source_hash = compose_semantic_document(**_INPUTS)
    assert emb.texts == [text]
    assert repo.upserts == [
        {
            "resource_id": 7,
            "layer": SEMANTIC_LAYER,
            "space_id": 3,
            "embedding": [0.1, 0.2],
            "source_hash": source_hash,
            "source_text": text,
        }
    ]
    assert repo.gets == [(7, SEMANTIC_LAYER, 3)]
    assert inputs["calls"][0][0] == 7


@pytest.mark.asyncio
async def test_unchanged_document_is_not_re_embedded(inputs):
    _, source_hash = compose_semantic_document(**_INPUTS)
    repo, emb = _Repo(existing={"source_hash": source_hash}), _Embedder()
    ok, reason = await bf.embed_candidate(_row(), embedder=emb, space_id=3, repo=repo)
    assert (ok, reason) == (True, None)
    assert emb.texts == [], "same hash: no embedding call, no spend"
    assert repo.upserts == []


@pytest.mark.asyncio
async def test_changed_document_is_re_embedded(inputs):
    repo, emb = _Repo(existing={"source_hash": "stale"}), _Embedder()
    ok, _ = await bf.embed_candidate(_row(), embedder=emb, space_id=3, repo=repo)
    assert ok and len(emb.texts) == 1 and len(repo.upserts) == 1


@pytest.mark.asyncio
async def test_empty_document_is_reported_not_embedded(inputs):
    inputs["value"] = {**_INPUTS, "title": "", "description": "", "tags": []}
    repo, emb = _Repo(), _Embedder()
    ok, reason = await bf.embed_candidate(_row(), embedder=emb, space_id=3, repo=repo)
    assert (ok, reason) == (False, "empty_text")
    assert emb.texts == [] and repo.upserts == []


@pytest.mark.asyncio
async def test_missing_store_is_a_typed_reason(inputs):
    repo = _Repo(fail=EmbeddingStoreMissing("resource_embeddings does not exist"))
    ok, reason = await bf.embed_candidate(
        _row(), embedder=_Embedder(), space_id=3, repo=repo
    )
    assert (ok, reason) == (False, "store_missing")


@pytest.mark.asyncio
async def test_missing_store_on_write_is_a_typed_reason(inputs):
    class _WriteFails(_Repo):
        async def upsert(self, **kwargs):
            raise EmbeddingStoreMissing("gone")

    ok, reason = await bf.embed_candidate(
        _row(), embedder=_Embedder(), space_id=3, repo=_WriteFails()
    )
    assert (ok, reason) == (False, "store_missing")


@pytest.mark.asyncio
async def test_embedder_reason_passes_through(inputs):
    repo = _Repo()
    ok, reason = await bf.embed_candidate(
        _row(),
        embedder=_Embedder((None, "dimension_mismatch: 2560 != 2048")),
        space_id=3,
        repo=repo,
    )
    assert not ok and classify_embed_reason(reason) == "dimension_mismatch"
    assert repo.upserts == []


@pytest.mark.asyncio
async def test_none_without_reason_is_still_a_failure(inputs):
    ok, reason = await bf.embed_candidate(
        _row(), embedder=_Embedder((None, None)), space_id=3, repo=_Repo()
    )
    assert not ok and classify_embed_reason(reason) == "provider_error"


def test_store_missing_is_a_stable_reason_code():
    assert "store_missing" in EMBED_REASON_CODES
    assert classify_embed_reason("store_missing: 42P01") == "store_missing"
