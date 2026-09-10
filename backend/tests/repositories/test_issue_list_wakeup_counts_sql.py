"""``IssueRepository.list_for_user``'s pending-wake-up aggregate — the compiled
SQL and the shape it folds onto every item (harness 2b-2 Task 6).

Runs everywhere (stubbed session). The DB-backed proof that Postgres accepts
the statement lives in ``test_issue_list_pending_wakeups.py``; this file pins
the two things a stub CAN see and that a person would get wrong:

  * ``payload->>'issue_id'`` yields TEXT, so the ids go in as **strings** —
    a bigint bind compiles happily and matches nothing;
  * the field lands on EVERY item, including the ones with no wake-up, so the
    client never has to distinguish "0" from "not folded".
"""

from __future__ import annotations

import contextlib
import uuid
from unittest.mock import patch

import pytest

from app.repositories.issue_repository import IssueRepository

pytestmark = pytest.mark.unit


def _Issue(issue_id: int):
    """A whole Issues ORM object — ``_row`` folds every mapped column, so a
    hand-picked subset would fail on the first field nobody thought of."""
    from app.models import Issues

    return Issues(id=issue_id, title=f"Issue {issue_id}")


class _Session:
    """First execute() = the page, second = the wake-up aggregate."""

    def __init__(self, issues, counts):
        self.stmts: list = []
        self._issues = issues
        self._counts = counts

    async def scalar(self, stmt):
        self.stmts.append(stmt)
        return len(self._issues)

    async def execute(self, stmt):
        self.stmts.append(stmt)
        issues, counts = self._issues, self._counts

        class _R:
            def scalars(self_inner):
                class _S:
                    def all(self_s):
                        return issues

                return _S()

            def all(self_inner):
                return counts

        return _R()


def _scope(sess):
    @contextlib.asynccontextmanager
    async def _cm():
        yield sess

    return _cm


async def _list(issues, counts):
    sess = _Session(issues, counts)
    with patch("app.repositories.issue_repository.read_scope", _scope(sess)):
        items, total = await IssueRepository().list_for_user(str(uuid.uuid4()))
    return sess, items, total


async def test_every_item_carries_a_pending_wakeups_count():
    _sess, items, _total = await _list([_Issue(1), _Issue(2)], [(1, 3)])
    by_id = {i["id"]: i for i in items}
    assert by_id[1]["pending_wakeups"] == 3
    # Not folded ≠ absent: the issue with no wake-up still gets the key.
    assert by_id[2]["pending_wakeups"] == 0


async def test_ids_are_bound_as_text_because_the_json_operator_yields_text():
    sess, _items, _total = await _list([_Issue(11), _Issue(22)], [])
    agg = sess.stmts[-1]
    sql = str(agg)
    assert "user_schedules" in sql
    assert "issue_wakeup" in agg.compile().params.values()
    bound_ids = [
        v
        for v in agg.compile().params.values()
        if isinstance(v, (list, tuple)) and v and isinstance(v[0], str)
    ]
    assert bound_ids, f"expected string-bound issue ids, got {agg.compile().params}"
    assert sorted(bound_ids[0]) == ["11", "22"]


async def test_no_aggregate_is_issued_for_an_empty_page():
    sess, items, _total = await _list([], [])
    assert items == []
    # count + page only — an IN () over nothing is a query for nothing.
    assert not any("user_schedules" in str(s) for s in sess.stmts)
