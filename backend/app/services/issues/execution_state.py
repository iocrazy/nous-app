"""The one merge-writer for ``issues.execution_state`` decorations.

Every writer of this column merges (``||``) its own keys and leaves the rest
alone — see ``issue_lifecycle.set_status`` for the history of the assignment
that used to drop other writers' keys. This module is the reusable form of
that rule: ``merge_execution_state(issue_id, {...})``. The column is
service_role-only under the mig-170 allowlist trigger, so the write runs as
``service_role`` for the transaction (``SET LOCAL`` — reverts at COMMIT).
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import cast, func, literal, text, update
from sqlalchemy.dialects.postgresql import JSONB

from app.db.session import write_scope
from app.models import Issues

# A role identifier cannot be a bound parameter; this fixed fragment is the
# same carve-out mark_turn_progress / set_status rely on.
_AS_SERVICE_ROLE = text("SET LOCAL ROLE service_role")


def merge_stmt(issue_id: int, patch: dict[str, Any]):
    """``UPDATE issues SET execution_state = COALESCE(execution_state,'{}') || :patch``."""
    merged = func.coalesce(Issues.execution_state, cast(literal("{}"), JSONB)).op(
        "||", return_type=JSONB
    )(cast(literal(json.dumps(patch, default=str)), JSONB))
    return (
        update(Issues).where(Issues.id == int(issue_id)).values(execution_state=merged)
    )


async def merge_execution_state(issue_id: int, patch: dict[str, Any]) -> None:
    if not patch:
        return
    async with write_scope() as session:
        await session.execute(_AS_SERVICE_ROLE)
        await session.execute(merge_stmt(issue_id, patch))


__all__ = ["merge_execution_state", "merge_stmt"]
