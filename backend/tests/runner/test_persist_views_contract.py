"""终态前那次 ``persist_views()`` 是一条**硬契约**，实现必须跟着硬（终审 I1/I2）。

树收口按**行**读 ``agent_runs.metadata_json.cost``，所以每条 run 的最终 ``own_cents``
必须在它被标成终态**之前**落库。在此之前这条契约声明是硬的、实现是软的：

* **I1** —— ``persist_views`` → ``_mirror`` 整段 ``except Exception`` 只打一条
  WARNING，然后 ``_finish`` 照样收口。镜像失败 = 按**上一次事件镜像的旧值**收口，
  而戳一盖这棵树**再也不会被收第二次**。现在失败要说出来，``_finish`` 跳过收口，
  把树留给清扫器（提名条件是 ``charged_at IS NULL``，戳没盖就还提得到）。
* **I2** —— ``refold_external_slices`` 的提前返回只看**内存**。一次纯生图回合的
  ``media_cents`` 是 ``for_run`` writer 写进库的，活 recorder 的内存里 outputs 恒为
  0，于是守卫关着、整个 ``cost`` 值被原样盖回去、``media_cents`` 归 0 —— 收口读到
  0。``persist_views`` 这一次**无条件重折**（一次运行只调一次），把一条概率性的
  兜底（seq 撞车）换成保证。
"""

from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.services.ai.runner.run_recorder import RunEventWriter, RunRecorder

pytestmark = pytest.mark.unit


# ── I1：镜像失败 → 跳过收口 ────────────────────────────────────────────────


class _Writer:
    """折叠视图的最小桩。``persist_views`` 的答案由用例给。"""

    def __init__(self, mirrored: bool) -> None:
        self._mirrored = mirrored
        self.views = {
            "cost": {"own_cents": 5.0, "by_child": {}, "media_cents": 0.0},
            "efficiency": {
                "steps": 1,
                "tool_calls": 0,
                "tool_errors": 0,
                "deliverables": 0,
                "turn_end_reason": "completed",
            },
        }

    async def refold_external_slices(self, *, force: bool = False):
        return None

    async def persist_views(self) -> bool:
        return self._mirrored


@contextlib.asynccontextmanager
async def _ws():
    class _S:
        async def execute(self, stmt):
            class _R:
                rowcount = 1

            return _R()

    yield _S()


async def _finish_with(monkeypatch, *, mirrored: bool):
    monkeypatch.setattr("app.db.session.write_scope", _ws)
    rec = RunRecorder(agent_id=uuid4(), user_id=uuid4(), trigger="chat", team_id=1)
    rec.run_id = "777"
    rec._event_writer = _Writer(mirrored)
    settle = AsyncMock()
    with (
        patch("app.services.ai_usage.record_usage", AsyncMock()),
        patch("app.services.ai.billing.token_billing.reconcile_run", AsyncMock()),
        patch("app.services.ai.billing.tree_charge.settle_tree_if_closed", settle),
        patch("app.services.search.projection.project_run_best_effort", AsyncMock()),
    ):
        await rec._finish(status="completed")
    return settle


async def test_a_failed_mirror_hands_the_tree_to_the_sweeper(monkeypatch):
    """镜像失败时收口**必须不发生**。当场收口是拿旧数扣钱且不可逆（戳盖上就没有
    第二次）；跳过只是晚 2 小时 —— 清扫器按 ``charged_at IS NULL`` 提名，戳没盖，
    它还捞得回来。"""
    settle = await _finish_with(monkeypatch, mirrored=False)
    assert settle.await_count == 0


async def test_a_successful_mirror_settles_as_usual(monkeypatch):
    """正常路径一字不变 —— 这条用例证明上一条不是把收口整个关掉了。"""
    settle = await _finish_with(monkeypatch, mirrored=True)
    assert settle.await_count == 1


