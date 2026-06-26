"""Agent memory recall (Phase A). MemoryContext is mandatory — there is no
unscoped read path. recall() never raises (a memory miss must never break a
chat turn)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from loguru import logger

from app.repositories.agent_memory_repository import recall_rows


@dataclass(frozen=True)
class MemoryContext:
    user_id: str
    team_ids: Tuple[int, ...] = ()
    project_id: Optional[int] = None
    agent_id: Optional[str] = None
    session_id: Optional[int] = None


@dataclass(frozen=True)
class MemoryHit:
    id: int
    title: str
    body_md: str
    kind: str
    score: float


async def recall(ctx: MemoryContext, query: str, *, limit: int = 5) -> List[MemoryHit]:
    """Scope-isolated, ranked recall. Empty on blank query / no rows / any error."""
    if not query or not query.strip():
        return []
    try:
        rows = await recall_rows(
            query=query.strip(),
            user_id=ctx.user_id,
            team_ids=list(ctx.team_ids),
            limit=limit,
        )
        return [
            MemoryHit(
                id=int(r["id"]),
                title=str(r.get("title") or ""),
                body_md=str(r.get("body_md") or ""),
                kind=str(r.get("kind") or "fact"),
                score=float(r.get("score") or 0.0),
            )
            for r in rows
        ]
    except Exception:  # noqa: BLE001 — recall must never break a chat turn
        logger.warning(f"[agent_memory] recall failed for user={ctx.user_id}")
        return []


__all__ = ["MemoryContext", "MemoryHit", "recall"]
