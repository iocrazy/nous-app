"""``visible_issue_ids`` — the D6.1 rule, asked about many issues at once.

The per-object lineage reader has a chain that may span several issues and has
to decide, per issue, whether the caller may be handed a link into it. Asking
one issue at a time is two round trips per version; asking once for the whole
chain is the point of this function, so its cost model is pinned here:

* ONE ``get_by_ids`` for every issue in the batch — never one read per id.
* ONE ``is_team_member`` per distinct ``team_id`` inside the batch.
* An empty batch touches the database not at all.
"""

from __future__ import annotations

import importlib

import pytest

mod = importlib.import_module("app.services.issues.issue_visibility")

pytestmark = pytest.mark.unit

ME = "11111111-1111-1111-1111-111111111111"
SOMEONE_ELSE = "22222222-2222-2222-2222-222222222222"
A_THIRD_PARTY = "33333333-3333-3333-3333-333333333333"

MY_TEAM = 424242424242
OTHER_TEAM = 999999999999

A = 348087075560001  # created by me
B = 348087075560002  # someone else's, in a team I belong to
C = 348087075560003  # someone else's, in a team I do not


class _Auth:
    def __init__(self, user_id=ME):
        self.user_id = user_id


class _Repo:
    """Records every read so the "one IN query, one membership check per team"
    claim is an assertion rather than a reading of the implementation.

    ``is_team_member`` answers for ``member_user`` ONLY. Membership is a
    per-user fact in the real repository (``team_members`` is keyed by both
    columns), and a stub that answered by team alone would let a user-blind
    implementation pass — the exact bug the memo key has to avoid.
    """

    def __init__(self, rows, member_of=(MY_TEAM,), member_user=ME):
        self.rows = rows
        self.member_of = set(member_of)
        self.member_user = member_user
        self.batches: list[list] = []
        self.membership_calls: list[tuple] = []

    async def get_by_ids(self, issue_ids):
        wanted = {int(i) for i in issue_ids}
        self.batches.append(sorted(wanted))
        return [r for r in self.rows if int(r["id"]) in wanted]

    async def is_team_member(self, user_id, team_id):
        self.membership_calls.append((user_id, int(team_id)))
        return user_id == self.member_user and int(team_id) in self.member_of


def _issue(issue_id, *, creator=SOMEONE_ELSE, assignee=None, team_id=MY_TEAM):
    return {
        "id": issue_id,
        "created_by_user_id": creator,
        "assignee_user_id": assignee,
        "team_id": team_id,
    }


ROWS = [
    _issue(A, creator=ME, team_id=OTHER_TEAM),
    _issue(B, team_id=MY_TEAM),
    _issue(C, team_id=OTHER_TEAM),
]


@pytest.fixture
def repo(monkeypatch):
    r = _Repo(ROWS)
    monkeypatch.setattr(mod, "issue_repository", r)
    return r


@pytest.mark.asyncio
async def test_each_issue_is_judged_on_its_own_rule(repo):
    """Creator wins (A, despite being in a team I am not in), membership wins
    (B), and neither leaves C out. The result is a set of STRING ids: every
    snowflake on this wire is a string, and the caller compares against the
    stringified ``issue_id`` the deliverables join hands it."""
    got = await mod.visible_issue_ids([A, B, C], _Auth())
    assert got == {str(A), str(B)}


@pytest.mark.asyncio
async def test_the_whole_batch_is_one_read(repo):
    await mod.visible_issue_ids([A, B, C], _Auth())
    assert repo.batches == [sorted([A, B, C])]


@pytest.mark.asyncio
async def test_membership_is_checked_once_per_distinct_team(repo):
    """A and C both sit in ``OTHER_TEAM``; A never reaches the membership
    branch (its creator answers first) and C's answer is remembered, so a chain
    of thirty issues in two teams costs two membership reads, not thirty."""
    rows = [
        _issue(A, team_id=OTHER_TEAM),
        _issue(B, team_id=MY_TEAM),
        _issue(C, team_id=OTHER_TEAM),
    ]
    repo.rows = rows
    got = await mod.visible_issue_ids([A, B, C], _Auth())
    assert got == {str(B)}
    assert sorted(t for _, t in repo.membership_calls) == [MY_TEAM, OTHER_TEAM]


@pytest.mark.asyncio
async def test_an_empty_batch_does_not_touch_the_database(repo):
    assert await mod.visible_issue_ids([], _Auth()) == set()
    assert await mod.visible_issue_ids([None, None], _Auth()) == set()
    assert repo.batches == []
    assert repo.membership_calls == []


@pytest.mark.asyncio
async def test_an_id_with_no_row_is_simply_not_visible(repo):
    """A deleted issue is absent from the read, not an error: the caller's
    answer for it is "no link", the same as for one it may not see."""
    missing = 348087075560999
    got = await mod.visible_issue_ids([A, missing], _Auth())
    assert got == {str(A)}


@pytest.mark.asyncio
async def test_duplicate_ids_are_asked_about_once(repo):
    await mod.visible_issue_ids([A, A, B, str(B)], _Auth())
    assert repo.batches == [sorted([A, B])]


@pytest.mark.asyncio
async def test_the_assignee_sees_an_issue_from_a_team_they_are_not_in(repo):
    repo.rows = [_issue(A, assignee=ME, team_id=OTHER_TEAM)]
    assert await mod.visible_issue_ids([A], _Auth()) == {str(A)}
    assert repo.membership_calls == []


@pytest.mark.asyncio
async def test_a_memo_reused_across_users_answers_each_user_for_themselves(repo):
    """The memo key is ``(user_id, team_id)``, not ``team_id``.

    ``visible_issue_ids`` holds ``user_id`` fixed, but ``team_memo`` is a
    PUBLIC keyword on the sole app-layer enforcement of "team 是铁边界". A memo
    keyed by team alone would hand the second caller the first caller's
    membership answer — silently, with no read to notice — which on this one
    path is a cross-tenant read. So: ME is in MY_TEAM, SOMEONE_ELSE is not, and
    one shared memo must still answer each of them for themselves.
    """
    repo.member_of = {MY_TEAM}
    # Created by neither of them, so both have to reach the membership branch.
    row = _issue(B, creator=A_THIRD_PARTY, team_id=MY_TEAM)
    memo: dict = {}

    assert await mod.is_issue_visible(row, ME, team_memo=memo) is True
    assert await mod.is_issue_visible(row, SOMEONE_ELSE, team_memo=memo) is False

    # The second user's membership was actually queried, not read off the
    # first user's cached answer.
    assert repo.membership_calls == [(ME, MY_TEAM), (SOMEONE_ELSE, MY_TEAM)]
    assert set(memo) == {(ME, MY_TEAM), (SOMEONE_ELSE, MY_TEAM)}

    # And within ONE user the memo still saves the read it exists to save.
    assert await mod.is_issue_visible(row, ME, team_memo=memo) is True
    assert len(repo.membership_calls) == 2


@pytest.mark.asyncio
async def test_a_personal_issue_of_someone_else_is_invisible(repo):
    """``team_id IS NULL`` — there is no team to fall back on, and the
    membership read must not be attempted with a None team."""
    repo.rows = [_issue(A, team_id=None)]
    assert await mod.visible_issue_ids([A], _Auth()) == set()
    assert repo.membership_calls == []
