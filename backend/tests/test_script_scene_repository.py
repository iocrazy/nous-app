"""Unit tests for ``ScriptSceneRepository`` (capture-emitted-SQL style).

These run in the UNIT suite (no DSN): the session + ``read_scope`` /
``write_scope`` scopes are faked, and the emitted SQLAlchemy statements are
captured + compiled so we can prove the version-guard invariants without a live
database. Mirrors ``tests/test_projects_member_update_delete.py``.

The invariants under test are the load-bearing ones from the plan Task 4:

  - ``apply_element_ops`` UPDATE carries the ``content_version`` DOUBLE-GUARD in
    its WHERE (optimistic concurrency: never write over a version we did not read).
  - the op ledger INSERT into ``script_ops`` fires in the SAME session /
    transaction as the scene UPDATE (atomic op + ledger).
  - a stale ``expected_version`` raises ``VersionConflict`` carrying the current
    version and the current elements (so the router can 409 with a re-sync body).
  - a lost double-guard race (rowcount==0) re-reads and raises ``VersionConflict``.
  - ``move_scene`` renumbers the whole chapter group when the neighbour gap is
    exhausted (adjacent sort_orders).
  - ``create`` auto-assigns ``sort_order = MAX + 1000`` within the group.
  - every bigint id is ``_bigint``-coerced (str in → int bound param).
"""

from __future__ import annotations

import datetime as _dt
import uuid as _uuid_mod
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import insert as _sa_insert
from sqlalchemy import select as _sa_select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker as _async_sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine as _create_async_engine

import app.repositories.script_scene_repository as scene_mod
from app.models.scripts import ScriptProjects, ScriptScenes
from app.repositories.script_scene_repository import (
    ScriptSceneRepository,
    VersionConflict,
    _project_scene_rows_stmt,
)
from app.services.script.scene_ops import OpError

_SCRIPT_ID = 700000000000000001
_SCENE_ID = 700000000000000002
_CHAPTER_ID = 700000000000000003


# ── fakes ────────────────────────────────────────────────────────────────


class _FakeResult:
    """A stand-in for a SQLAlchemy Result.

    ``scalar_first`` backs ``.scalars().first()`` (full-row selects / RETURNING),
    ``first_row`` backs ``.first()`` (column-tuple selects), ``all_rows`` backs
    ``.all()`` (sibling lists), and ``rowcount`` backs the UPDATE guard check."""

    def __init__(self, *, scalar_first=None, first_row=None, all_rows=None, rowcount=1):
        self._scalar_first = scalar_first
        self._first_row = first_row
        self._all_rows = all_rows or []
        self.rowcount = rowcount

    def scalars(self):
        return self

    def first(self):
        # After scalars() → scalar_first; bare .first() → first_row.
        return self._scalar_first if self._first_row is None else self._first_row

    def all(self):
        return self._all_rows


class _CaptureSession:
    """Records every executed statement and returns queued results in order."""

    def __init__(self, results):
        self.statements: list = []
        self._results = list(results)
        self.scalars_calls: list = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return self._results.pop(0)

    async def scalar(self, stmt):
        self.statements.append(stmt)
        res = self._results.pop(0)
        return res._scalar_first


class _ScopeCtx:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


def _scene_obj(
    content_version=1, content_json=None, chapter_id=_CHAPTER_ID, sort_order=1000
):
    return ScriptScenes(
        id=_SCENE_ID,
        script_id=_SCRIPT_ID,
        chapter_id=chapter_id,
        content_json=content_json if content_json is not None else [],
        content="",
        content_version=content_version,
        sort_order=sort_order,
        created_at=_dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc),
        updated_at=_dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc),
    )


def _rendered(stmt) -> tuple[str, dict]:
    compiled = stmt.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


_INSERT_OP = {
    "op": "insert",
    "element_id": "el_1",
    "payload": {"type": "action", "text": "A man walks in."},
}


# ── apply_element_ops: happy path ────────────────────────────────────────


