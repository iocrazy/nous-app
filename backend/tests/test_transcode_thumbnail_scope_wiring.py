"""Pin the ambient tenant-``Scope`` WIRING on the transcode + thumbnail DBOS
workflows (A2 pass 5 — the final cover-all-callers wiring pass for ``resources``).

A2 pass 5 wrapped each workflow BODY (the step-call sequence) in the ambient
scope boundary so that flipping ``SCOPE_ENFORCE_RESOURCES`` later finds a scope
already established at every resources-touching step:

  * ``transcode_workflow`` — CONDITIONAL on its frozen ``user_id`` input:
        user_id truthy → ``request_scope(Scope(user_id=user_id))`` (USER)
        user_id falsy  → ``system_request_scope(reason="system-transcode")``
    Its steps (``resolve_resource_title_step`` / ``transcode_to_hls_step`` /
    ``log_transcode_outcome_step``) touch ``resources`` / ``resource_versions``.
  * ``thumbnail_workflow`` — always ``system_request_scope("system-thumbnail")``
    (no user_id; owner-agnostic derived-asset write dispatched out-of-band).

Both bodies ``await`` their ``@DBOS.step`` functions DIRECTLY (native async, same
task / ContextVar — no run_async thread hop), so wrapping the body propagates the
scope into every awaited step. The wiring is INERT today (flag off → the choke
point ignores ``_scope`` for ``resources``), so a response-shape test cannot tell
"wired" from "not wired". These tests assert the AMBIENT SCOPE itself instead:
they patch the step (transcode) / the repo write the step calls (thumbnail) to
record ``current_scope()`` AT THE POINT THE RESOURCE WORK RUNS, then assert the
recorded scope matches the wrap and that ``_scope`` resets to ``None`` after.

Removing the ``async with`` wrap from either workflow body would make the recorded
scope ``None`` (no boundary establishes it anywhere else on this path) → every
``isinstance``/``is SYSTEM`` assertion below fails. So these tests genuinely
exercise the wrap, not just the response shape.

Pure-process: no DBOS runtime, no Supabase, no network. The ``@DBOS.workflow()`` /
``@DBOS.step()`` decorators preserve the wrapped coroutine, so we drive the
workflow coroutine directly with its inner steps patched (same approach as
``test_download_workflows_scope_wiring.py``). NOT marked ``integration`` so it
runs in the unit suite — the wiring it guards is inert, so a regression would
otherwise pass silently until the flag flip.
"""

from __future__ import annotations

import inspect
from unittest.mock import patch

from app.db.scope import SYSTEM, Scope, current_scope

_USER = "u-xyz"

# ``@DBOS.workflow()`` refuses to run before ``DBOS.launch()`` (raises
# DBOSException). It is a transparent ``@wraps`` wrapper though, so
# ``inspect.unwrap`` recovers the underlying coroutine — the real workflow body
# (with OUR ``async with`` scope wrap) — and we drive THAT in-process. The body
# calls its steps by module-global name, so the ``patch.object(tc, ...)`` /
# ``patch(...ThumbnailService.generate_thumbnail)`` stubs below still intercept.


# ─── 1. transcode_workflow — user_id present → USER scope ─────────────────


