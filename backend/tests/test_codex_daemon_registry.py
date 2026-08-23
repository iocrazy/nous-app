"""C2 — daemon connection registry (online/offline bookkeeping).

The registry answers one question for the dispatch path: does THIS user
have a live daemon right now? Design: the C-plan spec §4.
"""

from __future__ import annotations

import pytest

from app.services.codex.daemon_registry import DaemonRegistry


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list = []
        self.closed = False

    async def send_json(self, payload) -> None:
        self.sent.append(payload)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_register_makes_user_online_and_unregister_clears_it():
    reg = DaemonRegistry()
    ws = _FakeWS()
    assert reg.is_online("u1") is False
    reg.register(user_id="u1", device_id="d1", ws=ws)
    assert reg.is_online("u1") is True
    assert reg.devices_for("u1") == ["d1"]
    reg.unregister(user_id="u1", device_id="d1")
    assert reg.is_online("u1") is False


@pytest.mark.asyncio
async def test_send_job_picks_a_live_connection_and_returns_false_when_offline():
    reg = DaemonRegistry()
    ws = _FakeWS()
    reg.register(user_id="u1", device_id="d1", ws=ws)
    ok = await reg.send_job("u1", {"type": "job", "job_id": "j1"})
    assert ok is True
    assert ws.sent[0]["job_id"] == "j1"
    assert await reg.send_job("nobody", {"type": "job", "job_id": "j2"}) is False


@pytest.mark.asyncio
async def test_revoking_a_device_closes_its_socket():
    reg = DaemonRegistry()
    ws = _FakeWS()
    reg.register(user_id="u1", device_id="d1", ws=ws)
    await reg.disconnect_device("u1", "d1")
    assert ws.closed is True
    assert reg.is_online("u1") is False


@pytest.mark.asyncio
async def test_second_device_for_same_user_coexists():
    reg = DaemonRegistry()
    a, b = _FakeWS(), _FakeWS()
    reg.register(user_id="u1", device_id="d1", ws=a)
    reg.register(user_id="u1", device_id="d2", ws=b)
    assert sorted(reg.devices_for("u1")) == ["d1", "d2"]
    reg.unregister(user_id="u1", device_id="d1")
    assert reg.devices_for("u1") == ["d2"]
    assert reg.is_online("u1") is True


@pytest.mark.asyncio
async def test_ws_auth_resolves_device_by_token_hash(monkeypatch):
    """The daemon socket authenticates with its device token (hashed lookup);
    unknown/revoked tokens are refused before accept()."""
    import sys

    import app.main  # noqa: F401  (populate sys.modules)

    mod = sys.modules["app.api.codex_daemon_ws_router"]

    async def _fake_lookup(token_hash: str):
        return (
            {"id": "d1", "user_id": "u1"}
            if token_hash == mod.token_hash("good")
            else None
        )

    monkeypatch.setattr(mod, "_lookup_device", _fake_lookup)
    assert await mod.authenticate_device("good") == {"id": "d1", "user_id": "u1"}
    assert await mod.authenticate_device("bad") is None
    assert await mod.authenticate_device("") is None
