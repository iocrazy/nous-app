import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.distribution_router as dr


def _make_app(monkeypatch, *, module_on=True) -> TestClient:
    async def fake_require():
        if not module_on:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Not found")

    app = FastAPI()
    app.include_router(dr.router, prefix="/api/v1")
    app.dependency_overrides[dr.require_distribution] = fake_require
    app.dependency_overrides[dr.get_current_user] = lambda: {"id": "u-1"}
    return app


def test_create_task_flag_off_404(monkeypatch):
    app = _make_app(monkeypatch, module_on=False)
    client = TestClient(app)
    resp = client.post(
        "/api/v1/distribution/tasks",
        json={"title": "x", "resource_ids": ["1"], "account_ids": ["10"]},
    )
    assert resp.status_code == 404


def test_create_task_dispatches_and_returns_accounts(monkeypatch):
    app = _make_app(monkeypatch, module_on=True)
    dispatched = {}

    # authorize every supplied account to the caller
    async def fake_authorize_account(account_id, user):
        return {"id": str(account_id), "scope_type": "user", "scope_id": "u-1"}

    async def fake_create_task(**f):
        return {
            "id": "700",
            "title": f["title"],
            "content_type": "video",
            "topics": [],
            "visibility": "public",
            "distribution_mode": "broadcast",
            "created_at": "2026-07-08T00:00:00Z",
        }

    created_accounts = []

    async def fake_create_task_account(**f):
        row = {
            "id": str(len(created_accounts) + 1),
            "account_id": f["account_id"],
            "username": "HEYGO",
            "avatar_url": None,
            "channel": f.get("channel", "h5"),
            "status": "pending",
        }
        created_accounts.append(row)
        return row

    async def fake_get_task(task_id):
        return {
            "id": "700",
            "title": "x",
            "content_type": "video",
            "topics": [],
            "visibility": "public",
            "distribution_mode": "broadcast",
            "created_at": "2026-07-08T00:00:00Z",
            "dbos_workflow_id": "wf-1",
        }

    async def fake_get_task_accounts(task_id):
        return created_accounts

    async def fake_set_wf(task_id, wf_id):
        dispatched["set_wf"] = wf_id

    class _Mgr:
        async def create(self, **kw):
            dispatched["tt_create"] = kw.get("dbos_workflow_id")
            return kw.get("dbos_workflow_id")

    async def fake_start_wf(*a, **kw):
        dispatched["start_wf"] = kw.get("workflow_id")
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

    client = TestClient(app)
    resp = client.post(
        "/api/v1/distribution/tasks",
        json={
            "title": "Launch",
            "resource_ids": ["30"],
            "account_ids": ["10", "11"],
            "channel": "h5",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == "700"
    assert len(body["accounts"]) == 2
    # task_tracking id and the dispatched workflow_id are the SAME wf_id (路线 C)
    assert dispatched["tt_create"] == dispatched["start_wf"] == dispatched["set_wf"]


def _wire_repo_spies(monkeypatch, spy):
    """把 create_task 会走到的每一处写入都换成探针。

    校验前移（图集设计 §2 D3）的全部意义就是"被拒的批次一行都不建"，
    所以这些探针里**任何一个**被碰到，前移就没做到 —— 断言的是缺席，
    不是返回码。
    """

    async def fake_create_task(**f):
        spy.setdefault("create_task", []).append(f)
        return {
            "id": "700",
            "title": f["title"],
            "content_type": f.get("content_type", "video"),
            "topics": [],
            "visibility": "public",
            "distribution_mode": "broadcast",
            "created_at": "2026-08-11T00:00:00Z",
        }

    async def fake_create_task_account(**f):
        row = {
            "id": str(len(spy.get("create_task_account", [])) + 1),
            "account_id": f["account_id"],
            "username": "HEYGO",
            "channel": f.get("channel", "h5"),
            "status": "pending",
        }
        spy.setdefault("create_task_account", []).append(row)
        return row

    async def fake_get_task(task_id):
        return {
            "id": "700",
            "title": "x",
            "content_type": "video",
            "topics": [],
            "visibility": "public",
            "distribution_mode": "broadcast",
            "created_at": "2026-08-11T00:00:00Z",
        }

    async def fake_get_task_accounts(task_id):
        return spy.get("create_task_account", [])

    async def fake_set_wf(task_id, wf_id):
        spy.setdefault("set_wf", []).append(wf_id)

    class _Mgr:
        async def create(self, **kw):
            spy.setdefault("tt_create", []).append(kw.get("dbos_workflow_id"))
            return kw.get("dbos_workflow_id")

    async def fake_start_wf(*a, **kw):
        spy.setdefault("start_wf", []).append(kw.get("workflow_id"))
        return {"mode": "dbos"}

    monkeypatch.setattr(dr.publish_repo, "create_task", fake_create_task)
    monkeypatch.setattr(
        dr.publish_repo, "create_task_account", fake_create_task_account
    )
    monkeypatch.setattr(dr.publish_repo, "get_task", fake_get_task)
    monkeypatch.setattr(dr.publish_repo, "get_task_accounts", fake_get_task_accounts)
    monkeypatch.setattr(dr.publish_repo, "set_task_workflow_id", fake_set_wf)
    monkeypatch.setattr(dr, "get_task_manager", lambda: _Mgr())
    monkeypatch.setattr(dr, "start_workflow_routed", fake_start_wf)


def _authorize_returning(accounts):
    async def fake_authorize_account(account_id, user):
        return accounts[str(account_id)]

    return fake_authorize_account


def test_image_post_mixing_session_and_oauth_accounts_creates_no_row(monkeypatch):
    """D3 的核心动机：``decide_channel`` 会把 OAuth 账号降级到 h5，一个图集
    批次于是一半走浏览器一半走 H5 交接。前移之后，混选在提交时就被拒 ——
    **没有任务行、没有 task_tracking 行、没有 workflow 派发**。"""
    app = _make_app(monkeypatch, module_on=True)
    spy = {}
    _wire_repo_spies(monkeypatch, spy)
    monkeypatch.setattr(
        dr,
        "_authorize_account",
        _authorize_returning(
            {
                "10": {"id": "10", "platform": "douyin", "auth_type": "session"},
                "11": {"id": "11", "platform": "douyin", "auth_type": "oauth"},
            }
        ),
    )
    # 生产此刻仍声明 content_types={"video"}（T7 才翻转），所以这里把画像翻过来，
    # 让拒绝理由落在"混选"这一条上而不是 unsupported_content_type。
    from dataclasses import replace

    from app.services.distribution.session_adapter import SESSION_PLATFORM_PROFILES

    monkeypatch.setitem(
        SESSION_PLATFORM_PROFILES,
        "douyin",
        replace(
            SESSION_PLATFORM_PROFILES["douyin"],
            content_types=frozenset({"video", "images"}),
        ),
    )

    client = TestClient(app)
    resp = client.post(
        "/api/v1/distribution/tasks",
        json={
            "title": "Summer Trip",
            "content_type": "images",
            "resource_ids": ["1", "2", "3"],
            "account_ids": ["10", "11"],
            "channel": "session",
        },
    )

    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["reason"] == "publish_intent_rejected"
    assert [p["reason"] for p in detail["problems"]] == ["account_not_session_bound"]
    assert detail["problems"][0]["account_id"] == "11"
    # 一行都没建，一次都没派发。
    assert spy == {}


def test_an_accepted_batch_still_creates_its_rows(monkeypatch):
    """对照组：同一条路径在放行时**确实**会建行 —— 否则上面那个"什么都没发生"
    的断言可能只是因为整个链路被打断了。"""
    app = _make_app(monkeypatch, module_on=True)
    spy = {}
    _wire_repo_spies(monkeypatch, spy)
    monkeypatch.setattr(
        dr,
        "_authorize_account",
        _authorize_returning(
            {"10": {"id": "10", "platform": "douyin", "auth_type": "session"}}
        ),
    )

    client = TestClient(app)
    resp = client.post(
        "/api/v1/distribution/tasks",
        json={
            "title": "Summer Trip",
            "content_type": "video",
            "resource_ids": ["30"],
            "account_ids": ["10"],
            "channel": "session",
        },
    )

    assert resp.status_code == 200, resp.text
    assert len(spy["create_task"]) == 1
    assert len(spy["create_task_account"]) == 1
    assert spy["start_wf"] == spy["tt_create"] == spy["set_wf"]


def test_a_platform_without_a_publisher_creates_no_row(monkeypatch):
    """小红书能绑账号但发不了。走到 workflow 也必然是一行 failed —— 提交时
    就说清楚，别在记录页上留一条注定失败的批次。"""
    app = _make_app(monkeypatch, module_on=True)
    spy = {}
    _wire_repo_spies(monkeypatch, spy)
    monkeypatch.setattr(
        dr,
        "_authorize_account",
        _authorize_returning(
            {"12": {"id": "12", "platform": "xiaohongshu", "auth_type": "session"}}
        ),
    )

    client = TestClient(app)
    resp = client.post(
        "/api/v1/distribution/tasks",
        json={
            "title": "Summer Trip",
            "resource_ids": ["30"],
            "account_ids": ["12"],
            "channel": "session",
        },
    )

    assert resp.status_code == 422, resp.text
    assert [p["reason"] for p in resp.json()["detail"]["problems"]] == [
        "publishing_not_implemented"
    ]
    assert spy == {}


def _fake_task_with_wf(scheduled_at=None):
    async def fake_get_task(task_id):
        return {
            "id": "700",
            "user_id": "u-1",
            "title": "x",
            "content_type": "video",
            "topics": [],
            "visibility": "public",
            "distribution_mode": "broadcast",
            "created_at": "2026-07-08T00:00:00Z",
            "dbos_workflow_id": "wf-old",
            "scheduled_at": scheduled_at,
        }

    return fake_get_task


def test_retry_non_terminal_task_409(monkeypatch):
    """retry_task() returning None (task not failed/cancelled/lost) must NOT
    re-dispatch a second, untracked DBOS workflow — it must 409 instead."""
    app = _make_app(monkeypatch, module_on=True)
    spy = {}

    async def fake_get_task_accounts(task_id):
        return []

    async def fake_reset_failed_accounts(task_id):
        spy["reset_failed_accounts"] = task_id

    async def fake_set_wf(task_id, wf_id):
        spy["set_wf"] = wf_id

    class _Mgr:
        async def retry_task(self, old_wf, user_id, *, new_workflow_id=None):
            spy["retry_task_called_with"] = (old_wf, user_id, new_workflow_id)
            return None  # simulate: task not in a terminal state

    async def fake_start_wf(*a, **kw):
        spy["start_wf"] = kw.get("workflow_id")
        return {"mode": "dbos"}

    monkeypatch.setattr(dr.publish_repo, "get_task", _fake_task_with_wf())
    monkeypatch.setattr(dr.publish_repo, "get_task_accounts", fake_get_task_accounts)
    monkeypatch.setattr(
        dr.publish_repo, "reset_failed_accounts", fake_reset_failed_accounts
    )
    monkeypatch.setattr(dr.publish_repo, "set_task_workflow_id", fake_set_wf)
    monkeypatch.setattr(dr, "get_task_manager", lambda: _Mgr())
    monkeypatch.setattr(dr, "start_workflow_routed", fake_start_wf)

    client = TestClient(app)
    resp = client.post("/api/v1/distribution/tasks/700/retry")

    assert resp.status_code == 409, resp.text
    assert "retry_task_called_with" in spy  # manager was consulted
    assert "reset_failed_accounts" not in spy
    assert "set_wf" not in spy
    assert "start_wf" not in spy


def test_retry_terminal_task_redispatches(monkeypatch):
    """retry_task() returning a truthy result (task WAS failed/cancelled/lost)
    must re-dispatch under the SAME new wf_id used to re-key task_tracking."""
    app = _make_app(monkeypatch, module_on=True)
    spy = {}

    async def fake_get_task_accounts(task_id):
        return []

    async def fake_reset_failed_accounts(task_id):
        spy["reset_failed_accounts"] = task_id

    async def fake_set_wf(task_id, wf_id):
        spy["set_wf"] = wf_id

    class _Mgr:
        async def retry_task(self, old_wf, user_id, *, new_workflow_id=None):
            spy["retry_task_called_with"] = (old_wf, user_id, new_workflow_id)
            return {"dbos_workflow_id": new_workflow_id}

    async def fake_start_wf(*a, **kw):
        spy["start_wf"] = kw.get("workflow_id")
        return {"mode": "dbos"}

    monkeypatch.setattr(dr.publish_repo, "get_task", _fake_task_with_wf())
    monkeypatch.setattr(dr.publish_repo, "get_task_accounts", fake_get_task_accounts)
    monkeypatch.setattr(
        dr.publish_repo, "reset_failed_accounts", fake_reset_failed_accounts
    )
    monkeypatch.setattr(dr.publish_repo, "set_task_workflow_id", fake_set_wf)
    monkeypatch.setattr(dr, "get_task_manager", lambda: _Mgr())
    monkeypatch.setattr(dr, "start_workflow_routed", fake_start_wf)

    client = TestClient(app)
    resp = client.post("/api/v1/distribution/tasks/700/retry")

    assert resp.status_code == 200, resp.text
    assert spy["reset_failed_accounts"] == 700
    assert spy["set_wf"] == spy["start_wf"]
    # same new wf_id flowed through retry_task -> set_task_workflow_id -> dispatch
    assert spy["retry_task_called_with"][2] == spy["set_wf"]


# ── 定时批次的重投（生产实证 2026-08-15） ──────────────────────────────
#
# 用户点了三次 Retry。三次都 200，三次都真的派发了 workflow，三次都在
# ``validate_publish_intent`` 被同一条规则拒掉 —— 因为重投沿用批次里存着的
# ``scheduled_at``，而那个时间早就过去了。界面上看起来是"点了没反应"。
#
# 这三条守的是同一件事：**结构上不可能成功的重投不许被接受**，而唯一合法的
# 出路（清掉定时、立即发布）必须由调用方显式说出来。


def _retry_spies(monkeypatch, *, scheduled_at):
    """把 retry 路径上的每一个副作用都插上探针，返回 (spy, app)。

    每个都要探，因为这道门失守的表现是"多做了一件事"而不是"少做了一件事"：
    派发出去的 workflow 会真的开浏览器、真的重置账号行，代价在门外面。
    """
    app = _make_app(monkeypatch, module_on=True)
    spy = {}

    async def fake_get_task_accounts(task_id):
        return []

    async def fake_reset_failed_accounts(task_id):
        spy["reset_failed_accounts"] = task_id

    async def fake_set_wf(task_id, wf_id):
        spy["set_wf"] = wf_id

    async def fake_clear_schedule(task_id):
        spy["clear_schedule"] = task_id

    async def fake_clear_music(task_id):
        spy["clear_music"] = task_id

    class _Mgr:
        async def retry_task(self, old_wf, user_id, *, new_workflow_id=None):
            spy["retry_task_called_with"] = (old_wf, user_id, new_workflow_id)
            return {"dbos_workflow_id": new_workflow_id}

    async def fake_start_wf(*a, **kw):
        spy["start_wf"] = kw.get("workflow_id")
        return {"mode": "dbos"}

    monkeypatch.setattr(
        dr.publish_repo, "get_task", _fake_task_with_wf(scheduled_at=scheduled_at)
    )
    monkeypatch.setattr(dr.publish_repo, "get_task_accounts", fake_get_task_accounts)
    monkeypatch.setattr(
        dr.publish_repo, "reset_failed_accounts", fake_reset_failed_accounts
    )
    monkeypatch.setattr(dr.publish_repo, "set_task_workflow_id", fake_set_wf)
    monkeypatch.setattr(dr.publish_repo, "clear_task_schedule", fake_clear_schedule)
    monkeypatch.setattr(dr.publish_repo, "clear_task_music", fake_clear_music)
    monkeypatch.setattr(dr, "get_task_manager", lambda: _Mgr())
    monkeypatch.setattr(dr, "start_workflow_routed", fake_start_wf)
    return spy, app


def _past_iso(**kw):
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) - timedelta(**kw)).isoformat()


