"""ai_summary steps hit the ORM (SQLAlchemy 2.0 session) with correct
statements/params after the Phase C task 1 raw-SQL-to-ORM migration.

Phase B2 Task 1 (2026-08-04) had already moved the SECOND read
(user_settings.settings_json, consulted by ``resolve_summarization_config``)
onto ``read_scope()``. Phase C task 1 moved the FIRST read (the
parsed_media+resources+resource_transcripts JOIN, previously raw
``db_engine.fetch_one``) onto the SAME ``read_scope()`` seam, and
``persist_summary``'s raw ``engine.begin()`` three-statement transaction onto
a single ``write_scope()`` session running real ORM insert/update statements.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy.dialects import postgresql

from app.db import session as db_session


class _FakeExecuteResult:
    """Stand-in for the awaited ``session.execute(stmt)`` Result on the
    transcript+resource JOIN read path — only ``.mappings().first()`` is
    exercised (mirrors ``load_summary_inputs``'s real read)."""

    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class _CombinedReadSession:
    """Minimal stand-in for the ORM AsyncSession. Supports BOTH
    ``execute()`` (the transcript+resource JOIN read) and ``scalar()`` (the
    user_settings.settings_json read) — both flow through the SAME patched
    ``read_scope`` seam now that Phase C task 1 moved the JOIN off raw SQL."""

    def __init__(self, execute_row: Any = None, settings_json: Any = None) -> None:
        self._execute_row = execute_row
        self._settings_json = settings_json

    async def execute(self, _stmt: Any) -> _FakeExecuteResult:
        return _FakeExecuteResult(self._execute_row)

    async def scalar(self, _stmt: Any) -> Any:
        return self._settings_json


def _fake_read_scope(execute_row: Any = None, settings_json: Any = None):
    @asynccontextmanager
    async def _read_scope():
        yield _CombinedReadSession(execute_row=execute_row, settings_json=settings_json)

    return _read_scope


async def test_load_summary_inputs_raises_when_no_transcript():
    import app.workflows.ai_summary as m

    with patch.object(db_session, "read_scope", _fake_read_scope(execute_row=None)):
        with pytest.raises(RuntimeError, match="no transcript"):
            await m.load_summary_inputs(1, "u")


async def test_load_summary_inputs_raises_when_no_user_settings():
    import app.workflows.ai_summary as m

    row = {"transcript": "t", "pm_id": 1, "title": "x", "resource_id": 123}

    with patch.object(
        db_session,
        "read_scope",
        _fake_read_scope(execute_row=row, settings_json=None),
    ):
        with pytest.raises(RuntimeError, match="no user_settings"):
            await m.load_summary_inputs(1, "u")


async def test_load_summary_inputs_picks_first_enabled_provider():
    import app.workflows.ai_summary as m

    row = {"transcript": "t", "pm_id": 1, "title": "Title", "resource_id": 999}

    settings_json = {
        "ai_settings": {
            "ai_providers": {
                # doubao absent → qwen is the first enabled match
                "qwen": {
                    "api_key": "k",
                    "enabled": True,
                    "selected_model": "qwen-x",
                }
            }
        }
    }

    with patch.object(
        db_session,
        "read_scope",
        _fake_read_scope(execute_row=row, settings_json=settings_json),
    ):
        out = await m.load_summary_inputs(1, "u")

    assert out["provider_key"] == "qwen"
    assert out["resource_id"] == "999"  # bigint id surfaced as str for DBOS memo
    assert out["provider_config"]["model"] == "qwen-x"


# ── persist_summary: 3 writes in ONE write_scope() transaction, bigint rid ──


class _FakeWriteResult:
    def __init__(self, rowcount: int = 1) -> None:
        self.rowcount = rowcount


class _CapturingWriteSession:
    """Captures every statement passed to ``execute()`` for compile-level
    assertions — stands in for ``write_scope()``."""

    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def execute(self, stmt: Any) -> _FakeWriteResult:
        self.statements.append(stmt)
        return _FakeWriteResult()


def _fake_write_scope(session):
    @asynccontextmanager
    async def _write_scope():
        yield session

    return _write_scope


async def test_persist_summary_atomic_writes_with_int_rid():
    import app.workflows.ai_summary as m

    session = _CapturingWriteSession()
    with patch.object(db_session, "write_scope", _fake_write_scope(session)):
        out = await m.persist_summary(
            42,
            resource_id="999",
            summary="S",
            key_points=["a"],
            topics=["b"],
        )

    # All three writes go through the single write_scope() session/transaction.
    assert len(session.statements) == 3
    compiled = [s.compile(dialect=postgresql.dialect()) for s in session.statements]
    all_sql = " ".join(str(c) for c in compiled)
    assert "INSERT INTO public.resource_summaries" in all_sql
    assert "ON CONFLICT" in all_sql
    assert "public.parsed_media" in all_sql
    assert "public.resources" in all_sql
    assert "summary_status" in all_sql

    # resource_id is coerced to int for the bigint columns (asyncpg strictness).
    insert_params = compiled[0].params
    assert insert_params["resource_id"] == 999
    assert isinstance(insert_params["resource_id"], int)
    # native list values (not json.dumps()+CAST(...AS jsonb) strings).
    assert insert_params["key_points"] == ["a"]
    assert insert_params["topics"] == ["b"]
    assert out["summary_len"] == 1


async def test_persist_summary_writes_llm_telemetry():
    """llm_model / llm_provider have been NULL since resource_summaries was
    created — run_summarize_agent already knows both (provider_key +
    provider_config["model"]), persist_summary just never accepted them."""
    import app.workflows.ai_summary as m

    session = _CapturingWriteSession()
    with patch.object(db_session, "write_scope", _fake_write_scope(session)):
        await m.persist_summary(
            42,
            resource_id="999",
            summary="S",
            key_points=["a"],
            topics=["b"],
            llm_model="doubao-seed-2-0-pro-260215",
            llm_provider="doubao",
        )

    insert_params = session.statements[0].compile(dialect=postgresql.dialect()).params
    assert insert_params["llm_model"] == "doubao-seed-2-0-pro-260215"
    assert insert_params["llm_provider"] == "doubao"


async def test_persist_summary_truncates_llm_telemetry_over_50_chars():
    """resource_summaries.llm_model/llm_provider are varchar(50). A model
    name/provider string longer than that raises StringDataRightTruncation
    at the DB — which, combined with the route-C rule 4 re-raise, discards
    an already-paid-for summary. Truncate defensively before the write."""
    import app.workflows.ai_summary as m

    long_model = "m" * 80
    long_provider = "p" * 80

    session = _CapturingWriteSession()
    with patch.object(db_session, "write_scope", _fake_write_scope(session)):
        await m.persist_summary(
            42,
            resource_id="999",
            summary="S",
            key_points=["a"],
            topics=["b"],
            llm_model=long_model,
            llm_provider=long_provider,
        )

    insert_params = session.statements[0].compile(dialect=postgresql.dialect()).params
    assert insert_params["llm_model"] == long_model[:50]
    assert insert_params["llm_provider"] == long_provider[:50]
    assert len(insert_params["llm_model"]) == 50
    assert len(insert_params["llm_provider"]) == 50
