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


class _FakeRpcCall:
    def __init__(self, parent: "_FakeRpcClient", name: str, params: Dict[str, Any]):
        self._parent = parent
        self._name = name
        self._params = params

    async def execute(self) -> _FakeRpcResult:
        self._parent.calls.append((self._name, self._params))
        return _FakeRpcResult(self._parent.responses.get(self._name))


class _FakeRpcClient:
    """Captures ``client.rpc(name, params).execute()`` invocations."""

    def __init__(self, responses: Dict[str, Any] | None = None) -> None:
        self.calls: List[tuple[str, Dict[str, Any]]] = []
        self.responses: Dict[str, Any] = responses or {}

    def rpc(self, name: str, params: Dict[str, Any]) -> _FakeRpcCall:
        return _FakeRpcCall(self, name, params)


def _service_with_client(client: _FakeRpcClient) -> SearchService:
    svc = SearchService()

    async def _get_client():
        return client

    svc._get_client = _get_client  # type: ignore[method-assign]
    return svc


class _Auth:
    def __init__(self, user_id: str) -> None:
        self.user_id = user_id


# ---------------------------------------------------------------------------
# search_user_media_text
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_search_user_media_text_calls_rpc_and_maps_rows() -> None:
    rows = [{"id": 1, "platform_id": "p1"}, {"id": 2, "platform_id": "p2"}]
    client = _FakeRpcClient({"rpc_user_media_text_search": {"rows": rows}})
    svc = _service_with_client(client)

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
    assert len(client.calls) == 1
    name, params = client.calls[0]
    assert name == "rpc_user_media_text_search"
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
async def test_search_user_media_text_empty_payload_returns_empty_list() -> None:
    client = _FakeRpcClient({"rpc_user_media_text_search": None})
    svc = _service_with_client(client)
    out = await svc.search_user_media_text(user_id="u", pattern=None, fields=[])
    assert out == []
    # match-all path passes NULL pattern + NULL tag_ids through.
    _, params = client.calls[0]
    assert params["p_pattern"] is None
    assert params["p_tag_ids"] is None


# ---------------------------------------------------------------------------
# user_owned_platform_ids
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_user_owned_platform_ids_calls_rpc() -> None:
    client = _FakeRpcClient({"rpc_user_owned_platform_ids": ["p1", "p3"]})
    svc = _service_with_client(client)
    out = await svc.user_owned_platform_ids("u-9", ["p1", "p2", "p3"])
    assert out == ["p1", "p3"]
    name, params = client.calls[0]
    assert name == "rpc_user_owned_platform_ids"
    assert params == {"p_user_id": "u-9", "p_platform_ids": ["p1", "p2", "p3"]}


@pytest.mark.asyncio
async def test_user_owned_platform_ids_empty_input_short_circuits() -> None:
    client = _FakeRpcClient()
    svc = _service_with_client(client)
    out = await svc.user_owned_platform_ids("u-9", [])
    assert out == []
    assert client.calls == []  # never hit the RPC


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
    # CJK spaces stripped -> "%31岁%"; basic fields; filters forwarded.
    assert len(captured) == 1
    call = captured[0]
    assert call["pattern"] == "%31岁%"
    assert call["fields"] == ["title", "description", "author", "hashtags"]
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
    assert [c["fields"] for c in captured] == [
        ["title", "description", "author", "hashtags"],
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
    # parsed_media fetch returns two rows; user only owns one platform_id.
    pm_rows = [
        {"id": 1, "platform_id": "p1"},
        {"id": 2, "platform_id": "p2"},
    ]

    class _Q:
        def select(self, *_a, **_k):
            return self

        def in_(self, *_a, **_k):
            return self

        async def execute(self):
            return _FakeRpcResult(pm_rows)

    class _Client:
        def table(self, _name):
            return _Q()

    async def _fake_admin():
        return _Client()

    monkeypatch.setattr(search_router, "get_async_supabase_admin", _fake_admin)

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
