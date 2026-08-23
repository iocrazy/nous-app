"""``/distribution/covers/grab-frame`` —— 手动抓帧当参考图（封面工作室）。

这个端点与 ``/covers/select`` 共用抽帧那一半，**只有落点不同**，所以这里测的
就是那个差别以及它周围的闸门：

- **落点**：一个 ``generated_media`` 行，不是两个 ``resources`` 行。返回的 URL
  必须正好是 ``/api/v1/generated-media/{id}/cover`` —— 那是出图链路
  (``generated_media_service.GENERATED_MEDIA_URL_RE``) 唯一认得的形状，写错不会
  报错，只会让这一帧永远进不了模型。
- **不裁**：这一帧说的是"我片子里有什么"，裁掉边缘只丢信息；构图由模型按 3:4
  重新生成。所以 ``_crop_and_persist_pair`` 一次都不该被调到。
- **模块开关 / 鉴权**：关掉的模块与未认证的调用都不能进到业务代码。
- **类型化失败透传**：``CoverFrameError.status_code`` 原样变成 HTTP 状态码 ——
  抽不出那一帧要说 422，不能给一张空白参考图。
- **畸形时间点是正常路径**：NaN / inf / 负数是用户可达输入（前端把时间轴上的数
  字原样回传），必须 4xx 而不是一路飘到 ffmpeg 的 ``-ss`` 变成 500。
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import app.api.distribution_router as dr
from app.core.exceptions import register_exception_handlers
from app.services.distribution.cover_frames import CoverFrameError, GrabbedFrame

USER_ID = "u-1"
GEN_ID = "341582104263581"  # > 2^53：wire 上必须是字符串


def _make_app(*, module_on: bool = True, authed: bool = True) -> FastAPI:
    async def fake_require():
        if not module_on:
            raise HTTPException(status_code=404, detail="Not found")

    app = FastAPI()
    # ⚠️ 真实的异常处理器，不是裸 FastAPI()。少了它，RequestValidationError 会直接
    # 冒泡出 TestClient 而不是变成 422 —— 于是"畸形输入被拒"这类测试会以异常的形态
    # 失败，看起来像端点崩了，实际是测试骨架与生产不一致。
    register_exception_handlers(app)
    app.include_router(dr.router, prefix="/api/v1")
    app.dependency_overrides[dr.require_distribution] = fake_require
    if authed:
        app.dependency_overrides[dr.get_current_user] = lambda: {"id": USER_ID}
    return app


class _Spy:
    """Records what the endpoint set in motion, so a rejected request can be
    shown to have set NOTHING in motion."""

    def __init__(self) -> None:
        self.grabbed: List[Dict[str, Any]] = []


@pytest.fixture
def spy(monkeypatch) -> _Spy:
    s = _Spy()

    async def _grab(*, source_resource_id, timestamp_seconds, user_id, repo=None):
        s.grabbed.append(
            {
                "source_resource_id": source_resource_id,
                "timestamp_seconds": timestamp_seconds,
                "user_id": user_id,
            }
        )
        return GrabbedFrame(
            generated_media_id=GEN_ID,
            url=f"/api/v1/generated-media/{GEN_ID}/cover",
            timestamp_seconds=timestamp_seconds,
        )

    monkeypatch.setattr(
        "app.services.distribution.cover_frames.grab_frame_as_reference", _grab
    )
    return s


BODY = {"source_resource_id": "900", "timestamp_seconds": 3.1}


def test_returns_a_generated_media_reference_not_a_resource(spy):
    client = TestClient(_make_app())

    resp = client.post("/api/v1/distribution/covers/grab-frame", json=BODY)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # 字面量断言：出图链路只认这一种形状，"差不多对"的 URL 是查不出来的静默丢弃。
    assert body["url"] == f"/api/v1/generated-media/{GEN_ID}/cover"
    assert body["generated_media_id"] == GEN_ID
    # 成品封面那条路会回 cover_vertical/horizontal_resource_id；这条不该有。
    assert "cover_vertical_resource_id" not in body
    assert "cover_horizontal_resource_id" not in body


def test_the_snowflake_id_crosses_the_wire_as_a_string(spy):
    client = TestClient(_make_app())

    body = client.post("/api/v1/distribution/covers/grab-frame", json=BODY).json()

    assert isinstance(body["generated_media_id"], str)


def test_the_timestamp_is_echoed_so_the_pool_can_caption_it(spy):
    client = TestClient(_make_app())

    body = client.post(
        "/api/v1/distribution/covers/grab-frame",
        json={"source_resource_id": "900", "timestamp_seconds": 66.42},
    ).json()

    assert body["timestamp_seconds"] == pytest.approx(66.42)


def test_never_crops(spy, monkeypatch):
    """裁切属于成品封面那条路。这一帧是"我片子里有什么"，裁掉边缘只丢信息。"""
    called: List[Any] = []

    async def _boom(*a, **kw):
        called.append(1)
        raise AssertionError("grab-frame must not crop")

    monkeypatch.setattr(
        "app.services.distribution.cover_frames._crop_and_persist_pair", _boom
    )
    client = TestClient(_make_app())

    resp = client.post("/api/v1/distribution/covers/grab-frame", json=BODY)

    assert resp.status_code == 200
    assert called == []
    # 与上面成对：证明请求真的走通了，否则"没裁切"是空过的。
    assert len(spy.grabbed) == 1


def test_module_off_is_404_and_grabs_nothing(spy):
    client = TestClient(_make_app(module_on=False))

    resp = client.post("/api/v1/distribution/covers/grab-frame", json=BODY)

    assert resp.status_code == 404
    assert spy.grabbed == []


def test_unauthenticated_grabs_nothing(spy):
    client = TestClient(_make_app(authed=False))

    resp = client.post("/api/v1/distribution/covers/grab-frame", json=BODY)

    assert resp.status_code in (401, 403, 422)
    assert spy.grabbed == []


@pytest.mark.parametrize(
    "status,detail",
    [
        (404, "source video file is missing on disk"),
        (422, "could not re-read the frame at 3.1s — sample the video again"),
        (504, "re-reading the chosen frame timed out"),
    ],
)
def test_typed_failures_pass_through_unchanged(monkeypatch, status, detail):
    """抽不出那一帧必须说出来。给用户一张空白参考图，比一个 422 差得多 ——
    它会一路走到生成，然后产出一张跟他视频无关的封面。"""

    async def _fail(**kw):
        raise CoverFrameError(status_code=status, detail=detail)

    monkeypatch.setattr(
        "app.services.distribution.cover_frames.grab_frame_as_reference", _fail
    )
    client = TestClient(_make_app())

    resp = client.post("/api/v1/distribution/covers/grab-frame", json=BODY)

    assert resp.status_code == status
    if status < 500:
        assert detail[:20] in resp.text
    else:
        # ⚠️ 5xx 的正文是**刻意**被通用化的（app/core/exceptions.py）：5xx detail
        # 经常带着 str(exception)，会漏出列名、路径、traceback 片段。所以这一档
        # 只有状态码是契约，文案不是 —— 断言它会把一条安全措施钉成"回归"。
        assert "Internal server error" in resp.text
        assert detail not in resp.text


def test_a_negative_timestamp_is_rejected_by_the_schema(spy):
    client = TestClient(_make_app())

    resp = client.post(
        "/api/v1/distribution/covers/grab-frame",
        json={"source_resource_id": "900", "timestamp_seconds": -1.0},
    )

    assert resp.status_code == 422, resp.text
    assert spy.grabbed == []


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_a_nonfinite_timestamp_is_4xx_not_500(token):
    """⚠️ 这三个必须用**原始报文**发，而且**不能 mock 服务层**。

    两个坑，各绊了一次：

    1. ``json={"timestamp_seconds": float("nan")}`` 根本发不出去 —— Python 自己
       的编码器就拒绝了。但这条路是可达的：FastAPI 解析请求体用的 ``json.loads``
       **默认接受**裸的 ``NaN`` / ``Infinity`` 记号（不是标准 JSON，但解析器认）。
       用 ``json=`` 写这条测试，测到的是测试工具的限制，不是服务端的行为。
    2. 三个值走的是**不同的层**：``NaN >= 0`` 与 ``-inf >= 0`` 恒为假，被 schema
       的 ``ge=0`` 拦下；而 ``inf >= 0`` 为真，**穿过 schema**，靠服务层的
       ``math.isfinite`` 拦。把服务层 mock 掉（像本文件其他测试那样）就等于把唯一
       拦得住 ``Infinity`` 的东西拆了 —— 它会一路走到响应序列化才炸成 500。

    所以这里用真实的 ``grab_frame_as_reference``：它的 isfinite 检查在任何 DB 访问
    之前，不需要库也能跑到。
    """
    client = TestClient(_make_app())

    resp = client.post(
        "/api/v1/distribution/covers/grab-frame",
        content=f'{{"source_resource_id": "900", "timestamp_seconds": {token}}}',
        headers={"Content-Type": "application/json"},
    )

    assert 400 <= resp.status_code < 500, f"{token} → {resp.status_code}: {resp.text}"
