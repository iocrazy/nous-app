"""文本类花费 = 对 step_end 事件的读时折叠（3b spec §3.1），不回写。"""

from contextlib import asynccontextmanager

import pytest

from app.services.deliverables.lineage_view import allocate_step_costs

pytestmark = pytest.mark.unit
RUN = 913402881190401


def _v(version, **over):
    row = {
        "id": str(700000000000000 + version),
        "version": version,
        "run_id": str(RUN),
        "turn": 1,
        "step": 3,
        "cost_cents": None,
    }
    row.update(over)
    return row


def test_a_step_that_produced_two_deliverables_splits_in_half():
    """一步两镜 → 各一半（3b spec §5 验收⑥）。分母是那一步的 deliverable 事件
    数，不是本对象的版本数——两个分镜是两条链。"""
    out = allocate_step_costs([_v(1)], {(RUN, 1, 3): 0.09})  # 0.18 的一步，两件
    assert (out[0]["cost_cents"], out[0]["cost_kind"]) == (0.09, "allocated")


def test_no_step_end_yet_is_none_not_zero():
    """step_end 还没到（回合进行中 / 事件没落成）→ 不知道，不是免费。"""
    out = allocate_step_costs([_v(1)], {})
    assert (out[0]["cost_cents"], out[0]["cost_kind"]) == (None, None)


def test_a_registered_cost_is_exact_and_never_reallocated():
    out = allocate_step_costs([_v(1, cost_cents=12.0)], {(RUN, 1, 3): 0.09})
    assert (out[0]["cost_cents"], out[0]["cost_kind"]) == (12.0, "exact")


def test_a_version_without_run_coordinates_gets_nothing():
    """人手登记的版本没有 run/turn/step，没有可分摊的步。"""
    out = allocate_step_costs([_v(1, run_id=None, turn=None, step=None)], {})
    assert (out[0]["cost_cents"], out[0]["cost_kind"]) == (None, None)


def test_the_inputs_are_not_mutated():
    rows = [_v(1)]
    allocate_step_costs(rows, {(RUN, 1, 3): 0.09})
    assert rows[0]["cost_cents"] is None and "cost_kind" not in rows[0]


def _fake_read_scope(rows):
    @asynccontextmanager
    async def _scope():
        class _R:
            def mappings(self):
                return self

            def all(self):
                return rows

        class _Session:
            async def execute(self, stmt):
                return _R()

        yield _Session()

    return _scope


def _ev(event_type, **payload):
    """一行 transcript 事件。第一个参数刻意不叫 ``kind``——``deliverable`` 事件
    的 payload 自己就有一个 ``kind``（产出类型），同名会撞车。"""
    return {
        "run_id": RUN,
        "event_type": event_type,
        "turn": 1,
        "step": 3,
        "payload": payload,
    }


async def test_load_divides_the_step_cost_by_its_deliverable_count(monkeypatch):
    import app.services.deliverables.step_costs as sc

    rows = [
        _ev("step_end", cost_cents=0.18),
        _ev("deliverable", kind="script_shot", ref_id="9", version=1),
        _ev("deliverable", kind="script_shot", ref_id="10", version=1),
    ]
    monkeypatch.setattr(sc, "read_scope", _fake_read_scope(rows))
    assert await sc.load_step_shares([RUN]) == {(RUN, 1, 3): 0.09}


async def test_a_step_with_no_deliverable_event_is_not_a_share(monkeypatch):
    """分母为 0 的步不进表——除零与「凭空多出一份」都是错的答案。"""
    import app.services.deliverables.step_costs as sc

    monkeypatch.setattr(
        sc, "read_scope", _fake_read_scope([_ev("step_end", cost_cents=0.18)])
    )
    assert await sc.load_step_shares([RUN]) == {}


async def test_no_runs_means_no_query(monkeypatch):
    import app.services.deliverables.step_costs as sc

    @asynccontextmanager
    async def _boom():
        raise AssertionError("must not query")
        yield

    monkeypatch.setattr(sc, "read_scope", _boom)
    assert await sc.load_step_shares([]) == {}


async def test_a_failed_transcript_read_is_no_shares_not_a_raise(monkeypatch):
    """花费是装饰，不连坐血缘：读事件流失败就当作「这条链没有可分摊的步」。"""
    import app.services.deliverables.step_costs as sc

    @asynccontextmanager
    async def _boom():
        raise RuntimeError("db down")
        yield

    monkeypatch.setattr(sc, "read_scope", _boom)
    assert await sc.load_step_shares([RUN]) == {}


async def test_a_step_end_without_a_cost_is_not_a_share(monkeypatch):
    """``cost_cents`` 缺席（provider 没回用量）→ 不知道，不是 0。``True`` 是
    ``int`` 的子类，也不许当成 1 分钱。"""
    import app.services.deliverables.step_costs as sc

    rows = [
        _ev("step_end"),
        _ev("deliverable", kind="script_shot", ref_id="9", version=1),
    ]
    monkeypatch.setattr(sc, "read_scope", _fake_read_scope(rows))
    assert await sc.load_step_shares([RUN]) == {}

    rows[0]["payload"]["cost_cents"] = True
    monkeypatch.setattr(sc, "read_scope", _fake_read_scope(rows))
    assert await sc.load_step_shares([RUN]) == {}
