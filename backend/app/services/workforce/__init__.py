"""M2 Persistent Workforce — state machine, dispatcher, and lifecycle helpers.

Public surface re-exported so callers can do `from app.services.workforce import ...`.
"""

from __future__ import annotations

from app.services.workforce.state_machine import (
    ALLOWED_TRANSITIONS,
    InvalidTransitionError,
    WorkerStateMachine,
)

__all__ = [
    "ALLOWED_TRANSITIONS",
    "InvalidTransitionError",
    "WorkerStateMachine",
]
