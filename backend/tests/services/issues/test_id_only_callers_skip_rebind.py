"""The two id-only callers of get_or_create_issue_session must not be blocked
by the rebind (FH2 T5 review M1).

The subissue barrier's report-only branch and the inbox delivery only need the
session id; before this, an unresolvable assignee made the real function raise
IssueAssigneeNotFound and both callers swallowed it — the barrier roll-up was
lost and the inbox decided with no session. Both run the REAL function over
the fake ORM session from tests/test_issue_session.py.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from tests.test_issue_session import (
    _existing_row,
    _FakeSession,
    _patch_scopes,
    _spy_agent_repo,
)

pytestmark = pytest.mark.unit

SID = "315917457926636"


def _unresolvable(monkeypatch):
    from app.services.issues import issue_session as m

    session = _FakeSession(
        select_row=_existing_row(SID, uuid4()),
        meta_row={"agent_id": uuid4(), "agent_slug": "script_ai"},
    )
    _patch_scopes(monkeypatch, session)
    return _spy_agent_repo(monkeypatch, m, None)


async def test_barrier_report_gets_the_session_despite_unresolvable_assignee(
    monkeypatch,
):
    from app.services.issues.subissue_barrier import BarrierGateway

    repo = _unresolvable(monkeypatch)
    assert await BarrierGateway().ensure_session(409) == SID
    repo.get_by_id.assert_not_awaited()


async def test_inbox_delivery_gets_the_session_despite_unresolvable_assignee(
    monkeypatch,
):
    from app.services.issues.inbox_or_dispatch import _session_id

    repo = _unresolvable(monkeypatch)
    assert await _session_id(409) == SID
    repo.get_by_id.assert_not_awaited()
