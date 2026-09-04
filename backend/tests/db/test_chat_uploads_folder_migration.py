"""Migration 450 + the code that has to agree with it, against a real Postgres.

WHY THIS FILE EXISTS
────────────────────
Migration 450 is one UPDATE, and everything that makes it safe lives in the
server: ``DISTINCT ON (scope_id) ... ORDER BY id`` picking exactly one row per
scope, the ``NOT EXISTS`` guard that makes a second run a no-op, and the
partial unique index ``ux_folders_scope_system_key`` that is the only reason
"adopt, never insert" is enforceable at all. None of that is checkable with a
stubbed session — a wrong version compiles, runs, and quietly gives every scope
a second folder with the scope's chat-attachment history stranded in the first.

The same file runs ``chat_upload._ensure_chat_uploads_folder`` against the same
schema, because the migration and the code implement the SAME adoption rule in
two languages. If they ever disagree about which ``temp`` folder is the one,
each adopts a different row and the second hits the unique index — in
production, on a user's upload. Here, that shows up as a red test.

Point it at any CI-way Postgres (ci_bootstrap.sql → schema_baseline.sql →
migrations above the watermark):

  INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/drift \\
    uv run pytest tests/db/test_chat_uploads_folder_migration.py -v

Skips cleanly when INTEGRATION_DATABASE_URL is unset. Every test builds its own
teams/folders with fresh ids and tears them down in a ``finally``.
"""

from __future__ import annotations

import os
import pathlib
import uuid
from typing import List

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

pytest.importorskip("asyncpg")
import asyncpg  # noqa: E402

DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
if not DSN:
    pytest.skip("INTEGRATION_DATABASE_URL not set", allow_module_level=True)

from app.services.library.chat_upload import (  # noqa: E402
    CHAT_UPLOADS_DISPLAY_NAME,
    CHAT_UPLOADS_SYSTEM_KEY,
    LEGACY_CHAT_UPLOADS_FOLDER_NAME,
    _ensure_chat_uploads_folder,
)

MIGRATION_SQL = (
    pathlib.Path(__file__).resolve().parents[3]
    / "supabase"
    / "migrations"
    / "450_chat_uploads_system_folder.sql"
).read_text()

USER = uuid.UUID("b2180063-6860-4f97-9785-ad4eede16064")


@pytest.fixture
async def pg():
    conn = await asyncpg.connect(DSN)
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def scopes(pg):
    """Three teams to use as ``folders.scope_id`` (it is a real FK to teams).

    Three because the interesting cases are per-scope and must not be able to
    contaminate each other: one scope's adoption must not touch another's.
    """
    await pg.execute(
        "INSERT INTO auth.users (id, email) VALUES ($1, $2) "
        "ON CONFLICT (id) DO NOTHING",
        USER,
        "chat-uploads-folder-test@nous.test",
    )
    ids: List[int] = []
    for n in range(3):
        ids.append(
            int(
                await pg.fetchval(
                    "INSERT INTO teams (name, owner_id, invite_code) "
                    "VALUES ($1, $2, $3) RETURNING id",
                    f"Chat Uploads Folder Test {n}",
                    USER,
                    uuid.uuid4().hex[:12],
                )
            )
        )
    try:
        yield ids
    finally:
        # Folders first (libraries and teams are referenced by them), and the
        # libraries by TEXT scope_id — that column is not the bigint the
        # folders one is.
        await pg.execute("DELETE FROM folders WHERE scope_id = ANY($1::bigint[])", ids)
        await pg.execute(
            "DELETE FROM libraries WHERE scope_id = ANY($1::text[])",
            [str(i) for i in ids],
        )
        await pg.execute("DELETE FROM teams WHERE id = ANY($1::bigint[])", ids)


@pytest.fixture(autouse=True)
async def _engine():
    """Point the SQLAlchemy engine at the throwaway DB for the ORM half."""
    from unittest.mock import patch

    from app.db import engine as db_engine
    from app.db import session as db_session

    db_engine._engine = None
    db_session.dispose_sessionmaker()
    with patch.object(db_engine.settings, "SUPAVISOR_DATABASE_URL", DSN):
        yield
    await db_engine.dispose_engine()
    db_engine._engine = None
    db_session.dispose_sessionmaker()


