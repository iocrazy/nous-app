"""Who may continue a background sub-agent (``child_run_id``).

Task 7b defect D. Ownership was ANCESTRY only: the run asking to continue had
to be an ancestor of the child. A background child by definition comes back
AFTER the turn that spawned it has ended, so the natural way to continue it —
the user's next comment on the same issue — asks from a SIBLING run, and every
attempt was refused with ``not_your_child``. The 2026-09-10 re-verification
watched it happen: child ``…286155642`` hangs off run ``…207487810`` (turn 1),
while the continuing turn was run ``…858678313``; one hop up from the child
finds ``…207487810`` with no parent and no fork, so the walk ends False.

Ownership is now ancestry OR same-issue. A conversation-scoped run has no
issue to compare, so it keeps the ancestor rule alone, and a child belonging to
a DIFFERENT issue is still refused — the id comes from the model and this is
the guard that keeps it from reading a stranger's transcript.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.services.ai.runner.subagent_task_service import SubAgentTaskService

pytestmark = pytest.mark.unit


class _Row:
    """``(parent_run_id, fork_of_run_id, issue_id)`` as SQLAlchemy hands it
    back — indexable, which is how the walker reads it."""

    def __init__(self, parent, fork, issue):
        self._t = (parent, fork, issue)

    def __getitem__(self, i):
        return self._t[i]


class _Session:
    def __init__(self, rows: dict[int, Optional[_Row]], seen: list[int]):
        self._rows, self._seen = rows, seen

    async def execute(self, stmt):
        # The walker's only variable is the id it is looking up; pull it out of
        # the compiled binds rather than re-implementing the statement.
        params = stmt.compile().params
        rid = next(
            int(v) for v in params.values() if isinstance(v, (int, str)) and str(v)
        )
        self._seen.append(rid)
        row = self._rows.get(rid)
        return SimpleNamespace(first=lambda: row)


class _Scope:
    def __init__(self, session):
        self._s = session

    async def __aenter__(self):
        return self._s

    async def __aexit__(self, *exc):
        return False


def _service(*, run_id: Any, issue_id: Optional[int]) -> SubAgentTaskService:
    """A service whose ACTIVE run is ``run_id`` on ``issue_id`` — the shape the
    chat wiring builds for a turn (the recorder carries both)."""
    return SubAgentTaskService(
        caller_agent_id=uuid4(),
        caller_user_id=uuid4(),
        parent_run_id=None,
        parent_recorder=SimpleNamespace(run_id=run_id, issue_id=issue_id),
        issue_id=issue_id,
    )


async def _ask(svc, child_run_id: str, rows: dict[int, Optional[_Row]]):
    seen: list[int] = []
    session = _Session(rows, seen)
    with patch(
        "app.db.session.read_scope",
        lambda *a, **k: _Scope(session),
    ):
        return await svc._child_chain_ok(child_run_id), seen


# ── arm 1: the ancestor rule, unchanged ────────────────────────────────


async def test_a_direct_child_of_the_running_turn_is_still_ok():
    svc = _service(run_id=900, issue_id=7)
    ok, _ = await _ask(svc, "51", {51: _Row(900, None, 7)})
    assert ok is True


async def test_a_forked_round_still_walks_up_to_its_ancestor():
    """A continued round hangs off the round before it, not off the parent."""
    svc = _service(run_id=900, issue_id=None)
    ok, seen = await _ask(
        svc, "52", {52: _Row(None, 51, None), 51: _Row(900, None, None)}
    )
    assert ok is True
    assert seen == [52, 51], "the second hop was never taken"


# ── arm 2: same issue, a later turn ────────────────────────────────────


async def test_a_sibling_run_on_the_same_issue_may_continue_the_child():
    """The defect itself. The child hangs off turn 1; the request comes from
    turn 2. They are siblings, so the walk finds nothing — but they are the
    same issue's work, and a background child cannot be continued any other
    way."""
    svc = _service(run_id=858678313, issue_id=348057134767409)
    ok, _ = await _ask(
        svc,
        "286155642",
        {286155642: _Row(207487810, None, 348057134767409), 207487810: None},
    )
    assert ok is True


async def test_the_same_issue_arm_reads_the_child_row_only_once():
    """It is answered from the child's own row, so no chain walk is spent on
    a question the first read already settled."""
    svc = _service(run_id=858678313, issue_id=348057134767409)
    ok, seen = await _ask(
        svc,
        "286155642",
        {286155642: _Row(207487810, None, 348057134767409)},
    )
    assert ok is True and seen == [286155642]


# ── the refusals that must survive ─────────────────────────────────────


async def test_a_child_of_another_issue_is_refused():
    svc = _service(run_id=858678313, issue_id=348057134767409)
    ok, _ = await _ask(svc, "77", {77: _Row(None, None, 999999999999), 0: None})
    assert ok is False


async def test_a_child_with_no_issue_is_not_adopted_by_an_issue_run():
    """A NULL ``issue_id`` on the child is an unanswered question, not a
    match — reading it as one would hand every conversation-scoped run's
    transcript to any issue that asked."""
    svc = _service(run_id=858678313, issue_id=348057134767409)
    ok, _ = await _ask(svc, "77", {77: _Row(None, None, None)})
    assert ok is False


async def test_a_conversation_scoped_run_keeps_the_ancestor_rule_alone():
    """No issue on either side, so there is nothing for the second arm to
    compare and the walk is the whole answer."""
    svc = _service(run_id=900, issue_id=None)
    ok, _ = await _ask(svc, "77", {77: _Row(None, None, None)})
    assert ok is False


async def test_an_unknown_child_run_is_refused():
    svc = _service(run_id=900, issue_id=7)
    ok, _ = await _ask(svc, "77", {})
    assert ok is False


async def test_a_run_with_neither_a_parent_nor_an_issue_refuses_outright():
    """Nothing to compare against on either arm. It must not fall through to
    a walk whose ``target`` is None — a child whose ``parent_run_id`` is NULL
    would then match it."""
    svc = SubAgentTaskService(
        caller_agent_id=uuid4(),
        caller_user_id=uuid4(),
        parent_run_id=None,
    )
    ok, seen = await _ask(svc, "77", {77: _Row(None, None, None)})
    assert ok is False and seen == []
