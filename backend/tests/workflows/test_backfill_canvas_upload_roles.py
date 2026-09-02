"""backfill_canvas_upload_roles: what a legacy canvas_upload row proves.

Three layers, none of which needs a database:

* ``role_for_filename`` — the classification rule. Getting this wrong misfiles
  rows, and the only user-visible harm this backfill can do is hiding a file a
  person named themselves.
* ``plan_role_backfill`` — the plan and the ``by_role`` tally. That tally is
  what an operator reads off the dry run to decide whether to go live, so it
  is decision-support, not decoration.
* ``run_backfill`` — the loop, driven through a fake session. Pins that a dry
  run writes NOTHING and that a live run writes once per classifiable row.
  Called directly rather than through the ``@DBOS.workflow()`` shell, the same
  split ``backfill_publish_task_team_ids`` uses.

Plus ``role_update_stmt``, compiled: the ``role IS NULL`` re-check and the
atomic JSONB merge are both invisible in Python and only exist in the SQL.
"""

from contextlib import asynccontextmanager

import pytest
from sqlalchemy.dialects import postgresql

import app.workflows.backfill_canvas_upload_roles as wf
from app.api.admin.backfill_router import _BACKFILLS, workflow_kwargs
from app.workflows.backfill_canvas_upload_roles import (
    plan_role_backfill,
    role_for_filename,
    role_update_stmt,
)


def test_the_two_editor_filenames_are_classified():
    assert role_for_filename("mask.png") == "mask"
    assert role_for_filename("brush.png") == "brush"


def test_a_user_named_file_is_left_alone():
    """Exact match only.

    A ``contains``/``startswith`` rule would sweep up files a PERSON named —
    and a file the user chose is not evidence of anything. Hiding one of those
    is the only user-visible harm this backfill can do, so the rule that
    prevents it is pinned here.
    """
    for name in (
        "face-mask.png",
        "mask.png.bak",
        "Mask.png",
        "my brush.png",
        "photo.png",
        "",
        None,
    ):
        assert role_for_filename(name) is None, name


def test_reference_is_deliberately_not_recoverable():
    """The third intermediate has no legacy signature, on purpose.

    ``import-from-resource`` wrote no params at all, so an old transcoded
    reference is indistinguishable from a plain upload. This asserts we did
    not invent a rule for it — a heuristic here would hide the user's own
    files.
    """
    from app.services.library.generated_roles import LEGACY_FILENAME_ROLES

    assert "reference" not in LEGACY_FILENAME_ROLES.values()


def test_registered_and_gets_the_dispatching_admin_as_owner():
    """Registry wiring + the run_user_id that makes the task row exist.

    The template's all-zero system id violates the task_tracking FK and the
    row is never created — a backfill that runs invisibly (2026-08-14).
    """
    assert "canvas_upload_roles" in _BACKFILLS

    class _Body:
        dry_run = True
        limit = 500

    kwargs = workflow_kwargs("canvas_upload_roles", _Body(), "admin-uuid")
    assert kwargs["run_user_id"] == "admin-uuid"
    assert kwargs["dry_run"] is True
    assert kwargs["limit"] == 500


# ─── The plan and the operator's tally ──────────────────────────────────────


def _row(rid, filename=None, **extra):
    params = {} if filename is None else {"filename": filename}
    params.update(extra)
    return {"id": rid, "params": params}


class TestPlan:
    def test_tally_is_per_role_and_matches_the_stamp_list(self):
        """`by_role` is the number the dry run exists to produce.

        An operator reads it to decide whether to go live; a plan that
        stamped rows without reporting what it stamped would make the dry
        run useless for its only purpose.
        """
        plan = plan_role_backfill(
            [
                _row(1, "mask.png"),
                _row(2, "brush.png"),
                _row(3, "mask.png"),
                _row(4, "holiday.png"),
                _row(5),
            ]
        )
        assert plan["by_role"] == {"mask": 2, "brush": 1}
        assert [i["id"] for i in plan["to_stamp"]] == [1, 2, 3]
        assert plan["counts"] == {
            "scanned": 5,
            "unclassifiable": 2,
            "classifiable": 3,
        }

    def test_rows_it_cannot_classify_are_counted_not_touched(self):
        """Counted, so a re-run re-scans them cheaply rather than needing a
        "checked" marker column — and so the operator can see how many rows
        the rule could not speak for."""
        plan = plan_role_backfill([_row(1, "holiday.png"), _row(2)])
        assert plan["to_stamp"] == []
        assert plan["by_role"] == {}
        assert plan["counts"]["unclassifiable"] == 2

    def test_a_row_with_other_params_still_classifies(self):
        # The merge preserves them; the plan must not refuse them.
        plan = plan_role_backfill([_row(1, "mask.png", entity_kind="character")])
        assert [i["role"] for i in plan["to_stamp"]] == ["mask"]


