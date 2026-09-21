"""``agent_runs.own_cost_cents``（mig 479）的两个写方。

这一列是**这条 run 自己**烧掉的钱（``own_cents + media_cents``），不含后代 ——
与 ``cost_cents``（自身 + 已报到的后代，review I3 的口径）是两个数，各有各的读方。
聚合读方要按行求和，读 ``cost_cents`` 会把有子 run 的那一层重复计入。

两个写方都断在**编译后的语句**上，不断 mock：

* :meth:`RunEventWriter.mirror_stmt` —— 每次事件镜像都带上这一列，所以 running 的
  行也是实时的；崩溃写方（liveness / sweeper）只翻 status、不碰这一列，于是它停在
  最后一次镜像的值，与树收口按行读 ``metadata_json.cost`` 得到的是同一个数。
* :meth:`RunRecorder._finish` 的终态 UPDATE —— **无条件**写。费率未知时
  ``own_cents`` 是 None，但 media 那一道照样是真花掉的钱；漏写会让这一行在聚合里
  表现成「没花钱」而不是「不知道」。

值一律取自 ``tree_charge.spend_of_run(...).total`` —— 全仓只有那一处做
``own_cents + media_cents`` 这道加法，第二处就是第二套口径。
"""

from __future__ import annotations

import contextlib
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.services.ai.runner.run_recorder import RunEventWriter, RunRecorder

pytestmark = pytest.mark.unit


def _params(stmt) -> dict[str, Any]:
    """编译后的 bind 值 —— 断这个而不是断 ``.values()`` 的字面量，因为真正发给
    PG 的是它。"""
    return dict(stmt.compile(dialect=postgresql.dialect()).params)


def _bind(params: dict[str, Any], column: str) -> Any:
    """取某一列的 bind。``update().values()`` 的 bind 名以列名开头，重名时带后缀。"""
    keys = [k for k in params if k == column or k.startswith(f"{column}_")]
    assert keys, f"{column} 不在 bind 里：{sorted(params)}"
    assert len(keys) == 1, f"{column} 有多个 bind：{keys}"
    return params[keys[0]]


# ── 写方一：每次事件镜像 ──────────────────────────────────────────────────


@pytest.fixture
def writer_with_views() -> RunEventWriter:
    w = RunEventWriter(42)
    w.views["cost"].update(
        {
            "own_cents": 1.2,
            "media_cents": 0.3,
            # 后代那一笔在 by_child / spent_cents 里，**不该**进这一列。
            "by_child": {"c": 5.0},
            "spent_cents": 6.5,
        }
    )
    return w


def test_mirror_stmt_writes_own_cost_from_spend_of_run(writer_with_views):
    stmt = writer_with_views.mirror_stmt()
    params = _params(stmt)
    assert "own_cost_cents" in str(stmt.compile(dialect=postgresql.dialect()))
    assert _bind(params, "own_cost_cents") == 1.5


def test_mirror_stmt_own_cost_excludes_descendants(writer_with_views):
    """1.2 + 0.3，不是 ``spent_cents`` 的 6.5 —— 这一列不含后代，否则按行求和时
    有子 run 的那一层被数两遍。"""
    assert _bind(_params(writer_with_views.mirror_stmt()), "own_cost_cents") != 6.5


def test_mirror_stmt_still_writes_metadata_json(writer_with_views):
    """新列是加上去的，不是替换 —— 镜像本职（写 view / cost）一字不动。"""
    sql = str(writer_with_views.mirror_stmt().compile(dialect=postgresql.dialect()))
    assert "metadata_json" in sql and "jsonb_set" in sql


# ── 写方二：终态 UPDATE ───────────────────────────────────────────────────


class _Writer:
    """折叠视图的最小桩（照抄 test_persist_views_contract.py 的形状）。"""

    def __init__(self, cost: dict[str, Any]) -> None:
        self.views = {
            "cost": cost,
            "efficiency": {
                "steps": 1,
                "tool_calls": 0,
                "tool_errors": 0,
                "deliverables": 0,
                "turn_end_reason": "completed",
            },
        }

    async def refold_external_slices(self, *, force: bool = False) -> None:
        return None

    async def persist_views(self) -> bool:
        return True


async def _finish_binds(monkeypatch, cost: dict[str, Any]) -> dict[str, Any]:
    """跑一次 ``_finish``，把那条终态 UPDATE 的 bind 取出来。"""
    seen: list[Any] = []

    @contextlib.asynccontextmanager
    async def _ws():
        class _S:
            async def execute(self, stmt):
                seen.append(stmt)

                class _R:
                    rowcount = 1

                return _R()

        yield _S()

    monkeypatch.setattr("app.db.session.write_scope", _ws)
    rec = RunRecorder(agent_id=uuid4(), user_id=uuid4(), trigger="chat", team_id=1)
    rec.run_id = "777"
    rec._event_writer = _Writer(cost)
    with (
        patch("app.services.ai_usage.record_usage", AsyncMock()),
        patch("app.services.ai.billing.token_billing.reconcile_run", AsyncMock()),
        patch("app.services.ai.billing.tree_charge.settle_tree_if_closed", AsyncMock()),
        patch("app.services.search.projection.project_run_best_effort", AsyncMock()),
    ):
        await rec._finish(status="completed")
    assert len(seen) == 1, f"终态 UPDATE 应该只有一条，实际 {len(seen)}"
    return _params(seen[0])


@pytest.fixture
async def finish_updates(monkeypatch) -> dict[str, Any]:
    return await _finish_binds(
        monkeypatch,
        {"own_cents": 2.0, "media_cents": 0.5, "by_child": {"c": 4.0}},
    )


@pytest.fixture
async def finish_updates_no_rate(monkeypatch) -> dict[str, Any]:
    # 费率未知且没有折叠出 own_cents —— 只有生图那一道花了钱。
    return await _finish_binds(
        monkeypatch,
        {"own_cents": 0.0, "media_cents": 0.7, "by_child": {}},
    )


async def test_finish_writes_own_cost_not_tree_total(finish_updates):
    assert _bind(finish_updates, "own_cost_cents") == 2.5
    # 老列语义一字不变：自身 + 已报到的后代。
    assert _bind(finish_updates, "cost_cents") == 6.5


async def test_finish_writes_own_cost_even_when_own_cents_unknown(
    finish_updates_no_rate,
):
    """费率未知不等于没花钱 —— media 那一道照样要落库，否则聚合读方把这一行读成
    「没花钱」而不是「不知道」。"""
    assert _bind(finish_updates_no_rate, "own_cost_cents") == 0.7
