"""Unit tests for ``ScriptShotRepository`` (capture-emitted-SQL style).

These run in the UNIT suite (no DSN): the session + ``read_scope`` /
``write_scope`` scopes are faked, and the emitted SQLAlchemy statements are
captured + compiled so we can prove the write-lane invariants without a live
database. Mirrors ``tests/test_script_scene_repository.py``.

The invariants under test are the load-bearing ones from the plan Task 2:

  - ``create_many`` inserts every shot in ONE session/transaction, numbering
    ``shot_number`` 1..N and ``sort_order`` on a fresh 1000-step ladder, and
    drops any caller-supplied ``status`` (a fresh shot is always 'empty').
  - ``update`` writes ONLY the parameter-tag / description whitelist — a
    smuggled ``status`` / image_url never reaches the UPDATE values.
  - ``update_status`` is the lane that writes ``status`` (+ passed URLs only).
  - ``move_shot`` renumbers the whole scene group when the neighbour gap is
    exhausted, and places at the midpoint on a wide gap (single UPDATE).
  - ``create`` auto-assigns ``sort_order = MAX + 1000`` within the scene.
  - every bigint id is ``_bigint``-coerced (str in → int bound param).
"""

from __future__ import annotations

import datetime as _dt
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy.dialects import postgresql

import app.repositories.script_shot_repository as shot_mod
from app.models.scripts import ScriptShots
from app.repositories.script_shot_repository import ScriptShotRepository

_SCENE_ID = 700000000000000001
_SHOT_ID = 700000000000000002


# ── fakes (mirror test_script_scene_repository.py) ───────────────────────


class _FakeResult:
    def __init__(self, *, scalar_first=None, first_row=None, all_rows=None, rowcount=1):
        self._scalar_first = scalar_first
        self._first_row = first_row
        self._all_rows = all_rows or []
        self.rowcount = rowcount

    def scalars(self):
        return self

    def first(self):
        return self._scalar_first if self._first_row is None else self._first_row

    def all(self):
        return self._all_rows


class _CaptureSession:
    def __init__(self, results):
        self.statements: list = []
        self._results = list(results)

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


def _shot_obj(shot_number=1, status="empty", sort_order=1000):
    return ScriptShots(
        id=_SHOT_ID,
        scene_id=_SCENE_ID,
        shot_number=shot_number,
        shot_type="WIDE",
        camera_angle="EYE",
        camera_movement="STATIC",
        focal_length="35mm",
        lighting="soft key",
        description="A man walks in.",
        status=status,
        sort_order=sort_order,
        created_at=_dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc),
        updated_at=_dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc),
    )


def _rendered(stmt) -> tuple[str, dict]:
    compiled = stmt.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


# ── create: auto sort_order + bigint coercion ────────────────────────────


@pytest.mark.asyncio
async def test_create_auto_sort_order_max_plus_1000():
    """create() sets sort_order = scene MAX + 1000 (sparse insertion base) and
    coerces a str scene_id to an int bound param."""
    created = _shot_obj(sort_order=4000)
    session = _CaptureSession(
        [
            _FakeResult(scalar_first=3000),  # func.max(sort_order) → 3000
            _FakeResult(scalar_first=created),  # INSERT ... RETURNING
        ]
    )
    with patch.object(shot_mod, "write_scope", lambda: _ScopeCtx(session)):
        out = await ScriptShotRepository().create(
            {"scene_id": str(_SCENE_ID), "shot_type": "WIDE"}
        )
    ins_sql, ins_params = _rendered(session.statements[1])
    assert ins_sql.strip().upper().startswith("INSERT INTO PUBLIC.SCRIPT_SHOTS")
    assert 4000 in ins_params.values()  # 3000 + 1000
    assert _SCENE_ID in ins_params.values()  # bigint-coerced scene_id
    assert all(
        not isinstance(v, str) or v != str(_SCENE_ID) for v in ins_params.values()
    )
    assert out["sort_order"] == 4000


