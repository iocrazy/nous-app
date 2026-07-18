"""Sub-issue completion barrier (multica fan-in wake port).

Service-layer unit tests: all I/O is faked through a ``BarrierGateway`` stand-in
(mirrors the injected-deps style of ``test_project_stage_auto_issue`` /
``issue_lifecycle._run_dispatch_with_continuation``). No DB, no DBOS.

Coverage map (v2 contract):
  * transition guard re-entry — a repeat-save at the same status never fires
  * barrier open → complete silence (no per-child chatter)
  * barrier closed → report + wake
  * two children racing the close compute the SAME pinned wf id
  * the three wake guards each fall back to report-only
  * a cancelled child is labelled truthfully in the roll-up
  * reopen → re-complete recomputes the same pinned id (DBOS absorbs it)
  * a hook exception is swallowed, never aborting the child transition
"""

from __future__ import annotations

import pytest

from app.services.issues.subissue_barrier import (
    CHILD_EXCERPT_CAP,
    TOTAL_REPORT_CAP,
    BarrierGateway,
    barrier_key,
    build_report_body,
    is_terminal,
    on_child_issue_terminal,
    should_wake,
)

_OWNER = "00000000-0000-0000-0000-000000000001"
_AGENT = "11111111-1111-1111-1111-111111111111"


# ── fake gateway ──────────────────────────────────────────────────────────────


class _FakeGateway(BarrierGateway):
    def __init__(
        self,
        *,
        issues,
        children,
        last_messages=None,
        reported=False,
        dbos_enabled=True,
        session_ids=None,
        raise_on_get=False,
    ):
        self.issues = {int(k): v for k, v in issues.items()}
        self.children = {int(k): list(v) for k, v in children.items()}
        self.last_messages = last_messages or {}
        self._reported = reported
        self._dbos = dbos_enabled
        self.session_ids = {int(k): v for k, v in (session_ids or {}).items()}
        self._raise_on_get = raise_on_get
        self.session_writes = []
        self.im_writes = []
        self.wakes = []
        self.ensured = []

    async def get_issue(self, issue_id):
        if self._raise_on_get:
            raise RuntimeError("db down")
        return self.issues.get(int(issue_id))

    async def list_children(self, parent_id):
        return list(self.children.get(int(parent_id), []))

    async def last_substantive_message(self, issue_row):
        return self.last_messages.get(int(issue_row.get("id")), "")

    async def already_reported(self, parent_row, key):
        return self._reported

    async def ensure_session(self, issue_id):
        self.ensured.append(int(issue_id))
        return self.session_ids.get(int(issue_id))

    async def write_report_to_session(self, *, session_id, owner_id, body, key):
        self.session_writes.append(
            {"session_id": session_id, "owner_id": owner_id, "body": body, "key": key}
        )

    async def write_report_to_issue_messages(self, *, issue_id, owner_id, body, key):
        self.im_writes.append(
            {"issue_id": issue_id, "owner_id": owner_id, "body": body, "key": key}
        )

    async def dbos_enabled(self):
        return self._dbos

    async def dispatch_wake(self, *, parent_id, owner_id, body, key):
        self.wakes.append(
            {"parent_id": parent_id, "owner_id": owner_id, "body": body, "key": key}
        )


def _child(cid, *, status, parent_id=1, identifier=None, title=None):
    return {
        "id": cid,
        "parent_id": parent_id,
        "status": status,
        "identifier": identifier or f"MH-{cid}",
        "title": title or f"Child {cid}",
        "ai_session_id": None,
    }


def _parent(**over):
    base = {
        "id": 1,
        "parent_id": None,
        "status": "in_progress",
        "assignee_agent_id": _AGENT,
        "created_by_user_id": _OWNER,
        "assignee_user_id": None,
        "ai_session_id": 500,
    }
    base.update(over)
    return base


def _gw(parent, children, **kw):
    issues = {1: parent}
    for c in children:
        issues[c["id"]] = c
    return _FakeGateway(issues=issues, children={1: children}, **kw)


# ── pure helpers ──────────────────────────────────────────────────────────────


def test_barrier_key_is_stable_and_race_identical():
    assert barrier_key(1, 2) == "subwake:1:2"
    # Two children closing the same 2-child barrier derive the SAME id.
    assert barrier_key(1, 2) == barrier_key(1, 2)
    # Different child count → different barrier (a new child re-opens the fan-in).
    assert barrier_key(1, 3) != barrier_key(1, 2)


def test_is_terminal():
    assert is_terminal("done") and is_terminal("cancelled")
    assert not is_terminal("in_progress")
    assert not is_terminal(None)


