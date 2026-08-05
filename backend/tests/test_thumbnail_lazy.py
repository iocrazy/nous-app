"""Lazy thumbnail generation (million-files P1).

Covers the cover endpoint's on-demand enqueue (date-bucketed idempotency,
placeholder response, small-image passthrough, media-backed exclusion) and
the backfill sweeper's toggle/batch/step-vs-dispatch discipline.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request

from app.api.resources_crud_router import (
    _cover_placeholder,
    _enqueue_lazy_thumbnail,
    serve_resource_cover,
)


def _request() -> Request:
    """Minimal ASGI Request for handlers routed through serve_stored_file."""
    return Request({"type": "http", "method": "GET", "path": "/", "headers": []})


class _FakeExecuteResult:
    """Stand-in for the awaited ``session.execute(stmt)`` Result on the
    resources-claim read path — only ``.mappings().all()`` is exercised."""

    def __init__(self, rows):
        self._rows = rows or []

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _FakeScopeSession:
    """Stand-in for the ORM AsyncSession. Supports BOTH ``scalar()`` (the
    system_settings toggle read) and ``execute()`` (the resources claim
    query — migrated off raw ``db_engine.fetch_all`` onto a real ORM
    ``select()`` in Phase C task 1) — both flow through the SAME patched
    ``read_scope`` seam."""

    def __init__(self, scalar_value, execute_rows=None, capture=None):
        self._scalar_value = scalar_value
        self._execute_rows = execute_rows
        self._capture = capture

    async def scalar(self, stmt):
        return self._scalar_value

    async def execute(self, stmt):
        if self._capture is not None:
            self._capture.append(stmt)
        return _FakeExecuteResult(self._execute_rows)


def _fake_read_scope(scalar_value, execute_rows=None, capture=None):
    @asynccontextmanager
    async def _read_scope():
        yield _FakeScopeSession(
            scalar_value, execute_rows=execute_rows, capture=capture
        )

    return _read_scope


def _resource(**over):
    base = {
        "id": "324520385049690113",
        "media_id": None,
        "thumbnail_path": None,
        "cover_image_path": None,
        "file_path": "2026/07/06/u1/video.mp4",
        "mime_type": "video/mp4",
        "file_size_bytes": 5_000_000,
    }
    base.update(over)
    return base


def _patch_repo(resource):
    repo = MagicMock()
    repo.get_resource_by_id = AsyncMock(return_value=resource)
    return patch("app.api.resources_crud_router.ResourcesRepository", return_value=repo)


@pytest.mark.asyncio
async def test_cover_miss_enqueues_and_serves_placeholder(tmp_path):
    """Video without a thumbnail → idempotent enqueue + SVG placeholder."""
    res = _resource()
    (tmp_path / "2026/07/06/u1").mkdir(parents=True)
    (tmp_path / res["file_path"]).write_bytes(b"x")

    start = AsyncMock()
    with (
        _patch_repo(res),
        patch("app.core.config.settings.DOWNLOAD_PATH", str(tmp_path)),
        patch("app.services.infra.dbos_orchestrator.start_workflow_routed", new=start),
    ):
        resp = await serve_resource_cover(res["id"], _request())

    assert resp.media_type == "image/svg+xml"
    assert resp.headers["Cache-Control"] == "no-store"
    start.assert_awaited_once()
    kwargs = start.await_args.kwargs
    bucket = datetime.now(timezone.utc).strftime("%Y%m%d")
    assert kwargs["workflow_id"] == f"thumb-lazy-{res['id']}-{bucket}"
    assert kwargs["dbos_workflow_kwargs"]["file_path"] == res["file_path"]


@pytest.mark.asyncio
async def test_small_image_serves_original_without_enqueue(tmp_path):
    """Images ≤512KB ARE their own cover — no workflow spent."""
    res = _resource(mime_type="image/png", file_size_bytes=100_000)
    (tmp_path / "2026/07/06/u1").mkdir(parents=True)
    (tmp_path / res["file_path"]).write_bytes(b"png")

    start = AsyncMock()
    with (
        _patch_repo(res),
        patch("app.core.config.settings.DOWNLOAD_PATH", str(tmp_path)),
        patch("app.services.infra.dbos_orchestrator.start_workflow_routed", new=start),
    ):
        resp = await serve_resource_cover(res["id"], _request())

    # FileResponse (original bytes), and the queue was never touched.
    assert resp.__class__.__name__ == "FileResponse"
    start.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_thumbnail_short_circuits(tmp_path):
    """A generated thumbnail keeps the exact pre-P1 behavior."""
    res = _resource(thumbnail_path="thumbs/t.webp")
    (tmp_path / "thumbs").mkdir(parents=True)
    (tmp_path / "thumbs/t.webp").write_bytes(b"webp")

    start = AsyncMock()
    with (
        _patch_repo(res),
        patch("app.core.config.settings.DOWNLOAD_PATH", str(tmp_path)),
        patch("app.services.infra.dbos_orchestrator.start_workflow_routed", new=start),
    ):
        resp = await serve_resource_cover(res["id"], _request())

    assert resp.__class__.__name__ == "FileResponse"
    start.assert_not_awaited()


@pytest.mark.asyncio
async def test_media_backed_resource_never_lazy_enqueues(tmp_path):
    """parsed_media-backed covers have their own pipeline — excluded."""
    res = _resource(media_id="999", file_path="2026/07/06/u1/video.mp4")

    start = AsyncMock()
    supa = MagicMock()
    supa.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute = AsyncMock(
        return_value=MagicMock(data=None)
    )
    with (
        _patch_repo(res),
        patch("app.core.config.settings.DOWNLOAD_PATH", str(tmp_path)),
        patch(
            "app.db.supabase_client.get_async_supabase_admin",
            new=AsyncMock(return_value=supa),
        ),
        patch("app.services.infra.dbos_orchestrator.start_workflow_routed", new=start),
    ):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await serve_resource_cover(res["id"], _request())

    assert exc.value.status_code == 404
    start.assert_not_awaited()


@pytest.mark.asyncio
async def test_enqueue_failure_never_breaks_cover_serving():
    """A dead queue degrades to 'placeholder again next time', not a 500."""
    with patch(
        "app.services.infra.dbos_orchestrator.start_workflow_routed",
        new=AsyncMock(side_effect=RuntimeError("queue down")),
    ):
        await _enqueue_lazy_thumbnail("1", "a/b.mp4", "video/mp4")  # no raise


def test_placeholder_varies_by_media_class():
    video = _cover_placeholder("video/mp4").body.decode()
    audio = _cover_placeholder("audio/mp3").body.decode()
    assert video != audio
    assert "<svg" in video


# ─── Backfill sweeper ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_backfill_scan_disabled_returns_empty():
    from app.workflows.thumbnail import _backfill_scan_step

    with patch("app.db.session.read_scope", new=_fake_read_scope('{"enabled": false}')):
        assert await _backfill_scan_step.__wrapped__() == []


@pytest.mark.asyncio
async def test_backfill_scan_enabled_claims_batch():
    from app.workflows.thumbnail import _backfill_scan_step

    rows = [{"id": 1, "file_path": "a.mp4", "mime_type": "video/mp4"}]
    captured_stmts: list = []
    with patch(
        "app.db.session.read_scope",
        new=_fake_read_scope(
            {"enabled": True, "batch": 10}, execute_rows=rows, capture=captured_stmts
        ),
    ):
        items = await _backfill_scan_step.__wrapped__()

    assert items == [
        {"resource_id": "1", "file_path": "a.mp4", "mime_type": "video/mp4"}
    ]
    # The configured batch value reaches the compiled SELECT's LIMIT param.
    assert len(captured_stmts) == 1
    assert 10 in dict(captured_stmts[0].compile().params).values()


def test_backfill_workflow_registered_in_dispatch_bundle():
    """#1055 lesson: a workflow the router never imports must still reach the
    worker's registration bundle, or it queues forever."""
    import app.workflows._dispatch_bundle as bundle

    assert hasattr(bundle, "thumbnail_backfill_workflow")
