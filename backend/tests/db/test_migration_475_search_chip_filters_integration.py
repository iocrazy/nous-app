"""mig 475 against a real Postgres: keyword search honours the filter chips.

Why this has to run on a live database
======================================
The defect and the fix are both *inside a SQL function*. A stubbed session can
assert that the backend passed ``p_ai_transcribed=True``; only Postgres can
say whether a row that is not transcribed still comes back.

That distinction is exactly how the bug survived: the backend never passed the
parameter at all, every unit test was green, and the only place the truth
existed was a query nobody ran.

  INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/drift \\
    uv run pytest tests/db/test_migration_475_search_chip_filters_integration.py -v

Skips cleanly when the DSN is unset. The schema-drift workflow runs it through
pytest-no-full-skip.sh, so a full skip there is RED, not green.
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

pytest.importorskip("asyncpg")

_skip = pytest.mark.skipif(
    not _TEST_DSN,
    reason="INTEGRATION_DATABASE_URL not set — mig 475 needs a real database.",
)

#: The shared word every seeded row matches, so a query alone never separates
#: them and only a chip filter can.
_WORD = "seeddouyin"

_FIELDS = ["title", "description", "author", "hashtags"]


@pytest.fixture
async def conn():
    """A connection whose writes are always rolled back."""
    c = await asyncpg.connect(_TEST_DSN)
    tx = c.transaction()
    await tx.start()
    try:
        yield c
    finally:
        await tx.rollback()
        await c.close()


async def _mk_user(conn) -> uuid.UUID:
    uid = uuid.uuid4()
    await conn.execute(
        "INSERT INTO auth.users (id, email) VALUES ($1, $2)",
        uid,
        f"{uid}@example.test",
    )
    return uid


async def _mk_media(
    conn,
    user_id: uuid.UUID,
    *,
    title: str,
    # 'none' is what an untranscribed row actually holds — the column is NOT
    # NULL with that default, so seeding NULL would test a shape production
    # never has.
    transcript_status: str = "none",
    platform: str | None = "douyin",
    rating: int | None = None,
) -> int:
    """One parsed_media + the owning resources row, both matching ``_WORD``."""
    media_id = await conn.fetchval(
        """
        INSERT INTO parsed_media (platform_id, original_url, title, source_platform,
                                  media_type, like_count, comment_count)
        VALUES ($1, $2, $3, $4, 'video', 0, 0)
        RETURNING id
        """,
        f"pid_{uuid.uuid4().hex[:12]}",
        f"https://example.test/{uuid.uuid4().hex[:8]}",
        title,
        platform,
    )
    await conn.execute(
        """
        INSERT INTO resources (creator_id, source_type, filename, media_id,
                               is_trashed, transcript_status, rating)
        VALUES ($1, 'web', $2, $3, false, $4, $5)
        """,
        user_id,
        f"{title}.mp4",
        media_id,
        transcript_status,
        rating,
    )
    return media_id


async def _search(conn, user_id: uuid.UUID, **params) -> list[str]:
    """Call the RPC by NAME, the way the backend does, and return hit titles.

    Named arguments are deliberate: they are what proves the parameter the
    caller thinks it is setting is the one the function reads.
    """
    named = ", ".join(f"{k} => ${i + 3}" for i, k in enumerate(params))
    sql = (
        "SELECT public.rpc_user_media_text_search("
        "p_user_id => $1, p_pattern => $2, p_fields => "
        f"'{{{','.join(_FIELDS)}}}'::text[]" + (f", {named}" if named else "") + ")"
    )
    raw = await conn.fetchval(sql, user_id, f"%{_WORD}%", *params.values())
    import json

    payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
    return sorted(r["title"] for r in (payload.get("rows") or []))


# ---------------------------------------------------------------------------
# Exactly one implementation exists
# ---------------------------------------------------------------------------


@_skip
async def test_only_one_overload_survives(conn):
    """Two overloads would make an 8-argument call ambiguous, not "the old one".

    A superset signature with defaults matches every call the narrow one does,
    so leaving both would turn each existing caller into
    "function is not unique" — a hard error, not a graceful fallback.
    """
    n = await conn.fetchval(
        """
        SELECT count(*) FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE p.proname = 'rpc_user_media_text_search' AND n.nspname = 'public'
        """
    )
    assert n == 1


@_skip
async def test_the_browser_still_cannot_reach_it(conn):
    """DROP discards the grant mig 463 revoked; a fresh function is PUBLIC again.

    The function takes the target user's id as an ordinary argument and runs
    SECURITY DEFINER, so reachable-from-the-browser means "swap the uuid, read
    anyone's library". Re-stating the REVOKE is not tidiness.
    """
    acl = await conn.fetchval(
        """
        SELECT coalesce(array_to_string(p.proacl, ','), '')
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE p.proname = 'rpc_user_media_text_search' AND n.nspname = 'public'
        """
    )
    assert "anon=X" not in acl
    assert "authenticated=X" not in acl
    assert "=X/" not in acl.replace("postgres=X/postgres", "")


# ---------------------------------------------------------------------------
# The reported defect
# ---------------------------------------------------------------------------


@_skip
async def test_without_a_chip_every_match_comes_back(conn):
    """The control. Without it, a filter test could pass by returning nothing."""
    uid = await _mk_user(conn)
    await _mk_media(conn, uid, title=f"a {_WORD}", transcript_status="completed")
    await _mk_media(conn, uid, title=f"b {_WORD}", transcript_status="none")

    assert await _search(conn, uid) == [f"a {_WORD}", f"b {_WORD}"]


@_skip
async def test_transcribed_chip_drops_the_untranscribed_hit(conn):
    """The exact shape of the report: "抖音" returned rows that are not transcribed.

    Reproduced with the production ratio — one transcribed row among several
    that are not, all matching the query.
    """
    uid = await _mk_user(conn)
    await _mk_media(conn, uid, title=f"kept {_WORD}", transcript_status="completed")
    await _mk_media(conn, uid, title=f"drop1 {_WORD}", transcript_status="none")
    await _mk_media(conn, uid, title=f"drop2 {_WORD}", transcript_status="pending")

    assert await _search(conn, uid, p_ai_transcribed=True) == [f"kept {_WORD}"]


@_skip
async def test_a_false_chip_is_not_a_filter(conn):
    """The chip sends nothing when off, but false must read as "no opinion".

    Reading false as "require not-transcribed" would invert the filter for any
    caller that serialises its whole chip state.
    """
    uid = await _mk_user(conn)
    await _mk_media(conn, uid, title=f"a {_WORD}", transcript_status="completed")
    await _mk_media(conn, uid, title=f"b {_WORD}", transcript_status="none")

    assert await _search(conn, uid, p_ai_transcribed=False) == [
        f"a {_WORD}",
        f"b {_WORD}",
    ]


@_skip
async def test_platform_chip_applies(conn):
    """A second, differently-shaped chip — array over parsed_media, not resources.

    One passing filter would not show that the whole block is wired; these two
    sit in different halves of it.
    """
    uid = await _mk_user(conn)
    await _mk_media(conn, uid, title=f"dy {_WORD}", platform="douyin")
    await _mk_media(conn, uid, title=f"bili {_WORD}", platform="bilibili")

    assert await _search(conn, uid, p_platforms=["douyin"]) == [f"dy {_WORD}"]


@_skip
async def test_rating_chip_applies(conn):
    """resources.rating — and 0 means "no opinion", matching the list path."""
    uid = await _mk_user(conn)
    await _mk_media(conn, uid, title=f"hi {_WORD}", rating=5)
    await _mk_media(conn, uid, title=f"lo {_WORD}", rating=1)

    assert await _search(conn, uid, p_min_rating=3) == [f"hi {_WORD}"]
    assert await _search(conn, uid, p_min_rating=0) == [f"hi {_WORD}", f"lo {_WORD}"]


@_skip
async def test_chips_combine_with_AND(conn):
    """Two chips at once. Either one alone would keep a row the pair must drop."""
    uid = await _mk_user(conn)
    await _mk_media(
        conn,
        uid,
        title=f"both {_WORD}",
        transcript_status="completed",
        platform="douyin",
    )
    await _mk_media(
        conn,
        uid,
        title=f"onlyt {_WORD}",
        transcript_status="completed",
        platform="bilibili",
    )
    await _mk_media(
        conn, uid, title=f"onlyp {_WORD}", transcript_status="none", platform="douyin"
    )

    got = await _search(conn, uid, p_ai_transcribed=True, p_platforms=["douyin"])
    assert got == [f"both {_WORD}"]


@_skip
async def test_a_chip_never_reaches_across_users(conn):
    """The filters must narrow within the caller's library, never widen past it."""
    mine = await _mk_user(conn)
    theirs = await _mk_user(conn)
    await _mk_media(conn, mine, title=f"mine {_WORD}", transcript_status="completed")
    await _mk_media(
        conn, theirs, title=f"theirs {_WORD}", transcript_status="completed"
    )

    assert await _search(conn, mine, p_ai_transcribed=True) == [f"mine {_WORD}"]


