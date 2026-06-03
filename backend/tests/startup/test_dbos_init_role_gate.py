"""init_dbos role gate (Stage C FLIP):
- GATEWAY: constructs an enqueue-only DBOSClient via init_dbos_client() and
  does NOT launch a DBOS executor (no init_dbos / no launch_dbos). This stops
  the gateway from consuming _dbos_internal_queue / running an executor.
- WORKER / COMBINED: still init_dbos(executor_id=role) + launch_dbos with
  consume_queues=runs_dbos_workers (True for both)."""

from __future__ import annotations

import types

import pytest

from app.agent_framework.role import ProcessRole
from app.services.infra import dbos_orchestrator
from app.startup import dbos_init


def _make_app(role: ProcessRole):
    app = types.SimpleNamespace()
    app.state = types.SimpleNamespace(process_role=role)
    return app


@pytest.fixture
def spies(monkeypatch):
    captured: dict[str, object] = {
        "init_dbos_called": False,
        "init_dbos_client_called": False,
        "launch_called": False,
    }

    def _fake_init(executor_id: str | None = None):
        captured["init_dbos_called"] = True
        captured["executor_id"] = executor_id

    def _fake_init_client():
        captured["init_dbos_client_called"] = True

    def _fake_launch(consume_queues: bool = True):
        captured["launch_called"] = True
        captured["consume_queues"] = consume_queues

    monkeypatch.setattr(dbos_orchestrator, "init_dbos", _fake_init)
    monkeypatch.setattr(dbos_orchestrator, "init_dbos_client", _fake_init_client)
    monkeypatch.setattr(dbos_orchestrator, "is_enabled", lambda: True)
    monkeypatch.setattr(dbos_orchestrator, "launch_dbos", _fake_launch)
    return captured


def test_gateway_role_uses_enqueue_only_client(spies):
    dbos_init.init_dbos(_make_app(ProcessRole.GATEWAY))
    # gateway builds the client, never launches an executor
    assert spies["init_dbos_client_called"] is True
    assert spies["launch_called"] is False
    assert spies["init_dbos_called"] is False


def test_worker_role_launches_executor(spies):
    dbos_init.init_dbos(_make_app(ProcessRole.WORKER))
    assert spies["init_dbos_client_called"] is False
    assert spies["init_dbos_called"] is True
    assert spies["executor_id"] == "worker"
    assert spies["launch_called"] is True
    assert spies["consume_queues"] is True


def test_combined_role_launches_executor(spies):
    dbos_init.init_dbos(_make_app(ProcessRole.COMBINED))
    assert spies["init_dbos_client_called"] is False
    assert spies["init_dbos_called"] is True
    assert spies["executor_id"] == "combined"
    assert spies["launch_called"] is True
    assert spies["consume_queues"] is True
