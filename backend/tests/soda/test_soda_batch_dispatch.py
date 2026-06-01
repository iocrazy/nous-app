"""Tests for the Soda batch-download pure helpers + endpoint dispatch.

The pure helpers (``build_track_url`` / ``batch_plan``) are asserted directly.
The endpoint is exercised through FastAPI's TestClient with a fake task manager
and a stubbed ``start_workflow_routed`` so no real DBOS workflow is started.
"""

from __future__ import annotations

import hashlib

from app.api.media_soda_router import MAX_BATCH, batch_plan, build_track_url

# --- pure: build_track_url ---------------------------------------------------


def test_build_track_url_exact():
    assert (
        build_track_url("abc")
        == "https://music.douyin.com/qishui/share/track?track_id=abc"
    )


# --- pure: batch_plan --------------------------------------------------------


def _expected_wf_id(url: str, user_id: str, bucket: int) -> str:
    url_hash = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
    return f"parse-{user_id[:8]}-{url_hash}-{bucket}"


def test_batch_plan_builds_one_entry_per_track():
    plan = batch_plan(["1", "2"], flow_id="flow9", user_id="user1234", bucket=5)
    assert len(plan) == 2
    for entry, tid in zip(plan, ["1", "2"]):
        kwargs = entry["kwargs"]
        assert kwargs["flow_id"] == "flow9"
        assert kwargs["platform"] == "qishui"
        assert kwargs["video_bool"] is False
        assert kwargs["cover_bool"] is True
        assert kwargs["user_id"] == "user1234"
        assert kwargs["url"] == build_track_url(tid)
        assert entry["workflow_id"] == _expected_wf_id(
            build_track_url(tid), "user1234", 5
        )


def test_batch_plan_deterministic_workflow_ids():
    a = batch_plan(["7"], flow_id="f", user_id="user1234", bucket=3)
    b = batch_plan(["7"], flow_id="f", user_id="user1234", bucket=3)
    assert a[0]["workflow_id"] == b[0]["workflow_id"]


def test_batch_plan_truncates_over_cap():
    ids = [str(i) for i in range(MAX_BATCH + 50)]
    plan = batch_plan(ids, flow_id="f", user_id="u", bucket=1)
    assert len(plan) == MAX_BATCH


# --- endpoint ----------------------------------------------------------------


class _FakeManager:
    def __init__(self):
        self.created = []
        self.flow_name = None

    async def create_flow(self, *, user_id, name):
        self.flow_name = name
        return "flow-abc"

    async def create(self, **kwargs):
        self.created.append(kwargs)
        return kwargs.get("dbos_workflow_id")


def _make_client(monkeypatch, manager, dispatched):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import media_soda_router
    from app.core.deps import AuthContext, get_auth

    app = FastAPI()
    app.include_router(media_soda_router.router, prefix="/api/v1/media")

    async def _fake_auth():
        return AuthContext(user_id="user-123", auth_type="jwt")

    app.dependency_overrides[get_auth] = _fake_auth

    monkeypatch.setattr(media_soda_router, "get_task_manager", lambda: manager)

    async def _fake_dispatch(task_type, **kwargs):
        dispatched.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(media_soda_router, "start_workflow_routed", _fake_dispatch)
    return TestClient(app)


def test_download_endpoint_dispatches_per_track(monkeypatch):
    manager = _FakeManager()
    dispatched: list = []
    client = _make_client(monkeypatch, manager, dispatched)
    resp = client.post(
        "/api/v1/media/soda/playlist/download",
        json={"track_ids": ["1", "2", "3"], "playlist_title": "My Mix"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["flow_id"] == "flow-abc"
    assert body["submitted"] == 3
    assert body["total"] == 3
    assert manager.flow_name == "My Mix"
    assert len(dispatched) == 3
    # all dispatched under the same flow
    assert {d["dbos_workflow_kwargs"]["flow_id"] for d in dispatched} == {"flow-abc"}
    assert {d["dbos_workflow_kwargs"]["platform"] for d in dispatched} == {"qishui"}


def test_download_endpoint_rejects_empty(monkeypatch):
    manager = _FakeManager()
    client = _make_client(monkeypatch, manager, [])
    resp = client.post(
        "/api/v1/media/soda/playlist/download",
        json={"track_ids": []},
    )
    assert resp.status_code == 422


def test_download_endpoint_absorbs_dispatch_errors(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import media_soda_router
    from app.core.deps import AuthContext, get_auth

    manager = _FakeManager()
    app = FastAPI()
    app.include_router(media_soda_router.router, prefix="/api/v1/media")
    app.dependency_overrides[get_auth] = lambda: AuthContext(
        user_id="user-123", auth_type="jwt"
    )
    monkeypatch.setattr(media_soda_router, "get_task_manager", lambda: manager)

    calls = {"n": 0}

    async def _flaky(task_type, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("dispatch boom")
        return {"ok": True}

    monkeypatch.setattr(media_soda_router, "start_workflow_routed", _flaky)

    client = TestClient(app)
    resp = client.post(
        "/api/v1/media/soda/playlist/download",
        json={"track_ids": ["1", "2", "3"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    # best-effort batch: 2 succeed, 1 absorbed
    assert body["submitted"] == 2
    assert body["total"] == 3
