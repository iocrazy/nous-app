"""The chat-uploads folder is identified by ``system_key``, never by name.

P6 moved the identity of the per-scope folder that holds chat/issue
attachments from ``name='temp'`` to ``system_key='chat_uploads'``. This file
pins the code half of that move — the half that decides, on every single chat
upload, whether to reuse, adopt or create.

Right now it is the ONLY half that runs. Migration 450, which adopts the
folders in scopes nobody uploads to, is a separate follow-up PR that must not
merge until this backend is live (the old name-matching backend would mint a
second ``temp`` folder beside a renamed one). So every assertion below about
adoption is also the assertion that a scope which never saw the migration still
lands on its existing folder instead of a fresh one.

WHY THE ORDER OF THE THREE BRANCHES IS THE TEST
───────────────────────────────────────────────
Getting it wrong does not raise: it mints a SECOND folder in the scope and
leaves every historical attachment in the first one, where the agent no longer
looks. So each branch is pinned by what it did NOT do — the reuse case asserts
no write was attempted at all, the adopt case asserts no INSERT followed.

The session is stubbed here, so what these prove is the decision sequence and
the statements' shape. That the partial unique index actually rejects the
second keyed folder is a property of Postgres and is pinned in
``tests/db/test_chat_uploads_folder_migration.py``, which travels with
migration 450 in the follow-up PR.

Wire shapes: ``resolve_chat_scope`` hands ``scope_id`` over as a **str**
(a teams.id snowflake), while ``folders.id`` comes back from asyncpg as a
Python **int**. Both directions appear below on purpose — the ids these tests
use are real 15-digit snowflakes, not ``"1"``.
"""

from __future__ import annotations

from typing import Any, List, Sequence

import pytest
from sqlalchemy import Insert, Select, Update
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from app.services.library import chat_upload as m

# Real snowflake shapes — a str scope id (what resolve_chat_scope returns) and
# int folder ids (what the BIGINT column returns through asyncpg).
SCOPE_ID_STR = "324633973020186"
SCOPE_ID_INT = 324633973020186
KEYED_FOLDER_ID = 512901118829330
LEGACY_FOLDER_ID = 411290999812771
NEW_FOLDER_ID = 601277654390021
USER_ID = "b2180063-6860-4f97-9785-ad4eede16064"


# ------------------------------------------------------------------ #
# Session stubs
# ------------------------------------------------------------------ #


class _Scalars:
    def __init__(self, rows: Sequence[Sequence[Any]]):
        self._rows = list(rows)

    def first(self):
        return self._rows[0][0] if self._rows else None

    def all(self):
        return [r[0] for r in self._rows]


