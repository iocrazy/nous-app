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


# ─── seq back-fill (三期 3a Task 8a, defect 2) ──────────────────────────
#
# 真栈实测 ``run_deliverables.seq`` 恒为 null：wire 上文档化了却没有写入方。
# 顺序不改 —— 先插行、再落事件（反过来会出现「有事件没有行」），所以插行
# 时还不知道 seq，只能在事件落地之后补一次 UPDATE。


async def test_the_events_seq_is_stamped_back_onto_the_row(repo_spy, emit_spy):
    out = await register_deliverable(
        run_id=777, kind="script_shot", ref_id="9", title="t", turn=1, step=3
    )
    assert emit_spy.events[-1].type == "deliverable"
    assert repo_spy.seq_calls == [(out.id, emit_spy.last_event_seq)]


async def test_the_stamp_happens_after_the_event_not_before(repo_spy, emit_spy):
    """行在前、事件在后、回写最后——回写先于事件就会写下一个还不存在的 seq。"""
    order: list[str] = []
    original_insert = repo_spy.insert_version
    original_record = emit_spy.record_event
    original_set_seq = repo_spy.set_seq

    async def insert_version(**values):
        order.append("insert")
        return await original_insert(**values)

    async def record_event(*args, **kwargs):
        order.append("event")
        return await original_record(*args, **kwargs)

    async def set_seq(**kwargs):
        order.append("set_seq")
        return await original_set_seq(**kwargs)

    repo_spy.insert_version = insert_version
    emit_spy.record_event = record_event
    repo_spy.set_seq = set_seq

    await register_deliverable(run_id=777, kind="script_shot", ref_id="9")
    assert order == ["insert", "event", "set_seq"]


async def test_a_failed_stamp_keeps_the_row_and_the_event(repo_spy, emit_spy, caplog):
    """UPDATE 失败只记 WARNING：行与事件都已经落库，少一个定位字段不该把
    一次成功的登记判成失败（``register_deliverable_best_effort`` 的同族理由）。"""
    repo_spy.raise_on_set_seq = True
    with caplog.at_level("WARNING"):
        out = await register_deliverable(
            run_id=777, kind="script_shot", ref_id="9", title="t"
        )
    assert out is not None and out.version == 1
    assert emit_spy.events[-1].type == "deliverable"
    assert repo_spy.seq_calls == [(out.id, 1)]


async def test_no_recorder_means_no_stamp(repo_spy, monkeypatch):
    """事件没落成（run 拿不到写入口）就没有 seq 可填——列留 NULL，
    绝不写一个指向不存在事件的数字。"""
    from app.services.deliverables import registry

    async def _no_writer(_run_id):
        return None

    monkeypatch.setattr(registry, "_writer_for", _no_writer)
    out = await register_deliverable(run_id=777, kind="script_shot", ref_id="9")
    assert out is not None
    assert repo_spy.seq_calls == []


async def test_a_recorder_that_cannot_report_its_seq_is_not_guessed(repo_spy):
    """老的测试替身（只有 ``record_event``）不暴露 seq。填一个猜的数字比留
    NULL 更糟——NULL 说的是「不知道」，错的数字说的是「在那一步」。"""

    class BareRecorder:
        def __init__(self) -> None:
            self.calls = 0

        async def record_event(self, event_type, payload, *, turn=None, step=None):
            self.calls += 1

    rec = BareRecorder()
    out = await register_deliverable(
        run_id=777, kind="script_shot", ref_id="9", recorder=rec
    )
    assert out is not None and rec.calls == 1
    assert repo_spy.seq_calls == []


async def test_an_event_that_failed_to_persist_leaves_seq_null(repo_spy):
    """``RunEventWriter.append`` 插失败时返回 None 而 ``emit`` 仍报 True
    （遥测不连坐一次运行）。那条事件不在 transcript 里，所以它的 seq 不许
    落到行上。"""

    class FailedWriteRecorder:
        last_event_seq = None

        async def record_event(self, event_type, payload, *, turn=None, step=None):
            return None

    out = await register_deliverable(
        run_id=777, kind="script_shot", ref_id="9", recorder=FailedWriteRecorder()
    )
    assert out is not None
    assert repo_spy.seq_calls == []


