"""分镜出图/出视频把 run 坐标穿过 DBOS。

派发时 run 上下文就丢了，workflow 里再也拿不回来——这条路产出的图于是
永远没有 run 可挂（spec §1.3 的真栈缺口）。所以四个 enqueue 站点都要加宽，
两条 workflow 都要回填。

加宽的是**四个**站点（出图三个、出视频一个）。agent 工具那个手里早就有
``scope.run_id``，今天只写进了 task_tracking 的 metadata；漏掉任何一个，
就是那条路的产出永远无 run 可挂。
"""

import ast
import pathlib
from typing import Any, NamedTuple


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

    _stub_repos(monkeypatch)
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

    _stub_repos(monkeypatch)
    monkeypatch.setattr(wf, "_resolve_scope_id", _fake_scope_id)
    monkeypatch.setattr(wf, "reap_scratch_dir", lambda _p: None)

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


async def test_persist_registers_the_resolved_provider_and_model(monkeypatch):
    """哨兵 dall-e-3 + provider=None 不是归因（spec §3.2 前置票）。"""
    import app.workflows.script_shot_generate as wf

    seen: dict = {}

    async def _fake_register(**kwargs):
        seen["origin"] = kwargs["origin"]
        return {"id": 58}

    monkeypatch.setattr(
        "app.services.library.generated_media_service.register_generated_media",
        _fake_register,
    )
    _stub_repos(monkeypatch)
    monkeypatch.setattr(wf, "_resolve_scope_id", _fake_scope_id)

    await _call_step(
        wf.persist_generation,
        shot_id="1",
        provider_url="http://cdn/x.png",
        model="dall-e-3",
        provider=None,
        user_id="u",
        run_id=777,
        turn=1,
        step=4,
        resolved_provider="ark",
        resolved_model="doubao-seedream-4-0",
    )
    origin = seen["origin"]
    assert (origin.provider, origin.model) == ("ark", "doubao-seedream-4-0")


async def test_a_legacy_replay_without_attribution_still_drops_the_sentinel(
    monkeypatch,
):
    """旧 checkpoint 回放时没有 resolved_*——此时 model 仍是哨兵，宁可 None。"""
    import app.workflows.script_shot_generate as wf

    seen: dict = {}

    async def _fake_register(**kwargs):
        seen["origin"] = kwargs["origin"]
        return {"id": 59}

    monkeypatch.setattr(
        "app.services.library.generated_media_service.register_generated_media",
        _fake_register,
    )
    _stub_repos(monkeypatch)
    monkeypatch.setattr(wf, "_resolve_scope_id", _fake_scope_id)

    await _call_step(
        wf.persist_generation,
        shot_id="1",
        provider_url="http://cdn/x.png",
        model=wf._DEFAULT_MODEL,
        provider="ark",
        user_id="u",
    )
    origin = seen["origin"]
    assert (origin.provider, origin.model) == ("ark", None)


async def test_video_persist_registers_the_resolved_provider_and_model(monkeypatch):
    """出视频同族：请求侧的 model/provider 是**目录行名**（step 拿它当
    ``resolve_video_provider`` 的 name），不是跑出来的那一行。"""
    import app.workflows.script_shot_video as wf

    seen: dict = {}

    async def _fake_register(**kwargs):
        seen["origin"] = kwargs["origin"]
        return {"id": 67}

    monkeypatch.setattr(
        "app.services.library.generated_media_service.register_generated_media",
        _fake_register,
    )
    _stub_repos(monkeypatch)
    monkeypatch.setattr(wf, "_resolve_scope_id", _fake_scope_id)
    monkeypatch.setattr(wf, "reap_scratch_dir", lambda _p: None)

    await _call_step(
        wf.persist_video_generation,
        shot_id="1",
        local_path="/tmp/jimeng_x/out.mp4",
        model="nous-video",
        provider=None,
        user_id="u",
        run_id=777,
        turn=1,
        step=9,
        resolved_provider="jimeng-cli",
        resolved_model="seedance2.0fast",
    )
    origin = seen["origin"]
    assert (origin.provider, origin.model) == ("jimeng-cli", "seedance2.0fast")


def test_a_legacy_video_string_checkpoint_still_persists():
    """出视频 step 的旧 checkpoint 同样是裸 ``str``（那边的载荷是本地路径）。"""
    import app.workflows.script_shot_generate as wf

    assert wf._step_output("/tmp/jimeng_x/out.mp4", key="path") == (
        "/tmp/jimeng_x/out.mp4",
        None,
        None,
    )
    assert wf._step_output(
        {"path": "/p", "provider": "jimeng-cli", "model": "m"}, key="path"
    ) == ("/p", "jimeng-cli", "m")


def test_a_legacy_string_checkpoint_still_persists():
    """DBOS 冻结的旧 step 返回值是裸 str——回放时必须照旧能走完。"""
    import app.workflows.script_shot_generate as wf

    assert wf._step_output("http://cdn/x.png") == ("http://cdn/x.png", None, None)
    assert wf._step_output({"url": "u", "provider": "ark", "model": "m"}) == (
        "u",
        "ark",
        "m",
    )


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


