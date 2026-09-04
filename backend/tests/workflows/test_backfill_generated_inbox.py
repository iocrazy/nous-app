"""Unit tests for the generated-inbox backfill planner (pure) + admin wiring.

``plan_inbox_backfill`` is pure: no DB, no DBOS. Two decisions live in it and
nowhere else — which legacy temp uploads get a ``generated_media`` row, and
which already-registered rows get flipped to ``in_assets`` — so both are
pinned here, including the re-run (idempotency) shape.

``TestSystemScopeWrapping`` covers the one thing the pure planner cannot:
that the workflow enters a system scope before it touches the DB, and
``TestChatUploadsFolderIdentity`` covers the other: which folders the register
half actually reads from (mig 450 moved that from a name to a ``system_key``).
"""

from types import SimpleNamespace

import pytest

from app.api.admin.backfill_router import _BACKFILLS, workflow_kwargs
from app.workflows.backfill_generated_inbox import plan_inbox_backfill

SCOPE = 700
CREATOR = "11111111-1111-1111-1111-111111111111"


def _res(rid, *, mime="image/png", scope_id=SCOPE):
    return {
        "id": rid,
        "scope_id": scope_id,
        "creator_id": CREATOR,
        "file_path": f"teams/{scope_id}/temp/{rid}.png",
        "mime_type": mime,
    }


def _gen(gid, resource_id, *, review_state="saved", scope_id=SCOPE):
    return {
        "id": gid,
        "promoted_resource_id": resource_id,
        "review_state": review_state,
        "scope_id": scope_id,
    }


class TestRegisterPlan:
    def test_unregistered_temp_resource_is_planned(self):
        plan = plan_inbox_backfill([_res(1), _res(2)], {}, set())
        assert [r["id"] for r in plan["to_register"]] == [1, 2]
        assert plan["counts"] == {
            "temp_resources": 2,
            "already_registered": 0,
            "to_register": 2,
            "to_mark_in_assets": 0,
        }

    def test_already_registered_temp_resource_is_skipped(self):
        plan = plan_inbox_backfill([_res(1), _res(2)], {1: _gen(9001, 1)}, set())
        assert [r["id"] for r in plan["to_register"]] == [2]
        assert plan["counts"]["already_registered"] == 1
        assert plan["counts"]["to_register"] == 1

    def test_rerunning_the_plan_output_registers_nothing(self):
        """Idempotency, stated on the planner: feed it the world it just made."""
        first = plan_inbox_backfill([_res(1), _res(2)], {}, set())
        after = {
            int(r["id"]): _gen(9000 + int(r["id"]), int(r["id"]))
            for r in first["to_register"]
        }
        second = plan_inbox_backfill([_res(1), _res(2)], after, set())
        assert second["to_register"] == []
        assert second["counts"]["already_registered"] == 2

    def test_registration_carries_the_row_a_writer_needs(self):
        plan = plan_inbox_backfill([_res(5, mime="video/mp4")], {}, set())
        row = plan["to_register"][0]
        assert row["scope_id"] == SCOPE
        assert row["creator_id"] == CREATOR
        assert row["file_path"].endswith("5.png")
        assert row["mime_type"] == "video/mp4"


class TestMarkInAssets:
    def test_promoted_row_with_an_asset_file_is_marked(self):
        plan = plan_inbox_backfill([], {1: _gen(9001, 1)}, {1})
        assert plan["to_mark_in_assets"] == [9001]
        assert plan["counts"]["to_mark_in_assets"] == 1

    def test_promoted_row_without_an_asset_file_is_left_alone(self):
        plan = plan_inbox_backfill([], {1: _gen(9001, 1)}, set())
        assert plan["to_mark_in_assets"] == []

    def test_already_in_assets_is_not_replanned(self):
        plan = plan_inbox_backfill(
            [], {1: _gen(9001, 1, review_state="in_assets")}, {1}
        )
        assert plan["to_mark_in_assets"] == []

    def test_deleted_row_is_never_resurrected(self):
        """A dismissed generation stays dismissed.

        'deleted' is a user decision about the inbox card, not a statement
        about the file; flipping it to in_assets because someone attached the
        promoted resource to an asset would un-dismiss it behind their back.
        """
        plan = plan_inbox_backfill([], {1: _gen(9001, 1, review_state="deleted")}, {1})
        assert plan["to_mark_in_assets"] == []

    def test_unreviewed_row_with_an_asset_file_is_marked(self):
        plan = plan_inbox_backfill(
            [], {1: _gen(9001, 1, review_state="unreviewed")}, {1}
        )
        assert plan["to_mark_in_assets"] == [9001]

    def test_asset_file_on_a_resource_nobody_generated_is_ignored(self):
        """asset_files rows for plain uploads have no generation to flip."""
        plan = plan_inbox_backfill([], {}, {42})
        assert plan["to_mark_in_assets"] == []
        assert plan["counts"]["to_mark_in_assets"] == 0