# ─── The UPDATE: two guarantees that exist only in the SQL ──────────────────


def _compile(stmt):
    """Compiled WITHOUT ``literal_binds``: JSONB has no literal renderer, and
    asserting on the bound parameters is the stronger check anyway — it reads
    the values the database will actually receive. Same convention as
    ``test_generated_media_inbox_repo::test_registered_resource_insert_shape``.
    """
    return stmt.compile(dialect=postgresql.dialect())


class TestUpdateStatement:
    def test_rechecks_that_the_role_is_still_null(self):
        """Idempotency AND hand-fix safety, in one predicate.

        Without it a re-run would overwrite a role an operator corrected by
        hand — the backfill would quietly undo the repair it was re-run to
        avoid needing.
        """
        compiled = _compile(role_update_stmt(4242, "mask"))
        sql = str(compiled)
        assert "IS NULL" in sql
        assert 4242 in compiled.params.values()

    def test_merges_into_params_instead_of_replacing_them(self):
        """`params || '{"role": ...}'`, evaluated by PostgreSQL.

        A read-modify-write would send back a snapshot taken during the SELECT
        and drop anything another writer had added to `params` in between.
        Asserted on the compiled SQL because in Python the two spellings look
        equally correct.
        """
        compiled = _compile(role_update_stmt(4242, "mask"))
        sql = str(compiled)
        # The column appears on the RIGHT of the SET, concatenated. A
        # whole-object assignment would read `SET params=%(params)s` and
        # mention the column exactly once there.
        assert "params || " in sql
        assert "JSONB" in sql.upper()
        assert {"role": "mask"} in compiled.params.values()


# ─── The loop: dry run writes nothing ───────────────────────────────────────


class _FakeManager:
    async def create(self, **kw):
        pass

    async def start(self, *a, **kw):
        pass

    async def complete(self, *a, **kw):
        pass

    async def patch_metadata(self, *a, **kw):
        pass


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _ReadSession:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, _stmt):
        return _FakeResult(self._rows)


class _WriteSession:
    def __init__(self, log):
        self._log = log

    async def execute(self, stmt):
        compiled = _compile(stmt)
        self._log.append((str(compiled), compiled.params))
        return None


@pytest.fixture
def harness(monkeypatch):
    def _install(rows):
        writes: list[str] = []

        @asynccontextmanager
        async def fake_read_scope():
            yield _ReadSession(rows)

        @asynccontextmanager
        async def fake_write_scope():
            yield _WriteSession(writes)

        monkeypatch.setattr(wf, "read_scope", fake_read_scope)
        monkeypatch.setattr(wf, "write_scope", fake_write_scope)
        monkeypatch.setattr(
            "app.services.infra.unified_task_manager.get_task_manager",
            lambda: _FakeManager(),
        )
        monkeypatch.setattr(wf.DBOS, "workflow_id", "wf-test", raising=False)
        return writes

    return _install


ROWS = [_row(1, "mask.png"), _row(2, "brush.png"), _row(3, "holiday.png")]


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(harness):
    """The default. `dry_run=True` must report and touch nothing — this is
    the whole reason the destructive default is the safe one."""
    writes = harness(ROWS)

    result = await wf.run_backfill(dry_run=True, limit=500)

    assert writes == []
    assert result["would_fix"] == 2
    assert result["fixed"] == 0
    assert result["fixed_ids"] == []
    # ...and it still reports the tally, which is the point of running it.
    assert result["by_role"] == {"mask": 1, "brush": 1}
    assert result["scanned"] == 3 and result["unclassifiable"] == 1


@pytest.mark.asyncio
async def test_live_run_writes_once_per_classifiable_row(harness):
    """The positive control: without it the dry-run test would pass against
    an implementation that never writes at all."""
    writes = harness(ROWS)

    result = await wf.run_backfill(dry_run=False, limit=500)

    assert len(writes) == 2
    assert result["fixed"] == 2
    assert result["fixed_ids"] == ["1", "2"]
    assert result["by_role"] == {"mask": 1, "brush": 1}
    # Each write carries the re-check, so a concurrent hand-fix survives.
    assert all("IS NULL" in sql for sql, _ in writes)
    roles = [v for _, params in writes for v in params.values() if isinstance(v, dict)]
    assert roles == [{"role": "mask"}, {"role": "brush"}]


@pytest.mark.asyncio
async def test_nothing_to_do_is_a_clean_no_op(harness):
    writes = harness([])

    result = await wf.run_backfill(dry_run=False, limit=500)

    assert writes == []
    assert result["scanned"] == 0 and result["fixed"] == 0
    assert result["by_role"] == {}