async def test_transcode_workflow_user_id_present_establishes_user_scope():
    """``transcode_workflow(..., user_id="u-xyz")`` wraps its body in
    ``request_scope(Scope(user_id="u-xyz"))``. We patch the steps to record
    ``current_scope()`` at the point each runs and assert it carries the user."""
    from app.workflows import transcode as tc

    captured: dict[str, object] = {}

    async def _fake_resolve(_rid, _vid):
        captured["title_scope"] = current_scope()
        return "title"

    async def _fake_transcode(_rid, _vid, _uid):
        # The heavy ffmpeg work — stubbed so it doesn't actually transcode.
        captured["transcode_scope"] = current_scope()
        return {"status": "completed", "hls_path": "/x.m3u8"}

    async def _fake_log(**_kw):
        captured["log_scope"] = current_scope()

    with (
        patch.object(tc, "resolve_resource_title_step", _fake_resolve),
        patch.object(tc, "transcode_to_hls_step", _fake_transcode),
        patch.object(tc, "log_transcode_outcome_step", _fake_log),
    ):
        body = inspect.unwrap(tc.transcode_workflow)
        outcome = await body("r1", "v1", user_id=_USER)

    assert outcome == {"status": "completed", "hls_path": "/x.m3u8"}
    # The scope at the resource-touching step is a USER Scope for "u-xyz".
    scope = captured.get("transcode_scope")
    assert scope is not SYSTEM, "expected USER scope, got SYSTEM — wrong branch"
    assert isinstance(scope, Scope), (
        f"ambient scope not set during transcode step: {scope!r}. "
        "request_scope wiring regression on transcode_workflow (user branch)."
    )
    assert scope.user_id == _USER, f"scope user_id mismatch: {scope.user_id!r}"
    assert scope.team_ids == frozenset()
    assert scope.project_ids == frozenset()
    # Every step ran under the same USER scope.
    assert captured["title_scope"] is scope
    assert captured["log_scope"] is scope
    assert current_scope() is None, "ambient scope leaked after the workflow"


# ─── 2. transcode_workflow — user_id None → SYSTEM scope ──────────────────


async def test_transcode_workflow_user_id_none_establishes_system_scope():
    """``transcode_workflow(..., user_id=None)`` (batch/admin) wraps its body in
    ``system_request_scope("system-transcode")`` → SYSTEM at every step."""
    from app.workflows import transcode as tc

    captured: dict[str, object] = {}

    async def _fake_resolve(_rid, _vid):
        captured["title_scope"] = current_scope()
        return "title"

    async def _fake_transcode(_rid, _vid, _uid):
        captured["transcode_scope"] = current_scope()
        return {"status": "completed", "hls_path": "/x.m3u8"}

    async def _fake_log(**_kw):
        captured["log_scope"] = current_scope()

    with (
        patch.object(tc, "resolve_resource_title_step", _fake_resolve),
        patch.object(tc, "transcode_to_hls_step", _fake_transcode),
        patch.object(tc, "log_transcode_outcome_step", _fake_log),
    ):
        body = inspect.unwrap(tc.transcode_workflow)
        await body("r1", "v1", user_id=None)

    assert captured.get("transcode_scope") is SYSTEM, (
        "expected SYSTEM scope for None user_id, got "
        f"{captured.get('transcode_scope')!r} — system-transcode wrap regression."
    )
    assert captured["title_scope"] is SYSTEM
    assert captured["log_scope"] is SYSTEM
    assert current_scope() is None, "ambient scope leaked after the workflow"


# ─── 3. thumbnail_workflow → SYSTEM scope ─────────────────────────────────


async def test_thumbnail_workflow_establishes_system_scope():
    """``thumbnail_workflow`` wraps its body in
    ``system_request_scope("system-thumbnail")``. We patch the real
    ``ThumbnailService.generate_thumbnail`` (which would write
    ``resources.thumbnail_path``) to record ``current_scope()`` and assert it is
    SYSTEM — exercising the actual ``generate_thumbnail_step`` under the wrap."""
    from app.workflows import thumbnail as tn

    captured: dict[str, object] = {}

    async def _fake_generate(self, *, resource_id, file_path, mime_type):
        captured["scope"] = current_scope()
        return None

    with patch(
        "app.services.media.render.thumbnail_service.ThumbnailService."
        "generate_thumbnail",
        _fake_generate,
    ):
        body = inspect.unwrap(tn.thumbnail_workflow)
        result = await body("r1", "/f.mp4", "video/mp4")

    assert result == {"resource_id": "r1", "success": True}
    assert captured.get("scope") is SYSTEM, (
        "expected SYSTEM scope during thumbnail resources write, got "
        f"{captured.get('scope')!r} — system-thumbnail wrap regression."
    )
    assert current_scope() is None, "ambient scope leaked after the workflow"
