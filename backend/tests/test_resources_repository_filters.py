"""Unit tests for the new filter query params on ResourcesRepository.

Covers the PR 1 additions to ``get_resource_items``:
- ``tag_ids``  (AND-semantic intersection on resource_tags)
- ``min_rating`` (gte on resource.rating)
- ``types``   (IN-semantic mime-category filter, plus ``other`` catch-all)
- helper ``_build_mime_or_expr`` pure function

Plus PR 2 additions:
- ``platforms``  (source_platform lookup via parsed_media)
- ``ai_transcribed`` / ``ai_summarized`` / ``ai_analyzed`` (status == completed)
- ``created_after`` / ``created_before`` (UTC date range on resource.created_at)

Plus PR 3 additions:
- ``duration_min`` / ``duration_max`` (gte/lte on resource.duration_seconds)
- ``aspect_ratios`` (API-surface no-op; proved to not leak into the query)

Mirrors the _FakeQuery / _FakeClient fixture style used by
``test_skill_repository.py`` for consistency.
"""

from __future__ import annotations

from datetime import date
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


# ─── PR 2: platforms / ai_status / date_added filters ─────────────────


@pytest.mark.asyncio
async def test_list_resources_platforms_empty_returns_empty(
    repo: ResourcesRepository, fake_query: _FakeQuery
) -> None:
    """No parsed_media rows match → empty result without touching the
    main resource_items query."""

    async def fake_platform_ids(_platforms: list[str]) -> list[str]:
        return []

    repo._resource_ids_for_platforms = fake_platform_ids  # type: ignore[method-assign]

    result = await repo.get_resource_items(
        scope_type="personal",
        scope_id="user-1",
        platforms=["douyin"],
    )
    assert result == []
    table_calls = [c[1] for c in fake_query.calls if c[0] == "table"]
    assert ("resource_items",) not in table_calls


@pytest.mark.asyncio
async def test_list_resources_platforms_applies_in_clause(
    repo: ResourcesRepository, fake_query: _FakeQuery
) -> None:
    """Resolved platform resource ids feed into in_(resource_id, ids)."""

    async def fake_platform_ids(_platforms: list[str]) -> list[str]:
        return ["res-1", "res-2"]

    repo._resource_ids_for_platforms = fake_platform_ids  # type: ignore[method-assign]
    fake_query._data = [{"id": "i1", "resource": {"id": "res-1"}}]

    await repo.get_resource_items(
        scope_type="personal",
        scope_id="user-1",
        platforms=["douyin", "youtube"],
    )
    in_calls = [c[1] for c in fake_query.calls if c[0] == "in_"]
    matched = [c for c in in_calls if c[0] == "resource_id"]
    assert len(matched) == 1
    assert sorted(matched[0][1]) == ["res-1", "res-2"]


@pytest.mark.asyncio
async def test_list_resources_platforms_intersects_with_tag_ids(
    repo: ResourcesRepository, fake_query: _FakeQuery
) -> None:
    """When both tag_ids and platforms filter resource ids, the main
    query should see the intersection."""

    async def fake_tag_ids(_tag_ids: list[str]) -> list[str]:
        return ["res-1", "res-2", "res-3"]

    async def fake_platform_ids(_platforms: list[str]) -> list[str]:
        return ["res-2", "res-3", "res-4"]

    repo._resource_ids_with_all_tags = fake_tag_ids  # type: ignore[method-assign]
    repo._resource_ids_for_platforms = fake_platform_ids  # type: ignore[method-assign]

    await repo.get_resource_items(
        scope_type="personal",
        scope_id="user-1",
        tag_ids=["tag-a"],
        platforms=["douyin"],
    )
    in_calls = [c[1] for c in fake_query.calls if c[0] == "in_"]
    matched = [c for c in in_calls if c[0] == "resource_id"]
    assert len(matched) == 1
    # Intersection is tag-ordered (tag result drives the iteration).
    assert matched[0][1] == ["res-2", "res-3"]


