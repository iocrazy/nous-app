"""活路径把**自己的** recorder 交给登记口（三期 3a T8c 缺陷 1 的主修）。

登记口拿不到 recorder 就退回 ``RunEventWriter.for_run`` —— 同一个 run 上的
第二个 writer。两个后果都在生产上发生过（run 348401200407189）：

1. 那个 writer 把 ``view.outputs`` 折进 ``metadata_json``，活 recorder 的下
   一次镜像写的是**整个 ``view`` 值**，原样抹掉 —— 座舱产出格唯一的数据源
   永远是空的，这一格在生产上一次都没渲染过；
2. 两个 writer 各记各的 seq 计数器，活 recorder 的下一条 insert 撞
   ``(run_id, seq)`` 唯一索引被静默丢掉（真栈 transcript 里
   ``deliverable(4) tool_call(5)`` 正是这个形状）。

能交出 recorder 的只有**同进程、同轮次**的那两条路 —— 分镜/剧本工具与出图出
片工具，两者的 ``run_context`` 都由 ``_media_run_context`` 组装。其余登记照旧
走 ``for_run``。

⚠️ 别把 ``for_run`` 读成「那时父 run 一定已经结束」：`GenerateShotImage` 只确认
dispatch 就返回，DBOS 侧那次 `register_generated_media` 可能落在父 run 仍然活着
时。那条路够不着 recorder，兜住它的是 `RunEventWriter` 自己 —— `append` 撞唯一
索引重新播种并重试，重折守卫也以那次撞车为信号打开
（`tests/runner/test_seq_conflict_retry.py`）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit


def _runner():
    from app.services.ai.runner.agent_runner import AgentRunner

    return AgentRunner.__new__(AgentRunner)


def test_the_run_context_carries_the_live_recorder():
    recorder = SimpleNamespace(run_id=777, user_id="u", team_id=3)

    ctx = _runner()._media_run_context(recorder, SimpleNamespace(agent_id="a1"), step=2)

    assert ctx["recorder"] is recorder


def test_no_recorder_is_an_honest_none_not_a_missing_key():
    """键要在。读方用 ``ctx.get("recorder")``，缺键与 None 同义，但显式的
    None 说的是「这一路没有活 recorder」，而不是「有人忘了接线」。"""
    ctx = _runner()._media_run_context(None, SimpleNamespace(agent_id="a1"))
    assert ctx["recorder"] is None


async def test_a_screenwriting_write_registers_through_the_live_recorder(monkeypatch):
    import app.services.ai.tools.screenwriting_tools as swt

    seen: dict = {}

    async def _fake_register(**kwargs):
        seen.update(kwargs)
        return None

    monkeypatch.setattr(swt, "register_deliverable_best_effort", _fake_register)
    recorder = SimpleNamespace(run_id=777)

    await swt._register_write(
        SimpleNamespace(run_id=777),
        {"turn": 1, "step": 3, "recorder": recorder},
        kind="script_shot",
        ref_id=42,
        title="MEDIUM",
    )

    assert seen["recorder"] is recorder
    assert (seen["turn"], seen["step"]) == (1, 3)


async def test_the_image_tool_hands_the_recorder_down_to_the_registry(monkeypatch):
    import app.services.ai.tools.generate_media_tools as gmt

    seen: dict = {}

    async def _fake_register(**kwargs):
        seen.update(kwargs)
        return {"id": 1}

    monkeypatch.setattr(gmt, "register_generated_media", _fake_register)
    monkeypatch.setattr(gmt, "_resolve_provider_model", lambda _a, _k: ("openai", "m1"))

    class _Svc:
        async def generate_image(self, **_k):
            return {"url": "http://cdn/x.png"}

    monkeypatch.setattr(gmt.GenerateMediaTools, "_svc", lambda _self: _Svc())
    recorder = SimpleNamespace(run_id=777)

    out = await gmt.GenerateMediaTools().generate_image(
        {"prompt": "a cat"},
        {
            "run_id": 777,
            "user_id": "u",
            "team_id": 1,
            "agent_id": "a",
            "turn": 1,
            "step": 4,
            "recorder": recorder,
        },
    )

    assert out["ok"] is True
    assert seen["recorder"] is recorder


async def test_the_media_ingest_forwards_the_recorder_to_the_registry(monkeypatch):
    """``register_generated_media`` 是 11 个调用点的咽喉；recorder 必须从它
    穿过去，否则出图这条路仍然是两个 writer。"""
    import app.services.library.generated_media_service as gms

    seen: dict = {}

    async def _fake_register(**kwargs):
        seen.update(kwargs)
        return None

    monkeypatch.setattr(gms, "register_deliverable_best_effort", _fake_register)
    recorder = SimpleNamespace(run_id=777)

    # 只跑登记那一段：把入库与写盘都换成桩，形状与真实返回一致。
    async def _fake_download(dest, url):  # noqa: ANN001
        return 1

    monkeypatch.setattr(gms, "_download_to", _fake_download)
    monkeypatch.setattr(gms.settings, "FEATURE_CHAT_MEDIA_OBJECT_STORE", True)

    async def _fake_object_store(*, scope_id, source_url, mime, kind):  # noqa: ANN001
        return ("sb://chat-media/x.png", 1, "sha")

    monkeypatch.setattr(gms, "_write_generation_to_object_store", _fake_object_store)

    class _Row:
        def mappings(self):
            return self

        def first(self):
            return {"id": 99}

    class _S:
        async def execute(self, *_a, **_k):
            return _Row()

    import contextlib

    @contextlib.asynccontextmanager
    async def _ws():
        yield _S()

    monkeypatch.setattr(gms, "write_scope", _ws)

    await gms.register_generated_media(
        user_id="u",
        scope_id=1,
        source_url="http://cdn/x.png",
        mime="image/png",
        origin=gms.GenerationOrigin(kind="agent_run", run_id="777"),
        recorder=recorder,
    )

    assert seen["recorder"] is recorder


async def test_a_registration_with_a_recorder_never_opens_a_second_writer(
    monkeypatch, repo_spy
):
    """给了 recorder 就绝不再开 ``for_run`` —— 那正是覆盖与 seq 相撞的来源。"""
    from app.services.deliverables import registry

    async def _boom(_run_id):
        raise AssertionError("opened a second writer despite having a recorder")

    monkeypatch.setattr(registry, "_writer_for", _boom)

    class _Rec:
        def __init__(self):
            self.events: list[tuple] = []
            self.last_event_seq = None

        async def record_event(self, event_type, payload, *, turn=None, step=None):
            self.events.append((event_type, payload))
            self.last_event_seq = len(self.events)

    rec = _Rec()
    row = await registry.register_deliverable(
        run_id=777, kind="script_shot", ref_id=42, title="MEDIUM", recorder=rec
    )

    assert row is not None
    assert [e[0] for e in rec.events] == ["deliverable"]
    # 事件真落在这个 recorder 上，所以 seq 回写也拿得到号。
    assert repo_spy.seq_calls == [(str(row.id), 1)]
