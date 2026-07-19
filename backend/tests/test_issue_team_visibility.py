"""D6.1 team-visibility folding — the three faces of the 铁边界 contract.

用户立约 (2026-07-19): a team's members see the team's issues; other teams
never leak; own/assigned issues stay visible regardless of membership.
"""

import pytest
from fastapi import HTTPException

from app.api.issue_messages_router import _assert_issue_visible
from app.api.issues_router import _assert_visibility
from app.repositories.issue_repository import issue_repository, visibility_predicate


class _Auth:
    def __init__(self, user_id: str):
        self.user_id = user_id


_ME = "11111111-1111-4111-8111-111111111111"
_OTHER = "22222222-2222-4222-8222-222222222222"


def _row(**over):
    return {
        "id": 1,
        "created_by_user_id": _OTHER,
        "assignee_user_id": None,
        "team_id": 42,
        **over,
    }


class TestVisibilityPredicate:
    def test_sql_folds_team_membership_subquery(self):
        sql = str(
            visibility_predicate(_ME).compile(compile_kwargs={"literal_binds": False})
        )
        # The three faces, verbatim in the WHERE clause.
        assert "created_by_user_id" in sql
        assert "assignee_user_id" in sql
        assert "team_members" in sql  # membership subquery — the fold itself
        assert "IN" in sql


@pytest.mark.asyncio
class TestAssertVisibility:
    async def _patch_membership(self, monkeypatch, _router, result: bool):
        # Both routers import the SAME repository singleton — patch it once.
        async def fake(user_id, team_id):
            fake.calls.append((user_id, team_id))
            return result

        fake.calls = []
        monkeypatch.setattr(issue_repository, "is_team_member", fake)
        return fake

    async def test_creator_visible_without_membership(self, monkeypatch):
        fake = await self._patch_membership(monkeypatch, None, False)
        await _assert_visibility(_row(created_by_user_id=_ME), _Auth(_ME))
        assert fake.calls == []  # membership never even consulted

    async def test_same_team_member_visible(self, monkeypatch):
        fake = await self._patch_membership(monkeypatch, None, True)
        await _assert_visibility(_row(), _Auth(_ME))
        assert fake.calls == [(_ME, 42)]

    async def test_other_team_gets_404_not_403(self, monkeypatch):
        await self._patch_membership(monkeypatch, None, False)
        with pytest.raises(HTTPException) as exc:
            await _assert_visibility(_row(), _Auth(_ME))
        assert exc.value.status_code == 404  # never leak existence

    async def test_teamless_issue_stays_private(self, monkeypatch):
        fake = await self._patch_membership(monkeypatch, None, True)
        with pytest.raises(HTTPException):
            await _assert_visibility(_row(team_id=None), _Auth(_ME))
        assert fake.calls == []  # no team → nothing to fold, membership unused


@pytest.mark.asyncio
class TestAssertIssueVisibleMessagesSide:
    async def test_same_team_member_reads_the_thread(self, monkeypatch):
        async def fake_get(issue_id):
            return _row()

        async def fake_member(user_id, team_id):
            return True

        monkeypatch.setattr(issue_repository, "get_by_id", fake_get)
        monkeypatch.setattr(issue_repository, "is_team_member", fake_member)
        row = await _assert_issue_visible(1, _Auth(_ME))
        assert row["team_id"] == 42

    async def test_other_team_thread_404(self, monkeypatch):
        async def fake_get(issue_id):
            return _row()

        async def fake_member(user_id, team_id):
            return False

        monkeypatch.setattr(issue_repository, "get_by_id", fake_get)
        monkeypatch.setattr(issue_repository, "is_team_member", fake_member)
        with pytest.raises(HTTPException) as exc:
            await _assert_issue_visible(1, _Auth(_ME))
        assert exc.value.status_code == 404
