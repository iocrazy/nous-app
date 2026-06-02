"""init_dbos must forward consume_queues = process_role.runs_dbos_workers to
launch_dbos: gateway -> False (enqueue-only), worker/combined -> True."""

from __future__ import annotations

import types

import pytest

from app.agent_framework.role import ProcessRole
from app.startup import dbos_init
from app.services.infra import dbos_orchestrator


def _make_app(role: ProcessRole):
    app = types.SimpleNamespace()
    app.state = types.SimpleNamespace(process_role=role)
    return app


@pytest.fixture
def capture_launch(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(dbos_orchestrator, "init_dbos", lambda: None)
    monkeypatch.setattr(dbos_orchestrator, "is_enabled", lambda: True)

    def _fake_launch(consume_queues: bool = True):
        captured["consume_queues"] = consume_queues

    monkeypatch.setattr(dbos_orchestrator, "launch_dbos", _fake_launch)
    return captured


def test_gateway_role_launches_enqueue_only(capture_launch):
    dbos_init.init_dbos(_make_app(ProcessRole.GATEWAY))
    assert capture_launch["consume_queues"] is False


def test_worker_role_consumes_queues(capture_launch):
    dbos_init.init_dbos(_make_app(ProcessRole.WORKER))
    assert capture_launch["consume_queues"] is True


def test_combined_role_consumes_queues(capture_launch):
    dbos_init.init_dbos(_make_app(ProcessRole.COMBINED))
    assert capture_launch["consume_queues"] is True
