"""Unit tests for the generated-inbox backfill planner (pure) + admin wiring.

``plan_inbox_backfill`` is pure: no DB, no DBOS. Two decisions live in it and
nowhere else — which legacy temp uploads get a ``generated_media`` row, and
which already-registered rows get flipped to ``in_assets`` — so both are
pinned here, including the re-run (idempotency) shape.
"""

from types import SimpleNamespace

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


class TestTempSweeperIsNotScheduled:
    """Spec decision 7: temp clean-up is manual (`POST /generated/cleanup`).

    The inbox rows this task creates point at the resource's own file; a
    daily sweep would trash those files under a live inbox card. The cron
    decorator has been commented out since 2026-06-13, but the worker still
    imported the module through the scheduled bundle — one uncommented line
    away from arming it again, and reading as "scheduled" to anyone who
    grepped the bundle.
    """

    def test_scheduled_bundle_does_not_export_the_sweeper(self):
        import app.workflows._scheduled_bundle as bundle

        assert not hasattr(bundle, "temp_resource_sweeper_scheduled")
        assert not hasattr(bundle, "sweep_temp_resources")

    def test_the_sweeper_itself_still_exists_for_manual_use(self):
        from app.workflows import temp_resource_sweeper

        assert callable(temp_resource_sweeper.sweep_temp_resources)
