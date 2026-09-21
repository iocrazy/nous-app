"""Review I3: the view and the column must name the same number.

``issue_rollup._run_cents`` reads ``metadata_json.cost.spent_cents`` while a
run is running and ``agent_runs.cost_cents`` once it has finished. Before this
fix a sub-agent's spend lived only in the view, so an issue's ``spent_cents``
DROPPED by the children's cost the moment the parent run completed — a spend
gauge that walks backwards, feeding the budget gate.
"""

from __future__ import annotations

import pytest

from app.services.ai.runner import run_projection as rp
from app.services.issues.issue_rollup import _run_cents

pytestmark = pytest.mark.unit


def _views_with(own: float, children: dict[str, float]):
    views = rp.empty_views()
    if own:
        views = rp.apply(
            views,
            "step_end",
            {"turn": 1, "step": 1, "cost_cents": own, "model": "m"},
        )
    for cid, cents in children.items():
        views = rp.apply(
            views,
            "subagent_done",
            {
                "child_run_id": cid,
                "mode": "async",
                "status": "success",
                "cost_cents": cents,
            },
        )
    return views


def test_spent_is_own_plus_children():
    views = _views_with(10.0, {"51": 3.0, "52": 2.0})
    assert views["cost"]["own_cents"] == 10.0
    assert views["cost"]["by_child"] == {"51": 3.0, "52": 2.0}
    assert views["cost"]["spent_cents"] == 15.0


def test_a_repeated_done_for_the_same_child_does_not_double_add():
    """The worker writes ``subagent_done`` onto the parent run, and a DBOS
    step can replay. Adding on every arrival would inflate the parent's cost
    once per retry."""
    views = _views_with(10.0, {"51": 3.0})
    again = rp.apply(
        views,
        "subagent_done",
        {"child_run_id": "51", "mode": "async", "status": "success", "cost_cents": 3.0},
    )
    assert again["cost"]["by_child"] == {"51": 3.0}
    assert again["cost"]["spent_cents"] == 13.0


def test_own_cost_still_drives_the_budget_percentage():
    views = rp.empty_views()
    views["cost"]["budget_cents"] = 100.0
    views = rp.apply(
        views, "step_end", {"turn": 1, "step": 1, "cost_cents": 10.0, "model": "m"}
    )
    views = rp.apply(
        views,
        "subagent_done",
        {"child_run_id": "51", "mode": "async", "status": "ok", "cost_cents": 5.0},
    )
    assert views["cost"]["spent_cents"] == 15.0
    assert views["cost"]["pct"] == 15


def test_the_rollup_reads_the_same_number_before_and_after_completion():
    """The whole point of I3, asserted through the real consumer."""
    views = _views_with(10.0, {"51": 3.0, "52": 2.0})
    running = {"status": "running", "metadata_json": {"cost": views["cost"]}}
    # What RunRecorder._finish must now write into the column.
    finished = {
        "status": "completed",
        "metadata_json": {"cost": views["cost"]},
        "cost_cents": views["cost"]["spent_cents"],
    }
    assert _run_cents(running) == _run_cents(finished) == 15.0


# ─── the column side ─────────────────────────────────────────────────


async def test_finish_writes_own_plus_children_into_the_column(monkeypatch):
    """``compute_cost_cents`` only ever knew this run's own tokens. A parent
    that spent 10 and whose children spent 5 used to store 10 and show 15."""
    import contextlib

    from app.db import session as dbs
    from app.services.ai.runner import run_recorder as rr

    captured: dict = {}

    class _S:
        async def execute(self, stmt, *a, **k):
            compiled = stmt.compile()
            captured.setdefault("values", {}).update(
                {str(k): v for k, v in (compiled.params or {}).items()}
            )
            return None

    @contextlib.asynccontextmanager
    async def _ws():
        yield _S()

    monkeypatch.setattr(dbs, "write_scope", _ws)

    rec = rr.RunRecorder(agent_id=None, user_id=None, trigger="t")
    rec.run_id = "7"
    rec._prompt_rate = 1.0
    rec._completion_rate = 1.0
    rec.record_usage(prompt_tokens=5000, completion_tokens=5000)  # → 10.0 cents
    writer = rec._writer()
    writer.views["cost"]["by_child"] = {"51": 3.0, "52": 2.0}
    writer._mirror = _noop_mirror()

    await rec._finish(status="completed")

    assert captured["values"]["cost_cents"] == 15.0
    # The view must land on the same number, or the rollup still steps at
    # completion — just in the other direction.
    assert writer.views["cost"]["spent_cents"] == 15.0
    assert writer.views["cost"]["own_cents"] == 10.0


def _noop_mirror():
    from unittest.mock import AsyncMock

    return AsyncMock()