async def test_the_late_dbos_recorder_reports_the_seq_append_used(repo_spy):
    """分镜出图走 DBOS，父 run 早已收工——事件由 ``_writer_for`` 的迟到写入口
    落下。那条路也必须报得出 seq，否则只有活着的 run 才有 seq。"""
    from app.services.deliverables import registry

    class _Writer:
        def __init__(self) -> None:
            self.seq = 40
            self.appended: list[str] = []

        async def append(self, event_type, payload, *, turn=None, step=None):
            self.seq += 1
            self.appended.append(event_type)
            return self.seq

    writer = _Writer()
    rec = registry._LateRecorder(writer)
    assert rec.last_event_seq is None

    out = await register_deliverable(
        run_id=777, kind="script_shot", ref_id="9", recorder=rec
    )
    assert writer.appended == ["deliverable"]
    assert rec.last_event_seq == 41
    assert repo_spy.seq_calls == [(out.id, 41)]


async def test_the_late_recorder_reports_nothing_when_the_insert_failed(repo_spy):
    """``RunEventWriter.append`` 插失败返回 None —— 那条事件不在 transcript
    里，行上的 seq 必须留空。"""
    from app.services.deliverables import registry

    class _FailingWriter:
        async def append(self, event_type, payload, *, turn=None, step=None):
            return None

    rec = registry._LateRecorder(_FailingWriter())
    out = await register_deliverable(
        run_id=777, kind="script_shot", ref_id="9", recorder=rec
    )
    assert out is not None and rec.last_event_seq is None
    assert repo_spy.seq_calls == []


async def test_a_rejected_event_never_stamps_a_stale_seq(repo_spy):
    """``emit`` 报 False（这个 recorder 根本不收事件）时，它身上可能还留着
    **上一条**事件的 seq。那个数字与本次登记无关，不许落到行上。"""

    class NoEvents:
        """A recorder in name only — ``emit`` returns False for it."""

        last_event_seq = 9

    out = await register_deliverable(
        run_id=777, kind="script_shot", ref_id="9", recorder=NoEvents()
    )
    assert out is not None
    assert repo_spy.seq_calls == []


# ─── 修复轮 1 / I3：回写失败的吞错策略跟着同文件的先例走 ────────────────


async def test_a_failed_stamp_inside_an_ambient_transaction_is_raised(
    repo_spy, emit_spy, monkeypatch
):
    """在调用方的事务里，失败的 UPDATE 已经把那个 session 弄废了。吞掉它
    只会让后面某条无关语句抛 ``PendingRollbackError``——与 10 行之下的
    ``_insert_next_version`` 同一条理由，所以同一个处理：交出去。"""
    from app.db import session as dbs

    monkeypatch.setattr(dbs, "in_unit_of_work", lambda: True)
    repo_spy.raise_on_set_seq = True

    with pytest.raises(RuntimeError, match="update failed"):
        await register_deliverable(run_id=777, kind="script_shot", ref_id="9")

    # 行与事件照旧先发生——抛出的是回写那一步，不是登记本身。
    assert len(repo_spy.inserts) == 1
    assert emit_spy.events[-1].type == "deliverable"


async def test_the_best_effort_wrapper_absorbs_that_raise(
    repo_spy, emit_spy, monkeypatch
):
    """产物已经存在的写入点用 best_effort：它把这个 raise 记成 ERROR，
    不让一次成功的产出被判成失败。"""
    from app.db import session as dbs
    from app.services.deliverables.registry import register_deliverable_best_effort

    monkeypatch.setattr(dbs, "in_unit_of_work", lambda: True)
    repo_spy.raise_on_set_seq = True

    assert (
        await register_deliverable_best_effort(
            run_id=777, kind="script_shot", ref_id="9"
        )
        is None
    )


async def test_outside_a_transaction_a_failed_stamp_stays_a_warning(
    repo_spy, emit_spy, monkeypatch
):
    """没有 ambient 事务时没有东西被弄废，行与事件都在——只记 WARNING。"""
    from app.db import session as dbs

    monkeypatch.setattr(dbs, "in_unit_of_work", lambda: False)
    repo_spy.raise_on_set_seq = True

    out = await register_deliverable(run_id=777, kind="script_shot", ref_id="9")
    assert out is not None and out.version == 1
    assert emit_spy.events[-1].type == "deliverable"
