"""登记必须发生在 ``caller_scope`` **之外**（T2 修复轮 1，Critical）。

``caller_scope`` 把自己发布成 ambient session（``app/db/session.py`` 的
性质 1），所以在它里面调用的 ``read_scope()`` / ``write_scope()`` **join**
那个 ``authenticated`` 事务。而 ``run_deliverables`` 只有 service_role 策略
（mig 453:126-129，462 没有加），``agent_run_transcript_events`` 同样没有给
``authenticated`` 的 INSERT 策略 —— 于是登记的 INSERT 拿 42501。

要命的不是那个异常本身（best-effort 会吞掉并记 ERROR），是它**把调用方的
事务弄废了**：此刻 ``create_shot`` / ``update_shot`` / ``apply_element_edit``
还没提交（同一个事务），退出 ``caller_scope`` 时整笔回滚 —— agent 的分镜/
场次写入连坐丢失，工具报失败，agent 重试。

所以这里钉的是**顺序**：先提交，再登记。顺序是可机械检查的，而真正的
42501 只有真库能复现（单测里 session 是桩的，正是它让这个缺陷躲过了第一轮）。
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

import app.services.ai.tools.screenwriting_tools as st


@pytest.fixture
def order(monkeypatch):
    """记录 caller_scope 进出与登记调用的先后。"""
    seen: list[str] = []

    @asynccontextmanager
    async def _caller_scope(_user_id):
        seen.append("enter_caller_scope")
        try:
            yield None
        finally:
            # 真的 caller_scope 在这里提交它自己的事务。
            seen.append("exit_caller_scope")

    async def _register(**kwargs):
        seen.append(f"register:{kwargs['kind']}")
        return None

    monkeypatch.setattr(st, "caller_scope", _caller_scope)
    monkeypatch.setattr(st, "register_deliverable_best_effort", _register)
    return seen


def _scope():
    return SimpleNamespace(run_id=777, user_id="u", episode_id=1, project_id=1)


def _stub_scope(monkeypatch):
    async def _bound_scope(_ctx):
        return _scope()

    monkeypatch.setattr(st, "_bound_scope", _bound_scope)


def _ctx():
    return {"run_id": 777, "user_id": "u", "agent_id": "a", "turn": 1, "step": 4}


async def test_create_shot_registers_after_the_transaction_closes(order, monkeypatch):
    _stub_scope(monkeypatch)

    async def _resolve_scene(_sid, _scope):
        return SimpleNamespace(id=2, script_id=1)

    async def _create_shot(_scope, _scene, _args, **_k):
        order.append("gateway_write")
        return {"shot_id": "1", "shot_label": "S3 · Shot 1", "shot_type": "MS"}

    monkeypatch.setattr(st, "resolve_scene", _resolve_scene)
    monkeypatch.setattr(st.gateway, "create_shot", _create_shot)

    out = await st.SCREENWRITING_TOOLS.create_shot({"scene_id": "2"}, _ctx())

    assert out["ok"] is True, out
    assert order == [
        "enter_caller_scope",
        "gateway_write",
        "exit_caller_scope",
        "register:script_shot",
    ]


async def test_update_shot_registers_after_the_transaction_closes(order, monkeypatch):
    _stub_scope(monkeypatch)

    async def _resolve_shot(_sid, _scope):
        return SimpleNamespace(id=1, scene_id=2)

    async def _update_shot(_scope, _shot, _args, **_k):
        order.append("gateway_write")
        return {"shot_id": "1", "shot_label": "S3 · Shot 1", "shot_type": "MS"}

    monkeypatch.setattr(st, "resolve_shot", _resolve_shot)
    monkeypatch.setattr(st.gateway, "update_shot", _update_shot)

    out = await st.SCREENWRITING_TOOLS.update_shot(
        {"shot_id": "1", "description": "d"}, _ctx()
    )

    assert out["ok"] is True, out
    assert order == [
        "enter_caller_scope",
        "gateway_write",
        "exit_caller_scope",
        "register:script_shot",
    ]


async def test_a_registration_failure_cannot_touch_the_committed_write(
    order, monkeypatch
):
    """登记炸了（真库上就是 42501），分镜照样是写成功的。

    best-effort 已经吞掉异常；这条盯的是它吞的时候事务**已经关了** ——
    把登记搬回 ``caller_scope`` 里面，异常虽然还是被吞，但事务已经作废，
    退出时回滚，写入凭空消失。
    """
    _stub_scope(monkeypatch)

    async def _resolve_scene(_sid, _scope):
        return SimpleNamespace(id=2, script_id=1)

    async def _create_shot(_scope, _scene, _args, **_k):
        order.append("gateway_write")
        return {"shot_id": "1", "shot_label": "S3 · Shot 1", "shot_type": "MS"}

    # 真的 best-effort（不是桩）——要测的正是它吞错的那一刻事务已经关了。
    # 炸点放在它包住的那一层，模拟真库上的 42501。
    import app.services.deliverables.registry as registry

    async def _boom(**_k):
        order.append("register_raised")
        raise RuntimeError("permission denied for table run_deliverables")

    monkeypatch.setattr(st, "resolve_scene", _resolve_scene)
    monkeypatch.setattr(st.gateway, "create_shot", _create_shot)
    monkeypatch.setattr(registry, "register_deliverable", _boom)
    monkeypatch.setattr(
        st,
        "register_deliverable_best_effort",
        registry.register_deliverable_best_effort,
    )

    out = await st.SCREENWRITING_TOOLS.create_shot({"scene_id": "2"}, _ctx())

    assert out["ok"] is True, out
    assert out["shot"]["shot_id"] == "1"
    assert order.index("register_raised") > order.index("exit_caller_scope")


def test_the_gateway_no_longer_registers():
    """源码守卫：网关的三个写点都跑在 ``caller_scope`` 里面，所以那里
    **不许**再出现登记调用——否则 Critical 原样复发。"""
    import pathlib

    src = (
        pathlib.Path(st.__file__).resolve().parents[2]
        / "ai"
        / "scope"
        / "scoped_script_gateway.py"
    ).read_text(encoding="utf-8")
    assert "register_deliverable" not in src, (
        "scoped_script_gateway 的写点在 caller_scope(authenticated) 内，"
        "run_deliverables 只有 service_role 策略——在那里登记会 42501 "
        "并连坐回滚调用方的写入。登记属于 screenwriting_tools，在 "
        "async with 退出之后。"
    )


async def test_apply_edit_registers_the_scene_after_the_transaction_closes(
    order, monkeypatch
):
    _stub_scope(monkeypatch)
    _stub_edit(monkeypatch, order, refused=False)

    out = await st.SCREENWRITING_TOOLS.apply_edit(
        {"scene_id": "9", "base_content_version": 1, "edits": {"e1": "x"}}, _ctx()
    )

    assert out.get("ok") is True, out
    assert order == [
        "enter_caller_scope",
        "gateway_write",
        "exit_caller_scope",
        "register:script_scene",
    ]


async def test_a_refused_edit_registers_nothing(order, monkeypatch):
    """冲突被拒 = 什么都没写。给一个不存在的版本登记比不登记更糟。"""
    _stub_scope(monkeypatch)
    _stub_edit(monkeypatch, order, refused=True)

    out = await st.SCREENWRITING_TOOLS.apply_edit(
        {"scene_id": "9", "base_content_version": 1, "edits": {"e1": "x"}}, _ctx()
    )

    assert out.get("ok") is not True
    assert [o for o in order if o.startswith("register:")] == []


async def test_an_update_with_nothing_writable_registers_nothing(order, monkeypatch):
    """``update_shot`` 返回 ``None`` 是「没有可写字段」，不是新版本。"""
    _stub_scope(monkeypatch)

    async def _resolve_shot(_sid, _scope):
        return SimpleNamespace(id=1, scene_id=2)

    async def _update_shot(_scope, _shot, _args, **_k):
        order.append("gateway_write")
        return None

    monkeypatch.setattr(st, "resolve_shot", _resolve_shot)
    monkeypatch.setattr(st.gateway, "update_shot", _update_shot)

    await st.SCREENWRITING_TOOLS.update_shot(
        {"shot_id": "1", "description": "d"}, _ctx()
    )

    assert [o for o in order if o.startswith("register:")] == []


def _stub_edit(monkeypatch, order, *, refused: bool):
    scene = SimpleNamespace(
        id=9,
        script_id=1,
        heading_int_ext="INT.",
        location_text="CAFE",
        time_of_day="DAY",
    )
    selection = SimpleNamespace(scene=scene)

    async def _prepare(_self, _args, _ctx_, _scope):
        return selection, {"e1": "x"}, 1

    async def _apply(_scope, _scene, _edits, **_k):
        order.append("gateway_write")
        if refused:
            return st.gateway.EditRefused(
                code="version_conflict",
                message="nope",
                scene_id=9,
                element_ids=("e1",),
                observed_version=1,
                current_version=2,
            )
        return st.gateway.EditApplied(
            scene_id=9,
            element_ids=("e1",),
            content_version=2,
            rebased_from=None,
            observed_version=1,
            quoted_base_version=1,
        )

    async def _scene_no(_scope, _scene):
        return 3

    monkeypatch.setattr(st.ScreenwritingTools, "_prepare_edit", _prepare)
    monkeypatch.setattr(st.gateway, "apply_element_edit", _apply)
    monkeypatch.setattr(st.gateway, "scene_no_for", _scene_no)
