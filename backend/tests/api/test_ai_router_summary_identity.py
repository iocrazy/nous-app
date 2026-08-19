# backend/tests/api/test_ai_router_summary_identity.py

"""Regression tests for the ``/summarize/resource/{id}`` endpoint.

Bug B-1 (2026-08-07 diagnosis): the summary dispatch must run the
workflow as the resource OWNER, not the calling user.

Plus (Task 1b) the dedup race: the endpoint check-then-inserts, so
migration 121's partial unique index can still reject the INSERT, and
that must read as "already in progress" rather than a 500.

Points are already charged against ``resource_owner``'s team (see the
``resource.get("creator_id") or auth.user_id`` line ~349 in
ai_router.py). ``load_summary_inputs`` later filters transcripts by
``creator_id``. If the dispatched workflow's ``user_id`` kwarg is the
*caller* instead, a team member triggering summary on a shared resource
charges the owner's points and then fails "no transcript" — the owner
was charged, the workflow ran as someone else.

Follows the direct-coroutine-call + monkeypatch convention already used
for the sibling transcribe endpoint in
test_transcribe_auto_extract_chain.py (no HTTP test client exists for
ai_router in this repo).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api import ai_router
from app.core.deps import AuthContext


def _auth() -> AuthContext:
    """The caller — a team member, NOT the resource's creator."""
    return AuthContext(user_id="caller-uuid", auth_type="jwt")


class _NoActiveSession:
    """ORM session stand-in for both reads this endpoint makes on the
    session boundary: the dedup probe (``.first()``) and the flow-join
    lookup (``.scalars().first()``). Both must answer "no row" — leaving
    the second one as a bare MagicMock would hand the endpoint a truthy
    object and fake a flow id into the created row."""

    async def execute(self, *_a, **_k):
        result = MagicMock()
        result.first.return_value = None
        result.scalars.return_value.first.return_value = None
        return result


@asynccontextmanager
async def _fake_read_scope():
    yield _NoActiveSession()


def _patch_resource_resolver(monkeypatch, media: dict, owner_id: str):
    resource = {"id": "res-1", "media_id": media["id"], "creator_id": owner_id}

    async def _resolve(_rid, _uid):
        return resource, media["platform_id"], media

    monkeypatch.setattr(ai_router, "_resolve_resource_to_platform_id", _resolve)
    return resource


def _patch_billing_and_dedup(monkeypatch):
    import app.db.session as dbs

    monkeypatch.setattr(dbs, "read_scope", _fake_read_scope)

    team_spy = AsyncMock(return_value="team-owner")
    monkeypatch.setattr(ai_router, "get_team_id_for_user", team_spy)

    pts = MagicMock()
    pts.ensure_team_quota = AsyncMock()
    pts.check_and_consume = AsyncMock(return_value={"success": True, "points_cost": 5})
    monkeypatch.setattr(ai_router, "PointsService", lambda: pts)
    return team_spy, pts


def _patch_transcript_ready(monkeypatch):
    ai_repo = MagicMock()
    ai_repo.get_transcript = AsyncMock(return_value={"full_text": "hello world"})
    monkeypatch.setattr(ai_router, "get_ai_repository", lambda: ai_repo)


def _patch_dispatch(monkeypatch, dispatched: list, created: list):
    async def _create(**kwargs):
        created.append(kwargs)
        return "task-row-id"

    fake_mgr = MagicMock()
    fake_mgr.create = AsyncMock(side_effect=_create)
    fake_mgr.fail = AsyncMock()

    import app.services.infra.unified_task_manager as utm

    monkeypatch.setattr(utm, "get_task_manager", lambda: fake_mgr)

    async def _start(name, **kwargs):
        dispatched.append({"name": name, **kwargs})

    import app.services.infra.dbos_orchestrator as orch

    monkeypatch.setattr(orch, "start_workflow_routed", AsyncMock(side_effect=_start))

    return fake_mgr


@pytest.mark.asyncio
async def test_summary_dispatch_uses_resource_owner_not_caller(monkeypatch) -> None:
    """Caller (team member) != resource creator. Points were already charged
    to the OWNER's team; the workflow must run as the owner too, or
    load_summary_inputs' creator_id filter finds nothing → charged-then-fail
    (the bug observed in the 2026-08-07 diagnosis)."""
    media = {"id": "111", "platform_id": "pf-1", "title": "T"}
    _patch_resource_resolver(monkeypatch, media, owner_id="owner-uuid")
    _patch_billing_and_dedup(monkeypatch)
    _patch_transcript_ready(monkeypatch)

    dispatched: list = []
    created: list = []
    _patch_dispatch(monkeypatch, dispatched, created)

    res = await ai_router.trigger_summary_by_resource("res-1", _auth(), None)

    assert res["message"] == "Summary generation queued"
    # points_charged is on every exit of this endpoint, success included, so
    # "field absent" never has to be read as "not charged" (the two
    # already-in-progress branches report 0).
    assert res["points_charged"] == 5
    assert len(dispatched) == 1
    kwargs = dispatched[0]["dbos_workflow_kwargs"]
    assert kwargs["user_id"] == "owner-uuid"  # resource creator_id, not caller

    # task_tracking row still belongs to the caller (task center display),
    # per the brief: tracker identity != workflow identity.
    assert created[0]["user_id"] == "caller-uuid"