async def _mk_folder(
    pg,
    scope_id: int,
    name: str,
    *,
    system_key=None,
    is_system=False,
    trashed=False,
    parent_id=None,
    library_id=None,
) -> int:
    return int(
        await pg.fetchval(
            "INSERT INTO folders (name, scope_id, created_by, system_key, "
            "is_system, is_trashed, parent_id, library_id) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING id",
            name,
            scope_id,
            USER,
            system_key,
            is_system,
            trashed,
            parent_id,
            library_id,
        )
    )


async def _mk_library(pg, scope_id: int) -> int:
    """A library to hang a non-root folder off. ``libraries.scope_id`` is TEXT
    here, not the bigint that ``folders.scope_id`` is — copying the real column
    types matters, a "tidier" int bind is rejected."""
    return int(
        await pg.fetchval(
            "INSERT INTO libraries (name, scope_type, scope_id, created_by) "
            "VALUES ($1, 'team', $2, $3) RETURNING id",
            "Chat Uploads Test Library",
            str(scope_id),
            USER,
        )
    )


async def _run_migration(pg) -> int:
    """Apply 450; return how many rows it changed (asyncpg's 'UPDATE n')."""
    status = await pg.execute(MIGRATION_SQL)
    return int(status.split()[-1])


async def _folder(pg, folder_id: int) -> dict:
    row = await pg.fetchrow(
        "SELECT name, system_key, is_system, is_trashed FROM folders WHERE id = $1",
        folder_id,
    )
    return dict(row)


# ------------------------------------------------------------------ #
# The migration
# ------------------------------------------------------------------ #


class TestMigrationAdoption:
    async def test_a_legacy_temp_folder_is_adopted_in_place(self, pg, scopes):
        """Adopted, not replaced: the row keeps its id, so every
        ``resource_items.folder_id`` pointing at it still resolves."""
        scope = scopes[0]
        legacy = await _mk_folder(pg, scope, LEGACY_CHAT_UPLOADS_FOLDER_NAME)

        await _run_migration(pg)

        after = await _folder(pg, legacy)
        assert after["system_key"] == CHAT_UPLOADS_SYSTEM_KEY
        assert after["is_system"] is True
        assert after["name"] == CHAT_UPLOADS_DISPLAY_NAME
        # No second folder was minted for the scope.
        assert (
            await pg.fetchval("SELECT count(*) FROM folders WHERE scope_id = $1", scope)
            == 1
        )

    async def test_only_the_oldest_temp_folder_in_a_scope_is_adopted(self, pg, scopes):
        """Two ``temp`` folders in one scope: the older (smaller snowflake) is
        the one chat uploads have been landing in, so it is the one that gets
        the identity. The other stays a plain user folder — silently marking it
        system too would lock a folder the user made and can no longer rename."""
        scope = scopes[0]
        first = await _mk_folder(pg, scope, LEGACY_CHAT_UPLOADS_FOLDER_NAME)
        second = await _mk_folder(pg, scope, LEGACY_CHAT_UPLOADS_FOLDER_NAME)
        assert first < second, "snowflake ids must be monotonic for this to mean age"

        await _run_migration(pg)

        assert (await _folder(pg, first))["system_key"] == CHAT_UPLOADS_SYSTEM_KEY
        untouched = await _folder(pg, second)
        assert untouched["system_key"] is None
        assert untouched["is_system"] is False
        assert untouched["name"] == LEGACY_CHAT_UPLOADS_FOLDER_NAME

    async def test_each_scope_is_adopted_independently(self, pg, scopes):
        a, b, _ = scopes
        fa = await _mk_folder(pg, a, LEGACY_CHAT_UPLOADS_FOLDER_NAME)
        fb = await _mk_folder(pg, b, LEGACY_CHAT_UPLOADS_FOLDER_NAME)

        await _run_migration(pg)

        assert (await _folder(pg, fa))["system_key"] == CHAT_UPLOADS_SYSTEM_KEY
        assert (await _folder(pg, fb))["system_key"] == CHAT_UPLOADS_SYSTEM_KEY

    async def test_a_scope_that_already_has_the_key_is_left_alone(self, pg, scopes):
        """The NOT EXISTS guard. Without it the UPDATE would try to key a
        second folder in the scope and the partial unique index would abort the
        whole migration — for every scope, not just this one."""
        scope = scopes[0]
        keyed = await _mk_folder(
            pg,
            scope,
            "Chat Uploads",
            system_key=CHAT_UPLOADS_SYSTEM_KEY,
            is_system=True,
        )
        stray_temp = await _mk_folder(pg, scope, LEGACY_CHAT_UPLOADS_FOLDER_NAME)

        await _run_migration(pg)

        assert (await _folder(pg, keyed))["system_key"] == CHAT_UPLOADS_SYSTEM_KEY
        untouched = await _folder(pg, stray_temp)
        assert untouched["system_key"] is None
        assert untouched["name"] == LEGACY_CHAT_UPLOADS_FOLDER_NAME

    async def test_a_trashed_temp_folder_is_not_adopted(self, pg, scopes):
        """A folder in the bin is not the scope's chat-uploads folder, and the
        unique index does not count it either (``WHERE ... is_trashed = false``)."""
        scope = scopes[0]
        trashed = await _mk_folder(
            pg, scope, LEGACY_CHAT_UPLOADS_FOLDER_NAME, trashed=True
        )

        await _run_migration(pg)

        assert (await _folder(pg, trashed))["system_key"] is None

    async def test_a_folder_keyed_for_something_else_is_never_hijacked(
        self, pg, scopes
    ):
        """``system_key IS NULL`` in the candidate predicate. A cover-template
        folder someone happened to name "temp" is still the cover-template
        folder."""
        scope = scopes[0]
        other = await _mk_folder(
            pg,
            scope,
            LEGACY_CHAT_UPLOADS_FOLDER_NAME,
            system_key="cover_templates",
            is_system=True,
        )

        await _run_migration(pg)

        after = await _folder(pg, other)
        assert after["system_key"] == "cover_templates"
        assert after["name"] == LEGACY_CHAT_UPLOADS_FOLDER_NAME


