# backend/tests/tasks/test_download_helpers_chain_summary.py

"""chain_summary_for_tags — dispatch identity + no-resource visibility (Bug B-3).

Transcription can be triggered by a teammate on a shared resource (the
inbound ``user_id`` is the transcription CALLER, not necessarily the
resource owner). The pre-fix code resolved the resource via
``get_resource_by_media_id_and_creator(media_id, user_id)`` — a creator-
scoped lookup — so a teammate's trigger found no resource and the chain
silently no-op'd (never even reaching the Summary-tag check). Two bugs:

  1. The summary workflow, when it DOES dispatch, must run as the
     resource CREATOR — the workflow's ``creator_id`` filter and the
     points ledger both key off that identity (see the analogous
     ai_router.py fix, 2026-08-07, commit 3c45716).
  2. "No resource found" must not be fully silent — it should be
     discoverable in logs (info level), not swallowed at debug/no-op.

These tests patch the module-level boundaries chain_summary_for_tags
imports (MediaRepository, ResourcesRepository, read_resource_tag_names,
get_task_manager, start_workflow_routed) and drive the coroutine
directly — no DB, no DBOS runtime.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.repositories.media_repository import MediaRepository
from app.repositories.resources_repository import ResourcesRepository
from app.tasks import download_helpers as dh


class _NoFlowIdRow:
    """Stands in for the ORM AsyncSession used by chain_summary_for_tags'
    ``_read_flow_id`` inner helper — its execute().first() returns None
    (no prior transcript task to inherit a flow_id from)."""

    async def execute(self, *_a, **_k):
        result = MagicMock()
        result.first.return_value = None
        return result


@asynccontextmanager
async def _fake_read_scope():
    yield _NoFlowIdRow()


@pytest.mark.asyncio
async def test_chain_dispatches_as_resource_creator():
    """Transcription can be triggered by a teammate; the summary chain must
    still run as the resource CREATOR (whose creator_id filter the workflow
    applies), not the transcription caller."""

    async def _fake_get_by_id(self, media_id):
        return {"id": media_id, "platform_id": "p-1", "title": "Clip"}

    async def _fake_get_resource(self, media_id):
        return {"id": "res-1", "creator_id": "owner-1"}

    async def _fake_tag_names(_resource_id):
        return {"Summary"}

    fake_mgr = AsyncMock()
    swr = AsyncMock()

    with (
        patch.object(MediaRepository, "get_by_id", _fake_get_by_id),
        patch.object(
            ResourcesRepository, "get_resource_by_media_id", _fake_get_resource
        ),
        patch.object(dh, "read_resource_tag_names", _fake_tag_names),
        patch("app.db.session.read_scope", _fake_read_scope),
        patch(
            "app.services.infra.unified_task_manager.get_task_manager",
            lambda: fake_mgr,
        ),
        patch("app.services.infra.dbos_orchestrator.start_workflow_routed", swr),
    ):
        # Transcription was triggered by teammate "caller-1" on owner-1's
        # shared resource.
        await dh.chain_summary_for_tags(12345, "caller-1")

    swr.assert_awaited_once()
    assert swr.await_args.kwargs["dbos_workflow_kwargs"]["user_id"] == "owner-1"
    # task_tracking row still attributes to the actual caller (who
    # triggered it) — only the workflow dispatch identity changes.
    fake_mgr.create.assert_awaited_once()
    assert fake_mgr.create.await_args.kwargs["user_id"] == "caller-1"


@pytest.mark.asyncio
async def test_chain_dispatches_as_resource_creator_when_enforced(monkeypatch):
    """F1 (2026-08-08 终审 Critical): with SCOPE_ENFORCE_RESOURCES ON
    (production's actual setting), the resource lookup must run under
    SYSTEM scope, not the ambient CALLER scope that
    ``ai_transcription_workflow`` opens around this call
    (``async with request_scope(Scope(user_id=user_id))`` at
    ai_transcription.py:556). Left unwrapped, the choke point would inject
    ``creator_id == caller`` on the SELECT and reinstate the exact
    creator-scoped filter Task 4 removed — a teammate-triggered
    transcription would silently fail to find the resource and never
    dispatch summary at all.

    Behavioral pin: caller ("caller-1") != resource creator ("owner-1"),
    enforcement ON, and the resource is still found and dispatched with
    owner identity — proving the fix's system-scope wrap is what makes
    this possible (not merely that the choke point is inert in tests)."""
    import app.db.scope as scope_module
    from app.db.scope import SYSTEM, Scope, current_scope, request_scope

    monkeypatch.setattr(scope_module.settings, "SCOPE_ENFORCE_RESOURCES", True)

    observed_scope: dict[str, object] = {}

    async def _fake_get_by_id(self, media_id):
        return {"id": media_id, "platform_id": "p-1", "title": "Clip"}

    async def _fake_get_resource(self, media_id):
        # Records the scope THIS particular read observes — the choke
        # point itself isn't exercised (the repo method is replaced), but
        # this proves chain_summary_for_tags' own wrap is what's active at
        # the call site, independent of whatever's ambient outside it.
        observed_scope["scope"] = current_scope()
        return {"id": "res-1", "creator_id": "owner-1"}

    async def _fake_tag_names(_resource_id):
        return {"Summary"}

    fake_mgr = AsyncMock()
    swr = AsyncMock()

    with (
        patch.object(MediaRepository, "get_by_id", _fake_get_by_id),
        patch.object(
            ResourcesRepository, "get_resource_by_media_id", _fake_get_resource
        ),
        patch.object(dh, "read_resource_tag_names", _fake_tag_names),
        patch("app.db.session.read_scope", _fake_read_scope),
        patch(
            "app.services.infra.unified_task_manager.get_task_manager",
            lambda: fake_mgr,
        ),
        patch("app.services.infra.dbos_orchestrator.start_workflow_routed", swr),
    ):
        # Ambient CALLER scope, exactly as ai_transcription_workflow sets it
        # around this call in production.
        async with request_scope(Scope(user_id="caller-1")):
            await dh.chain_summary_for_tags(12345, "caller-1")

    assert observed_scope.get("scope") is SYSTEM
    swr.assert_awaited_once()
    assert swr.await_args.kwargs["dbos_workflow_kwargs"]["user_id"] == "owner-1"
    fake_mgr.create.assert_awaited_once()
    assert fake_mgr.create.await_args.kwargs["user_id"] == "caller-1"


@pytest.mark.asyncio
async def test_chain_skips_when_resource_missing():
    """No resource for the parsed_media → the chain must not dispatch
    anything. (The "not fully silent" half of the fix is an info-level
    loguru call, which — like the rest of this module's logging — has no
    caplog hook; see tests/boundary/test_b9g_audit.py for the same
    convention. Verified by reading the diff instead.)"""

    async def _fake_get_by_id(self, media_id):
        return {"id": media_id, "platform_id": "p-1", "title": "Clip"}

    async def _fake_get_resource(self, media_id):
        return None

    swr = AsyncMock()

    with (
        patch.object(MediaRepository, "get_by_id", _fake_get_by_id),
        patch.object(
            ResourcesRepository, "get_resource_by_media_id", _fake_get_resource
        ),
        patch("app.services.infra.dbos_orchestrator.start_workflow_routed", swr),
    ):
        await dh.chain_summary_for_tags(12345, "caller-1")

    swr.assert_not_awaited()
