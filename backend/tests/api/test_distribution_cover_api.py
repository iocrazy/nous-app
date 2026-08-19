"""``/distribution/covers/{extract,select}`` —— 端点层测试。

端点层只负责四件事，所以只测这四件（抽帧/裁切本身归 ``tests/test_cover_frames``）：

- **模块开关 / 鉴权**：关掉的模块与未认证的调用都不能进到业务代码。
- **fail-fast 校验**：源不存在 / 不是视频要在派工**之前**返回 4xx —— 建一条
  task_tracking 行、起一个 workflow、几秒后让用户去任务中心看一条红色失败，
  是比一个立刻返回的 404 差得多的体验。
- **IDOR**：写回目标必须是调用者自己的任务，且 404（不是 403）—— 不泄漏存在性。
  鉴权要发生在裁切之前，免得权限不足时还白裁两张图、白建两个 resources 行。
- **类型化失败透传**：``CoverFrameError.status_code`` 原样变成 HTTP 状态码。
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import app.api.distribution_router as dr
from app.services.distribution.cover_frames import (
    CoverFrameError,
    CoverPair,
    SourceVideo,
)

USER_ID = "u-1"


def _make_app(*, module_on: bool = True, authed: bool = True) -> FastAPI:
    async def fake_require():
        if not module_on:
            raise HTTPException(status_code=404, detail="Not found")

    app = FastAPI()
    app.include_router(dr.router, prefix="/api/v1")
    app.dependency_overrides[dr.require_distribution] = fake_require
    if authed:
        app.dependency_overrides[dr.get_current_user] = lambda: {"id": USER_ID}
    return app


class _Spy:
    """Records everything the endpoint would set in motion, so a test can
    assert that a rejected request set NOTHING in motion."""

    def __init__(self) -> None:
        self.created: List[Dict[str, Any]] = []
        self.started: List[Dict[str, Any]] = []

    def manager(self):
        spy = self

        class _Mgr:
            async def create(self, **kw):
                spy.created.append(kw)
                return kw.get("dbos_workflow_id")

        return _Mgr()

    async def start_workflow(self, *a, **kw):
        self.started.append(kw)
        return {"mode": "dbos"}


def _install_dispatch_spy(monkeypatch: pytest.MonkeyPatch) -> _Spy:
    spy = _Spy()
    monkeypatch.setattr(dr, "get_task_manager", spy.manager)
    monkeypatch.setattr(dr, "start_workflow_routed", spy.start_workflow)
    return spy


def _source(filename: str = "clip.mp4") -> SourceVideo:
    return SourceVideo(
        resource={"id": "500", "filename": filename},
        file_path="sb://library/t1/ab/cd/x.mp4",
        scope_id="scope-1",
        folder_id=None,
        library_id=None,
    )


def _stub_load(monkeypatch: pytest.MonkeyPatch, result) -> None:
    """Patch the seam the endpoint imports lazily (``load_source_video``)."""

    async def fake_load(repo, resource_id):
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(
        "app.services.distribution.cover_frames.load_source_video", fake_load
    )


# ============================================================
# POST /covers/extract
# ============================================================


def test_extract_is_404_when_the_distribution_module_is_off() -> None:
    client = TestClient(_make_app(module_on=False))
    resp = client.post(
        "/api/v1/distribution/covers/extract", json={"resource_id": "500"}
    )
    assert resp.status_code == 404


def test_extract_rejects_unauthenticated_callers_without_dispatching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _install_dispatch_spy(monkeypatch)
    client = TestClient(_make_app(authed=False))
    resp = client.post(
        "/api/v1/distribution/covers/extract", json={"resource_id": "500"}
    )
    assert resp.status_code in (401, 403, 422)
    assert spy.created == [] and spy.started == []


def test_extract_dispatches_one_workflow_sharing_the_task_tracking_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """路线 C：task_tracking 行的主键与 DBOS workflow id 必须是同一个 —— 否则
    trigger 同步不到这条行，UI 永远停在 queued。"""
    spy = _install_dispatch_spy(monkeypatch)
    _stub_load(monkeypatch, _source())

    client = TestClient(_make_app())
    resp = client.post(
        "/api/v1/distribution/covers/extract",
        json={"resource_id": "500", "num_frames": 4},
    )

    assert resp.status_code == 200, resp.text
    task_id = resp.json()["task_id"]
    assert len(spy.created) == 1 and len(spy.started) == 1
    assert spy.created[0]["dbos_workflow_id"] == task_id
    assert spy.started[0]["workflow_id"] == task_id
    assert spy.started[0]["dbos_workflow_kwargs"] == {
        "source_resource_id": "500",
        "user_id": USER_ID,
        "num_frames": 4,
    }
    # 任务行一建好就带着空候选清单，前端订阅时拿到的形状是稳定的。
    assert spy.created[0]["metadata"]["cover_frames"]["candidates"] == []
    # task_type 必须塞得进 task_tracking.task_type 的 VARCHAR(20)。
    assert len(spy.created[0]["task_type"]) <= 20


def test_extract_returns_404_for_an_unknown_source_before_creating_a_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fail-fast：能在毫秒内判定的坏输入不该变成一条红色的任务中心记录。"""
    spy = _install_dispatch_spy(monkeypatch)
    _stub_load(monkeypatch, CoverFrameError(status_code=404, detail="not found"))

    client = TestClient(_make_app())
    resp = client.post(
        "/api/v1/distribution/covers/extract", json={"resource_id": "nope"}
    )

    assert resp.status_code == 404
    assert spy.created == [] and spy.started == []


