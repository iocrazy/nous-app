# backend/tests/api/test_ai_router_summary_identity.py

"""Regression test for Bug B-1 (2026-08-07 diagnosis): the summary
dispatch must run the workflow as the resource OWNER, not the calling
user.

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
    """Dedup probe's ORM session stand-in — no in-flight summary."""

    async def execute(self, *_a, **_k):
        result = MagicMock()
        result.first.return_value = None
        return result


@asynccontextmanager
async def _fake_read_scope():
    yield _NoActiveSession()


def _patch_resource_resolver(monkeypatch, media: dict, owner_id: str):
    resource = {"id": "res-1", "media_id": media["id"], "creator_id": owner_id}

    async def _resolve(_rid):
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
    assert len(dispatched) == 1
    kwargs = dispatched[0]["dbos_workflow_kwargs"]
    assert kwargs["user_id"] == "owner-uuid"  # resource creator_id, not caller

    # task_tracking row still belongs to the caller (task center display),
    # per the brief: tracker identity != workflow identity.
    assert created[0]["user_id"] == "caller-uuid"
