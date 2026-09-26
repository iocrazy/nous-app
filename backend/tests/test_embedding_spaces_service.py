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

    async def get_platform_embedding_by_actual_model(self, actual):
        return self.row

    async def list_enabled(self, type_filter=None, viewer_user_id=None, **kw):
        self.listed = (type_filter, viewer_user_id)
        return [self.row] if self.row else []


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
        (
            {**_ROW, "owner_user_id": "11111111-1111-1111-1111-111111111111"},
            "byok_row_not_allowed",
        ),
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


# ----------------------------------------------- platform catalog lookup ----
def test_space_to_catalog_lookup_skips_private_rows_and_prefers_enabled():
    """A personal (BYOK) row must never serve a platform space, and a disabled
    duplicate must not hide an enabled row with the same actual_model."""
    from sqlalchemy.dialects import postgresql

    from app.repositories.nous_model_repository import (
        platform_embedding_by_actual_model_stmt,
    )

    sql = str(
        platform_embedding_by_actual_model_stmt("wemm-embedding-2b").compile(
            dialect=postgresql.dialect()
        )
    )
    assert "nous_models.owner_user_id IS NULL" in sql
    assert "nous_models.type = " in sql
    assert "nous_models.actual_model = " in sql
    order = sql.split("ORDER BY", 1)[1]
    assert order.index("is_enabled DESC") < order.index("sort_order")


@pytest.mark.asyncio
async def test_platform_embedding_models_reads_the_system_view(monkeypatch):
    """Add Space reads the platform provider view's system rows (no viewer:
    platform-wide rows, no personal blacklist), never the table directly."""
    from tests.services.ai.test_platform_provider import Env, catalog_row

    env = Env(monkeypatch)
    env.rows = [
        catalog_row("nous-emb", type="embedding"),
        catalog_row("nous-chat"),
    ]
    out = await mod.platform_embedding_models()
    env.repo.list_enabled_private.assert_awaited_with(None)
    assert [m["name"] for m in out["models"]] == ["nous-emb"]
    assert out["models"][0]["last_test_status"] == "ok"
    assert not {"api_key", "base_url", "actual_provider"} & set(out["models"][0])
    assert out["engine"] is None
    assert out["governance"] == "on"


@pytest.mark.asyncio
async def test_platform_embedding_models_drops_what_the_view_drops(monkeypatch):
    """Governance off / engine no longer listing the service / failed row →
    not offered."""
    from tests.services.ai.test_platform_provider import (
        Env,
        catalog_row,
        engine_row,
        listed,
    )

    env = Env(monkeypatch)
    env.rows = [
        engine_row("nous-gone", "gone-emb", type="embedding"),
        engine_row("nous-here", "here-emb", type="embedding"),
        catalog_row("nous-bad", type="embedding", last_test_status="fail"),
    ]
    env.engine_answers = [listed(("here-emb", False))]
    out = await mod.platform_embedding_models()
    assert [(m["name"], m["last_test_status"]) for m in out["models"]] == [
        ("nous-here", "idle")
    ]
    assert out["engine"]["reachable"] is True

    env.governance = False
    assert (await mod.platform_embedding_models())["models"] == []


# ---------------------------------------------------- active_actual_model ----
class _Gov:
    def __init__(self, values=None, fail=None):
        self.values, self.fail = values or {}, fail

    async def get_value(self, key):
        if self.fail is not None:
            raise self.fail
        return self.values.get(key)


def _active(gov, platform=None, error=None):
    return (
        patch.object(mod, "get_system_settings_repository", lambda: gov),
        patch(
            "app.services.ai.providers.ai_provider_helpers.resolve_platform_model",
            AsyncMock(return_value=platform, side_effect=error),
        ),
    )


@pytest.mark.asyncio
async def test_active_model_resolves_the_governance_catalog_name():
    a, b = _active(_Gov({mod.EMBEDDING_MODEL_SETTING: "nous-wemm"}), _PLATFORM)
    with a, b:
        assert await mod.active_actual_model() == "wemm-embedding-2b"


@pytest.mark.asyncio
async def test_active_model_is_a_manual_model_string_when_not_in_the_catalog():
    a, b = _active(_Gov({mod.EMBEDDING_MODEL_SETTING: "text-embedding-3"}), None)
    with a, b:
        assert await mod.active_actual_model() == "text-embedding-3"


@pytest.mark.asyncio
async def test_active_model_falls_back_to_graph_embedder_like_the_resolver():
    a, b = _active(_Gov({"graph_embedder_model": "nous-wemm"}), _PLATFORM)
    with a, b:
        assert await mod.active_actual_model() == "wemm-embedding-2b"


@pytest.mark.asyncio
async def test_active_model_is_none_only_when_nothing_is_configured():
    a, b = _active(_Gov({mod.EMBEDDING_MODEL_SETTING: "  "}))
    with a, b:
        assert await mod.active_actual_model() is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "gov,error",
    [
        (_Gov(fail=RuntimeError("db down")), None),
        (_Gov({mod.EMBEDDING_MODEL_SETTING: "nous-wemm"}), RuntimeError("disabled")),
    ],
)
async def test_active_model_unknown_is_raised_not_read_as_none(gov, error):
    a, b = _active(gov, error=error)
    with a, b, pytest.raises(mod.ActiveSpaceUnknown):
        await mod.active_actual_model()