def _future_iso(**kw):
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) + timedelta(**kw)).isoformat()


def test_retry_of_an_expired_scheduled_batch_is_refused_not_dispatched(monkeypatch):
    """定时时间已过 → 409 + 类型化 reason，**一个副作用都不许发生**。

    这是那个 bug 的直接回归：修复回退后本条变红（旧代码 200 并真的派发）。
    """
    spy, app = _retry_spies(monkeypatch, scheduled_at=_past_iso(hours=20))

    resp = TestClient(app).post("/api/v1/distribution/tasks/700/retry")

    assert resp.status_code == 409, resp.text
    # reason 是契约（前端按它键控文案），message 只是给日志的。
    assert resp.json()["detail"]["reason"] == "schedule_unreachable"
    # 门在最前面：连 task_manager 都不该被打扰，更不该派发 workflow。
    assert spy == {}


def test_retry_is_also_refused_while_the_schedule_is_too_close_to_finish(monkeypatch):
    """还没到、但已经近到传不完 —— 失败形态一模一样，所以同样拒。

    只判"时间是否已过去"会漏掉这一段，而漏掉的后果与原 bug 完全相同。
    """
    spy, app = _retry_spies(monkeypatch, scheduled_at=_future_iso(minutes=30))

    resp = TestClient(app).post("/api/v1/distribution/tasks/700/retry")

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["reason"] == "schedule_unreachable"
    assert spy == {}


