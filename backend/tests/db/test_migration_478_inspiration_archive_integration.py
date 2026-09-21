"""mig 478 against a real Postgres: an archived note leaves every surface.

Why this has to run on a live database
======================================
"Out of sight" is a claim about four different queries, and two of them are
SQL functions (``inspiration_tag_counts``, ``inspiration_activity``). A stub
can show the backend called them; only Postgres can say whether an archived
note is still counted inside one.

That gap is the whole risk here. Forgetting the list predicate is loud — the
note is visibly still there. Forgetting the tag-count predicate is silent: the
sidebar just reads one higher than the list it describes, and no test that
mocks the function would ever notice.

  INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/drift \\
    uv run pytest tests/db/test_migration_478_inspiration_archive_integration.py -v

Skips cleanly when the DSN is unset. The schema-drift workflow runs it through
pytest-no-full-skip.sh, so a full skip there is RED, not green.
"""

from __future__ import annotations

import datetime
import os
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

pytest.importorskip("asyncpg")

_skip = pytest.mark.skipif(
    not _TEST_DSN,
    reason="INTEGRATION_DATABASE_URL not set — mig 478 needs a real database.",
)

_DAY = datetime.date(2026, 9, 20)


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


async def _mk_note(
    conn,
    user_id: uuid.UUID,
    *,
    body: str,
    tags: list[str],
    archived: bool = False,
    pinned: bool = False,
    day: datetime.date = _DAY,
) -> int:
    return await conn.fetchval(
        """
        INSERT INTO inspiration_notes
            (user_id, content_md, tags, note_date, pinned, archived_at)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING id
        """,
        user_id,
        body,
        tags,
        day,
        pinned,
        datetime.datetime.now(datetime.timezone.utc) if archived else None,
    )


async def _live(conn, user_id: uuid.UUID) -> list[str]:
    rows = await conn.fetch(
        """
        SELECT content_md FROM inspiration_notes
        WHERE user_id = $1 AND deleted_at IS NULL AND archived_at IS NULL
        ORDER BY id DESC
        """,
        user_id,
    )
    return [r["content_md"] for r in rows]


async def _archived(conn, user_id: uuid.UUID) -> list[str]:
    rows = await conn.fetch(
        """
        SELECT content_md FROM inspiration_notes
        WHERE user_id = $1 AND deleted_at IS NULL AND archived_at IS NOT NULL
        ORDER BY archived_at DESC
        """,
        user_id,
    )
    return [r["content_md"] for r in rows]


# ---------------------------------------------------------------------------
# The column
# ---------------------------------------------------------------------------


@_skip
async def test_a_new_note_is_not_archived(conn):
    """The column has to default to "not archived" or the migration hides
    every note that already exists."""
    uid = await _mk_user(conn)
    await _mk_note(conn, uid, body="fresh", tags=[])

    assert await _live(conn, uid) == ["fresh"]
    assert await _archived(conn, uid) == []


# ---------------------------------------------------------------------------
# Surface 1 & 2: the list, both directions
# ---------------------------------------------------------------------------


@_skip
async def test_the_list_shows_one_and_the_archive_shows_the_other(conn):
    uid = await _mk_user(conn)
    await _mk_note(conn, uid, body="kept", tags=["a"])
    await _mk_note(conn, uid, body="put away", tags=["a"], archived=True)

    assert await _live(conn, uid) == ["kept"]
    assert await _archived(conn, uid) == ["put away"]


@_skip
async def test_the_archive_is_newest_put_away_first(conn):
    """Not creation order: the archive answers "what did I just tuck away"."""
    uid = await _mk_user(conn)
    older = await _mk_note(conn, uid, body="archived first", tags=[], archived=True)
    newer = await _mk_note(conn, uid, body="archived second", tags=[], archived=True)
    # Pin the two timestamps apart; both rows were inserted in the same
    # transaction, where now() does not advance.
    await conn.execute(
        "UPDATE inspiration_notes SET archived_at = $2 WHERE id = $1",
        older,
        datetime.datetime(2026, 9, 1, tzinfo=datetime.timezone.utc),
    )
    await conn.execute(
        "UPDATE inspiration_notes SET archived_at = $2 WHERE id = $1",
        newer,
        datetime.datetime(2026, 9, 19, tzinfo=datetime.timezone.utc),
    )

    assert await _archived(conn, uid) == ["archived second", "archived first"]


# ---------------------------------------------------------------------------
# Surface 3: the tag sidebar's numbers
# ---------------------------------------------------------------------------


@_skip
async def test_tag_counts_stop_counting_archived_notes(conn):
    """The silent one. A stale count reads one higher than the list it labels."""
    uid = await _mk_user(conn)
    await _mk_note(conn, uid, body="kept #model", tags=["model"])
    await _mk_note(conn, uid, body="away #model", tags=["model"], archived=True)

    rows = await conn.fetch("SELECT tag, cnt FROM inspiration_tag_counts($1)", uid)
    assert {r["tag"]: r["cnt"] for r in rows} == {"model": 1}


