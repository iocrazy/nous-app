"""``PlaybackPositionsRepository`` against real Postgres (mig 473).

Unit tests stub the session, so compiling is all they prove. Three things only
the server can answer, and each one is load-bearing:

  * the upsert's ``ON CONFLICT (user_id, media_key)`` finds a real unique
    index — without it every heartbeat appends a row instead of updating one;
  * ``updated_at`` actually MOVES on the UPDATE arm — that timestamp is the
    arbiter the client uses to tell "my own write came back" from "another
    device wrote after me", so a frozen one silently breaks sync;
  * the ``UserScoped`` choke point really filters by ``user_id`` — this table
    is viewing history, and a missing filter is the whole risk.

    INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/drift \
      uv run pytest tests/db/test_playback_positions_integration.py -v
"""

from __future__ import annotations

import asyncio
import os
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

pytest.importorskip("asyncpg")

_skip = pytest.mark.skipif(
    not _TEST_DSN,
    reason="INTEGRATION_DATABASE_URL not set — playback positions need a DB.",
)

KEY = "resource:350063275939728"


@pytest.fixture
async def orm_dsn():
    """Repoint the ORM engine at the test DSN, then restore + dispose so no
    other test inherits a stray engine."""
    from app.core.config import settings
    from app.db import engine as engine_mod
    from app.db import session as session_mod

    old = settings.SUPAVISOR_DATABASE_URL
    settings.SUPAVISOR_DATABASE_URL = _TEST_DSN
    await engine_mod.dispose_engine()
    session_mod.dispose_sessionmaker()
    try:
        yield _TEST_DSN
    finally:
        await engine_mod.dispose_engine()
        session_mod.dispose_sessionmaker()
        settings.SUPAVISOR_DATABASE_URL = old


@pytest.fixture
async def pg():
    conn = await asyncpg.connect(_TEST_DSN)
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def users(pg):
    """Two accounts — the second exists purely to prove it stays invisible."""
    alice, bob = uuid.uuid4(), uuid.uuid4()
    await pg.execute("INSERT INTO auth.users (id) VALUES ($1), ($2)", alice, bob)
    try:
        yield {"alice": alice, "bob": bob}
    finally:
        # playback_positions FKs auth.users ON DELETE CASCADE, so this also
        # clears anything the tests wrote.
        await pg.execute(
            "DELETE FROM auth.users WHERE id = ANY($1::uuid[])", [alice, bob]
        )


def _repo():
    from app.repositories.playback_positions_repository import (
        PlaybackPositionsRepository,
    )

    return PlaybackPositionsRepository()


async def _scoped(user_id, coro_factory):
    from app.db.scope import Scope, request_scope

    async with request_scope(Scope(user_id=str(user_id))):
        return await coro_factory()


@_skip
async def test_second_write_updates_in_place_and_moves_updated_at(orm_dsn, pg, users):
    alice = users["alice"]
    repo = _repo()

    first = await _scoped(
        alice,
        lambda: repo.upsert(
            user_id=str(alice),
            media_key=KEY,
            position_seconds=30.0,
            duration_seconds=600.0,
        ),
    )
    # Postgres `now()` is transaction time; without a real gap two upserts in
    # the same millisecond could tie and hide a frozen timestamp.
    await asyncio.sleep(0.05)
    second = await _scoped(
        alice,
        lambda: repo.upsert(
            user_id=str(alice),
            media_key=KEY,
            position_seconds=137.5,
            duration_seconds=600.0,
        ),
    )

    rows = await pg.fetchval(
        "SELECT count(*) FROM public.playback_positions "
        "WHERE user_id = $1 AND media_key = $2",
        alice,
        KEY,
    )
    assert rows == 1, "ON CONFLICT did not find the unique index — appended instead"
    assert second["position_seconds"] == 137.5
    assert second["updated_at"] > first["updated_at"], (
        "updated_at did not move on the UPDATE arm; the client can no longer "
        "tell its own write from another device's"
    )