@pytest.mark.asyncio
async def test_list_resources_platforms_intersection_empty_short_circuits(
    repo: ResourcesRepository, fake_query: _FakeQuery
) -> None:
    """Tag filter matches r1, platform filter matches r2 — no overlap,
    main query is skipped."""

    async def fake_tag_ids(_tag_ids: list[str]) -> list[str]:
        return ["res-1"]

    async def fake_platform_ids(_platforms: list[str]) -> list[str]:
        return ["res-2"]

    repo._resource_ids_with_all_tags = fake_tag_ids  # type: ignore[method-assign]
    repo._resource_ids_for_platforms = fake_platform_ids  # type: ignore[method-assign]

    result = await repo.get_resource_items(
        scope_type="personal",
        scope_id="user-1",
        tag_ids=["tag-a"],
        platforms=["douyin"],
    )
    assert result == []
    table_calls = [c[1] for c in fake_query.calls if c[0] == "table"]
    assert ("resource_items",) not in table_calls


@pytest.mark.asyncio
async def test_list_resources_ai_transcribed_adds_completed_filter(
    repo: ResourcesRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    await repo.get_resource_items(
        scope_type="personal", scope_id="user-1", ai_transcribed=True
    )
    eq_values = [c[1] for c in fake_query.calls if c[0] == "eq"]
    assert ("resource.transcript_status", "completed") in eq_values


@pytest.mark.asyncio
async def test_list_resources_ai_flags_false_or_none_are_inactive(
    repo: ResourcesRepository, fake_query: _FakeQuery
) -> None:
    """Only ``True`` applies the filter; ``False`` and ``None`` are no-ops."""
    fake_query._data = []
    await repo.get_resource_items(
        scope_type="personal",
        scope_id="user-1",
        ai_transcribed=False,
        ai_summarized=None,
        ai_analyzed=False,
    )
    eq_values = [c[1] for c in fake_query.calls if c[0] == "eq"]
    assert ("resource.transcript_status", "completed") not in eq_values
    assert ("resource.summary_status", "completed") not in eq_values
    assert ("resource.visual_analysis_status", "completed") not in eq_values


@pytest.mark.asyncio
async def test_list_resources_ai_all_three_flags_apply_together(
    repo: ResourcesRepository, fake_query: _FakeQuery
) -> None:
    """AND semantics: requesting all three AI flags adds all three eq()s."""
    fake_query._data = []
    await repo.get_resource_items(
        scope_type="personal",
        scope_id="user-1",
        ai_transcribed=True,
        ai_summarized=True,
        ai_analyzed=True,
    )
    eq_values = [c[1] for c in fake_query.calls if c[0] == "eq"]
    assert ("resource.transcript_status", "completed") in eq_values
    assert ("resource.summary_status", "completed") in eq_values
    assert ("resource.visual_analysis_status", "completed") in eq_values


@pytest.mark.asyncio
async def test_list_resources_date_range_applies_gte_and_lte(
    repo: ResourcesRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    await repo.get_resource_items(
        scope_type="personal",
        scope_id="user-1",
        created_after=date(2026, 4, 1),
        created_before=date(2026, 4, 20),
    )
    gte_calls = [c[1] for c in fake_query.calls if c[0] == "gte"]
    lte_calls = [c[1] for c in fake_query.calls if c[0] == "lte"]

    after_matches = [c for c in gte_calls if c[0] == "resource.created_at"]
    before_matches = [c for c in lte_calls if c[0] == "resource.created_at"]
    assert len(after_matches) == 1
    assert len(before_matches) == 1
    # Starts at midnight UTC of created_after.
    assert after_matches[0][1].startswith("2026-04-01T00:00:00")
    assert after_matches[0][1].endswith("+00:00")
    # Ends at max microsecond of created_before (23:59:59.999999).
    assert before_matches[0][1].startswith("2026-04-20T23:59:59")
    assert before_matches[0][1].endswith("+00:00")


@pytest.mark.asyncio
async def test_list_resources_date_range_single_bound_only(
    repo: ResourcesRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    await repo.get_resource_items(
        scope_type="personal",
        scope_id="user-1",
        created_after=date(2026, 4, 15),
    )
    gte_calls = [c[1] for c in fake_query.calls if c[0] == "gte"]
    lte_calls = [c[1] for c in fake_query.calls if c[0] == "lte"]
    assert [c for c in gte_calls if c[0] == "resource.created_at"]
    assert [c for c in lte_calls if c[0] == "resource.created_at"] == []


@pytest.mark.asyncio
async def test_list_resources_combined_filters_all_stack(
    repo: ResourcesRepository, fake_query: _FakeQuery
) -> None:
    """Sanity check: tags + rating + types + platforms + AI + dates all
    layer into a single query without stomping on each other."""

    async def fake_tag_ids(_tag_ids: list[str]) -> list[str]:
        return ["res-1", "res-2"]

    async def fake_platform_ids(_platforms: list[str]) -> list[str]:
        return ["res-1", "res-2", "res-3"]

    repo._resource_ids_with_all_tags = fake_tag_ids  # type: ignore[method-assign]
    repo._resource_ids_for_platforms = fake_platform_ids  # type: ignore[method-assign]
    fake_query._data = []

    await repo.get_resource_items(
        scope_type="personal",
        scope_id="user-1",
        tag_ids=["tag-a"],
        min_rating=3,
        types=["video"],
        platforms=["douyin"],
        ai_transcribed=True,
        created_after=date(2026, 4, 1),
    )
    gte_calls = [c[1] for c in fake_query.calls if c[0] == "gte"]
    assert ("resource.rating", 3) in gte_calls
    assert any(c[0] == "resource.created_at" for c in gte_calls)

    eq_values = [c[1] for c in fake_query.calls if c[0] == "eq"]
    assert ("resource.transcript_status", "completed") in eq_values

    in_calls = [c[1] for c in fake_query.calls if c[0] == "in_"]
    matched = [c for c in in_calls if c[0] == "resource_id"]
    assert len(matched) == 1
    # tag ∩ platform = {res-1, res-2}
    assert sorted(matched[0][1]) == ["res-1", "res-2"]

    or_calls = [c for c in fake_query.calls if c[0] == "or_"]
    assert len(or_calls) == 1


# ─── _resource_ids_for_platforms ──────────────────────────────────────


@pytest.mark.asyncio
async def test_resource_ids_for_platforms_empty_input_returns_empty(
    repo: ResourcesRepository,
) -> None:
    assert await repo._resource_ids_for_platforms([]) == []
    assert await repo._resource_ids_for_platforms(["  "]) == []


@pytest.mark.asyncio
async def test_resource_ids_for_platforms_two_step_lookup(
    repo: ResourcesRepository,
) -> None:
    """Step 1: parsed_media ids for the platforms; step 2: resources
    whose media_id is in that set."""

    call_log: List[str] = []

    responses: List[List[Dict[str, str]]] = [
        # parsed_media hits
        [{"id": "m1"}, {"id": "m2"}],
        # resources hits
        [{"id": "r1"}, {"id": "r2"}],
    ]

    class _ScriptedQuery:
        def __init__(self) -> None:
            self._idx = 0

        def __getattr__(self, _name: str):
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
        def table(self, name: str) -> _ScriptedQuery:
            call_log.append(name)
            return scripted

    async def _get_client():
        return _ScriptedClient()

    repo._get_client = _get_client  # type: ignore[method-assign]

    result = await repo._resource_ids_for_platforms(["douyin", "youtube"])
    assert result == ["r1", "r2"]
    # Expect parsed_media probed first, then resources.
    assert call_log == ["parsed_media", "resources"]


@pytest.mark.asyncio
async def test_resource_ids_for_platforms_no_media_short_circuits(
    repo: ResourcesRepository,
) -> None:
    """If parsed_media lookup returns nothing, the resources query must
    not run."""
    call_log: List[str] = []

    class _ScriptedQuery:
        def __init__(self) -> None:
            self._idx = 0

        def __getattr__(self, _name: str):
            def _capture(*_a: Any, **_kw: Any) -> "_ScriptedQuery":
                return self

            return _capture

        async def execute(self) -> Any:
            payload: list[dict[str, str]] = [] if self._idx == 0 else [{"id": "r1"}]
            self._idx += 1

            class _R:
                data = payload

            return _R()

    scripted = _ScriptedQuery()

    class _ScriptedClient:
        def table(self, name: str) -> _ScriptedQuery:
            call_log.append(name)
            return scripted

    async def _get_client():
        return _ScriptedClient()

    repo._get_client = _get_client  # type: ignore[method-assign]

    result = await repo._resource_ids_for_platforms(["douyin"])
    assert result == []
    # Only the first table (parsed_media) should be queried.
    assert call_log == ["parsed_media"]


@pytest.mark.asyncio
async def test_resource_ids_for_platforms_returns_empty_on_exception(
    repo: ResourcesRepository,
) -> None:
    async def _get_client():
        raise RuntimeError("boom")

    repo._get_client = _get_client  # type: ignore[method-assign]

    assert await repo._resource_ids_for_platforms(["douyin"]) == []


# ─── PR 3: duration / aspect filters ───────────────────────────────────


@pytest.mark.asyncio
async def test_list_resources_duration_min_applies_gte(
    repo: ResourcesRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    await repo.get_resource_items(
        scope_type="personal", scope_id="user-1", duration_min=60
    )
    gte_calls = [c[1] for c in fake_query.calls if c[0] == "gte"]
    assert ("resource.duration_seconds", 60) in gte_calls


@pytest.mark.asyncio
async def test_list_resources_duration_max_applies_lte(
    repo: ResourcesRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    await repo.get_resource_items(
        scope_type="personal", scope_id="user-1", duration_max=300
    )
    lte_calls = [c[1] for c in fake_query.calls if c[0] == "lte"]
    assert ("resource.duration_seconds", 300) in lte_calls


@pytest.mark.asyncio
async def test_list_resources_duration_range_applies_both_bounds(
    repo: ResourcesRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    await repo.get_resource_items(
        scope_type="personal",
        scope_id="user-1",
        duration_min=60,
        duration_max=300,
    )
    gte_calls = [c[1] for c in fake_query.calls if c[0] == "gte"]
    lte_calls = [c[1] for c in fake_query.calls if c[0] == "lte"]
    assert ("resource.duration_seconds", 60) in gte_calls
    assert ("resource.duration_seconds", 300) in lte_calls


@pytest.mark.asyncio
async def test_list_resources_duration_none_is_inactive(
    repo: ResourcesRepository, fake_query: _FakeQuery
) -> None:
    """``None`` for both bounds means no duration filter at all."""
    fake_query._data = []
    await repo.get_resource_items(scope_type="personal", scope_id="user-1")
    gte_calls = [c[1] for c in fake_query.calls if c[0] == "gte"]
    lte_calls = [c[1] for c in fake_query.calls if c[0] == "lte"]
    assert [c for c in gte_calls if c[0] == "resource.duration_seconds"] == []
    assert [c for c in lte_calls if c[0] == "resource.duration_seconds"] == []


@pytest.mark.asyncio
async def test_list_resources_duration_coerces_to_int(
    repo: ResourcesRepository, fake_query: _FakeQuery
) -> None:
    """Float-ish inputs are safe: the repository casts to int before
    passing to PostgREST so the wire payload stays predictable."""
    fake_query._data = []
    await repo.get_resource_items(
        scope_type="personal",
        scope_id="user-1",
        duration_min=60.7,  # type: ignore[arg-type]
        duration_max=300.2,  # type: ignore[arg-type]
    )
    gte_calls = [c[1] for c in fake_query.calls if c[0] == "gte"]
    lte_calls = [c[1] for c in fake_query.calls if c[0] == "lte"]
    assert ("resource.duration_seconds", 60) in gte_calls
    assert ("resource.duration_seconds", 300) in lte_calls


@pytest.mark.asyncio
async def test_list_resources_aspect_ratios_is_noop_at_repository(
    repo: ResourcesRepository, fake_query: _FakeQuery
) -> None:
    """Aspect filtering is client-side for now. The parameter must be
    accepted without leaking into any PostgREST call."""
    fake_query._data = []
    await repo.get_resource_items(
        scope_type="personal",
        scope_id="user-1",
        aspect_ratios=["9:16", "16:9"],
    )
    # No or_ clauses should appear (or_ is used for type / mime filters).
    or_calls = [c for c in fake_query.calls if c[0] == "or_"]
    assert or_calls == []
    # No filters reference a "resolution" column either.
    for op in ("eq", "gte", "lte", "in_", "like", "ilike"):
        matches = [c for c in fake_query.calls if c[0] == op]
        for _, args, _kw in matches:
            assert not (args and isinstance(args[0], str) and "resolution" in args[0])


@pytest.mark.asyncio
async def test_list_resources_duration_and_aspect_stack_with_other_filters(
    repo: ResourcesRepository, fake_query: _FakeQuery
) -> None:
    """Sanity: duration + aspect stack on top of the PR 1/2 filters
    without stomping on other clauses."""

    async def fake_tag_ids(_tag_ids: list[str]) -> list[str]:
        return ["res-1"]

    repo._resource_ids_with_all_tags = fake_tag_ids  # type: ignore[method-assign]
    fake_query._data = []

    await repo.get_resource_items(
        scope_type="personal",
        scope_id="user-1",
        tag_ids=["tag-a"],
        min_rating=4,
        duration_min=60,
        duration_max=300,
        aspect_ratios=["9:16"],
        ai_transcribed=True,
    )
    gte_calls = [c[1] for c in fake_query.calls if c[0] == "gte"]
    lte_calls = [c[1] for c in fake_query.calls if c[0] == "lte"]
    eq_values = [c[1] for c in fake_query.calls if c[0] == "eq"]

    assert ("resource.rating", 4) in gte_calls
    assert ("resource.duration_seconds", 60) in gte_calls
    assert ("resource.duration_seconds", 300) in lte_calls
    assert ("resource.transcript_status", "completed") in eq_values


# ─── PR 4: social metric filters (client-side; repo must no-op) ────────


def _no_column_match(
    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]], needle: str
) -> bool:
    """True iff no recorded call's first positional arg mentions needle.

    Used to assert a filter parameter doesn't leak into any PostgREST
    verb's column argument."""
    for op in ("eq", "gte", "lte", "in_", "like", "ilike", "or_"):
        for _, args, _kw in (c for c in calls if c[0] == op):
            if args and isinstance(args[0], str) and needle in args[0]:
                return False
    return True


@pytest.mark.asyncio
async def test_list_resources_social_thresholds_are_noop_at_repository(
    repo: ResourcesRepository, fake_query: _FakeQuery
) -> None:
    """Social-metric thresholds are accepted without leaking into the
    query — filtering runs client-side in useResourcesDisplay."""
    fake_query._data = []
    await repo.get_resource_items(
        scope_type="personal",
        scope_id="user-1",
        min_likes=1000,
        min_comments=50,
        min_favorites=10,
        min_shares=5,
    )
    for needle in ("like_count", "comment_count", "favorite_count", "share_count"):
        assert _no_column_match(
            fake_query.calls, needle
        ), f"Expected no {needle} filter to leak; calls={fake_query.calls}"


@pytest.mark.asyncio
async def test_list_resources_social_combine_or_is_noop_at_repository(
    repo: ResourcesRepository, fake_query: _FakeQuery
) -> None:
    """``social_combine="or"`` must also stay server-silent."""
    fake_query._data = []
    await repo.get_resource_items(
        scope_type="personal",
        scope_id="user-1",
        min_likes=100,
        min_comments=100,
        social_combine="or",
    )
    for needle in ("like_count", "comment_count"):
        assert _no_column_match(fake_query.calls, needle)


@pytest.mark.asyncio
async def test_list_resources_has_comments_flag_is_noop_at_repository(
    repo: ResourcesRepository, fake_query: _FakeQuery
) -> None:
    """``has_comments=True`` is accepted without adding a comment_count
    clause to the PostgREST call."""
    fake_query._data = []
    await repo.get_resource_items(
        scope_type="personal",
        scope_id="user-1",
        has_comments=True,
    )
    assert _no_column_match(fake_query.calls, "comment_count")


@pytest.mark.asyncio
async def test_list_resources_social_params_stack_with_other_filters(
    repo: ResourcesRepository, fake_query: _FakeQuery
) -> None:
    """Passing social thresholds alongside active PR 1/2/3 filters must
    not disturb them."""

    async def fake_tag_ids(_tag_ids: list[str]) -> list[str]:
        return ["res-1"]

    repo._resource_ids_with_all_tags = fake_tag_ids  # type: ignore[method-assign]
    fake_query._data = []

    await repo.get_resource_items(
        scope_type="personal",
        scope_id="user-1",
        tag_ids=["tag-a"],
        min_rating=3,
        duration_min=60,
        min_likes=1000,
        min_comments=100,
        social_combine="and",
        has_comments=True,
    )
    gte_calls = [c[1] for c in fake_query.calls if c[0] == "gte"]
    # PR 1/3 filters still present:
    assert ("resource.rating", 3) in gte_calls
    assert ("resource.duration_seconds", 60) in gte_calls
    # Social filters absent from the wire payload.
    for needle in ("like_count", "comment_count"):
        assert _no_column_match(fake_query.calls, needle)


@pytest.mark.asyncio
async def test_list_resources_social_combine_default_is_and(
    repo: ResourcesRepository, fake_query: _FakeQuery
) -> None:
    """Omitting ``social_combine`` should behave the same as passing
    ``"and"`` — i.e. a no-op at the repository without raising."""
    fake_query._data = []
    # No kwarg → uses default "and".
    await repo.get_resource_items(
        scope_type="personal",
        scope_id="user-1",
        min_likes=10,
    )
    for needle in ("like_count", "comment_count"):
        assert _no_column_match(fake_query.calls, needle)