@pytest.mark.asyncio
async def test_apply_element_ops_update_has_version_double_guard():
    """The UPDATE filters BOTH id AND content_version (optimistic double-guard)
    and bumps content_version. This is the concurrency-safety gate."""
    session = _CaptureSession(
        [
            _FakeResult(first_row=SimpleNamespace(content_json=[], content_version=3)),
            _FakeResult(rowcount=1),  # UPDATE
            _FakeResult(rowcount=1),  # INSERT ledger
        ]
    )
    with patch.object(scene_mod, "write_scope", lambda: _ScopeCtx(session)):
        out = await ScriptSceneRepository().apply_element_ops(
            str(_SCENE_ID), [_INSERT_OP], expected_version=3, actor="user-uuid"
        )

    # SELECT, UPDATE, INSERT — three statements, one transaction.
    assert len(session.statements) == 3
    upd_sql, upd_params = _rendered(session.statements[1])
    assert upd_sql.strip().upper().startswith("UPDATE PUBLIC.SCRIPT_SCENES")
    # Double-guard: id AND content_version in the WHERE.
    assert "script_scenes.id" in upd_sql
    assert "WHERE" in upd_sql.upper()
    assert "script_scenes.content_version = " in upd_sql
    assert 3 in upd_params.values()  # expected_version bound in WHERE
    # Returned contract.
    assert out["content_version"] == 4
    assert out["elements"] == [
        {"id": "el_1", "type": "action", "text": "A man walks in."}
    ]


@pytest.mark.asyncio
async def test_apply_element_ops_writes_op_ledger_same_transaction():
    """The op ledger INSERT into script_ops fires in the same session as the
    scene UPDATE, with op_seq == the new content_version and the actor bound."""
    session = _CaptureSession(
        [
            _FakeResult(first_row=SimpleNamespace(content_json=[], content_version=0)),
            _FakeResult(rowcount=1),
            _FakeResult(rowcount=1),
        ]
    )
    with patch.object(scene_mod, "write_scope", lambda: _ScopeCtx(session)):
        await ScriptSceneRepository().apply_element_ops(
            str(_SCENE_ID), [_INSERT_OP], expected_version=0, actor="copilot"
        )

    ins_sql, ins_params = _rendered(session.statements[2])
    assert ins_sql.strip().upper().startswith("INSERT INTO PUBLIC.SCRIPT_OPS")
    assert 1 in ins_params.values()  # op_seq = new_version = 0 + 1
    assert "copilot" in ins_params.values()  # actor bound


@pytest.mark.asyncio
async def test_apply_element_ops_bigint_coerces_scene_id():
    """A str scene_id is coerced to an int bound param on the SELECT (the
    _bigint iron rule — a snowflake compared as str would silently miss)."""
    session = _CaptureSession(
        [
            _FakeResult(first_row=SimpleNamespace(content_json=[], content_version=0)),
            _FakeResult(rowcount=1),
            _FakeResult(rowcount=1),
        ]
    )
    with patch.object(scene_mod, "write_scope", lambda: _ScopeCtx(session)):
        await ScriptSceneRepository().apply_element_ops(
            str(_SCENE_ID), [_INSERT_OP], expected_version=0, actor="u"
        )
    _, sel_params = _rendered(session.statements[0])
    assert _SCENE_ID in sel_params.values()
    assert all(
        not isinstance(v, str) or v != str(_SCENE_ID) for v in sel_params.values()
    )


# ── apply_element_ops: conflict paths ────────────────────────────────────


@pytest.mark.asyncio
async def test_apply_element_ops_stale_version_raises_conflict():
    """expected_version != current → VersionConflict BEFORE any write, carrying
    the current version + current elements for the client to re-sync."""
    current_elements = [{"id": "el_0", "type": "action", "text": "Old."}]
    session = _CaptureSession(
        [
            _FakeResult(
                first_row=SimpleNamespace(
                    content_json=current_elements, content_version=7
                )
            ),
        ]
    )
    with patch.object(scene_mod, "write_scope", lambda: _ScopeCtx(session)):
        with pytest.raises(VersionConflict) as ei:
            await ScriptSceneRepository().apply_element_ops(
                str(_SCENE_ID), [_INSERT_OP], expected_version=2, actor="u"
            )
    assert ei.value.current_version == 7
    assert ei.value.elements == current_elements
    # No UPDATE/INSERT was attempted.
    assert len(session.statements) == 1


@pytest.mark.asyncio
async def test_apply_element_ops_lost_race_rowcount_zero_raises_conflict():
    """A concurrent writer wins between our SELECT and UPDATE: the double-guard
    UPDATE matches 0 rows → re-read → VersionConflict with the fresh version."""
    fresh = [{"id": "el_x", "type": "action", "text": "Winner."}]
    session = _CaptureSession(
        [
            _FakeResult(first_row=SimpleNamespace(content_json=[], content_version=3)),
            _FakeResult(rowcount=0),  # UPDATE matched nothing
            _FakeResult(
                first_row=SimpleNamespace(content_json=fresh, content_version=4)
            ),
        ]
    )
    with patch.object(scene_mod, "write_scope", lambda: _ScopeCtx(session)):
        with pytest.raises(VersionConflict) as ei:
            await ScriptSceneRepository().apply_element_ops(
                str(_SCENE_ID), [_INSERT_OP], expected_version=3, actor="u"
            )
    assert ei.value.current_version == 4
    assert ei.value.elements == fresh


