"""分镜出图/出视频把 run 坐标穿过 DBOS。

派发时 run 上下文就丢了，workflow 里再也拿不回来——这条路产出的图于是
永远没有 run 可挂（spec §1.3 的真栈缺口）。所以四个 enqueue 站点都要加宽，
两条 workflow 都要回填。

加宽的是**四个**站点（出图三个、出视频一个）。agent 工具那个手里早就有
``scope.run_id``，今天只写进了 task_tracking 的 metadata；漏掉任何一个，
就是那条路的产出永远无 run 可挂。
"""

import pathlib
from typing import Any


class _EnqueueSpy:
    def __init__(self) -> None:
        self.kwargs: list[dict[str, Any]] = []

    async def __call__(self, _name, *, dbos_workflow_kwargs, **_rest):
        self.kwargs.append(dbos_workflow_kwargs)
        return None


# --------------------------------------------------------------------------
# The two workflows: what arrives in the kwargs must reach the origin.
# --------------------------------------------------------------------------


async def test_image_workflow_backfills_the_run_coordinates(monkeypatch):
    import app.workflows.script_shot_generate as wf

    seen: dict = {}

    async def _fake_register(**kwargs):
        seen["origin"] = kwargs["origin"]
        return {"id": 55}

    monkeypatch.setattr(
        "app.services.library.generated_media_service.register_generated_media",
        _fake_register,
    )

    class _ShotRepo:
        async def get_by_id(self, _sid):
            return {"id": 1, "scene_id": 2, "description": "d"}

    class _SceneRepo:
        async def get_by_id(self, _sid):
            return {"id": 2, "scene_number": 3, "title": "t"}

    monkeypatch.setattr(
        "app.repositories.script_shot_repository.get_script_shot_repository",
        lambda: _ShotRepo(),
    )
    monkeypatch.setattr(
        "app.repositories.script_scene_repository.get_script_scene_repository",
        lambda: _SceneRepo(),
    )
    monkeypatch.setattr(wf, "_resolve_scope_id", _fake_scope_id)

    out = await _call_step(
        wf.persist_generation,
        shot_id="1",
        provider_url="http://cdn/x.png",
        model="m",
        provider="p",
        user_id="u",
        run_id=777,
        turn=1,
        step=4,
    )

    assert out["image_url"] == "/api/v1/generated-media/55/cover"
    origin = seen["origin"]
    assert (origin.run_id, origin.turn, origin.step) == (777, 1, 4)


async def test_video_workflow_backfills_the_run_coordinates(monkeypatch):
    import app.workflows.script_shot_video as wf

    seen: dict = {}

    async def _fake_register(**kwargs):
        seen["origin"] = kwargs["origin"]
        return {"id": 66}

    monkeypatch.setattr(
        "app.services.library.generated_media_service.register_generated_media",
        _fake_register,
    )

    class _ShotRepo:
        async def get_by_id(self, _sid):
            return {"id": 1, "scene_id": 2, "description": "d"}

    class _SceneRepo:
        async def get_by_id(self, _sid):
            return {"id": 2, "scene_number": 3, "title": "t"}

    monkeypatch.setattr(
        "app.repositories.script_shot_repository.get_script_shot_repository",
        lambda: _ShotRepo(),
    )
    monkeypatch.setattr(
        "app.repositories.script_scene_repository.get_script_scene_repository",
        lambda: _SceneRepo(),
    )
    monkeypatch.setattr(wf, "_resolve_scope_id", _fake_scope_id)
    monkeypatch.setattr(wf, "_reap_scratch_dir", lambda _p: None)

    url = await _call_step(
        wf.persist_video_generation,
        shot_id="1",
        local_path="/tmp/jimeng_x/out.mp4",
        model="m",
        provider="p",
        user_id="u",
        run_id=777,
        turn=1,
        step=9,
    )

    assert url == "/api/v1/generated-media/66/stream"
    origin = seen["origin"]
    assert (origin.run_id, origin.turn, origin.step) == (777, 1, 9)


async def test_no_run_leaves_the_origin_honestly_empty(monkeypatch):
    """人手点的 /generate 没有 run。回填成 0 或者省略键都会让登记口
    把一次人手生成记成某个 run 的产出。"""
    import app.workflows.script_shot_generate as wf

    seen: dict = {}

    async def _fake_register(**kwargs):
        seen["origin"] = kwargs["origin"]
        return {"id": 55}

    monkeypatch.setattr(
        "app.services.library.generated_media_service.register_generated_media",
        _fake_register,
    )

    class _Repo:
        async def get_by_id(self, _sid):
            return {"id": 1, "scene_id": 2, "description": "d"}

    monkeypatch.setattr(
        "app.repositories.script_shot_repository.get_script_shot_repository",
        lambda: _Repo(),
    )
    monkeypatch.setattr(
        "app.repositories.script_scene_repository.get_script_scene_repository",
        lambda: _Repo(),
    )
    monkeypatch.setattr(wf, "_resolve_scope_id", _fake_scope_id)

    await _call_step(
        wf.persist_generation,
        shot_id="1",
        provider_url="http://cdn/x.png",
        model="m",
        provider="p",
        user_id="u",
    )
    origin = seen["origin"]
    assert (origin.run_id, origin.turn, origin.step) == (None, None, None)


