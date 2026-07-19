"""Integration tests for the note-tag DB paths (resolve + junction sync + counts).

These exercise the real SQLAlchemy statements against a live Postgres built the
CI way (ci_bootstrap.sql → schema_baseline.sql → migrations above the watermark,
which includes migration 368's ``tags.origin`` + ``note_tags`` + the
``unique_tag_per_scope`` index). They cover the paths that the boundary-stub unit
tests (``test_tags_resolve_note_tags.py`` / ``test_note_tags_repository.py``)
cannot: the ON CONFLICT re-select under a real unique index, the junction
diff-sync round-trip, and the live-note count join with soft-delete.

Gated on INTEGRATION_DATABASE_URL — skips cleanly when unset (CI's unit lane and
local runs without a DB stay green). Point it at any CI-way Postgres:

  INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5467/drift \
    uv run pytest tests/db/test_note_tags_integration.py -v

The repo methods route through the ORM engine (app.db.session), so the fixture
below repoints that engine at the same DSN; asyncpg is used directly only for
row setup and assertions.
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
    reason="INTEGRATION_DATABASE_URL not set — note-tag integration tests need a DB.",
)


@pytest.fixture
async def orm_dsn():
    """Repoint the ORM engine (app.db.session read/write scope) at the test DSN
    for the duration of a test, then restore + dispose so no other test inherits
    a stray engine."""
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
    """A raw asyncpg connection for setup/assertions (plain libpq DSN)."""
    conn = await asyncpg.connect(_TEST_DSN)
    try:
        yield conn
    finally:
        await conn.close()


async def _new_user(pg) -> str:
    """Create a fresh auth.users row (tags.user_id FK-references auth.users) and
    return its id."""
    uid = uuid.uuid4()
    await pg.execute("INSERT INTO auth.users (id) VALUES ($1)", uid)
    return str(uid)


@_skip
async def test_resolve_note_tags_creates_shadow_tag_and_is_idempotent_on_rerun(
    orm_dsn, pg
):
    """Same word twice → same id both times, exactly one tags row, origin='note'.
    Also covers the ``.strip()`` normalization guard (whitespace is trimmed and a
    whitespace-only word is dropped)."""
    from app.repositories.tags_repository import TagsRepository

    repo = TagsRepository()
    user_id = await _new_user(pg)

    # Leading/trailing whitespace must be trimmed; a whitespace-only word dropped.
    first = await repo.resolve_note_tags(user_id, ["  Marketing  ", "   "])
    assert len(first) == 1
    second = await repo.resolve_note_tags(user_id, ["marketing"])
    assert first == second  # idempotent — same id on rerun

    rows = await pg.fetch(
        "SELECT id, name, origin, type FROM tags "
        "WHERE user_id = $1 AND name = 'marketing'",
        uuid.UUID(user_id),
    )
    assert len(rows) == 1  # exactly one row, no duplicate
    assert rows[0]["origin"] == "note"
    assert rows[0]["type"] == "user"
    assert rows[0]["name"] == "marketing"  # stripped
    assert int(rows[0]["id"]) == first[0]


@_skip
async def test_resolve_note_tags_conflict_takes_reselect_branch(orm_dsn, pg):
    """Two concurrent resolves for a brand-new word: one wins the INSERT, the
    other hits ON CONFLICT DO NOTHING → None → the re-select branch. Both return
    the same id, exactly one row exists, and nothing raises."""
    from app.repositories.tags_repository import TagsRepository

    repo = TagsRepository()
    user_id = await _new_user(pg)

    a, b = await asyncio.gather(
        repo.resolve_note_tags(user_id, ["storytelling"]),
        repo.resolve_note_tags(user_id, ["storytelling"]),
    )
    assert a == b  # both resolve to the same tag id (re-select found the winner)

    rows = await pg.fetch(
        "SELECT id FROM tags WHERE user_id = $1 AND name = 'storytelling'",
        uuid.UUID(user_id),
    )
    assert len(rows) == 1  # no duplicate despite the race
    assert int(rows[0]["id"]) == a[0]


@_skip
async def test_sync_and_counts_roundtrip(orm_dsn, pg):
    """create note + tags → sync([a,b]) → re-sync([b,c]) leaves exactly {b,c};
    counts reflect it; soft-deleting the note drops counts to 0."""
    from app.repositories.note_tags_repository import NoteTagsRepository
    from app.repositories.tags_repository import TagsRepository

    tags_repo = TagsRepository()
    nt_repo = NoteTagsRepository()
    user_id = await _new_user(pg)

    note_id = await pg.fetchval(
        "INSERT INTO inspiration_notes (user_id, note_date) "
        "VALUES ($1, CURRENT_DATE) RETURNING id",
        uuid.UUID(user_id),
    )

    a, b, c = await tags_repo.resolve_note_tags(user_id, ["alpha", "beta", "gamma"])

    await nt_repo.sync_for_note(note_id, [a, b])
    junction = {
        int(r["tag_id"])
        for r in await pg.fetch(
            "SELECT tag_id FROM note_tags WHERE note_id = $1", int(note_id)
        )
    }
    assert junction == {a, b}

    await nt_repo.sync_for_note(note_id, [b, c])
    junction = {
        int(r["tag_id"])
        for r in await pg.fetch(
            "SELECT tag_id FROM note_tags WHERE note_id = $1", int(note_id)
        )
    }
    assert junction == {b, c}  # exactly {b, c} — a removed, c added

    counts = await nt_repo.counts_for_user(user_id)
    assert counts.get(b) == 1
    assert counts.get(c) == 1
    assert a not in counts

    # Soft-delete the note → it drops out of the live-note count join.
    await pg.execute(
        "UPDATE inspiration_notes SET deleted_at = now() WHERE id = $1", int(note_id)
    )
    counts_after = await nt_repo.counts_for_user(user_id)
    assert counts_after == {}


@_skip
async def test_service_create_note_persists_atomically_in_one_session(orm_dsn, pg):
    """Drive the SERVICE create path (not repos directly): the note row, the
    resolved tags, and the note_tags junction must ALL be present after one save.

    Also proves the single-connection goal: the note-row write, the tag resolve,
    and the junction sync each observe the SAME ambient unit_of_work session
    (``_request_session``), i.e. they joined one transaction rather than opening
    three fresh connections."""
    import app.db.session as session_mod
    from app.repositories.note_tags_repository import NoteTagsRepository
    from app.repositories.tags_repository import TagsRepository
    from app.services.inspiration import notes_service as notes_svc_mod
    from app.services.inspiration.notes_service import NotesService

    user_id = await _new_user(pg)
    seen: dict[str, object] = {}

    real_tags = TagsRepository()
    real_nt = NoteTagsRepository()
    real_notes = notes_svc_mod.get_inspiration_notes_repository()

    class _RecTags:
        async def resolve_note_tags(self, uid, names):
            seen["resolve"] = session_mod._request_session.get()
            return await real_tags.resolve_note_tags(uid, names)

    class _RecNoteTags:
        async def sync_for_note(self, note_id, tag_ids):
            seen["sync"] = session_mod._request_session.get()
            return await real_nt.sync_for_note(note_id, tag_ids)

    class _RecNotes:
        async def create(self, **kwargs):
            row = await real_notes.create(**kwargs)
            seen["create"] = session_mod._request_session.get()
            return row

    notes_svc_mod.get_tags_repository = lambda: _RecTags()  # type: ignore[assignment]
    notes_svc_mod.get_note_tags_repository = lambda: _RecNoteTags()  # type: ignore
    try:
        svc = NotesService()
        svc._notes = _RecNotes()  # type: ignore[assignment]
        row = await svc.create_note(user_id, "captured idea #alpha #beta")
    finally:
        notes_svc_mod.get_tags_repository = TagsRepository  # restore factories
        notes_svc_mod.get_note_tags_repository = NoteTagsRepository

    assert row is not None
    note_id = int(row["id"])

    # (a) note row present
    note_rows = await pg.fetch(
        "SELECT id FROM inspiration_notes WHERE id = $1 AND deleted_at IS NULL",
        note_id,
    )
    assert len(note_rows) == 1

    # (a) two shadow tags resolved
    tag_rows = await pg.fetch(
        "SELECT name FROM tags WHERE user_id = $1 ORDER BY name",
        uuid.UUID(user_id),
    )
    assert {r["name"] for r in tag_rows} == {"alpha", "beta"}

    # (a) junction wired to exactly those two tags
    junction = await pg.fetch(
        "SELECT tag_id FROM note_tags WHERE note_id = $1", note_id
    )
    assert len(junction) == 2

    # single-session proof: all three writes saw the SAME ambient UoW session
    assert seen["create"] is not None
    assert seen["create"] is seen["resolve"] is seen["sync"]


@_skip
async def test_service_create_note_rolls_back_when_tag_sync_fails(orm_dsn, pg):
    """Force a failure mid-UoW (junction sync raises): the whole save must roll
    back — NO note row, NO orphaned shadow tags — proving the note write and the
    tag sync share ONE transaction (the pre-fix three-transaction path would have
    left the note row committed)."""
    from app.repositories.note_tags_repository import NoteTagsRepository
    from app.repositories.tags_repository import TagsRepository
    from app.services.inspiration import notes_service as notes_svc_mod
    from app.services.inspiration.notes_service import NotesService

    user_id = await _new_user(pg)

    class _BoomNoteTags:
        async def sync_for_note(self, note_id, tag_ids):
            raise RuntimeError("forced sync failure mid-UoW")

    notes_svc_mod.get_note_tags_repository = lambda: _BoomNoteTags()  # type: ignore
    try:
        svc = NotesService()
        with pytest.raises(RuntimeError, match="forced sync failure"):
            await svc.create_note(user_id, "doomed idea #gamma")
    finally:
        notes_svc_mod.get_note_tags_repository = NoteTagsRepository

    # Atomic rollback: the note row must be absent.
    note_rows = await pg.fetch(
        "SELECT id FROM inspiration_notes WHERE user_id = $1", uuid.UUID(user_id)
    )
    assert note_rows == []

    # The shadow tag created by resolve inside the same txn must also be gone.
    tag_rows = await pg.fetch(
        "SELECT id FROM tags WHERE user_id = $1 AND name = 'gamma'",
        uuid.UUID(user_id),
    )
    assert tag_rows == []
