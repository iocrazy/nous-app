"""PR 5 (spec 2026-09-16 §4.5): the LibrarySearch agent tool.

The handler is a thin adapter over ``SearchService.hybrid_search`` plus the
shared ``resource_lookup`` (media_id -> resource_id). It never raises; every
failure comes back as ``{"error": "<short>"}`` for the model to read."""

from __future__ import annotations

from typing import Any

import pytest

from app.db import scope as db_scope
from app.services.ai.tools import library_search_tool as lst
from app.services.library.search_service import SearchResponse, SearchResult

pytestmark = pytest.mark.unit

USER = "11111111-1111-1111-1111-111111111111"
BIG_MEDIA_ID = 9_007_199_254_740_993  # > 2^53: must survive as a string


def _hit(media_id: int, layer: str, score: float, **kw: Any) -> SearchResult:
    return SearchResult(
        media_id=media_id,
        platform_id=kw.get("platform_id", f"p{media_id}"),
        title=kw.get("title", f"Title {media_id}"),
        description=kw.get("description"),
        cover_url=None,
        similarity=score,
        author=kw.get("author", "someone"),
        created_at=kw.get("created_at", "2026-09-01T00:00:00Z"),
        layer=layer,
        shot=kw.get("shot"),
    )


class _FakeSearch:
    def __init__(self, results: list[SearchResult], vector_leg: str = "ok"):
        self.results = results
        self.vector_leg = vector_leg
        self.calls: list[dict] = []
        self.scope_seen: Any = "unset"

    async def hybrid_search(self, query: str, **kw: Any) -> SearchResponse:
        self.calls.append({"query": query, **kw})
        self.scope_seen = db_scope._scope.get()
        res = self.results[: kw.get("limit", 20)]
        counts: dict[str, int] = {}
        for r in res:
            counts[r.layer] = counts.get(r.layer, 0) + 1
        return SearchResponse(
            results=res,
            total=len(res),
            query=query,
            search_type="hybrid",
            vector_leg=self.vector_leg,
            legs=counts,
        )

    async def visual_only(self, query: str, **kw: Any) -> SearchResponse:
        self.calls.append({"query": query, "visual_only": True, **kw})
        self.scope_seen = db_scope._scope.get()
        res = [r for r in self.results if r.layer == "visual"][: kw.get("limit", 20)]
        return SearchResponse(
            results=res,
            total=len(res),
            query=query,
            search_type="visual",
            visual_leg=self.vector_leg,
            legs={"visual": len(res)},
        )

    async def semantic_only(self, query: str, **kw: Any) -> SearchResponse:
        self.calls.append({"query": query, "semantic_only": True, **kw})
        self.scope_seen = db_scope._scope.get()
        res = [r for r in self.results if r.layer == "semantic"][: kw.get("limit", 20)]
        return SearchResponse(
            results=res,
            total=len(res),
            query=query,
            search_type="semantic",
            vector_leg=self.vector_leg,
            legs={"semantic": len(res)},
        )


@pytest.fixture
def lookup(monkeypatch):
    seen: dict[str, Any] = {}

    async def _fake(user_id: str, media_ids: list[int]) -> dict:
        seen["user_id"] = user_id
        seen["media_ids"] = list(media_ids)
        return {
            int(m): {"resource_id": str(m + 1_000_000_000_000_000_000)}
            for m in media_ids
        }

    monkeypatch.setattr(lst, "fetch_user_resources_by_media_id", _fake)
    return seen


def test_spec_shape_is_what_the_model_sees():
    spec = lst.library_search_spec()
    fn = spec["function"]
    assert spec["type"] == "function"
    assert fn["name"] == lst.LIBRARY_SEARCH_TOOL_NAME == "LibrarySearch"
    props = fn["parameters"]["properties"]
    assert fn["parameters"]["required"] == ["query"]
    assert props["layers"]["items"]["enum"] == [
        "text",
        "semantic",
        "visual",
        "camera",
        "transcript",
    ]
    assert props["limit"]["minimum"] == 1
    assert props["limit"]["maximum"] == 20
    assert props["limit"]["default"] == 8
    desc = fn["description"]
    assert "own" in desc and "resource_id" in desc and "shot" in desc


