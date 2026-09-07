"""In-process registry of live codex-daemon WebSocket connections (C2).

One question matters to the dispatch path: *does this user have a daemon
online right now?* — because "no daemon" must become a readable error at
the moment of the click, never a silent hang (CLAUDE.md「触发路径必须类型
化失败回显」).

Scope note: this map is per-process. A multi-worker deployment ALSO writes a
short-TTL Redis marker (see ``mark_online``) so a worker that does not hold
the socket can still tell the user's daemon is up and route the job to the
holder via Redis pub/sub. C2 ships the in-process half; the cross-worker hop
lands with C4's dispatch.
"""

from __future__ import annotations

from typing import Any, Dict, Protocol

from loguru import logger


class _Sendable(Protocol):
    async def send_json(self, payload: Any) -> None: ...
    async def close(self, code: int = 1000, reason: str = "") -> None: ...


class DaemonRegistry:
    def __init__(self) -> None:
        # user_id -> {device_id -> websocket}
        self._conns: Dict[str, Dict[str, _Sendable]] = {}

    # ── bookkeeping ──────────────────────────────────────────────────────

    def register(self, *, user_id: str, device_id: str, ws: _Sendable) -> None:
        self._conns.setdefault(user_id, {})[device_id] = ws
        logger.info(
            "[codex-daemon] device online user={} device={}", user_id[:8], device_id
        )

    def unregister(
        self, *, user_id: str, device_id: str, ws: _Sendable | None = None
    ) -> bool:
        """Drop a device's connection. Returns True when something was removed.

        With ``ws`` given, only that exact socket is removed: a daemon that
        reconnects before the server noticed the old socket died (systemctl
        restart, the 0.5.1 pong watchdog) re-registers the SAME device id, and
        the old session's teardown running afterwards must not evict the fresh
        one (2026-09-07: every reconnect showed ~30s "offline"). Without ``ws``
        (revocation) the device is removed whichever socket holds it.
        """
        devices = self._conns.get(user_id)
        if not devices:
            return False
        current = devices.get(device_id)
        if current is None or (ws is not None and current is not ws):
            return False
        devices.pop(device_id, None)
        if not devices:
            self._conns.pop(user_id, None)
        logger.info(
            "[codex-daemon] device offline user={} device={}", user_id[:8], device_id
        )
        return True

    def is_online(self, user_id: str) -> bool:
        return bool(self._conns.get(user_id))

    def devices_for(self, user_id: str) -> list[str]:
        return list(self._conns.get(user_id, {}).keys())

    # ── delivery ─────────────────────────────────────────────────────────

    async def send_job(self, user_id: str, payload: dict) -> bool:
        """Hand a job to any live device of this user. False when offline."""
        devices = self._conns.get(user_id)
        if not devices:
            return False
        # Newest registration first — a reconnected daemon is the freshest.
        for device_id, ws in reversed(list(devices.items())):
            try:
                await ws.send_json(payload)
                return True
            except Exception as exc:  # dead socket: drop and try the next
                logger.warning(
                    "[codex-daemon] send failed, dropping device={} err={}",
                    device_id,
                    exc,
                )
                self.unregister(user_id=user_id, device_id=device_id)
        return False

    async def disconnect_device(self, user_id: str, device_id: str) -> None:
        """Close a device's socket (used when the user revokes it)."""
        ws = self._conns.get(user_id, {}).get(device_id)
        self.unregister(user_id=user_id, device_id=device_id)
        if ws is None:
            return
        try:
            await ws.close(code=4003, reason="device revoked")
        except Exception:
            pass


# Process-wide singleton — the WS handler and the dispatch path share it.
registry = DaemonRegistry()
