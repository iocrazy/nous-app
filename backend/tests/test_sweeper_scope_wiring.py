"""Pin the ambient SYSTEM-``Scope`` WIRING on the three sweeper / cleanup paths
that do cross-user resource access (A2 pass 3).

A2 pass 3 wrapped the resource-touching calls in:
  - ``sweep_temp_resources``       → ``async with system_request_scope("temp-resource-sweep")``
  - ``cleanup_trashed_resources_step._do`` → ``async with system_request_scope("cleanup-trashed-resources")``
  - ``cleanup_orphan_storage_step._load_resource_ids`` → ``async with system_request_scope("cleanup-orphan-storage")``

so that flipping ``SCOPE_ENFORCE_RESOURCES`` later finds an ambient SYSTEM scope
at every cross-user resource read/write (no ``UnscopedQueryError``). It is INERT
today — with the flag off the choke point ignores ``_scope`` for ``resources`` — so
a plain response-shape test cannot tell "wired" from "not wired".

These tests assert the AMBIENT SCOPE IS ``SYSTEM`` while the resource work runs:
they monkeypatch the repo / service / db method each path calls and capture
``current_scope()`` from inside it, then assert it is the ``SYSTEM`` sentinel and
that ``current_scope()`` is ``None`` after the call (no scope leak).

async-native (ORM 2.0 §2.4b)
----------------------------
``cleanup_trashed_resources_step`` and ``cleanup_orphan_storage_step`` are now
``async def @DBOS.step()`` — the old ``run_async`` fresh-thread/event-loop bridge
is gone, so the cross-thread ContextVar subtlety no longer applies. The
``system_request_scope`` is entered directly inside the async step body, in the
same loop as the DB call. These tests now ``await`` the real async step and assert
the captured scope is SYSTEM at the DB call and ``None`` after (no leak).

SYSTEM scope is correct under BOTH flag states:
  - FLAG OFF: choke point is inert → system_request_scope is a no-op guard
    (just sets the ContextVar + logs one INFO line). Sweepers work exactly as before.
  - FLAG ON: SYSTEM scope bypasses injection and fail-closed raise entirely.
    Cross-user sweepers must use SYSTEM, never a per-user Scope.

Pure-process: no DBOS runtime, no Supabase, no network.
NOT marked ``integration`` so it runs in the unit suite.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import app.db.scope as scope_mod
from app.db.scope import SYSTEM, current_scope

# ─── 1. sweep_temp_resources ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sweep_temp_resources_establishes_system_scope(monkeypatch):
    """``sweep_temp_resources`` wraps its repo access in SYSTEM scope.

    We stub ``_iter_scopes`` to yield one scope and patch ``_sweep_scope``
    with a fake that captures ``current_scope()`` while it runs. The ambient
    scope must be ``SYSTEM`` during the call and ``None`` after.

    SYSTEM scope is correct under both flag states: with the flag off the choke
    point is inert (no-op); with the flag on SYSTEM bypasses enforcement
    (cross-user sweep is deliberate).
    """
    from app.workflows import temp_resource_sweeper as m

    captured: dict[str, object] = {}

    async def _fake_iter():
        yield ("personal", "u1")

    async def _fake_sweep(scope_type, scope_id):
        captured["scope"] = current_scope()
        return 0

    monkeypatch.setattr(m, "_iter_scopes", _fake_iter)
    monkeypatch.setattr(m, "_sweep_scope", _fake_sweep)

    result = await m.sweep_temp_resources()

    assert result["scopes_swept"] == 1
    scope = captured.get("scope")
    assert scope is SYSTEM, (
        f"ambient scope was {scope!r} during sweep_temp_resources, expected SYSTEM. "
        "system_request_scope wiring regression."
    )
    assert current_scope() is None, "ambient scope leaked after sweep_temp_resources"


@pytest.mark.asyncio
async def test_sweep_temp_resources_system_scope_reaches_repo(monkeypatch):
    """SYSTEM scope is active at the actual repo call level inside ``_sweep_scope``.

    This drives the real ``_sweep_scope`` (not stubbed) with a patched repo,
    verifying the scope is SYSTEM when ``soft_delete_resource`` is called — the
    resource-touching write the flag would enforce.

    SYSTEM scope must hold under both flag states.
    """
    from datetime import datetime, timedelta, timezone

    from app.workflows import temp_resource_sweeper as m

    captured: dict[str, object] = {}

    fake_repo = type("R", (), {})()
    fake_repo.get_folders = AsyncMock(return_value=[{"id": "f-temp", "name": "temp"}])
    fake_repo.list_resources_in_folder = AsyncMock(
        return_value=[
            {
                "id": "r-old",
                "created_at": (
                    datetime.now(timezone.utc) - timedelta(days=45)
                ).isoformat(),
            }
        ]
    )

    async def _capture_soft_delete(rid):
        captured["scope"] = current_scope()

    fake_repo.soft_delete_resource = _capture_soft_delete

    async def _fake_iter():
        yield ("personal", "u1")

    monkeypatch.setattr(m, "_iter_scopes", _fake_iter)
    monkeypatch.setattr(m, "_build_repo", lambda: fake_repo)
    monkeypatch.setattr(m, "get_chat_temp_ttl_days", AsyncMock(return_value=30))

    await m.sweep_temp_resources()

    scope = captured.get("scope")
    assert scope is SYSTEM, (
        f"scope at soft_delete_resource was {scope!r}; expected SYSTEM. "
        "system_request_scope not reaching repo call in sweep_temp_resources."
    )
    assert current_scope() is None, "scope leaked after sweep_temp_resources"


# ─── 2. cleanup_trashed_resources_step ───────────────────────────────────────


@pytest.mark.asyncio
async def test_cleanup_trashed_resources_step_establishes_system_scope(monkeypatch):
    """``cleanup_trashed_resources_step`` (async) establishes SYSTEM scope before
    calling ``svc.cleanup_expired_trash``.

    Awaits the real async ``@DBOS.step``; the captured scope MUST be SYSTEM.
    SYSTEM scope is correct under both flag states.
    """
    from app.workflows.scheduled_cleanup import cleanup_trashed_resources_step

    captured: dict[str, object] = {}

    class _FakeSvc:
        async def cleanup_expired_trash(self, older_than_days: int) -> int:
            captured["scope"] = current_scope()
            return 0

    with patch(
        "app.services.library.resources_service.ResourcesService",
        return_value=_FakeSvc(),
    ):
        result = await cleanup_trashed_resources_step()

    assert result == {"status": "success", "cleaned": 0}
    scope = captured.get("scope")
    assert scope is SYSTEM, (
        f"ambient scope inside _do() was {scope!r}; expected SYSTEM. "
        "system_request_scope wiring regression on cleanup_trashed_resources_step."
    )
    assert current_scope() is None, "scope leaked after cleanup_trashed_resources_step"


@pytest.mark.asyncio
async def test_cleanup_trashed_resources_step_scope_enforced_flag_on(monkeypatch):
    """Belt-and-suspenders: even with SCOPE_ENFORCE_RESOURCES=True the sweep
    succeeds (no UnscopedQueryError) because SYSTEM bypasses enforcement.

    SYSTEM scope is explicitly exempt from the fail-closed raise — this test
    documents that contract so a future refactor cannot accidentally swap SYSTEM
    for a user scope on this path.
    """
    from app.workflows.scheduled_cleanup import cleanup_trashed_resources_step

    captured: dict[str, object] = {}

    class _FakeSvc:
        async def cleanup_expired_trash(self, older_than_days: int) -> int:
            captured["scope"] = current_scope()
            return 5

    with (
        patch(
            "app.services.library.resources_service.ResourcesService",
            return_value=_FakeSvc(),
        ),
        patch.object(
            scope_mod,
            "_ENFORCEMENT_OVERRIDES",
            {"resources": lambda: True},
        ),
    ):
        result = await cleanup_trashed_resources_step()

    assert result == {"status": "success", "cleaned": 5}
    assert (
        captured.get("scope") is SYSTEM
    ), "SYSTEM scope must hold even with SCOPE_ENFORCE_RESOURCES=True."
    assert current_scope() is None, "scope leaked after step"


@pytest.mark.asyncio
async def test_cleanup_trashed_step_scope_inside_async_step():
    """PINS that the SYSTEM scope is established INSIDE the async step body, in the
    same loop as the DB call (not hoisted to a caller that the DB call can't see).

    Post §2.4b the step is ``async def`` and enters ``system_request_scope``
    directly around ``svc.cleanup_expired_trash`` — so the scope must be SYSTEM at
    the moment the service is invoked. A refactor that drops the ``async with`` or
    moves it off the DB-call path would capture a non-SYSTEM scope here.
    """
    from app.workflows.scheduled_cleanup import cleanup_trashed_resources_step

    captured: dict[str, object] = {}

    class _FakeSvc:
        async def cleanup_expired_trash(self, older_than_days: int) -> int:
            captured["scope"] = current_scope()
            return 0

    with patch(
        "app.services.library.resources_service.ResourcesService",
        return_value=_FakeSvc(),
    ):
        await cleanup_trashed_resources_step()

    assert captured.get("scope") is SYSTEM, (
        "scope at cleanup_expired_trash was not SYSTEM — the async step must enter "
        "system_request_scope around the DB call."
    )
    assert current_scope() is None, "scope leaked after cleanup_trashed_resources_step"


# ─── 3. cleanup_orphan_storage_step ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_cleanup_orphan_storage_step_establishes_system_scope(
    monkeypatch, tmp_path
):
    """``cleanup_orphan_storage_step`` (async) establishes SYSTEM scope inside its
    inner ``_load_resource_ids`` coroutine before calling ``db_engine.fetch_all``.

    Note: ``db_engine.fetch_all`` is RAW SQL and currently bypasses the ORM choke
    point — so the scope is a defensive entry-boundary annotation for now (a
    later task A3 will add the raw-SQL backstop). The wiring is still required so
    the entry boundary is consistent once A3 lands.

    SYSTEM scope is correct under both flag states.
    """
    from app.workflows.scheduled_cleanup import cleanup_orphan_storage_step

    captured: dict[str, object] = {}

    async def _fake_fetch_all(sql, *args, **kwargs):
        captured["scope"] = current_scope()
        return []

    with (
        patch(
            "app.core.utils.Utils.get_download_base_path",
            return_value=str(tmp_path),
        ),
        patch(
            "app.db.engine.fetch_all",
            side_effect=_fake_fetch_all,
        ),
    ):
        result = await cleanup_orphan_storage_step()

    assert result["status"] == "success"
    scope = captured.get("scope")
    assert scope is SYSTEM, (
        f"ambient scope inside _load_resource_ids() was {scope!r}; expected SYSTEM. "
        "system_request_scope wiring regression on cleanup_orphan_storage_step."
    )
    assert current_scope() is None, "scope leaked after cleanup_orphan_storage_step"


@pytest.mark.asyncio
async def test_cleanup_orphan_storage_step_scope_reset_on_no_download_path():
    """When the download path is not configured the step returns early (skipped).

    The scope must not be left set in this early-exit path — ``current_scope()``
    must remain ``None`` after the step returns (regression guard for any future
    refactor that moves the scope block outside the DB-load guard).
    """
    from app.workflows.scheduled_cleanup import cleanup_orphan_storage_step

    with patch(
        "app.core.utils.Utils.get_download_base_path",
        side_effect=ValueError("not configured"),
    ):
        result = await cleanup_orphan_storage_step()

    assert result == {"status": "skipped", "reason": "download path not configured"}
    assert current_scope() is None, "scope must be None after skipped early return"


@pytest.mark.asyncio
async def test_cleanup_orphan_storage_step_scope_inside_async_step(tmp_path):
    """PINS that SYSTEM scope wraps the ``db_engine.fetch_all`` read inside the
    async ``_load_resource_ids`` coroutine (same loop as the DB call).

    Post §2.4b there is no ``run_async`` thread-hop; the ``async with
    system_request_scope`` sits directly around the read. A refactor that moves it
    off the read path would capture a non-SYSTEM scope here.
    """
    from app.workflows.scheduled_cleanup import cleanup_orphan_storage_step

    captured: dict[str, object] = {}

    async def _fake_fetch_all(sql, *args, **kwargs):
        captured["scope"] = current_scope()
        return []

    with (
        patch(
            "app.core.utils.Utils.get_download_base_path",
            return_value=str(tmp_path),
        ),
        patch(
            "app.db.engine.fetch_all",
            side_effect=_fake_fetch_all,
        ),
    ):
        await cleanup_orphan_storage_step()

    assert captured.get("scope") is SYSTEM, (
        "scope at db_engine.fetch_all was not SYSTEM — the async step must enter "
        "system_request_scope around the resource-ids read."
    )
    assert current_scope() is None, "scope leaked after cleanup_orphan_storage_step"