def test_should_wake_guards():
    assert should_wake(_parent(status="in_progress"))
    assert should_wake(_parent(status="todo"))
    assert not should_wake(_parent(assignee_agent_id=None))
    assert not should_wake(_parent(status="done"))
    assert not should_wake(_parent(status="cancelled"))
    assert not should_wake(_parent(status="backlog"))


def test_build_report_caps():
    long = "x" * 2000
    body = build_report_body(
        [{"identifier": "MH-9", "title": "T", "status": "done", "last_message": long}]
    )
    # Per-child excerpt is capped.
    assert "x" * CHILD_EXCERPT_CAP not in body  # would exceed the cap
    assert "…" in body
    # Grand-total cap holds even with many big children.
    big = [
        {
            "identifier": f"MH-{i}",
            "title": "T",
            "status": "done",
            "last_message": "y" * CHILD_EXCERPT_CAP,
        }
        for i in range(50)
    ]
    assert len(build_report_body(big)) <= TOTAL_REPORT_CAP + 64  # header slack


# ── gate 1: transition re-entry ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_repeat_save_terminal_to_terminal_does_not_fire():
    gw = _gw(_parent(), [_child(10, status="done"), _child(11, status="done")])
    res = await on_child_issue_terminal(10, "done", "done", gateway=gw)
    assert res["fired"] is False
    assert res["reason"] == "prev_already_terminal"
    assert gw.wakes == [] and gw.session_writes == [] and gw.im_writes == []


@pytest.mark.asyncio
async def test_non_terminal_new_status_does_not_fire():
    gw = _gw(_parent(), [_child(10, status="in_progress")])
    res = await on_child_issue_terminal(10, "todo", "in_progress", gateway=gw)
    assert res == {"fired": False, "reason": "new_not_terminal"}


@pytest.mark.asyncio
async def test_child_without_parent_is_silent():
    orphan = _child(10, status="done", parent_id=None)
    gw = _FakeGateway(issues={10: orphan}, children={})
    res = await on_child_issue_terminal(10, "in_progress", "done", gateway=gw)
    assert res["reason"] == "no_parent"
    assert gw.wakes == [] and gw.im_writes == []


# ── gate 3: barrier open → silent ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_barrier_open_is_completely_silent():
    gw = _gw(
        _parent(),
        [_child(10, status="done"), _child(11, status="in_progress")],
        last_messages={10: "did A"},
    )
    res = await on_child_issue_terminal(10, "in_progress", "done", gateway=gw)
    assert res == {"fired": False, "reason": "barrier_open"}
    # No per-child chatter while siblings are still running.
    assert gw.wakes == [] and gw.session_writes == [] and gw.im_writes == []


# ── barrier closed → report + wake ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_barrier_closed_reports_and_wakes():
    gw = _gw(
        _parent(status="in_progress"),
        [
            _child(10, status="done", identifier="MH-10", title="Alpha"),
            _child(11, status="done", identifier="MH-11", title="Beta"),
        ],
        last_messages={10: "finished alpha", 11: "finished beta"},
    )
    res = await on_child_issue_terminal(10, "in_progress", "done", gateway=gw)
    assert res["fired"] is True and res["wake"] is True
    assert res["barrier_key"] == "subwake:1:2"
    assert len(gw.wakes) == 1
    wake = gw.wakes[0]
    assert wake["parent_id"] == 1 and wake["owner_id"] == _OWNER
    assert wake["key"] == "subwake:1:2"
    # Roll-up carries every child's identifier + title + last message.
    for token in ("MH-10", "Alpha", "finished alpha", "MH-11", "Beta", "finished beta"):
        assert token in wake["body"]
    # Wake path does not also double-write a report row.
    assert gw.session_writes == [] and gw.im_writes == []


# ── the three wake guards → report-only ───────────────────────────────────────


@pytest.mark.asyncio
async def test_no_agent_parent_reports_to_issue_messages_no_wake():
    gw = _gw(
        _parent(assignee_agent_id=None, ai_session_id=None),
        [_child(10, status="done"), _child(11, status="done")],
        last_messages={10: "a", 11: "b"},
    )
    res = await on_child_issue_terminal(11, "in_progress", "done", gateway=gw)
    assert res["fired"] is True and res["wake"] is False
    assert res["reason"] == "reported_issue_messages"
    assert gw.wakes == []
    assert len(gw.im_writes) == 1
    assert gw.im_writes[0]["issue_id"] == 1
    assert gw.im_writes[0]["owner_id"] == _OWNER  # author satisfies the CHECK
    assert gw.im_writes[0]["key"] == "subwake:1:2"


