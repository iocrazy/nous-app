"""Unit tests for the filter query params on ``ResourcesRepository.get_resource_items``.

Post-collapse ``get_resource_items`` is the ORM implementation: it builds a
single dynamic-WHERE SQL string with named bind params and runs it through
ONE ``read_scope()`` session (the tag/platform pre-resolution helpers each
open their own session, so tests patch them out). These tests use a
scope-mock fake session that CAPTURES the built ``(sql, params)`` and assert
the filter semantics are preserved:

- ``tag_ids``  (AND-semantic intersection → ``i.resource_id = ANY(:matched_ids)``)
- ``min_rating`` (``r.rating >= :min_rating``)
- ``types``   (mime-category ``OR`` expression inlined into the WHERE)
- ``platforms``  (source_platform pre-resolution → matched ids)
- ``ai_transcribed`` / ``ai_summarized`` / ``ai_analyzed`` (status == completed)
- ``ai_has_prompt`` (any of 4 prompt columns non-empty, OR predicate)
- ``created_after`` / ``created_before`` (UTC datetime bind on r.created_at)
- ``duration_min`` / ``duration_max`` (bounds on r.duration_seconds)
- ``aspect_ratios`` + social metrics (API-surface no-ops — must NOT leak)

Plus the two pre-resolution helpers ``_resource_ids_with_all_tags`` /
``_resource_ids_for_platforms`` (short-circuits + the SQL/params they bind).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
from typing import Any, Dict, List, Optional

import pytest

from app.repositories import resources_repository as repo_mod
from app.repositories.resources_repository import ResourcesRepository

# ─── Scope-mock fake session plumbing ─────────────────────────────────


class _Mappings:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self._rows = rows

    def all(self) -> List[Dict[str, Any]]:
        return self._rows


class _Result:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _Mappings:
        return _Mappings(self._rows)

    def all(self) -> List[Any]:
        """Bare (non-``.mappings()``) row access — the shape a real ORM
        ``select(col)`` result returns (tuple-like rows indexable by
        position), used by ``_resource_ids_for_platforms`` post Phase C
        task 3."""
        return self._rows


class _CapSession:
    """Records every ``execute(stmt, params)`` as ``(sql_text, params)`` and
    hands back a queued rowset (default empty). ``raw_calls`` additionally
    keeps the RAW statement object (not stringified) for the ORM sites (Phase
    C task 3) whose bind params are compiled into the statement itself rather
    than passed as a separate runtime dict."""

    def __init__(self, rowsets: Optional[List[List[Dict[str, Any]]]] = None) -> None:
        self._rowsets = list(rowsets or [])
        self.calls: List[tuple[str, Dict[str, Any]]] = []
        self.raw_calls: List[Any] = []

    async def execute(self, stmt: Any, params: Any = None) -> _Result:
        self.calls.append((str(stmt), dict(params or {})))
        self.raw_calls.append(stmt)
        rows = self._rowsets.pop(0) if self._rowsets else []
        return _Result(rows)


@asynccontextmanager
async def _fake_scope(session: _CapSession):
    yield session


@pytest.fixture
def cap_session() -> _CapSession:
    return _CapSession()


@pytest.fixture
def repo(
    cap_session: _CapSession, monkeypatch: pytest.MonkeyPatch
) -> ResourcesRepository:
    """Repo whose module-level ``read_scope`` yields the capturing session."""
    monkeypatch.setattr(repo_mod, "read_scope", lambda: _fake_scope(cap_session))
    return ResourcesRepository()


def _main_call(cap_session: _CapSession) -> tuple[str, Dict[str, Any]]:
    """The get_resource_items main query is the LAST captured execute (the
    tag/platform helpers are patched out, so there is exactly one)."""
    assert cap_session.calls, "expected the main query to open a session"
    return cap_session.calls[-1]


# ─── get_resource_items: baseline + folder scoping ────────────────────


@pytest.mark.asyncio
async def test_list_resources_no_filters_baseline(
    repo: ResourcesRepository, cap_session: _CapSession
) -> None:
    await repo.get_resource_items(scope_type="personal", scope_id="user-1")
    sql, params = _main_call(cap_session)

    # scope_id binds; root-folder + non-trashed defaults applied.
    assert params["scope_id"] == "user-1"
    assert "i.scope_id = :scope_id" in sql
    assert "i.folder_id IS NULL" in sql
    assert "r.is_trashed = false" in sql
    # No filter side-effects.
    assert "matched_ids" not in params
    assert "min_rating" not in params
    assert "r.rating" not in sql


@pytest.mark.asyncio
async def test_list_resources_folder_id_binds(
    repo: ResourcesRepository, cap_session: _CapSession
) -> None:
    await repo.get_resource_items(
        scope_type="personal", scope_id="user-1", folder_id="42"
    )
    sql, params = _main_call(cap_session)
    assert "i.folder_id = :folder_id" in sql
    assert params["folder_id"] == 42  # _bigint-coerced


@pytest.mark.asyncio
async def test_list_resources_all_folders_drops_root_predicate(
    repo: ResourcesRepository, cap_session: _CapSession
) -> None:
    """all_folders=True must list scope-wide (no folder predicate at all) —
    the Distribution Publish picker relies on this to see videos filed into
    folders, not just root-level items."""
    await repo.get_resource_items(
        scope_type="personal", scope_id="user-1", all_folders=True
    )
    sql, params = _main_call(cap_session)
    assert "i.folder_id IS NULL" not in sql
    assert "i.folder_id = :folder_id" not in sql
    assert "folder_id" not in params


@pytest.mark.asyncio
async def test_list_resources_folder_id_wins_over_all_folders(
    repo: ResourcesRepository, cap_session: _CapSession
) -> None:
    """An explicit folder_id still narrows to that folder even when
    all_folders is (nonsensically) passed alongside it."""
    await repo.get_resource_items(
        scope_type="personal", scope_id="user-1", folder_id="42", all_folders=True
    )
    sql, params = _main_call(cap_session)
    assert "i.folder_id = :folder_id" in sql
    assert params["folder_id"] == 42


@pytest.mark.asyncio
async def test_list_resources_limit_binds_and_caps(
    repo: ResourcesRepository, cap_session: _CapSession
) -> None:
    await repo.get_resource_items(scope_type="personal", scope_id="user-1", limit=500)
    sql, params = _main_call(cap_session)
    assert sql.rstrip().endswith("LIMIT :limit")
    assert params["limit"] == 500


@pytest.mark.asyncio
async def test_list_resources_no_limit_by_default(
    repo: ResourcesRepository, cap_session: _CapSession
) -> None:
    await repo.get_resource_items(scope_type="personal", scope_id="user-1")
    sql, params = _main_call(cap_session)
    assert "LIMIT" not in sql
    assert "limit" not in params


@pytest.mark.asyncio
async def test_list_resources_include_trashed_drops_default_filter(
    repo: ResourcesRepository, cap_session: _CapSession
) -> None:
    await repo.get_resource_items(
        scope_type="personal", scope_id="user-1", include_trashed=True
    )
    sql, _ = _main_call(cap_session)
    assert "r.is_trashed = false" not in sql


@pytest.mark.asyncio
async def test_list_resources_min_rating_applies(
    repo: ResourcesRepository, cap_session: _CapSession
) -> None:
    await repo.get_resource_items(
        scope_type="personal", scope_id="user-1", min_rating=4
    )
    sql, params = _main_call(cap_session)
    assert "r.rating >= :min_rating" in sql
    assert params["min_rating"] == 4


@pytest.mark.asyncio
async def test_list_resources_types_adds_mime_or_expr(
    repo: ResourcesRepository, cap_session: _CapSession
) -> None:
    await repo.get_resource_items(
        scope_type="personal", scope_id="user-1", types=["video"]
    )
    sql, _ = _main_call(cap_session)
    assert "r.mime_type LIKE 'video/%'" in sql


@pytest.mark.asyncio
async def test_list_resources_types_empty_skips_mime_clause(
    repo: ResourcesRepository, cap_session: _CapSession
) -> None:
    await repo.get_resource_items(scope_type="personal", scope_id="user-1", types=[])
    sql, _ = _main_call(cap_session)
    assert "mime_type LIKE" not in sql


# ─── source_types (resource.source_type provenance) ───────────────────


@pytest.mark.asyncio
async def test_list_resources_source_types_adds_any_clause(
    repo: ResourcesRepository, cap_session: _CapSession
) -> None:
    """The Distribution Publish picker passes own-content source types →
    ``r.source_type = ANY(:source_types)`` with the values bound verbatim."""
    await repo.get_resource_items(
        scope_type="personal",
        scope_id="user-1",
        source_types=["upload", "generated", "derived"],
    )
    sql, params = _main_call(cap_session)
    assert "r.source_type = ANY(:source_types)" in sql
    assert params["source_types"] == ["upload", "generated", "derived"]


@pytest.mark.asyncio
async def test_list_resources_source_types_none_skips_clause(
    repo: ResourcesRepository, cap_session: _CapSession
) -> None:
    await repo.get_resource_items(scope_type="personal", scope_id="user-1")
    sql, params = _main_call(cap_session)
    assert "source_type" not in sql
    assert "source_types" not in params


@pytest.mark.asyncio
async def test_list_resources_source_types_empty_skips_clause(
    repo: ResourcesRepository, cap_session: _CapSession
) -> None:
    await repo.get_resource_items(
        scope_type="personal", scope_id="user-1", source_types=[]
    )
    sql, params = _main_call(cap_session)
    assert "source_type" not in sql
    assert "source_types" not in params


# ─── tag_ids / platforms pre-resolution + intersection ────────────────


@pytest.mark.asyncio
async def test_list_resources_tag_ids_empty_intersection_short_circuits(
    repo: ResourcesRepository, cap_session: _CapSession
) -> None:
    """No resource carries all requested tags → [] without the main query."""

    async def fake_intersection(tag_ids: list[str]) -> list[str]:
        return []

    repo._resource_ids_with_all_tags = fake_intersection  # type: ignore[method-assign]

    result = await repo.get_resource_items(
        scope_type="personal", scope_id="user-1", tag_ids=["tag-a"]
    )
    assert result == []
    # Main query never opened a session.
    assert cap_session.calls == []


@pytest.mark.asyncio
async def test_list_resources_tag_ids_applies_matched_ids(
    repo: ResourcesRepository, cap_session: _CapSession
) -> None:
    """Non-empty intersection feeds ``i.resource_id = ANY(:matched_ids)``."""

    async def fake_intersection(tag_ids: list[str]) -> list[str]:
        return ["res-1", "res-2"]

    repo._resource_ids_with_all_tags = fake_intersection  # type: ignore[method-assign]

    await repo.get_resource_items(
        scope_type="personal", scope_id="user-1", tag_ids=["tag-a", "tag-b"]
    )
    sql, params = _main_call(cap_session)
    assert "i.resource_id = ANY(:matched_ids)" in sql
    assert sorted(params["matched_ids"]) == ["res-1", "res-2"]


@pytest.mark.asyncio
async def test_list_resources_platforms_empty_short_circuits(
    repo: ResourcesRepository, cap_session: _CapSession
) -> None:
    async def fake_platform_ids(_platforms: list[str]) -> list[str]:
        return []

    repo._resource_ids_for_platforms = fake_platform_ids  # type: ignore[method-assign]

    result = await repo.get_resource_items(
        scope_type="personal", scope_id="user-1", platforms=["douyin"]
    )
    assert result == []
    assert cap_session.calls == []


@pytest.mark.asyncio
async def test_list_resources_platforms_applies_matched_ids(
    repo: ResourcesRepository, cap_session: _CapSession
) -> None:
    async def fake_platform_ids(_platforms: list[str]) -> list[str]:
        return ["res-1", "res-2"]

    repo._resource_ids_for_platforms = fake_platform_ids  # type: ignore[method-assign]

    await repo.get_resource_items(
        scope_type="personal", scope_id="user-1", platforms=["douyin", "youtube"]
    )
    _, params = _main_call(cap_session)
    assert sorted(params["matched_ids"]) == ["res-1", "res-2"]


@pytest.mark.asyncio
async def test_list_resources_tag_and_platform_intersect(
    repo: ResourcesRepository, cap_session: _CapSession
) -> None:
    async def fake_tag_ids(_t: list[str]) -> list[str]:
        return ["res-1", "res-2", "res-3"]

    async def fake_platform_ids(_p: list[str]) -> list[str]:
        return ["res-2", "res-3", "res-4"]

    repo._resource_ids_with_all_tags = fake_tag_ids  # type: ignore[method-assign]
    repo._resource_ids_for_platforms = fake_platform_ids  # type: ignore[method-assign]

    await repo.get_resource_items(
        scope_type="personal",
        scope_id="user-1",
        tag_ids=["tag-a"],
        platforms=["douyin"],
    )
    _, params = _main_call(cap_session)
    # Intersection is tag-ordered (tag result drives the iteration).
    assert params["matched_ids"] == ["res-2", "res-3"]


@pytest.mark.asyncio
async def test_list_resources_tag_platform_empty_intersection_short_circuits(
    repo: ResourcesRepository, cap_session: _CapSession
) -> None:
    async def fake_tag_ids(_t: list[str]) -> list[str]:
        return ["res-1"]

    async def fake_platform_ids(_p: list[str]) -> list[str]:
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
    assert cap_session.calls == []


# ─── AI status filters ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_resources_ai_transcribed_adds_completed_filter(
    repo: ResourcesRepository, cap_session: _CapSession
) -> None:
    await repo.get_resource_items(
        scope_type="personal", scope_id="user-1", ai_transcribed=True
    )
    sql, params = _main_call(cap_session)
    assert 'r."transcript_status" = :ai_transcribed' in sql
    assert params["ai_transcribed"] == "completed"


@pytest.mark.asyncio
async def test_list_resources_ai_flags_false_or_none_are_inactive(
    repo: ResourcesRepository, cap_session: _CapSession
) -> None:
    await repo.get_resource_items(
        scope_type="personal",
        scope_id="user-1",
        ai_transcribed=False,
        ai_summarized=None,
        ai_analyzed=False,
    )
    sql, params = _main_call(cap_session)
    assert "transcript_status" not in sql
    assert "summary_status" not in sql
    assert "visual_analysis_status" not in sql
    assert "ai_transcribed" not in params


@pytest.mark.asyncio
async def test_list_resources_ai_has_prompt_adds_four_column_or_predicate(
    repo: ResourcesRepository, cap_session: _CapSession
) -> None:
    await repo.get_resource_items(
        scope_type="personal", scope_id="user-1", ai_has_prompt=True
    )
    sql, params = _main_call(cap_session)
    assert "r.gen_prompt IS NOT NULL AND r.gen_prompt <> ''" in sql
    assert "r.gen_prompt_negative IS NOT NULL AND r.gen_prompt_negative <> ''" in sql
    assert "r.gen_prompt_json IS NOT NULL AND r.gen_prompt_json <> ''" in sql
    assert "r.slide_prompts IS NOT NULL" in sql
    assert "r.slide_prompts <> 'null'::jsonb" in sql
    assert "r.slide_prompts <> '{}'::jsonb" in sql
    # Purely literal predicate — no bind params introduced for it.
    assert "ai_has_prompt" not in params


@pytest.mark.asyncio
async def test_list_resources_ai_has_prompt_false_or_none_are_inactive(
    repo: ResourcesRepository, cap_session: _CapSession
) -> None:
    await repo.get_resource_items(
        scope_type="personal", scope_id="user-1", ai_has_prompt=False
    )
    sql, _params = _main_call(cap_session)
    assert "gen_prompt" not in sql
    assert "slide_prompts" not in sql

    await repo.get_resource_items(
        scope_type="personal", scope_id="user-1", ai_has_prompt=None
    )
    sql, _params = _main_call(cap_session)
    assert "gen_prompt" not in sql
    assert "slide_prompts" not in sql


@pytest.mark.asyncio
async def test_list_resources_ai_all_three_flags_apply_together(
    repo: ResourcesRepository, cap_session: _CapSession
) -> None:
    await repo.get_resource_items(
        scope_type="personal",
        scope_id="user-1",
        ai_transcribed=True,
        ai_summarized=True,
        ai_analyzed=True,
    )
    sql, params = _main_call(cap_session)
    assert 'r."transcript_status" = :ai_transcribed' in sql
    assert 'r."summary_status" = :ai_summarized' in sql
    assert 'r."visual_analysis_status" = :ai_analyzed' in sql
    assert params["ai_summarized"] == "completed"


# ─── date + duration ranges ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_resources_date_range_binds_both_bounds(
    repo: ResourcesRepository, cap_session: _CapSession
) -> None:
    await repo.get_resource_items(
        scope_type="personal",
        scope_id="user-1",
        created_after=date(2026, 4, 1),
        created_before=date(2026, 4, 20),
    )
    sql, params = _main_call(cap_session)
    assert "r.created_at >= :created_after" in sql
    assert "r.created_at <= :created_before" in sql
    # Bound as tz-aware datetimes (start of day / end of day UTC).
    assert params["created_after"].isoformat().startswith("2026-04-01T00:00:00")
    assert params["created_before"].isoformat().startswith("2026-04-20T23:59:59")


@pytest.mark.asyncio
async def test_list_resources_date_range_single_bound(
    repo: ResourcesRepository, cap_session: _CapSession
) -> None:
    await repo.get_resource_items(
        scope_type="personal", scope_id="user-1", created_after=date(2026, 4, 15)
    )
    sql, params = _main_call(cap_session)
    assert "r.created_at >= :created_after" in sql
    assert "created_before" not in params


@pytest.mark.asyncio
async def test_list_resources_duration_bounds_bind_int(
    repo: ResourcesRepository, cap_session: _CapSession
) -> None:
    await repo.get_resource_items(
        scope_type="personal",
        scope_id="user-1",
        duration_min=60.7,  # type: ignore[arg-type]
        duration_max=300.2,  # type: ignore[arg-type]
    )
    sql, params = _main_call(cap_session)
    assert "r.duration_seconds >= :duration_min" in sql
    assert "r.duration_seconds <= :duration_max" in sql
    # Coerced to int for a predictable bind.
    assert params["duration_min"] == 60
    assert params["duration_max"] == 300


@pytest.mark.asyncio
async def test_list_resources_duration_none_is_inactive(
    repo: ResourcesRepository, cap_session: _CapSession
) -> None:
    await repo.get_resource_items(scope_type="personal", scope_id="user-1")
    sql, params = _main_call(cap_session)
    assert "duration_seconds" not in sql
    assert "duration_min" not in params


# ─── API-surface no-ops (aspect ratios + social metrics) ──────────────


@pytest.mark.asyncio
async def test_list_resources_aspect_ratios_is_noop(
    repo: ResourcesRepository, cap_session: _CapSession
) -> None:
    await repo.get_resource_items(
        scope_type="personal", scope_id="user-1", aspect_ratios=["9:16", "16:9"]
    )
    sql, params = _main_call(cap_session)
    assert "resolution" not in sql
    assert "aspect" not in sql


@pytest.mark.asyncio
async def test_list_resources_social_metrics_are_noop(
    repo: ResourcesRepository, cap_session: _CapSession
) -> None:
    await repo.get_resource_items(
        scope_type="personal",
        scope_id="user-1",
        min_likes=1000,
        min_comments=50,
        min_favorites=10,
        min_shares=5,
        social_combine="or",
        has_comments=True,
    )
    sql, params = _main_call(cap_session)
    for needle in ("like_count", "comment_count", "favorite_count", "share_count"):
        assert needle not in sql
    for leaked in ("min_likes", "min_comments", "min_favorites", "min_shares"):
        assert leaked not in params


@pytest.mark.asyncio
async def test_list_resources_combined_filters_all_stack(
    repo: ResourcesRepository, cap_session: _CapSession
) -> None:
    """Sanity: tags + rating + types + platforms + AI + dates all layer into a
    single query without stomping on each other."""

    async def fake_tag_ids(_t: list[str]) -> list[str]:
        return ["res-1", "res-2"]

    async def fake_platform_ids(_p: list[str]) -> list[str]:
        return ["res-1", "res-2", "res-3"]

    repo._resource_ids_with_all_tags = fake_tag_ids  # type: ignore[method-assign]
    repo._resource_ids_for_platforms = fake_platform_ids  # type: ignore[method-assign]

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
    sql, params = _main_call(cap_session)
    assert "r.rating >= :min_rating" in sql
    assert params["min_rating"] == 3
    assert 'r."transcript_status" = :ai_transcribed' in sql
    assert "r.created_at >= :created_after" in sql
    assert "r.mime_type LIKE 'video/%'" in sql
    # tag ∩ platform = {res-1, res-2}
    assert sorted(params["matched_ids"]) == ["res-1", "res-2"]


# ─── _build_mime_sql (pure helper) ────────────────────────────────────


def test_build_mime_sql_empty_returns_none() -> None:
    assert ResourcesRepository._build_mime_sql(None) is None
    assert ResourcesRepository._build_mime_sql([]) is None
    assert ResourcesRepository._build_mime_sql(["  "]) is None


def test_build_mime_sql_single_video_category() -> None:
    expr = ResourcesRepository._build_mime_sql(["video"])
    assert expr == "(r.mime_type LIKE 'video/%')"


def test_build_mime_sql_document_expands() -> None:
    expr = ResourcesRepository._build_mime_sql(["document"])
    assert expr is not None
    assert "r.mime_type = 'application/pdf'" in expr
    assert "r.mime_type LIKE 'application/msword%'" in expr
    assert "r.mime_type LIKE 'text/%'" in expr


def test_build_mime_sql_other_is_not_of_known_prefixes() -> None:
    expr = ResourcesRepository._build_mime_sql(["other"])
    assert expr is not None
    assert expr.startswith("(NOT ")
    assert "r.mime_type LIKE 'video/%'" in expr


def test_build_mime_sql_ignores_unknown_categories() -> None:
    expr = ResourcesRepository._build_mime_sql(["video", "bogus"])
    assert expr == "(r.mime_type LIKE 'video/%')"


# ─── _resource_ids_with_all_tags (ORM GROUP BY HAVING) ────────────────


@pytest.mark.asyncio
async def test_resource_ids_with_all_tags_empty_input_returns_empty(
    repo: ResourcesRepository, cap_session: _CapSession
) -> None:
    assert await repo._resource_ids_with_all_tags([]) == []
    assert cap_session.calls == []


@pytest.mark.asyncio
async def test_resource_ids_with_all_tags_builds_having_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _CapSession(rowsets=[[{"resource_id": 7}, {"resource_id": 9}]])
    monkeypatch.setattr(repo_mod, "read_scope", lambda: _fake_scope(session))
    repo = ResourcesRepository()

    result = await repo._resource_ids_with_all_tags(["10", "20", "20"])
    assert result == ["7", "9"]  # coerced to str

    sql, params = session.calls[-1]
    assert "GROUP BY resource_id" in sql
    assert "HAVING count(DISTINCT tag_id) = :n" in sql
    # tag ids coerced to bigint; n counts DISTINCT requested tags.
    assert params["tag_ids"] == [10, 20, 20]
    assert params["n"] == 2


@pytest.mark.asyncio
async def test_resource_ids_with_all_tags_returns_empty_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A DB error inside the intersection query is swallowed → [] (the
    defensive ``except`` branch must NOT propagate)."""

    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(repo_mod, "read_scope", _boom)
    repo = ResourcesRepository()

    assert await repo._resource_ids_with_all_tags(["tag-a"]) == []