def test_retry_mode_now_clears_the_schedule_before_dispatching(monkeypatch):
    """用户显式点了 Publish now → 清掉定时再派发。

    顺序是重点：不先清 ``scheduled_at``，workflow 读到的还是那个过期时间，
    于是"立即发布"会以完全相同的方式再失败一次。
    """
    spy, app = _retry_spies(monkeypatch, scheduled_at=_past_iso(hours=20))

    resp = TestClient(app).post(
        "/api/v1/distribution/tasks/700/retry", json={"mode": "now"}
    )

    assert resp.status_code == 200, resp.text
    assert spy["clear_schedule"] == 700
    assert spy["set_wf"] == spy["start_wf"]


def test_retry_never_clears_a_schedule_on_its_own(monkeypatch):
    """定时还在窗口内 → 照常重投，且**不许**碰 ``scheduled_at``。

    把一条本该 15:20 上线的稿子改成"现在就发"是不可撤销的，绝不能是
    "再试一次"的副作用。
    """
    spy, app = _retry_spies(monkeypatch, scheduled_at=_future_iso(days=3))

    resp = TestClient(app).post("/api/v1/distribution/tasks/700/retry")

    assert resp.status_code == 200, resp.text
    assert "clear_schedule" not in spy
    assert spy["set_wf"] == spy["start_wf"]


