"""Tests for routing a qishui UGC video through the parse download dispatch.

``dispatch_soda_ugc_download_step`` mirrors ``dispatch_soda_download_step`` but
dispatches ``soda_ugc_download_workflow`` with subtitle "video". The async
sync/async bridge + DBOS call are stubbed so no real workflow is started.
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


def test_dispatch_soda_ugc_download_step_routes_to_ugc_workflow(monkeypatch):
    manager = _FakeManager()
    monkeypatch.setattr(parse_mod, "get_task_manager", lambda: manager, raising=False)

    captured: dict = {}

    async def _fake_routed(
        task_type, *, dbos_workflow_callable, dbos_workflow_kwargs, workflow_id
    ):
        captured["task_type"] = task_type
        captured["callable"] = dbos_workflow_callable
        captured["kwargs"] = dbos_workflow_kwargs
        captured["workflow_id"] = workflow_id
        return {"ok": True}

    # start_workflow_routed + get_task_manager are imported inside the fn from
    # their home modules — patch at the source.
    import app.services.infra.dbos_orchestrator as orch
    import app.services.infra.unified_task_manager as utm

    monkeypatch.setattr(orch, "start_workflow_routed", _fake_routed)
    monkeypatch.setattr(utm, "get_task_manager", lambda: manager)

    result = dispatch_soda_ugc_download_step(
        platform_id="UV1",
        user_id="u1",
        media_id="999",
        video_title="My Clip",
        resource_id="res-1",
        user_agent="ua",
        flow_id="flow-1",
    )

    assert result == {"ok": True}
    # task_tracking row pre-created with the "video" subtitle
    assert len(manager.created) == 1
    assert manager.created[0]["subtitle"] == "video"
    assert manager.created[0]["task_type"] == "download"
    # routed to the UGC workflow, not the audio one
    from app.workflows.soda_ugc_download import soda_ugc_download_workflow

    assert captured["task_type"] == "download"
    assert captured["callable"] is soda_ugc_download_workflow
    kw = captured["kwargs"]
    assert kw["platform_id"] == "UV1"
    assert kw["media_id"] == "999"
    assert kw["title"] == "My Clip"
    assert kw["resource_id"] == "res-1"
    assert kw["flow_id"] == "flow-1"
    # no numeric media_type on the soda path
    assert "media_type" not in kw
