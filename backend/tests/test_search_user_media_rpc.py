"""Unit tests for the scale Tier-1c user-scoped media search RPCs.

Covers:
  * ``SearchService.search_user_media_text`` — calls ``rpc_user_media_text_search``
    with the right params and maps ``{"rows": [...]}`` -> list.
  * ``SearchService.user_owned_platform_ids`` — calls ``rpc_user_owned_platform_ids``
    and short-circuits on empty input.
  * ``SearchService.hybrid_search`` — query path / analysis fallback / no-query
    filter path / missing-user guard, asserting the RPC field sets.
  * ``search_router.text_search`` — maps RPC rows into the response shape.
  * ``search_router._hydrate_media_by_platform_ids`` — scopes hydration via the
    ownership RPC (no full allowlist).
"""

from __future__ import annotations

import importlib
from typing import Any, Dict, List

import pytest

from app.schemas.search import TextSearchRequest
from app.services.library.search_service import SearchService

# NOTE: ``app/api/__init__.py`` rebinds the package attribute
# ``app.api.search_router`` to the APIRouter instance, so a plain
# ``import app.api.search_router`` would resolve to the router, not the module.
# Pull the real module out of sys.modules via importlib instead.
search_router = importlib.import_module("app.api.search_router")


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class _FakeRpcResult:
    def __init__(self, data: Any) -> None:
        self.data = data


class _ScalarResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar(self) -> Any:
        return self._value


class _FakeSession:
    """Captures ``session.execute(text(...), params)`` and returns a preset
    scalar keyed by whichever RPC function name appears in the rendered SQL."""

    def __init__(self, responses: Dict[str, Any] | None = None) -> None:
        self.calls: List[tuple[str, Dict[str, Any]]] = []
        self.responses: Dict[str, Any] = responses or {}

    async def execute(self, statement: Any, params: Any = None) -> _ScalarResult:
        sql = str(statement)
        self.calls.append((sql, params or {}))
        for name, resp in self.responses.items():
            if name in sql:
                return _ScalarResult(resp)
        return _ScalarResult(None)


def _service_with_session(monkeypatch, session: _FakeSession) -> SearchService:
    from contextlib import asynccontextmanager

    import app.services.library.search_service as search_mod

    @asynccontextmanager
    async def _scope():
        yield session

    # search_service imports read_scope at module top level, so patch it there.
    monkeypatch.setattr(search_mod, "read_scope", _scope)
    return SearchService()


class _Auth:
    def __init__(self, user_id: str) -> None:
        self.user_id = user_id


# ---------------------------------------------------------------------------
# search_user_media_text
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_search_user_media_text_calls_rpc_and_maps_rows(monkeypatch) -> None:
    rows = [{"id": 1, "platform_id": "p1"}, {"id": 2, "platform_id": "p2"}]
    session = _FakeSession({"rpc_user_media_text_search": {"rows": rows}})
    svc = _service_with_session(monkeypatch, session)

    out = await svc.search_user_media_text(
        user_id="u-123",
        pattern="%foo%",
        fields=["title", "notes"],
        author="alice",
        date_from="2026-01-01",
        date_to="2026-02-01",
        tag_ids=["10", "20"],
        limit=500,
    )

    assert out == rows
    assert len(session.calls) == 1
    sql, params = session.calls[0]
    assert "rpc_user_media_text_search" in sql
    assert params == {
        "p_user_id": "u-123",
        "p_pattern": "%foo%",
        "p_fields": ["title", "notes"],
        "p_author": "alice",
        "p_date_from": "2026-01-01",
        "p_date_to": "2026-02-01",
        "p_tag_ids": ["10", "20"],
        "p_limit": 500,
    }


@pytest.mark.asyncio
async def test_search_user_media_text_empty_payload_returns_empty_list(
    monkeypatch,
) -> None:
    session = _FakeSession({"rpc_user_media_text_search": None})
    svc = _service_with_session(monkeypatch, session)
    out = await svc.search_user_media_text(user_id="u", pattern=None, fields=[])
    assert out == []
    # match-all path passes NULL pattern + NULL tag_ids through.
    _, params = session.calls[0]
    assert params["p_pattern"] is None
    assert params["p_tag_ids"] is None


