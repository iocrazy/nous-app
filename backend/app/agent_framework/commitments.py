"""Commitments — pure types + state machine for agent followups.

Sprint 4 primitive layer. Distinct from agent_memories (which holds
facts). Commitments are promises the agent owes the user across sessions:
"I'll check in tomorrow", "remind you when build #142 finishes",
"follow up on the cooldown change in 1 week".

This module is DB-agnostic — it defines the types and decides when a
commitment is due / expired / etc. The repository layer
(``app/repositories/commitment_repository.py``) handles persistence.
The service layer (``app/services/commitment_service.py``) wires repo +
LifecycleBus events.

Three trigger types:
  - TIME: fire at a specific moment. Sweeper polls due rows.
  - EVENT: fire when a named event publishes (e.g., 'pr.merged:142').
  - NEXT_SESSION: fire next time this user opens a session with this agent.

Lifecycle:
  pending ─┬─ fulfilled  (agent did the thing)
           ├─ cancelled  (user/agent dismissed)
           ├─ expired    (passed expires_at without firing)
           └─ failed     (sweeper tried but agent run failed)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


class TriggerType(str, Enum):
    TIME = "time"
    EVENT = "event"
    NEXT_SESSION = "next_session"


class CommitmentStatus(str, Enum):
    PENDING = "pending"
    FULFILLED = "fulfilled"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    FAILED = "failed"


# Terminal statuses — once entered, no further transitions allowed.
TERMINAL_STATUSES = frozenset(
    {
        CommitmentStatus.FULFILLED,
        CommitmentStatus.CANCELLED,
        CommitmentStatus.EXPIRED,
        CommitmentStatus.FAILED,
    }
)


class InvalidCommitmentError(ValueError):
    """Raised when a commitment fails validation (missing trigger data,
    bad transition, etc.)."""


@dataclass
class Commitment:
    """Agent's owed-followup. DB-agnostic value object — repo translates
    to/from the SQL row.

    Validation rules (mirror migration 186 CHECK constraints):
      - TIME triggers MUST have ``trigger_at``
      - EVENT triggers MUST have ``trigger_event``
      - NEXT_SESSION needs neither
    """

    agent_id: str
    description: str
    trigger_type: TriggerType
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    payload_json: Optional[dict[str, Any]] = None
    trigger_at: Optional[datetime] = None
    trigger_event: Optional[str] = None
    expires_at: Optional[datetime] = None
    status: CommitmentStatus = CommitmentStatus.PENDING
    id: Optional[int] = None
    created_at: Optional[datetime] = None
    fulfilled_at: Optional[datetime] = None
    fulfillment_run_id: Optional[str] = None
    fulfillment_notes: Optional[str] = None

    def __post_init__(self) -> None:
        # Allow string inputs (e.g., from DB row) — coerce to enum.
        if isinstance(self.trigger_type, str):
            self.trigger_type = TriggerType(self.trigger_type)
        if isinstance(self.status, str):
            self.status = CommitmentStatus(self.status)
        if not self.description or not self.description.strip():
            raise InvalidCommitmentError("description must be non-empty")
        if self.trigger_type == TriggerType.TIME and self.trigger_at is None:
            raise InvalidCommitmentError(
                "TIME trigger requires trigger_at"
            )
        if self.trigger_type == TriggerType.EVENT and not self.trigger_event:
            raise InvalidCommitmentError(
                "EVENT trigger requires trigger_event"
            )

    def is_due(self, *, now: Optional[datetime] = None) -> bool:
        """True if this TIME-triggered commitment is ready to fire.
        EVENT/NEXT_SESSION return False (they fire via different paths)."""
        if self.status != CommitmentStatus.PENDING:
            return False
        if self.trigger_type != TriggerType.TIME:
            return False
        if self.trigger_at is None:  # defensive (should be caught in __post_init__)
            return False
        return (now or _utcnow()) >= self.trigger_at

    def is_expired(self, *, now: Optional[datetime] = None) -> bool:
        """True if expires_at has passed and we're still pending. Sweeper
        flips these to EXPIRED before considering them due."""
        if self.status != CommitmentStatus.PENDING:
            return False
        if self.expires_at is None:
            return False
        return (now or _utcnow()) >= self.expires_at

    def can_transition_to(self, new_status: CommitmentStatus) -> bool:
        """Terminal statuses are sticky — only PENDING rows can transition."""
        if self.status in TERMINAL_STATUSES:
            return False
        return new_status != CommitmentStatus.PENDING


def _utcnow() -> datetime:
    """UTC-aware now. Wrapped so tests can monkeypatch a fixed clock."""
    return datetime.now(timezone.utc)


__all__ = [
    "Commitment",
    "CommitmentStatus",
    "InvalidCommitmentError",
    "TERMINAL_STATUSES",
    "TriggerType",
]
