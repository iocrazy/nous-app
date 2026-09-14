"""ledger_ref：版本 → 账本行的**精确**映射（spec §2.2 / 3a 小票 C6）。

3a 按 created_at 映射（diff._at_or_before），那不是外键：同一个事务里的两次写共享
事务开始时间，按它切会把 v1 画成 v2。回退要写回的正是「某一版的内容」，错位比显示
错更糟。存量行没有 ledger_ref 且永远不会有，所以 created_at 是常设退路。"""

import datetime as _dt
import types

import pytest

from app.services.deliverables import diff as mod

pytestmark = pytest.mark.unit


def _at(day: int) -> _dt.datetime:
    return _dt.datetime(2026, 9, day, tzinfo=_dt.timezone.utc)


SHOT = [
    ({"after": {"shot_type": "WS", "description": "v1 text"}}, _at(1), 101),
    (
        {"after": {"description": "v2 text"}, "before": {"description": "v1 text"}},
        _at(2),
        102,
    ),
    (
        {"after": {"description": "v3 text"}, "before": {"description": "v2 text"}},
        _at(3),
        103,
    ),
]

#: 这条分镜此刻的六个字段。账本碰过的字段一律由账本说了算，所以这里只是第三档的
#: 兜底（这个文件里没有「从没被碰过」的字段，故全 None 即可）。
CURRENT = {field: None for field in mod._SHOT_FIELDS}


def _row(version, *, ledger_ref=None, created_at=None):
    return {
        "version": version,
        "run_id": "913402881190401",
        "issue_id": None,
        "created_at": created_at,
        "model": None,
        "cost_cents": None,
        "title": f"v{version}",
        "ledger_ref": ledger_ref,
    }


def test_a_ledger_ref_beats_a_timestamp_that_would_pick_the_wrong_prefix():
    """三行账本落在同一秒（真实：一个事务里的多次写共享 created_at）。只有
    ledger_ref 能把 v1 和 v3 分开。"""
    same_second = [(p, _at(3), k) for p, _c, k in SHOT]
    side = mod._shot_side(
        _row(1, ledger_ref="101", created_at=_at(3).isoformat()), same_second, CURRENT
    )
    assert side["available"] is True
    assert side["text"] == "shot_type: WS\ndescription: v1 text"


@pytest.mark.parametrize("ref", [None, "not-a-number"])
def test_a_missing_or_garbage_ledger_ref_falls_back_to_the_timestamp(ref):
    """存量行（None）与脏值走同一条退路——一条血缘不该因为一个字段而 500。"""
    side = mod._shot_side(
        _row(2, ledger_ref=ref, created_at=_at(2).isoformat()), SHOT, CURRENT
    )
    assert side["text"] == "shot_type: WS\ndescription: v2 text"


def test_a_scene_replays_to_the_ledger_ref_watermark():
    """两行账本同秒落地。ledger_ref=1 时只该重放到 op_seq 1。

    ⚠️ op 的形状照抄 ``scene_ops`` 真正接受的那一种（``element_id`` +
    ``payload``）——写成 ``{"element": {...}}`` 会被 ``_op_insert`` 拒绝，
    那样这个用例就只是在测桩数据，不是在测水位（「边界 mock 必须用真实
    形状」）。

    场次侧的 ledger_ref 只有 ``_prefix_for`` 一个入口：``_scene_side`` 的水位
    就取前缀的末尾，不再按 ledger_ref 自己算第二遍（fix 轮 1 删掉了那份冗余
    —— 两处算同一个界时，拆掉任何一处都不会有测试转红）。"""
    ledger = [
        (
            {
                "op_seq": 1,
                "op_json": {
                    "ops": [
                        {
                            "op": "insert",
                            "element_id": "e1",
                            "payload": {"type": "action", "text": "a"},
                        }
                    ]
                },
            },
            _at(5),
            1,
        ),
        (
            {
                "op_seq": 2,
                "op_json": {
                    "ops": [
                        {
                            "op": "update",
                            "element_id": "e1",
                            "payload": {"text": "b"},
                        }
                    ]
                },
            },
            _at(5),
            2,
        ),
    ]
    side = mod._scene_side(
        _row(1, ledger_ref="1", created_at=_at(5).isoformat()), ledger
    )
    assert side["text"] == "action: a"


def test_ledger_ref_never_reaches_the_wire():
    """账本位置是服务端定位字段。上线等于把 ops 行 id 交给浏览器。"""
    from app.services.deliverables.lineage_view import version_of

    assert "ledger_ref" not in version_of(_row(1, ledger_ref="101"))


async def test_register_write_forwards_the_ledger_ref(monkeypatch):
    """网关 RETURNING 出来的 script_shot_ops.id 必须一路走到登记口——差一步就等于
    这个字段永远是 NULL，而所有读侧测试照样绿。"""
    from app.services.ai.tools import screenwriting_tools as tools

    seen = {}

    async def _reg(**kw):
        seen.update(kw)

    monkeypatch.setattr(tools, "register_deliverable_best_effort", _reg)
    await tools._register_write(
        types.SimpleNamespace(run_id="777", user_id="u"),
        {"turn": 1, "step": 2},
        kind="script_shot",
        ref_id="9",
        title="t",
        ledger_ref="4242",
    )
    assert seen["ledger_ref"] == "4242"


def test_the_shot_dict_the_model_sees_carries_no_ledger_ref():
    """账本行 id 是宿主侧定位字段。留在工具返回值里 = 进模型上下文。"""
    from app.services.ai.tools import screenwriting_tools as tools

    shot = {"shot_id": "9", "ledger_ref": "4242"}
    assert tools._take_ledger_ref(shot) == "4242"
    assert "ledger_ref" not in shot