# ─── _resource_ids_for_platforms (ORM two-step) ───────────────────────


@pytest.mark.asyncio
async def test_resource_ids_for_platforms_empty_input_returns_empty(
    repo: ResourcesRepository, cap_session: _CapSession
) -> None:
    assert await repo._resource_ids_for_platforms([]) == []
    assert await repo._resource_ids_for_platforms(["  "]) == []
    assert cap_session.calls == []


@pytest.mark.asyncio
async def test_resource_ids_for_platforms_two_step_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase C task 3: migrated off raw ``text()`` SQL to two ORM
    ``select()``s. The fake session hands back tuple rows (the real ORM
    ``.all()`` shape) and assertions compile the captured RAW statements
    (``raw_calls``) rather than substring-matching a params dict that no
    longer exists at the ``session.execute()`` call boundary (ORM binds are
    embedded in the compiled statement, not passed separately)."""
    # Step 1 → parsed_media ids; step 2 → resources ids.
    session = _CapSession(rowsets=[[(1,), (2,)], [(100,), (200,)]])
    monkeypatch.setattr(repo_mod, "read_scope", lambda: _fake_scope(session))
    repo = ResourcesRepository()

    result = await repo._resource_ids_for_platforms(["douyin", "youtube"])
    assert result == ["100", "200"]

    assert len(session.raw_calls) == 2
    first_compiled = session.raw_calls[0].compile()
    second_compiled = session.raw_calls[1].compile()
    first_sql, first_params = str(first_compiled), dict(first_compiled.params)
    second_sql, second_params = str(second_compiled), dict(second_compiled.params)
    assert "parsed_media" in first_sql
    assert any(
        isinstance(v, (list, tuple)) and list(v) == ["douyin", "youtube"]
        for v in first_params.values()
    )
    assert "resources" in second_sql
    assert any(
        isinstance(v, (list, tuple)) and list(v) == [1, 2]
        for v in second_params.values()
    )


@pytest.mark.asyncio
async def test_resource_ids_for_platforms_no_media_short_circuits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """parsed_media lookup empty → resources query must NOT run."""
    session = _CapSession(rowsets=[[]])  # step 1 returns nothing
    monkeypatch.setattr(repo_mod, "read_scope", lambda: _fake_scope(session))
    repo = ResourcesRepository()

    result = await repo._resource_ids_for_platforms(["douyin"])
    assert result == []
    assert len(session.calls) == 1  # only parsed_media probed


@pytest.mark.asyncio
async def test_resource_ids_for_platforms_returns_empty_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A DB error inside the platform lookup is swallowed → [] (the defensive
    ``except`` branch must NOT propagate)."""

    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(repo_mod, "read_scope", _boom)
    repo = ResourcesRepository()

    assert await repo._resource_ids_for_platforms(["douyin"]) == []