@pytest.mark.asyncio
async def test_create_empty_scene_starts_at_1000():
    created = _shot_obj(sort_order=1000)
    session = _CaptureSession(
        [
            _FakeResult(scalar_first=None),  # no rows yet
            _FakeResult(scalar_first=created),
        ]
    )
    with patch.object(shot_mod, "write_scope", lambda: _ScopeCtx(session)):
        await ScriptShotRepository().create({"scene_id": str(_SCENE_ID)})
    _, ins_params = _rendered(session.statements[1])
    assert 1000 in ins_params.values()


# ── create_many: one transaction, numbered, status dropped ───────────────


@pytest.mark.asyncio
async def test_create_many_single_transaction_numbers_and_drops_status():
    """create_many inserts every shot in ONE session, numbering shot_number
    1..N and sort_order 1000..N*1000 for an EMPTY scene (base 0), and drops a
    caller-supplied status."""
    session = _CaptureSession(
        [
            _FakeResult(scalar_first=None),  # MAX(shot_number) → 0
            _FakeResult(scalar_first=None),  # MAX(sort_order) → 0
            _FakeResult(scalar_first=_shot_obj(shot_number=1, sort_order=1000)),
            _FakeResult(scalar_first=_shot_obj(shot_number=2, sort_order=2000)),
            _FakeResult(scalar_first=_shot_obj(shot_number=3, sort_order=3000)),
        ]
    )
    shots = [
        {"shot_type": "WIDE", "status": "done"},  # smuggled status → dropped
        {"shot_type": "MEDIUM"},
        {"shot_type": "CLOSE"},
    ]
    with patch.object(shot_mod, "write_scope", lambda: _ScopeCtx(session)):
        out = await ScriptShotRepository().create_many(str(_SCENE_ID), shots)

    # One session: 2 MAX scalars + 3 INSERTs (atomic breakdown).
    assert len(session.statements) == 5
    assert len(out) == 3
    for i, stmt in enumerate(session.statements[2:], start=1):
        sql, params = _rendered(stmt)
        assert sql.strip().upper().startswith("INSERT INTO PUBLIC.SCRIPT_SHOTS")
        assert i in params.values()  # shot_number = base(0) + sequence
        assert i * 1000 in params.values()  # sort_order ladder from base 0
        assert _SCENE_ID in params.values()  # bigint-coerced scene_id
        # The smuggled 'done' status never reaches any INSERT (server default).
        assert "done" not in params.values()


@pytest.mark.asyncio
async def test_create_many_appends_offset_from_existing_max():
    """A scene that already holds shots (MAX shot_number=2, sort_order=2000)
    gets the new batch APPENDED: shot_number 3/4/5, sort_order 3000/4000/5000 —
    no collision with, or clobber of, the user's existing shots."""
    session = _CaptureSession(
        [
            _FakeResult(scalar_first=2),  # MAX(shot_number) → 2
            _FakeResult(scalar_first=2000),  # MAX(sort_order) → 2000
            _FakeResult(scalar_first=_shot_obj(shot_number=3, sort_order=3000)),
            _FakeResult(scalar_first=_shot_obj(shot_number=4, sort_order=4000)),
            _FakeResult(scalar_first=_shot_obj(shot_number=5, sort_order=5000)),
        ]
    )
    shots = [{"shot_type": "WIDE"}, {"shot_type": "MEDIUM"}, {"shot_type": "CLOSE"}]
    with patch.object(shot_mod, "write_scope", lambda: _ScopeCtx(session)):
        out = await ScriptShotRepository().create_many(str(_SCENE_ID), shots)

    assert len(out) == 3
    inserts = session.statements[2:]
    for offset, stmt in enumerate(inserts):
        _, params = _rendered(stmt)
        assert (3 + offset) in params.values()  # shot_number 3,4,5
        assert (3000 + offset * 1000) in params.values()  # sort_order 3000,4000,5000


# ── update: whitelist excludes status + URLs ─────────────────────────────