#: 派发这两条 workflow 的调用，其 ``dbos_workflow_kwargs`` 必须带的三个键。
_RUN_COORDINATES = ("run_id", "turn", "step")

#: 只认这两条 workflow —— 同一个 router 里还有一个 breakdown workflow，
#: 按文件名扫会把它算进来。
_WATCHED_WORKFLOWS = ("script_shot_generate_workflow", "script_shot_video_workflow")


class _EnqueueSite(NamedTuple):
    where: str
    which: str
    #: ``dbos_workflow_kwargs`` 的字面量键集合；**载荷不是字面 dict 时是
    #: None** —— 那种站点这条守卫读不懂，必须显式报出来而不是当成合格。
    keys: frozenset[str] | None


def _callee_name(node: ast.expr) -> str | None:
    """``f`` / ``mod.f`` 这类表达式的末端名字。"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _scan_enqueue_sites() -> list[_EnqueueSite]:
    """扫 ``app/`` 下每一处派发这两条 workflow 的调用。

    ⚠️ 用 ``ast`` 而不是正则，这是 C2 这张票的全部内容。上一版锚定的是
    ``dbos_workflow_callable=<名字>,`` **紧跟着** ``dbos_workflow_kwargs={``
    这个字面形状，于是三种完全正常的写法它一个都看不见：

    * 两个 kwarg 之间隔了别的参数（``workflow_id=`` 插在中间）；
    * 载荷先赋给一个变量再传进来；
    * 调用被格式化工具换行成别的样子。

    看不见的代价不是转红而是**静默漏检**：新加的第五个站点如果长成上面任何
    一种，正则只扫出原来那四个，数量断言照样是 4，于是它带没带 run 坐标
    永远没人问。（本票落地前已用一个真实的第五个站点验过：旧守卫全绿。）
    """
    app = pathlib.Path(__file__).resolve().parents[2] / "app"
    sites: list[_EnqueueSite] = []
    for path in sorted(app.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg}
            callable_arg = kwargs.get("dbos_workflow_callable")
            if callable_arg is None:
                continue
            which = _callee_name(callable_arg)
            if which not in _WATCHED_WORKFLOWS:
                continue
            payload = kwargs.get("dbos_workflow_kwargs")
            keys: frozenset[str] | None
            if isinstance(payload, ast.Dict):
                keys = frozenset(
                    k.value
                    for k in payload.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)
                )
            else:
                keys = None
            sites.append(
                _EnqueueSite(
                    where=f"{path.relative_to(app.parent)}:{node.lineno}",
                    which=which,
                    keys=keys,
                )
            )
    return sites


def test_every_shot_enqueue_site_passes_the_three_keys():
    """四个站点全覆盖——而且是**扫出来的**，不是手数的。

    加宽站点这件事的失败模式就是漏掉一个，而漏掉的那个通常没有测试。
    新加第五个站点忘了带坐标，这条转红——无论那个站点的参数怎么排、
    换不换行。
    """
    sites = _scan_enqueue_sites()

    assert len(sites) == 4, f"expected 4 enqueue sites, found {len(sites)}: {sites}"
    # 出图三个、出视频一个——数量对了但类型错了，说明扫到了别的 workflow。
    assert sorted(s.which for s in sites) == [
        "script_shot_generate_workflow",
        "script_shot_generate_workflow",
        "script_shot_generate_workflow",
        "script_shot_video_workflow",
    ]
    missing = [
        s.where
        for s in sites
        if s.keys is not None and not set(_RUN_COORDINATES) <= s.keys
    ]
    assert missing == [], f"这些派发方没带 run 坐标：{missing}"


def test_no_enqueue_site_hides_its_payload_behind_a_variable():
    """载荷读不出来 ≠ 载荷是对的。

    上一条只能对**字面 dict** 下判断。``dbos_workflow_kwargs=payload`` 这种
    写法它读不出键，此时唯一诚实的做法是**说出来**：把「这个站点我看不懂」
    报成失败，而不是让它从缺失清单里消失、混成一次干净的通过。

    CLAUDE.md 那条「探针够不着目标 ≠ 目标是坏的」的反面同样成立 —— 够不着
    也**绝不等于**目标是好的。真要这么写，就在这里显式写下它并另想办法证明
    那三个键在。
    """
    opaque = [s.where for s in _scan_enqueue_sites() if s.keys is None]
    assert opaque == [], (
        "这些派发方的 dbos_workflow_kwargs 不是字面 dict，守卫无法确认它带了 "
        f"{list(_RUN_COORDINATES)}：{opaque}"
    )


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


async def _fake_scope_id(_scene, _user_id):
    return 1


def _stub_repos(monkeypatch):
    """分镜 + 场次的仓库桩——persist 路径上的三个用例共用同一份。"""

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