class _Result:
    def __init__(self, rows: Sequence[Sequence[Any]]):
        self._rows = list(rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def scalars(self):
        return _Scalars(self._rows)


class _FakeSession:
    """Returns the queued outcome for each ``execute``, in order.

    An outcome is either a list of row tuples or an exception to raise. A
    statement arriving after the queue is empty is an error, not an empty
    result: "the code asked one more question than we planned for" must not
    read as "the answer was no".
    """

    def __init__(self, outcomes: Sequence[Any], label: str):
        self._outcomes = list(outcomes)
        self.label = label
        self.statements: List[Any] = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        if not self._outcomes:
            raise AssertionError(
                f"unplanned {kind_of(stmt)} on the {self.label} session: {stmt}"
            )
        out = self._outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return _Result(out)

    @property
    def kinds(self) -> List[str]:
        return [kind_of(s) for s in self.statements]

    def drained(self) -> bool:
        return not self._outcomes


def kind_of(stmt) -> str:
    if isinstance(stmt, Select):
        return "SELECT"
    if isinstance(stmt, Update):
        return "UPDATE"
    if isinstance(stmt, Insert):
        return "INSERT"
    return type(stmt).__name__


class _Scope:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


def _install(monkeypatch, *, reads: Sequence[Any], writes: Sequence[Any]):
    """Point ``read_scope`` / ``write_scope`` at stub sessions.

    Both are deferred imports inside ``chat_upload``, so patching the module
    attributes on ``app.db.session`` is what the callee actually resolves.
    Every ``read_scope()`` opens a fresh session over the SAME queue, which is
    what makes "the second lookup saw the winner" expressible.
    """
    read_session = _FakeSession(reads, "read")
    write_session = _FakeSession(writes, "write")
    monkeypatch.setattr("app.db.session.read_scope", lambda: _Scope(read_session))
    monkeypatch.setattr("app.db.session.write_scope", lambda: _Scope(write_session))
    return read_session, write_session


def _sql(stmt) -> str:
    return str(
        stmt.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


def _params(stmt) -> dict:
    return stmt.compile(dialect=postgresql.dialect()).params


# ------------------------------------------------------------------ #
# The identity criteria — one definition, shared with the backfill
# ------------------------------------------------------------------ #


class TestIdentityCriteria:
    def test_keyed_arm_matches_the_system_key_and_nothing_else(self):
        sql = _sql(m.keyed_chat_uploads_criteria())
        assert "system_key = 'chat_uploads'" in sql
        assert "name" not in sql

    def test_legacy_arm_requires_an_unkeyed_folder_named_temp(self):
        sql = _sql(m.legacy_chat_uploads_criteria())
        assert "system_key IS NULL" in sql
        assert "name = 'temp'" in sql

    def test_union_carries_both_arms(self):
        """A folder is a chat-uploads folder if EITHER arm matches.

        Losing the legacy arm is the failure that produces an empty plan and
        calls it reconciled; losing the keyed arm stops finding the folder the
        moment the migration lands.
        """
        sql = _sql(m.chat_uploads_folder_criteria())
        assert "system_key = 'chat_uploads'" in sql
        assert "system_key IS NULL" in sql
        assert "name = 'temp'" in sql
        assert " OR " in sql

    def test_the_adoptable_arm_is_root_only(self):
        """What we may CLAIM is narrower than what we may READ.

        Adoption flips ``is_system``, which the API turns into a 409 on
        rename / move / trash / delete. A user's nested "temp" scratch folder
        must never be claimed — the pre-450 code that created the real one
        passed neither ``parent_id`` nor ``library_id``, so root-level is a
        fact about the folder, not a precaution.
        """
        sql = _sql(m.adoptable_chat_uploads_criteria())
        assert "system_key IS NULL" in sql
        assert "name = 'temp'" in sql
        assert "parent_id IS NULL" in sql
        assert "library_id IS NULL" in sql

    def test_the_read_union_stays_wide(self):
        """The root-only guards belong to adoption ONLY.

        Putting them on the read side would drop a nested legacy folder's
        uploads out of the backfill's plan and report that as reconciled.
        """
        sql = _sql(m.chat_uploads_folder_criteria())
        assert "parent_id" not in sql
        assert "library_id" not in sql

    def test_display_name_is_title_case_and_key_is_stable(self):
        assert m.CHAT_UPLOADS_SYSTEM_KEY == "chat_uploads"
        assert m.CHAT_UPLOADS_DISPLAY_NAME == "Chat Uploads"
        assert m.LEGACY_CHAT_UPLOADS_FOLDER_NAME == "temp"


# ------------------------------------------------------------------ #
# Branch 1 — find by key
# ------------------------------------------------------------------ #


class TestLookupByKey:
    @pytest.mark.asyncio
    async def test_existing_keyed_folder_is_returned_without_any_write(
        self, monkeypatch
    ):
        reads, writes = _install(monkeypatch, reads=[[(KEYED_FOLDER_ID,)]], writes=[])

        folder_id = await m._ensure_chat_uploads_folder(SCOPE_ID_STR, USER_ID)

        assert folder_id == str(KEYED_FOLDER_ID)
        # The whole point: no second row can be minted if no write happens.
        assert writes.statements == []
        assert reads.kinds == ["SELECT"]

    @pytest.mark.asyncio
    async def test_the_lookup_keys_off_system_key_and_the_scope(self, monkeypatch):
        reads, _ = _install(monkeypatch, reads=[[(KEYED_FOLDER_ID,)]], writes=[])

        await m._ensure_chat_uploads_folder(SCOPE_ID_STR, USER_ID)

        sql = _sql(reads.statements[0])
        assert "system_key = 'chat_uploads'" in sql
        assert f"scope_id = {SCOPE_ID_INT}" in sql
        assert "is_trashed IS false" in sql
        # A name match here would resurrect the bug this change removes.
        assert "name = 'temp'" not in sql
        assert "name = 'Chat Uploads'" not in sql

    @pytest.mark.asyncio
    async def test_str_scope_id_is_bound_as_a_bigint(self, monkeypatch):
        """asyncpg rejects a str bind on int8, so the coercion is load-bearing."""
        reads, _ = _install(monkeypatch, reads=[[(KEYED_FOLDER_ID,)]], writes=[])

        await m._ensure_chat_uploads_folder(SCOPE_ID_STR, USER_ID)

        assert SCOPE_ID_INT in _params(reads.statements[0]).values()

    @pytest.mark.asyncio
    async def test_find_helper_returns_none_when_the_scope_has_no_keyed_folder(
        self, monkeypatch
    ):
        _install(monkeypatch, reads=[[]], writes=[])
        assert await m._find_chat_uploads_folder(SCOPE_ID_INT) is None


# ------------------------------------------------------------------ #
# Branch 2 — adopt the legacy temp folder
# ------------------------------------------------------------------ #


class TestAdoption:
    @pytest.mark.asyncio
    async def test_a_legacy_temp_folder_is_adopted_rather_than_duplicated(
        self, monkeypatch
    ):
        reads, writes = _install(
            monkeypatch,
            reads=[[]],  # no keyed folder yet
            writes=[
                [(LEGACY_FOLDER_ID,)],  # the legacy candidate
                [(LEGACY_FOLDER_ID,)],  # UPDATE ... RETURNING id
            ],
        )

        folder_id = await m._ensure_chat_uploads_folder(SCOPE_ID_STR, USER_ID)

        assert folder_id == str(LEGACY_FOLDER_ID)
        # No INSERT: the historical attachments stay reachable in the folder
        # they were always in.
        assert writes.kinds == ["SELECT", "UPDATE"]
        assert writes.drained()

    @pytest.mark.asyncio
    async def test_adoption_sets_the_key_the_switch_and_the_display_name(
        self, monkeypatch
    ):
        _, writes = _install(
            monkeypatch,
            reads=[[]],
            writes=[[(LEGACY_FOLDER_ID,)], [(LEGACY_FOLDER_ID,)]],
        )

        await m._ensure_chat_uploads_folder(SCOPE_ID_STR, USER_ID)

        update = writes.statements[1]
        sql = _sql(update)
        assert "system_key='chat_uploads'" in sql.replace(" = ", "=")
        assert "is_system=true" in sql.replace(" = ", "=")
        assert "name='Chat Uploads'" in sql.replace(" = ", "=")
        # updated_at belongs to update_folders_updated_at (BEFORE UPDATE);
        # writing it here would be overwritten anyway.
        assert "updated_at" not in sql

    @pytest.mark.asyncio
    async def test_adoption_only_ever_claims_an_unkeyed_folder(self, monkeypatch):
        """The WHERE guard is the race guard AND the safety guard.

        Without ``system_key IS NULL`` in the UPDATE, a concurrent adopter's
        row (or a folder keyed for something else) could be overwritten.
        """
        _, writes = _install(
            monkeypatch,
            reads=[[]],
            writes=[[(LEGACY_FOLDER_ID,)], [(LEGACY_FOLDER_ID,)]],
        )

        await m._ensure_chat_uploads_folder(SCOPE_ID_STR, USER_ID)

        assert "system_key IS NULL" in _sql(writes.statements[1])

    @pytest.mark.asyncio
    async def test_the_oldest_temp_folder_wins(self, monkeypatch):
        """Same rule as migration 450's ``DISTINCT ON (scope_id) ... ORDER BY id``.

        Folder ids are snowflakes, so the smallest id is the oldest folder —
        the one chat uploads have actually been landing in. If the two rules
        disagreed, each would adopt a different folder and the second would hit
        ux_folders_scope_system_key.
        """
        _, writes = _install(
            monkeypatch,
            reads=[[]],
            writes=[[(LEGACY_FOLDER_ID,)], [(LEGACY_FOLDER_ID,)]],
        )

        await m._ensure_chat_uploads_folder(SCOPE_ID_STR, USER_ID)

        candidate_sql = _sql(writes.statements[0])
        assert "ORDER BY public.folders.id ASC" in candidate_sql
        assert "LIMIT 1" in candidate_sql
        assert "system_key IS NULL" in candidate_sql
        assert "name = 'temp'" in candidate_sql
        assert "is_trashed IS false" in candidate_sql

    @pytest.mark.asyncio
    async def test_the_candidate_query_can_only_claim_a_root_folder(self, monkeypatch):
        """All five conditions of migration 450's candidate predicate, on the
        one query that claims a row. Any of them missing here and the two
        rules have drifted apart."""
        _, writes = _install(
            monkeypatch,
            reads=[[]],
            writes=[[(LEGACY_FOLDER_ID,)], [(LEGACY_FOLDER_ID,)]],
        )

        await m._ensure_chat_uploads_folder(SCOPE_ID_STR, USER_ID)

        candidate_sql = _sql(writes.statements[0])
        assert f"scope_id = {SCOPE_ID_INT}" in candidate_sql
        assert "is_trashed IS false" in candidate_sql
        assert "system_key IS NULL" in candidate_sql
        assert "parent_id IS NULL" in candidate_sql
        assert "library_id IS NULL" in candidate_sql


# ------------------------------------------------------------------ #
# Branch 3 — create, carrying the identity
# ------------------------------------------------------------------ #


class TestCreation:
    @pytest.mark.asyncio
    async def test_a_fresh_scope_gets_a_folder_carrying_the_identity(self, monkeypatch):
        reads, writes = _install(
            monkeypatch,
            reads=[[]],
            writes=[[], [(NEW_FOLDER_ID,)]],  # no legacy candidate, then INSERT
        )

        folder_id = await m._ensure_chat_uploads_folder(SCOPE_ID_STR, USER_ID)

        assert folder_id == str(NEW_FOLDER_ID)
        assert writes.kinds == ["SELECT", "INSERT"]
        params = _params(writes.statements[1])
        assert params["system_key"] == m.CHAT_UPLOADS_SYSTEM_KEY
        assert params["is_system"] is True
        assert params["name"] == m.CHAT_UPLOADS_DISPLAY_NAME
        assert params["scope_id"] == SCOPE_ID_INT
        # folders.created_by is UUID NOT NULL with no default (mig 044).
        assert params["created_by"] == USER_ID

    @pytest.mark.asyncio
    async def test_a_created_folder_is_never_named_temp(self, monkeypatch):
        _, writes = _install(monkeypatch, reads=[[]], writes=[[], [(NEW_FOLDER_ID,)]])

        await m._ensure_chat_uploads_folder(SCOPE_ID_STR, USER_ID)

        assert _params(writes.statements[1])["name"] != "temp"


# ------------------------------------------------------------------ #
# Races — the database, not our Python, decides who wins
# ------------------------------------------------------------------ #


class TestIntegrityDiagnostics:
    """The race log has to name the constraint, or it asserts the benign case.

    ``IntegrityError`` alone reads the same whether we lost a harmless race on
    ``ux_folders_scope_system_key`` or violated something nobody anticipated —
    so a log that prints only the class name quietly claims the first reading.

    The three shapes below are not hypothetical: measured against this stack
    (SQLAlchemy 2 + asyncpg) the name is on ``.orig.__cause__``, while
    ``.orig`` itself has neither ``constraint_name`` nor ``diag``. The other
    two are covered because the driver is not part of this module's contract.
    """

    def _err(self, orig):
        e = IntegrityError("INSERT", {}, Exception("boom"))
        e.orig = orig
        return e

    def test_reads_the_name_off_orig(self):
        orig = type("Orig", (), {"constraint_name": "ux_folders_scope_system_key"})()
        assert (
            m._integrity_constraint_name(self._err(orig))
            == "ux_folders_scope_system_key"
        )

    def test_reads_the_name_off_orig_diag(self):
        diag = type("Diag", (), {"constraint_name": "ux_folders_scope_system_key"})()
        orig = type("Orig", (), {"diag": diag})()
        assert (
            m._integrity_constraint_name(self._err(orig))
            == "ux_folders_scope_system_key"
        )

    def test_reads_the_name_off_orig_cause_which_is_the_real_shape_here(self):
        """asyncpg's ``UniqueViolationError`` sits under the dialect wrapper."""

        class _Wrapper(Exception):
            pass

        # __cause__ must be a real exception — asyncpg's UniqueViolationError
        # is one, so the stub has to be too or it stops modelling the shape.
        class _UniqueViolationError(Exception):
            constraint_name = "ux_folders_scope_system_key"

        orig = _Wrapper("dup key")
        orig.__cause__ = _UniqueViolationError("dup")
        assert (
            m._integrity_constraint_name(self._err(orig))
            == "ux_folders_scope_system_key"
        )

    def test_returns_none_when_no_shape_carries_it(self):
        """None, not a guess. The caller still logs ``str(orig)``."""
        assert m._integrity_constraint_name(self._err(object())) is None
        assert m._integrity_constraint_name(self._err(None)) is None

    @pytest.mark.asyncio
    async def test_the_race_log_carries_the_constraint_and_the_driver_message(
        self, monkeypatch, caplog
    ):
        import logging

        from loguru import logger as loguru_logger

        class _Orig(Exception):
            pass

        class _UniqueViolationError(Exception):
            constraint_name = "ux_folders_scope_system_key"

        orig = _Orig('duplicate key value violates unique constraint "ux_x"')
        orig.__cause__ = _UniqueViolationError("dup")
        integrity = IntegrityError("INSERT", {}, Exception("dup"))
        integrity.orig = orig

        _install(
            monkeypatch,
            reads=[[], [(KEYED_FOLDER_ID,)]],
            writes=[[], integrity],
        )

        sink_id = loguru_logger.add(
            lambda msg: logging.getLogger("chat_upload_test").info(msg), level="INFO"
        )
        try:
            with caplog.at_level(logging.INFO, logger="chat_upload_test"):
                await m._ensure_chat_uploads_folder(SCOPE_ID_STR, USER_ID)
        finally:
            loguru_logger.remove(sink_id)

        line = "\n".join(caplog.messages)
        assert "constraint=ux_folders_scope_system_key" in line
        assert "duplicate key value violates unique constraint" in line


class TestConcurrency:
    @pytest.mark.asyncio
    async def test_a_rejected_insert_re_reads_the_winner(self, monkeypatch):
        """ux_folders_scope_system_key rejecting our INSERT means somebody
        else already has the folder — reuse it, never retry into a duplicate."""
        integrity = IntegrityError("INSERT", {}, Exception("dup key"))
        reads, writes = _install(
            monkeypatch,
            reads=[[], [(KEYED_FOLDER_ID,)]],  # miss, then the winner
            writes=[[], integrity],
        )

        folder_id = await m._ensure_chat_uploads_folder(SCOPE_ID_STR, USER_ID)

        assert folder_id == str(KEYED_FOLDER_ID)
        assert reads.kinds == ["SELECT", "SELECT"]

    @pytest.mark.asyncio
    async def test_an_adoption_that_lost_the_race_does_not_return_nothing(
        self, monkeypatch
    ):
        """UPDATE matching zero rows (another caller keyed it first) must fall
        through, not hand back a folder id that was never claimed."""
        integrity = IntegrityError("INSERT", {}, Exception("dup key"))
        reads, writes = _install(
            monkeypatch,
            reads=[[], [(KEYED_FOLDER_ID,)]],
            writes=[[(LEGACY_FOLDER_ID,)], [], integrity],
        )

        folder_id = await m._ensure_chat_uploads_folder(SCOPE_ID_STR, USER_ID)

        assert folder_id == str(KEYED_FOLDER_ID)
        assert writes.kinds == ["SELECT", "UPDATE", "INSERT"]

    @pytest.mark.asyncio
    async def test_nothing_adopted_created_or_found_raises(self, monkeypatch):
        """A silent failure here would upload the file into folder ``None``.

        RLS denial and a lost race look the same from inside the transaction,
        so the re-read is what tells them apart; when even that is empty the
        only honest answer is an exception.
        """
        reads, writes = _install(
            monkeypatch,
            reads=[[], []],
            writes=[[], []],  # no candidate, INSERT returned no row
        )

        with pytest.raises(RuntimeError, match="could not ensure the Chat Uploads"):
            await m._ensure_chat_uploads_folder(SCOPE_ID_STR, USER_ID)

        assert reads.kinds == ["SELECT", "SELECT"]


# ------------------------------------------------------------------ #
# The upload path uses it
# ------------------------------------------------------------------ #


class TestUploadPathWiring:
    @pytest.mark.asyncio
    async def test_save_chat_temp_upload_files_into_the_ensured_folder(
        self, monkeypatch
    ):
        """The ensure helper is called with the scope id alone (no scope_type:
        scope_id is a teams.id snowflake and unique across both kinds)."""
        from unittest.mock import AsyncMock, MagicMock

        from app.services.library import resources_service as rs

        monkeypatch.setattr(m, "_get_session_team_id", AsyncMock(return_value=None))
        monkeypatch.setattr(
            rs, "_resolve_personal_team_id", AsyncMock(return_value=SCOPE_ID_STR)
        )
        ensure = AsyncMock(return_value=str(KEYED_FOLDER_ID))
        monkeypatch.setattr(m, "_ensure_chat_uploads_folder", ensure)
        monkeypatch.setattr(m, "_register_in_generated_inbox", AsyncMock())

        svc = MagicMock()
        svc.upload_resource = AsyncMock(
            return_value={"id": "res-1", "file_path": "teams/1/uploads/res-1/v1/x.png"}
        )
        monkeypatch.setattr(m, "_resources_service", lambda: svc)

        await m.save_chat_temp_upload(
            user_id=USER_ID,
            session_id=None,
            file_bytes=b"\x89PNG\r\n\x1a\n",
            filename="x.png",
            mime="image/png",
        )

        assert ensure.call_args.args == (SCOPE_ID_STR, USER_ID)
        assert svc.upload_resource.call_args.kwargs["folder_id"] == str(KEYED_FOLDER_ID)


def test_the_old_name_constant_is_gone():
    """``TEMP_FOLDER_NAME`` was the identity. Re-adding it as a lookup key is
    the regression; the legacy name survives only as an ADOPTION target."""
    assert not hasattr(m, "TEMP_FOLDER_NAME")
    assert not hasattr(m, "_ensure_temp_folder")