async def test_happy_path_returns_structured_hits_with_string_ids(lookup):
    fake = _FakeSearch([_hit(BIG_MEDIA_ID, "text", 1.0), _hit(7, "semantic", 0.61234)])
    out = await lst.library_search(
        query="handheld tracking shot", user_id=USER, search_service=fake
    )
    assert fake.calls[0]["query"] == "handheld tracking shot"
    assert fake.calls[0]["user_id"] == USER
    assert fake.calls[0]["limit"] == 8
    assert fake.calls[0]["threshold"] == 0.4
    assert lookup["user_id"] == USER
    assert out["query"] == "handheld tracking shot"
    assert out["total"] == 2
    assert out["vector_leg"] == "ok"
    assert out["legs"] == {"text": 1, "semantic": 1}
    assert out["reranked"] is False
    first, second = out["hits"]
    assert first["media_id"] == str(BIG_MEDIA_ID)
    assert first["resource_id"] == str(BIG_MEDIA_ID + 1_000_000_000_000_000_000)
    assert first["layer"] == "text"
    assert first["shot"] is None
    assert second["score"] == pytest.approx(0.6123, abs=1e-4)
    assert set(first) == {
        "resource_id",
        "media_id",
        "platform_id",
        "title",
        "description",
        "layer",
        "score",
        "author",
        "created_at",
        "shot",
    }


async def test_title_and_description_are_capped(lookup):
    fake = _FakeSearch([_hit(1, "text", 1.0, title="x" * 500, description="y" * 900)])
    out = await lst.library_search(query="q", user_id=USER, search_service=fake)
    hit = out["hits"][0]
    assert len(hit["title"]) <= lst.TEXT_MAX_CHARS + 1
    assert hit["title"].endswith("…")
    assert len(hit["description"]) <= lst.TEXT_MAX_CHARS + 1


async def test_text_layer_filter_goes_through_hybrid_and_recounts_legs(lookup):
    fake = _FakeSearch([_hit(1, "text", 1.0), _hit(2, "semantic", 0.5)])
    out = await lst.library_search(
        query="q", layers=["text"], limit=5, user_id=USER, search_service=fake
    )
    assert [h["layer"] for h in out["hits"]] == ["text"]
    assert out["total"] == 1
    # Filtering after the merge would underfill; ask the service for a full page.
    assert fake.calls[0]["limit"] == lst.MAX_LIMIT
    assert "semantic_only" not in fake.calls[0]
    # legs describe what the model is shown, not the pre-filter merge.
    assert out["legs"] == {"text": 1}


async def test_semantic_without_text_skips_the_text_leg(lookup):
    """Through hybrid, 20 text rows fill the page and the vector leg never
    runs (skipped_full_page) — so a semantic-only ask goes straight to it."""
    fake = _FakeSearch(
        [_hit(1, "text", 1.0), _hit(2, "semantic", 0.5)], vector_leg="timeout"
    )
    out = await lst.library_search(
        query="q", layers=["semantic"], limit=5, user_id=USER, search_service=fake
    )
    assert fake.calls[0]["semantic_only"] is True
    assert fake.calls[0]["limit"] == 5
    assert fake.calls[0]["user_id"] == USER
    assert [h["layer"] for h in out["hits"]] == ["semantic"]
    assert out["vector_leg"] == "timeout"
    assert out["legs"] == {"semantic": 1}


async def test_only_unbuilt_layers_call_nothing(lookup):
    fake = _FakeSearch([_hit(1, "text", 1.0)])
    out = await lst.library_search(
        query="q", layers=["camera"], user_id=USER, search_service=fake
    )
    assert fake.calls == []
    assert out["hits"] == [] and out["legs"] == {}


async def test_visual_alone_runs_the_shot_leg_and_carries_the_moment(lookup):
    shot = {"shot_id": BIG_MEDIA_ID + 5, "start_ms": 41000, "end_ms": 52000}
    fake = _FakeSearch(
        [_hit(1, "text", 1.0), _hit(BIG_MEDIA_ID, "visual", 0.62, shot=shot)]
    )
    out = await lst.library_search(
        query="night street", layers=["visual"], user_id=USER, search_service=fake
    )
    assert fake.calls[0]["visual_only"] is True
    assert [h["layer"] for h in out["hits"]] == ["visual"]
    assert out["hits"][0]["shot"] == {
        "shot_id": str(BIG_MEDIA_ID + 5),  # Snowflake survives as a string
        "start_ms": 41000,
        "end_ms": 52000,
    }
    assert out["legs"] == {"visual": 1} and out["vector_leg"] is None