@pytest.mark.asyncio
async def test_apply_element_ops_oi_error_propagates():
    """An OpError from scene_ops is NOT swallowed by the repo (router maps 422).
    A move against a missing element raises unknown_element."""
    session = _CaptureSession(
        [
            _FakeResult(first_row=SimpleNamespace(content_json=[], content_version=0)),
        ]
    )
    bad_op = {"op": "move", "element_id": "nope", "after_id": "x"}
    with patch.object(scene_mod, "write_scope", lambda: _ScopeCtx(session)):
        with pytest.raises(OpError):
            await ScriptSceneRepository().apply_element_ops(
                str(_SCENE_ID), [bad_op], expected_version=0, actor="u"
            )


# ── create: auto sort_order ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_auto_sort_order_max_plus_1000():
    """create() sets sort_order = group MAX + 1000 (sparse insertion base)."""
    created = _scene_obj(sort_order=4000)
    session = _CaptureSession(
        [
            _FakeResult(scalar_first=3000),  # func.max(sort_order) → 3000
            _FakeResult(scalar_first=created),  # INSERT ... RETURNING
        ]
    )
    with patch.object(scene_mod, "write_scope", lambda: _ScopeCtx(session)):
        out = await ScriptSceneRepository().create(
            {"script_id": str(_SCRIPT_ID), "chapter_id": str(_CHAPTER_ID)}
        )
    ins_sql, ins_params = _rendered(session.statements[1])
    assert ins_sql.strip().upper().startswith("INSERT INTO PUBLIC.SCRIPT_SCENES")
    assert 4000 in ins_params.values()  # 3000 + 1000
    # bigint coercion on script_id / chapter_id.
    assert _SCRIPT_ID in ins_params.values()
    assert _CHAPTER_ID in ins_params.values()
    assert out["sort_order"] == 4000


@pytest.mark.asyncio
async def test_create_empty_group_starts_at_1000():
    """An empty (script, chapter) group starts at sort_order 1000."""
    created = _scene_obj(sort_order=1000)
    session = _CaptureSession(
        [
            _FakeResult(scalar_first=None),  # no rows yet
            _FakeResult(scalar_first=created),
        ]
    )
    with patch.object(scene_mod, "write_scope", lambda: _ScopeCtx(session)):
        await ScriptSceneRepository().create({"script_id": str(_SCRIPT_ID)})
    _, ins_params = _rendered(session.statements[1])
    assert 1000 in ins_params.values()


# ── move_scene: renumber on gap exhaustion ───────────────────────────────


@pytest.mark.asyncio
async def test_move_scene_renumbers_when_gap_exhausted():
    """Adjacent neighbour sort_orders (gap < 2) force a full chapter-group
    renumber: every scene in the group is rewritten to a fresh 1000-step ladder,
    so the emitted UPDATE count exceeds a single-row move."""
    scene = _scene_obj(sort_order=5000)
    sib_a = (700000000000000010, 1000)
    sib_b = (700000000000000011, 1001)  # adjacent to sib_a → gap 1
    session = _CaptureSession(
        [
            _FakeResult(scalar_first=scene),  # load moving scene
            _FakeResult(all_rows=[sib_a, sib_b]),  # siblings ordered
            _FakeResult(rowcount=1),  # renumber UPDATE 1
            _FakeResult(rowcount=1),  # renumber UPDATE 2
            _FakeResult(rowcount=1),  # renumber UPDATE 3 (moved scene)
            _FakeResult(scalar_first=_scene_obj(sort_order=2000)),  # re-read
        ]
    )
    with patch.object(scene_mod, "write_scope", lambda: _ScopeCtx(session)):
        await ScriptSceneRepository().move_scene(
            str(_SCENE_ID), before_scene_id=str(sib_b[0])
        )
    # SELECT scene, SELECT siblings, then 3 renumber UPDATEs, then re-read = 6.
    update_stmts = [
        s
        for s in session.statements
        if str(s.compile(dialect=postgresql.dialect()))
        .strip()
        .upper()
        .startswith("UPDATE")
    ]
    assert len(update_stmts) == 3  # whole group renumbered, not a single move