def test_extract_returns_400_for_a_non_video_source_before_creating_a_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _install_dispatch_spy(monkeypatch)
    _stub_load(
        monkeypatch,
        CoverFrameError(
            status_code=400, detail="cover extraction is only valid for video resources"
        ),
    )

    client = TestClient(_make_app())
    resp = client.post(
        "/api/v1/distribution/covers/extract", json={"resource_id": "500"}
    )

    assert resp.status_code == 400
    assert "video" in resp.json()["detail"]
    assert spy.created == [] and spy.started == []


@pytest.mark.parametrize("num_frames", [0, -1, 13, 999])
def test_extract_rejects_out_of_range_frame_counts(
    monkeypatch: pytest.MonkeyPatch, num_frames: int
) -> None:
    """每帧都是一次 ffmpeg seek + 一份要走 Realtime 的预览，所以上限在 schema
    上就要挡住 —— 钳制是兜底，不是唯一的门。"""
    spy = _install_dispatch_spy(monkeypatch)
    _stub_load(monkeypatch, _source())

    client = TestClient(_make_app())
    resp = client.post(
        "/api/v1/distribution/covers/extract",
        json={"resource_id": "500", "num_frames": num_frames},
    )

    assert resp.status_code == 422
    assert spy.created == [] and spy.started == []


def test_extract_defaults_the_frame_count_when_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _install_dispatch_spy(monkeypatch)
    _stub_load(monkeypatch, _source())

    client = TestClient(_make_app())
    resp = client.post(
        "/api/v1/distribution/covers/extract", json={"resource_id": "500"}
    )

    assert resp.status_code == 200
    from app.services.distribution.cover_frames import DEFAULT_COVER_FRAMES

    assert spy.started[0]["dbos_workflow_kwargs"]["num_frames"] == DEFAULT_COVER_FRAMES