def test_retry_response_tells_the_client_whether_the_schedule_is_reachable(monkeypatch):
    """``schedule_state`` 必须在响应里 —— 前端据此决定画哪个按钮。

    判据是 ``SCHEDULE_MIN_LEAD``（后端常量），所以由服务端算。让前端拿
    ``scheduled_at`` 自己减一个它猜的下限，就是把同一条规则抄成第二份。
    """
    _, app = _retry_spies(monkeypatch, scheduled_at=_future_iso(days=3))
    body = TestClient(app).post("/api/v1/distribution/tasks/700/retry").json()
    assert body["schedule_state"] == "pending"


def test_immediate_batches_are_untouched_by_the_schedule_gate(monkeypatch):
    """没有定时的批次重投照旧（绝大多数发布都是这一档）。"""
    spy, app = _retry_spies(monkeypatch, scheduled_at=None)

    resp = TestClient(app).post("/api/v1/distribution/tasks/700/retry")

    assert resp.status_code == 200, resp.text
    assert resp.json()["schedule_state"] == "none"
    assert "clear_schedule" not in spy
    assert spy["set_wf"] == spy["start_wf"]


# ── 配乐没选上的批次的重投（生产实证 2026-08-16） ───────────────────────
#
# 一条图集发布挂在 ``[music_not_found]``，记录页给的是"换个写法再试"+ Retry。
# 两半都不成立：那首曲子是从平台自己的曲库面板里挑的，而重投沿用批次里存着的
# ``music_name`` / ``music_ref``（记录页改不了），所以它跑的是**同一次搜索**。
#
# 出路和定时那条同构：清掉配乐再重投 —— 但配乐是用户的意图，作品发出去之后换
# 不了歌，所以只能由调用方显式说出来。


