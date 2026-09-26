"""GET /search/vectors/status — what the library UI reads to show vector
coverage (PR 2 / mig 499). Called as a coroutine with patched collaborators,
like ``test_visual_analysis_read_endpoint``. Three statuses, one test each,
plus the not_built layer rule."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any, List

import pytest

from app.core.embedding_space import SEMANTIC_LAYER, SpaceSpec
from app.repositories.resource_embeddings_repository import EmbeddingStoreMissing
from app.services.library.embedding_spaces import VisualSpaceError

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


_CANDIDATE = {
    **_SPACE,
    "id": 9,
    "actual_model": "wemm-embedding-2b",
    "protocol": "openai-embeddings-chat",
    "modalities": ["text"],
}


class _SpaceRepo:
    def __init__(self, fail: Exception | None = None, spaces=None):
        self.fail = fail
        self.spaces = [_SPACE] if spaces is None else spaces

    async def get_or_create(self, spec):
        if self.fail is not None:
            raise self.fail
        return _SPACE

    async def list_all(self):
        if self.fail is not None:
            raise self.fail
        return list(self.spaces)


class _EmbRepo:
    def __init__(
        self,
        covered: int,
        total: int,
        fail: Exception | None = None,
        stale: int = 0,
    ):
        self.covered, self.total, self.fail, self.stale = covered, total, fail, stale
        self.calls: List[dict] = []
        self.stale_calls: List[dict] = []
        # Per-space (covered, stale) overrides; default = the numbers above.
        self.per_space: dict = {}

    async def stale_count(self, **kwargs):
        self.stale_calls.append(kwargs)
        return self.per_space.get(kwargs["space_id"], (None, self.stale))[1]

    async def coverage(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail is not None:
            raise self.fail
        covered = self.per_space.get(kwargs["space_id"], (self.covered, 0))[0]
        return covered, self.total


class _ShotRepo:
    """Visual layer (mig 507): frame vectors per video, counted against the
    caller's VIDEOS."""

    def __init__(self, covered: int = 0, total: int = 0, stale: int = 0, fail=None):
        self.covered, self.total, self.stale, self.fail = covered, total, stale, fail
        self.calls: List[dict] = []

    async def coverage(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail is not None:
            raise self.fail
        return self.covered, self.total

    async def stale_count(self, **kwargs):
        return self.stale


_CATALOG = {
    "doubao-embedding-vision-251215": "nous-doubao-embedding-vision",
    "wemm-embedding-2b": "nous-wemm-embedding-2b",
}


def _wire(
    monkeypatch,
    *,
    spec=_SPEC,
    space_repo=None,
    emb_repo=None,
    admin: bool = False,
    catalog=None,
    shot_repo=None,
) -> Any:
    emb_repo = emb_repo or _EmbRepo(0, 0)
    shot_repo = shot_repo or _ShotRepo()
    monkeypatch.setattr(
        search_router, "get_video_shot_embeddings_repository", lambda: shot_repo
    )
    names = _CATALOG if catalog is None else catalog

    async def _is_admin(user_id):
        return admin

    async def _catalog_name(actual_model):
        return names.get(actual_model)

    async def _follows():
        return True

    async def _resolve_visual():
        # Follows the active space: same row the status resolves, or the
        # same typed refusal when there is nothing to resolve.
        if spec is None:
            raise VisualSpaceError("embedder_unconfigured")
        try:
            return await (space_repo or _SpaceRepo()).get_or_create(spec), None
        except EmbeddingStoreMissing as e:
            raise VisualSpaceError("store_missing", str(e)) from e

    monkeypatch.setattr(search_router, "is_admin_user", _is_admin)
    monkeypatch.setattr(search_router, "catalog_name_for", _catalog_name)

    async def _policy(visual_space):
        return None

    monkeypatch.setattr(search_router, "_shots_policy_status", _policy)
    monkeypatch.setattr(search_router, "visual_follows_active", _follows)
    monkeypatch.setattr(
        search_router, "resolve_visual_space_and_embedder", _resolve_visual
    )
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
        {
            "layer": "semantic",
            "status": "ok",
            "covered": 12,
            "total": 1409,
            "stale": 0,
        },
        {
            "layer": "visual",
            "status": "not_built",
            "covered": 0,
            "total": 0,
            "stale": 0,
        },
        {
            "layer": "transcript",
            "status": "not_built",
            "covered": 0,
            "total": 1409,
            "stale": 0,
        },
    ]
    assert emb.calls == [{"user_id": "u-1", "space_id": 3, "layer": SEMANTIC_LAYER}]