# ─── dedup race on the unique index (Task 1b) ─────────────────────


def _unique_violation():
    return Exception(
        "duplicate key value violates unique constraint "
        '"idx_task_tracking_active_per_resource_type"'
    )


@pytest.mark.asyncio
async def test_an_active_summary_short_circuits_without_dispatching(
    monkeypatch,
) -> None:
    """The plain dedup path: an active ai_summary row is found by the
    SELECT, so nothing is charged and nothing is dispatched."""
    media = {"id": "111", "platform_id": "pf-1", "title": "T"}
    _patch_resource_resolver(monkeypatch, media, owner_id="owner-uuid")

    class _ActiveSession:
        """Answers "yes" only when the query really asks about ai_summary
        with an active status — so narrowing the predicate turns this red
        instead of silently passing on a canned row."""

        async def execute(self, stmt, *_a, **_k):
            from sqlalchemy.dialects import postgresql

            sql = str(
                stmt.compile(
                    dialect=postgresql.dialect(),
                    compile_kwargs={"literal_binds": True},
                )
            )
            result = MagicMock()
            result.first.return_value = (
                ("wf-existing",)
                if "ai_summary" in sql and "processing" in sql
                else None
            )
            return result

    @asynccontextmanager
    async def _scope():
        yield _ActiveSession()

    import app.db.session as dbs

    monkeypatch.setattr(dbs, "read_scope", _scope)

    consume = AsyncMock()
    pts = MagicMock()
    pts.check_and_consume = consume
    monkeypatch.setattr(ai_router, "PointsService", lambda: pts)

    dispatched: list = []
    created: list = []
    _patch_dispatch(monkeypatch, dispatched, created)

    res = await ai_router.trigger_summary_by_resource("res-1", _auth(), None)

    assert res["message"] == "Summary already in progress"
    assert res["points_charged"] == 0
    consume.assert_not_awaited()
    assert dispatched == []
    assert created == []


@pytest.mark.asyncio
async def test_losing_the_insert_race_reports_in_progress_and_refunds(
    monkeypatch,
) -> None:
    """TOCTOU: another request creates the active row between our SELECT
    and our INSERT. Migration 121's own comment says callers must treat the
    unique violation as "already in progress" — a 500 would tell the user
    their summary failed while it is in fact being generated."""
    media = {"id": "111", "platform_id": "pf-1", "title": "T"}
    _patch_resource_resolver(monkeypatch, media, owner_id="owner-uuid")
    _, pts = _patch_billing_and_dedup(monkeypatch)
    refund = AsyncMock()
    pts.refund_points = refund
    _patch_transcript_ready(monkeypatch)

    dispatched: list = []
    created: list = []
    mgr = _patch_dispatch(monkeypatch, dispatched, created)

    async def _conflict(**_kwargs):
        raise _unique_violation()

    mgr.create = AsyncMock(side_effect=_conflict)

    res = await ai_router.trigger_summary_by_resource("res-1", _auth(), None)

    assert res["message"] == "Summary already in progress"
    assert res["points_charged"] == 0
    assert dispatched == []
    # No task row was ever created, so there is nothing to mark failed.
    mgr.fail.assert_not_awaited()
    refund.assert_awaited_once()
    assert refund.await_args.kwargs["amount"] == 5


@pytest.mark.asyncio
async def test_an_unrelated_dispatch_failure_is_still_a_500(monkeypatch) -> None:
    """Reverse control: the conflict branch must not swallow real failures.
    Reporting a broken dispatch as "already in progress" would be exactly
    the silent no-op this codebase keeps banning."""
    media = {"id": "111", "platform_id": "pf-1", "title": "T"}
    _patch_resource_resolver(monkeypatch, media, owner_id="owner-uuid")
    _, pts = _patch_billing_and_dedup(monkeypatch)
    pts.refund_points = AsyncMock()
    _patch_transcript_ready(monkeypatch)

    dispatched: list = []
    created: list = []
    mgr = _patch_dispatch(monkeypatch, dispatched, created)
    mgr.create = AsyncMock(side_effect=RuntimeError("DBOS is not launched"))

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await ai_router.trigger_summary_by_resource("res-1", _auth(), None)

    assert exc.value.status_code == 500
    assert "Failed to queue summary" in exc.value.detail