async def test_a_late_child_done_rewrites_the_finished_parents_column(monkeypatch):
    """The background ``subagent_done`` lands on a parent that finished turns
    ago. Folding it into a view nobody reads any more is not enough — the
    column is what the rollup reads for an ended run."""
    import contextlib
    from unittest.mock import AsyncMock

    from app.db import session as dbs
    from app.services.ai.runner import run_recorder as rr

    seen: list = []

    class _S:
        async def execute(self, stmt, *a, **k):
            seen.append(stmt)
            return None

    @contextlib.asynccontextmanager
    async def _ws():
        yield _S()

    monkeypatch.setattr(dbs, "write_scope", _ws)

    writer = rr.RunEventWriter(7, seq_start=3)
    writer.views["cost"]["own_cents"] = 10.0
    writer._mirror = AsyncMock()

    await writer.append(
        "subagent_done",
        {"child_run_id": "51", "mode": "async", "status": "success", "cost_cents": 4.0},
    )

    assert writer.views["cost"]["spent_cents"] == 14.0
    cost_updates = [s for s in seen if "cost_cents" in str(s.compile().params or {})]
    assert cost_updates, "no UPDATE carried the recomputed cost_cents"
    assert float(cost_updates[-1].compile().params["cost_cents"]) == 14.0


async def test_for_run_seeds_own_cents_from_a_pre_change_runs_total(monkeypatch):
    """A run finished before ``own_cents`` existed stored a ``spent_cents``
    that WAS its own (no fold could add a child then). Reading it as 0 would
    let the next late child done overwrite the column with children only."""
    import contextlib

    from app.db import session as dbs
    from app.services.ai.runner import run_recorder as rr

    class _Res:
        def __init__(self, v):
            self._v = v

        def scalar_one_or_none(self):
            return self._v

        def scalar(self):
            return self._v

    class _S:
        async def execute(self, stmt, *a, **k):
            if "max(" in str(stmt.compile()).lower():
                return _Res(41)
            return _Res({"view": {}, "cost": {"spent_cents": 12.5}})

    @contextlib.asynccontextmanager
    async def _rs():
        yield _S()

    monkeypatch.setattr(dbs, "read_scope", _rs)
    w = await rr.RunEventWriter.for_run(7)
    assert w.views["cost"]["own_cents"] == 12.5
    assert w.views["cost"]["spent_cents"] == 12.5


def test_the_issue_run_list_still_shows_root_runs_only():
    """``list_for_issue`` 喂的是驾驶舱那张 run 列表 —— 一行一次顶层运行，每行显示
    它自己那棵树的总额（``cost_cents``）。把子 run 也列进去既是重复展示，也会让
    同一笔钱在列表里出现两次。

    ⚠️ 与它成对的 ``spent_cents_for_issue`` **不再** root-only：见下一条。
    """
    import inspect

    from app.repositories import agent_runs_repository as repo

    src = inspect.getsource(repo.AgentRunsRepository.list_for_issue)
    assert "parent_run_id.is_(None)" in src


def test_the_budget_gate_no_longer_excludes_child_runs():
    """I2 给后台子 run 发了 ``issue_id``，而 I3 让父行的 ``cost_cents`` 包含子的钱
    —— 两条合起来逼出当时那道 root 过滤（不滤就双计）。代价是：只有**已经报回父行**
    的子 run 才进得了议题的账，Delegate 出去、还没报回来（或根本不报回来）的那部分
    对预算完全隐形。

    3d 第 0 票换了列：``own_cost_cents`` 每行只记自身，不含后代，所以双计的前提没了
    —— 全行求和才是这个议题真花的钱。SQL 形状由
    ``tests/repositories/test_issue_spend_own_cost.py`` 钉住，这里只拦「有人把 root
    过滤加回来」。
    """
    import inspect

    from app.repositories import agent_runs_repository as repo

    src = inspect.getsource(repo.AgentRunsRepository.spent_cents_for_issue)
    assert "parent_run_id.is_(None)" not in src
    assert "_own_cost_sum_stmt" in src


# ─── 3b Task 4b fix 1：媒体的钱也必须两侧同名 ──────────────────────────


async def test_finish_writes_own_plus_children_plus_media_into_the_column(monkeypatch):
    """媒体的钱进了视图却没进列，等于把 I3 的缺陷换个分量重演一遍：一条生了图的
    run 完成的那一刻，issue 的 spend 会掉下去（``_run_cents`` 运行中读视图、结束后
    读列），而跨 run 的 ``prior_cents``（``SUM(agent_runs.cost_cents)``）从头到尾
    看不见媒体。"""
    import contextlib

    from app.db import session as dbs
    from app.services.ai.runner import run_recorder as rr

    captured: dict = {}

    class _S:
        async def execute(self, stmt, *a, **k):
            compiled = stmt.compile()
            captured.setdefault("values", {}).update(
                {str(k): v for k, v in (compiled.params or {}).items()}
            )
            return None

    @contextlib.asynccontextmanager
    async def _ws():
        yield _S()

    monkeypatch.setattr(dbs, "write_scope", _ws)

    rec = rr.RunRecorder(agent_id=None, user_id=None, trigger="t")
    rec.run_id = "7"
    rec._prompt_rate = 1.0
    rec._completion_rate = 1.0
    rec.record_usage(prompt_tokens=5000, completion_tokens=5000)  # → 10.0 cents
    writer = rec._writer()
    writer.views["cost"]["by_child"] = {"51": 3.0}
    writer.views["cost"]["media_cents"] = 12.0
    writer._mirror = _noop_mirror()

    await rec._finish(status="completed")

    assert captured["values"]["cost_cents"] == 25.0
    assert writer.views["cost"]["spent_cents"] == 25.0