@pytest.mark.asyncio
async def test_ok_reports_stale_vectors_of_the_semantic_layer(monkeypatch):
    from app.services.library.embedding_document import DOC_VERSION

    emb = _wire(monkeypatch, emb_repo=_EmbRepo(12, 1409, stale=5))
    body = (await search_router.vectors_status(_AUTH)).model_dump()
    assert [x["stale"] for x in body["layers"]] == [5, 0, 0]
    assert emb.stale_calls == [
        {
            "user_id": "u-1",
            "space_id": 3,
            "layer": SEMANTIC_LAYER,
            "doc_version": DOC_VERSION,
        }
    ]


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
        "stale": 0,
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
        ("visual", "not_built", 0, 0),
        ("transcript", "not_built", 0, 7),
    ]
    # No space exists to count against; the count must not match any real one.
    assert emb.calls[0]["space_id"] == search_router.NO_SPACE_ID


@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["space", "coverage"])
async def test_store_missing(monkeypatch, where):
    missing = EmbeddingStoreMissing("migration 499 not applied")
    _wire(
        monkeypatch,
        space_repo=_SpaceRepo(fail=missing if where == "space" else None),
        emb_repo=_EmbRepo(0, 0, fail=missing if where == "coverage" else None),
    )
    body = (await search_router.vectors_status(_AUTH)).model_dump()
    assert body == {
        "space": None,
        "status": "store_missing",
        "layers": [],
        "spaces": [],
        "can_manage": False,
        "visual_space": None,
        "visual_status": "store_missing",
        "shots_policy": None,
    }


def test_route_is_registered_before_the_similar_route():
    paths = [r.path for r in search_router.router.routes]
    assert "/search/vectors/status" in paths
    assert paths.index("/search/vectors/status") < paths.index(
        "/search/similar/{media_id}"
    )


@pytest.mark.asyncio
async def test_spaces_lists_active_and_candidate_with_per_space_coverage(
    monkeypatch,
):
    emb = _EmbRepo(12, 200)
    emb.per_space = {3: (12, 2), 9: (150, 0)}
    _wire(
        monkeypatch,
        space_repo=_SpaceRepo(spaces=[_SPACE, _CANDIDATE]),
        emb_repo=emb,
    )
    body = (await search_router.vectors_status(_AUTH)).model_dump()
    spaces = {s["id"]: s for s in body["spaces"]}
    assert set(spaces) == {"3", "9"}, "ids go out as strings"
    assert spaces["3"]["active"] is True and spaces["9"]["active"] is False
    assert spaces["3"]["catalog_name"] == "nous-doubao-embedding-vision"
    assert spaces["9"]["catalog_name"] == "nous-wemm-embedding-2b"
    assert spaces["9"]["protocol"] == "openai-embeddings-chat"
    assert [(x["covered"], x["total"], x["stale"]) for x in spaces["9"]["layers"]][
        0
    ] == (150, 200, 0)
    # The top-level layers stay the ACTIVE space's (old readers).
    assert body["layers"][0]["covered"] == 12 and body["layers"][0]["stale"] == 2
    assert body["space"]["id"] == "3"


