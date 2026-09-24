"""Embedding space switching: ``POST /search/vectors/spaces`` (Add Space,
probed), ``POST .../{id}/activate`` (Switch) and ``DELETE .../{id}``.

Called as coroutines with patched collaborators, like
``test_vectors_status_endpoint``. The admin gate is pinned on the routes
(``AdminAuthDep``), not re-tested per call.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any, List

import pytest
from fastapi import HTTPException

from app.core.admin_deps import get_admin_auth
from app.core.embedding_space import EmbeddingDimensionMismatch, SpaceSpec
from app.repositories.resource_embeddings_repository import EmbeddingStoreMissing
from app.schemas.search import CreateSpaceRequest
from app.services.ai.providers.embedding_config import EmbeddingConfig
from app.services.library.embedding_spaces import SpaceCatalogError

search_router = importlib.import_module("app.api.search_router")

_AUTH = SimpleNamespace(user_id="admin-1")
_REQ = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))

_ACTIVE = {
    "id": 3,
    "actual_model": "doubao-embedding-vision-251215",
    "protocol": "ark-multimodal",
    "dims": 2048,
    "modalities": ["image", "text", "video"],
    "instruction_version": "en_keyword_v1",
    "created_at": "2026-09-23T00:00:00+00:00",
}
_CANDIDATE = {
    **_ACTIVE,
    "id": 1234567890123456789,
    "actual_model": "wemm-embedding-2b",
    "protocol": "openai-embeddings-chat",
    "modalities": ["text"],
}
_ACTIVE_SPEC = SpaceSpec(
    actual_model="doubao-embedding-vision-251215",
    dims=2048,
    protocol="ark-multimodal",
    modalities=("image", "text", "video"),
)
_CAND_SPEC = SpaceSpec(
    actual_model="wemm-embedding-2b",
    dims=2048,
    protocol="openai-embeddings-chat",
    modalities=("text",),
)
_CFG = EmbeddingConfig(
    base_url="http://nous-engine:8000/v1",
    api_key="k",
    model="wemm-embedding-2b",
    dimensions=0,
    source="platform",
)


class _Embedder:
    """Stands in for both the active embedder (no cfg) and a candidate."""

    def __init__(self, *, active_spec, probe=None, cand_spec=_CAND_SPEC, cfg=None):
        self.cfg = cfg
        self._active_spec, self._probe, self._cand_spec = (
            active_spec,
            probe,
            cand_spec,
        )

    async def space_spec(self):
        return self._active_spec if self.cfg is None else self._cand_spec

    async def probe(self, text):
        assert self.cfg is not None, "probe must use the candidate's own config"
        if isinstance(self._probe, Exception):
            raise self._probe
        return self._probe


class _SpaceRepo:
    def __init__(self, spaces=None, fail: Exception | None = None):
        self.spaces = {s["id"]: s for s in (spaces or [_ACTIVE, _CANDIDATE])}
        self.fail = fail
        self.created: List[Any] = []
        self.deleted: List[int] = []

    async def get(self, space_id):
        return self.spaces.get(space_id)

    async def get_or_create(self, spec):
        if self.fail is not None:
            raise self.fail
        self.created.append(spec)
        return _CANDIDATE

    async def list_all(self):
        return list(self.spaces.values())

    async def delete(self, space_id):
        self.deleted.append(space_id)
        return self.spaces.pop(space_id, None) is not None


class _EmbRepo:
    def __init__(self, count=0):
        self.count = count
        self.counted: List[int] = []

    async def count_in_space(self, space_id):
        self.counted.append(space_id)
        return self.count

    async def coverage(self, **kwargs):
        return 0, 0

    async def stale_count(self, **kwargs):
        return 0


class _Settings:
    def __init__(self):
        self.writes: List[tuple] = []

    async def upsert_setting(self, key, value, updated_by):
        self.writes.append((key, value, updated_by))
        return {}


def _wire(
    monkeypatch,
    *,
    probe=None,
    cfg_error: SpaceCatalogError | None = None,
    catalog_row=None,
    active_spec=_ACTIVE_SPEC,
    space_repo=None,
    emb_repo=None,
) -> SimpleNamespace:
    space_repo = space_repo or _SpaceRepo()
    emb_repo = emb_repo or _EmbRepo()
    settings = _Settings()
    audits: List[dict] = []
    forgotten: List[int] = []
    made: List[Any] = []

    def _make(cfg=None):
        e = _Embedder(active_spec=active_spec, probe=probe, cfg=cfg)
        made.append(e)
        return e

    async def _config_for(name):
        if cfg_error is not None:
            raise cfg_error
        return _CFG

    async def _row_for(space):
        if isinstance(catalog_row, SpaceCatalogError):
            raise catalog_row
        return catalog_row or {"name": "nous-wemm-embedding-2b", "is_enabled": True}

    async def _audit(**kwargs):
        audits.append(kwargs)

    async def _admin(user_id):
        return True

    async def _catalog_name(actual_model):
        return None

    monkeypatch.setattr(search_router, "EmbeddingService", _make)
    monkeypatch.setattr(search_router, "config_for_catalog_model", _config_for)
    monkeypatch.setattr(search_router, "catalog_row_for_space", _row_for)
    monkeypatch.setattr(search_router, "forget_space_id", forgotten.append)
    monkeypatch.setattr(search_router, "create_audit_log", _audit)
    monkeypatch.setattr(search_router, "is_admin_user", _admin)
    monkeypatch.setattr(search_router, "catalog_name_for", _catalog_name)
    monkeypatch.setattr(
        search_router, "get_embedding_space_repository", lambda: space_repo
    )
    monkeypatch.setattr(
        search_router, "get_resource_embeddings_repository", lambda: emb_repo
    )
    monkeypatch.setattr(
        search_router, "get_system_settings_repository", lambda: settings
    )
    return SimpleNamespace(
        space_repo=space_repo,
        emb_repo=emb_repo,
        settings=settings,
        audits=audits,
        forgotten=forgotten,
        made=made,
    )


def _body(name="nous-wemm-embedding-2b"):
    return CreateSpaceRequest(model_name=name)


# ---------------------------------------------------------------- routes ----
@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/search/vectors/spaces"),
        ("POST", "/search/vectors/spaces/{space_id}/activate"),
        ("DELETE", "/search/vectors/spaces/{space_id}"),
    ],
)
def test_space_mutations_are_admin_only(method, path):
    route = next(
        r for r in search_router.router.routes if r.path == path and method in r.methods
    )
    calls = [d.call for d in route.dependant.dependencies]
    assert get_admin_auth in calls


# ---------------------------------------------------------------- create ----
@pytest.mark.asyncio
async def test_create_probes_then_creates_the_space(monkeypatch):
    w = _wire(monkeypatch, probe=([0.1] * 2048, None))
    out = await search_router.create_vector_space(_body(), _AUTH, _REQ)
    assert out.id == str(_CANDIDATE["id"]), "Snowflake id as a string"
    assert out.actual_model == "wemm-embedding-2b"
    assert w.space_repo.created == [_CAND_SPEC]
    # Probed and created with the candidate's own config, not the active one.
    assert [e.cfg for e in w.made] == [_CFG]
    assert w.audits and w.audits[0]["action"] == "create_embedding_space"


def test_create_answers_201():
    route = next(
        r
        for r in search_router.router.routes
        if r.path == "/search/vectors/spaces" and "POST" in r.methods
    )
    assert route.status_code == 201


@pytest.mark.asyncio
async def test_create_refuses_a_wrong_width_with_both_widths(monkeypatch):
    w = _wire(
        monkeypatch,
        probe=EmbeddingDimensionMismatch(
            expected=2048, got=2560, model="wemm-embedding-4b"
        ),
    )
    with pytest.raises(HTTPException) as exc:
        await search_router.create_vector_space(
            _body("nous-wemm-embedding-4b"), _AUTH, _REQ
        )
    assert exc.value.status_code == 422
    assert exc.value.detail == {
        "code": "dimension_mismatch",
        "expected": 2048,
        "got": 2560,
        "model": "nous-wemm-embedding-4b",
        "message": "nous-wemm-embedding-4b returns 2560 dimensions; the "
        "vector store holds 2048.",
    }
    assert w.space_repo.created == [], "no row for a model that cannot fit"


@pytest.mark.asyncio
async def test_create_reports_an_unreachable_provider_as_502(monkeypatch):
    w = _wire(monkeypatch, probe=(None, "provider_error: ConnectError http://x"))
    with pytest.raises(HTTPException) as exc:
        await search_router.create_vector_space(_body(), _AUTH, _REQ)
    assert exc.value.status_code == 502
    assert exc.value.detail["code"] == "provider_error"
    # Provider text stays in the log.
    assert "http://x" not in str(exc.value.detail)
    assert w.space_repo.created == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code,status",
    [
        ("catalog_model_not_found", 404),
        ("catalog_model_disabled", 409),
        ("not_an_embedding_model", 422),
    ],
)
async def test_create_refuses_a_bad_catalog_pick(monkeypatch, code, status):
    w = _wire(monkeypatch, cfg_error=SpaceCatalogError(code, "x"))
    with pytest.raises(HTTPException) as exc:
        await search_router.create_vector_space(_body(), _AUTH, _REQ)
    assert exc.value.status_code == status and exc.value.detail["code"] == code
    assert w.made == []


@pytest.mark.asyncio
async def test_create_store_missing_is_503(monkeypatch):
    _wire(
        monkeypatch,
        probe=([0.1] * 2048, None),
        space_repo=_SpaceRepo(fail=EmbeddingStoreMissing("499")),
    )
    with pytest.raises(HTTPException) as exc:
        await search_router.create_vector_space(_body(), _AUTH, _REQ)
    assert exc.value.status_code == 503
    assert exc.value.detail["code"] == "vector_store_missing"


# -------------------------------------------------------------- activate ----
@pytest.mark.asyncio
async def test_activate_writes_the_governance_model_with_the_catalog_name(
    monkeypatch,
):
    w = _wire(monkeypatch)
    out = await search_router.activate_vector_space(str(_CANDIDATE["id"]), _AUTH, _REQ)
    assert w.settings.writes == [
        ("ai_module.embedding.model", "nous-wemm-embedding-2b", "admin-1")
    ]
    assert w.audits[0]["action"] == "activate_embedding_space"
    # Answers with the fresh status so the UI re-renders in one round trip.
    assert out.status in ("ok", "unconfigured")


@pytest.mark.asyncio
@pytest.mark.parametrize("space_id", ["999", "not-a-number"])
async def test_activate_unknown_space_is_404(monkeypatch, space_id):
    w = _wire(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        await search_router.activate_vector_space(space_id, _AUTH, _REQ)
    assert exc.value.status_code == 404
    assert exc.value.detail["code"] == "space_not_found"
    assert w.settings.writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code,status",
    [("space_catalog_row_missing", 409), ("catalog_model_disabled", 409)],
)
async def test_activate_refuses_a_space_without_a_usable_catalog_row(
    monkeypatch, code, status
):
    w = _wire(monkeypatch, catalog_row=SpaceCatalogError(code, "x"))
    with pytest.raises(HTTPException) as exc:
        await search_router.activate_vector_space(str(_CANDIDATE["id"]), _AUTH, _REQ)
    assert exc.value.status_code == status and exc.value.detail["code"] == code
    assert w.settings.writes == []


# ---------------------------------------------------------------- delete ----
@pytest.mark.asyncio
async def test_delete_refuses_the_active_space(monkeypatch):
    w = _wire(monkeypatch, emb_repo=_EmbRepo(count=5))
    with pytest.raises(HTTPException) as exc:
        await search_router.delete_vector_space(str(_ACTIVE["id"]), _AUTH, _REQ)
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "space_active"
    assert w.space_repo.deleted == []


@pytest.mark.asyncio
async def test_delete_counts_the_cascaded_vectors_first(monkeypatch):
    w = _wire(monkeypatch, emb_repo=_EmbRepo(count=187))
    out = await search_router.delete_vector_space(str(_CANDIDATE["id"]), _AUTH, _REQ)
    assert out.model_dump() == {
        "deleted": True,
        "space_id": str(_CANDIDATE["id"]),
        "deleted_vectors": 187,
    }
    assert w.emb_repo.counted == [_CANDIDATE["id"]]
    assert w.space_repo.deleted == [_CANDIDATE["id"]]
    assert w.forgotten == [_CANDIDATE["id"]]
    assert w.audits[0]["action"] == "delete_embedding_space"
    assert w.audits[0]["details"]["deleted_vectors"] == 187


@pytest.mark.asyncio
async def test_delete_is_allowed_when_no_embedder_is_active(monkeypatch):
    w = _wire(monkeypatch, active_spec=None)
    await search_router.delete_vector_space(str(_ACTIVE["id"]), _AUTH, _REQ)
    assert w.space_repo.deleted == [_ACTIVE["id"]]


@pytest.mark.asyncio
async def test_delete_unknown_space_is_404(monkeypatch):
    _wire(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        await search_router.delete_vector_space("42", _AUTH, _REQ)
    assert exc.value.status_code == 404
