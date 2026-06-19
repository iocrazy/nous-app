"""Regression test for the /api/v1/resources/search schema-drift 500.

Background — 2026-06-19: GET /api/v1/resources/search returned 500 with
`asyncpg.exceptions.UndefinedColumnError: column r.file_size does not exist
(HINT: Perhaps you meant "r.file_type")`. The raw SQL in
ResourcesRepository.list_accessible_for_user selected `r.file_size AS size`,
but the resources table column is `file_size_bytes` (file_size never existed).
Every resource search hit the error → broken picker / search.

This pins the column name so the drift can't silently come back.
"""

from __future__ import annotations

import importlib
import inspect
import re


def _repo_source() -> str:
    mod = importlib.import_module("app.repositories.resources_repository")
    return inspect.getsource(mod.ResourcesRepository.list_accessible_for_user)


def test_search_uses_real_size_column() -> None:
    source = _repo_source()
    # The real column is file_size_bytes.
    assert "file_size_bytes" in source, (
        "resources search must select r.file_size_bytes — the resources table "
        "has no `file_size` column (it is file_size_bytes)."
    )
    # The nonexistent `r.file_size` (not followed by _bytes) must not appear.
    assert not re.search(r"\br\.file_size\b(?!_bytes)", source), (
        "resources search references r.file_size, which does not exist and "
        "raises UndefinedColumnError (asyncpg) → 500 on every search. Use "
        "r.file_size_bytes."
    )
