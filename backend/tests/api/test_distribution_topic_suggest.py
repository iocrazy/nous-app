"""``GET /distribution/topics/suggest`` —— 话题实时建议。

这一套的重心不是"能返回列表"，而是**空列表只允许有一个含义**。

发布页的下拉在两种情况下都是空的：上游说"这个词没有话题"，和上游根本没答话。
如果后端把后者也表达成 200 + `[]`，用户对着一个空下拉永远分不清是自己输错了
还是我们挂了 —— 这正是本仓库在 ``attachment_failures`` 上栽过的那一族（后端
返回了原因，前端从来没读）。所以这里每一条失败路径都单独钉一遍：状态码非 2xx，
且 ``detail.reason`` 是可分支的码。

mock 用的是**上游真实响应形状**（``cha_name`` / ``cid`` / ``view_count`` /
``group_id`` / ``tag``，外层 ``status_code``）—— 抄自 PR #1830 阶段二勘探的实测
返回，不是按前端书写习惯美化过的版本。
"""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.distribution_router as dr
from app.services.distribution import topic_suggest as ts

_PATH = "/api/v1/distribution/topics/suggest"
_UPSTREAM = ts.DOUYIN_SUGGEST_URL

# 实测返回的一段（南通，2026-08 勘探）。view_count 是原始整数，UI 上的
# `309.1亿` 是格式化出来的；cid 空串 = 平台还没有这个话题实体。
_REAL_BODY = {
    "sug_list": [
        {
            "cha_name": "南通",
            "view_count": 30909355369,
            "cid": "1583761434171470",
            "group_id": "7000000000000000000",
            "tag": 0,
        },
        {
            "cha_name": "南通天气",
            "view_count": 12345678,
            "cid": "",
            "group_id": "",
            "tag": 0,
        },
    ],
    "status_code": 0,
}


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(dr.router, prefix="/api/v1")
    app.dependency_overrides[dr.require_distribution] = lambda: None
    app.dependency_overrides[dr.get_current_user] = lambda: {"id": "u-1"}
    return app


@pytest.fixture(autouse=True)
def _clean_cache():
    """进程内缓存跨测试泄漏 = 后一条测试悄悄命中前一条的结果，等于没测。"""
    ts.clear_suggest_cache()
    yield
    ts.clear_suggest_cache()


@pytest.fixture
def client() -> TestClient:
    return TestClient(_make_app())


@respx.mock
def test_success_maps_upstream_shape(client):
    """名字 / 实体 id / 播放量原样过来，空 cid 被明确标成 is_new。"""
    route = respx.get(_UPSTREAM).mock(return_value=httpx.Response(200, json=_REAL_BODY))

    body = client.get(_PATH, params={"platform": "douyin", "keyword": "南通"}).json()

    assert route.called
    # aid 是唯一必需参数（去掉它上游回空列表）——它必须真的在请求里。
    assert route.calls[0].request.url.params["aid"] == ts.DOUYIN_AID
    assert route.calls[0].request.url.params["keyword"] == "南通"

    assert body["platform"] == "douyin"
    assert body["suggestions"][0] == {
        "name": "南通",
        "topic_id": "1583761434171470",
        "view_count": 30909355369,
        "is_new": False,
    }
    # 新话题：cid 为空不是"缺数据"，是"平台还没有这个实体"。
    assert body["suggestions"][1]["topic_id"] == ""
    assert body["suggestions"][1]["is_new"] is True


@respx.mock
def test_empty_sug_list_is_a_result_not_an_error(client):
    """上游正常作答但没有建议 —— 200 + 空列表，这是空列表**唯一**合法的来源。"""
    respx.get(_UPSTREAM).mock(
        return_value=httpx.Response(200, json={"sug_list": [], "status_code": 0})
    )
    resp = client.get(_PATH, params={"keyword": "zzzz-no-such-topic"})
    assert resp.status_code == 200
    assert resp.json()["suggestions"] == []


@respx.mock
def test_upstream_http_error_is_typed_not_empty(client):
    """守卫：上游 500 必须变成 502 + ``upstream_status``。

    ⚠️ 反向验证的靶子。把服务层的 raise 换成 `return []`，这条立刻红 —— 那正是
    "接口挂了但 UI 显示成没搜到"的形状。
    """
    respx.get(_UPSTREAM).mock(return_value=httpx.Response(500, text="boom"))
    resp = client.get(_PATH, params={"keyword": "南通"})
    assert resp.status_code == 502
    assert resp.json()["detail"]["reason"] == ts.REASON_UPSTREAM_STATUS


@respx.mock
def test_network_failure_is_typed(client):
    """够不着上游（超时 / DNS / 连接被拒）→ ``upstream_unreachable``。"""
    respx.get(_UPSTREAM).mock(side_effect=httpx.ConnectTimeout("timed out"))
    resp = client.get(_PATH, params={"keyword": "南通"})
    assert resp.status_code == 502
    assert resp.json()["detail"]["reason"] == ts.REASON_UPSTREAM_UNREACHABLE


@respx.mock
def test_shape_drift_is_typed(client):
    """上游哪天加了签名/换了字段，最先撞上的就是这道门 —— 它必须响。"""
    respx.get(_UPSTREAM).mock(
        return_value=httpx.Response(200, json={"status_code": 8, "verify": "captcha"})
    )
    resp = client.get(_PATH, params={"keyword": "南通"})
    assert resp.status_code == 502
    assert resp.json()["detail"]["reason"] == ts.REASON_UPSTREAM_SHAPE


