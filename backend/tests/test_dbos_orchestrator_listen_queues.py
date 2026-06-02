"""launch_dbos(consume_queues=False) must restrict the process to zero user
queues via DBOS.listen_queues([]) BEFORE DBOS.launch() — the enqueue-only
gateway path. consume_queues=True (worker/combined) must NOT touch listen_queues."""

from __future__ import annotations

import pytest

from app.services.infra import dbos_orchestrator


class _FakeDBOS:
    """Records the order of listen_queues / launch calls."""

    def __init__(self):
        self.calls: list[tuple[str, object]] = []

    def listen_queues(self, queues):
        self.calls.append(("listen_queues", list(queues)))

    def launch(self):
        self.calls.append(("launch", None))


@pytest.fixture
def fake_dbos(monkeypatch):
    fake = _FakeDBOS()
    # launch_dbos does `from dbos import DBOS` locally — override the attr on
    # the real dbos module so the import inside the function picks up the fake.
    import dbos

    monkeypatch.setattr(dbos, "DBOS", fake)
    # Treat DBOS as initialized + skip the stale-scheduled sweep (DB-touching).
    monkeypatch.setattr(dbos_orchestrator, "_dbos", object())
    monkeypatch.setattr(
        dbos_orchestrator, "_pre_launch_sweep_stale_scheduled", lambda: None
    )
    return fake


def test_enqueue_only_calls_listen_queues_empty_before_launch(fake_dbos):
    dbos_orchestrator.launch_dbos(consume_queues=False)
    assert fake_dbos.calls == [("listen_queues", []), ("launch", None)]


def test_consume_default_does_not_call_listen_queues(fake_dbos):
    dbos_orchestrator.launch_dbos()  # default consume_queues=True
    assert fake_dbos.calls == [("launch", None)]
