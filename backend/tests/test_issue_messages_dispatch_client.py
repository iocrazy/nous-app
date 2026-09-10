"""Client-aware dispatch for the issue_messages router (gateway→DBOSClient prep).

`_dispatch_respond_to_issue_reply(issue_id, owner_id, body, attachments, wf_id)`
routes through the gateway DBOSClient when one is constructed, else falls back to
`SetWorkflowID + DBOS.start_workflow`. Client is None today → fallback → zero
behavior change. Preserves the exact positional args.
"""

from __future__ import annotations

import importlib

from app.services.infra import dbos_orchestrator

# NOTE: `app/api/__init__.py` rebinds the name `issue_messages_router` to the
# APIRouter instance (shadowing the submodule), so a plain import-as would yield
# the router object. Load the actual module.
msgs_router = importlib.import_module("app.api.issue_messages_router")
# The dispatcher itself now lives here; the router keeps a private alias.
dispatch_mod = importlib.import_module("app.services.issues.issue_reply_dispatch")


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list = []

    def enqueue(self, options, *args):
        self.calls.append((options, args))
        return "fake-wf-handle"


def test_dispatch_uses_client_when_set(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(dbos_orchestrator, "_client", fake)
    monkeypatch.setattr(dbos_orchestrator, "_resolve_pinned_app_version", lambda: None)

    from dbos import DBOS

    called = {"start": False}
    monkeypatch.setattr(
        DBOS, "start_workflow", lambda *a, **k: called.__setitem__("start", True)
    )

    attachments = [{"kind": "resource", "id": "1"}]
    msgs_router._dispatch_respond_to_issue_reply(
        42, "owner-99", "hello body", attachments, "issue-reply-42-uuid"
    )

    assert called["start"] is False
    assert len(fake.calls) == 1
    options, args = fake.calls[0]
    assert options["workflow_name"] == "respond_to_issue_reply"
    assert options["queue_name"] == "dbos_dispatch"
    assert options["workflow_id"] == "issue-reply-42-uuid"
    assert "app_version" not in options
    assert args == (42, "owner-99", "hello body", attachments)


def test_dispatch_pins_app_version_when_present(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(dbos_orchestrator, "_client", fake)
    monkeypatch.setattr(
        dbos_orchestrator, "_resolve_pinned_app_version", lambda: "cafe1234"
    )

    msgs_router._dispatch_respond_to_issue_reply(1, "o", "b", None, "issue-reply-1-x")

    options, args = fake.calls[0]
    assert options["app_version"] == "cafe1234"
    assert args == (1, "o", "b", None)


def test_dispatch_falls_back_to_start_workflow_when_client_none(monkeypatch):
    monkeypatch.setattr(dbos_orchestrator, "_client", None)

    from dbos import DBOS

    spy: list = []
    monkeypatch.setattr(
        DBOS, "start_workflow", lambda wf, *args: spy.append((wf, args))
    )

    attachments = [{"kind": "resource", "id": "7"}]
    msgs_router._dispatch_respond_to_issue_reply(
        5, "owner-5", "body text", attachments, "issue-reply-5-y"
    )

    assert len(spy) == 1
    wf, args = spy[0]
    assert wf is dispatch_mod.respond_to_issue_reply
    assert args == (5, "owner-5", "body text", attachments)