@pytest.mark.asyncio
async def test_update_whitelist_excludes_status_and_urls():
    """update() writes only parameter tags / description — a status or image_url
    in the payload is silently dropped (that lane is update_status)."""
    session = _CaptureSession([_FakeResult(scalar_first=_shot_obj())])
    with patch.object(shot_mod, "write_scope", lambda: _ScopeCtx(session)):
        await ScriptShotRepository().update(
            str(_SHOT_ID),
            {
                "description": "New desc",
                "shot_type": "CLOSE",
                "status": "done",  # must NOT reach the UPDATE
                "image_url": "http://evil/x.png",  # must NOT reach the UPDATE
            },
        )
    upd_sql, upd_params = _rendered(session.statements[0])
    assert upd_sql.strip().upper().startswith("UPDATE PUBLIC.SCRIPT_SHOTS")
    assert "New desc" in upd_params.values()
    assert "CLOSE" in upd_params.values()
    assert "done" not in upd_params.values()
    assert "http://evil/x.png" not in upd_params.values()
    # The status/url columns are absent from the SET clause entirely.
    assert "status" not in upd_sql.split("WHERE")[0]
    assert "image_url" not in upd_sql.split("WHERE")[0]


@pytest.mark.asyncio
async def test_update_status_is_the_status_lane():
    """update_status writes status and ONLY the URLs explicitly passed (a None
    URL argument leaves that column untouched — no clobber on a 'generating'
    flip)."""
    session = _CaptureSession([_FakeResult(scalar_first=_shot_obj(status="done"))])
    with patch.object(shot_mod, "write_scope", lambda: _ScopeCtx(session)):
        await ScriptShotRepository().update_status(
            str(_SHOT_ID), "done", image_url="http://cdn/x.png"
        )
    upd_sql, upd_params = _rendered(session.statements[0])
    set_clause = upd_sql.split("WHERE")[0]
    assert "done" in upd_params.values()
    assert "http://cdn/x.png" in upd_params.values()
    assert "status" in set_clause
    assert "image_url" in set_clause
    # thumbnail_url / video_url were not passed → not in the SET clause.
    assert "thumbnail_url" not in set_clause
    assert "video_url" not in set_clause


# ── move_shot: renumber on gap exhaustion / sparse single update ──────────


@pytest.mark.asyncio
async def test_move_shot_renumbers_when_gap_exhausted():
    """Adjacent neighbour sort_orders (gap < 2) force a full scene-group
    renumber: every shot is rewritten to a fresh 1000-step ladder."""
    shot = _shot_obj(sort_order=5000)
    sib_a = (700000000000000010, 1000)
    sib_b = (700000000000000011, 1001)  # adjacent → gap 1
    session = _CaptureSession(
        [
            _FakeResult(scalar_first=shot),  # load moving shot
            _FakeResult(all_rows=[sib_a, sib_b]),  # siblings ordered
            _FakeResult(rowcount=1),  # renumber UPDATE 1
            _FakeResult(rowcount=1),  # renumber UPDATE 2
            _FakeResult(rowcount=1),  # renumber UPDATE 3 (moved shot)
            _FakeResult(scalar_first=_shot_obj(sort_order=2000)),  # re-read
        ]
    )
    with patch.object(shot_mod, "write_scope", lambda: _ScopeCtx(session)):
        await ScriptShotRepository().move_shot(
            str(_SHOT_ID), before_shot_id=str(sib_b[0])
        )
    update_stmts = [
        s
        for s in session.statements
        if str(s.compile(dialect=postgresql.dialect()))
        .strip()
        .upper()
        .startswith("UPDATE")
    ]
    assert len(update_stmts) == 3  # whole scene renumbered, not a single move


@pytest.mark.asyncio
async def test_move_shot_sparse_insert_single_update():
    """A wide gap between neighbours → a single UPDATE at the midpoint
    sort_order (no renumber)."""
    shot = _shot_obj(sort_order=9000)
    sib_a = (700000000000000010, 1000)
    sib_b = (700000000000000011, 3000)  # gap 2000 → midpoint 2000
    session = _CaptureSession(
        [
            _FakeResult(scalar_first=shot),
            _FakeResult(all_rows=[sib_a, sib_b]),
            _FakeResult(rowcount=1),  # single placement UPDATE
            _FakeResult(scalar_first=_shot_obj(sort_order=2000)),
        ]
    )
    with patch.object(shot_mod, "write_scope", lambda: _ScopeCtx(session)):
        await ScriptShotRepository().move_shot(
            str(_SHOT_ID), before_shot_id=str(sib_b[0])
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
