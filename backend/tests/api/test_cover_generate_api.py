"""``/distribution/covers/generate`` —— 封面工作室的生成入口。

这个端点自己不出图，它做四件事，所以只测这四件：

- **组装 prompt** 并**原样回给前端**（设计稿有一块 "What was sent to the model"
  要显示它；让前端自己拼一份"应该一样"的字符串，是两份必然漂移的真相）。
- **把画幅写进 params["ratio"]** —— ⚠️ 图片分支读的是 `ratio`，视频分支才读
  `aspect`。写错不报错，只会静默变成正方形。
- **超过 9 张响亮拒绝，而不是静默截断**。上限由 schema 在边界上给 422；
  服务端再 `[:9]` 一刀，会把「我明明选了 11 张」变成一声不吭扔掉两张。
- **把组装器的拒绝翻译成 4xx**（空主题、编号越界），不是让它在深处炸成 500。

模块开关 / 鉴权与其它 covers 端点同口径。
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import app.api.distribution_router as dr
from app.core.exceptions import register_exception_handlers

USER_ID = "u-1"
TOPIC = "三分钟看懂内容差"


def _make_app(*, module_on: bool = True, authed: bool = True) -> FastAPI:
    async def fake_require():
        if not module_on:
            raise HTTPException(status_code=404, detail="Not found")

    app = FastAPI()
    # 真实异常处理器：少了它 RequestValidationError 会直接冒泡出 TestClient 而不是
    # 变成 422，看起来像端点崩了（同 test_cover_grab_frame_api 的注释）。
    register_exception_handlers(app)
    app.include_router(dr.router, prefix="/api/v1")
    app.dependency_overrides[dr.require_distribution] = fake_require
    if authed:
        app.dependency_overrides[dr.get_current_user] = lambda: {"id": USER_ID}
    return app


class _Spy:
    def __init__(self) -> None:
        self.created: List[Dict[str, Any]] = []
        self.started: List[Dict[str, Any]] = []


@pytest.fixture
def spy(monkeypatch) -> _Spy:
    s = _Spy()

    class _Mgr:
        async def create(self, **kw):
            s.created.append(kw)
            return "task-1"

    async def _start(name, **kw):
        s.started.append({"name": name, **kw})

    monkeypatch.setattr(dr, "get_task_manager", lambda: _Mgr())
    monkeypatch.setattr(
        "app.services.infra.dbos_orchestrator.start_workflow_routed", _start
    )
    return s


def _post(client, **over):
    body = {"stage": 1, "topic": TOPIC, "source_urls": []}
    body.update(over)
    return client.post("/api/v1/distribution/covers/generate", json=body)


class TestDispatch:
    def test_stage_one_returns_the_prompt_it_sent(self, spy):
        resp = _post(TestClient(_make_app()))

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["task_id"] == "task-1"
        # 主题真的进了 prompt，而且回给了前端。
        assert TOPIC in body["prompt"]
        assert "2x2" in body["prompt"]
        # 回给前端的那份，与真正派出去的那份，必须是同一个字符串。
        assert spy.started[0]["dbos_workflow_kwargs"]["prompt"] == body["prompt"]

    def test_the_aspect_goes_into_params_ratio_not_aspect(self, spy):
        """⚠️ 图片分支读 params["ratio"]（canvas_generation.py:153）；"aspect" 是
        视频分支读的键。放错位置不会报错，只会静默产出正方形。"""
        resp = _post(TestClient(_make_app()))

        assert resp.status_code == 200
        params = spy.started[0]["dbos_workflow_kwargs"]["params"]
        assert params["ratio"] == "3:4"
        assert "aspect" not in params
        assert resp.json()["aspect"] == "3:4"

    def test_it_runs_without_a_canvas(self, spy):
        """封面工作室没有画布。workflow 的 canvas_id 本来就是 Optional —— 不为了
        满足一个路由前缀去凭空造一个隐藏画布。"""
        _post(TestClient(_make_app()))

        kw = spy.started[0]["dbos_workflow_kwargs"]
        assert kw["canvas_id"] is None
        assert kw["node_id"] is None
        assert kw["kind"] == "image"

    def test_the_task_type_fits_the_column(self, spy):
        # task_tracking.task_type 是 VARCHAR(20)。
        _post(TestClient(_make_app()))
        assert len(spy.created[0]["task_type"]) <= 20

    def test_references_ride_along_in_pool_order(self, spy):
        urls = [f"/api/v1/generated-media/{i}/cover" for i in range(3)]
        resp = _post(TestClient(_make_app()), source_urls=urls)

        assert resp.json()["reference_count"] == 3
        assert spy.started[0]["dbos_workflow_kwargs"]["params"]["source_urls"] == urls

    def test_exactly_nine_goes_through_untouched(self, spy):
        urls = [f"/api/v1/generated-media/{i}/cover" for i in range(9)]
        resp = _post(TestClient(_make_app()), source_urls=urls)

        assert resp.status_code == 200
        assert spy.started[0]["dbos_workflow_kwargs"]["params"]["source_urls"] == urls

    def test_more_than_nine_is_REFUSED_not_silently_trimmed(self, spy):
        """上限在边界上响亮拒绝，不在服务端一刀切。

        早先这里是 `refs = [...][:9]`：客户端多送两张，服务端一声不吭扔掉，用户看到
        的是"我明明选了 11 张"。而且那会是同一个数字的第三份副本（schema 一份、
        引擎 canvas_generation.py:141 一份）。
        """
        urls = [f"/api/v1/generated-media/{i}/cover" for i in range(11)]
        resp = _post(TestClient(_make_app()), source_urls=urls)

        assert resp.status_code == 422
        assert spy.started == [], "被拒绝的请求不该派出任何工作"

    def test_empty_urls_are_filtered_without_changing_the_rest(self, spy):
        urls = [
            "/api/v1/generated-media/1/cover",
            "",
            "/api/v1/generated-media/2/cover",
        ]
        resp = _post(TestClient(_make_app()), source_urls=urls)

        sent = spy.started[0]["dbos_workflow_kwargs"]["params"]["source_urls"]
        assert sent == [urls[0], urls[2]]
        assert resp.json()["reference_count"] == 2


class TestStageTwo:
    def test_it_names_the_chosen_draft(self, spy):
        resp = _post(TestClient(_make_app()), stage=2, selected_draft=3)

        assert resp.status_code == 200
        assert "draft number 3" in resp.json()["prompt"]

    def test_stage_two_without_a_draft_is_422_and_dispatches_nothing(self, spy):
        """继续下去等于随便挑一格重画 —— 用户拿到一张他没选的封面。而且这是对
        输入的判断，必须是 4xx，不能让组装器在深处炸成 500。"""
        resp = _post(TestClient(_make_app()), stage=2, selected_draft=None)

        assert resp.status_code == 422, resp.text
        assert spy.started == []

    @pytest.mark.parametrize("bad", [0, 5, -1])
    def test_an_out_of_range_draft_is_refused_by_the_schema(self, spy, bad):
        resp = _post(TestClient(_make_app()), stage=2, selected_draft=bad)

        assert resp.status_code == 422
        assert spy.started == []


class TestSmallLabels:
    def test_default_off_forbids_them(self, spy):
        resp = _post(TestClient(_make_app()))
        assert "Do not add any other small labels" in resp.json()["prompt"]

    def test_on_allows_them(self, spy):
        resp = _post(TestClient(_make_app()), allow_small_labels=True)
        p = resp.json()["prompt"]
        assert "are allowed to add platform feel" in p
        assert "Do not add any other small labels" not in p


class TestGates:
    def test_a_blank_topic_is_422_and_dispatches_nothing(self, spy):
        """``min_length=1`` 查的是原串，四个空格能过 —— 然后在组装器里炸成 500。"""
        resp = _post(TestClient(_make_app()), topic="    ")

        assert resp.status_code == 422, resp.text
        assert spy.started == []

    def test_module_off_is_404_and_dispatches_nothing(self, spy):
        resp = _post(TestClient(_make_app(module_on=False)))

        assert resp.status_code == 404
        assert spy.started == []

    def test_unauthenticated_dispatches_nothing(self, spy):
        resp = _post(TestClient(_make_app(authed=False)))

        assert resp.status_code in (401, 403, 422)
        assert spy.started == []


class TestInstructions:
    def test_the_creators_note_rides_into_the_prompt(self, spy):
        resp = _post(TestClient(_make_app()), instructions="人物指向右侧的大屏幕")
        assert resp.status_code == 200, resp.text
        assert "人物指向右侧的大屏幕" in resp.json()["prompt"]
        assert (
            "人物指向右侧的大屏幕" in spy.started[0]["dbos_workflow_kwargs"]["prompt"]
        )

    def test_too_long_is_422(self, spy):
        resp = _post(TestClient(_make_app()), instructions="x" * 501)
        assert resp.status_code == 422
        assert spy.started == []


class TestAspectAndStyle:
    def test_4_3_goes_into_ratio_and_the_prompt(self, spy):
        resp = _post(TestClient(_make_app()), aspect="4:3")
        assert resp.status_code == 200, resp.text
        assert resp.json()["aspect"] == "4:3"
        assert "4:3 horizontal" in resp.json()["prompt"]
        assert spy.started[0]["dbos_workflow_kwargs"]["params"]["ratio"] == "4:3"

    def test_an_unsupported_aspect_is_422(self, spy):
        assert _post(TestClient(_make_app()), aspect="16:9").status_code == 422
        assert spy.started == []

    def test_an_unknown_style_is_404_and_dispatches_nothing(self, spy, monkeypatch):
        async def _none(user_id, slug):
            return None

        monkeypatch.setattr(
            "app.services.distribution.cover_styles.resolve_cover_style", _none
        )
        resp = _post(TestClient(_make_app()), style="nope")
        assert resp.status_code == 404
        assert spy.started == []

    def test_a_skill_style_drives_the_prompt_from_its_skeleton(self, spy, monkeypatch):
        from app.services.distribution.cover_styles import style_from_skill

        body = (
            "## Four-Draft Preview Prompt Skeleton\n\n```text\nGrid of four neon "
            "covers about [topic].\n```\n"
        )

        async def _neon(user_id, slug):
            return style_from_skill(
                {"slug": "neon", "name": "Neon", "category": "cover", "body_md": body}
            )

        monkeypatch.setattr(
            "app.services.distribution.cover_styles.resolve_cover_style", _neon
        )
        resp = _post(TestClient(_make_app()), style="neon")
        assert resp.status_code == 200, resp.text
        assert resp.json()["prompt"].startswith("Grid of four neon covers about 三分钟")


class TestStylesEndpoint:
    def test_lists_builtin_first_plus_visible_cover_skills(self, monkeypatch):
        from app.services.distribution.cover_styles import merge_styles

        async def _list(user_id):
            return merge_styles(
                [{"slug": "neon", "name": "Neon", "category": "cover", "body_md": "x"}]
            )

        monkeypatch.setattr(
            "app.services.distribution.cover_styles.list_cover_styles", _list
        )
        resp = TestClient(_make_app()).get("/api/v1/distribution/covers/styles")
        assert resp.status_code == 200, resp.text
        slugs = [s["slug"] for s in resp.json()["styles"]]
        assert slugs == ["viral-video-cover", "neon"]
        assert resp.json()["styles"][0]["builtin"] is True
        assert resp.json()["styles"][0]["aspects"] == ["3:4", "4:3"]


class TestRoundBookkeeping:
    """轮次要活过对话框关闭：生成参数里带上 cover 元数据，落到 generated_media.params。"""

    def test_stage_one_stamps_source_video_and_stage(self, spy):
        resp = _post(TestClient(_make_app()), source_video_id="900", aspect="4:3")
        assert resp.status_code == 200, resp.text
        cover = spy.started[0]["dbos_workflow_kwargs"]["params"]["cover"]
        assert cover["stage"] == 1 and cover["aspect"] == "4:3"
        assert cover["source_video_id"] == "900" and cover["grid_gen_id"] is None
        assert spy.created[0]["metadata"]["source_video_id"] == "900"

    def test_stage_two_links_back_to_its_grid(self, spy):
        resp = _post(
            TestClient(_make_app()),
            stage=2,
            selected_draft=3,
            grid_gen_id="500",
            source_video_id="900",
        )
        assert resp.status_code == 200, resp.text
        cover = spy.started[0]["dbos_workflow_kwargs"]["params"]["cover"]
        assert cover["stage"] == 2 and cover["selected_draft"] == 3
        assert cover["grid_gen_id"] == "500"
