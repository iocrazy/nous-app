"""接线三：分镜 / 场次 / 章节三类各写一次 ⇒ 一行一事件。

写点在 ``scoped_script_gateway``（它手里有 ``scope.run_id``），不在
``script_scene_repository`` —— 那一层是 agent 车道与人手编辑器车道**共用**的，
没有 run 可取，在那里登记只能是无条件 no-op。

网关有四个写点，登记的是**改内容的三个**：``apply_element_edit``（场次）、
``create_shot`` / ``update_shot``（分镜）。``set_shot_status`` 只改状态，
状态不是新版本。
"""

from types import SimpleNamespace

import pytest

from app.services.ai.scope import scoped_script_gateway as gateway


@pytest.fixture
def register_spy(monkeypatch):
    calls: list[dict] = []

    async def _spy(**kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr(gateway, "register_deliverable_best_effort", _spy)
    return calls


def _resolved_scene():
    return SimpleNamespace(
        id=9,
        script_id=1,
        heading_int_ext="INT.",
        location_text="CAFE",
        time_of_day="DAY",
    )


def _scope(run_id="777"):
    return SimpleNamespace(run_id=run_id, user_id="u", episode_id=1, project_id=1)


# --------------------------------------------------------------------------
# 分镜
# --------------------------------------------------------------------------


async def test_create_shot_registers_one_deliverable(register_spy, monkeypatch):
    _stub_shot_write(monkeypatch, action="create")

    await gateway.create_shot(
        _scope(), SimpleNamespace(id=2), {"shot_type": "MS", "description": "d"}
    )

    assert len(register_spy) == 1
    call = register_spy[0]
    assert call["kind"] == "script_shot"
    assert call["run_id"] == "777"
    assert call["ref_id"] == "1"
    assert call["title"]  # 有标题，不是一个裸 id


async def test_update_shot_registers_one_deliverable(register_spy, monkeypatch):
    _stub_shot_write(monkeypatch, action="update")

    await gateway.update_shot(
        _scope(), SimpleNamespace(id=1, scene_id=2), {"description": "new"}
    )

    assert [c["kind"] for c in register_spy] == ["script_shot"]
    assert register_spy[0]["ref_id"] == "1"


async def test_set_shot_status_registers_nothing(register_spy, monkeypatch):
    """状态变更不是新版本。让它登记，一次生成就会凭空多出两版。"""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _write_scope():
        class _S:
            async def execute(self, _stmt):
                return None

        yield _S()

    monkeypatch.setattr(gateway, "write_scope", _write_scope)

    await gateway.set_shot_status(_scope(), SimpleNamespace(id=1), "generating")

    assert register_spy == []


# --------------------------------------------------------------------------
# 场次
# --------------------------------------------------------------------------


async def test_apply_element_edit_registers_one_scene_deliverable(
    register_spy, monkeypatch
):
    _stub_scene_edit(monkeypatch)

    out = await gateway.apply_element_edit(
        _scope(),
        _resolved_scene(),
        {"e1": "rewritten"},
        actor="agent",
    )

    assert isinstance(out, gateway.EditApplied)
    assert [c["kind"] for c in register_spy] == ["script_scene"]
    assert register_spy[0]["ref_id"] == "9"


async def test_a_refused_edit_registers_nothing(register_spy, monkeypatch):
    """冲突被拒 = 什么都没写。登记一个不存在的版本比不登记更糟。"""
    _stub_scene_edit(monkeypatch, observed_version=None)

    out = await gateway.apply_element_edit(
        _scope(), _resolved_scene(), {"e1": "rewritten"}, actor="agent"
    )

    assert isinstance(out, gateway.EditRefused)
    assert register_spy == []


# --------------------------------------------------------------------------
# 章节
# --------------------------------------------------------------------------


async def test_expand_chapter_registers_with_the_run_it_was_given(monkeypatch):
    import app.workflows.script_ai_workflows as wf

    calls: list[dict] = []
    _stub_chapter_service(monkeypatch, calls)

    await _call_step(
        wf.script_ai_expand_persist,
        chapter_id="5",
        html="<p>x</p>",
        run_id=777,
        turn=1,
        step=2,
    )

    assert calls[-1]["attributed_to_run_id"] == 777


async def test_create_branches_registers_each_new_chapter(monkeypatch):
    import app.workflows.script_ai_workflows as wf

    calls: list[dict] = []
    _stub_chapter_service(monkeypatch, calls)

    await _call_step(
        wf.script_ai_branches_persist,
        script_id="1",
        chapter_id="5",
        branch_type="choice",
        branches=[
            {"title": "A", "summary": "s", "branch_label": "1"},
            {"title": "B", "summary": "s", "branch_label": "2"},
        ],
        run_id=777,
        turn=1,
        step=2,
    )

    assert [c["attributed_to_run_id"] for c in calls] == [777, 777]


async def test_a_chapter_write_with_no_run_attributes_to_nobody(monkeypatch):
    """人手编辑章节走同样的 service 方法。默认 None ⇒ 登记口 no-op。"""
    import app.workflows.script_ai_workflows as wf

    calls: list[dict] = []
    _stub_chapter_service(monkeypatch, calls)

    await _call_step(wf.script_ai_expand_persist, chapter_id="5", html="<p>x</p>")

    assert calls[-1]["attributed_to_run_id"] is None


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


async def _call_step(step_fn, **kwargs):
    fn = getattr(step_fn, "__wrapped__", step_fn)
    return await fn(**kwargs)


def _stub_chapter_service(monkeypatch, calls):
    import app.services.storyboard.script.script_service as svc

    class _Repo:
        async def get_by_id(self, _cid):
            return {"position_x": 400, "position_y": 100}

    class _Svc:
        chapter_repo = _Repo()

        async def update_chapter(
            self, chapter_id, data, *, attributed_to_run_id=None, turn=None, step=None
        ):
            calls.append(
                {
                    "chapter_id": chapter_id,
                    "attributed_to_run_id": attributed_to_run_id,
                    "turn": turn,
                    "step": step,
                }
            )
            return {"id": chapter_id}

        async def create_chapter(
            self, script_id, data, *, attributed_to_run_id=None, turn=None, step=None
        ):
            calls.append(
                {
                    "script_id": script_id,
                    "attributed_to_run_id": attributed_to_run_id,
                    "turn": turn,
                    "step": step,
                }
            )
            return {"id": "new"}

    monkeypatch.setattr(svc, "ScriptService", _Svc)


def _stub_shot_write(monkeypatch, *, action):
    from contextlib import asynccontextmanager

    row = SimpleNamespace(
        id=1,
        shot_number=1,
        shot_type="MS",
        camera_angle=None,
        camera_movement=None,
        focal_length=None,
        lighting=None,
        description="d",
        status="empty",
    )

    class _Result:
        def first(self):
            return row

    @asynccontextmanager
    async def _write_scope():
        class _S:
            async def scalar(self, _stmt):
                return 0

            async def execute(self, _stmt):
                return _Result()

        yield _S()

    async def _scene_no(*_a, **_k):
        return 3

    monkeypatch.setattr(monkeypatch_target(action), "write_scope", _write_scope)
    monkeypatch.setattr(
        monkeypatch_target(action), "scene_no_for", _scene_no, raising=False
    )
    monkeypatch.setattr(
        monkeypatch_target(action), "scene_no_for_shot", _scene_no, raising=False
    )


def monkeypatch_target(_action):
    return gateway


def _stub_scene_edit(monkeypatch, *, observed_version=1):
    async def _current_scene_state(_scene_id):
        return 1, [{"id": "e1", "text": "old", "type": "action"}]

    def _observed(_run_id, _scene_id):
        if observed_version is None:
            return None
        return SimpleNamespace(
            content_version=observed_version,
            elements=[{"id": "e1", "text": "old", "type": "action"}],
        )

    class _Repo:
        async def apply_element_ops(self, _sid, _ops, expected_version, actor):
            return {
                "content_version": expected_version + 1,
                "elements": [{"id": "e1", "text": "rewritten"}],
            }

    async def _scene_no(*_a, **_k):
        return 3

    monkeypatch.setattr(gateway, "scene_no_for", _scene_no)
    monkeypatch.setattr(gateway, "_current_scene_state", _current_scene_state)
    monkeypatch.setattr(gateway, "observed_scene", _observed)
    monkeypatch.setattr(gateway, "get_script_scene_repository", lambda: _Repo())


async def test_the_chapter_service_actually_uses_the_attribution_it_is_given(
    monkeypatch,
):
    """跑真的 ``ScriptService``，不是桩。

    上面那两条只证明 workflow **传了**署名；把 service 里的
    ``attributed_to_run_id`` 换成 ``None``，它们照样绿——真正把它用出去的
    这一步没人看着。这条盯的就是那一段。
    """
    import app.services.deliverables.registry as registry
    from app.services.storyboard.script.script_service import ScriptService

    calls: list[dict] = []

    async def _spy(**kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr(registry, "register_deliverable_best_effort", _spy)

    class _Repo:
        async def update(self, chapter_id, _data):
            return {"id": chapter_id, "title": "Act I"}

        async def create(self, data):
            return {"id": "new-1", "title": data.get("title")}

    svc = ScriptService()
    monkeypatch.setattr(svc, "chapter_repo", _Repo(), raising=False)

    await svc.update_chapter("5", {"content": "x"}, attributed_to_run_id=777, step=2)
    await svc.create_chapter("1", {"title": "B"}, attributed_to_run_id=777, step=3)

    assert [c["run_id"] for c in calls] == [777, 777]
    assert [c["kind"] for c in calls] == ["script_chapter", "script_chapter"]
    assert [c["ref_id"] for c in calls] == ["5", "new-1"]
    assert [c["step"] for c in calls] == [2, 3]


async def test_a_human_chapter_edit_registers_nothing(monkeypatch):
    """编辑器走同一个方法、不传署名 ⇒ 登记口拿到 None ⇒ no-op。"""
    import app.services.deliverables.registry as registry
    from app.services.storyboard.script.script_service import ScriptService

    calls: list[dict] = []

    async def _spy(**kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr(registry, "register_deliverable_best_effort", _spy)

    class _Repo:
        async def update(self, chapter_id, _data):
            return {"id": chapter_id, "title": "Act I"}

    svc = ScriptService()
    monkeypatch.setattr(svc, "chapter_repo", _Repo(), raising=False)

    await svc.update_chapter("5", {"content": "x"})

    assert [c["run_id"] for c in calls] == [None]
