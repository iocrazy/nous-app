"""G2 — DBOS approval gate primitive tests.

DBOS recv/send are tested via mock since real DBOS workflow context
isn't available in unit-test harness."""
from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest

from app.agent_framework import approval_gate as ag


@pytest.mark.unit
def test_decision_factory_constructors():
    """ApprovalDecision factories produce the right shape."""
    a = ag.ApprovalDecision.approve("lgtm")
    assert a.approved is True
    assert a.note == "lgtm"
    assert a.timed_out is False

    r = ag.ApprovalDecision.reject("nope")
    assert r.approved is False
    assert r.note == "nope"
    assert r.timed_out is False

    t = ag.ApprovalDecision.timeout()
    assert t.approved is False
    assert t.timed_out is True


@pytest.mark.unit
def test_topic_for_uses_prefix():
    """Topic strings use the 'approval:' prefix to avoid colliding
    with future DBOS.send usages on other topics."""
    aid = str(uuid4())
    assert ag._topic_for(aid) == f"approval:{aid}"


@pytest.mark.unit
def test_await_returns_decision_from_dict_payload():
    """When DBOS.recv returns a dict {approved, note}, parse it."""
    aid = str(uuid4())
    fake_dbos = type("DBOS", (), {})
    fake_dbos.recv = staticmethod(
        lambda topic, timeout_seconds: {"approved": True, "note": "ok"}
    )

    with patch.dict("sys.modules", {"dbos": type("M", (), {"DBOS": fake_dbos})}):
        decision = ag.await_approval_in_workflow(aid)
    assert decision.approved is True
    assert decision.note == "ok"
    assert decision.timed_out is False


@pytest.mark.unit
def test_await_returns_timeout_when_recv_returns_none():
    """DBOS.recv returns None on timeout."""
    aid = str(uuid4())
    fake_dbos = type("DBOS", (), {})
    fake_dbos.recv = staticmethod(lambda topic, timeout_seconds: None)

    with patch.dict("sys.modules", {"dbos": type("M", (), {"DBOS": fake_dbos})}):
        decision = ag.await_approval_in_workflow(aid)
    assert decision.timed_out is True
    assert decision.approved is False


@pytest.mark.unit
def test_await_treats_malformed_payload_as_reject():
    """Defensive: unknown payload shape → reject, not crash."""
    aid = str(uuid4())
    fake_dbos = type("DBOS", (), {})
    fake_dbos.recv = staticmethod(lambda topic, timeout_seconds: "garbage_string")

    with patch.dict("sys.modules", {"dbos": type("M", (), {"DBOS": fake_dbos})}):
        decision = ag.await_approval_in_workflow(aid)
    assert decision.approved is False
    assert "malformed" in (decision.note or "")


@pytest.mark.unit
def test_await_topic_passed_to_recv():
    """Topic is correctly built from approval_id."""
    aid = str(uuid4())
    captured: dict = {}

    def _recv(topic, timeout_seconds):
        captured["topic"] = topic
        captured["timeout"] = timeout_seconds
        return None

    fake_dbos = type("DBOS", (), {})
    fake_dbos.recv = staticmethod(_recv)

    with patch.dict("sys.modules", {"dbos": type("M", (), {"DBOS": fake_dbos})}):
        ag.await_approval_in_workflow(aid, ttl_seconds=300)
    assert captured["topic"] == f"approval:{aid}"
    assert captured["timeout"] == 300


@pytest.mark.unit
def test_signal_calls_dbos_send_with_topic():
    """signal_approval_decision wires DBOS.send correctly."""
    aid = str(uuid4())
    wf_id = "wf-abc"
    captured: dict = {}

    def _send(workflow_id, payload, *, topic):
        captured["workflow_id"] = workflow_id
        captured["payload"] = payload
        captured["topic"] = topic

    fake_dbos = type("DBOS", (), {})
    fake_dbos.send = staticmethod(_send)

    with patch.dict("sys.modules", {"dbos": type("M", (), {"DBOS": fake_dbos})}):
        ok = ag.signal_approval_decision(
            workflow_id=wf_id, approval_id=aid, approved=True, note="lgtm",
        )
    assert ok is True
    assert captured["workflow_id"] == wf_id
    assert captured["topic"] == f"approval:{aid}"
    assert captured["payload"] == {"approved": True, "note": "lgtm"}


@pytest.mark.unit
def test_signal_returns_false_on_dbos_error():
    """When DBOS.send raises (workflow doesn't exist / chat path),
    signal returns False — caller treats as no-op."""
    aid = str(uuid4())

    def _broken_send(workflow_id, payload, *, topic):
        raise RuntimeError("workflow not found")

    fake_dbos = type("DBOS", (), {})
    fake_dbos.send = staticmethod(_broken_send)

    with patch.dict("sys.modules", {"dbos": type("M", (), {"DBOS": fake_dbos})}):
        ok = ag.signal_approval_decision(
            workflow_id="missing", approval_id=aid, approved=False,
        )
    assert ok is False
