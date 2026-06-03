"""Tests for routing a qishui UGC video through the parse download dispatch.

``dispatch_soda_ugc_download_step`` mirrors ``dispatch_soda_download_step`` but
enqueues ``soda_ugc_download_workflow`` (subtitle "video") on the shared,
per-user partitioned ``soda_download_queue``. The task-manager + the queue's
``enqueue`` are stubbed so no real workflow is started.
"""

from __future__ import annotations

from app.workflows import parse as parse_mod
from app.workflows.parse import dispatch_soda_ugc_download_step


class _FakeManager:
    def __init__(self):
        self.created: list[dict] = []

    async def create(self, **kwargs):
        self.created.append(kwargs)
        return kwargs.get("dbos_workflow_id")


def test_dispatch_soda_ugc_download_step_enqueues_ugc_workflow(monkeypatch):
    manager = _FakeManager()

    captured: dict = {}

    def _fake_enqueue(func, *args, **kwargs):
        captured["callable"] = func
        captured["args"] = args
        captured["kwargs"] = kwargs

        class _Handle:
            workflow_id = "fake-wf"

        return _Handle()

    # get_task_manager + the queue are imported inside the fn from their home
    # modules — patch at the source.
    import app.services.infra.unified_task_manager as utm
    import app.workflows.soda_download as soda_dl

    monkeypatch.setattr(utm, "get_task_manager", lambda: manager)
    monkeypatch.setattr(soda_dl.soda_download_queue, "enqueue", _fake_enqueue)

    result = dispatch_soda_ugc_download_step(
        platform_id="UV1",
        user_id="u1",
        media_id="999",
        video_title="My Clip",
        resource_id="res-1",
        user_agent="ua",
        flow_id="flow-1",
    )

    # Returns the queued marker + the deterministic workflow id.
    assert result["queued"] is True
    assert isinstance(result["dbos_workflow_id"], str)

    # task_tracking row pre-created with the "video" subtitle.
    assert len(manager.created) == 1
    assert manager.created[0]["subtitle"] == "video"
    assert manager.created[0]["task_type"] == "download"

    # Enqueued the UGC workflow, not the audio one, with the right args.
    from app.workflows.soda_ugc_download import soda_ugc_download_workflow

    assert captured["callable"] is soda_ugc_download_workflow
    # positional: platform_id, user_id
    assert captured["args"] == ("UV1", "u1")
    kw = captured["kwargs"]
    assert kw["media_id"] == "999"
    assert kw["title"] == "My Clip"
    assert kw["resource_id"] == "res-1"
    assert kw["flow_id"] == "flow-1"
    # no numeric media_type on the soda path
    assert "media_type" not in kw


def test_dispatch_soda_audio_download_step_enqueues_audio_workflow(monkeypatch):
    """The audio step enqueues the audio workflow on the same shared queue."""
    manager = _FakeManager()
    captured: dict = {}

    def _fake_enqueue(func, *args, **kwargs):
        captured["callable"] = func
        captured["args"] = args
        captured["kwargs"] = kwargs

        class _Handle:
            workflow_id = "fake-wf"

        return _Handle()

    import app.services.infra.unified_task_manager as utm
    import app.workflows.soda_download as soda_dl

    monkeypatch.setattr(utm, "get_task_manager", lambda: manager)
    monkeypatch.setattr(soda_dl.soda_download_queue, "enqueue", _fake_enqueue)

    result = parse_mod.dispatch_soda_download_step(
        platform_id="T1",
        user_id="u1",
        media_id="888",
        video_title="My Track",
        resource_id="res-2",
        user_agent="ua",
        flow_id="flow-2",
    )

    assert result["queued"] is True
    assert len(manager.created) == 1
    assert manager.created[0]["subtitle"] == "audio"

    assert captured["callable"] is soda_dl.soda_download_workflow
    assert captured["args"] == ("T1", "u1")
    assert captured["kwargs"]["title"] == "My Track"


def test_soda_download_queue_is_partitioned_with_configured_concurrency():
    """The shared queue is per-user partitioned with the configured cap."""
    from app.workflows.soda_download import (
        SODA_DOWNLOAD_CONCURRENCY,
        soda_download_queue,
    )

    assert soda_download_queue.name == "soda_download"
    assert soda_download_queue.partition_queue is True
    assert soda_download_queue.worker_concurrency == SODA_DOWNLOAD_CONCURRENCY