class TestMigrationAdoptsRootOnly:
    """Root-only is a fact about the folder, not a precaution.

    ``_ensure_temp_folder`` was the only code that ever created a ``temp``
    folder and it passed neither ``parent_id`` nor ``library_id``, so the real
    one is always at the root of the scope's library-less tree. A nested
    "temp" is a user's scratch drawer, and adopting it would set
    ``is_system`` — after which they can no longer rename, move, or delete
    their own folder, while their chat attachments sit somewhere else.
    """

    async def test_a_nested_temp_with_a_smaller_id_is_not_adopted(self, pg, scopes):
        """Explicitly the id-ordering trap: the nested one is OLDER, so a
        rule that only sorted by id would take it."""
        scope = scopes[0]
        parent = await _mk_folder(pg, scope, "My Project")
        nested = await _mk_folder(
            pg, scope, LEGACY_CHAT_UPLOADS_FOLDER_NAME, parent_id=parent
        )
        root = await _mk_folder(pg, scope, LEGACY_CHAT_UPLOADS_FOLDER_NAME)
        assert nested < root, "the nested folder must be the older one here"

        await _run_migration(pg)

        adopted = await _folder(pg, root)
        assert adopted["system_key"] == CHAT_UPLOADS_SYSTEM_KEY
        assert adopted["name"] == CHAT_UPLOADS_DISPLAY_NAME
        untouched = await _folder(pg, nested)
        assert untouched["system_key"] is None
        assert untouched["is_system"] is False
        assert untouched["name"] == LEGACY_CHAT_UPLOADS_FOLDER_NAME

    async def test_a_temp_inside_a_library_with_a_smaller_id_is_not_adopted(
        self, pg, scopes
    ):
        scope = scopes[0]
        library = await _mk_library(pg, scope)
        in_library = await _mk_folder(
            pg, scope, LEGACY_CHAT_UPLOADS_FOLDER_NAME, library_id=library
        )
        root = await _mk_folder(pg, scope, LEGACY_CHAT_UPLOADS_FOLDER_NAME)
        assert in_library < root

        await _run_migration(pg)

        assert (await _folder(pg, root))["system_key"] == CHAT_UPLOADS_SYSTEM_KEY
        assert (await _folder(pg, in_library))["system_key"] is None

    async def test_a_scope_whose_only_temp_is_nested_adopts_nothing(self, pg, scopes):
        """No candidate is the right answer, not a reason to relax the rule.
        The scope simply has no chat-uploads folder yet."""
        scope = scopes[0]
        parent = await _mk_folder(pg, scope, "My Project")
        nested = await _mk_folder(
            pg, scope, LEGACY_CHAT_UPLOADS_FOLDER_NAME, parent_id=parent
        )

        await _run_migration(pg)

        assert (await _folder(pg, nested))["system_key"] is None
        assert (
            await pg.fetchval(
                "SELECT count(*) FROM folders WHERE scope_id = $1 "
                "AND system_key = $2",
                scope,
                CHAT_UPLOADS_SYSTEM_KEY,
            )
            == 0
        )