# ---------------------------------------------------------------------------
# user_owned_platform_ids
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_user_owned_platform_ids_calls_rpc(monkeypatch) -> None:
    session = _FakeSession({"rpc_user_owned_platform_ids": ["p1", "p3"]})
    svc = _service_with_session(monkeypatch, session)
    out = await svc.user_owned_platform_ids("u-9", ["p1", "p2", "p3"])
    assert out == ["p1", "p3"]
    sql, params = session.calls[0]
    assert "rpc_user_owned_platform_ids" in sql
    assert params == {"p_user_id": "u-9", "p_platform_ids": ["p1", "p2", "p3"]}


@pytest.mark.asyncio
async def test_user_owned_platform_ids_empty_input_short_circuits(monkeypatch) -> None:
    session = _FakeSession()
    svc = _service_with_session(monkeypatch, session)
    out = await svc.user_owned_platform_ids("u-9", [])
    assert out == []
    assert session.calls == []  # never hit the RPC


# ---------------------------------------------------------------------------
# hybrid_search
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_hybrid_search_query_path_uses_basic_fields(monkeypatch) -> None:
    svc = SearchService()
    captured: List[Dict[str, Any]] = []

    async def _fake_text(**kwargs):
        captured.append(kwargs)
        return [
            {"id": 5, "platform_id": "px", "title": "T", "cover_urls": ["c"]},
        ]

    monkeypatch.setattr(svc, "search_user_media_text", _fake_text)

    resp = await svc.hybrid_search(
        query="31 岁",
        tag_ids=["7"],
        author="bob",
        date_from="2026-01-01",
        date_to=None,
        user_id="u-1",
    )

    assert resp.search_type == "hybrid"
    assert resp.total == 1
    assert resp.results[0].media_id == 5
    assert resp.results[0].similarity == 0.5  # text-match score
    # CJK spaces stripped -> "%31岁%"; default scope; filters forwarded.
    # Asserted against the shared constant so widening the default set is a
    # one-line change rather than a hunt through hardcoded lists.
    from app.schemas.search import DEFAULT_SEARCH_FIELDS

    assert len(captured) == 1
    call = captured[0]
    assert call["pattern"] == "%31岁%"
    assert call["fields"] == list(DEFAULT_SEARCH_FIELDS)
    assert call["author"] == "bob"
    assert call["tag_ids"] == ["7"]


@pytest.mark.asyncio
async def test_hybrid_search_falls_back_to_analysis_when_basic_empty(
    monkeypatch,
) -> None:
    svc = SearchService()
    captured: List[Dict[str, Any]] = []

    async def _fake_text(**kwargs):
        captured.append(kwargs)
        # First (basic) call empty, second (analysis) call returns a hit.
        if kwargs["fields"] == ["analysis"]:
            return [{"id": 9, "platform_id": "pa"}]
        return []

    monkeypatch.setattr(svc, "search_user_media_text", _fake_text)

    resp = await svc.hybrid_search(query="cat", user_id="u-2")

    assert resp.total == 1
    assert resp.results[0].media_id == 9
    from app.schemas.search import DEFAULT_SEARCH_FIELDS

    assert [c["fields"] for c in captured] == [
        list(DEFAULT_SEARCH_FIELDS),
        ["analysis"],
    ]


@pytest.mark.asyncio
async def test_hybrid_search_no_query_uses_match_all(monkeypatch) -> None:
    svc = SearchService()
    captured: List[Dict[str, Any]] = []

    async def _fake_text(**kwargs):
        captured.append(kwargs)
        return [{"id": 3, "platform_id": "pn"}]

    monkeypatch.setattr(svc, "search_user_media_text", _fake_text)

    resp = await svc.hybrid_search(query="", tag_ids=["1"], user_id="u-3")

    assert resp.total == 1
    assert resp.results[0].similarity == 1.0  # no semantic ranking
    assert len(captured) == 1
    assert captured[0]["pattern"] is None  # match-all
    assert captured[0]["fields"] == []
    assert captured[0]["tag_ids"] == ["1"]


@pytest.mark.asyncio
async def test_hybrid_search_without_user_returns_empty(monkeypatch) -> None:
    svc = SearchService()

    async def _fail(**kwargs):  # pragma: no cover - must not be called
        raise AssertionError("RPC must not run without a user_id")

    monkeypatch.setattr(svc, "search_user_media_text", _fail)

    resp = await svc.hybrid_search(query="x", user_id=None)
    assert resp.results == []
    assert resp.total == 0


