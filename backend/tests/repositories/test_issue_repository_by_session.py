"""IssueRepository.get_by_session (phase 2b-1): the issue behind an agent
conversation — the durable run → issue link when issue_id was never
backfilled."""

from __future__ import annotations

import contextlib
from unittest.mock import patch

import pytest

from app.repositories.issue_repository import IssueRepository

pytestmark = pytest.mark.unit


async def test_get_by_session_filters_on_ai_session_id():
    stmts = []

    class _Scalars:
        def first(self):
            return None

    class _Result:
        def scalars(self):
            return _Scalars()

    class _Session:
        async def execute(self, stmt):
            stmts.append(stmt)
            return _Result()

    @contextlib.asynccontextmanager
    async def _rs():
        yield _Session()

    with patch("app.repositories.issue_repository.read_scope", _rs):
        assert await IssueRepository().get_by_session(347474246337075) is None
    binds = dict(stmts[0].compile().params)
    assert binds["ai_session_id_1"] == 347474246337075
    assert "issues.ai_session_id = " in str(stmts[0])