@_skip
async def test_one_user_cannot_see_anothers_history(orm_dsn, pg, users):
    alice, bob = users["alice"], users["bob"]
    repo = _repo()

    await _scoped(
        alice,
        lambda: repo.upsert(
            user_id=str(alice),
            media_key=KEY,
            position_seconds=137.5,
            duration_seconds=600.0,
        ),
    )

    bobs = await _scoped(bob, lambda: repo.get_many(user_id=str(bob), media_keys=[KEY]))
    assert bobs == [], "UserScoped did not filter — viewing history leaked"

    alices = await _scoped(
        alice, lambda: repo.get_many(user_id=str(alice), media_keys=[KEY])
    )
    assert [r["position_seconds"] for r in alices] == [137.5]


@_skip
async def test_two_users_keep_separate_rows_for_the_same_media(orm_dsn, pg, users):
    """The unique index is (user_id, media_key), not media_key: a shared team
    video must not make two people fight over one row."""
    alice, bob = users["alice"], users["bob"]
    repo = _repo()

    await _scoped(
        alice,
        lambda: repo.upsert(
            user_id=str(alice),
            media_key=KEY,
            position_seconds=300.0,
            duration_seconds=600.0,
        ),
    )
    await _scoped(
        bob,
        lambda: repo.upsert(
            user_id=str(bob),
            media_key=KEY,
            position_seconds=120.0,
            duration_seconds=600.0,
        ),
    )

    assert (
        await pg.fetchval(
            "SELECT count(*) FROM public.playback_positions WHERE media_key = $1", KEY
        )
        == 2
    )
    a = await _scoped(
        alice, lambda: repo.get_many(user_id=str(alice), media_keys=[KEY])
    )
    b = await _scoped(bob, lambda: repo.get_many(user_id=str(bob), media_keys=[KEY]))
    assert a[0]["position_seconds"] == 300.0
    assert b[0]["position_seconds"] == 120.0


@_skip
async def test_delete_removes_only_the_callers_row(orm_dsn, pg, users):
    alice, bob = users["alice"], users["bob"]
    repo = _repo()
    for who, pos in ((alice, 300.0), (bob, 120.0)):
        await _scoped(
            who,
            lambda w=who, p=pos: repo.upsert(
                user_id=str(w),
                media_key=KEY,
                position_seconds=p,
                duration_seconds=600.0,
            ),
        )

    await _scoped(bob, lambda: repo.delete(user_id=str(bob), media_key=KEY))

    remaining = await pg.fetch(
        "SELECT user_id FROM public.playback_positions WHERE media_key = $1", KEY
    )
    assert [r["user_id"] for r in remaining] == [alice]


@_skip
async def test_batch_read_is_capped(orm_dsn, pg, users):
    """An unbounded IN-list is one request scanning the table."""
    from app.repositories.playback_positions_repository import MAX_KEYS_PER_READ

    alice = users["alice"]
    repo = _repo()
    keys = [f"resource:{i}" for i in range(MAX_KEYS_PER_READ + 10)]
    for k in keys[: MAX_KEYS_PER_READ + 10]:
        await _scoped(
            alice,
            lambda kk=k: repo.upsert(
                user_id=str(alice),
                media_key=kk,
                position_seconds=30.0,
                duration_seconds=600.0,
            ),
        )

    got = await _scoped(
        alice, lambda: repo.get_many(user_id=str(alice), media_keys=keys)
    )
    assert len(got) == MAX_KEYS_PER_READ


@_skip
async def test_check_constraints_reject_impossible_rows(orm_dsn, pg, users):
    """The Python schema mirrors these; this proves the server enforces them
    too, so a caller that bypasses the API cannot write nonsense."""
    alice = users["alice"]
    for pos, dur, key in ((-1.0, 600.0, "a"), (1.0, 0.0, "b"), (1.0, 600.0, "")):
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await pg.execute(
                "INSERT INTO public.playback_positions "
                "(user_id, media_key, position_seconds, duration_seconds) "
                "VALUES ($1,$2,$3,$4)",
                alice,
                key,
                pos,
                dur,
            )
