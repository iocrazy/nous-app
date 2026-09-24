"""Re-running a transcription / summary must move ``created_at``.

Neither table has an ``updated_at``; ``created_at`` doubles as the last write
time. The vector backfill reads it (``resource_embeddings_repository.
_stale_source``): a summary regenerated after the vector marks the vector
``stale_source``. The upsert's ON CONFLICT used to leave ``created_at`` at the
first insert, so a re-run was invisible there.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy.dialects import postgresql

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

REPO = "app.repositories.ai_repository"


class _Session:
    def __init__(self) -> None:
        self.stmts: list[Any] = []

    async def execute(self, stmt, params=None):
        self.stmts.append(stmt)

        class _R:
            def scalars(self):
                return self

            def first(self):
                return None

        return _R()


def _scope(session: _Session):
    @asynccontextmanager
    async def _cm():
        yield session

    return _cm


def _on_conflict_set(stmt) -> str:
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    return sql[sql.index("ON CONFLICT") :]


@pytest.mark.parametrize(
    "method, data",
    [
        ("save_transcript", {"full_text": "hello", "language": "en"}),
        ("save_summary", {"summary_text": "short", "summary_type": "brief"}),
    ],
)
async def test_upsert_moves_created_at_on_conflict(method, data):
    from app.repositories.ai_repository import AIRepository

    session = _Session()
    with patch(f"{REPO}.write_scope", _scope(session)):
        await getattr(AIRepository(), method)("123", data)
    on_conflict = _on_conflict_set(session.stmts[0])
    assert "created_at = now()" in on_conflict
