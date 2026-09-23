"""analyze_l1 writes the semantic layer of ``resource_embeddings`` (mig 499).

The embedding half of the step lives in ``_embed_semantic_layer`` so it can be
driven without the VLM. Pins: the vector lands in the embedder's space with
the composed document's hash; ``store_missing`` is a typed reason; and the
resources read runs under SYSTEM scope when enforcement is on (a DBOS step has
no request scope — CLAUDE.md, and the "DBOS steps writing resources need
system_request_scope" lesson: production enforces, CI does not).
"""

from __future__ import annotations

import inspect
from typing import Any, Dict, List

import pytest

from app.core.embedding_space import SEMANTIC_LAYER, SpaceSpec
from app.db.scope import SYSTEM, current_scope
from app.repositories.resource_embeddings_repository import EmbeddingStoreMissing
from app.services.library.embedding_document import compose_semantic_document
from app.workflows import analyze_l1 as m

_SPEC = SpaceSpec(
    actual_model="m-1", dims=2048, protocol="ark_multimodal", modalities=("text",)
)
_INPUTS: Dict[str, Any] = {
    "title": "T",
    "description": "",
    "tags": [],
    "summary_text": None,
    "transcript_text": None,
    "analysis": {"visual_description": "V"},
}


class _Embedder:
    def __init__(self, result=([0.5], None), spec=_SPEC):
        self.result, self.spec = result, spec
        self.texts: List[str] = []

    async def try_embed(self, text):
        self.texts.append(text)
        return self.result

    async def space_spec(self):
        return self.spec


class _SpaceRepo:
    async def get_or_create(self, spec):
        return {"id": 11}


class _EmbRepo:
    def __init__(self, fail: Exception | None = None):
        self.fail = fail
        self.upserts: List[dict] = []

    async def upsert(self, **kwargs):
        if self.fail is not None:
            raise self.fail
        self.upserts.append(kwargs)


@pytest.fixture
def wired(monkeypatch):
    seen: Dict[str, Any] = {}

    async def _load(resource_id, **kwargs):
        seen["scope"] = current_scope()
        seen["rid"] = resource_id
        return dict(_INPUTS)

    repo = _EmbRepo()
    monkeypatch.setattr(
        "app.services.library.embedding_document.load_semantic_inputs", _load
    )
    monkeypatch.setattr(
        "app.repositories.embedding_space_repository.get_embedding_space_repository",
        lambda: _SpaceRepo(),
    )
    monkeypatch.setattr(
        "app.repositories.resource_embeddings_repository"
        ".get_resource_embeddings_repository",
        lambda: repo,
    )
    return seen, repo


@pytest.mark.asyncio
async def test_vector_lands_in_the_embedders_space(wired, monkeypatch):
    seen, repo = wired
    monkeypatch.setattr(m, "is_enforced", lambda table: False)
    emb = _Embedder()
    ok, reason = await m._embed_semantic_layer(42, emb)
    assert (ok, reason) == (True, None)
    text, source_hash = compose_semantic_document(**_INPUTS)
    assert emb.texts == [text]
    assert repo.upserts == [
        {
            "resource_id": 42,
            "layer": SEMANTIC_LAYER,
            "space_id": 11,
            "embedding": [0.5],
            "source_hash": source_hash,
            "source_text": text,
        }
    ]
    assert seen["rid"] == 42


@pytest.mark.asyncio
async def test_resources_read_runs_under_system_scope_when_enforced(wired, monkeypatch):
    seen, _ = wired
    asked: List[str] = []

    def _enforced(table):
        asked.append(table)
        return True

    monkeypatch.setattr(m, "is_enforced", _enforced)
    await m._embed_semantic_layer(42, _Embedder())
    assert seen["scope"] is SYSTEM
    assert "resources" in asked
    assert current_scope() is None, "scope leaked out of the step helper"


@pytest.mark.asyncio
async def test_flag_off_opens_no_scope(wired, monkeypatch):
    seen, _ = wired
    monkeypatch.setattr(m, "is_enforced", lambda table: False)
    await m._embed_semantic_layer(42, _Embedder())
    assert seen["scope"] is None


@pytest.mark.asyncio
async def test_embedder_reason_is_returned_and_nothing_written(wired, monkeypatch):
    _, repo = wired
    monkeypatch.setattr(m, "is_enforced", lambda table: False)
    ok, reason = await m._embed_semantic_layer(
        42, _Embedder(result=(None, "unconfigured"))
    )
    assert (ok, reason) == (False, "unconfigured")
    assert repo.upserts == []


@pytest.mark.asyncio
async def test_missing_store_is_a_typed_reason(wired, monkeypatch):
    from app.services.ai.providers.embedding_service import classify_embed_reason

    _, repo = wired
    repo.fail = EmbeddingStoreMissing("resource_embeddings missing")
    monkeypatch.setattr(m, "is_enforced", lambda table: False)
    ok, reason = await m._embed_semantic_layer(42, _Embedder())
    assert not ok and classify_embed_reason(reason) == "store_missing"


def test_step_no_longer_writes_the_legacy_column():
    src = inspect.getsource(m.call_analyze_l1)
    assert "update_embedding(" not in src
    assert "_embed_semantic_layer(" in src
