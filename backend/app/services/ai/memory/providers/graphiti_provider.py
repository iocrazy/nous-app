"""GraphitiProvider — L3 adapter over GraphMemoryService (Phase 1).

Behaviour-preserving: record_turn reproduces the service-side body of the
existing ``write_memory._write_graph_episode`` (minus the cross-provider
prefs.learn gate, which stays at the call site). get_context wraps search().
"""

from __future__ import annotations

from typing import List, Optional

from loguru import logger

from app.services.ai.memory.graph_memory import get_graph_memory_service
from app.services.ai.memory.provider import MemoryLayer, MemoryProvider, MemoryTurn


class GraphitiProvider(MemoryProvider):
    @property
    def name(self) -> str:
        return "graphiti"

    @property
    def layer(self) -> MemoryLayer:
        return MemoryLayer.L3

    def enabled(self) -> bool:
        try:
            return bool(get_graph_memory_service().config.enabled)
        except Exception:  # noqa: BLE001
            return False

    async def is_operative(self) -> bool:
        try:
            return await get_graph_memory_service().is_enabled()
        except Exception:  # noqa: BLE001
            return False

    async def record_turn(self, turn: MemoryTurn) -> bool:
        from app.workflows.write_memory import (  # lazy: avoids circular import (Task 5)
            _build_turn_episode,
        )

        service = get_graph_memory_service()
        if not service.config.enabled:
            return False
        body = _build_turn_episode(turn.user_msgs, turn.asst_msgs)
        if not body:
            return False
        return await service.add_chat_episode(
            group_id=f"user-{turn.user_id}",
            name=f"chat-{turn.session_id}-{turn.run_id or turn.iteration}",
            body=body,
            source_description="mediahub chat turn",
        )

    async def get_context(
        self,
        *,
        user_id: str,
        query: str = "",
        workspace_id: Optional[str] = None,
        group_ids: Optional[List[str]] = None,
    ) -> Optional[str]:
        try:
            facts = await get_graph_memory_service().search(
                query, group_ids=group_ids or [], limit=10
            )
        except Exception:  # noqa: BLE001
            logger.exception("[graphiti_provider] search failed (user=%s)", user_id)
            return None
        if not facts:
            return None
        rendered = "\n".join(str(getattr(f, "fact", f)) for f in facts)
        return rendered or None

    async def reload(self) -> None:
        # In-process: drop the cached config so the next call re-reads
        # GraphMemoryConfig.from_settings(). GraphMemoryService._ensure_config
        # rebuilds when _config_loaded is False AND graphiti is None (line 357).
        # We reset BOTH config and _config_loaded: config=None satisfies the test
        # assertion; _config_loaded=False is the real prod rebuild trigger.
        try:
            svc = get_graph_memory_service()
            svc.config = None  # type: ignore[assignment]
            svc._config_loaded = False  # type: ignore[attr-defined]  # real rebuild gate
        except Exception:  # noqa: BLE001
            logger.warning("[graphiti_provider] reload failed")

    async def health(self) -> bool:
        return await self.is_operative()


__all__ = ["GraphitiProvider"]
