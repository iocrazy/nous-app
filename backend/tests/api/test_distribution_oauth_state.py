"""Unit tests for the distribution OAuth-state helpers (Phase B5 Task 1).

``_save_oauth_state``/``_pop_oauth_state`` were migrated from a broken
``accounts_repo.execute(...)`` call and a raw ``db_engine.execute_returning_one
(...)`` DELETE...RETURNING, to the SQLAlchemy ORM (``app.db.session.write_scope``
+ the ``DistributionOauthStates`` model).

``accounts_repo.execute(...)`` was never a real method, not something a later
migration removed: ``git log --follow --reverse`` shows ``_save_oauth_state``
was introduced in the very same commit (f137bf0) that created
``SocialAccountsRepository`` — and that class was ORM-only (read_scope/
write_scope) from its first line, with no generic ``.execute()`` passthrough
ever defined on it. The call was dead code from day one, silently orphaning
this INSERT — no test caught it because the only prior coverage
(test_distribution_flag_gate.py) mocked ``_save_oauth_state`` wholesale rather
than exercising the real call. Tests here patch ``write_scope`` and assert
against the COMPILED statement.

deferred-minors batch (B5 final review Minor 2): the tests above only ever
exercise a hand-written ``_FakeResult``/``_RecordingSession`` mock — they
never round-trip ``_pop_oauth_state_stmt`` through a REAL SQLAlchemy
``Result``, so the B4 row-shape bug class (``.returning(*_OAUTH_STATE_COLS)``
column-level vs ``.returning(DistributionOauthStates)`` entity-level — the
mapping shape a mock can't distinguish) had no coverage. The two tests below
close that gap with a genuine ``aiosqlite``-backed engine, mirroring
``tests/test_orm_b5_task1_row_shape_e2e.py``'s positive/negative-control
pair. Reverse-injection self-check performed while writing this: swapping
the positive test's statement to the entity-level ``.returning(
DistributionOauthStates)`` form turns it red (``KeyError`` / ``None``
reads) exactly like the negative-control test below asserts, then
reverting to the real ``_pop_oauth_state_stmt`` import turns it green
again — confirming the test is actually sensitive to the bug class, not
just re-confirming whatever the code happens to do today.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import delete as sa_delete
from sqlalchemy import insert
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.api.distribution_router as dr
from app.models import DistributionOauthStates


def _compile(stmt: Any) -> tuple[str, dict[str, Any]]:
    compiled = stmt.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


class _FakeResult:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self._rows = rows if rows is not None else []

    def mappings(self) -> "_FakeResult":
        return self

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


class _RecordingSession:
    def __init__(self, result: _FakeResult | None = None) -> None:
        self.calls: list[Any] = []
        self._result = result or _FakeResult()

    async def execute(self, stmt: Any) -> _FakeResult:
        self.calls.append(stmt)
        return self._result


def _patch_write_scope(monkeypatch: pytest.MonkeyPatch, result=None):
    session = _RecordingSession(result)

    @asynccontextmanager
    async def fake_scope():
        yield session

    monkeypatch.setattr(dr, "write_scope", fake_scope)
    return session


@pytest.mark.asyncio
async def test_save_oauth_state_inserts_row(monkeypatch):
    session = _patch_write_scope(monkeypatch)
    uid = "11111111-1111-1111-1111-111111111111"

    await dr._save_oauth_state("state-abc", uid, "douyin", "user", uid)

    assert len(session.calls) == 1
    sql, binds = _compile(session.calls[0])
    assert "INSERT INTO public.distribution_oauth_states" in sql
    assert binds["state"] == "state-abc"
    assert binds["user_id"] == uid
    assert binds["platform"] == "douyin"
    assert binds["scope_type"] == "user"
    assert binds["scope_id"] == uid


@pytest.mark.asyncio
async def test_pop_oauth_state_deletes_and_returns_row(monkeypatch):
    now = datetime.now(timezone.utc)
    row = {
        "state": "state-abc",
        "user_id": UUID("11111111-1111-1111-1111-111111111111"),
        "platform": "douyin",
        "scope_type": "user",
        "scope_id": "11111111-1111-1111-1111-111111111111",
        "created_at": now,
    }
    session = _patch_write_scope(monkeypatch, _FakeResult([row]))

    result = await dr._pop_oauth_state("state-abc")
    assert result == row

    sql, binds = _compile(session.calls[0])
    assert "DELETE FROM public.distribution_oauth_states" in sql
    assert "RETURNING public.distribution_oauth_states.state" in sql
    assert binds["state_1"] == "state-abc"
    # 10-minute freshness window bound as an app-side cutoff timestamp, not a
    # server-side INTERVAL literal (see the function's docstring).
    cutoff = binds["created_at_1"]
    assert datetime.now(timezone.utc) - cutoff >= timedelta(minutes=10)
    assert datetime.now(timezone.utc) - cutoff < timedelta(minutes=10, seconds=5)


@pytest.mark.asyncio
async def test_pop_oauth_state_returns_none_when_not_found(monkeypatch):
    _patch_write_scope(monkeypatch, _FakeResult([]))
    result = await dr._pop_oauth_state("missing-state")
    assert result is None


# ── Real-aiosqlite row-shape regression (B5 final review Minor 2) ──────────

_OAUTH_STATE_DDL = """
CREATE TABLE distribution_oauth_states (
    state TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL
)
"""


@pytest.mark.asyncio
async def test_pop_oauth_state_stmt_returning_yields_column_keyed_row_against_real_sqlite():
    """The REAL production statement (``dr._pop_oauth_state_stmt``, imported —
    not reconstructed here) round-tripped through a genuine aiosqlite
    ``Result`` gives a column-keyed RowMapping, matching ``_pop_oauth_state``'s
    ``dict(row)`` consumption. Proves the row-shape choice
    (``.returning(*_OAUTH_STATE_COLS)``, not
    ``.returning(DistributionOauthStates)``) is load-bearing, not incidental
    (the B4 row-shape lesson)."""
    uid = uuid.UUID("22222222-2222-2222-2222-222222222222")
    inserted_at = datetime.now(timezone.utc)
    # Cutoff well BEFORE the row's created_at so the freshness predicate
    # (`created_at > cutoff`) matches it, mirroring a real not-yet-expired
    # OAuth state.
    cutoff = inserted_at - timedelta(minutes=20)

    engine = create_async_engine("sqlite+aiosqlite://")
    engine = engine.execution_options(schema_translate_map={"public": None})
    async with engine.begin() as conn:
        await conn.exec_driver_sql(_OAUTH_STATE_DDL)
        await conn.execute(
            insert(DistributionOauthStates.__table__).values(
                state="state-real-row",
                user_id=uid,
                platform="douyin",
                scope_type="user",
                scope_id=str(uid),
                created_at=inserted_at,
            )
        )

    sessionmaker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session:
            row = (
                (
                    await session.execute(
                        dr._pop_oauth_state_stmt("state-real-row", cutoff)
                    )
                )
                .mappings()
                .first()
            )
            await session.commit()
    finally:
        await engine.dispose()

    assert row is not None
    result = dict(row)  # exact consumption shape used by _pop_oauth_state
    assert result["state"] == "state-real-row"
    assert str(result["user_id"]) == str(uid)
    assert result["platform"] == "douyin"
    assert result["scope_type"] == "user"
    assert result["scope_id"] == str(uid)


@pytest.mark.asyncio
async def test_pop_oauth_state_stmt_entity_level_negative_control_proves_sensitivity():
    """Negative control: the KNOWN-BAD ``.returning(DistributionOauthStates)``
    form (entity-level), executed against the exact same real table/row,
    must produce the wrong shape — proving the test above actually
    distinguishes correct from broken, not just re-confirming whatever the
    code already does."""
    uid = uuid.UUID("33333333-3333-3333-3333-333333333333")
    inserted_at = datetime.now(timezone.utc)
    cutoff = inserted_at - timedelta(minutes=20)

    engine = create_async_engine("sqlite+aiosqlite://")
    engine = engine.execution_options(schema_translate_map={"public": None})
    async with engine.begin() as conn:
        await conn.exec_driver_sql(_OAUTH_STATE_DDL)
        await conn.execute(
            insert(DistributionOauthStates.__table__).values(
                state="state-bad-row",
                user_id=uid,
                platform="douyin",
                scope_type="user",
                scope_id=str(uid),
                created_at=inserted_at,
            )
        )

    sessionmaker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        bad_stmt = (
            sa_delete(DistributionOauthStates)
            .where(
                DistributionOauthStates.state == "state-bad-row",
                DistributionOauthStates.created_at > cutoff,
            )
            .returning(DistributionOauthStates)
        )
        async with sessionmaker() as session:
            bad_row = (await session.execute(bad_stmt)).mappings().first()
            await session.commit()
    finally:
        await engine.dispose()

    assert bad_row is not None
    assert list(bad_row.keys()) == ["DistributionOauthStates"]  # entity-keyed
    assert bad_row.get("state") is None  # _pop_oauth_state's dict(row) would break
    with pytest.raises(KeyError):
        bad_row["state"]