# ---------------------------------------------------------------------------
# The backend's own SQL, not a hand-written call
# ---------------------------------------------------------------------------
#
# Every test above calls the RPC with SQL written here. That proves the
# function; it does not prove the string SearchService actually sends — named
# arguments, ``CAST(:x AS text[])``, SQLAlchemy's ``text()`` bind rewriting and
# asyncpg's type inference all sit between the two. The unit tests stub the
# session, so until this test ran nothing had ever put that string in front of
# Postgres.


@pytest.fixture
async def orm_session():
    """A real AsyncSession on the drift DB, installed as the ambient session.

    ``read_scope()`` yields the ambient session when one is set, so the service
    reads inside this transaction and sees the rows seeded below — and the
    rollback leaves nothing behind.
    """
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from app.db.session import _request_session

    dsn = _TEST_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(dsn)
    session = AsyncSession(engine, expire_on_commit=False)
    token = _request_session.set(session)
    try:
        yield session
    finally:
        _request_session.reset(token)
        await session.rollback()
        await session.close()
        await engine.dispose()


@_skip
async def test_the_service_sql_is_accepted_and_applies_the_chip(orm_session):
    """The reported case, end to end through SearchService's real SQL string."""
    from sqlalchemy import text as sa_text

    from app.schemas.search import LibraryChipFilters
    from app.services.library.search_service import SearchService

    uid = uuid.uuid4()
    await orm_session.execute(
        sa_text("INSERT INTO auth.users (id, email) VALUES (:id, :e)"),
        {"id": uid, "e": f"{uid}@example.test"},
    )
    for title, status in ((f"kept {_WORD}", "completed"), (f"drop {_WORD}", "none")):
        mid = (
            await orm_session.execute(
                sa_text(
                    "INSERT INTO parsed_media (platform_id, original_url, title, "
                    "source_platform, media_type, like_count, comment_count) "
                    "VALUES (:p, :u, :t, 'douyin', 'video', 0, 0) RETURNING id"
                ),
                {
                    "p": f"pid_{uuid.uuid4().hex[:12]}",
                    "u": f"https://example.test/{uuid.uuid4().hex[:8]}",
                    "t": title,
                },
            )
        ).scalar_one()
        await orm_session.execute(
            sa_text(
                "INSERT INTO resources (creator_id, source_type, filename, media_id, "
                "is_trashed, transcript_status) "
                "VALUES (:c, 'web', :f, :m, false, :s)"
            ),
            {"c": uid, "f": f"{title}.mp4", "m": mid, "s": status},
        )

    svc = SearchService.__new__(SearchService)  # skip embedding client setup

    unfiltered = await svc.search_user_media_text(
        user_id=str(uid), pattern=f"%{_WORD}%", fields=list(_FIELDS)
    )
    assert sorted(r["title"] for r in unfiltered) == [f"drop {_WORD}", f"kept {_WORD}"]

    filtered = await svc.search_user_media_text(
        user_id=str(uid),
        pattern=f"%{_WORD}%",
        fields=list(_FIELDS),
        filters=LibraryChipFilters(ai_transcribed=True, platforms=["douyin"]),
    )
    assert [r["title"] for r in filtered] == [f"kept {_WORD}"]