# ---------------------------------------------------------------------------
# router: text_search mapping
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_text_search_router_maps_rpc_rows(monkeypatch) -> None:
    rows = [
        {
            "id": 100,
            "platform_id": "abc",
            "title": "Memory lane",
            "description": "d",
            "cover_urls": ["http://c/1.jpg"],
            "author": "ann",
            "like_count": 42,
            "created_at": "2026-06-01T00:00:00+00:00",
            "resource_id": "555",
            "transcript_status": "completed",
        }
    ]

    async def _fake_text(self, **kwargs):
        # Caller builds a SQL ILIKE pattern (% wildcards) and forwards fields.
        assert kwargs["pattern"] == "%memory%"
        assert kwargs["fields"] == ["title", "description"]
        assert kwargs["user_id"] == "u-77"
        return rows

    monkeypatch.setattr(SearchService, "search_user_media_text", _fake_text)

    req = TextSearchRequest(query="memory", fields=["title", "description"], limit=1000)
    resp = await search_router.text_search(req, _Auth("u-77"))

    assert resp.search_type == "text"
    assert resp.total == 1
    assert resp.videos == rows  # full card rows passed through unchanged
    item = resp.results[0]
    assert item.media_id == 100
    assert item.platform_id == "abc"
    assert item.cover_url == "http://c/1.jpg"
    assert item.view_count == 42  # mapped from like_count
    assert item.similarity_score == 1.0