@_skip
async def test_a_tag_only_on_archived_notes_leaves_the_sidebar(conn):
    """Not just a smaller number — the row itself goes, or the sidebar offers a
    filter that returns nothing."""
    uid = await _mk_user(conn)
    await _mk_note(conn, uid, body="kept #model", tags=["model"])
    await _mk_note(conn, uid, body="away #retired", tags=["retired"], archived=True)

    rows = await conn.fetch("SELECT tag FROM inspiration_tag_counts($1)", uid)
    assert [r["tag"] for r in rows] == ["model"]


# ---------------------------------------------------------------------------
# Surface 4: the activity calendar
# ---------------------------------------------------------------------------


@_skip
async def test_activity_stops_counting_archived_notes(conn):
    uid = await _mk_user(conn)
    await _mk_note(conn, uid, body="kept", tags=[])
    await _mk_note(conn, uid, body="away", tags=[], archived=True)

    rows = await conn.fetch(
        "SELECT day, cnt FROM inspiration_activity($1, $2, $3)",
        uid,
        _DAY,
        _DAY,
    )
    assert [(r["day"], r["cnt"]) for r in rows] == [(_DAY, 1)]


@_skip
async def test_a_day_of_only_archived_notes_drops_off_the_calendar(conn):
    uid = await _mk_user(conn)
    await _mk_note(conn, uid, body="away", tags=[], archived=True)

    rows = await conn.fetch(
        "SELECT day FROM inspiration_activity($1, $2, $3)", uid, _DAY, _DAY
    )
    assert rows == []


# ---------------------------------------------------------------------------
# Neighbours
# ---------------------------------------------------------------------------


@_skip
async def test_archived_is_not_deleted(conn):
    """Two separate doors. Archiving must not set deleted_at — that one has no
    way back in the UI."""
    uid = await _mk_user(conn)
    nid = await _mk_note(conn, uid, body="away", tags=[], archived=True)

    row = await conn.fetchrow(
        "SELECT deleted_at, archived_at FROM inspiration_notes WHERE id = $1", nid
    )
    assert row["deleted_at"] is None
    assert row["archived_at"] is not None


@_skip
async def test_a_deleted_note_stays_out_of_the_archive(conn):
    """Delete wins over archive: a note archived and then deleted must not
    reappear in the archive view."""
    uid = await _mk_user(conn)
    nid = await _mk_note(conn, uid, body="away then deleted", tags=[], archived=True)
    await conn.execute(
        "UPDATE inspiration_notes SET deleted_at = now() WHERE id = $1", nid
    )

    assert await _archived(conn, uid) == []


@_skip
async def test_the_archive_never_reaches_across_users(conn):
    uid = await _mk_user(conn)
    other = await _mk_user(conn)
    await _mk_note(conn, uid, body="mine", tags=["t"], archived=True)
    await _mk_note(conn, other, body="theirs", tags=["t"], archived=True)

    assert await _archived(conn, uid) == ["mine"]


# ---------------------------------------------------------------------------
# The indexes the predicates lean on
# ---------------------------------------------------------------------------


@_skip
async def test_both_partial_indexes_exist(conn):
    """The list gained a second predicate; without a matching partial index it
    falls back to a scan that grows with the archive."""
    rows = await conn.fetch(
        """
        SELECT indexname FROM pg_indexes
        WHERE tablename = 'inspiration_notes'
          AND indexname IN ('idx_inspiration_notes_user_live',
                            'idx_inspiration_notes_user_archived')
        """
    )
    assert {r["indexname"] for r in rows} == {
        "idx_inspiration_notes_user_live",
        "idx_inspiration_notes_user_archived",
    }


# ---------------------------------------------------------------------------
# The repository's own statements, executed by Postgres
# ---------------------------------------------------------------------------
#
# Everything above reads the shape mig 478 built. This part runs the code that
# will query it. The unit tests around InspirationNotesRepository stub the
# session, so these two statements have never been seen by a server:
#
#   * the archive's keyset is a ROW comparison, ``(archived_at, id) < ($1, $2)``.
#     SQLAlchemy compiles a tuple_ against a plain tuple without complaint; only
#     asyncpg can say whether both halves bind (a bare ISO string against a
#     timestamptz does not — the same trap ``_date`` exists for).
#   * ``set_archived`` clears ``pinned`` in the same UPDATE, and returns the
#     whole row. Whether RETURNING yields that row as mappings is a dialect fact.