@pytest.mark.asyncio
async def test_candidate_without_a_catalog_row_says_so(monkeypatch):
    _wire(
        monkeypatch,
        space_repo=_SpaceRepo(spaces=[_SPACE, _CANDIDATE]),
        catalog={"doubao-embedding-vision-251215": "nous-doubao-embedding-vision"},
    )
    body = (await search_router.vectors_status(_AUTH)).model_dump()
    cand = next(s for s in body["spaces"] if s["id"] == "9")
    assert cand["catalog_name"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("admin", [True, False])
async def test_can_manage_follows_the_admin_role(monkeypatch, admin):
    _wire(monkeypatch, admin=admin)
    body = (await search_router.vectors_status(_AUTH)).model_dump()
    assert body["can_manage"] is admin


@pytest.mark.asyncio
async def test_unconfigured_still_lists_candidate_spaces(monkeypatch):
    _wire(
        monkeypatch,
        spec=None,
        space_repo=_SpaceRepo(spaces=[_CANDIDATE]),
        emb_repo=_EmbRepo(4, 7),
    )
    body = (await search_router.vectors_status(_AUTH)).model_dump()
    assert body["status"] == "unconfigured"
    assert [(s["id"], s["active"]) for s in body["spaces"]] == [("9", False)]


@pytest.mark.asyncio
async def test_active_space_is_listed_even_if_the_listing_misses_it(monkeypatch):
    # get_or_create just made it; a listing that raced it must not drop it.
    _wire(monkeypatch, space_repo=_SpaceRepo(spaces=[]))
    body = (await search_router.vectors_status(_AUTH)).model_dump()
    assert [(s["id"], s["active"]) for s in body["spaces"]] == [("3", True)]


@pytest.mark.asyncio
async def test_visual_layer_row_counts_videos_in_the_space(monkeypatch):
    """The visual row (mig 507) sits between semantic and transcript, counts
    the caller's VIDEOS (its own total) in the same space, and reports its
    own stale count (older cut algorithm)."""
    shot_repo = _ShotRepo(covered=38, total=1409, stale=2)
    _wire(monkeypatch, emb_repo=_EmbRepo(1445, 1445), shot_repo=shot_repo)
    body = (await search_router.vectors_status(_AUTH)).model_dump()
    assert [x["layer"] for x in body["layers"]] == ["semantic", "visual", "transcript"]
    visual = body["layers"][1]
    assert visual == {
        "layer": "visual",
        "status": "ok",
        "covered": 38,
        "total": 1409,
        "stale": 2,
    }
    assert shot_repo.calls[0]["kind"] == "frame"
    assert str(shot_repo.calls[0]["space_id"]) == body["spaces"][0]["id"]
    assert body["spaces"][0]["layers"][1]["covered"] == 38


@pytest.mark.asyncio
async def test_missing_shot_store_leaves_the_semantic_row_alone(monkeypatch):
    """Migration 507 not applied: the visual row is not_built with zero
    totals; the semantic row and the status are unaffected."""
    from app.repositories.resource_embeddings_repository import EmbeddingStoreMissing

    _wire(
        monkeypatch,
        emb_repo=_EmbRepo(12, 20),
        shot_repo=_ShotRepo(fail=EmbeddingStoreMissing("no 507")),
    )
    body = (await search_router.vectors_status(_AUTH)).model_dump()
    assert body["status"] == "ok" and body["layers"][0]["covered"] == 12
    assert body["layers"][1] == {
        "layer": "visual",
        "status": "not_built",
        "covered": 0,
        "total": 0,
        "stale": 0,
    }


@pytest.mark.asyncio
async def test_visual_space_follows_the_active_space_by_default(monkeypatch):
    _wire(monkeypatch, emb_repo=_EmbRepo(1, 1))
    body = (await search_router.vectors_status(_AUTH)).model_dump()
    assert body["visual_status"] == "ok"
    assert body["visual_space"] == {
        "id": "3",
        "actual_model": "doubao-embedding-vision-251215",
        "catalog_name": "nous-doubao-embedding-vision",
        "follows_active": True,
    }
    assert body["spaces"][0]["active"] is True and body["spaces"][0]["visual"] is True


@pytest.mark.asyncio
async def test_unconfigured_reports_the_visual_layer_as_unconfigured_too(monkeypatch):
    _wire(monkeypatch, spec=None, emb_repo=_EmbRepo(0, 7))
    body = (await search_router.vectors_status(_AUTH)).model_dump()
    assert body["status"] == "unconfigured"
    assert body["visual_space"] is None
    assert body["visual_status"] == "embedder_unconfigured"


@pytest.mark.asyncio
async def test_store_missing_reports_the_visual_layer_as_store_missing(monkeypatch):
    _wire(monkeypatch, space_repo=_SpaceRepo(fail=EmbeddingStoreMissing("no 499")))
    body = (await search_router.vectors_status(_AUTH)).model_dump()
    assert (
        body["status"] == "store_missing" and body["visual_status"] == "store_missing"
    )