@pytest.mark.asyncio
async def test_text_search_router_empty_fields_returns_empty(monkeypatch) -> None:
    async def _fail(self, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("RPC must not run with empty fields")

    monkeypatch.setattr(SearchService, "search_user_media_text", _fail)

    req = TextSearchRequest(query="memory", fields=[], limit=1000)
    resp = await search_router.text_search(req, _Auth("u-1"))
    assert resp.total == 0
    assert resp.results == []


# ---------------------------------------------------------------------------
# router: hydration scoped via ownership RPC
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_hydrate_scopes_via_ownership_rpc(monkeypatch) -> None:
    # parsed_media fetch returns two ParsedMedia rows; user only owns one
    # platform_id. The endpoint now reads via an ORM read_scope() session and
    # maps each ORM row through _pm_card_dict, so stub that boundary.
    from contextlib import asynccontextmanager

    class _PMObj:
        """A ParsedMedia stand-in: id/platform_id set, every other CARD
        column resolves to None so _pm_card_dict can project the row."""

        def __init__(self, **kw):
            self.__dict__.update(kw)

        def __getattr__(self, _name):
            return None

    pm_objs = [_PMObj(id=1, platform_id="p1"), _PMObj(id=2, platform_id="p2")]

    class _Result:
        def scalars(self):
            return self

        def all(self):
            return pm_objs

    class _Session:
        async def execute(self, _stmt):
            return _Result()

    @asynccontextmanager
    async def _read_scope():
        yield _Session()

    import app.db.session as db_session_mod

    monkeypatch.setattr(db_session_mod, "read_scope", _read_scope)

    async def _fake_owned(self, user_id, platform_ids):
        assert platform_ids == ["p1", "p2"]
        return ["p1"]  # user owns only p1

    monkeypatch.setattr(SearchService, "user_owned_platform_ids", _fake_owned)

    async def _fake_resource_map(user_id, media_ids):
        return {1: {"resource_id": "r1", "transcript_status": "completed"}}

    monkeypatch.setattr(
        search_router, "_fetch_user_resources_by_media_id", _fake_resource_map
    )

    out = await search_router._hydrate_media_by_platform_ids(
        ["p1", "p2"], user_id="u-5"
    )

    assert [r["platform_id"] for r in out] == ["p1"]  # p2 filtered out
    assert out[0]["resource_id"] == "r1"  # per-user decoration merged


# ---------------------------------------------------------------------------
# Search scope wiring (2026-09-11 fix)
#
# Two defects this section pins:
#   1. ``DEFAULT_SEARCH_FIELDS`` dropped ``tags`` / ``notes`` even though the
#      search box placeholder promises "title, tags, notes". A user who never
#      opened the scope picker could not find anything by tag.
#   2. ``hybrid_search`` hardcoded the four basic fields, so the scope the user
#      picked was silently discarded on the Smart Search path.
# ---------------------------------------------------------------------------
def test_default_search_fields_match_the_placeholder_promise() -> None:
    """The box says "title, tags, notes" — the default scope must deliver it."""
    from app.schemas.search import DEFAULT_SEARCH_FIELDS

    for promised in ("title", "tags", "notes"):
        assert promised in DEFAULT_SEARCH_FIELDS


def test_hybrid_request_defaults_to_the_shared_default_scope() -> None:
    from app.schemas.search import DEFAULT_SEARCH_FIELDS, HybridSearchRequest

    assert HybridSearchRequest(query="x").fields == list(DEFAULT_SEARCH_FIELDS)


@pytest.mark.asyncio
async def test_hybrid_search_forwards_caller_fields(monkeypatch) -> None:
    """Smart Search must honour the scope checkboxes, not overrule them."""
    svc = SearchService()
    captured: List[Dict[str, Any]] = []

    async def _fake_text(**kwargs):
        captured.append(kwargs)
        return [{"id": 1, "platform_id": "p1", "title": "T"}]

    monkeypatch.setattr(svc, "search_user_media_text", _fake_text)

    await svc.hybrid_search(query="krea", user_id="u-1", fields=["title", "tags"])

    assert captured[0]["fields"] == ["title", "tags"]


@pytest.mark.asyncio
async def test_hybrid_search_without_fields_uses_default_scope(monkeypatch) -> None:
    from app.schemas.search import DEFAULT_SEARCH_FIELDS

    svc = SearchService()
    captured: List[Dict[str, Any]] = []

    async def _fake_text(**kwargs):
        captured.append(kwargs)
        return [{"id": 1, "platform_id": "p1"}]

    monkeypatch.setattr(svc, "search_user_media_text", _fake_text)

    await svc.hybrid_search(query="krea", user_id="u-1")

    assert captured[0]["fields"] == list(DEFAULT_SEARCH_FIELDS)


@pytest.mark.asyncio
async def test_semantic_search_scopes_embedding_rpc_to_the_user(monkeypatch) -> None:
    """``match_videos_by_embedding`` used to scan every user's analysis rows.

    The router filtered the leak away afterwards, but the un-scoped RPC still
    burned the whole ``limit`` on other people's rows, so a user with few
    embeddings got an empty page instead of their own matches.
    """
    from app.repositories.analysis_repository import AnalysisRepository

    session = _FakeSession()
    repo = AnalysisRepository()

    from contextlib import asynccontextmanager

    import app.repositories.analysis_repository as analysis_mod

    @asynccontextmanager
    async def _scope():
        yield session

    monkeypatch.setattr(analysis_mod, "read_scope", _scope)

    class _Mappings:
        def all(self):
            return []

    monkeypatch.setattr(
        _ScalarResult, "mappings", lambda self: _Mappings(), raising=False
    )

    await repo.search_by_embedding([0.1, 0.2], limit=5, threshold=0.3, user_id="u-7")

    sql, params = session.calls[0]
    assert "match_videos_by_embedding" in sql
    assert params["u"] == "u-7"


# ---------------------------------------------------------------------------
# Router → service scope/ownership wiring (2026-09-11 fix)
#
# The service-level tests above prove ``hybrid_search`` HONOURS a scope it is
# handed, and the repository test proves ``search_by_embedding`` scopes to the
# user id it is handed. Neither says anything about whether the endpoint
# actually hands them over — and "the request carried it but the router
# dropped it" is precisely the shape of the two defects being fixed. So the
# forwarding is pinned at every boundary the value crosses.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_hybrid_router_forwards_the_requested_scope(monkeypatch) -> None:
    """POST /search/hybrid must pass ``request.fields`` down to the service."""
    from app.schemas.search import DEFAULT_SEARCH_FIELDS, HybridSearchRequest
    from app.services.library.search_service import SearchResponse as SvcResponse

    captured: List[Dict[str, Any]] = []

    async def _fake_hybrid(self, **kwargs):
        captured.append(kwargs)
        return SvcResponse(results=[], total=0, query="q", search_type="hybrid")

    async def _no_hydrate(platform_ids, user_id=None):
        return []

    monkeypatch.setattr(SearchService, "hybrid_search", _fake_hybrid)
    monkeypatch.setattr(search_router, "_hydrate_media_by_platform_ids", _no_hydrate)

    await search_router.hybrid_search(
        HybridSearchRequest(query="krea", fields=["title", "transcript"]),
        _Auth("u-9"),
    )
    assert captured[0]["fields"] == ["title", "transcript"]
    assert captured[0]["user_id"] == "u-9"

    # ...and a request that names no scope still arrives with the shared
    # default, because the schema fills it in rather than sending None.
    await search_router.hybrid_search(HybridSearchRequest(query="krea"), _Auth("u-9"))
    assert captured[1]["fields"] == list(DEFAULT_SEARCH_FIELDS)


@pytest.mark.asyncio
async def test_similar_router_scopes_the_search_to_the_caller(monkeypatch) -> None:
    """GET /search/similar/{id} must pass the caller's id to the service.

    Without it the embedding RPC ranks every user's analysis rows and spends
    the whole ``limit`` before ownership is considered.
    """
    from app.services.library.search_service import SearchResponse as SvcResponse
    from app.services.library.search_service import SearchResult as SvcResult

    captured: List[Dict[str, Any]] = []

    async def _fake_similar(self, **kwargs):
        captured.append(kwargs)
        return SvcResponse(
            results=[
                SvcResult(
                    media_id=2,
                    platform_id="p2",
                    title="T",
                    description=None,
                    cover_url=None,
                    similarity=0.9,
                )
            ],
            total=1,
            query="similar to media 1",
            search_type="similar",
        )

    monkeypatch.setattr(SearchService, "find_similar_media", _fake_similar)

    resp = await search_router.find_similar_media(_Auth("u-3"), media_id=1)

    assert captured[0]["user_id"] == "u-3"
    assert resp.total == 1


# ---------------------------------------------------------------------------
# Service → repository ownership wiring
# ---------------------------------------------------------------------------
class _FakeAnalysisRepo:
    """Captures ``search_by_embedding`` kwargs; serves one analysis row."""

    def __init__(self, analysis: Dict[str, Any] | None = None) -> None:
        self.calls: List[Dict[str, Any]] = []
        self._analysis = analysis

    async def get_analysis(self, media_id: int) -> Dict[str, Any] | None:
        return self._analysis

    async def search_by_embedding(self, **kwargs) -> List[Dict[str, Any]]:
        self.calls.append(kwargs)
        return []


@pytest.mark.asyncio
async def test_semantic_search_forwards_user_id_to_the_embedding_repo(
    monkeypatch,
) -> None:
    svc = SearchService()
    repo = _FakeAnalysisRepo()
    svc.analysis_repo = repo

    async def _fake_embed(_query):
        return [0.1, 0.2]

    monkeypatch.setattr(svc.embedding_service, "generate_embedding", _fake_embed)

    await svc.semantic_search(query="krea", limit=7, threshold=0.3, user_id="u-4")

    assert repo.calls[0]["user_id"] == "u-4"


@pytest.mark.asyncio
async def test_find_similar_media_forwards_user_id_to_the_embedding_repo() -> None:
    svc = SearchService()
    repo = _FakeAnalysisRepo({"content_embedding": "[0.1, 0.2]"})
    svc.analysis_repo = repo

    await svc.find_similar_media(media_id=1, limit=5, threshold=0.6, user_id="u-6")

    assert repo.calls[0]["user_id"] == "u-6"
    # +1 keeps room for the self-match the caller filters out afterwards.
    assert repo.calls[0]["limit"] == 6


@pytest.mark.asyncio
async def test_search_by_embedding_requires_a_user(monkeypatch) -> None:
    """``user_id`` has no default, by design.

    ``/search/similar`` and ``/search/quick`` do not post-filter the RPC's
    output, so this argument is their only cross-user control. A default meant
    one forgotten keyword anywhere on the path silently restored the global
    scan — the failure is invisible because the response still looks like a
    normal result page.
    """
    from app.repositories.analysis_repository import AnalysisRepository

    with pytest.raises(TypeError):
        await AnalysisRepository().search_by_embedding([0.1, 0.2])


@pytest.mark.asyncio
async def test_search_by_embedding_sends_the_user_as_the_fourth_argument(
    monkeypatch,
) -> None:
    """The 4-arg overload must be the one called, not the dropped 3-arg one."""
    from contextlib import asynccontextmanager

    import app.repositories.analysis_repository as analysis_mod
    from app.repositories.analysis_repository import AnalysisRepository

    session = _FakeSession()

    @asynccontextmanager
    async def _scope():
        yield session

    monkeypatch.setattr(analysis_mod, "read_scope", _scope)

    class _Mappings:
        def all(self):
            return []

    monkeypatch.setattr(
        _ScalarResult, "mappings", lambda self: _Mappings(), raising=False
    )

    await AnalysisRepository().search_by_embedding([0.1, 0.2], user_id="u-42")

    sql, params = session.calls[0]
    assert "CAST(:u AS uuid)" in sql
    assert params["u"] == "u-42"


async def test_embedding_search_degrades_when_migration_has_not_landed(
    monkeypatch,
) -> None:
    """A database still on the 3-arg signature must fail in a way callers can read.

    Migrations and backend code deploy on independent triggers, so the window
    where code is newer than schema is real. Undefined-function raises a typed
    error the router turns into a 503 — returning an empty list here would be
    a transport failure wearing the costume of a valid "nothing matched"
    answer, which is the shape CLAUDE.md rules out.
    """
    from contextlib import asynccontextmanager

    from sqlalchemy.exc import ProgrammingError

    import app.repositories.analysis_repository as analysis_mod
    from app.repositories.analysis_repository import AnalysisRepository

    class _Orig(Exception):
        sqlstate = "42883"

    class _ExplodingSession:
        async def execute(self, *_args, **_kwargs):
            raise ProgrammingError("stmt", {}, _Orig())

    @asynccontextmanager
    async def _scope():
        yield _ExplodingSession()

    monkeypatch.setattr(analysis_mod, "read_scope", _scope)

    from app.repositories.analysis_repository import EmbeddingSearchUnavailable

    with pytest.raises(EmbeddingSearchUnavailable):
        await AnalysisRepository().search_by_embedding([0.1], user_id="u-1")


@pytest.mark.asyncio
async def test_embedding_search_reraises_other_programming_errors(
    monkeypatch,
) -> None:
    """Only 42883 is a deployment-window symptom; the rest are real defects."""
    from contextlib import asynccontextmanager

    from sqlalchemy.exc import ProgrammingError

    import app.repositories.analysis_repository as analysis_mod
    from app.repositories.analysis_repository import AnalysisRepository

    class _Orig(Exception):
        sqlstate = "42703"  # undefined_column

    class _ExplodingSession:
        async def execute(self, *_args, **_kwargs):
            raise ProgrammingError("stmt", {}, _Orig())

    @asynccontextmanager
    async def _scope():
        yield _ExplodingSession()

    monkeypatch.setattr(analysis_mod, "read_scope", _scope)

    with pytest.raises(ProgrammingError):
        await AnalysisRepository().search_by_embedding([0.1], user_id="u-1")


@pytest.mark.asyncio
async def test_embedding_search_detects_the_pgcode_spelling_too(
    monkeypatch,
) -> None:
    """The two tests above pin the guard against an ``orig.sqlstate``.

    asyncpg exposes that attribute; psycopg exposes ``pgcode`` instead, and the
    repository reads both. A guard that only understood the spelling the local
    driver happens to use would look correct in every unit test here and never
    fire in production — a 500 on every search for the whole deployment window,
    which is the one thing it exists to prevent. So the second spelling gets its
    own case rather than riding on the first.
    """
    from contextlib import asynccontextmanager

    from sqlalchemy.exc import ProgrammingError

    import app.repositories.analysis_repository as analysis_mod
    from app.repositories.analysis_repository import AnalysisRepository

    class _Orig(Exception):
        pgcode = "42883"  # no ``sqlstate`` attribute at all

    class _ExplodingSession:
        async def execute(self, *_args, **_kwargs):
            raise ProgrammingError("stmt", {}, _Orig())

    @asynccontextmanager
    async def _scope():
        yield _ExplodingSession()

    monkeypatch.setattr(analysis_mod, "read_scope", _scope)

    from app.repositories.analysis_repository import EmbeddingSearchUnavailable

    with pytest.raises(EmbeddingSearchUnavailable):
        await AnalysisRepository().search_by_embedding([0.1], user_id="u-1")
