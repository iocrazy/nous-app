"""MemoryHarvester — PostToolUse hook skeleton.

M1.A purpose: validate the side_effect Celery dispatch path. We do NOT
write memories yet — that's M1.B (Memory v1 implementation). The skeleton
returns a side_effect Celery signature that points at a no-op task.
This proves the wiring works end-to-end before M1.B fills in real
extraction logic (UUID->int mapping, dual-prompt extraction, when_to_use
embedding, salience reinforcement).

Promotion path: in M1.B, replace ``_noop_memory_signature`` with a real
Celery task that:
  1. Pulls the recent ai_messages for ctx.session_id
  2. Runs both extractors (UserMemoryExtractor + AssistantMemoryExtractor)
  3. Embeds the resulting facts on ``when_to_use``
  4. Inserts into agent_memories with scope='agent_user'
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from app.services.hooks import HookContext, HookResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MemoryHarvesterHook:
    """PostToolUse — fires a Celery side_effect to write memories.

    Skeleton in M1.A. Real implementation lands in M1.B.
    """

    name: str = "memory_harvester"
    priority: int = 80  # Run after CostAuditor so audit row exists first.

    # Optional injection: a Celery task signature factory. In production
    # wiring, the chat service constructs the Hook with a closure over
    # the real ``write_memory_task.s`` so it can curry the run_id /
    # session_id. Tests pass a MagicMock so the dispatch path can be
    # verified without Celery.
    signature_factory: Optional[Any] = None

    async def __call__(
        self, ctx: HookContext, tool_result: dict[str, Any]
    ) -> HookResult:
        signature = self._build_signature(ctx, tool_result)
        if signature is None:
            # No factory bound (e.g. M1.A startup before Celery task exists).
            # Log + continue. AgentRunner.run_turn will not call .delay() on None.
            logger.debug(
                "[MemoryHarvester] no signature_factory bound; M1.B will wire it"
            )
            return HookResult(decision="continue")

        return HookResult(decision="continue", side_effect=signature)

    def _build_signature(
        self, ctx: HookContext, tool_result: dict[str, Any]
    ) -> Optional[Any]:
        if self.signature_factory is None:
            return None
        try:
            return self.signature_factory(
                run_id=str(ctx.run_id),
                agent_id=str(ctx.agent_id),
                user_id=str(ctx.user_id),
                session_id=str(ctx.session_id) if ctx.session_id else None,
                iteration=ctx.iteration,
                tool_name=ctx.tool_name,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "[MemoryHarvester] signature_factory raised; skipping side_effect"
            )
            return None


__all__ = ["MemoryHarvesterHook"]
