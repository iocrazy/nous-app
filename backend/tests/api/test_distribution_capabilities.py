"""``GET /distribution/capabilities`` —— 能力声明的唯一下发口（图集设计 D1）。

前端曾经自带一张 ``components/Distribution/capabilities.ts``，靠注释里的
「必须与后端同一个 PR 落地」维持同步。这个端点让那张表可以整个删掉：页面不再
持有任何能力常量，所以浏览器侧真的实现图集那天，**前端不需要发版**。

这里钉四件事：

* 响应是 profile 的**投影**，不是第二份手写声明 —— 改 profile 就该改响应；
* 集合字段**排过序**，否则 frozenset 的迭代顺序随进程 hash 种子变，同一份代码
  每次重启返回的 JSON 都不同；
* ``supports_publishing=False`` 的平台，能力字段**置空 + is_placeholder** ——
  它们 profile 里写的是占位值，原样下发等于用一个看起来权威的 API 响应给猜测
  背书；
* 与隔壁端点同一套认证 + 同一个模块 ACCESS 门。
"""

from __future__ import annotations

import dataclasses

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import app.api.distribution_router as dr
from app.services.distribution.session_adapter import (
    SESSION_PLATFORM_PROFILES,
    project_capability,
)

_USER = "11111111-1111-1111-1111-111111111111"
_PATH = "/api/v1/distribution/capabilities"


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(dr.router, prefix="/api/v1")
    app.dependency_overrides[dr.require_distribution] = lambda: None
    app.dependency_overrides[dr.get_current_user] = lambda: {"id": _USER}
    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_make_app())


def test_douyin_reports_video_only_today(client):
    """用户可见口径：Images tab 现在该是灰的，而这是端点说的。

    钉 ``["video"]`` 这个具体值，不是钉"有 content_types 字段" —— T7 翻转声明
    时这条会红，那正是它该做的：翻转是一次明确的、要连带改浏览器实现的改动。
    """
    body = client.get(_PATH).json()

    douyin = body["platforms"]["douyin"]
    assert douyin["supports_publishing"] is True
    assert douyin["is_placeholder"] is False
    assert douyin["content_types"] == ["video"]


def test_every_collection_field_is_sorted(client):
    """排序不是洁癖：源头是 frozenset。

    不排序的话，同一份代码在两次进程启动之间返回的 JSON 就不同（PYTHONHASHSEED
    随机化），任何缓存、快照或 diff 都会漂。
    """
    for cap in client.get(_PATH).json()["platforms"].values():
        for field in (
            "content_types",
            "video_extensions",
            "image_extensions",
            "self_declarations",
        ):
            assert cap[field] == sorted(cap[field]), f"{cap['platform']}.{field} 没排序"


def test_scheduling_window_is_projected_as_seconds(client):
    """profile 用两个 timedelta 表达定时窗口，前端要的是布尔 + 秒数。

    换算在后端做一次，而不是让每个调用方自己解释「两个 None 意味着没有定时
    能力」—— 那条规则一旦被复述，就会有人复述错。
    """
    douyin = client.get(_PATH).json()["platforms"]["douyin"]
    profile = SESSION_PLATFORM_PROFILES["douyin"]

    assert douyin["supports_scheduling"] is True
    assert douyin["schedule_min_lead_seconds"] == int(
        profile.schedule_min_lead.total_seconds()
    )
    assert douyin["schedule_max_ahead_seconds"] == int(
        profile.schedule_max_ahead.total_seconds()
    )


def test_a_platform_that_cannot_publish_ships_no_capability_values(client):
    """占位值不下发。

    ``session_adapter.py`` 里那段注释的原话：这些 content_types / 扩展名是
    **占位**，等真正实现发布时要对着平台实测填准。把它们放进 API 响应，就是
    把猜测升格成看起来权威的事实 —— 这正是本次要消灭的病，换个地方犯不算修。
    """
    body = client.get(_PATH).json()

    non_publishing = [
        name
        for name, p in SESSION_PLATFORM_PROFILES.items()
        if not p.supports_publishing
    ]
    assert non_publishing, "没有未发布平台，本用例形同虚设"

    for name in non_publishing:
        cap = body["platforms"][name]
        assert cap["supports_publishing"] is False
        assert cap["is_placeholder"] is True
        # 关键：profile 里那些占位值一个都没漏出来。
        assert set(SESSION_PLATFORM_PROFILES[name].content_types), "占位值前提消失了"
        assert cap["content_types"] == []
        assert cap["video_extensions"] == []
        assert cap["image_extensions"] == []
        assert cap["max_images"] is None
        assert cap["min_images"] is None


def test_the_response_follows_the_profile_rather_than_a_hardcoded_copy(client):
    """证明这是投影而不是手抄的第二份声明。

    改 profile（一个不存在的测试平台），响应必须跟着变。这条如果只断言现有
    平台的值，就无法区分"从 profile 读的"和"照着 profile 抄了一份常量"。
    """
    probe = dataclasses.replace(
        SESSION_PLATFORM_PROFILES["douyin"],
        platform="testonly",
        content_types=frozenset({"images", "video"}),
        max_images=9,
        min_images=2,
        max_title_len=42,
    )
    SESSION_PLATFORM_PROFILES["testonly"] = probe
    try:
        cap = client.get(_PATH).json()["platforms"]["testonly"]
    finally:
        SESSION_PLATFORM_PROFILES.pop("testonly")

    assert cap["content_types"] == ["images", "video"]
    assert cap["max_images"] == 9
    assert cap["min_images"] == 2
    assert cap["max_title_len"] == 42


def test_projection_is_pure_and_needs_no_request():
    """投影函数本身可单测 —— 端点里没有它之外的逻辑。

    这保证「加一个 profile 字段」的成本是改一处，不是改一处再想起来还有个
    router 也要动。
    """
    cap = project_capability(SESSION_PLATFORM_PROFILES["douyin"])
    assert cap.platform == "douyin"
    assert cap.content_types == sorted(
        SESSION_PLATFORM_PROFILES["douyin"].content_types
    )


def test_capabilities_is_behind_the_module_access_switch():
    """模块关掉 → 404，和没注册过这条路由一样（隔壁端点同款）。"""
    app = _make_app()

    def _off():
        raise HTTPException(status_code=404, detail="Not found")

    app.dependency_overrides[dr.require_distribution] = _off
    assert TestClient(app).get(_PATH).status_code == 404


def test_capabilities_requires_authentication():
    """匿名拿不到：这描述的是本部署接了哪些平台、各自上限，属内部能力信息。

    与隔壁 ``/browser/health`` 同一形状：``get_current_user`` 把
    ``Authorization`` 声明成必填 Header，缺了它在依赖解析阶段就被拒。这里钉的
    是"不是 200"，不是那个具体状态码。
    """
    app = FastAPI()
    app.include_router(dr.router, prefix="/api/v1")
    app.dependency_overrides[dr.require_distribution] = lambda: None

    assert TestClient(app).get(_PATH).status_code != 200
