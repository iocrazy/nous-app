"""Regression tests for resources query schema-drift (UndefinedColumnError 500s).

Background — 2026-06-19, two drift bugs in the same queries:

1. `r.file_size` — GET /api/v1/resources/search returned 500
   (`column r.file_size does not exist`). The resources column is
   `file_size_bytes`; `file_size` never existed.

2. `ri.is_trashed` — `resource_items` has no `is_trashed` column (trashing is
   tracked only at the resource level, `r.is_trashed`). Three queries filtered
   on `ri.is_trashed = false` (added in #361), which raises UndefinedColumnError.
   In the search query PostgreSQL reported `file_size` first (a SELECT column),
   masking the `ri.is_trashed` error until #794 fixed file_size — then the
   search still 500'd on `ri.is_trashed`. The AI resource-fetch / ref-resolver
   queries hit it directly (low traffic, so it went unnoticed).

3. `owner_id::text = :user_id` — 2026-07-20, GET /api/v1/resources/search
   500'd 22 times with `operator does not exist: text = uuid`. Not a column
   drift but a *bind-parameter type* drift, same 500 symptom. PostgreSQL infers
   a parameter's type from its first use: `team_members.user_id = :user_id`
   (uuid column) pins `:user_id` to uuid, so a later `owner_id::text = :user_id`
   asks for `text = uuid` and blows up. Both `team_members.user_id` and
   `teams.owner_id` are uuid — the `::text` was never needed. The branch only
   assembles when `scope_team_id is not None` (issue-scoped picker), which is
   why it was conditional rather than every-call.

These pins keep all three drifts from coming back across the query sites.
"""

from __future__ import annotations

import importlib
import inspect
import re

_FILES = [
    "app.repositories.resources_repository",
    "app.services.ai.tools.resource_fetch_tool",
    "app.services.ai.chat.resource_ref_resolver",
]


def _module_source(dotted: str) -> str:
    return inspect.getsource(importlib.import_module(dotted))


def test_no_nonexistent_file_size_column() -> None:
    source = inspect.getsource(
        importlib.import_module(
            "app.repositories.resources_repository"
        ).ResourcesRepository.list_accessible_for_user
    )
    assert "file_size_bytes" in source, (
        "resources search must select r.file_size_bytes (the resources table "
        "has no `file_size` column)."
    )
    assert not re.search(
        r"\br\.file_size\b(?!_bytes)", source
    ), "resources search references r.file_size — does not exist, 500s."


def test_no_text_cast_against_uuid_user_id_bind() -> None:
    """`:user_id` is bound against uuid columns, so it must never be compared
    to a ::text expression in the same statement.

    PostgreSQL resolves a bind parameter's type from its first use. Once
    `team_members.user_id = :user_id` (uuid) fixes `:user_id` as uuid, any
    `<expr>::text = :user_id` in the same query becomes `text = uuid` →
    UndefinedFunctionError → 500. Both uuid-typed sides should compare
    directly, with no cast.
    """
    for dotted in _FILES:
        source = _module_source(dotted)
        assert not re.search(r"::text\s*=\s*:user_id", source), (
            f"{dotted} compares a ::text expression against :user_id, but "
            ":user_id is bound as uuid (team_members.user_id / teams.owner_id "
            "are both uuid) → `operator does not exist: text = uuid` (500). "
            "Drop the ::text and compare uuid to uuid."
        )


def test_no_nonexistent_resource_items_is_trashed() -> None:
    """No query may filter on ri.is_trashed — resource_items has no such column;
    trashing lives on resources.is_trashed only."""
    for dotted in _FILES:
        source = _module_source(dotted)
        assert not re.search(r"\bri\.is_trashed\b", source), (
            f"{dotted} filters on ri.is_trashed, but resource_items has no "
            "is_trashed column → asyncpg UndefinedColumnError (500). Trashing "
            "is tracked only on resources (r.is_trashed)."
        )
