"""App-layer issue visibility, shared by every router that takes an issue id.

RLS does the same at the DB layer for non-service-role callers, but the
repositories use service_role, so the rule is re-enforced here — in one
place. D6.1 (用户立约: team 是铁边界): visible = creator OR assignee OR member
of the issue's team; anything else is 404, never 403, so existence does not
leak across teams.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status

from app.repositories.issue_repository import issue_repository


async def is_issue_visible(row: dict[str, Any], user_id: str) -> bool:
    if (
        row.get("created_by_user_id") == user_id
        or row.get("assignee_user_id") == user_id
    ):
        return True
    team_id = row.get("team_id")
    return team_id is not None and await issue_repository.is_team_member(
        user_id, int(team_id)
    )


async def assert_issue_visible(issue_id: int, auth: Any) -> dict[str, Any]:
    """Return the issue row if the caller can see it; 404 otherwise."""
    row = await issue_repository.get_by_id(issue_id)
    if row and await is_issue_visible(row, str(auth.user_id)):
        return row
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")


__all__ = ["assert_issue_visible", "is_issue_visible"]
