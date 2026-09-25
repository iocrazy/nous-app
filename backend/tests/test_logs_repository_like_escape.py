"""The logs viewer / export search box is a literal, not an ILIKE pattern.

``_apply_filters`` wrapped the raw text in ``%…%``, so searching for ``%``
returned every log and ``_`` matched any character. Both ``GET /logs`` and
``GET /logs/export`` build their query here.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.models import UserLogs
from app.repositories.logs_repository import _apply_filters

pytestmark = pytest.mark.unit


def _compiled(search: str):
    stmt = _apply_filters(select(UserLogs), "u-1", None, None, None, search)
    return stmt.compile(dialect=postgresql.dialect())


def test_metacharacters_in_search_match_themselves():
    compiled = _compiled("50%_off\\")
    patterns = [v for v in compiled.params.values() if isinstance(v, str)]
    assert "%50\\%\\_off\\\\%" in patterns
    assert "ESCAPE" in str(compiled)


def test_search_is_still_scoped_to_the_caller():
    compiled = _compiled("x")
    assert "u-1" in compiled.params.values()