@pytest.mark.asyncio
async def test_move_scene_sparse_insert_single_update():
    """A wide gap between neighbours → a single UPDATE placing the scene at the
    midpoint sort_order (no renumber)."""
    scene = _scene_obj(sort_order=9000)
    sib_a = (700000000000000010, 1000)
    sib_b = (700000000000000011, 3000)  # gap 2000 → midpoint 2000
    session = _CaptureSession(
        [
            _FakeResult(scalar_first=scene),
            _FakeResult(all_rows=[sib_a, sib_b]),
            _FakeResult(rowcount=1),  # single placement UPDATE
            _FakeResult(scalar_first=_scene_obj(sort_order=2000)),
        ]
    )
    with patch.object(scene_mod, "write_scope", lambda: _ScopeCtx(session)):
        await ScriptSceneRepository().move_scene(
            str(_SCENE_ID), before_scene_id=str(sib_b[0])
        )
    update_stmts = [
        s
        for s in session.statements
        if str(s.compile(dialect=postgresql.dialect()))
        .strip()
        .upper()
        .startswith("UPDATE")
    ]
    assert len(update_stmts) == 1
    _, params = _rendered(update_stmts[0])
    assert 2000 in params.values()  # midpoint of 1000..3000


# ── lock_numbering: freeze (plan A3 / agent-layer spec §4.2) ─────────────


@pytest.mark.asyncio
async def test_lock_numbering_derives_from_canonical_order_and_stamps_project():
    """Two scenes, unlocked -> scene_number 1 / 2 written in canonical order,
    then script_projects.numbering_locked_at stamped, all in one write_scope."""
    scene_a = _scene_obj(chapter_id=None, sort_order=1000)
    scene_a.id = 700000000000000010
    scene_b = _scene_obj(chapter_id=None, sort_order=2000)
    scene_b.id = 700000000000000011
    session = _CaptureSession(
        [
            _FakeResult(first_row=SimpleNamespace(numbering_locked_at=None)),
            _FakeResult(all_rows=[scene_a, scene_b]),  # .scalars().all()
            _FakeResult(rowcount=1),  # UPDATE scene_a.scene_number
            _FakeResult(rowcount=1),  # UPDATE scene_b.scene_number
            _FakeResult(rowcount=1),  # UPDATE script_projects
        ]
    )

    with patch.object(scene_mod, "write_scope", lambda: _ScopeCtx(session)):
        out = await ScriptSceneRepository().lock_numbering(str(_SCRIPT_ID))

    assert out["already_locked"] is False
    assert [s["scene_number"] for s in out["scenes"]] == ["1", "2"]

    update_stmts = [
        s
        for s in session.statements
        if str(s.compile(dialect=postgresql.dialect()))
        .strip()
        .upper()
        .startswith("UPDATE")
    ]
    assert len(update_stmts) == 3  # 2 scenes + 1 script_projects stamp
    scene_upd_sql_0, scene_upd_params_0 = _rendered(update_stmts[0])
    assert scene_upd_sql_0.strip().upper().startswith("UPDATE PUBLIC.SCRIPT_SCENES")
    assert "1" in scene_upd_params_0.values()
    scene_upd_sql_1, scene_upd_params_1 = _rendered(update_stmts[1])
    assert "2" in scene_upd_params_1.values()
    project_upd_sql, _ = _rendered(update_stmts[2])
    assert project_upd_sql.strip().upper().startswith("UPDATE PUBLIC.SCRIPT_PROJECTS")


@pytest.mark.asyncio
async def test_lock_numbering_is_idempotent_when_already_locked():
    """Locking an already-locked script is a no-op: no scene UPDATE fires,
    the existing (already-frozen) scenes are returned via list_by_script."""
    already_locked_at = _dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc)
    session = _CaptureSession(
        [_FakeResult(first_row=SimpleNamespace(numbering_locked_at=already_locked_at))]
    )
    canned = [{"id": "1", "scene_number": "1", "scene_no_in_episode": "1"}]
    with patch.object(scene_mod, "write_scope", lambda: _ScopeCtx(session)):
        with patch.object(
            ScriptSceneRepository, "list_by_script", AsyncMock(return_value=canned)
        ):
            out = await ScriptSceneRepository().lock_numbering(str(_SCRIPT_ID))

    assert out == {"already_locked": True, "scenes": canned}
    # Only the ONE lock-status check ran — no scene_number UPDATE was ever issued.
    assert len(session.statements) == 1


@pytest.mark.asyncio
async def test_lock_numbering_raises_when_script_not_found():
    session = _CaptureSession([_FakeResult(first_row=None)])
    with patch.object(scene_mod, "write_scope", lambda: _ScopeCtx(session)):
        with pytest.raises(ValueError):
            await ScriptSceneRepository().lock_numbering(str(_SCRIPT_ID))