class TestBothHalvesTogether:
    def test_counts_are_independent(self):
        plan = plan_inbox_backfill(
            [_res(1), _res(2), _res(3)],
            {1: _gen(9001, 1), 8: _gen(9008, 8)},
            {8},
        )
        assert [r["id"] for r in plan["to_register"]] == [2, 3]
        assert plan["to_mark_in_assets"] == [9008]
        assert plan["counts"] == {
            "temp_resources": 3,
            "already_registered": 1,
            "to_register": 2,
            "to_mark_in_assets": 1,
        }


class TestRouterWiring:
    def test_registered(self):
        assert "generated_inbox" in _BACKFILLS

    def test_run_user_id_passed_but_not_limit(self):
        """No ``limit``: the workflow takes none, and passing one would be a
        TypeError at dispatch."""
        body = SimpleNamespace(dry_run=True, limit=10)
        assert workflow_kwargs("generated_inbox", body, "admin-uuid") == {
            "dry_run": True,
            "run_user_id": "admin-uuid",
        }


class TestAssetFilesQuery:
    """I1: a soft-deleted asset keeps its asset_files rows.

    Asserted on the compiled SQL, not on Python: without the join to
    ``assets`` the query still runs and still returns rows — it just quietly
    flips resources attached only to a deleted asset to ``in_assets``, and
    nothing downstream would say so.
    """

    def test_deleted_assets_are_excluded(self):
        from sqlalchemy.dialects import postgresql

        from app.workflows.backfill_generated_inbox import (
            _resources_with_asset_files_stmt,
        )

        sql = str(
            _resources_with_asset_files_stmt().compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        assert "assets.deleted_at IS NULL" in sql
        assert "JOIN public.assets" in sql
        assert "DISTINCT" in sql


class TestChatUploadsFolderIdentity:
    """P6 mig 450: the folder is matched by ``system_key``, not by name.

    The register half's entire correctness is this predicate, and it is only
    visible on the compiled SQL — every wrong version of it still runs, still
    returns rows, and reports whatever it missed as "already registered".

    The criterion is IMPORTED from ``chat_upload`` rather than restated here
    for the same reason ``_NOT_MARKABLE`` is shared: two copies would let the
    upload path and the reconciliation disagree about which folder holds a
    scope's chat uploads, and the disagreement would be silent.
    """

    def _sql(self):
        from sqlalchemy.dialects import postgresql

        from app.workflows.backfill_generated_inbox import (
            _chat_upload_resources_stmt,
        )

        return str(
            _chat_upload_resources_stmt().compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )

    def test_the_keyed_folder_is_matched(self):
        assert "system_key = 'chat_uploads'" in self._sql()

    def test_a_not_yet_adopted_legacy_temp_folder_is_still_matched(self):
        """Migration 450 and this workflow have no ordering guarantee, and the
        migration adopts only ONE ``temp`` folder per scope. Dropping this arm
        turns "the migration has not run here" into an empty, confident plan."""
        sql = self._sql()
        assert "system_key IS NULL" in sql
        assert "name = 'temp'" in sql

    def test_the_two_arms_are_a_union_not_a_conjunction(self):
        sql = self._sql()
        assert "system_key = 'chat_uploads' OR public.folders.system_key IS NULL" in sql

    def test_trashed_folders_and_resources_are_excluded(self):
        sql = self._sql()
        assert "public.folders.is_trashed IS false" in sql
        assert "public.resources.is_trashed IS false" in sql

    def test_rows_without_a_file_path_are_excluded(self):
        """``generated_media.file_path`` is NOT NULL — registering these would
        fail the insert rather than the plan."""
        assert "public.resources.file_path IS NOT NULL" in self._sql()

    def test_the_criterion_comes_from_the_upload_path(self):
        import app.workflows.backfill_generated_inbox as wf
        from app.services.library.chat_upload import chat_uploads_folder_criteria

        assert wf.chat_uploads_folder_criteria is chat_uploads_folder_criteria


class TestTempSweeperIsGone:
    """Spec decision 7: temp clean-up is manual (`POST /generated/cleanup`).

    The inbox rows this task creates point at the resource's own file; a
    daily sweep would trash those files under a live inbox card. The cron
    decorator had been commented out since 2026-06-13 and P1 removed the
    scheduled-bundle import; asset-library P6 (2026-09-04) deleted the module
    itself, so the sweep can no longer be armed by uncommenting one line.

    Both assertions stay falsifiable: restoring the module turns the first
    red, and re-adding a bundle import turns the second red.
    """

    def test_the_sweeper_module_no_longer_exists(self):
        import importlib.util

        assert importlib.util.find_spec("app.workflows.temp_resource_sweeper") is None

    def test_scheduled_bundle_does_not_export_the_sweeper(self):
        import app.workflows._scheduled_bundle as bundle

        assert not hasattr(bundle, "temp_resource_sweeper_scheduled")
        assert not hasattr(bundle, "sweep_temp_resources")


class TestSystemScopeWrapping:
    """The DB halves must run under an ambient system scope.

    ``_load_inputs`` selects from ``Resources``, a ``UserScoped`` model, and
    production runs with ``SCOPE_ENFORCE_RESOURCES=True`` — with no scope set
    the very first SELECT raises ``UnscopedQueryError`` and the run dies
    before reading a row (observed in production, 2026-08-29).

    Asserted as an **ordered event log** rather than "the workflow returned
    something": a stubbed ``_load_inputs`` succeeds with or without a scope,
    so only the ordering can tell the two apart. Falsifiability: drop the
    ``async with system_request_scope(...)`` from the workflow and both tests
    below fail on the missing ``scope_enter``.
    """

    @staticmethod
    def _harness(monkeypatch, events):
        """Patch DBOS, the task manager, the scope, and both DB halves."""
        import inspect
        from contextlib import asynccontextmanager
        from unittest.mock import MagicMock, patch

        import app.workflows.backfill_generated_inbox as m

        @asynccontextmanager
        async def fake_scope(reason: str):
            events.append(("scope_enter", reason))
            try:
                yield
            finally:
                events.append(("scope_exit", reason))

        async def fake_load_inputs():
            events.append(("load_inputs", None))
            return [_res(1)], {}, set()

        async def fake_apply(plan, run_user_id):
            events.append(("apply", run_user_id))
            return {
                "registered": len(plan["to_register"]),
                "marked_in_assets": 0,
                "registered_ids": [],
                "marked_ids": [],
            }

        class _Manager:
            async def create(self, **kw):
                events.append(("manager_create", kw.get("user_id")))

            async def start(self, task_id, **kw):
                events.append(("manager_start", task_id))

            async def complete(self, task_id, **kw):
                events.append(("manager_complete", task_id))

            async def patch_metadata(self, task_id, patch_):
                events.append(("manager_patch_metadata", task_id))

        monkeypatch.setattr(m, "system_request_scope", fake_scope)
        monkeypatch.setattr(m, "_load_inputs", fake_load_inputs)
        monkeypatch.setattr(m, "_apply", fake_apply)

        dbos = MagicMock()
        dbos.workflow_id = "wf-backfill-inbox-1"
        return (
            m,
            patch.object(m, "DBOS", dbos),
            patch(
                "app.services.infra.unified_task_manager.get_task_manager",
                return_value=_Manager(),
            ),
            inspect,
        )

    @pytest.mark.asyncio
    async def test_dry_run_reads_inside_a_system_scope(self, monkeypatch):
        events = []
        m, p_dbos, p_manager, inspect = self._harness(monkeypatch, events)
        with p_dbos, p_manager:
            out = await inspect.unwrap(m.backfill_generated_inbox)(dry_run=True)

        assert out["counts"]["to_register"] == 1
        names = [e[0] for e in events]
        # The read is inside the scope, and the scope closes after it.
        assert names.index("scope_enter") < names.index("load_inputs")
        assert names.index("load_inputs") < names.index("scope_exit")
        # The reason is the audit trail a system scope is required to carry.
        assert ("scope_enter", "backfill-generated-inbox") in events
        # Task Center writes touch task_tracking, which is not scope-enforced:
        # they stay outside so the scope covers exactly the enforced work.
        assert names.index("manager_start") < names.index("scope_enter")
        assert names.index("scope_exit") < names.index("manager_complete")

    @pytest.mark.asyncio
    async def test_live_run_writes_inside_the_same_system_scope(self, monkeypatch):
        """``_apply`` writes ``generated_media`` rows keyed off scoped reads —
        it must be under the scope too, not just the planning read."""
        events = []
        m, p_dbos, p_manager, inspect = self._harness(monkeypatch, events)
        with p_dbos, p_manager:
            out = await inspect.unwrap(m.backfill_generated_inbox)(
                dry_run=False, run_user_id="admin-uuid"
            )

        assert out["applied"]["registered"] == 1
        names = [e[0] for e in events]
        assert names.index("scope_enter") < names.index("apply")
        assert names.index("apply") < names.index("scope_exit")
        assert ("apply", "admin-uuid") in events