def test_retry_with_drop_music_clears_the_track_before_dispatching(monkeypatch):
    """用户显式点了"去掉配乐发布" → 清掉曲子再派发。

    顺序是重点：workflow 是从行上读 ``music_name`` / ``music_ref`` 的，清晚了
    这一次重投照样会去开配乐弹窗、照样撞同一堵墙。
    """
    spy, app = _retry_spies(monkeypatch, scheduled_at=None)

    resp = TestClient(app).post(
        "/api/v1/distribution/tasks/700/retry", json={"drop_music": True}
    )

    assert resp.status_code == 200, resp.text
    assert spy["clear_music"] == 700
    assert spy["set_wf"] == spy["start_wf"]
    # 只动配乐这一根轴：没让用户改的定时不许被顺手改掉。
    assert "clear_schedule" not in spy


def test_retry_never_drops_music_on_its_own(monkeypatch):
    """普通重投**不许**碰配乐。

    把一条用户选好了配乐的稿子改成"没有配乐"是不可撤销的（作品发出去之后换不
    了歌），绝不能是"再试一次"的副作用 —— 与定时那条同一条纪律。
    """
    spy, app = _retry_spies(monkeypatch, scheduled_at=None)

    resp = TestClient(app).post("/api/v1/distribution/tasks/700/retry")

    assert resp.status_code == 200, resp.text
    assert "clear_music" not in spy