class TestMigrationIdempotency:
    async def test_a_second_run_changes_zero_rows(self, pg, scopes):
        """Re-running a migration must be free. Both exits are exercised: the
        adopted row no longer matches ``system_key IS NULL``, and the scope's
        leftover ``temp`` folder is now blocked by the NOT EXISTS guard."""
        a, b, c = scopes
        await _mk_folder(pg, a, LEGACY_CHAT_UPLOADS_FOLDER_NAME)
        await _mk_folder(pg, b, LEGACY_CHAT_UPLOADS_FOLDER_NAME)
        await _mk_folder(pg, b, LEGACY_CHAT_UPLOADS_FOLDER_NAME)
        await _mk_folder(
            pg, c, "Chat Uploads", system_key=CHAT_UPLOADS_SYSTEM_KEY, is_system=True
        )

        first = await _run_migration(pg)
        assert first >= 2, "the two unkeyed scopes must be adopted on run 1"

        assert await _run_migration(pg) == 0
        assert await _run_migration(pg) == 0

    async def test_the_partial_unique_index_holds_after_the_migration(self, pg, scopes):
        """The guarantee the whole design rests on: one live keyed folder per
        scope. If this ever stops raising, "adopt, never insert" is unenforced."""
        scope = scopes[0]
        await _mk_folder(pg, scope, LEGACY_CHAT_UPLOADS_FOLDER_NAME)
        await _run_migration(pg)

        with pytest.raises(asyncpg.UniqueViolationError):
            await _mk_folder(
                pg,
                scope,
                "Chat Uploads",
                system_key=CHAT_UPLOADS_SYSTEM_KEY,
                is_system=True,
            )


# ------------------------------------------------------------------ #
# The code half — same rule, same schema
# ------------------------------------------------------------------ #


