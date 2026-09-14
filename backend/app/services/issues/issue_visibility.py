"""App-layer issue visibility, shared by every router that takes an issue id.

RLS does the same at the DB layer for non-service-role callers, but the
repositories use service_role, so the rule is re-enforced here — in one
place. D6.1 (用户立约: team 是铁边界): visible = creator OR assignee OR member
of the issue's team; anything else is 404, never 403, so existence does not
leak across teams.
"""

from __future__ import annotations

from typing import Any, Iterable

from fastapi import HTTPException, status

from app.repositories.issue_repository import issue_repository


async def is_issue_visible(
    row: dict[str, Any],
    user_id: str,
    *,
    team_memo: dict[int, bool] | None = None,
) -> bool:
    """The D6.1 rule for ONE already-loaded issue row.

    ``team_memo`` is an optional caller-owned cache of ``is_team_member``
    answers, keyed by ``team_id``. It exists for ``visible_issue_ids``, which
    judges many rows in a row and must not pay one membership read per issue.
    Its lifetime is the caller's: a long-lived memo would outlive a membership
    change, so nobody should hold one across requests.
    """
    if (
        row.get("created_by_user_id") == user_id
        or row.get("assignee_user_id") == user_id
    ):
        return True
    team_id = row.get("team_id")
    if team_id is None:
        return False
    team_id = int(team_id)
    if team_memo is not None and team_id in team_memo:
        return team_memo[team_id]
    member = await issue_repository.is_team_member(user_id, team_id)
    if team_memo is not None:
        team_memo[team_id] = member
    return member


async def visible_issue_ids(issue_ids: Iterable[Any], auth: Any) -> set[str]:
    """Which of ``issue_ids`` this caller may see — the batch form of the rule.

    Returns STRING ids (every snowflake on the wire is a string, and callers
    compare against a stringified ``issue_id``). An id with no row is simply
    absent: a deleted issue and an invisible one get the same answer, which is
    also what keeps existence from leaking.

    **Cost model**: one ``get_by_ids`` ``IN`` query for the whole deduplicated
    set, plus at most one ``is_team_member`` read per distinct ``team_id`` in
    it (memoized for this call only). An empty set costs nothing at all. So a
    thirty-version lineage chain spanning two issues in one team is two reads,
    not sixty.
    """
    wanted = {str(i) for i in issue_ids if i is not None}
    if not wanted:
        return set()
    user_id = str(auth.user_id)
    team_memo: dict[int, bool] = {}
    visible: set[str] = set()
    for row in await issue_repository.get_by_ids(wanted):
        row_id = str(row.get("id"))
        if row_id in wanted and await is_issue_visible(
            row, user_id, team_memo=team_memo
        ):
            visible.add(row_id)
    return visible


async def assert_issue_visible(issue_id: int, auth: Any) -> dict[str, Any]:
    """Return the issue row if the caller can see it; 404 otherwise."""
    row = await issue_repository.get_by_id(issue_id)
    if row and await is_issue_visible(row, str(auth.user_id)):
        return row
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")


__all__ = ["assert_issue_visible", "is_issue_visible", "visible_issue_ids"]
