"""``/media`` fetch, batch, per-media and soda-download routes: wire parity
after they gained response models (P5).

Each route runs over real HTTP through the real handler and helper code
(``handle_media_fetch_dispatch``, ``dedup_and_dispatch``); only the edges are
stubbed — the task manager, the DBOS dispatch, the douyin parse chain, the
repositories, DNS. The body must equal ``jsonable_encoder`` of the dict the
handler builds (``tests/api/wire_parity.py``).

``POST /media/fetch`` and ``POST /media/fetch/batch`` answer with one of
several shapes, declared as unions; every shape is exercised so none of them
is reshaped into another.

Also pinned: extract-audio uses the download routes' read rule.
"""

from __future__ import annotations

import datetime as dt
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.boundary import URLBlockedError
from app.boundary.types import ValidatedURL
from app.core.cache import module_gate_cache
from app.core.deps import AuthContext, get_auth
from app.main import app
from tests.api.wire_parity import SAMPLE_BIGINT, assert_wire_unchanged

fetch_mod = sys.modules["app.api.media_fetch_router"]
batch_mod = sys.modules["app.api.media_batch_router"]
soda_mod = sys.modules["app.api.media_soda_router"]
helpers_mod = sys.modules["app.api.media_fetch_helpers"]
guard_mod = sys.modules["app.api.media_access_guard"]
media_repo_mod = sys.modules["app.repositories.media_repository"]
tm_mod = sys.modules["app.services.infra.unified_task_manager"]
orch_mod = sys.modules["app.services.infra.dbos_orchestrator"]

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000042"
URL = "https://www.douyin.com/video/7300000000000000001"
INTERNAL = "http://10.0.0.5/admin"


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=USER, auth_type="jwt")


async def _fake_validate(url: str) -> ValidatedURL:
    if "10.0.0.5" in url:
        raise URLBlockedError("blocked")
    return ValidatedURL(url)


