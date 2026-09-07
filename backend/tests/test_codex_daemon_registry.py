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
    unknown/revoked tokens resolve to None and are refused (see
    test_unauthenticated_socket_is_accepted_then_closed for how)."""
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


@pytest.mark.asyncio
async def test_unauthenticated_socket_is_accepted_then_closed(monkeypatch):
    """A rejected daemon MUST be accept()ed before close(4001).

    Closing an unaccepted WebSocket makes uvicorn fail the HTTP handshake with
    403 and throw the close code away — the daemon then only sees 1006, which
    it correctly treats as a transient network error and retries forever. Since
    find_by_token_hash filters `revoked_at IS NULL`, a *revoked* device that
    reconnects lands on exactly this path (not on 4003), so getting the order
    wrong makes revocation unenforceable for any device that was offline when
    it was revoked.
    """
    import sys

    import app.main  # noqa: F401  (populate sys.modules)

    mod = sys.modules["app.api.codex_daemon_ws_router"]

    calls: list[tuple] = []

    class _RejectedWS:
        headers = {"authorization": "Bearer revoked-or-unknown"}

        async def accept(self) -> None:
            calls.append(("accept",))

        async def close(self, code: int = 1000, reason: str = "") -> None:
            calls.append(("close", code, reason))

    async def _no_device(_hashed: str):
        return None

    monkeypatch.setattr(mod, "_lookup_device", _no_device)
    await mod.ws_codex_agent(_RejectedWS())

    assert [c[0] for c in calls] == [
        "accept",
        "close",
    ], f"accept() must come first, got {calls}"
    assert calls[1][1] == 4001


# ── a stale connection's close must not evict the fresh one (2026-09-07) ─────
# systemctl restart / the 0.5.1 pong watchdog reconnect BEFORE the server has
# noticed the old socket is gone. The old session's ``finally`` then ran
# ``unregister(user, device)`` and ``mark_offline`` by key alone — evicting the
# NEW socket and deleting presence, so every reconnect produced ~30s of
# "offline" (real-stack: online 04:27:17, offline 04:27:26, back on the next
# ping). Teardown must act only when the registry still holds THIS socket.


@pytest.mark.asyncio
async def test_unregister_of_a_stale_socket_leaves_the_fresh_one_registered():
    reg = DaemonRegistry()
    old, fresh = _FakeWS(), _FakeWS()
    reg.register(user_id="u1", device_id="d1", ws=old)
    reg.register(user_id="u1", device_id="d1", ws=fresh)  # reconnect, same device
    assert reg.unregister(user_id="u1", device_id="d1", ws=old) is False
    assert reg.is_online("u1") is True
    assert await reg.send_job("u1", {"type": "job", "job_id": "j"}) is True
    assert fresh.sent and not old.sent


@pytest.mark.asyncio
async def test_unregister_of_the_current_socket_still_clears_it():
    reg = DaemonRegistry()
    ws = _FakeWS()
    reg.register(user_id="u1", device_id="d1", ws=ws)
    assert reg.unregister(user_id="u1", device_id="d1", ws=ws) is True
    assert reg.is_online("u1") is False
    # Without a socket in hand (revoke path) it behaves as before.
    reg.register(user_id="u1", device_id="d1", ws=ws)
    assert reg.unregister(user_id="u1", device_id="d1") is True
    assert reg.is_online("u1") is False
