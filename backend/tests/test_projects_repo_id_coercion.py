"""Snowflake-id coercion at the projects ORM write boundary.

Routers/services carry ids as str end-to-end (JS BIGINT precision), but
asyncpg binds BIGINT params strictly — a str raises DataError instead of
coercing. Every ``create_file`` since the ORM cutover (#966) 500'd on
``$2::BIGINT`` this way, which is why prod's ``project_files`` table was
empty. ``_coerce_id_ints`` normalizes known id columns in the write
``values()`` dict; these tests pin the helper and the per-writer key sets.
"""

from __future__ import annotations

import pytest

from app.repositories.projects_repository import _coerce_id_ints

pytestmark = pytest.mark.unit


class TestCoerceIdInts:
    def test_str_ids_become_int(self):
        out = _coerce_id_ints(
            {"project_id": "327326231179111", "filename": "a.txt"},
            ("project_id",),
        )
        assert out["project_id"] == 327326231179111
        assert isinstance(out["project_id"], int)
        assert out["filename"] == "a.txt"

    def test_int_passthrough(self):
        out = _coerce_id_ints({"file_id": 42}, ("file_id",))
        assert out["file_id"] == 42

    def test_none_and_absent_keys_untouched(self):
        out = _coerce_id_ints({"parent_id": None}, ("parent_id", "project_id"))
        assert out["parent_id"] is None
        assert "project_id" not in out

    def test_returns_new_dict(self):
        src = {"project_id": "1"}
        out = _coerce_id_ints(src, ("project_id",))
        assert src["project_id"] == "1"  # no mutation
        assert out is not src

    def test_non_numeric_raises_value_error(self):
        # Garbage ids must fail loudly at the boundary, not deep in asyncpg.
        with pytest.raises(ValueError):
            _coerce_id_ints({"project_id": "not-a-snowflake"}, ("project_id",))