# ── delete: hard delete pre-lock, OMIT in place post-lock ────────────────


@pytest.mark.asyncio
async def test_delete_hard_deletes_when_script_unlocked():
    session = _CaptureSession(
        [
            _FakeResult(first_row=SimpleNamespace(script_id=_SCRIPT_ID)),
            _FakeResult(scalar_first=None),  # numbering_locked_at IS NULL
            _FakeResult(rowcount=1),  # DELETE
        ]
    )
    with patch.object(scene_mod, "write_scope", lambda: _ScopeCtx(session)):
        out = await ScriptSceneRepository().delete(str(_SCENE_ID))

    assert out == {"deleted": True, "omitted": False, "scene": None}
    del_stmts = [
        s
        for s in session.statements
        if str(s.compile(dialect=postgresql.dialect()))
        .strip()
        .upper()
        .startswith("DELETE")
    ]
    assert len(del_stmts) == 1


@pytest.mark.asyncio
async def test_delete_omits_in_place_when_script_locked():
    """The 'delete after lock' invariant: the row is kept, scene_number is
    untouched, only omitted_at is stamped — no DELETE statement fires."""
    omitted_row = _scene_obj()
    omitted_row.scene_number = "3"
    session = _CaptureSession(
        [
            _FakeResult(first_row=SimpleNamespace(script_id=_SCRIPT_ID)),
            _FakeResult(scalar_first=_dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc)),
            _FakeResult(scalar_first=omitted_row),  # UPDATE ... RETURNING
        ]
    )
    with patch.object(scene_mod, "write_scope", lambda: _ScopeCtx(session)):
        out = await ScriptSceneRepository().delete(str(_SCENE_ID))

    assert out["deleted"] is False
    assert out["omitted"] is True
    assert out["scene"]["scene_number"] == "3"  # number preserved, never cleared
    del_stmts = [
        s
        for s in session.statements
        if str(s.compile(dialect=postgresql.dialect()))
        .strip()
        .upper()
        .startswith("DELETE")
    ]
    assert len(del_stmts) == 0  # never a hard DELETE once locked
    upd_sql, upd_params = _rendered(session.statements[-1])
    assert upd_sql.strip().upper().startswith("UPDATE PUBLIC.SCRIPT_SCENES")
    assert "omitted_at" in upd_sql.lower()


@pytest.mark.asyncio
async def test_delete_missing_scene_is_a_quiet_noop():
    session = _CaptureSession([_FakeResult(first_row=None)])
    with patch.object(scene_mod, "write_scope", lambda: _ScopeCtx(session)):
        out = await ScriptSceneRepository().delete(str(_SCENE_ID))
    assert out == {"deleted": False, "omitted": False, "scene": None}


# ── create_after_lock: post-lock insert (letter suffix) ──────────────────


@pytest.mark.asyncio
async def test_create_after_lock_raises_when_script_not_locked():
    session = _CaptureSession([_FakeResult(scalar_first=None)])
    with patch.object(scene_mod, "read_scope", lambda: _ScopeCtx(session)):
        with pytest.raises(ValueError):
            await ScriptSceneRepository().create_after_lock(
                {"script_id": str(_SCRIPT_ID)}
            )


@pytest.mark.asyncio
async def test_create_after_lock_assigns_letter_suffix_between_neighbours():
    """A new scene landing (via the mocked create()/move_scene()) between the
    locked "3" and "4" gets "3A" — and the existing neighbours' own numbers
    are never part of any UPDATE statement (never renumbered)."""
    e1, e2, e3, e4, e5 = (
        700000000000000021,
        700000000000000022,
        700000000000000023,
        700000000000000024,
        700000000000000025,
    )
    new_scene_id = 700000000000000099
    siblings = [
        (e1, 1000, "1"),
        (e2, 2000, "2"),
        (e3, 3000, "3"),
        (new_scene_id, 3500, None),  # newly created + moved into place
        (e4, 4000, "4"),
        (e5, 5000, "5"),
    ]
    numbered_row = _scene_obj(sort_order=3500)
    numbered_row.id = new_scene_id
    numbered_row.scene_number = "3A"

    session = _CaptureSession(
        [
            _FakeResult(scalar_first=_dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc)),
            _FakeResult(all_rows=siblings),  # script-wide siblings post-placement
            _FakeResult(scalar_first=numbered_row),  # final UPDATE ... RETURNING
        ]
    )
    repo = ScriptSceneRepository()
    with (
        patch.object(scene_mod, "read_scope", lambda: _ScopeCtx(session)),
        patch.object(scene_mod, "write_scope", lambda: _ScopeCtx(session)),
        patch.object(
            repo,
            "create",
            AsyncMock(return_value={"id": new_scene_id, "chapter_id": None}),
        ),
        patch.object(
            repo,
            "move_scene",
            AsyncMock(return_value={"id": new_scene_id, "chapter_id": None}),
        ),
    ):
        out = await repo.create_after_lock(
            {"script_id": str(_SCRIPT_ID)},
            before_scene_id=str(e3),
            after_scene_id=str(e4),
        )

    assert out["scene_number"] == "3A"
    assert out["scene_no_in_episode"] == "3A"
    upd_sql, upd_params = _rendered(session.statements[-1])
    assert upd_sql.strip().upper().startswith("UPDATE PUBLIC.SCRIPT_SCENES")
    assert "3A" in upd_params.values()
    # The ONLY scene_number UPDATE is for the new scene — its own id is bound,
    # not e3's or e4's (their numbers are never touched by this call).
    assert new_scene_id in upd_params.values()
    assert e3 not in upd_params.values()
    assert e4 not in upd_params.values()


