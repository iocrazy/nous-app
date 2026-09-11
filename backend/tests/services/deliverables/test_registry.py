"""登记口的四条契约：no-op、落行、落事件、截断。

真库不在单测里——repository 用桩，断言我们发出去的东西，
真执行留给 schema-drift 的集成套件与 Task 8 的真栈。
"""

import pytest

from app.services.deliverables.registry import register_deliverable


async def test_no_run_id_registers_nothing(repo_spy, emit_spy):
    """人手改动 / 前端生成走同一个函数，必须什么都不做。"""
    out = await register_deliverable(
        run_id=None, kind="generated_media", ref_id="123", title="x"
    )
    assert out is None
    assert repo_spy.inserts == [] and emit_spy.events == []


@pytest.mark.parametrize("empty", ["", 0, "0"])
async def test_the_other_empty_run_ids_register_nothing(repo_spy, emit_spy, empty):
    """``scope.run_id`` 的测试 sentinel 是字符串 "0"，画布路径给空串——
    三种「没有 run」的写法都不许占版本号。"""
    assert (
        await register_deliverable(
            run_id=empty, kind="generated_media", ref_id="123", title="x"
        )
        is None
    )
    assert repo_spy.inserts == [] and emit_spy.events == []


async def test_first_registration_is_v1_with_no_parent(repo_spy, emit_spy):
    repo_spy.latest_version_returns = None
    out = await register_deliverable(
        run_id=777,
        kind="generated_media",
        ref_id="123",
        title="Shot 1",
        model="gpt-image-2.5",
        cost_cents=0.12,
        turn=1,
        step=3,
    )
    assert (out.version, out.parent_version) == (1, None)
    ev = emit_spy.events[-1]
    assert ev.type == "deliverable"
    assert ev.payload == {
        "kind": "generated_media",
        "ref_id": "123",
        "version": 1,
        "parent_version": None,
        "title": "Shot 1",
        "model": "gpt-image-2.5",
        "cost_cents": 0.12,
        "turn": 1,
        "step": 3,
    }
    assert (ev.turn, ev.step) == (1, 3)  # 坐标也要进列，不只进载荷


async def test_second_registration_chains_to_the_previous(repo_spy, emit_spy):
    repo_spy.latest_version_returns = 2
    out = await register_deliverable(
        run_id=777, kind="script_shot", ref_id="9", title="t"
    )
    assert (out.version, out.parent_version) == (3, 2)


async def test_title_is_clipped_to_120(repo_spy, emit_spy):
    """标题进事件也进列；不设界，一个超长剧本行就能把 transcript 撑爆。"""
    out = await register_deliverable(
        run_id=777, kind="script_scene", ref_id="9", title="x" * 400
    )
    assert len(out.title) == 120
    assert len(emit_spy.events[-1].payload["title"]) == 120


async def test_an_unknown_kind_is_a_typed_failure_not_a_silent_skip(repo_spy, emit_spy):
    """四类之外的 kind 是接线 bug。静默跳过会让那条路的产出永远不存在，
    而没有任何地方说得出来——「触发路径必须类型化失败回显」。"""
    with pytest.raises(ValueError, match="unknown deliverable kind"):
        await register_deliverable(run_id=777, kind="script_beat", ref_id="9")
    assert repo_spy.inserts == [] and emit_spy.events == []


async def test_the_row_carries_the_coordinates_and_the_money(repo_spy, emit_spy):
    """事件是给 UI 看的投影，行才是真相（spec §2.4：表是唯一真相）。
    坐标和花费只进载荷不进列，血缘端点就查不到它们。"""
    await register_deliverable(
        run_id=777,
        kind="generated_media",
        ref_id="123",
        title="Shot 1",
        model="gpt-image-2.5",
        cost_cents=0.12,
        turn=1,
        step=3,
    )
    inserted = repo_spy.inserts[-1]
    assert inserted["run_id"] == 777
    assert inserted["kind"] == "generated_media"
    assert inserted["ref_id"] == "123"
    assert inserted["version"] == 1
    assert inserted["parent_version"] is None
    assert inserted["title"] == "Shot 1"
    assert inserted["model"] == "gpt-image-2.5"
    assert inserted["cost_cents"] == 0.12
    assert (inserted["turn"], inserted["step"]) == (1, 3)


async def test_an_explicit_recorder_wins_over_the_late_writer(repo_spy, monkeypatch):
    """活着的 run 有自己的 recorder，不该为了一条事件再去读一次库。"""
    from app.services.deliverables import registry

    from .conftest import EmitSpy

    async def _boom(_run_id):  # pragma: no cover — 断言它不被调用
        raise AssertionError("_writer_for must not be used when a recorder is given")

    monkeypatch.setattr(registry, "_writer_for", _boom)
    recorder = EmitSpy()
    await register_deliverable(
        run_id=777, kind="script_shot", ref_id="9", title="t", recorder=recorder
    )
    assert [e.type for e in recorder.events] == ["deliverable"]


async def test_a_non_bigint_run_id_registers_nothing_instead_of_crashing(
    repo_spy, emit_spy
):
    """画布车道历史上传过 "r1"。它引用不到 agent_runs 的任何一行——
    当成没有 run，而不是让 ``int()`` 把整次媒体写入连坐掉。"""
    out = await register_deliverable(
        run_id="r1", kind="generated_media", ref_id="123", title="x"
    )
    assert out is None
    assert repo_spy.inserts == [] and emit_spy.events == []


async def test_best_effort_does_not_fail_a_write_that_already_happened(
    repo_spy, emit_spy
):
    """图已生成、卡已入库之后，登记失败把整次调用判败会让 agent 重试、
    同一件东西产两遍。所以吞下、记 ERROR、返回 None。"""
    from app.services.deliverables.registry import register_deliverable_best_effort

    async def _db_down(**_k):
        raise ConnectionError("db unreachable")

    repo_spy.insert_version = _db_down
    out = await register_deliverable_best_effort(
        run_id=777, kind="script_shot", ref_id="9", title="t"
    )
    assert out is None
    assert emit_spy.events == []


async def test_best_effort_still_raises_a_wiring_bug(repo_spy, emit_spy):
    """kind 写错是接线 bug，要在 CI 里炸——best-effort 不许把它也吞掉。"""
    from app.services.deliverables.registry import register_deliverable_best_effort

    with pytest.raises(ValueError, match="unknown deliverable kind"):
        await register_deliverable_best_effort(
            run_id=777, kind="script_beat", ref_id="9"
        )
