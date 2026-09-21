"""议题线程的「◇ n」取整棵 run 树的合计，不是 root 那一条流水（3c 终审 I2）。

``list_for_issue`` 回的是 root-only 的行（``parent_run_id IS NULL``），而扣费逐 run
发生。真栈实测一次回合 6 条 consume、余额 −6，而 root 那条是 −1。

这里让**真的** ``charged_points_for_run_trees`` 跑起来（只桩两个仓库），所以它钉的是
整条链而不是一次转发。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import app.services.billing.run_tree_points as tree_mod
from app.services.issues.issue_rollup import load_rollup

pytestmark = pytest.mark.unit

_ROOT = 700
_ISSUE = {"id": 5, "status": "in_progress", "ai_session_id": None}


class _Runs:
    """只回 root 行——与 ``list_for_issue`` 的真实形状一致。"""

    async def list_for_issue(self, *, issue_id, conversation_id=None):
        return [{"id": _ROOT, "status": "completed", "cost_cents": 0.92}]

    async def run_ids_in_trees(self, root_ids):
        assert root_ids == [_ROOT]
        return {"700": ["700", "701", "702"]}

    async def efficiency_for_issue(self, issue_id):
        return {}

    async def own_cost_cents_for_issue_runs(self, issue_id):
        return 0.92

    async def last_transcript_seq(self, run_id):
        return None


class _Points:
    async def charged_points_for_references(self, *, reference_type, reference_ids):
        assert sorted(reference_ids) == ["700", "701", "702"]
        return {"700": 1.0, "701": 3.0, "702": 2.0}


class _BrokenRuns(_Runs):
    async def run_ids_in_trees(self, root_ids):
        raise RuntimeError("db down")


def _wire(monkeypatch, runs):
    import app.repositories.agent_run_inbox_repository as inbox_mod
    import app.repositories.agent_runs_repository as runs_mod
    import app.repositories.issue_repository as issue_mod
    from app.services.issues import issue_rollup as mod

    class _Inbox:
        async def pending_count(self, *, target_kind, target_id):
            return 0

    monkeypatch.setattr(runs_mod, "get_agent_runs_repository", lambda: runs)
    monkeypatch.setattr(inbox_mod, "get_agent_run_inbox_repository", lambda: _Inbox())
    monkeypatch.setattr(issue_mod.issue_repository, "list_children", _no_children)
    monkeypatch.setattr(mod, "resolve_origin", _no_origin)
    return runs


async def _no_children(issue_id):
    return []


async def _no_origin(issue):
    return None


async def test_the_bubble_shows_what_the_whole_tree_cost(monkeypatch):
    runs = _wire(monkeypatch, _Runs())
    with (
        patch.object(tree_mod, "get_agent_runs_repository", lambda: runs),
        patch.object(tree_mod, "get_points_repository", lambda: _Points()),
    ):
        out = await load_rollup(dict(_ISSUE))
    assert out["runs"][0]["charged_points"] == 6.0


async def test_a_points_read_failure_only_empties_this_one_field(monkeypatch):
    """驾驶舱是被轮询的：一个字段读不到不该把进度、子议题、收件箱计数一起带走。"""
    runs = _wire(monkeypatch, _BrokenRuns())
    with patch.object(tree_mod, "get_agent_runs_repository", lambda: runs):
        out = await load_rollup(dict(_ISSUE))
    assert out["runs"][0]["charged_points"] is None
    assert out["runs"][0]["cost_cents"] == 0.92