@respx.mock
def test_unsupported_platform_says_so(client):
    """只有抖音有实现。别的平台**明说**不支持，不返回空列表假装没搜到。"""
    route = respx.get(_UPSTREAM).mock(return_value=httpx.Response(200, json=_REAL_BODY))
    resp = client.get(_PATH, params={"platform": "xiaohongshu", "keyword": "南通"})
    assert resp.status_code == 400
    assert resp.json()["detail"]["reason"] == ts.REASON_PLATFORM_UNSUPPORTED
    assert not route.called  # 也没有顺手去问抖音


@respx.mock
def test_hash_only_keyword_is_rejected_typed(client):
    """`#` 剥掉之后什么都不剩 —— 请求不成立，说清楚而不是空跑一次上游。"""
    route = respx.get(_UPSTREAM).mock(return_value=httpx.Response(200, json=_REAL_BODY))
    resp = client.get(_PATH, params={"keyword": "#"})
    assert resp.status_code == 400
    assert resp.json()["detail"]["reason"] == ts.REASON_KEYWORD_EMPTY
    assert not route.called


@respx.mock
def test_second_identical_call_is_served_from_cache(client):
    """短 TTL 缓存：同一个词打两次，上游只被问一次。

    打字联想天然会反复问同一个词（退格重打、切标签页），这一层是我们对上游的
    自我约束 —— 它是私有接口，被我们打出限流谁也修不了。
    """
    route = respx.get(_UPSTREAM).mock(return_value=httpx.Response(200, json=_REAL_BODY))

    first = client.get(_PATH, params={"keyword": "南通"}).json()
    second = client.get(_PATH, params={"keyword": " #南通 "}).json()  # 同一个词

    assert route.call_count == 1
    assert first["cached"] is False
    assert second["cached"] is True
    assert second["suggestions"] == first["suggestions"]


@respx.mock
def test_failures_are_not_cached(client):
    """缓存一次失败 = 把一次抖动放大成 5 分钟的功能不可用。"""
    respx.get(_UPSTREAM).mock(
        side_effect=[
            httpx.Response(500, text="boom"),
            httpx.Response(200, json=_REAL_BODY),
        ]
    )
    assert client.get(_PATH, params={"keyword": "南通"}).status_code == 502
    assert client.get(_PATH, params={"keyword": "南通"}).status_code == 200


# ── topic_refs 真的落到发布任务里（mig 426） ──────────────────────


def _publish_app(monkeypatch, spy) -> FastAPI:
    """一个只为"提交载荷里的 topic_refs 有没有走到 repo"服务的最小发布链。"""
    app = _make_app()

    async def fake_authorize_account(account_id, user):
        return {
            "id": str(account_id),
            "platform": "douyin",
            "auth_type": "session",
            "scope_type": "user",
            "scope_id": "u-1",
        }

    async def fake_create_task(**f):
        spy["create_task"] = f
        row = {
            "id": "700",
            "title": f["title"],
            "content_type": "video",
            "topics": f.get("topics") or [],
            "topic_refs": f.get("topic_refs") or [],
            "visibility": "public",
            "distribution_mode": "broadcast",
            "created_at": "2026-08-13T00:00:00Z",
        }
        spy["created_row"] = row
        return row

    async def fake_create_task_account(**f):
        return {
            "id": "1",
            "account_id": f["account_id"],
            "username": "HEYGO",
            "channel": f.get("channel", "h5"),
            "status": "pending",
        }

    async def fake_get_task(task_id):
        return spy["created_row"]

    async def fake_get_task_accounts(task_id):
        return []

    async def fake_set_wf(task_id, wf_id):
        return None

    class _Mgr:
        async def create(self, **kw):
            return kw.get("dbos_workflow_id")

    async def fake_start_wf(*a, **kw):
        return {"mode": "dbos"}

    monkeypatch.setattr(dr, "_authorize_account", fake_authorize_account)
    monkeypatch.setattr(dr.publish_repo, "create_task", fake_create_task)
    monkeypatch.setattr(
        dr.publish_repo, "create_task_account", fake_create_task_account
    )
    monkeypatch.setattr(dr.publish_repo, "get_task", fake_get_task)
    monkeypatch.setattr(dr.publish_repo, "get_task_accounts", fake_get_task_accounts)
    monkeypatch.setattr(dr.publish_repo, "set_task_workflow_id", fake_set_wf)
    monkeypatch.setattr(dr, "get_task_manager", lambda: _Mgr())
    monkeypatch.setattr(dr, "start_workflow_routed", fake_start_wf)
    return app


def test_topic_refs_reach_the_repository_and_come_back(monkeypatch):
    """守卫：从下拉里选中的话题，它的 ``topic_id`` 必须一路存到发布任务。

    ⚠️ 反向验证的靶子。把路由里那行 ``topic_refs=[...]`` 删掉（或让前端不带这
    个字段），这条立刻红 —— cid 只在用户选中那一刻存在，漏一次就永远补不回来。

    同时钉**回显**：写进去读不回来，跟没存一样没人能证明。
    """
    spy: dict = {}
    client = TestClient(_publish_app(monkeypatch, spy))

    resp = client.post(
        "/api/v1/distribution/tasks",
        json={
            "title": "Launch",
            "resource_ids": ["30"],
            "account_ids": ["10"],
            "channel": "session",
            "topics": ["南通", "handtyped"],
            "topic_refs": [
                {
                    "name": "南通",
                    "topic_id": "1583761434171470",
                    "view_count": 30909355369,
                },
                # 手打的话题没有 cid —— 它不携带 topics 之外的信息，应被丢掉。
                {"name": "handtyped", "topic_id": "", "view_count": 0},
            ],
        },
    )

    assert resp.status_code == 200, resp.text
    stored = spy["create_task"]["topic_refs"]
    assert stored == [
        {"name": "南通", "topic_id": "1583761434171470", "view_count": 30909355369}
    ]
    assert resp.json()["topic_refs"] == stored
