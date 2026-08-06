"""`/session/publish` 的入口契约。

这些测试都不启动浏览器。它们守的是**在到达浏览器之前**就该被挡下来或被
正确成形的东西 —— 因为一次真发布要几分钟和几百兆，任何本可以在入口拒绝
的请求走到那一步都是纯粹的浪费，而形状不对的响应会让调用方读到
KeyError 而不是一个可处理的失败。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main
from app.schemas import SessionStatus
from tests.conftest import TEST_TOKEN

pytestmark = pytest.mark.unit

LIVE_STATE = {"cookies": [{"name": "sessionid", "value": "x"}], "origins": []}


@pytest.fixture
def client():
    return TestClient(main.app, raise_server_exceptions=False)


def _intent(**overrides):
    intent = {
        "content_type": "video",
        "media": [
            {
                "kind": "video",
                "url": "http://nous-kong:8000/storage/v1/object/sign/library/x",
                "filename": "clip.mp4",
                "content_type": "video/mp4",
                "size_bytes": 1024,
            }
        ],
        "title": "Title",
        "description": "Description",
        "topics": ["alpha"],
        "visibility": "public",
        "allow_download": True,
        "cover": None,
        "scheduled_at": None,
        "platform_options": {},
    }
    intent.update(overrides)
    return intent


def _body(**overrides):
    payload = {
        "platform": "douyin",
        "storage_state": LIVE_STATE,
        "intent": _intent(),
    }
    payload.update(overrides)
    return payload


def _auth():
    return {"X-Internal-Token": TEST_TOKEN}


# ── 鉴权 ────────────────────────────────────────────────────────


def test_missing_token_is_rejected(client):
    """这个端点收的是解密后的会话，且能代表用户发内容。裸奔一秒都不行。"""
    resp = client.post("/session/publish", json=_body())
    assert resp.status_code == 401


def test_wrong_token_is_rejected(client):
    resp = client.post(
        "/session/publish", json=_body(), headers={"X-Internal-Token": "nope"}
    )
    assert resp.status_code == 401


# ── 请求体 ──────────────────────────────────────────────────────


def test_malformed_body_is_rejected_before_any_browser_work(client):
    resp = client.post("/session/publish", json={"platform": "douyin"}, headers=_auth())
    assert resp.status_code == 422


def test_unknown_platform_answers_in_the_session_result_shape(client):
    """400 而不是 404，且是 SessionResult 形状。

    形状统一,调用方只解析一种东西;400 而非 5xx,以免被读成"这个账号的会话
    出问题了" —— 平台不支持是**请求**的问题,与账号无关。
    """
    resp = client.post(
        "/session/publish", json=_body(platform="myspace"), headers=_auth()
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == SessionStatus.FAILED.value
    assert body["success"] is False
    for key in ("message", "detail"):
        assert key in body


def test_a_schedule_outside_the_platform_window_is_refused_before_the_browser(client):
    """定时**已支持**,但窗口外的时间要在起浏览器之前就挡下(§7.7)。

    最坏的实现是忽略这个字段照常发 —— 用户以为排到了明早八点,实际此刻
    就发出去了,而且撤不回来。次坏的是把它送进浏览器,等视频传完几百兆
    之后才被平台拒绝。这里两种都不允许:请求根本没走到浏览器。
    """
    resp = client.post(
        "/session/publish",
        json=_body(intent=_intent(scheduled_at="2099-01-01T08:00:00Z")),
        headers=_auth(),
    )
    body = resp.json()
    assert body["success"] is False
    assert body["status"] == SessionStatus.FAILED.value
    assert body["detail"]["reason"] == "schedule_too_far"
    assert body["detail"]["stage"] == "intent"


def test_a_naive_scheduled_time_is_refused_rather_than_guessed_at(client):
    """没有时区的时间戳 = 差八小时的赌局,而且赌输了帖子已经发出去了。"""
    resp = client.post(
        "/session/publish",
        json=_body(intent=_intent(scheduled_at="2099-01-01T08:00:00")),
        headers=_auth(),
    )
    body = resp.json()
    assert body["success"] is False
    assert body["detail"]["reason"] == "schedule_naive_datetime"


def test_an_illegal_self_declaration_is_refused_before_the_browser(client):
    """自主声明只认平台原文那六个。非法值在入口就拒 —— 走到弹窗里才发现
    的话,视频已经传完了,而唯一能做的还是放弃这一次发布。"""
    resp = client.post(
        "/session/publish",
        json=_body(
            intent=_intent(platform_options={"self_declaration": "AI generated"})
        ),
        headers=_auth(),
    )
    body = resp.json()
    assert body["success"] is False
    assert body["detail"]["reason"] == "unknown_self_declaration"
    assert body["detail"]["stage"] == "intent"


def test_publish_response_always_carries_the_envelope(client):
    """无论走哪条失败分支,响应都得是完整信封(spec 7.8)。

    调用方按 status 分支;少一个键就是一个 KeyError 而不是一次可处理的失败。
    """
    resp = client.post(
        "/session/publish", json=_body(platform="myspace"), headers=_auth()
    )
    body = resp.json()
    for key in ("success", "status", "message"):
        assert key in body, f"响应缺少 {key}"


def test_status_value_comes_from_the_shared_enum(client):
    """返回的 status 必须是 §7.8 权威表里的值,不能自创。"""
    resp = client.post(
        "/session/publish", json=_body(platform="myspace"), headers=_auth()
    )
    assert resp.json()["status"] in {s.value for s in SessionStatus}