def test_a_retry_that_is_refused_does_not_quietly_edit_the_batch(monkeypatch):
    """重投被拒（任务不在可重投状态）→ 一个字段都不许被改。

    ``drop_music`` 是破坏性的：它扔掉用户选的曲子。一次以 409 收场的请求顺手
    改了批次，用户在界面上看不到任何变化，下一次重投却已经不带配乐了。
    """
    spy, app = _retry_spies(monkeypatch, scheduled_at=None)

    class _RefusingMgr:
        async def retry_task(self, old_wf, user_id, *, new_workflow_id=None):
            return None

    monkeypatch.setattr(dr, "get_task_manager", lambda: _RefusingMgr())

    resp = TestClient(app).post(
        "/api/v1/distribution/tasks/700/retry", json={"drop_music": True}
    )

    assert resp.status_code == 409, resp.text
    assert "clear_music" not in spy
    assert "start_wf" not in spy


def test_both_walls_at_once_needs_both_escape_hatches(monkeypatch):
    """定时过期 **且** 配乐选不上 → 只有"立即发布 + 去掉配乐"能成。

    两根轴刻意正交。合成一个枚举就表达不了这个组合，界面上就只能画一颗必然撞
    到另一堵墙的按钮 —— 正是这两次改动要消灭的东西。
    """
    spy, app = _retry_spies(monkeypatch, scheduled_at=_past_iso(hours=20))

    resp = TestClient(app).post(
        "/api/v1/distribution/tasks/700/retry",
        json={"mode": "now", "drop_music": True},
    )

    assert resp.status_code == 200, resp.text
    assert spy["clear_schedule"] == 700
    assert spy["clear_music"] == 700
    assert spy["set_wf"] == spy["start_wf"]


def test_authorize_task_cross_user_404(monkeypatch):
    """IDOR guard: a task owned by another user must 404, not 403 — existence
    of the task must not be leaked to a non-owner."""
    app = _make_app(monkeypatch, module_on=True)

    async def fake_get_task(task_id):
        return {
            "id": "700",
            "user_id": "someone-else",
            "title": "x",
            "content_type": "video",
            "topics": [],
            "visibility": "public",
            "distribution_mode": "broadcast",
            "created_at": "2026-07-08T00:00:00Z",
            "dbos_workflow_id": "wf-old",
        }

    monkeypatch.setattr(dr.publish_repo, "get_task", fake_get_task)

    client = TestClient(app)
    resp = client.get("/api/v1/distribution/tasks/700")

    assert resp.status_code == 404, resp.text


# ── 归属：一个发布批次必须记下它属于哪个 workspace ─────────────────────
#
# 用户实测：发了两条图集（都失败），镜像 issue **建出来了**，但待办界面永远
# 看不见。归属链从第一环就断了 —— 没人写 `publish_tasks.team_id` → 镜像建
# issue 时没有 team 可写 → 待办的 `team_id` AND-过滤把这些行筛掉。
#
# 个人空间 == 一个真实的 team 行（`teams.kind='personal'`，每人至多一个，由
# `uq_teams_owner_personal` 保证）。所以「个人空间没有 team 属性」在库里并不
# 成立：它有，只是一直没人填。