@pytest.fixture
async def orm_dsn():
    """Repoint the ORM engine at the test DSN, then restore and dispose so no
    other test inherits a stray engine. Same pattern as the assets repo file."""
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
async def committed_user():
    """A user whose notes really are committed (the repo opens its own
    connection, so the rollback fixture above cannot reach them). Torn down in
    a finally, so the file stays re-runnable against the same database."""
    conn = await asyncpg.connect(_TEST_DSN)
    uid = uuid.uuid4()
    await conn.execute(
        "INSERT INTO auth.users (id, email) VALUES ($1, $2)",
        uid,
        f"{uid}@example.test",
    )
    try:
        yield uid, conn
    finally:
        await conn.execute("DELETE FROM inspiration_notes WHERE user_id = $1", uid)
        await conn.execute("DELETE FROM auth.users WHERE id = $1", uid)
        await conn.close()


@_skip
async def test_the_repository_splits_the_two_views(orm_dsn, committed_user):
    from app.repositories.inspiration_repository import (
        InspirationNotesRepository,
    )

    uid, conn = committed_user
    repo = InspirationNotesRepository()
    kept = await repo.create(str(uid), "kept", ["a"], "2026-09-20")
    away = await repo.create(str(uid), "put away", ["a"], "2026-09-20")
    await repo.set_archived(away["id"], True)

    live = await repo.list(str(uid))
    archived = await repo.list(str(uid), archived=True)

    assert [r["content_md"] for r in live] == ["kept"]
    assert [r["content_md"] for r in archived] == ["put away"]
    assert str(live[0]["id"]) == str(kept["id"])


@_skip
async def test_archiving_gives_up_the_pin(orm_dsn, committed_user):
    """A pin is a claim on the top of the default list. Restoring a note days
    later must not silently jump it to the top for a reason the user cannot
    see, so the pin goes when the note does."""
    uid, conn = committed_user
    from app.repositories.inspiration_repository import InspirationNotesRepository

    repo = InspirationNotesRepository()
    note = await repo.create(str(uid), "pinned then archived", [], "2026-09-20")
    await repo.update(note["id"], pinned=True)

    row = await repo.set_archived(note["id"], True)
    assert row["pinned"] is False
    assert row["archived_at"] is not None

    back = await repo.set_archived(note["id"], False)
    assert back["archived_at"] is None
    assert back["pinned"] is False  # given up, not suspended


@_skip
async def test_the_archive_pages_without_skipping_a_row(orm_dsn, committed_user):
    """The archive orders by archived_at with id breaking ties, so its cursor
    has to be that pair compared as a row. This fixture defeats either half on
    its own: two notes share a timestamp (so a timestamp-only cursor steps past
    one of them), and the archiving order is the reverse of the creation order
    (so an id-only cursor walks off the end after two rows). Paging one at a
    time must still return all four, newest put away first.
    """
    uid, conn = committed_user
    from app.repositories.inspiration_repository import InspirationNotesRepository

    repo = InspirationNotesRepository()
    ids = {}
    for body in ("a", "b", "c", "d"):  # ids ascending in this order
        ids[body] = (await repo.create(str(uid), body, [], "2026-09-20"))["id"]
    # d and c were archived long ago; b and a were put away together, just now.
    utc = datetime.timezone.utc
    for body, when in (
        ("a", datetime.datetime(2026, 9, 19, 10, tzinfo=utc)),
        ("b", datetime.datetime(2026, 9, 19, 10, tzinfo=utc)),
        ("c", datetime.datetime(2026, 9, 10, tzinfo=utc)),
        ("d", datetime.datetime(2026, 9, 1, tzinfo=utc)),
    ):
        await conn.execute(
            "UPDATE inspiration_notes SET archived_at = $2 WHERE id = $1",
            int(ids[body]),
            when,
        )

    seen, cursor = [], None
    for _ in range(8):  # bounded: a broken cursor must not loop forever
        page = await repo.list(
            str(uid),
            archived=True,
            limit=1,
            before_id=cursor[0] if cursor else None,
            before_archived_at=cursor[1] if cursor else None,
        )
        if not page:
            break
        seen.append(page[0]["content_md"])
        cursor = (page[0]["id"], page[0]["archived_at"])

    assert seen == ["b", "a", "c", "d"]


@_skip
async def test_the_default_list_still_pages_by_id_alone(orm_dsn, committed_user):
    """The live view's cursor did not change — a caller that passes only
    before_id keeps working."""
    uid, conn = committed_user
    from app.repositories.inspiration_repository import InspirationNotesRepository

    repo = InspirationNotesRepository()
    for body in ("first", "second"):
        await repo.create(str(uid), body, [], "2026-09-20")

    page1 = await repo.list(str(uid), limit=1)
    page2 = await repo.list(str(uid), limit=1, before_id=page1[0]["id"])

    assert [page1[0]["content_md"], page2[0]["content_md"]] == ["second", "first"]
