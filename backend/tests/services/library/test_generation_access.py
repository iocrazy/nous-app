"""The one scope-read rule shared by promote, canvas derive and upscale."""

from __future__ import annotations

from app.services.library.generation_access import can_read_generation_scope


class FakeMembership:
    def __init__(self, teams: set[int]) -> None:
        self.teams = teams
        self.calls: list[tuple[int, str]] = []

    async def is_team_member(self, *, team_id: int, user_id: str) -> bool:
        self.calls.append((team_id, user_id))
        return team_id in self.teams


async def test_row_without_scope_is_unreadable() -> None:
    membership = FakeMembership({7})
    assert not await can_read_generation_scope(
        {"scope_id": None}, user_id="u1", personal_team_id=7, membership=membership
    )
    assert membership.calls == []


async def test_personal_scope_is_readable_without_asking_membership() -> None:
    membership = FakeMembership(set())
    assert await can_read_generation_scope(
        {"scope_id": "7"}, user_id="u1", personal_team_id=7, membership=membership
    )
    assert membership.calls == []


async def test_team_scope_is_readable_by_a_member() -> None:
    membership = FakeMembership({42})
    assert await can_read_generation_scope(
        {"scope_id": "42"}, user_id="u1", personal_team_id=7, membership=membership
    )
    assert membership.calls == [(42, "u1")]


async def test_team_scope_is_refused_for_a_non_member() -> None:
    assert not await can_read_generation_scope(
        {"scope_id": 42},
        user_id="u1",
        personal_team_id=7,
        membership=FakeMembership(set()),
    )
