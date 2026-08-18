"""``resolve_resource_refs`` meta carries the AI processing status.

RECON#2: the resolver dropped ``transcript_status`` / ``summary_status``, so
neither the prompt composer nor the agent could tell "never processed" from
"being processed right now" — the three-in-a-row "not available" replies the
user reported (spec 2026-08-17 §1-F1).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from app.services.ai.chat.resource_ref_resolver import _fetch_accessible_meta

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class _CapturingSession:
    def __init__(self, rows):
        self._rows = rows
        self.captured_sql: str | None = None

    async def execute(self, stmt):
        from sqlalchemy.dialects import postgresql

        self.captured_sql = str(
            stmt.compile(
                dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
            )
        )

        class _Result:
            def mappings(_self):
                class _M:
                    def all(_m):
                        return self._rows

                return _M()

        return _Result()


def _patch_scopes(monkeypatch, session):
    import app.db.scope as scope_module
    import app.db.session as session_module

    @asynccontextmanager
    async def _read_scope():
        yield session

    @asynccontextmanager
    async def _system_request_scope(reason: str):
        yield None

    monkeypatch.setattr(session_module, "read_scope", _read_scope)
    monkeypatch.setattr(scope_module, "system_request_scope", _system_request_scope)


def _row(**over):
    base = {
        "id": "1",
        "name": "pitch.mp4",
        "mime": "video/mp4",
        "size": 100,
        "brief": None,
        "updated_at": "2026-08-01T00:00:00Z",
        "scope_id": "9",
        "team_name": None,
        "scope_kind": "personal",
        "transcript_status": "processing",
        "summary_status": "none",
    }
    base.update(over)
    return base


async def test_query_selects_the_status_columns(monkeypatch):
    session = _CapturingSession(rows=[])
    _patch_scopes(monkeypatch, session)

    await _fetch_accessible_meta("u1", ["1"])

    sql = session.captured_sql or ""
    assert "transcript_status" in sql
    assert "summary_status" in sql


async def test_meta_exposes_both_statuses(monkeypatch):
    session = _CapturingSession(rows=[_row()])
    _patch_scopes(monkeypatch, session)

    meta = await _fetch_accessible_meta("u1", ["1"])

    assert meta["1"]["transcript_status"] == "processing"
    assert meta["1"]["summary_status"] == "none"


async def test_enum_members_are_rendered_as_their_values(monkeypatch):
    """Real rows carry ``AiTaskStatus`` members; ``str()`` on that
    ``(str, Enum)`` mixin yields ``'AiTaskStatus.PROCESSING'``, which would
    end up verbatim in the system prompt."""
    from app.models._enums import AiTaskStatus

    session = _CapturingSession(
        rows=[
            _row(
                transcript_status=AiTaskStatus.COMPLETED,
                summary_status=AiTaskStatus.PENDING,
            )
        ]
    )
    _patch_scopes(monkeypatch, session)

    meta = await _fetch_accessible_meta("u1", ["1"])

    assert meta["1"]["transcript_status"] == "completed"
    assert meta["1"]["summary_status"] == "pending"


async def test_missing_status_degrades_to_none_not_a_crash(monkeypatch):
    """Defensive: a row shape without the columns (older cached path, test
    doubles) must not blow up the whole @-mention turn."""
    row = _row()
    row.pop("transcript_status")
    row.pop("summary_status")
    session = _CapturingSession(rows=[row])
    _patch_scopes(monkeypatch, session)

    meta = await _fetch_accessible_meta("u1", ["1"])

    assert meta["1"]["transcript_status"] is None
    assert meta["1"]["summary_status"] is None


async def test_existing_meta_keys_are_untouched(monkeypatch):
    session = _CapturingSession(rows=[_row()])
    _patch_scopes(monkeypatch, session)

    meta = await _fetch_accessible_meta("u1", ["1"])

    assert meta["1"]["name"] == "pitch.mp4"
    assert meta["1"]["kind"] == "video"
    assert meta["1"]["scope"] == "personal"