async def test_empty_layers_means_all(lookup):
    fake = _FakeSearch([_hit(1, "text", 1.0), _hit(2, "semantic", 0.5)])
    out = await lst.library_search(
        query="q", layers=[], limit=4, user_id=USER, search_service=fake
    )
    assert fake.calls[0]["limit"] == 4
    assert out["total"] == 2


async def test_long_query_is_truncated_not_rejected(lookup):
    fake = _FakeSearch([])
    out = await lst.library_search(query="x" * 900, user_id=USER, search_service=fake)
    assert len(fake.calls[0]["query"]) == lst.QUERY_MAX_CHARS == 500
    assert out["truncated"] is True
    short = await lst.library_search(query="q", user_id=USER, search_service=fake)
    assert short["truncated"] is False


async def test_layers_not_built_yet_are_accepted_and_empty(lookup):
    fake = _FakeSearch([_hit(1, "text", 1.0)])
    out = await lst.library_search(
        query="q",
        layers=["text", "visual", "camera"],
        user_id=USER,
        search_service=fake,
    )
    assert "error" not in out
    assert [h["layer"] for h in out["hits"]] == ["text"]


@pytest.mark.parametrize(
    "kwargs, fragment",
    [
        ({"query": ""}, "query"),
        ({"query": "   "}, "query"),
        ({"query": 3}, "query"),
        ({"query": "q", "layers": ["bogus"]}, "layer"),
        ({"query": "q", "layers": "text"}, "layer"),
    ],
)
async def test_bad_arguments_return_error_not_raise(lookup, kwargs, fragment):
    fake = _FakeSearch([])
    out = await lst.library_search(user_id=USER, search_service=fake, **kwargs)
    assert fragment in out["error"]
    assert fake.calls == []


@pytest.mark.parametrize(
    "raw, expected", [(0, 1), (99, 20), ("5", 5), ("x", 8), (None, 8)]
)
async def test_limit_is_clamped(lookup, raw, expected):
    fake = _FakeSearch([])
    await lst.library_search(query="q", limit=raw, user_id=USER, search_service=fake)
    assert fake.calls[0]["limit"] == expected


async def test_search_failure_becomes_error_result(lookup):
    class _Boom:
        async def hybrid_search(self, *a, **kw):
            raise RuntimeError("db down")

    out = await lst.library_search(query="q", user_id=USER, search_service=_Boom())
    assert out == {"error": "library search failed: RuntimeError"}


async def test_missing_user_is_an_error():
    out = await lst.library_search(
        query="q", user_id="", search_service=_FakeSearch([])
    )
    assert "error" in out


async def test_runs_under_system_scope_when_resources_enforced(monkeypatch, lookup):
    """The @agent path runs in a DBOS worker with no request scope; with
    SCOPE_ENFORCE_RESOURCES on (production) an unscoped Resources read raises
    UnscopedQueryError. Every query here is filtered by user_id explicitly."""
    monkeypatch.setattr(lst, "is_enforced", lambda table: table == "resources")
    fake = _FakeSearch([_hit(1, "text", 1.0)])
    await lst.library_search(query="q", user_id=USER, search_service=fake)
    assert fake.scope_seen is db_scope.SYSTEM


async def test_handler_factory_binds_user_and_reads_args(lookup):
    fake = _FakeSearch([_hit(1, "semantic", 0.7)])
    handler = lst.make_library_search_handler(USER, search_service=fake)
    out = await handler({"query": "rain", "layers": ["semantic"], "limit": 3})
    assert fake.calls[0]["user_id"] == USER
    assert out["hits"][0]["layer"] == "semantic"


def test_prompts_readme_quotes_the_spec_verbatim():
    """What the model sees is pasted into prompts/README.md; drift there means
    reviewers read a schema the model never gets."""
    import json
    from pathlib import Path

    readme = Path("app/services/ai/prompts/README.md").read_text(encoding="utf-8")
    pasted = json.dumps(lst.library_search_spec(), indent=1, ensure_ascii=False)
    assert pasted in readme
