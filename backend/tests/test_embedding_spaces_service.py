"""``app.services.library.embedding_spaces``: catalog row <-> space <->
embedder, and the space-id cache that deleting a space must not poison."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.core.embedding_space import SpaceSpec
from app.services.library import embedding_spaces as mod
from app.services.library import semantic_store as store_mod
from app.services.library.embedding_spaces import SpaceCatalogError

_ROW = {
    "name": "nous-wemm-embedding-2b",
    "type": "embedding",
    "is_enabled": True,
    "actual_model": "wemm-embedding-2b",
}
_PLATFORM = (
    "nous-engine",
    {"api_key": "sk", "base_url": "http://nous-engine:8000/v1", "model": "x"},
    "wemm-embedding-2b",
)


class _Repo:
    def __init__(self, row):
        self.row = row

    async def get_by_name(self, name):
        return self.row

    async def get_by_actual_model(self, actual):
        return self.row


def _patch(row, platform=_PLATFORM):
    return (
        patch.object(mod, "_repo", lambda: _Repo(row)),
        patch(
            "app.services.ai.providers.ai_provider_helpers.resolve_platform_model",
            AsyncMock(return_value=platform),
        ),
    )


@pytest.mark.asyncio
async def test_config_for_catalog_model_builds_the_platform_config():
    a, b = _patch(_ROW)
    with a, b:
        cfg = await mod.config_for_catalog_model("nous-wemm-embedding-2b")
    assert cfg.model == "wemm-embedding-2b"
    assert cfg.base_url == "http://nous-engine:8000/v1" and cfg.api_key == "sk"
    assert cfg.source == "platform"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "row,code",
    [
        (None, "catalog_model_not_found"),
        ({**_ROW, "type": "chat"}, "not_an_embedding_model"),
        ({**_ROW, "is_enabled": False}, "catalog_model_disabled"),
    ],
)
async def test_config_for_catalog_model_refuses_unusable_rows(row, code):
    a, b = _patch(row)
    with a, b, pytest.raises(SpaceCatalogError) as exc:
        await mod.config_for_catalog_model("x")
    assert exc.value.code == code


@pytest.mark.asyncio
async def test_a_space_whose_catalog_row_was_removed_says_so():
    a, b = _patch(None)
    with a, b, pytest.raises(SpaceCatalogError) as exc:
        await mod.service_for_space({"actual_model": "wemm-embedding-2b"})
    assert exc.value.code == "space_catalog_row_missing"


@pytest.mark.asyncio
async def test_service_for_space_embeds_with_the_spaces_own_row():
    a, b = _patch(_ROW)
    with a, b:
        svc = await mod.service_for_space({"actual_model": "wemm-embedding-2b"})
    assert svc.model == "wemm-embedding-2b"
    spec = await svc.space_spec()
    assert spec.protocol == "openai-embeddings-chat"


@pytest.mark.asyncio
async def test_catalog_name_for_is_none_without_a_row():
    with patch.object(mod, "_repo", lambda: _Repo(None)):
        assert await mod.catalog_name_for("gone") is None


# ------------------------------------------------------ space-id cache ----
_SPEC = SpaceSpec(actual_model="m", dims=2048, protocol="p", modalities=("text",))


class _SpaceRepo:
    def __init__(self):
        self.next_id = 10
        self.calls = 0

    async def get_or_create(self, spec):
        self.calls += 1
        return {"id": self.next_id}


class _Embedder:
    async def space_spec(self):
        return _SPEC


def _store(space_repo):
    return store_mod.SemanticStore(
        embedding_service=_Embedder(),
        analysis_repo=None,
        space_repo=space_repo,
        embeddings_repo=None,
    )


@pytest.fixture(autouse=True)
def _clean_cache():
    store_mod._SPACE_ID_CACHE.clear()
    yield
    store_mod._SPACE_ID_CACHE.clear()


@pytest.mark.asyncio
async def test_space_id_is_cached_within_the_ttl():
    repo = _SpaceRepo()
    store = _store(repo)
    assert await store.current_space_id() == 10
    repo.next_id = 11
    assert await store.current_space_id() == 10
    assert repo.calls == 1


@pytest.mark.asyncio
async def test_forget_space_id_drops_a_deleted_space():
    repo = _SpaceRepo()
    store = _store(repo)
    await store.current_space_id()
    mod.forget_space_id(10)
    repo.next_id = 11  # re-created after the delete: a new id
    assert await store.current_space_id() == 11


@pytest.mark.asyncio
async def test_other_workers_re_resolve_after_the_ttl(monkeypatch):
    repo = _SpaceRepo()
    store = _store(repo)
    now = [1000.0]
    monkeypatch.setattr(store_mod.time, "monotonic", lambda: now[0])
    await store.current_space_id()
    repo.next_id = 11
    now[0] += store_mod._SPACE_ID_TTL_S + 1
    assert await store.current_space_id() == 11
