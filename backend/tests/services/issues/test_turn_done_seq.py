"""回合结束信号的水位（3b §4）。

两个源：WS ``status{phase:'done'}`` 与 ``useIssueProgress`` 的 ``current_run``。
前端按 seq 丢重复与乱序帧，所以两边都要报得出「这条 run 的 transcript 走到哪
了」。报不出时是 ``None``——「不知道」不能伪装成 0，0 会把最新的一帧当最旧的丢掉。
"""

from unittest.mock import AsyncMock

import pytest

from app.services.issues.issue_rollup import compute_rollup

pytestmark = pytest.mark.unit
ISSUE = {"id": 1, "status": "in_progress", "budget_cents": None}
RUNS = [{"id": 777, "status": "running", "started_at": None, "model": "m"}]


def _sink(published):
    async def _pub(_issue_id, payload):
        published.append(payload)

    return _pub


async def test_done_frame_carries_the_run_and_its_last_seq(monkeypatch):
    import app.services.issues.issue_chat_stream as st

    published: list[dict] = []

    async def _last_seq(run_id):
        assert run_id == 777
        return 42

    async def _keys(run_id):
        assert run_id == 777
        return [{"kind": "script_shot", "ref_id": "9"}]

    monkeypatch.setattr(st, "_publish", _sink(published))
    monkeypatch.setattr(st, "_last_transcript_seq", _last_seq)
    monkeypatch.setattr(st, "_run_output_keys", _keys)

    await st.publish_status(1, "done", run_id="777")

    assert published == [
        {
            "type": "status",
            "phase": "done",
            "run_id": "777",
            "seq": 42,
            "outputs": [{"kind": "script_shot", "ref_id": "9"}],
        }
    ]


async def test_a_frame_without_a_run_still_goes_out(monkeypatch):
    """两个键恒定存在、未知时为 null——有时缺席的字段会被读成 seq 0。"""
    import app.services.issues.issue_chat_stream as st

    published: list[dict] = []
    monkeypatch.setattr(st, "_publish", _sink(published))

    await st.publish_status(1, "running")

    assert published == [
        {
            "type": "status",
            "phase": "running",
            "run_id": None,
            "seq": None,
            "outputs": [],
        }
    ]


def test_current_run_reports_its_last_seq():
    out = compute_rollup(ISSUE, RUNS, [], 0, {}, last_seq=42)
    assert out["current_run"]["last_seq"] == 42


def test_current_run_last_seq_is_none_when_unknown():
    out = compute_rollup(ISSUE, RUNS, [], 0, {})
    assert out["current_run"]["last_seq"] is None


async def test_load_rollup_asks_the_running_run_for_its_watermark(monkeypatch):
    """``compute_rollup`` 的那个参数只有 ``load_rollup`` 会填。它不去读，端点就
    永远报 null——而所有单测照样绿：键一直在，只是永远没有值。所以这条钉的是
    「真的问了那条 running run」，不是「字典里有这个键」。"""
    import app.repositories.agent_run_inbox_repository as inbox_mod
    import app.repositories.agent_runs_repository as runs_mod
    import app.repositories.issue_repository as issue_mod
    import app.services.issues.issue_rollup as rollup

    asked: list = []

    class _Runs:
        async def list_for_issue(self, **_kw):
            return [
                {"id": 777, "status": "running", "started_at": None, "model": "m"},
                {"id": 776, "status": "completed", "started_at": None, "model": "m"},
            ]

        async def last_transcript_seq(self, run_id):
            asked.append(run_id)
            return 42

    class _Inbox:
        async def pending_count(self, **_kw):
            return 0

    monkeypatch.setattr(runs_mod, "get_agent_runs_repository", lambda: _Runs())
    monkeypatch.setattr(inbox_mod, "get_agent_run_inbox_repository", lambda: _Inbox())
    monkeypatch.setattr(
        issue_mod.issue_repository, "list_children", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(rollup, "resolve_origin", AsyncMock(return_value={}))

    out = await rollup.load_rollup(dict(ISSUE, ai_session_id=None))

    # 问的是 running 的那条，不是列表里最新的那条。
    assert asked == [777]
    assert out["current_run"]["last_seq"] == 42


async def test_load_rollup_asks_nobody_when_no_run_is_running(monkeypatch):
    """没有 running run 就没有水位可报——不该白问一次库，更不该编一个 0。"""
    import app.repositories.agent_run_inbox_repository as inbox_mod
    import app.repositories.agent_runs_repository as runs_mod
    import app.repositories.issue_repository as issue_mod
    import app.services.issues.issue_rollup as rollup

    asked: list = []

    class _Runs:
        async def list_for_issue(self, **_kw):
            return [
                {"id": 776, "status": "completed", "started_at": None, "model": "m"}
            ]

        async def last_transcript_seq(self, run_id):
            asked.append(run_id)
            return 42

    class _Inbox:
        async def pending_count(self, **_kw):
            return 0

    monkeypatch.setattr(runs_mod, "get_agent_runs_repository", lambda: _Runs())
    monkeypatch.setattr(inbox_mod, "get_agent_run_inbox_repository", lambda: _Inbox())
    monkeypatch.setattr(
        issue_mod.issue_repository, "list_children", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(rollup, "resolve_origin", AsyncMock(return_value={}))

    out = await rollup.load_rollup(dict(ISSUE, ai_session_id=None))

    assert asked == []
    assert out["current_run"] is None
