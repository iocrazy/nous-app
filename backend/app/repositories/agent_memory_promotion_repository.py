"""Promotion review-queue repository for Agent Memory Phase C1.

A ``pending`` proposal represents an intent to flip a private
``agent_memory`` row to ``shared``. Admin-reviewed; nothing here auto-shares.

All functions are best-effort: they never raise — errors are logged via loguru
and the function returns False / [] / None as documented.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import text

from app.db.session import write_scope

# ---------------------------------------------------------------------------
# SQL constants
# ---------------------------------------------------------------------------

_INSERT_PROPOSAL_SQL = text(
    """
    INSERT INTO public.agent_memory_promotions
        (memory_id, proposed_scope, target_team_id, target_project_id,
         classification_kind, confidence, justification, scrubbed_body_md)
    VALUES
        (:memory_id, :proposed_scope, :target_team_id, :target_project_id,
         :classification_kind, :confidence, :justification, :scrubbed_body_md)
    ON CONFLICT DO NOTHING
    """
)

_SELECT_PENDING_FOR_UPDATE_SQL = text(
    """
    SELECT id, memory_id, target_team_id, target_project_id, scrubbed_body_md, status
    FROM public.agent_memory_promotions
    WHERE id = :id AND status = 'pending'
    FOR UPDATE
    """
)

_UPDATE_AGENT_MEMORY_PROMOTE_SQL = text(
    """
    UPDATE public.agent_memory
    SET visibility = :visibility,
        team_id    = :team_id,
        project_id = :project_id,
        body_md    = :body_md,
        updated_at = now()
    WHERE id = :memory_id
    """
)

_UPDATE_PROMOTION_APPROVED_SQL = text(
    """
    UPDATE public.agent_memory_promotions
    SET status      = 'approved',
        reviewed_by = :reviewer_id,
        reviewed_at = now()
    WHERE id = :proposal_id AND status = 'pending'
    """
)

_UPDATE_PROMOTION_REJECTED_SQL = text(
    """
    UPDATE public.agent_memory_promotions
    SET status      = 'rejected',
        reviewed_by = :reviewer_id,
        reviewed_at = now()
    WHERE id = :proposal_id AND status = 'pending'
    """
)

_UPDATE_AGENT_MEMORY_DEMOTE_SQL = text(
    """
    UPDATE public.agent_memory
    SET visibility = :visibility,
        updated_at = now()
    WHERE id = :memory_id
    """
)

_LIST_PROPOSALS_SQL = text(
    """
    SELECT
        p.id,
        p.memory_id,
        p.proposed_scope,
        p.target_team_id,
        p.target_project_id,
        p.classification_kind,
        p.confidence,
        p.justification,
        p.scrubbed_body_md,
        p.status,
        p.reviewed_by,
        p.reviewed_at,
        p.created_at,
        m.title,
        m.owner_user_id,
        m.body_md,
        m.scope
    FROM public.agent_memory_promotions p
    JOIN public.agent_memory m ON m.id = p.memory_id
    WHERE p.status = :status
    ORDER BY p.created_at DESC
    LIMIT :limit
    """
)


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


async def insert_proposal(
    *,
    memory_id: int,
    proposed_scope: str,
    target_team_id: int,
    target_project_id: Optional[int],
    classification_kind: str,
    confidence: float,
    justification: str,
    scrubbed_body_md: str,
) -> bool:
    """INSERT a new promotion proposal. Idempotent — ON CONFLICT DO NOTHING on
    the partial unique index ``(memory_id) WHERE status='pending'``.

    Returns True on success, False on any error (never raises).
    """
    try:
        async with write_scope() as session:
            await session.execute(
                _INSERT_PROPOSAL_SQL,
                {
                    "memory_id": memory_id,
                    "proposed_scope": proposed_scope,
                    "target_team_id": target_team_id,
                    "target_project_id": target_project_id,
                    "classification_kind": classification_kind,
                    "confidence": confidence,
                    "justification": justification,
                    "scrubbed_body_md": scrubbed_body_md,
                },
            )
        return True
    except Exception:  # noqa: BLE001 — best-effort write
        logger.warning(
            f"[agent_memory_promotions] insert_proposal failed for memory_id={memory_id}"
        )
        return False


async def approve_proposal(*, proposal_id: int, reviewer_id: str) -> bool:
    """Single-transaction approval: SELECT FOR UPDATE (guard) → UPDATE agent_memory
    (flip to shared) → UPDATE promotion (mark approved).

    Returns False immediately (without mutating) if no pending proposal matches
    ``proposal_id``, or on any error (rolled back). Never raises.
    """
    try:
        async with write_scope() as session:
            # Step 1 — guard: acquire the row only if it is still pending.
            result = await session.execute(
                _SELECT_PENDING_FOR_UPDATE_SQL,
                {"id": proposal_id},
            )
            row = result.mappings().fetchone()
            if row is None:
                return False

            # Step 2 — promote the memory row to shared.
            await session.execute(
                _UPDATE_AGENT_MEMORY_PROMOTE_SQL,
                {
                    "visibility": "shared",
                    "team_id": row["target_team_id"],
                    "project_id": row["target_project_id"],
                    "body_md": row["scrubbed_body_md"],
                    "memory_id": row["memory_id"],
                },
            )

            # Step 3 — mark the proposal as approved.
            await session.execute(
                _UPDATE_PROMOTION_APPROVED_SQL,
                {
                    "reviewer_id": reviewer_id,
                    "proposal_id": proposal_id,
                },
            )
        return True
    except Exception:  # noqa: BLE001
        logger.warning(
            f"[agent_memory_promotions] approve_proposal failed for proposal_id={proposal_id}"
        )
        return False


async def reject_proposal(*, proposal_id: int, reviewer_id: str) -> bool:
    """Mark a pending proposal as rejected. The ``agent_memory`` row is untouched.

    Returns True on success, False on any error (never raises).
    """
    try:
        async with write_scope() as session:
            await session.execute(
                _UPDATE_PROMOTION_REJECTED_SQL,
                {
                    "reviewer_id": reviewer_id,
                    "proposal_id": proposal_id,
                },
            )
        return True
    except Exception:  # noqa: BLE001
        logger.warning(
            f"[agent_memory_promotions] reject_proposal failed for proposal_id={proposal_id}"
        )
        return False


async def demote_memory(*, memory_id: int) -> bool:
    """Revoke sharing — flip ``agent_memory.visibility`` back to ``'private'``.

    ``team_id`` is intentionally left as-is (private rows may retain team_id;
    the CHECK constraint only requires team_id when visibility='shared').

    Returns True on success, False on any error (never raises).
    """
    try:
        async with write_scope() as session:
            await session.execute(
                _UPDATE_AGENT_MEMORY_DEMOTE_SQL,
                {
                    "visibility": "private",
                    "memory_id": memory_id,
                },
            )
        return True
    except Exception:  # noqa: BLE001
        logger.warning(
            f"[agent_memory_promotions] demote_memory failed for memory_id={memory_id}"
        )
        return False


async def list_proposals(
    *, status: str = "pending", limit: int = 100
) -> List[Dict[str, Any]]:
    """Return proposals joined with ``agent_memory`` (title, owner_user_id,
    original body_md, scope), ordered ``created_at DESC``.

    Returns an empty list on any error (never raises).
    """
    try:
        async with write_scope() as session:
            result = await session.execute(
                _LIST_PROPOSALS_SQL,
                {"status": status, "limit": limit},
            )
            return [dict(m) for m in result.mappings().all()]
    except Exception:  # noqa: BLE001
        logger.warning(
            f"[agent_memory_promotions] list_proposals failed for status={status}"
        )
        return []


__all__ = [
    "approve_proposal",
    "demote_memory",
    "insert_proposal",
    "list_proposals",
    "reject_proposal",
]