@pytest.mark.asyncio
async def test_terminal_parent_reports_to_session_no_wake():
    gw = _gw(
        _parent(status="done", ai_session_id=500),
        [_child(10, status="done"), _child(11, status="cancelled")],
        session_ids={1: "500"},
    )
    res = await on_child_issue_terminal(10, "in_progress", "done", gateway=gw)
    assert res["fired"] is True and res["wake"] is False
    assert res["reason"] == "reported_session_no_wake"
    assert gw.wakes == [] and gw.im_writes == []
    assert len(gw.session_writes) == 1
    assert gw.session_writes[0]["session_id"] == 500


@pytest.mark.asyncio
async def test_backlog_parent_reports_to_session_no_wake():
    gw = _gw(
        _parent(status="backlog", ai_session_id=500),
        [_child(10, status="done"), _child(11, status="done")],
        session_ids={1: "500"},
    )
    res = await on_child_issue_terminal(10, "in_progress", "done", gateway=gw)
    assert res["reason"] == "reported_session_no_wake"
    assert gw.wakes == [] and len(gw.session_writes) == 1


@pytest.mark.asyncio
async def test_dbos_disabled_agent_parent_falls_back_to_report_only():
    # Active agent parent, but DBOS is off → cannot wake; must still report.
    gw = _gw(
        _parent(status="in_progress", ai_session_id=500),
        [_child(10, status="done"), _child(11, status="done")],
        session_ids={1: "500"},
        dbos_enabled=False,
    )
    res = await on_child_issue_terminal(10, "in_progress", "done", gateway=gw)
    assert res["wake"] is False and gw.wakes == []
    assert len(gw.session_writes) == 1


# ── cancelled child labelled truthfully ───────────────────────────────────────


@pytest.mark.asyncio
async def test_cancelled_child_labelled_in_report():
    gw = _gw(
        _parent(assignee_agent_id=None, ai_session_id=None),
        [
            _child(10, status="done", identifier="MH-10"),
            _child(11, status="cancelled", identifier="MH-11"),
        ],
        last_messages={10: "ok", 11: "user cancelled"},
    )
    await on_child_issue_terminal(10, "in_progress", "done", gateway=gw)
    body = gw.im_writes[0]["body"]
    assert "[cancelled]" in body and "MH-11" in body
    assert "[done]" in body


# ── idempotency: race + reopen ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_two_children_race_compute_same_wf_id():
    children = [_child(10, status="done"), _child(11, status="done")]
    # Both children observe the fully-terminal set and both fire (the race).
    gw = _gw(_parent(status="in_progress"), children, last_messages={10: "a", 11: "b"})
    r10 = await on_child_issue_terminal(10, "in_progress", "done", gateway=gw)
    r11 = await on_child_issue_terminal(11, "in_progress", "done", gateway=gw)
    # Identical pinned wf id → DBOS de-dups the actual dispatch to one run.
    assert r10["barrier_key"] == r11["barrier_key"] == "subwake:1:2"
    assert gw.wakes[0]["key"] == gw.wakes[1]["key"]


@pytest.mark.asyncio
async def test_reopen_then_recomplete_recomputes_same_wf_id():
    children = [_child(10, status="done"), _child(11, status="done")]
    gw = _gw(_parent(status="in_progress"), children, last_messages={10: "a", 11: "b"})
    first = await on_child_issue_terminal(10, "in_progress", "done", gateway=gw)
    # Child 10 reopened (done→in_progress: non-terminal, no fire) then completed
    # again. The sibling count is unchanged → the same pinned id → DBOS absorbs.
    reopened = await on_child_issue_terminal(10, "done", "in_progress", gateway=gw)
    assert reopened["reason"] == "new_not_terminal"
    second = await on_child_issue_terminal(10, "in_progress", "done", gateway=gw)
    assert first["barrier_key"] == second["barrier_key"] == "subwake:1:2"


@pytest.mark.asyncio
async def test_already_reported_marker_skips_duplicate_report():
    gw = _gw(
        _parent(assignee_agent_id=None, ai_session_id=None),
        [_child(10, status="done"), _child(11, status="done")],
        reported=True,
    )
    res = await on_child_issue_terminal(10, "in_progress", "done", gateway=gw)
    assert res["fired"] is False and res["reason"] == "already_reported"
    assert gw.im_writes == []


# ── best-effort: never raises ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gateway_exception_is_swallowed():
    gw = _FakeGateway(issues={}, children={}, raise_on_get=True)
    res = await on_child_issue_terminal(10, "in_progress", "done", gateway=gw)
    assert res["fired"] is False and res["reason"] == "error"
