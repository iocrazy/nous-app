"""Who may read a generated_media row's scope.

One rule, three callers (promote, canvas derive, upscale). A second copy of
this check would be the place the two answers start to differ.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol


class TeamMembership(Protocol):
    async def is_team_member(self, *, team_id: int, user_id: str) -> bool: ...


async def can_read_generation_scope(
    gen: Mapping[str, Any],
    *,
    user_id: str,
    personal_team_id: int,
    membership: TeamMembership,
) -> bool:
    """May *user_id* read the scope this generation lives in?

    Works for every ``origin_kind`` — a chat_upload row carries the same
    ``scope_id`` as any other (the resolved chat scope). A row with no
    ``scope_id`` at all is unreadable: a check that cannot run has not passed.
    """
    gen_scope = gen.get("scope_id")
    if gen_scope is None:
        return False
    source_scope_id = int(gen_scope)
    if source_scope_id == personal_team_id:
        return True
    return bool(
        await membership.is_team_member(team_id=source_scope_id, user_id=user_id)
    )
