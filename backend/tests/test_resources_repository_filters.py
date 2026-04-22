"""Unit tests for the new filter query params on ResourcesRepository.

Covers the PR 1 additions to ``get_resource_items``:
- ``tag_ids``  (AND-semantic intersection on resource_tags)
- ``min_rating`` (gte on resource.rating)
- ``types``   (IN-semantic mime-category filter, plus ``other`` catch-all)
- helper ``_build_mime_or_expr`` pure function

Mirrors the _FakeQuery / _FakeClient fixture style used by
``test_skill_repository.py`` for consistency.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.repositories.resources_repository import (
    ResourcesRepository,
    _build_mime_or_expr,
)

# ─── Fake Supabase client/query plumbing ──────────────────────────────


class _FakeQuery:
    def __init__(self) -> None:
        self.calls: List[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self._data: Any = []
        self._raises: Exception | None = None

    def __getattr__(self, name: str):
        def _capture(*args: Any, **kwargs: Any) -> "_FakeQuery":
            self.calls.append((name, args, kwargs))
            return self

        return _capture

    async def execute(self) -> Any:
        if self._raises is not None:
            raise self._raises

        class _R:
            data = self._data

        return _R()


class _FakeClient:
    def __init__(self, query: _FakeQuery) -> None:
        self._query = query

    def table(self, name: str) -> _FakeQuery:
        self._query.calls.append(("table", (name,), {}))
        return self._query


@pytest.fixture
def fake_query() -> _FakeQuery:
    return _FakeQuery()


@pytest.fixture
def repo(fake_query: _FakeQuery) -> ResourcesRepository:
    r = ResourcesRepository()

    async def _get_client():
        return _FakeClient(fake_query)

    r._get_client = _get_client  # type: ignore[method-assign]
    return r


# ─── _build_mime_or_expr (pure helper) ─────────────────────────────────


def test_build_mime_or_expr_empty_returns_none() -> None:
    assert _build_mime_or_expr(None) is None
    assert _build_mime_or_expr([]) is None
    assert _build_mime_or_expr(["  "]) is None


def test_build_mime_or_expr_single_video_category() -> None:
    expr = _build_mime_or_expr(["video"])
    assert expr == "mime_type.like.video/*"


def test_build_mime_or_expr_multiple_categories_joined_by_comma() -> None:
    expr = _build_mime_or_expr(["video", "image"])
    # Order isn't guaranteed (built from a set), but both must appear.
    assert expr is not None
    parts = expr.split(",")
    assert "mime_type.like.video/*" in parts
    assert "mime_type.like.image/*" in parts


def test_build_mime_or_expr_document_expands_to_multiple_clauses() -> None:
    expr = _build_mime_or_expr(["document"])
    assert expr is not None
    assert "mime_type.eq.application/pdf" in expr
    assert "mime_type.like.application/msword*" in expr
    assert "mime_type.like.application/vnd.*" in expr
    assert "mime_type.like.text/*" in expr


def test_build_mime_or_expr_other_is_and_not_of_known_prefixes() -> None:
    """'other' means: none of the known prefixes match."""
    expr = _build_mime_or_expr(["other"])
    assert expr is not None
    assert expr.startswith("and(")
    assert "mime_type.not.like.video/*" in expr
    assert "mime_type.not.like.image/*" in expr
    assert "mime_type.not.like.audio/*" in expr
    assert "mime_type.not.like.text/*" in expr


def test_build_mime_or_expr_ignores_unknown_categories() -> None:
    # Unknown categories are silently dropped; router validates first.
    expr = _build_mime_or_expr(["video", "bogus"])
    assert expr == "mime_type.like.video/*"


# ─── get_resource_items: filter plumbing ───────────────────────────────


@pytest.mark.asyncio
async def test_list_resources_no_filters_baseline(
    repo: ResourcesRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [
        {"id": "i1", "resource": {"id": "r1", "mime_type": "video/mp4"}}
    ]
    items = await repo.get_resource_items(scope_type="personal", scope_id="user-1")
    assert len(items) == 1

    eq_values = [c[1] for c in fake_query.calls if c[0] == "eq"]
    assert ("scope_type", "personal") in eq_values
    assert ("scope_id", "user-1") in eq_values
    # Default behaviour: no trashed resources.
    assert ("resource.is_trashed", False) in eq_values
    # Root folder filter: folder_id IS NULL.
    is_calls = [c[1] for c in fake_query.calls if c[0] == "is_"]
    assert ("folder_id", "null") in is_calls

    # No filter side-effects:
    or_calls = [c for c in fake_query.calls if c[0] == "or_"]
    assert or_calls == []
    gte_calls = [c for c in fake_query.calls if c[0] == "gte"]
    assert gte_calls == []
    in_calls = [c for c in fake_query.calls if c[0] == "in_"]
    assert in_calls == []


@pytest.mark.asyncio
async def test_list_resources_min_rating_applies_gte(
    repo: ResourcesRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    await repo.get_resource_items(
        scope_type="personal", scope_id="user-1", min_rating=4
    )
    gte_calls = [c[1] for c in fake_query.calls if c[0] == "gte"]
    assert ("resource.rating", 4) in gte_calls


@pytest.mark.asyncio
async def test_list_resources_types_adds_or_clause_on_resource_table(
    repo: ResourcesRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    await repo.get_resource_items(
        scope_type="personal",
        scope_id="user-1",
        types=["video"],
    )
    or_calls = [c for c in fake_query.calls if c[0] == "or_"]
    assert len(or_calls) == 1
    args, kwargs = or_calls[0][1], or_calls[0][2]
    assert args[0] == "mime_type.like.video/*"
    # IMPORTANT: embedded-table filter must reference `resources` so
    # PostgREST parses the column name correctly.
    assert kwargs.get("reference_table") == "resources"


@pytest.mark.asyncio
async def test_list_resources_types_skips_or_clause_when_empty(
    repo: ResourcesRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    await repo.get_resource_items(scope_type="personal", scope_id="user-1", types=[])
    assert [c for c in fake_query.calls if c[0] == "or_"] == []


@pytest.mark.asyncio
async def test_list_resources_tag_ids_empty_intersection_short_circuits(
    repo: ResourcesRepository, fake_query: _FakeQuery
) -> None:
    """If no resource carries all requested tags, return [] without
    issuing the main query."""

    async def fake_intersection(tag_ids: list[str]) -> list[str]:
        return []

    repo._resource_ids_with_all_tags = fake_intersection  # type: ignore[method-assign]

    result = await repo.get_resource_items(
        scope_type="personal",
        scope_id="user-1",
        tag_ids=["tag-a"],
    )
    assert result == []
    # Main query was never built.
    table_calls = [c[1] for c in fake_query.calls if c[0] == "table"]
    assert ("resource_items",) not in table_calls


@pytest.mark.asyncio
async def test_list_resources_tag_ids_applies_in_clause(
    repo: ResourcesRepository, fake_query: _FakeQuery
) -> None:
    """Non-empty intersection feeds `in_(resource_id, ids)` into the main query."""

    async def fake_intersection(tag_ids: list[str]) -> list[str]:
        return ["res-1", "res-2"]

    repo._resource_ids_with_all_tags = fake_intersection  # type: ignore[method-assign]
    fake_query._data = [{"id": "i1", "resource": {"id": "res-1"}}]

    await repo.get_resource_items(
        scope_type="personal",
        scope_id="user-1",
        tag_ids=["tag-a", "tag-b"],
    )
    in_calls = [c[1] for c in fake_query.calls if c[0] == "in_"]
    # Order of the intersected set is not guaranteed — compare by content.
    matched = [c for c in in_calls if c[0] == "resource_id"]
    assert len(matched) == 1
    assert sorted(matched[0][1]) == ["res-1", "res-2"]


# ─── _resource_ids_with_all_tags ──────────────────────────────────────


@pytest.mark.asyncio
async def test_resource_ids_with_all_tags_empty_input_returns_empty(
    repo: ResourcesRepository,
) -> None:
    assert await repo._resource_ids_with_all_tags([]) == []


@pytest.mark.asyncio
async def test_resource_ids_with_all_tags_intersects_multiple_lookups(
    repo: ResourcesRepository,
) -> None:
    """Each tag id triggers its own resource_tags query; the helper
    returns the intersection of the resource ids across all queries."""

    # Scripted per-call responses. The fake query is reused across
    # queries, so we swap the data payload before each `.execute()`.
    responses: List[List[Dict[str, str]]] = [
        [{"resource_id": "r1"}, {"resource_id": "r2"}, {"resource_id": "r3"}],
        [{"resource_id": "r2"}, {"resource_id": "r3"}],
        [{"resource_id": "r2"}, {"resource_id": "r9"}],
    ]

    class _ScriptedQuery:
        def __init__(self) -> None:
            self._idx = 0

        def __getattr__(self, name: str):
            def _capture(*_a: Any, **_kw: Any) -> "_ScriptedQuery":
                return self

            return _capture

        async def execute(self) -> Any:
            payload = responses[self._idx]
            self._idx += 1

            class _R:
                data = payload

            return _R()

    scripted = _ScriptedQuery()

    class _ScriptedClient:
        def table(self, _name: str) -> _ScriptedQuery:
            return scripted

    async def _get_client():
        return _ScriptedClient()

    repo._get_client = _get_client  # type: ignore[method-assign]

    result = await repo._resource_ids_with_all_tags(["tag-a", "tag-b", "tag-c"])
    # Only r2 appears in all three tag lookups.
    assert result == ["r2"]


@pytest.mark.asyncio
async def test_resource_ids_with_all_tags_short_circuits_on_empty_intersection(
    repo: ResourcesRepository,
) -> None:
    """Once the running intersection is empty, later tags are skipped."""

    call_count = {"n": 0}

    class _ScriptedQuery:
        def __getattr__(self, name: str):
            def _capture(*_a: Any, **_kw: Any) -> "_ScriptedQuery":
                return self

            return _capture

        async def execute(self) -> Any:
            call_count["n"] += 1
            # First tag has r1, second has only r2 → intersection empty.
            idx = call_count["n"]

            class _R:
                data = [{"resource_id": "r1"}] if idx == 1 else [{"resource_id": "r2"}]

            return _R()

    scripted = _ScriptedQuery()

    class _ScriptedClient:
        def table(self, _name: str) -> _ScriptedQuery:
            return scripted

    async def _get_client():
        return _ScriptedClient()

    repo._get_client = _get_client  # type: ignore[method-assign]

    result = await repo._resource_ids_with_all_tags(["tag-a", "tag-b", "tag-c"])
    assert result == []
    # Third lookup must not run once we know the intersection is empty.
    assert call_count["n"] == 2


@pytest.mark.asyncio
async def test_resource_ids_with_all_tags_returns_empty_on_exception(
    repo: ResourcesRepository,
) -> None:
    async def _get_client():
        raise RuntimeError("boom")

    repo._get_client = _get_client  # type: ignore[method-assign]

    assert await repo._resource_ids_with_all_tags(["tag-a"]) == []