async def test_a_run_whose_only_spend_is_media_still_gets_a_column(monkeypatch):
    """只生图、一步 LLM 都没走的 run：没有 own、没有 children。旧的写入条件是
    「own 不为 None 或 children 非零」，于是这条 run 的列留在 NULL，钱彻底消失。"""
    import contextlib

    from app.db import session as dbs
    from app.services.ai.runner import run_recorder as rr

    captured: dict = {}

    class _S:
        async def execute(self, stmt, *a, **k):
            captured.setdefault("values", {}).update(
                {str(k): v for k, v in (stmt.compile().params or {}).items()}
            )
            return None

    @contextlib.asynccontextmanager
    async def _ws():
        yield _S()

    monkeypatch.setattr(dbs, "write_scope", _ws)

    rec = rr.RunRecorder(agent_id=None, user_id=None, trigger="t")
    rec.run_id = "7"
    writer = rec._writer()
    writer.views["cost"]["media_cents"] = 12.0
    writer._mirror = _noop_mirror()

    await rec._finish(status="completed")

    assert captured["values"]["cost_cents"] == 12.0


async def test_the_refold_lifts_the_media_account_with_the_outputs_slice(monkeypatch):
    """登记口常常通过 ``for_run`` 另开一个 writer 写 ``deliverable``——活着的
    recorder 从没折过它（真栈 run 348401200407189 就是这条路）。outputs 切片是从
    transcript 捞回来的，**钱必须跟着同一次捞回来**：只捞计数不捞账，就会出现
    「面板上有 1 件产出、账上一分钱没有」。"""
    import contextlib
    from unittest.mock import AsyncMock

    from app.db import session as dbs
    from app.services.ai.runner import run_recorder as rr

    rows = [
        {
            "event_type": "deliverable",
            "payload": {
                "kind": "generated_media",
                "ref_id": "1",
                "version": 1,
                "cost_cents": 12.0,
            },
        }
    ]

    class _Res:
        def mappings(self):
            return self

        def all(self):
            return rows

    class _S:
        async def execute(self, *a, **k):
            return _Res()

    @contextlib.asynccontextmanager
    async def _rs():
        yield _S()

    monkeypatch.setattr(dbs, "read_scope", _rs)

    writer = rr.RunEventWriter(7, seq_start=3)
    writer.views["cost"]["own_cents"] = 10.0
    writer._mirror = AsyncMock()
    # 另一个 writer 抢过 seq —— 这就是「这条 run 上还有别人」的通知，
    # 也是打开 refold 那道守卫的三个条件之一。
    writer._seq_conflicts = 1
    assert writer.foreign_writer_seen is True

    await writer.refold_external_slices()

    assert writer.views["view"]["outputs"]["total"] == 1
    assert writer.views["cost"]["media_cents"] == 12.0
    assert writer.views["cost"]["spent_cents"] == 22.0


def test_a_childs_media_reaches_the_parents_by_child():
    """子 run 的媒体花费坐 ``subagent_done`` 的 ``cost_cents`` 进父的 ``by_child``。
    那个数由 ``_cost_cents_of`` 从子的 ``spent_cents`` 取——所以只要子的账里有媒体，
    父的账里就有；列与视图同名这件事在父子两级都得成立。"""
    from types import SimpleNamespace

    from app.services.ai.runner.subagent_task_service import _cost_cents_of

    child = rp.replay(
        [
            ("step_end", {"turn": 1, "step": 1, "cost_cents": 2.0, "model": "m"}),
            (
                "deliverable",
                {
                    "kind": "generated_media",
                    "ref_id": "1",
                    "version": 1,
                    "cost_cents": 12.0,
                },
            ),
        ]
    )
    cents = _cost_cents_of(SimpleNamespace(views=child))
    assert cents == 14.0

    parent = rp.apply(
        rp.empty_views(),
        "subagent_done",
        {
            "child_run_id": "51",
            "mode": "async",
            "status": "success",
            "cost_cents": cents,
        },
    )
    assert parent["cost"]["by_child"] == {"51": 14.0}
    assert parent["cost"]["spent_cents"] == 14.0
