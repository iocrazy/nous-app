import pytest

from app.repositories.signal_sources_repository import (
    SignalSourcesRepository,
    compute_health,
)


def test_compute_health_success_resets():
    h = compute_health(prev_failures=2, ok=True, dead_threshold=3)
    assert h == {"health": "ok", "consecutive_failures": 0, "flipped_to_dead": False}


def test_compute_health_degraded():
    h = compute_health(prev_failures=0, ok=False, dead_threshold=3)
    assert h["health"] == "degraded" and h["consecutive_failures"] == 1
    assert h["flipped_to_dead"] is False


def test_compute_health_flips_to_dead_once():
    h = compute_health(prev_failures=2, ok=False, dead_threshold=3)
    assert h["health"] == "dead" and h["consecutive_failures"] == 3
    assert h["flipped_to_dead"] is True


def test_compute_health_stays_dead_no_reflip():
    h = compute_health(prev_failures=3, ok=False, dead_threshold=3)
    assert h["health"] == "dead" and h["flipped_to_dead"] is False


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows
        self.ops = []

    def select(self, *a):
        self.ops.append(("select", a))
        return self

    def order(self, col):
        self.ops.append(("order", col))
        return self

    async def execute(self):
        return type("R", (), {"data": self._rows})()


class _FakeClient:
    def __init__(self, rows):
        self.q = _FakeQuery(rows)

    def table(self, name):
        return self.q


@pytest.mark.asyncio
async def test_list_all_selects_and_orders(monkeypatch):
    rows = [{"id": "1", "name": "A", "health": "dead"}]
    client = _FakeClient(rows)
    repo = SignalSourcesRepository()

    async def _fake_client():
        return client

    monkeypatch.setattr(repo, "_client", _fake_client)
    out = await repo.list_all()
    assert out == rows
    # worst-first ordering: order by health then name
    assert ("order", "health") in client.q.ops
    assert ("order", "name") in client.q.ops
