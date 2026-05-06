"""MemoryHarvester — PostToolUse hook.

Returns a side_effect zero-arg callable that fires the memory-write
background task. The hook itself doesn't know whether the dispatch
goes via Celery or DBOS — it just calls ``signature_factory(...)`` and
hands the resulting closure back as ``HookResult.side_effect``.
Wiring lives in ``app.services.ai.chat.ai_library_chat_wiring``, which routes
through ``start_workflow_routed("memory_tasks", ...)`` so the routing
table picks celery vs shadow vs dbos.

Real memory extraction (UUID->int mapping, dual-prompt extraction,
when_to_use embedding, salience reinforcement) lives in
``app.workflows.write_memory`` (PR-D3c port of the legacy
``app.tasks.memory_tasks.write_memory_task``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from app.services.infra.hooks import HookContext, HookResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MemoryHarvesterHook:
    """PostToolUse — fires a routed side_effect to write memories.

    Dispatch mechanism (Celery vs DBOS) is decided by the
    ``signature_factory`` closure injected at wiring time, NOT by the
    hook itself.
    """

    name: str = "memory_harvester"
    priority: int = 80  # Run after CostAuditor so audit row exists first.

    # Optional injection: a factory that returns a zero-arg callable.
    # Production wiring (ai_library_chat_wiring.py) returns a closure
    # that calls start_workflow_routed("memory_tasks", ...). Tests pass
    # a MagicMock returning a callable so the dispatch path can be
    # verified without Celery or DBOS.
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
