"""Gateway→DBOSClient prep (DORMANT) for `enqueue_parse_for_user`.

`get_dbos_client()` returns None everywhere today → the existing
`parse_user_queue.enqueue` singleton path is taken (zero behavior change).
These tests force a fake client (and force None) to exercise both branches.
"""

import app.services.infra.dbos_orchestrator as o
import app.workflows.parse as P


class _H:
    workflow_id = "wf-x"


class _FakeClient:
    def __init__(self):
        self.calls = []

    def enqueue(self, options, *a, **k):
        self.calls.append((options, a, k))
        return _H()


def test_uses_client_when_present(monkeypatch):
    fc = _FakeClient()
    monkeypatch.setattr(o, "_client", fc)  # get_dbos_client() returns it
    monkeypatch.setattr(o, "_resolve_pinned_app_version", lambda: "v1")
    P.enqueue_parse_for_user(user_id="u1", workflow_id="wf1", kwargs={"url": "x"})
    opts, _, _ = fc.calls[0]
    assert opts["workflow_name"] == "parse_workflow"
    assert opts["queue_name"] == "parse_user"
    assert opts["queue_partition_key"] == "u1"
    assert opts["app_version"] == "v1"
    assert opts["workflow_id"] == "wf1"
    assert opts["authenticated_user"] == "u1"


def test_falls_back_to_queue_when_no_client(monkeypatch):
    monkeypatch.setattr(o, "_client", None)
    enq = {"n": 0}
    monkeypatch.setattr(
        P.parse_user_queue,
        "enqueue",
        lambda *a, **k: enq.__setitem__("n", enq["n"] + 1),
    )
    P.enqueue_parse_for_user(user_id="u1", workflow_id="wf1", kwargs={"url": "x"})
    assert enq["n"] == 1  # existing path