def test_extract_truncates_an_absurdly_long_source_filename_in_the_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``task_tracking.title`` 有长度上限；用户可控的文件名直接拼进去会 500。"""
    spy = _install_dispatch_spy(monkeypatch)
    _stub_load(monkeypatch, _source(filename="x" * 500))

    client = TestClient(_make_app())
    resp = client.post(
        "/api/v1/distribution/covers/extract", json={"resource_id": "500"}
    )

    assert resp.status_code == 200
    assert len(spy.created[0]["title"]) <= 200


# ============================================================
# POST /covers/select
# ============================================================


# 当前形状：候选帧不落库，所以选帧回传的是"源视频 + 秒数"这个坐标。
SELECT_BODY: Dict[str, Any] = {"source_resource_id": "500", "timestamp_seconds": 15.25}


def _stub_derive(monkeypatch: pytest.MonkeyPatch, result) -> List[Dict[str, Any]]:
    calls: List[Dict[str, Any]] = []

    async def fake_derive(*, source_resource_id, timestamp_seconds, user_id, repo=None):
        calls.append(
            {
                "source_resource_id": source_resource_id,
                "timestamp_seconds": timestamp_seconds,
                "user_id": user_id,
            }
        )
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(
        "app.services.distribution.cover_frames.derive_cover_pair", fake_derive
    )
    return calls


def _stub_derive_legacy(
    monkeypatch: pytest.MonkeyPatch, result
) -> List[Dict[str, Any]]:
    """旧形状（frame_resource_id）走的是另一个函数 —— 部署错峰兼容路径。"""
    calls: List[Dict[str, Any]] = []

    async def fake_derive(*, frame_resource_id, user_id, repo=None):
        calls.append({"frame_resource_id": frame_resource_id, "user_id": user_id})
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(
        "app.services.distribution.cover_frames.derive_cover_pair_from_frame",
        fake_derive,
    )
    return calls


def _pair() -> CoverPair:
    return CoverPair(
        vertical_resource_id="9999000000000001",
        horizontal_resource_id="9999000000000002",
        source_frame_resource_id="900",
    )


def test_select_is_404_when_the_distribution_module_is_off() -> None:
    client = TestClient(_make_app(module_on=False))
    resp = client.post("/api/v1/distribution/covers/select", json=dict(SELECT_BODY))
    assert resp.status_code == 404


def test_select_without_a_publish_task_returns_both_covers_and_writes_nothing_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """常见顺序是封面在前：撰写表单先挑封面，再把两个 id 塞进创建任务的 body。
    所以 publish_task_id 缺席是正常路径，不是缺参数。"""
    _stub_derive(monkeypatch, _pair())
    written: List[Any] = []

    async def fake_set_covers(task_id, **kw):
        written.append((task_id, kw))

    monkeypatch.setattr(dr.publish_repo, "set_task_covers", fake_set_covers)

    client = TestClient(_make_app())
    resp = client.post("/api/v1/distribution/covers/select", json=dict(SELECT_BODY))

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "cover_vertical_resource_id": "9999000000000001",
        "cover_horizontal_resource_id": "9999000000000002",
        "publish_task_id": None,
    }
    assert written == []


def test_select_writes_both_cover_ids_onto_the_named_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """两列一起写：分两次落库会开出"竖版是新帧、横版还是上一次的"的中间态。"""
    _stub_derive(monkeypatch, _pair())
    written: List[Any] = []

    async def fake_get_task(task_id):
        return {"id": str(task_id), "user_id": USER_ID}

    async def fake_set_covers(task_id, **kw):
        written.append((task_id, kw))

    monkeypatch.setattr(dr.publish_repo, "get_task", fake_get_task)
    monkeypatch.setattr(dr.publish_repo, "set_task_covers", fake_set_covers)

    client = TestClient(_make_app())
    resp = client.post(
        "/api/v1/distribution/covers/select",
        json={**SELECT_BODY, "publish_task_id": "700"},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["publish_task_id"] == "700"
    assert written == [
        (
            700,
            {
                "vertical_resource_id": 9999000000000001,
                "horizontal_resource_id": 9999000000000002,
            },
        )
    ]


def test_select_on_someone_elses_task_is_404_and_crops_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """IDOR guard，两件事一起守：

    - 404 而不是 403 —— 403 等于确认"这个任务存在"。
    - 鉴权在裁切**之前** —— 否则权限不足的调用照样会往别人的 scope 里建两个
      resources 行，一次被拒绝的请求留下两份垃圾。
    """
    calls = _stub_derive(monkeypatch, _pair())

    async def fake_get_task(task_id):
        return {"id": str(task_id), "user_id": "somebody-else"}

    monkeypatch.setattr(dr.publish_repo, "get_task", fake_get_task)

    client = TestClient(_make_app())
    resp = client.post(
        "/api/v1/distribution/covers/select",
        json={**SELECT_BODY, "publish_task_id": "700"},
    )

    assert resp.status_code == 404
    assert calls == []


def test_select_on_a_missing_task_is_404_and_crops_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _stub_derive(monkeypatch, _pair())

    async def fake_get_task(task_id):
        return None

    monkeypatch.setattr(dr.publish_repo, "get_task", fake_get_task)

    client = TestClient(_make_app())
    resp = client.post(
        "/api/v1/distribution/covers/select",
        json={**SELECT_BODY, "publish_task_id": "700"},
    )

    assert resp.status_code == 404
    assert calls == []


def test_select_rejects_unauthenticated_callers_without_cropping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _stub_derive(monkeypatch, _pair())
    client = TestClient(_make_app(authed=False))
    resp = client.post("/api/v1/distribution/covers/select", json=dict(SELECT_BODY))
    assert resp.status_code in (401, 403, 422)
    assert calls == []


@pytest.mark.parametrize("status", [400, 404, 422])
def test_select_passes_typed_failures_through_as_their_own_status(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    """silent no-op / 裸 500 不可接受 —— 用户动作触发的每条路径都要有类型化
    回显（CLAUDE.md 已知陷阱）。"""
    _stub_derive(monkeypatch, CoverFrameError(status_code=status, detail="bad frame"))

    client = TestClient(_make_app())
    resp = client.post("/api/v1/distribution/covers/select", json=dict(SELECT_BODY))

    assert resp.status_code == status
    assert resp.json()["detail"] == "bad frame"


@pytest.mark.parametrize("bad_id", ["not-a-number", "", "700; DROP TABLE", "7.5"])
def test_select_treats_a_malformed_publish_task_id_as_not_found(
    monkeypatch: pytest.MonkeyPatch, bad_id: str
) -> None:
    """id 走 str 传输（snowflake 放不进 JS 的 2^53），所以"解析不出整数"是一条
    用户可达的输入路径，不是内部不变量。

    裸 ``int()`` 会把它变成 500 —— 一次坏输入伪装成服务故障，还绕过了整个
    404-不泄漏-存在性的姿态。畸形 id 与"任务不存在"必须给同一个答案。
    """
    calls = _stub_derive(monkeypatch, _pair())

    async def fake_get_task(task_id):
        raise AssertionError("malformed id must never reach the repository")

    monkeypatch.setattr(dr.publish_repo, "get_task", fake_get_task)

    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/distribution/covers/select",
        json={**SELECT_BODY, "publish_task_id": bad_id},
    )

    assert resp.status_code == 404
    assert calls == []


def test_select_carries_the_picked_coordinate_through_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """时间点是"用户挑的那一帧"的唯一坐标 —— 端点丢掉它、取整它、或者传错资源
    就是"挑 A 得到 B"，而那不会报任何错。"""
    calls = _stub_derive(monkeypatch, _pair())

    client = TestClient(_make_app())
    resp = client.post("/api/v1/distribution/covers/select", json=dict(SELECT_BODY))

    assert resp.status_code == 200, resp.text
    assert calls == [
        {
            "source_resource_id": "500",
            "timestamp_seconds": 15.25,
            "user_id": USER_ID,
        }
    ]


def test_select_still_accepts_the_legacy_frame_id_during_the_deploy_skew(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """前端与后端是两条独立发布链。窗口里的旧前端只有 resource id，那一次点击
    必须仍然成功 —— 否则用户看到的是一个突然坏掉的按钮。"""
    modern = _stub_derive(monkeypatch, _pair())
    legacy = _stub_derive_legacy(monkeypatch, _pair())

    client = TestClient(_make_app())
    resp = client.post(
        "/api/v1/distribution/covers/select", json={"frame_resource_id": "900"}
    )

    assert resp.status_code == 200, resp.text
    assert legacy == [{"frame_resource_id": "900", "user_id": USER_ID}]
    assert modern == []


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"source_resource_id": "500"},  # 缺秒数
        {"timestamp_seconds": 1.0},  # 缺源
        {**SELECT_BODY, "frame_resource_id": "900"},  # 两种形状混着给
        {"source_resource_id": "500", "timestamp_seconds": -1.0},  # 负秒数
    ],
)
def test_select_rejects_an_incoherent_body_without_cropping(
    monkeypatch: pytest.MonkeyPatch, body: Dict[str, Any]
) -> None:
    """形状校验在 schema 层，所以坏 body 是一个说得清的 422，不是 router 里
    ``if`` 出来的 500，也不是"用一半参数猜着干"。"""
    modern = _stub_derive(monkeypatch, _pair())
    legacy = _stub_derive_legacy(monkeypatch, _pair())

    client = TestClient(_make_app())
    resp = client.post("/api/v1/distribution/covers/select", json=body)

    assert resp.status_code == 422
    assert modern == []
    assert legacy == []