class TestCodeAgreesWithTheMigration:
    async def test_the_code_adopts_the_same_row_the_migration_would(self, pg, scopes):
        """Two scopes, identical shapes, one adopted by each side. They must
        land on the same row — that agreement is what lets the migration and
        the deploy arrive in either order.

        ORDER MATTERS IN THE SETUP: migration 450 is GLOBAL, so the code
        scope's folders are created AFTER it runs. Build them first and the
        migration adopts them too, the code's lookup returns the keyed row
        without ever reaching its own candidate query, and this test passes
        while asserting nothing about the code at all.
        """
        by_migration, by_code, _ = scopes
        m_first = await _mk_folder(pg, by_migration, LEGACY_CHAT_UPLOADS_FOLDER_NAME)
        m_second = await _mk_folder(pg, by_migration, LEGACY_CHAT_UPLOADS_FOLDER_NAME)

        await _run_migration(pg)

        c_first = await _mk_folder(pg, by_code, LEGACY_CHAT_UPLOADS_FOLDER_NAME)
        c_second = await _mk_folder(pg, by_code, LEGACY_CHAT_UPLOADS_FOLDER_NAME)
        assert (await _folder(pg, c_first))[
            "system_key"
        ] is None, (
            "the code scope must still be unadopted here, or this test is vacuous"
        )

        chosen = await _ensure_chat_uploads_folder(str(by_code), str(USER))

        assert chosen == str(c_first), "the code must adopt the OLDEST, as 450 does"
        assert (await _folder(pg, m_first))["system_key"] == CHAT_UPLOADS_SYSTEM_KEY
        assert (await _folder(pg, m_second))["system_key"] is None
        adopted = await _folder(pg, c_first)
        assert adopted["system_key"] == CHAT_UPLOADS_SYSTEM_KEY
        assert adopted["is_system"] is True
        assert adopted["name"] == CHAT_UPLOADS_DISPLAY_NAME
        assert (await _folder(pg, c_second))["system_key"] is None

    async def test_running_the_migration_after_the_code_changes_nothing(
        self, pg, scopes
    ):
        """The reverse order. The code adopted already, so 450 is a no-op —
        and crucially does not try to key the scope's other temp folder."""
        scope = scopes[0]
        first = await _mk_folder(pg, scope, LEGACY_CHAT_UPLOADS_FOLDER_NAME)
        second = await _mk_folder(pg, scope, LEGACY_CHAT_UPLOADS_FOLDER_NAME)

        assert await _ensure_chat_uploads_folder(str(scope), str(USER)) == str(first)
        assert await _run_migration(pg) == 0
        assert (await _folder(pg, second))["system_key"] is None

    async def test_the_code_also_refuses_a_nested_temp(self, pg, scopes):
        """Lockstep, the case that matters most: the two rules must reject the
        SAME row. If only the migration were tightened, a deploy that reached
        production first would claim the user's nested folder and the later
        migration would find it already keyed — permanent, and invisible."""
        by_migration, by_code, _ = scopes
        m_parent = await _mk_folder(pg, by_migration, "My Project")
        m_nested = await _mk_folder(
            pg, by_migration, LEGACY_CHAT_UPLOADS_FOLDER_NAME, parent_id=m_parent
        )
        m_root = await _mk_folder(pg, by_migration, LEGACY_CHAT_UPLOADS_FOLDER_NAME)

        await _run_migration(pg)

        # Built after the migration on purpose — see the sibling test: 450 is
        # global, and a scope it already adopted proves nothing about the code.
        c_parent = await _mk_folder(pg, by_code, "My Project")
        c_nested = await _mk_folder(
            pg, by_code, LEGACY_CHAT_UPLOADS_FOLDER_NAME, parent_id=c_parent
        )
        c_root = await _mk_folder(pg, by_code, LEGACY_CHAT_UPLOADS_FOLDER_NAME)
        assert c_nested < c_root, "the nested folder must be the older one here"
        assert (await _folder(pg, c_root))["system_key"] is None

        chosen = await _ensure_chat_uploads_folder(str(by_code), str(USER))

        assert chosen == str(c_root)
        assert (await _folder(pg, m_root))["system_key"] == CHAT_UPLOADS_SYSTEM_KEY
        for nested in (m_nested, c_nested):
            row = await _folder(pg, nested)
            assert row["system_key"] is None
            assert row["is_system"] is False
            assert row["name"] == LEGACY_CHAT_UPLOADS_FOLDER_NAME

    async def test_the_code_creates_rather_than_claiming_a_nested_temp(
        self, pg, scopes
    ):
        """The self-healing path: no adoptable candidate means a NEW keyed
        folder, which the user can see and understand — not a silent hijack of
        the folder they made."""
        scope = scopes[0]
        parent = await _mk_folder(pg, scope, "My Project")
        nested = await _mk_folder(
            pg, scope, LEGACY_CHAT_UPLOADS_FOLDER_NAME, parent_id=parent
        )

        created = await _ensure_chat_uploads_folder(str(scope), str(USER))

        assert int(created) != nested
        row = await _folder(pg, int(created))
        assert row["system_key"] == CHAT_UPLOADS_SYSTEM_KEY
        assert row["name"] == CHAT_UPLOADS_DISPLAY_NAME
        assert (await _folder(pg, nested))["system_key"] is None
        assert (await _folder(pg, nested))["is_system"] is False

    async def test_a_fresh_scope_gets_exactly_one_keyed_folder(self, pg, scopes):
        scope = scopes[0]

        created = await _ensure_chat_uploads_folder(str(scope), str(USER))

        row = await _folder(pg, int(created))
        assert row["system_key"] == CHAT_UPLOADS_SYSTEM_KEY
        assert row["is_system"] is True
        assert row["name"] == CHAT_UPLOADS_DISPLAY_NAME
        assert (
            await pg.fetchval("SELECT count(*) FROM folders WHERE scope_id = $1", scope)
            == 1
        )

    async def test_calling_it_again_reuses_the_folder(self, pg, scopes):
        """The failure this guards is invisible without a row count: a second
        call that creates a second folder returns a perfectly valid id."""
        scope = scopes[0]

        first = await _ensure_chat_uploads_folder(str(scope), str(USER))
        second = await _ensure_chat_uploads_folder(str(scope), str(USER))
        third = await _ensure_chat_uploads_folder(str(scope), str(USER))

        assert first == second == third
        assert (
            await pg.fetchval("SELECT count(*) FROM folders WHERE scope_id = $1", scope)
            == 1
        )

    async def test_a_trashed_keyed_folder_does_not_satisfy_the_lookup(self, pg, scopes):
        """A folder in the bin must not be handed back as the place to file a
        new upload; the index leaves the slot free precisely so a new one can
        be created."""
        scope = scopes[0]
        binned = await _mk_folder(
            pg,
            scope,
            CHAT_UPLOADS_DISPLAY_NAME,
            system_key=CHAT_UPLOADS_SYSTEM_KEY,
            is_system=True,
            trashed=True,
        )

        created = await _ensure_chat_uploads_folder(str(scope), str(USER))

        assert int(created) != binned
        assert (await _folder(pg, int(created)))["is_trashed"] is False
