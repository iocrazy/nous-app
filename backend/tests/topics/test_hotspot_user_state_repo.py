import pytest

from app.repositories.hotspot_user_state_repository import (
    HotspotUserStateRepository,
)


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows
        self.ops = []
        self.upserted = None

    def select(self, *a):
        self.ops.append(("select", a))
        return self

    def eq(self, col, val):
        self.ops.append(("eq", col, val))
        return self

    def in_(self, col, vals):
        self.ops.append(("in", col, list(vals)))
        return self

    def upsert(self, payload, on_conflict=None):
        self.upserted = (payload, on_conflict)
        return self

    async def execute(self):
        if self.upserted is not None:
            return type("R", (), {"data": [self.upserted[0]]})()
        return type("R", (), {"data": self._rows})()


class _FakeClient:
    def __init__(self, rows):
        self.q = _FakeQuery(rows)

    def table(self, name):
        return self.q


def _repo_with(monkeypatch, rows):
    client = _FakeClient(rows)
    repo = HotspotUserStateRepository()

    async def _fake_client():
        return client

    monkeypatch.setattr(repo, "_client", _fake_client)
    return repo, client


@pytest.mark.asyncio
async def test_get_states_maps_by_id(monkeypatch):
    rows = [
        {"hotspot_id": 5, "is_read": True, "is_saved": False, "is_hidden": False},
        {"hotspot_id": 9, "is_read": False, "is_saved": True, "is_hidden": True},
    ]
    repo, _ = _repo_with(monkeypatch, rows)
    out = await repo.get_states("u1", ["5", "9"])
    assert out["5"] == {"is_read": True, "is_saved": False, "is_hidden": False}
    assert out["9"]["is_saved"] is True and out["9"]["is_hidden"] is True


@pytest.mark.asyncio
async def test_get_states_empty_ids_short_circuits(monkeypatch):
    repo, _ = _repo_with(monkeypatch, [])
    assert await repo.get_states("u1", []) == {}


@pytest.mark.asyncio
async def test_list_ids_where_filters_by_flag(monkeypatch):
    repo, client = _repo_with(monkeypatch, [{"hotspot_id": 3}, {"hotspot_id": 7}])
    ids = await repo.list_ids_where("u1", flag="is_saved")
    assert ids == ["3", "7"]
    assert ("eq", "is_saved", True) in client.q.ops


@pytest.mark.asyncio
async def test_list_ids_where_rejects_unknown_flag(monkeypatch):
    repo, _ = _repo_with(monkeypatch, [])
    with pytest.raises(ValueError):
        await repo.list_ids_where("u1", flag="bogus")


@pytest.mark.asyncio
async def test_set_state_upserts_only_provided_flags(monkeypatch):
    repo, client = _repo_with(monkeypatch, [])
    flags = await repo.set_state("u1", "42", is_saved=True)
    payload, on_conflict = client.q.upserted
    assert payload["user_id"] == "u1" and payload["hotspot_id"] == "42"
    assert payload["is_saved"] is True
    assert "is_read" not in payload and "is_hidden" not in payload
    assert on_conflict == "user_id,hotspot_id"
    # returned flags coerce missing keys to False
    assert flags == {"is_read": False, "is_saved": True, "is_hidden": False}