@pytest.mark.asyncio
async def test_create_after_lock_between_bare_and_lettered_repositions_and_reorders():
    """SAME-BASE CORNER regression: inserting between "3" and its own already-
    lettered child "3A" cannot get a letter that sorts there (no suffix sorts
    after "" and before "A") — the fix repositions the row to sit after "3A"
    instead and assigns "3B", so display order (3, 3A, 3B, 4) agrees with the
    label, instead of the pre-fix bug (3, 3B, 3A, 4) where the row was left
    at the literally-requested slot with a label that sorted past it."""
    e3, e3a, e4 = 700000000000000041, 700000000000000042, 700000000000000043
    new_scene_id = 700000000000000097

    # Siblings right after the CALLER-requested placement (between "3" and
    # "3A") — this is the pre-fix physical position.
    siblings_before_reposition = [
        (e3, 3000, "3"),
        (new_scene_id, 3500, None),
        (e3a, 4000, "3A"),
        (e4, 5000, "4"),
    ]
    # Siblings after the fix's second move_scene call (repositioned after "3A").
    siblings_after_reposition = [
        (e3, 3000, "3"),
        (e3a, 4000, "3A"),
        (new_scene_id, 4500, None),
        (e4, 5000, "4"),
    ]
    numbered_row = _scene_obj(sort_order=4500)
    numbered_row.id = new_scene_id
    numbered_row.scene_number = "3B"

    session = _CaptureSession(
        [
            _FakeResult(scalar_first=_dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc)),
            _FakeResult(all_rows=siblings_before_reposition),
            _FakeResult(all_rows=siblings_after_reposition),
            _FakeResult(scalar_first=numbered_row),  # final UPDATE ... RETURNING
        ]
    )
    repo = ScriptSceneRepository()
    with (
        patch.object(scene_mod, "read_scope", lambda: _ScopeCtx(session)),
        patch.object(scene_mod, "write_scope", lambda: _ScopeCtx(session)),
        patch.object(
            repo,
            "create",
            AsyncMock(return_value={"id": new_scene_id, "chapter_id": None}),
        ),
        patch.object(repo, "move_scene", AsyncMock()) as move_mock,
    ):
        out = await repo.create_after_lock(
            {"script_id": str(_SCRIPT_ID)},
            before_scene_id=str(e3a),
            after_scene_id=str(e3),
        )

    # The label agrees with where the fix physically places the row: after
    # "3A", sorting between "3A" and "4" — NOT the literally-requested slot.
    assert out["scene_number"] == "3B"
    assert out["scene_no_in_episode"] == "3B"

    # move_scene fires TWICE: the caller's requested placement, then the
    # same-base-corner reposition — the second call anchors on "after 3A"
    # (e3a's id), not the caller's original before/after pair.
    assert move_mock.call_count == 2
    first_call, second_call = move_mock.call_args_list
    assert first_call.kwargs.get("before_scene_id") == str(e3a)
    assert first_call.kwargs.get("after_scene_id") == str(e3)
    assert second_call.kwargs.get("after_scene_id") == str(e3a)
    assert second_call.kwargs.get("before_scene_id") is None

    # Neither "3"'s nor "3A"'s own id/number appears in the final UPDATE —
    # only the new scene's id and its own "3B" label are bound.
    upd_sql, upd_params = _rendered(session.statements[-1])
    assert upd_sql.strip().upper().startswith("UPDATE PUBLIC.SCRIPT_SCENES")
    assert "3B" in upd_params.values()
    assert new_scene_id in upd_params.values()
    assert e3 not in upd_params.values()
    assert e3a not in upd_params.values()