# --------------------------------------------------------------------------
# The four enqueue sites: the payload must carry the three keys.
# --------------------------------------------------------------------------


async def test_agent_tool_enqueue_carries_the_run_it_already_has(monkeypatch):
    """agent 工具手里早就有 scope.run_id，今天只写进 task_tracking 的
    metadata —— workflow 侧一无所知。"""
    import app.services.ai.tools.screenwriting_tools as st

    spy = _EnqueueSpy()
    _stub_agent_dispatch(monkeypatch, st, spy)
    from app.core.config import settings

    monkeypatch.setattr(settings, "FEATURE_SHOT_GENERATE", True)

    out = await st.SCREENWRITING_TOOLS.generate_shot_image(
        {"shot_id": "1"},
        {"run_id": 777, "user_id": "u", "agent_id": "a", "turn": 1, "step": 4},
    )

    assert out["ok"] is True, out
    assert spy.kwargs[0]["run_id"] == 777
    assert (spy.kwargs[0]["turn"], spy.kwargs[0]["step"]) == (1, 4)


def test_every_shot_enqueue_site_passes_the_three_keys():
    """四个站点全覆盖——而且是**扫出来的**，不是手数的。

    加宽站点这件事的失败模式就是漏掉一个，而漏掉的那个通常没有测试。
    锚点是 ``dbos_workflow_callable=`` 那一行（不是"文件里提到过这个名字"
    ——同一个 router 里还有一个 breakdown workflow，按文件扫会把它算进来），
    取紧随其后的 kwargs 块。新加第五个站点忘了带坐标，这条一样转红。"""
    import re

    app = pathlib.Path(__file__).resolve().parents[2] / "app"
    wanted = ("script_shot_generate_workflow", "script_shot_video_workflow")
    pattern = re.compile(
        r"dbos_workflow_callable=(" + "|".join(wanted) + r")\s*,"
        r"\s*dbos_workflow_kwargs=\{(.*?)\}",
        re.S,
    )
    sites = [
        (str(path.relative_to(app.parent)), which, block)
        for path in app.rglob("*.py")
        for which, block in pattern.findall(path.read_text(encoding="utf-8"))
    ]

    assert len(sites) == 4, f"expected 4 enqueue sites, found {len(sites)}: {sites}"
    # 出图三个、出视频一个——数量对了但类型错了，说明扫到了别的 workflow。
    assert sorted(w for _p, w, _b in sites) == [
        "script_shot_generate_workflow",
        "script_shot_generate_workflow",
        "script_shot_generate_workflow",
        "script_shot_video_workflow",
    ]
    missing = [
        where
        for where, _which, block in sites
        if not all(k in block for k in ('"run_id"', '"turn"', '"step"'))
    ]
    assert missing == [], f"这些派发方没带 run 坐标：{missing}"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


async def _fake_scope_id(_scene, _user_id):
    return 1


async def _call_step(step_fn, **kwargs):
    """``@DBOS.step()`` 包过的函数——拿它底下的真身来调，单测里不起引擎。"""
    fn = getattr(step_fn, "__wrapped__", step_fn)
    return await fn(**kwargs)


def _stub_agent_dispatch(monkeypatch, st, spy):
    from types import SimpleNamespace

    scope = SimpleNamespace(run_id=777, user_id="u", episode_id=1, project_id=1)
    shot = SimpleNamespace(id=1, scene_id=2, status="empty")

    from contextlib import asynccontextmanager

    async def _bound_scope(_ctx):
        return scope

    async def _resolve_shot(_shot_id, _scope):
        return shot

    async def _set_shot_status(_scope, _shot, _status):
        return None

    @asynccontextmanager
    async def _caller_scope(_user_id):
        yield None

    monkeypatch.setattr(st, "_bound_scope", _bound_scope, raising=False)
    monkeypatch.setattr(st, "resolve_shot", _resolve_shot)
    monkeypatch.setattr(st, "caller_scope", _caller_scope)
    monkeypatch.setattr(st.gateway, "set_shot_status", _set_shot_status)
    monkeypatch.setattr(
        "app.services.infra.dbos_orchestrator.start_workflow_routed", spy
    )

    class _Mgr:
        async def create(self, **_k):
            return "task-1"

    monkeypatch.setattr(
        "app.services.infra.unified_task_manager.get_task_manager", lambda: _Mgr()
    )
    return scope, shot
