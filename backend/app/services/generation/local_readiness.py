"""Is the user's OWN machine ready to run a local engine right now?

The picker used to list every catalog row: the server codex row and its
local twin, Dreamina server and local, a Seedream whose upstream model is
gone. "Configured" for a local row means one concrete thing — the user's
daemon is online and its env_report says that CLI is installed and logged
in — and that is what this module answers, from the same two signals the
Settings → Local CLI card shows (presence key in Redis, env_report in
``codex_daemons``).

Degrades to "nothing local" on any failure: a Redis blip must hide the local
rows for one refresh, never 500 the picker.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from loguru import logger


@dataclass(frozen=True)
class LocalReadiness:
    codex: bool = False
    dreamina: bool = False

    def for_engine(self, actual_provider: str) -> Optional[bool]:
        """Readiness of a LOCAL provider, or None when the provider is not local."""
        p = (actual_provider or "").lower()
        if p == "codex-local":
            return self.codex
        if p == "jimeng-local":
            return self.dreamina
        return None


# Server rows that duplicate a local engine. When the local twin can run, the
# server twin is hidden: it is the same Dreamina account either way, and the
# user asked for one entry per engine. The server ``codex`` twin was retired
# outright on 2026-09-23 (protocol + catalog row, mig 498), so only Dreamina
# still has a server-side duplicate.
SERVER_TWIN_OF: dict[str, str] = {"jimeng-cli": "jimeng-local"}


async def local_engine_readiness(
    user_id: str,
    *,
    online_device_id: Optional[Callable[[str], Awaitable[Optional[str]]]] = None,
    list_devices: Optional[Callable[[str], Awaitable[list[dict[str, Any]]]]] = None,
    card_enabled: Optional[Callable[[str], Awaitable[bool]]] = None,
) -> LocalReadiness:
    try:
        if online_device_id is None:
            from app.services.codex.daemon_presence import online_device_id as _odi

            online_device_id = _odi
        if list_devices is None:
            from app.repositories.codex_daemon_repository import CodexDaemonRepository

            list_devices = CodexDaemonRepository().list_for_user
        if card_enabled is None:
            from app.services.codex.provider_card import card_enabled as _ce

            card_enabled = _ce
        device_id = await online_device_id(user_id)
        if not device_id:
            return LocalReadiness()
        report: dict[str, Any] = {}
        for d in await list_devices(user_id):
            if str(d.get("id")) == str(device_id):
                report = dict(d.get("env_report") or {})
                break
        # The Providers page is the ONE management entry (2026-09-06): an
        # online, logged-in daemon is necessary but the user must also have
        # switched the Codex card on for any application to offer it.
        return LocalReadiness(
            codex=bool(report.get("auth_ok"))
            and bool(report.get("skill_ok"))
            and await card_enabled(user_id),
            dreamina=bool(report.get("dreamina_ok"))
            and bool(report.get("dreamina_auth_ok")),
        )
    except Exception as exc:  # noqa: BLE001 — a picker must render without Redis
        logger.warning("[local-readiness] lookup failed, treating as offline: {}", exc)
        return LocalReadiness()


@dataclass(frozen=True)
class LocalVerdict:
    """What the user's own machine says about one catalog row.

    ``local_ready`` is ``None`` for a row that does not run locally;
    ``superseded`` marks a server twin hidden because its local twin can run.
    """

    local_ready: Optional[bool]
    superseded: bool

    @property
    def offered(self) -> bool:
        """Whether a picker should offer the row right now."""
        return self.local_ready is not False and not self.superseded


def local_verdict(
    actual_provider: Optional[str], ready: LocalReadiness
) -> LocalVerdict:
    """The daemon rule for one row. ``GET /ai/platform-status``
    (``services/ai/platform_provider``) publishes it per row and every
    generation picker applies it client side. Pure."""
    provider = str(actual_provider or "").lower()
    twin = SERVER_TWIN_OF.get(provider)
    return LocalVerdict(
        local_ready=ready.for_engine(provider),
        superseded=bool(twin and ready.for_engine(twin)),
    )