@pytest.mark.asyncio
async def test_create_after_lock_tail_append_gets_plain_next_integer():
    """Appending past the last locked scene (no anchors) continues the plain
    sequence — no letter suffix, nothing to protect."""
    e1, e2, e3 = 700000000000000031, 700000000000000032, 700000000000000033
    new_scene_id = 700000000000000098
    siblings = [
        (e1, 1000, "1"),
        (e2, 2000, "2"),
        (e3, 3000, "3"),
        (new_scene_id, 4000, None),
    ]
    numbered_row = _scene_obj(sort_order=4000)
    numbered_row.id = new_scene_id
    numbered_row.scene_number = "4"

    session = _CaptureSession(
        [
            _FakeResult(scalar_first=_dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc)),
            _FakeResult(all_rows=siblings),
            _FakeResult(scalar_first=numbered_row),
        ]
    )
    repo = ScriptSceneRepository()
    with (
        patch.object(scene_mod, "read_scope", lambda: _ScopeCtx(session)),
        patch.object(scene_mod, "write_scope", lambda: _ScopeCtx(session)),
        patch.object(
            repo,
            "create",
            AsyncMock(return_value={"id": new_scene_id, "chapter_id": None}),
        ),
        patch.object(repo, "move_scene", AsyncMock()) as move_mock,
    ):
        out = await repo.create_after_lock({"script_id": str(_SCRIPT_ID)})

    move_mock.assert_not_called()  # no anchors -> plain tail append, no move needed
    assert out["scene_number"] == "4"


@pytest.mark.asyncio
async def test_create_after_lock_skips_an_omitted_scenes_number():
    """An OMITTED scene (agent-layer spec §4.2's 'delete after lock' — row
    kept, omitted_at stamped, scene_number PRESERVED) still occupies its
    number forever: the siblings query that feeds existing_numbers has no
    ``WHERE omitted_at IS NULL`` filter, so "3A" being omitted does not free
    it up for a later insert to reuse — the next insert at base 3 must skip
    straight to "3B"."""
    e3, e3_omitted, e4 = (
        700000000000000051,
        700000000000000052,
        700000000000000053,
    )
    new_scene_id = 700000000000000096
    siblings = [
        (e3, 1000, "3"),
        (e3_omitted, 1500, "3A"),  # omitted_at set, but scene_number kept
        (new_scene_id, 1750, None),
        (e4, 2000, "4"),
    ]
    numbered_row = _scene_obj(sort_order=1750)
    numbered_row.id = new_scene_id
    numbered_row.scene_number = "3B"

    session = _CaptureSession(
        [
            _FakeResult(scalar_first=_dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc)),
            _FakeResult(all_rows=siblings),
            _FakeResult(scalar_first=numbered_row),
        ]
    )
    repo = ScriptSceneRepository()
    with (
        patch.object(scene_mod, "read_scope", lambda: _ScopeCtx(session)),
        patch.object(scene_mod, "write_scope", lambda: _ScopeCtx(session)),
        patch.object(
            repo,
            "create",
            AsyncMock(return_value={"id": new_scene_id, "chapter_id": None}),
        ),
        patch.object(
            repo,
            "move_scene",
            AsyncMock(return_value={"id": new_scene_id, "chapter_id": None}),
        ),
    ):
        out = await repo.create_after_lock(
            {"script_id": str(_SCRIPT_ID)},
            before_scene_id=str(e3_omitted),
            after_scene_id=str(e4),
        )

    # "3A" is taken (even though omitted) -> next unused is "3B", not a
    # collision with the omitted scene's preserved number.
    assert out["scene_number"] == "3B"
    upd_sql, upd_params = _rendered(session.statements[-1])
    assert "3B" in upd_params.values()


# ── Real-aiosqlite row-shape regression (B5 review leftover — deferred
# minors batch, Minor 4) ─────────────────────────────────────────────────
#
# Every test above fakes the Result (_FakeResult), so the B4 row-shape bug
# class (a JOIN select of individually-labeled columns vs an accidental
# select(Entity)/select(*Entity.__table__.c) collapsing the mapping to one
# key) has no coverage for list_scene_rows_for_project's fetch->consume
# chain (``.mappings().all()`` -> ``dict(r)`` in the repository method).
# Mirrors tests/test_orm_b5_task1_row_shape_e2e.py's positive/negative-
# control pair with a genuine aiosqlite engine.

