"""_fork must pass the pinned application_version on BOTH the client and the
in-process path. DBOS fork_workflow inserts application_version verbatim (no
fallback to the live version), so a NULL leaves the forked row un-dequeuable by
a version-pinned worker (orphan → 'lost')."""

from __future__ import annotations

import importlib

import app.services.infra.dbos_orchestrator as o

wr = importlib.import_module("app.api.workflows_router")


class _H:
    workflow_id = "forked"


class _FakeClient:
    def __init__(self):
        self.captured = None

    async def fork_workflow_async(self, workflow_id, start_step, **kw):
        self.captured = (workflow_id, start_step, kw)
        return _H()


async def test_fork_client_path_passes_app_version(monkeypatch):
    fc = _FakeClient()
    monkeypatch.setattr(o, "_client", fc)
    monkeypatch.setattr(o, "_resolve_pinned_app_version", lambda: "v1")
    await wr._fork("wf1")
    assert fc.captured == ("wf1", 1, {"application_version": "v1"})


async def test_fork_inprocess_path_passes_app_version(monkeypatch):
    monkeypatch.setattr(o, "_client", None)
    monkeypatch.setattr(o, "_resolve_pinned_app_version", lambda: "v2")
    captured = {}
    from dbos import DBOS

    async def _fake(workflow_id, *, start_step, application_version=None, **kw):
        captured.update(
            workflow_id=workflow_id,
            start_step=start_step,
            application_version=application_version,
        )
        return _H()

    monkeypatch.setattr(DBOS, "fork_workflow_async", staticmethod(_fake))
    await wr._fork("wf1")
    assert captured["application_version"] == "v2"
    assert captured["start_step"] == 1
