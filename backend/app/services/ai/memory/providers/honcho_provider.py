"""HonchoProvider — L2 adapter over HonchoMemoryService (Phase 1).

Behaviour-preserving: record_turn reproduces the service-side body of the
existing ``write_memory._write_honcho_turn`` (minus the cross-provider
prefs.learn gate, which stays at the call site). get_context wraps the
per-turn inject path's ``get_user_representation`` (NOT the slow dialectic
``get_user_context``).
"""

from __future__ import annotations

from typing import List, Optional

from loguru import logger

from app.services.ai.memory.honcho_memory import get_honcho_memory_service
from app.services.ai.memory.provider import MemoryLayer, MemoryProvider, MemoryTurn


async def _resolve_team_workspace(session_id: str) -> Optional[str]:
    """Lazy proxy so tests can patch this module-level name while the real
    import of ``write_memory`` stays deferred until call time (avoids the
    circular import introduced in Task 5 when the registry imports providers)."""
    from app.workflows.write_memory import (  # noqa: PLC0415
        _resolve_team_workspace as _impl,
    )

    return await _impl(session_id)


class HonchoProvider(MemoryProvider):
    @property
    def name(self) -> str:
        return "honcho"

    @property
    def layer(self) -> MemoryLayer:
        return MemoryLayer.L2

    def enabled(self) -> bool:
        try:
            return bool(get_honcho_memory_service().config.enabled)
        except Exception:  # noqa: BLE001
            return False

    async def is_operative(self) -> bool:
        try:
            return bool(get_honcho_memory_service().config.operative())
        except Exception:  # noqa: BLE001
            return False

    async def record_turn(self, turn: MemoryTurn) -> bool:
        try:
            service = get_honcho_memory_service()
            if not service.config.enabled:
                return False
            user_message = turn.user_msgs[-1] if turn.user_msgs else ""
            assistant_message = turn.asst_msgs[-1] if turn.asst_msgs else ""
            if not (user_message.strip() or assistant_message.strip()):
                return False
            workspace_id = await _resolve_team_workspace(turn.session_id)
            return await service.add_chat_turn(
                user_id=turn.user_id,
                agent_id=turn.agent_id,
                session_id=turn.session_id,
                user_message=user_message,
                assistant_message=assistant_message,
                workspace_id=workspace_id,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                f"[honcho_provider] record_turn failed (user={turn.user_id})"
            )
            return False

    async def get_context(
        self,
        *,
        user_id: str,
        query: str = "",
        workspace_id: Optional[str] = None,
        group_ids: Optional[List[str]] = None,
    ) -> Optional[str]:
        try:
            return await get_honcho_memory_service().get_user_representation(
                user_id=user_id, workspace_id=workspace_id
            )
        except Exception:  # noqa: BLE001
            logger.exception(f"[honcho_provider] get_context failed (user={user_id})")
            return None

    async def reload(self) -> None:
        # Drop the cached httpx client so a changed connection (Phase 2: when
        # base_url/workspace move to system_settings) takes effect on the next
        # call. The HonchoMemoryService singleton lazily rebuilds its client.
        try:
            service = get_honcho_memory_service()
            if getattr(service, "client", None) is not None:
                try:
                    await service.client.aclose()  # type: ignore[union-attr]
                except Exception:  # noqa: BLE001
                    pass
                service.client = None  # type: ignore[assignment]
        except Exception:  # noqa: BLE001
            logger.warning("[honcho_provider] reload failed")

    async def health(self) -> bool:
        return await self.is_operative()


__all__ = ["HonchoProvider"]
