"""Shot index endpoints over real HTTP with the module boundaries stubbed:
``POST /ai/analyze/index-shots/{id}``, ``POST /ai/analyze/backfill-shots``,
``GET /resources/{id}/shots``, ``GET /resources/{id}/frame``.

Pinned: the typed refusals (409 ``already_indexed`` / ``provider_no_image``,
422 ``not_a_video``, 404), ``force`` bypassing the already-indexed check,
the dry-run estimate (a parsed ``H:M:S`` duration vs the 60-shot default),
one bad dispatch not sinking the batch, ids as strings on the wire, and the
frame's content type + private cache header."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api import ai_router
from app.core.deps import AuthContext, get_auth
from app.main import app
from app.repositories.video_shots_repository import ShotBackfillRow
from app.services.distribution.cover_frames import CoverFrameError
from app.services.library.shot_index import ShotIndexError
from tests.api.wire_parity import SAMPLE_BIGINT

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000042"
RID = str(SAMPLE_BIGINT)
SPACE = {"id": SAMPLE_BIGINT + 7, "actual_model": "doubao-embedding-vision-251215"}


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=USER, auth_type="jwt")


@pytest.fixture(autouse=True)
def _auth():
    app.dependency_overrides[get_auth] = _fake_auth
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _source():
    return SimpleNamespace(
        resource={"id": SAMPLE_BIGINT, "filename": "clip.mp4"},
        file_path="sb://library/clip.mp4",
        filename="clip.mp4",
    )


@pytest.fixture
def index_stack(monkeypatch):
    monkeypatch.setattr(
        "app.services.distribution.cover_frames.load_source_video",
        AsyncMock(return_value=_source()),
    )
    monkeypatch.setattr(
        "app.services.library.shot_index.resolve_space_and_embedder",
        AsyncMock(return_value=(SPACE, object())),
    )
    covered = AsyncMock(return_value=False)
    monkeypatch.setattr("app.api.resources_shots_router._resource_covered", covered)
    dispatch = AsyncMock(return_value="wf-1")
    monkeypatch.setattr(ai_router, "_dispatch_index_shots", dispatch)
    return SimpleNamespace(covered=covered, dispatch=dispatch)


# ─── index-shots ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_index_shots_dispatches_one_task(client, index_stack):
    r = await client.post(f"/api/v1/ai/analyze/index-shots/{RID}", json={})
    assert r.status_code == 202, r.text
    assert r.json() == {"task_id": "wf-1", "workflow_id": "wf-1", "resource_id": RID}
    kwargs = index_stack.dispatch.await_args.kwargs
    assert kwargs["user_id"] == USER and kwargs["resource_id"] == RID
    assert kwargs["title"] == "clip.mp4" and kwargs["flow_id"] is None


@pytest.mark.asyncio
async def test_index_shots_refuses_already_indexed_unless_forced(client, index_stack):
    index_stack.covered.return_value = True
    r = await client.post(f"/api/v1/ai/analyze/index-shots/{RID}", json={})
    assert r.status_code == 409
    assert r.json()["details"]["code"] == "already_indexed"
    index_stack.dispatch.assert_not_awaited()

    r = await client.post(f"/api/v1/ai/analyze/index-shots/{RID}", json={"force": True})
    assert r.status_code == 202
    index_stack.dispatch.assert_awaited_once()


@pytest.mark.asyncio
async def test_index_shots_typed_refusals(client, index_stack, monkeypatch):
    monkeypatch.setattr(
        "app.services.library.shot_index.resolve_space_and_embedder",
        AsyncMock(side_effect=ShotIndexError("provider_no_image", "text only")),
    )
    r = await client.post(f"/api/v1/ai/analyze/index-shots/{RID}", json={})
    assert r.status_code == 409 and r.json()["details"]["code"] == "provider_no_image"

    monkeypatch.setattr(
        "app.services.distribution.cover_frames.load_source_video",
        AsyncMock(side_effect=CoverFrameError(status_code=400, detail="not a video")),
    )
    r = await client.post(f"/api/v1/ai/analyze/index-shots/{RID}", json={})
    assert r.status_code == 422 and r.json()["details"]["code"] == "not_a_video"

    monkeypatch.setattr(
        "app.services.distribution.cover_frames.load_source_video",
        AsyncMock(side_effect=CoverFrameError(status_code=404, detail="gone")),
    )
    r = await client.post(f"/api/v1/ai/analyze/index-shots/{RID}", json={})
    assert r.status_code == 404


# ─── backfill-shots ──────────────────────────────────────────────


class _VecRepo:
    def __init__(self, rows, total):
        self.rows, self.total = rows, total
        self.calls = []

    async def pending_for_user(self, **kw):
        self.calls.append(kw)
        return self.rows, self.total


@pytest.fixture
def backfill_stack(monkeypatch):
    rows = [
        ShotBackfillRow(SAMPLE_BIGINT + 1, 1, "First", duration="3:12"),
        ShotBackfillRow(
            SAMPLE_BIGINT + 2, 2, "Second", duration=None, reason="stale_algo"
        ),
    ]
    repo = _VecRepo(rows, total=9)
    monkeypatch.setattr(
        "app.services.library.shot_index.resolve_space_and_embedder",
        AsyncMock(return_value=(SPACE, object())),
    )
    monkeypatch.setattr(
        "app.repositories.video_shots_repository.get_video_shot_embeddings_repository",
        lambda: repo,
    )
    manager = SimpleNamespace(create_flow=AsyncMock(return_value="flow-1"))
    monkeypatch.setattr(
        "app.services.infra.unified_task_manager.get_task_manager", lambda: manager
    )
    dispatch = AsyncMock(side_effect=["wf-a", RuntimeError("dbos down")])
    monkeypatch.setattr(ai_router, "_dispatch_index_shots", dispatch)
    return SimpleNamespace(repo=repo, manager=manager, dispatch=dispatch)


@pytest.mark.asyncio
async def test_backfill_dry_run_estimates_and_creates_nothing(client, backfill_stack):
    r = await client.post(
        "/api/v1/ai/analyze/backfill-shots", json={"limit": 5, "dry_run": True}
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["dry_run"] is True and out["space_id"] == str(SPACE["id"])
    assert out["total_pending"] == 9 and out["stale"] == 1
    assert out["candidates"] == [str(SAMPLE_BIGINT + 1), str(SAMPLE_BIGINT + 2)]
    # 3:12 → 43 shots; unknown length → the 60-shot default.
    assert out["estimated_shots"] == 103
    assert out["estimated_tokens"] == 103 * 300
    assert out["dispatched"] == [] and out["parent_task_id"] is None
    backfill_stack.dispatch.assert_not_awaited()
    backfill_stack.manager.create_flow.assert_not_awaited()
    assert backfill_stack.repo.calls[0]["limit"] == 5
    assert backfill_stack.repo.calls[0]["kind"] == "frame"


@pytest.mark.asyncio
async def test_backfill_run_dispatches_under_one_flow(client, backfill_stack):
    r = await client.post("/api/v1/ai/analyze/backfill-shots", json={"limit": 5})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["parent_task_id"] == "flow-1"
    assert out["dispatched"] == ["wf-a"]
    assert out["skipped"] == [
        {"resource_id": str(SAMPLE_BIGINT + 2), "reason": "dispatch_failed"}
    ]
    flow_kwargs = backfill_stack.manager.create_flow.await_args.kwargs
    assert flow_kwargs["name"] == "Index shots · 2 videos"
    assert backfill_stack.dispatch.await_args_list[0].kwargs["flow_id"] == "flow-1"


@pytest.mark.asyncio
async def test_backfill_refuses_without_an_image_embedder(client, monkeypatch):
    monkeypatch.setattr(
        "app.services.library.shot_index.resolve_space_and_embedder",
        AsyncMock(side_effect=ShotIndexError("embedder_unconfigured")),
    )
    r = await client.post("/api/v1/ai/analyze/backfill-shots", json={})
    assert r.status_code == 409
    assert r.json()["details"]["code"] == "embedder_unconfigured"


# ─── GET /resources/{id}/shots ───────────────────────────────────


class _ShotsRepo:
    def __init__(self, index, shots):
        self.index, self.shots = index, shots

    async def get_index(self, rid):
        return self.index

    async def list_shots(self, rid):
        return self.shots


@pytest.fixture
def visible(monkeypatch):
    class _Repo:
        async def get_resource_by_id(self, rid):
            return {"id": SAMPLE_BIGINT}

    monkeypatch.setattr("app.api.resources_shots_router.ResourcesRepository", _Repo)


@pytest.mark.asyncio
async def test_shots_not_indexed_is_a_plain_false(client, visible, monkeypatch):
    monkeypatch.setattr(
        "app.repositories.video_shots_repository.get_video_shots_repository",
        lambda: _ShotsRepo(None, []),
    )
    r = await client.get(f"/api/v1/resources/{RID}/shots")
    assert r.status_code == 200, r.text
    assert r.json() == {
        "resource_id": RID,
        "indexed": False,
        "index": None,
        "shots": [],
    }


@pytest.mark.asyncio
async def test_shots_indexed_shape(client, visible, monkeypatch):
    index = {
        "resource_id": SAMPLE_BIGINT,
        "algo_version": "hist_v1",
        "shot_count": 1,
        "duration_ms": 9000,
        "indexed_at": "2026-09-25T10:00:00+00:00",
    }
    shots = [
        {
            "id": SAMPLE_BIGINT + 3,
            "resource_id": SAMPLE_BIGINT,
            "shot_index": 0,
            "start_ms": 0,
            "end_ms": 9000,
            "rep_frame_ms": 4500,
            "cut_score": None,
        }
    ]
    monkeypatch.setattr(
        "app.repositories.video_shots_repository.get_video_shots_repository",
        lambda: _ShotsRepo(index, shots),
    )
    monkeypatch.setattr(
        "app.services.library.shot_index.resolve_space_and_embedder",
        AsyncMock(return_value=(SPACE, object())),
    )
    monkeypatch.setattr(
        "app.api.resources_shots_router._resource_covered", AsyncMock(return_value=True)
    )
    r = await client.get(f"/api/v1/resources/{RID}/shots")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["indexed"] is True
    assert out["index"] == {
        "algo_version": "hist_v1",
        "shot_count": 1,
        "duration_ms": 9000,
        "indexed_at": "2026-09-25T10:00:00+00:00",
        "space_id": str(SPACE["id"]),
        "covered": True,
        "stale": False,
    }
    assert out["shots"][0]["id"] == str(SAMPLE_BIGINT + 3)  # string on the wire
    assert out["shots"][0]["rep_frame_ms"] == 4500


@pytest.mark.asyncio
async def test_shots_of_an_invisible_resource_is_404(client, monkeypatch):
    class _Repo:
        async def get_resource_by_id(self, rid):
            return None

    monkeypatch.setattr("app.api.resources_shots_router.ResourcesRepository", _Repo)
    r = await client.get(f"/api/v1/resources/{RID}/shots")
    assert r.status_code == 404


# ─── GET /resources/{id}/frame ───────────────────────────────────


@pytest.fixture
def frame_stack(monkeypatch):
    monkeypatch.setattr(
        "app.services.distribution.cover_frames.load_source_video",
        AsyncMock(return_value=_source()),
    )

    @asynccontextmanager
    async def fake_materialize(file_path):
        yield Path("/tmp/clip.mp4")

    monkeypatch.setattr(
        "app.services.library.media_storage.materialize", fake_materialize
    )
    monkeypatch.setattr(
        "app.services.media.render.video_frame_extractor._probe_duration",
        AsyncMock(return_value=9.0),
    )
    extract = AsyncMock(return_value=b"\xff\xd8jpeg")
    monkeypatch.setattr(
        "app.services.media.render.video_frame_extractor.extract_frame_at", extract
    )
    return extract


@pytest.mark.asyncio
async def test_frame_is_cut_on_demand(client, frame_stack):
    r = await client.get(f"/api/v1/resources/{RID}/frame", params={"ms": 4500})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/jpeg"
    assert r.headers["cache-control"] == "private, max-age=86400"
    assert r.content == b"\xff\xd8jpeg"
    assert frame_stack.await_args.kwargs["timestamp_seconds"] == 4.5


@pytest.mark.asyncio
async def test_frame_past_the_end_is_422(client, frame_stack):
    r = await client.get(f"/api/v1/resources/{RID}/frame", params={"ms": 20_000})
    assert r.status_code == 422
    assert r.json()["details"]["code"] == "frame_out_of_range"
    frame_stack.assert_not_awaited()