async def test_the_skip_is_logged_as_an_error_with_the_run_id(monkeypatch):
    """少收一棵树的钱必须能被查出来。WARNING 混在遥测噪声里，且没有 run_id
    就无从回查是哪棵树。"""
    seen: list[str] = []
    with patch(
        "app.services.ai.runner.run_recorder.logger.error",
        lambda msg, *a, **k: seen.append(str(msg)),
    ):
        await _finish_with(monkeypatch, mirrored=False)
    assert any("777" in m for m in seen), seen


async def test_persist_views_answers_false_when_the_update_raises():
    """``_mirror`` 的 ``except`` 不再只是打日志 —— 它得把「没写进去」这件事说给
    调用方听，否则 ``_finish`` 没有任何办法知道。"""
    w = RunEventWriter(run_id=555)

    @contextlib.asynccontextmanager
    async def _boom():
        raise RuntimeError("connection reset")
        yield  # pragma: no cover

    with patch("app.db.session.write_scope", _boom):
        assert await w.persist_views() is False


async def test_persist_views_answers_true_when_the_update_lands():
    w = RunEventWriter(run_id=555)
    with patch("app.db.session.write_scope", _ws):
        assert await w.persist_views() is True


# ── I2：终态前那次重折是无条件的 ──────────────────────────────────────────


def _transcript(rows):
    """``refold_external_slices`` 那条 read_scope 的桩。"""

    class _S:
        async def execute(self, stmt):
            class _R:
                def mappings(self_inner):
                    class _M:
                        def all(self_m):
                            return rows

                    return _M()

            return _R()

    @contextlib.asynccontextmanager
    async def _rs():
        yield _S()

    return _rs


#: 一次纯生图回合：产出由 DBOS 那条道的 ``for_run`` writer 登记，活 recorder 从没
#: 折过它，所以内存里 children/outputs 都是 0、也没撞过 seq —— 守卫的四个条件
#: 一个都不成立。
#:
#: ⚠️ 键名照抄 ``folds/deliverables.py::fold_deliverable`` 真正读的那几个
#: （``kind`` / ``ref_id`` / ``version`` 缺一或 ``version`` 不是 int 就整条 return
#: None）。写成 ``resource_id`` 的 fixture 会让这条用例在**修好之后**仍然红，而红的
#: 原因是 fixture 自己 —— 同「边界 mock 必须用真实 JSON 形状」。
_MEDIA_DELIVERABLE = [
    {
        "event_type": "deliverable",
        "payload": {
            "kind": "image",
            "ref_id": "9001",
            "version": 1,
            "cost_cents": 7.5,
        },
    }
]


async def test_persist_views_refolds_even_when_memory_looks_empty():
    """``for_run`` writer 写进库的 ``media_cents`` 必须在终态 UPDATE 之前被抬回
    内存 —— 否则 ``_mirror`` 把**整个** ``cost`` 值盖回去时它就是 0，而收口紧接着
    按行读那个 0。"""
    w = RunEventWriter(run_id=555)
    assert w.views["cost"]["media_cents"] == 0.0
    with (
        patch("app.db.session.read_scope", _transcript(_MEDIA_DELIVERABLE)),
        patch("app.db.session.write_scope", _ws),
    ):
        assert await w.persist_views() is True
    assert w.views["cost"]["media_cents"] == 7.5


async def test_the_append_path_still_pays_zero_extra_reads():
    """守卫本身留着 —— 没有子 agent、没有产出、没有第二个 writer 的 run 在**每次
    append** 上都不该多打一次库。终态那一次是一次运行一次，成本可以忽略；把它写成
    「每次镜像都重折」就是拿一条真实的热路径成本去换同一个保证。"""
    w = RunEventWriter(run_id=555)
    reads = 0

    class _S:
        async def execute(self, stmt):  # pragma: no cover — 不该被调用
            nonlocal reads
            reads += 1
            raise AssertionError("append path must not read the transcript")

    @contextlib.asynccontextmanager
    async def _rs():
        yield _S()

    with patch("app.db.session.read_scope", _rs):
        await w.refold_external_slices()
    assert reads == 0