def _team_scope_env(monkeypatch, *, member_of=(), personal=None):
    """把两处 team 解析都换成探针：成员表与个人 team 解析器。

    两者都是 IO，而这套单元测试没有引擎；不打桩的话路由只会走 degrade 分支，
    于是「填对了 team」和「什么都没解析出来」看起来一模一样。
    """

    async def fake_user_team_ids(user_id):
        return [str(t) for t in member_of]

    async def fake_personal(user_id):
        return personal

    monkeypatch.setattr(dr, "_user_team_ids", fake_user_team_ids)
    monkeypatch.setattr(dr, "_personal_team_id", fake_personal)


def _session_account(monkeypatch):
    monkeypatch.setattr(
        dr,
        "_authorize_account",
        _authorize_returning(
            {"10": {"id": "10", "platform": "douyin", "auth_type": "session"}}
        ),
    )


def _publish(client, **over):
    body = {
        "title": "Summer Trip",
        "resource_ids": ["30"],
        "account_ids": ["10"],
        "channel": "session",
    }
    body.update(over)
    return client.post("/api/v1/distribution/tasks", json=body)


def test_publish_records_the_requested_workspace_team(monkeypatch):
    """带 team_id 的批次落库时必须带着它 —— 归属链的第一环。"""
    app = _make_app(monkeypatch, module_on=True)
    spy = {}
    _wire_repo_spies(monkeypatch, spy)
    _session_account(monkeypatch)
    _team_scope_env(
        monkeypatch, member_of=["310812366953241", "42"], personal="310812366953241"
    )

    resp = _publish(TestClient(app), team_id="42")

    assert resp.status_code == 200, resp.text
    # 字符串进、字符串出：Snowflake 全程不 Number() 化（仓库层才转 bigint bind）。
    assert spy["create_task"][0]["team_id"] == "42"


def test_publish_without_a_team_falls_back_to_the_personal_team(monkeypatch):
    """省略 team_id（缓存住的老前端包 / 其它客户端）不能再落成 NULL。

    用户原话：「自己创建的就是自己任务……如果有也是个人 team」。个人 team 是
    单成员的，填它不会把任何东西暴露给别人，却让待办的 team 过滤天然命中。
    """
    app = _make_app(monkeypatch, module_on=True)
    spy = {}
    _wire_repo_spies(monkeypatch, spy)
    _session_account(monkeypatch)
    _team_scope_env(
        monkeypatch, member_of=["310812366953241"], personal="310812366953241"
    )

    resp = _publish(TestClient(app))

    assert resp.status_code == 200, resp.text
    assert spy["create_task"][0]["team_id"] == "310812366953241"


def test_publish_into_a_team_the_caller_is_not_in_is_refused(monkeypatch):
    """IDOR：team_id 由客户端提供，不校验就等于让任何人往别人的待办里投任务。

    断言的是**缺席** —— 一行都没建，与 §2 D3 前移门禁同口径。
    """
    app = _make_app(monkeypatch, module_on=True)
    spy = {}
    _wire_repo_spies(monkeypatch, spy)
    _session_account(monkeypatch)
    _team_scope_env(
        monkeypatch, member_of=["310812366953241"], personal="310812366953241"
    )

    resp = _publish(TestClient(app), team_id="999999")

    assert resp.status_code == 403, resp.text
    assert spy == {}


def test_publish_survives_an_unresolvable_personal_team(monkeypatch):
    """解析不出个人 team 时降级为 NULL，**不能**让发布失败。

    归属是管理视图的装饰，不是发布的前置条件；为了填一个 id 而拒掉用户真正
    想做的事，比归属丢失更糟。
    """
    app = _make_app(monkeypatch, module_on=True)
    spy = {}
    _wire_repo_spies(monkeypatch, spy)
    _session_account(monkeypatch)
    _team_scope_env(monkeypatch, member_of=[], personal=None)

    resp = _publish(TestClient(app))

    assert resp.status_code == 200, resp.text
    assert spy["create_task"][0]["team_id"] is None