@pytest.fixture(autouse=True)
def _wiring(monkeypatch):
    app.dependency_overrides[get_auth] = _fake_auth
    module_gate_cache.clear()
    monkeypatch.setattr(
        "app.services.modules.registry._read_raw",
        AsyncMock(return_value={"enabled": True, "visible": True}),
    )
    monkeypatch.setattr(fetch_mod, "validate_url_async", _fake_validate)
    for mod in (fetch_mod, batch_mod, soda_mod):
        monkeypatch.setattr(mod, "resolve_team_id", AsyncMock(return_value=None))
    for mod in (fetch_mod, batch_mod):
        monkeypatch.setattr(mod, "log_user_action", AsyncMock())
    monkeypatch.setattr(helpers_mod, "log_user_action", AsyncMock())
    monkeypatch.setattr(
        helpers_mod, "resolve_intent_tag_ids", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(batch_mod, "resolve_intent_tag_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        helpers_mod.YtdlpService, "user_has_cookie", AsyncMock(return_value=False)
    )
    yield
    module_gate_cache.clear()
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _task_manager(monkeypatch, *, action: str = "created") -> MagicMock:
    mgr = MagicMock()
    mgr.acquire_or_subscribe = AsyncMock(
        return_value={"action": action, "dedup_key": "dedup-1"}
    )
    mgr.create_flow = AsyncMock(return_value="flow-1")
    mgr.create = AsyncMock(return_value="task-row-1")
    mgr.create_many = AsyncMock()
    monkeypatch.setattr(tm_mod, "get_task_manager", lambda: mgr)
    return mgr


def _start_workflow(monkeypatch) -> AsyncMock:
    start = AsyncMock()
    monkeypatch.setattr(orch_mod, "start_workflow_routed", start)
    return start


def _owned(monkeypatch, owned: dict | None) -> None:
    from app.repositories import resources_repository as res_mod

    monkeypatch.setattr(
        res_mod.ResourcesRepository,
        "get_completed_resource_by_url_and_creator",
        AsyncMock(return_value=owned),
    )


# ── POST /media/fetch: three shapes ──────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_already_owned_wire(client, monkeypatch):
    _task_manager(monkeypatch)
    _owned(monkeypatch, {"id": SAMPLE_BIGINT, "media_id": SAMPLE_BIGINT + 1})
    resp = await client.post("/api/v1/media/fetch", json={"url": URL})
    assert_wire_unchanged(
        resp,
        {
            "success": True,
            "async": False,
            "message": "You already have this in your library",
            "dedup_action": "already_owned",
            "resource_id": str(SAMPLE_BIGINT),
            "media_id": str(SAMPLE_BIGINT + 1),
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["subscribed", "completed"])
async def test_fetch_dedup_wire(client, monkeypatch, action):
    _task_manager(monkeypatch, action=action)
    _owned(monkeypatch, None)
    resp = await client.post("/api/v1/media/fetch", json={"url": URL})
    assert_wire_unchanged(
        resp,
        {
            "success": True,
            "async": True,
            "message": f"Parse already {action}",
            "dedup_action": action,
        },
    )


@pytest.mark.asyncio
async def test_fetch_submitted_wire(client, monkeypatch):
    _task_manager(monkeypatch)
    start = _start_workflow(monkeypatch)
    _owned(monkeypatch, None)
    resp = await client.post("/api/v1/media/fetch", json={"url": URL})
    assert_wire_unchanged(
        resp,
        {
            "success": True,
            "async": True,
            "message": "Parse task submitted",
            "task_id": "task-row-1",
        },
    )
    start.assert_awaited_once()


# ── POST /media/fetch/batch ──────────────────────────────────────────────


def _parsed(platform_id: str) -> dict[str, Any]:
    """What the douyin formatter hands back (a video post)."""
    return {
        "platform_id": platform_id,
        "author": "Author",
        "title": "Title",
        "description": "Desc",
        "like_count": 10,
        "comment_count": 2,
        "share_count": None,
        "favorite_count": 1,
        "media_type": "0",
        "published_at": dt.datetime(2026, 9, 24, 1, 2, 3, tzinfo=dt.timezone.utc),
        "duration": "00:15",
        "video_download_urls": ["https://cdn.example/v.mp4"],
        "cover_urls": ["https://cdn.example/c.jpg"],
    }


@pytest.mark.asyncio
async def test_batch_inline_wire(client, monkeypatch):
    parsed = _parsed("7300000000000000001")
    chain = AsyncMock(side_effect=[({"aweme": 1}, parsed, "abogus"), None])
    monkeypatch.setattr(batch_mod, "fetch_douyin_detail", chain)
    monkeypatch.setattr(batch_mod.MediaService, "process_video", AsyncMock())

    # INTERNAL is outside the domain allowlist (Utils.extract_valid_url).
    urls = [URL, "no link here", INTERNAL, URL + "2"]
    resp = await client.post("/api/v1/media/fetch/batch", json={"urls": urls})

    assert_wire_unchanged(
        resp,
        {
            "success": True,
            "total": 4,
            "submitted": 1,
            "failed": 3,
            "results": [
                {
                    "url": URL,
                    "platform_id": "7300000000000000001",
                    "status": "submitted",
                    "data": {
                        "platform_id": "7300000000000000001",
                        "title": "Title",
                        "description": "Desc",
                        "author": "Author",
                        "media_type": "0",
                        "video_download_urls": ["https://cdn.example/v.mp4"],
                        "cover_urls": ["https://cdn.example/c.jpg"],
                        "like_count": 10,
                        "comment_count": 2,
                        "share_count": None,
                        "favorite_count": 1,
                        "duration": "00:15",
                        "published_at": "2026-09-24T01:02:03+00:00",
                        "image_urls": [],
                        "sec_uid": None,
                        "unique_id": None,
                        "valid_url": URL,
                        "user_id": USER,
                    },
                }
            ],
            "errors": [
                {"url": "no link here", "error": "Cannot extract valid link"},
                {"url": INTERNAL, "error": "Cannot extract valid link"},
                {"url": URL + "2", "error": "Cannot fetch video info"},
            ],
        },
    )
    assert [c.args[0] for c in chain.await_args_list] == [URL, URL + "2"]


@pytest.mark.asyncio
async def test_batch_queued_wire(client, monkeypatch):
    enqueued: list[dict] = []

    def _enqueue(*, user_id, workflow_id, kwargs):
        enqueued.append(kwargs)
        return f"wf-{len(enqueued)}"

    monkeypatch.setattr("app.workflows.parse.enqueue_parse_for_user", _enqueue)
    resp = await client.post(
        "/api/v1/media/fetch/batch",
        json={"urls": [URL, INTERNAL], "use_celery": True},
    )
    assert_wire_unchanged(
        resp,
        {
            "success": True,
            "message": "Batch dispatched to DBOS",
            "task_id": "wf-1",
            "workflow_ids": ["wf-1", "wf-2"],
            "total": 2,
            "use_celery": True,
        },
    )
    assert [k["url"] for k in enqueued] == [URL, INTERNAL]


# ── POST /media/{platform_id}/fetch ──────────────────────────────────────


def _stub_type_fetch(monkeypatch, media: dict) -> None:
    from app.repositories import resources_repository as res_mod

    monkeypatch.setattr(
        media_repo_mod.MediaRepository,
        "get_by_platform_id",
        AsyncMock(return_value=media),
    )
    monkeypatch.setattr(
        res_mod.ResourcesRepository,
        "get_resource_by_media_id_and_creator",
        AsyncMock(return_value={"id": SAMPLE_BIGINT + 7}),
    )


_MEDIA = {
    "id": SAMPLE_BIGINT,
    "platform_id": "p-1",
    "media_type": "0",
    "title": "Clip",
    "original_url": None,
}


@pytest.mark.asyncio
async def test_type_fetch_dispatched_wire(client, monkeypatch):
    _stub_type_fetch(monkeypatch, _MEDIA)
    _task_manager(monkeypatch)
    _start_workflow(monkeypatch)
    monkeypatch.setattr(
        "app.services.media.download_cache.already_in_user_library",
        AsyncMock(return_value=False),
    )
    resp = await client.post(
        "/api/v1/media/p-1/fetch", json={"types": ["video", "cover"]}
    )
    assert_wire_unchanged(
        resp,
        {
            "success": True,
            "message": "Fetch submitted",
            "already_in_library": False,
            "platform_id": "p-1",
            "task_id": "task-row-1",
            "types_submitted": ["video", "cover"],
            "types_skipped": [],
            "types_subscribed": [],
        },
    )


@pytest.mark.asyncio
async def test_type_fetch_already_in_library_wire(client, monkeypatch):
    _stub_type_fetch(monkeypatch, _MEDIA)
    _task_manager(monkeypatch)
    monkeypatch.setattr(
        "app.services.media.download_cache.already_in_user_library",
        AsyncMock(return_value=True),
    )
    resp = await client.post("/api/v1/media/p-1/fetch", json={"types": ["video"]})
    assert_wire_unchanged(
        resp,
        {
            "success": True,
            "message": "You already have this in your library",
            "already_in_library": True,
            "platform_id": "p-1",
            "task_id": None,
            "types_submitted": [],
            "types_skipped": ["video"],
            "types_subscribed": [],
        },
    )


# ── POST /media/{platform_id}/extract-audio ──────────────────────────────


def _stub_extract(monkeypatch, *, allowed: bool) -> AsyncMock:
    from app.repositories import resources_repository as res_mod

    monkeypatch.setattr(
        media_repo_mod.MediaRepository,
        "get_by_platform_id",
        AsyncMock(return_value={**_MEDIA, "download_path": "sb://library/x.mp4"}),
    )
    monkeypatch.setattr(
        res_mod.ResourcesRepository,
        "get_resource_by_media_id_and_creator",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        guard_mod, "caller_can_read_media", AsyncMock(return_value=allowed)
    )
    _task_manager(monkeypatch)
    return _start_workflow(monkeypatch)


@pytest.mark.asyncio
async def test_extract_audio_wire(client, monkeypatch):
    start = _stub_extract(monkeypatch, allowed=True)
    resp = await client.post("/api/v1/media/p-1/extract-audio")
    wf_id = start.await_args.kwargs["workflow_id"]
    assert_wire_unchanged(
        resp,
        {
            "success": True,
            "message": "Audio extraction started",
            "platform_id": "p-1",
            "task_id": wf_id,
        },
    )


@pytest.mark.asyncio
async def test_extract_audio_on_media_the_caller_cannot_read_is_404(
    client, monkeypatch
):
    start = _stub_extract(monkeypatch, allowed=False)
    resp = await client.post("/api/v1/media/p-1/extract-audio")
    assert resp.status_code == 404
    start.assert_not_awaited()


# ── POST /media/soda/playlist/download ───────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("dispatch_ok", [True, False])
async def test_soda_download_wire(client, monkeypatch, dispatch_ok):
    mgr = _task_manager(monkeypatch)
    monkeypatch.setattr(soda_mod, "get_task_manager", lambda: mgr)

    def _enqueue(*, user_id, workflow_id, kwargs):
        if not dispatch_ok:
            raise RuntimeError("queue down")
        return workflow_id

    monkeypatch.setattr(soda_mod, "enqueue_parse_for_user", _enqueue)
    resp = await client.post(
        "/api/v1/media/soda/playlist/download",
        json={"items": [{"id": "t1", "kind": "track"}, {"id": "v1", "kind": "video"}]},
    )
    assert_wire_unchanged(
        resp,
        {
            "success": dispatch_ok,
            "flow_id": "flow-1",
            "submitted": 2 if dispatch_ok else 0,
            "total": 2,
        },
    )


@pytest.mark.asyncio
async def test_soda_download_without_flow_row_wire(client, monkeypatch):
    mgr = _task_manager(monkeypatch)
    mgr.create_flow = AsyncMock(return_value=None)
    monkeypatch.setattr(soda_mod, "get_task_manager", lambda: mgr)
    monkeypatch.setattr(
        soda_mod, "enqueue_parse_for_user", lambda **kw: kw["workflow_id"]
    )
    resp = await client.post(
        "/api/v1/media/soda/playlist/download", json={"track_ids": ["t1"]}
    )
    assert_wire_unchanged(
        resp, {"success": True, "flow_id": None, "submitted": 1, "total": 1}
    )