_SCRIPT_PROJECTS_DDL = """
CREATE TABLE script_projects (
    id INTEGER PRIMARY KEY, project_id INTEGER, team_id INTEGER,
    name TEXT, created_by TEXT, display_code TEXT, description TEXT,
    settings_json TEXT, viewport_json TEXT, status TEXT,
    created_at TIMESTAMP, updated_at TIMESTAMP, episode_id INTEGER,
    numbering_locked_at TIMESTAMP
)
"""
_SCRIPT_SCENES_DDL = """
CREATE TABLE script_scenes (
    id INTEGER PRIMARY KEY, script_id INTEGER, chapter_id INTEGER,
    heading_int_ext TEXT, location_text TEXT, location_id INTEGER,
    time_of_day TEXT, content_json TEXT, content TEXT,
    content_version INTEGER, position_x REAL, position_y REAL,
    width REAL, height REAL, sort_order INTEGER,
    created_at TIMESTAMP, updated_at TIMESTAMP, scene_number TEXT,
    omitted_at TIMESTAMP
)
"""


async def _seed_project_scene_pipeline(engine) -> None:
    async with engine.begin() as conn:
        await conn.exec_driver_sql(_SCRIPT_PROJECTS_DDL)
        await conn.exec_driver_sql(_SCRIPT_SCENES_DDL)
        await conn.execute(
            _sa_insert(ScriptProjects.__table__).values(
                id=10,
                project_id=99,
                team_id=1,
                name="Script A",
                created_by=_uuid_mod.uuid4(),
                status="active",
                episode_id=5,
            )
        )
        await conn.execute(
            _sa_insert(ScriptScenes.__table__).values(
                id=100,
                script_id=10,
                location_text="INT. HOUSE - DAY",
                content_json=[{"type": "action", "text": "hi"}],
                content="",
                content_version=0,
                sort_order=0,
            )
        )


@pytest.mark.asyncio
async def test_project_scene_rows_stmt_yields_column_keyed_row_against_real_sqlite():
    """The REAL production statement (``_project_scene_rows_stmt``, imported
    — not reconstructed here) round-tripped through a genuine aiosqlite
    ``Result`` gives a column-keyed RowMapping matching
    ``list_scene_rows_for_project``'s ``dict(r)`` consumption — including a
    real JSONB round-trip for ``content_json`` (a Python list survives, not
    a serialized string)."""
    engine = _create_async_engine("sqlite+aiosqlite://")
    engine = engine.execution_options(schema_translate_map={"public": None})
    await _seed_project_scene_pipeline(engine)

    sessionmaker = _async_sessionmaker(
        engine, class_=_AsyncSession, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session:
            rows = (
                (await session.execute(_project_scene_rows_stmt(99))).mappings().all()
            )
    finally:
        await engine.dispose()

    assert len(rows) == 1
    row = dict(rows[0])  # exact consumption shape used by the repository
    assert row["episode_id"] == 5
    assert row["location_text"] == "INT. HOUSE - DAY"
    assert row["content_json"] == [{"type": "action", "text": "hi"}]


@pytest.mark.asyncio
async def test_project_scene_rows_stmt_entity_level_negative_control_proves_sensitivity():
    """Negative control: selecting the ScriptScenes ENTITY alongside
    ScriptProjects.episode_id (rather than the individually-labeled columns
    ``_project_scene_rows_stmt`` uses) produces an entity-keyed mapping for
    the scene fields — proving the column-level select choice is
    load-bearing, not incidental."""
    engine = _create_async_engine("sqlite+aiosqlite://")
    engine = engine.execution_options(schema_translate_map={"public": None})
    await _seed_project_scene_pipeline(engine)

    sessionmaker = _async_sessionmaker(
        engine, class_=_AsyncSession, expire_on_commit=False
    )
    try:
        bad_stmt = (
            _sa_select(ScriptProjects.episode_id.label("episode_id"), ScriptScenes)
            .select_from(ScriptScenes)
            .join(ScriptProjects, ScriptProjects.id == ScriptScenes.script_id)
            .where(ScriptProjects.project_id == 99, ScriptProjects.status != "deleted")
        )
        async with sessionmaker() as session:
            bad_rows = (await session.execute(bad_stmt)).mappings().all()
    finally:
        await engine.dispose()

    assert len(bad_rows) == 1
    bad_row = bad_rows[0]
    assert "ScriptScenes" in bad_row.keys()  # entity-keyed, not column-keyed
    assert bad_row.get("location_text") is None  # dict(r) consumption would break
    with pytest.raises(KeyError):
        bad_row["location_text"]
