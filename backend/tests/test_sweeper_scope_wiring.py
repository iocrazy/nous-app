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

run_async subtlety (#2, #3)
---------------------------
``run_async`` spins a fresh thread/event-loop; Python's ``ContextVar`` copy is NOT
inherited across a new thread by default (a fresh ``ContextVar.get()`` returns the
default ``None``). The fix is to set the scope INSIDE the inner ``async def _do()``
/ ``_load_resource_ids()`` that ``run_async`` awaits — the scope is set in the same
loop/context where the repo calls run, so the assertion holds regardless of whether
``run_async`` ever copies context (Pass 4 will add that; Pass 3 is independent).

These tests call the real sync ``@DBOS.step`` function, which invokes ``run_async``
in its body; the captured scope MUST be SYSTEM for the wiring to be correct.

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


def test_cleanup_trashed_resources_step_establishes_system_scope(monkeypatch):
    """``cleanup_trashed_resources_step`` establishes SYSTEM scope inside its
    inner ``_do`` coroutine before calling ``svc.cleanup_expired_trash``.

    This calls the REAL sync ``@DBOS.step`` function (which calls ``run_async``
    internally). The scope is set inside the coroutine that ``run_async`` awaits,
    so it lands in the correct event-loop context — independent of whether
    ``run_async`` copies the outer ContextVar (it does not today; Pass 4 will
    fix that). Our captured scope MUST be SYSTEM regardless.

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
        result = cleanup_trashed_resources_step()

    assert result == {"status": "success", "cleaned": 0}
    scope = captured.get("scope")
    assert scope is SYSTEM, (
        f"ambient scope inside _do() was {scope!r}; expected SYSTEM. "
        "system_request_scope wiring regression on cleanup_trashed_resources_step."
    )
    assert current_scope() is None, "scope leaked after cleanup_trashed_resources_step"


def test_cleanup_trashed_resources_step_scope_enforced_flag_on(monkeypatch):
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
        result = cleanup_trashed_resources_step()

    assert result == {"status": "success", "cleaned": 5}
    assert (
        captured.get("scope") is SYSTEM
    ), "SYSTEM scope must hold even with SCOPE_ENFORCE_RESOURCES=True."
    assert current_scope() is None, "scope leaked after step"


@pytest.mark.asyncio
async def test_cleanup_trashed_step_scope_under_running_loop():
    """PINS the inner-``_do`` scope placement against a sync-step-level hoist.

    The two sync tests above call the step from a SYNC function with no running
    loop → ``run_async`` takes the ``asyncio.run(...)`` branch, which COPIES the
    calling context. A scope set even at the sync-step level would therefore still
    propagate and those tests would still pass — they document the rationale but
    don't pin it.

    Calling the sync step DIRECTLY from inside this running ``asyncio`` loop
    (NOT via ``asyncio.to_thread`` — that would land on a loop-less worker thread
    and take the context-copying ``asyncio.run`` branch) forces ``run_async`` onto
    its ``ThreadPoolExecutor`` branch: the worker thread runs ``asyncio.run`` with
    a FRESH context that does NOT inherit the caller's ContextVar. ONLY the inner
    ``_do()`` scope placement survives here — hoisting the scope to the sync-step
    body would capture ``None``. This is the test that actually catches the
    regression the code comments warn against (verified by temporarily hoisting
    the scope and watching this test fail).
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
        # Running loop present on THIS thread → run_async bounces to a
        # ThreadPoolExecutor worker that runs asyncio.run with a fresh context.
        cleanup_trashed_resources_step()

    assert captured.get("scope") is SYSTEM, (
        "scope captured on run_async's ThreadPoolExecutor branch was not SYSTEM — "
        "the scope must be set INSIDE _do(), not at the sync-step level (a "
        "sync-step-level scope is dropped when run_async bounces to a worker thread)."
    )


# ─── 3. cleanup_orphan_storage_step ──────────────────────────────────────────


def test_cleanup_orphan_storage_step_establishes_system_scope(monkeypatch, tmp_path):
    """``cleanup_orphan_storage_step`` establishes SYSTEM scope inside its inner
    ``_load_resource_ids`` coroutine before calling ``db_engine.fetch_all``.

    Note: ``db_engine.fetch_all`` is RAW SQL and currently bypasses the ORM choke
    point — so the scope is a defensive entry-boundary annotation for now (a
    later task A3 will add the raw-SQL backstop). The wiring is still required so
    the entry boundary is consistent once A3 lands.

    Same run_async inner-scope rationale as test #2 applies here.
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
        result = cleanup_orphan_storage_step()

    assert result["status"] == "success"
    scope = captured.get("scope")
    assert scope is SYSTEM, (
        f"ambient scope inside _load_resource_ids() was {scope!r}; expected SYSTEM. "
        "system_request_scope wiring regression on cleanup_orphan_storage_step."
    )
    assert current_scope() is None, "scope leaked after cleanup_orphan_storage_step"


def test_cleanup_orphan_storage_step_scope_reset_on_no_download_path():
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
        result = cleanup_orphan_storage_step()

    assert result == {"status": "skipped", "reason": "download path not configured"}
    assert current_scope() is None, "scope must be None after skipped early return"


@pytest.mark.asyncio
async def test_cleanup_orphan_storage_step_scope_under_running_loop(tmp_path):
    """PINS the inner-``_load_resource_ids`` scope placement against a
    sync-step-level hoist (analogue of the trashed-step running-loop test).

    Calling the sync step DIRECTLY from inside this running loop (NOT via
    ``asyncio.to_thread``) forces ``run_async`` onto its ``ThreadPoolExecutor``
    branch (fresh context, no ContextVar inheritance). Only the scope set INSIDE
    ``_load_resource_ids`` survives — a sync-step-level hoist would capture
    ``None`` here.
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
        # Running loop present on THIS thread → run_async bounces to a
        # ThreadPoolExecutor worker that runs asyncio.run with a fresh context.
        cleanup_orphan_storage_step()

    assert captured.get("scope") is SYSTEM, (
        "scope captured on run_async's ThreadPoolExecutor branch was not SYSTEM — "
        "the scope must be set INSIDE _load_resource_ids(), not at the sync-step "
        "level (a sync-step-level scope is dropped when run_async bounces to a "
        "worker thread)."
    )
