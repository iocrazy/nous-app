"""publish_readback (P1-3) — 到点回读校验。

守两类东西：

* **纯判断**（``select_due`` / ``select_abandoned`` / ``verdict_for``）——
  节奏、重试预算、以及三分法（上线 / 没上线 / 没问出来）。
* **``_verify_one`` 的接线** —— 结论真的落到了 ``publish_task_accounts``、
  基建失败真的什么都没判、会话真的写回去了。

反向验证（PR 描述里贴了输出）：
  * 把 ``verdict_for`` 的第 3 支改成"一律 not_live"（即合并"没问出来"与
    "没上线"）→ ``test_infra_failure_is_never_a_verdict_about_the_post`` 红。
  * 删掉 ``_verify_one`` 里的 ``record_verification`` 调用 →
    ``test_not_live_readback_is_recorded_with_a_typed_reason`` 红。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

import app.workflows.publish_readback as wf

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)


def _row(**overrides: Any) -> dict:
    row = {
        "id": "1001",
        "account_id": "2001",
        "platform": "douyin",
        "title": "Autumn Harvest Field Notes",
        "verify_state": None,
        "verify_attempts": 0,
        "verify_checked_at": None,
        "scheduled_at": NOW - timedelta(hours=3),
        "published_at": NOW - timedelta(hours=6),
    }
    row.update(overrides)
    return row


class TestSelectDue:
    def test_a_scheduled_row_past_go_live_is_due(self):
        assert wf.select_due([_row()], now=NOW) == [_row()]

    def test_a_row_still_inside_the_grace_window_waits(self):
        """到点那一刻平台自己还在处理；立刻去查必然看到"审核中"，
        白白烧掉一次重试预算。"""
        row = _row(scheduled_at=NOW - timedelta(minutes=1))
        assert wf.select_due([row], now=NOW, grace_s=600) == []

    def test_a_row_checked_recently_is_held_off(self):
        row = _row(verify_checked_at=NOW - timedelta(minutes=5))
        assert wf.select_due([row], now=NOW, min_interval_s=1800) == []

    def test_a_row_out_of_budget_is_not_retried(self):
        row = _row(verify_attempts=5)
        assert wf.select_due([row], now=NOW, max_attempts=5) == []

    def test_batch_size_is_capped(self):
        rows = [_row(id=str(i)) for i in range(20)]
        assert len(wf.select_due(rows, now=NOW, max_rows=5)) == 5

    def test_a_row_with_no_timestamps_is_still_reachable(self):
        """数据缺失不该让一行永远卡在队列里没人管 —— 那是静默挂着的另一种
        写法。"""
        row = _row(scheduled_at=None, published_at=None)
        assert wf.select_due([row], now=NOW) == [row]

    def test_naive_timestamps_are_read_as_utc_not_local(self):
        """一个 8 小时的时区错会让门提前或推迟半天放行，而没人会去复核。"""
        row = _row(scheduled_at=datetime(2026, 8, 10, 11, 0))  # naive
        assert wf.select_due([row], now=NOW, grace_s=600) == [row]


class TestSelectAbandoned:
    def test_budget_exhausted_and_still_pending_gets_closed_out(self):
        """否则它**永远停在 pending**，而 pending 让待办一直挂着不动 ——
        没结论、没报错、没人知道。"""
        rows = [_row(verify_attempts=5, verify_state="pending")]
        assert wf.select_abandoned(rows, max_attempts=5) == rows

    def test_a_row_that_reached_a_verdict_is_left_alone(self):
        for state in ("verified", "not_live", "abandoned", "not_supported"):
            rows = [_row(verify_attempts=9, verify_state=state)]
            assert wf.select_abandoned(rows, max_attempts=5) == [], state

    def test_a_row_with_budget_left_is_not_abandoned(self):
        rows = [_row(verify_attempts=2, verify_state="pending")]
        assert wf.select_abandoned(rows, max_attempts=5) == []


class TestVerdictFor:
    def test_live_is_verified(self):
        state, detail = wf.verdict_for({"status": "published"}, attempts_before=0)
        assert state == wf.VERIFY_VERIFIED
        assert detail is None

    @pytest.mark.parametrize(
        "reason", ["rejected", "under_review", "still_scheduled", "not_found"]
    )
    def test_not_published_is_not_live_and_keeps_the_typed_reason(self, reason):
        """原因原样带出来：它决定用户是去申诉、去等、还是重发。少了它，
        「没发出去」是一条无法据以行动的通知。"""
        state, detail = wf.verdict_for(
            {
                "status": "not_published",
                "message": "platform says so",
                "detail": {"reason": reason},
            },
            attempts_before=0,
        )
        assert state == wf.VERIFY_NOT_LIVE
        assert detail.startswith(f"[{reason}]")

    def test_missing_readback_support_is_its_own_verdict(self):
        """平台没有回读能力是**我们的**覆盖缺口，不是这条作品出了问题。"""
        state, detail = wf.verdict_for(
            {"status": "not_published", "detail": {"reason": "not_supported"}},
            attempts_before=0,
        )
        assert state == wf.VERIFY_NOT_SUPPORTED
        assert state not in wf.BLOCKING_VERIFY_STATES

    def test_infra_failure_with_budget_left_is_pending(self):
        state, _ = wf.verdict_for(
            {"status": "failed", "detail": {"error_kind": "unreachable"}},
            attempts_before=0,
            max_attempts=5,
        )
        assert state == wf.VERIFY_PENDING

    def test_infra_failure_is_never_a_verdict_about_the_post(self):
        """**核心分野。** 把"没问出来"折进"没上线"，一次容器宕机就会把一整批
        好好的作品挂成事故待办。"""
        for status in ("failed", "timeout", "proxy_failed", "session_invalid"):
            state, _ = wf.verdict_for(
                {"status": status, "detail": {}}, attempts_before=0, max_attempts=5
            )
            assert state == wf.VERIFY_PENDING, status
            assert state != wf.VERIFY_NOT_LIVE, status

    def test_the_last_attempt_abandons_rather_than_writing_pending_again(self):
        """用 ``attempts_before + 1``：否则最后一次会写 pending，下一轮才发现
        超额，中间那段时间待办显示的是"还在查"，与事实不符。"""
        state, detail = wf.verdict_for(
            {"status": "timeout", "message": "gave out", "detail": {}},
            attempts_before=4,
            max_attempts=5,
        )
        assert state == wf.VERIFY_ABANDONED
        assert "verification_abandoned" in detail

    def test_abandoned_blocks_the_work_item(self):
        """ "我们没能确认它上线了"不允许渲染成 done。"""
        assert wf.VERIFY_ABANDONED in wf.BLOCKING_VERIFY_STATES
        assert wf.VERIFY_NOT_LIVE in wf.BLOCKING_VERIFY_STATES
        assert wf.VERIFY_VERIFIED not in wf.BLOCKING_VERIFY_STATES


# ── _verify_one 的接线 ──────────────────────────────────────────────


class _FakeTasksRepo:
    def __init__(self) -> None:
        self.recorded: list[dict] = []

    async def record_verification(self, row_id, **kwargs):
        self.recorded.append({"row_id": row_id, **kwargs})


class _FakeAccountsRepo:
    def __init__(self, account: dict | None = None) -> None:
        self._account = account
        self.session_writes: list[tuple[int, str]] = []

    async def get_with_session(self, account_id):
        return dict(self._account) if self._account else None

    async def update_session_state(self, account_id, state):
        self.session_writes.append((account_id, state))


class _FakeVerifyResult:
    def __init__(self, result: dict, **extra: Any) -> None:
        self._result = result
        self.platform_item_id = extra.get("platform_item_id")
        self.published_url = extra.get("published_url")
        self.updated_storage_state = extra.get("updated_storage_state")

    @property
    def result(self):
        payload = self._result

        class _R:
            @staticmethod
            def to_dict():
                return payload

        return _R()

    @property
    def reason(self):
        return (self._result.get("detail") or {}).get("reason")


class _FakeAdapter:
    def __init__(self, outcome: _FakeVerifyResult) -> None:
        self._outcome = outcome
        self.calls: list[str] = []

    async def verify_publish(self, account, title):
        self.calls.append(title)
        return self._outcome


class _Lock:
    def __init__(self, acquired: bool = True) -> None:
        self._acquired = acquired

    def __call__(self, account_id, attempts=1):
        return self

    async def __aenter__(self):
        return self._acquired

    async def __aexit__(self, *exc):
        return False


#: Sentinel so `account=None` can mean "the account row is gone" rather than
#: "use the default" — the exact ambiguity that made the deleted-account test
#: pass against a healthy account on the first run.
_DEFAULT_ACCOUNT = object()


@pytest.fixture
def wiring(monkeypatch):
    def _install(*, outcome=None, account=_DEFAULT_ACCOUNT, acquired=True):
        tasks = _FakeTasksRepo()
        accounts = _FakeAccountsRepo(
            {"id": 2001, "auth_type": "session"}
            if account is _DEFAULT_ACCOUNT
            else account
        )
        adapter = _FakeAdapter(outcome)
        monkeypatch.setattr(
            "app.repositories.publish_tasks_repository.PublishTasksRepository",
            lambda: tasks,
        )
        monkeypatch.setattr(
            "app.repositories.social_accounts_repository.SocialAccountsRepository",
            lambda: accounts,
        )
        monkeypatch.setattr(
            "app.services.distribution.registry.get_session_adapter",
            lambda platform: adapter,
        )
        monkeypatch.setattr(
            "app.services.distribution.session_lock.account_session_lock",
            _Lock(acquired),
        )
        return tasks, accounts, adapter

    return _install


@pytest.mark.asyncio
async def test_verified_readback_backfills_the_published_url(wiring):
    """P1-3 的正题：回读成功后 ``published_url`` 终于有值 —— 那一列在此之前
    每一条记录上都是空的。"""
    tasks, _, adapter = wiring(
        outcome=_FakeVerifyResult(
            {"status": "published", "message": "live", "detail": {"reason": "live"}},
            published_url="https://www.douyin.com/video/7412345678901234567",
            platform_item_id="7412345678901234567",
        )
    )

    state = await wf._verify_one("1001", "2001", "douyin", "My Title", 0)

    assert state == wf.VERIFY_VERIFIED
    assert adapter.calls == ["My Title"]
    written = tasks.recorded[0]
    assert written["state"] == wf.VERIFY_VERIFIED
    assert written["published_url"].endswith("7412345678901234567")
    assert written["platform_item_id"] == "7412345678901234567"


@pytest.mark.asyncio
async def test_not_live_readback_is_recorded_with_a_typed_reason(wiring):
    """到点了但没上线 —— 待办要能变 blocked，且带着"为什么"。"""
    tasks, _, _ = wiring(
        outcome=_FakeVerifyResult(
            {
                "status": "not_published",
                "message": "the platform refused this post",
                "detail": {"reason": "rejected"},
            }
        )
    )

    state = await wf._verify_one("1001", "2001", "douyin", "My Title", 0)

    assert state == wf.VERIFY_NOT_LIVE
    assert state in wf.BLOCKING_VERIFY_STATES
    assert tasks.recorded[0]["detail"].startswith("[rejected]")


@pytest.mark.asyncio
async def test_infra_failure_leaves_the_verdict_open_and_stays_visible(wiring):
    """基建失败：不下结论（pending，下轮再来），但**记了一次尝试** ——
    计数是"这行到底试过几次"的唯一凭据，也是放弃语义的前提。"""
    tasks, _, _ = wiring(
        outcome=_FakeVerifyResult(
            {
                "status": "failed",
                "message": "browser service unreachable",
                "detail": {"error_kind": "unreachable"},
            }
        )
    )

    state = await wf._verify_one("1001", "2001", "douyin", "My Title", 0)

    assert state == wf.VERIFY_PENDING
    assert state not in wf.BLOCKING_VERIFY_STATES
    written = tasks.recorded[0]
    assert written["state"] == wf.VERIFY_PENDING
    # 没有结论就不该写 URL。
    assert not written.get("published_url")
    assert "[unreachable]" in written["detail"]


@pytest.mark.asyncio
async def test_the_refreshed_session_is_written_back(wiring):
    """回读也是一次真实的已认证页面加载，平台照样下发新 cookie。不写回等于
    让这次额外的浏览器任务**净消耗**会话寿命。"""
    _, accounts, _ = wiring(
        outcome=_FakeVerifyResult(
            {"status": "published", "detail": {}},
            published_url="https://www.douyin.com/video/7412345678901234567",
            updated_storage_state={"cookies": [{"name": "sessionid", "value": "new"}]},
        )
    )

    await wf._verify_one("1001", "2001", "douyin", "My Title", 0)

    assert len(accounts.session_writes) == 1
    assert "sessionid" in accounts.session_writes[0][1]


@pytest.mark.asyncio
async def test_a_busy_account_is_skipped_not_burned(wiring):
    """同一账号两个活动 context 会互相踢下线（spec §7.5）。抢不到锁就等下一
    轮 —— 而且**不该记一次尝试**，否则一个忙碌的账号会被重试预算饿死。"""
    tasks, _, adapter = wiring(
        outcome=_FakeVerifyResult({"status": "published", "detail": {}}),
        acquired=False,
    )

    state = await wf._verify_one("1001", "2001", "douyin", "My Title", 0)

    assert state == wf.VERDICT_BUSY
    assert tasks.recorded == []
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_a_row_with_no_title_is_closed_out_not_looped_forever(wiring):
    """标题是回读唯一的抓手。没有它就永远查不了这一行 —— 说清楚并终结，
    别让它占着每轮的名额无限循环。"""
    tasks, _, adapter = wiring(
        outcome=_FakeVerifyResult({"status": "published", "detail": {}})
    )

    state = await wf._verify_one("1001", "2001", "douyin", "   ", 0)

    assert state == wf.VERIFY_ABANDONED
    assert adapter.calls == []
    assert "[no_title]" in tasks.recorded[0]["detail"]


@pytest.mark.asyncio
async def test_a_decrypt_failure_never_condemns_the_post(wiring):
    """密文在、打不开：我们的密钥错了，平台会话八成好得很。这说的是账号，
    不是作品。"""
    tasks, _, adapter = wiring(
        outcome=_FakeVerifyResult({"status": "published", "detail": {}}),
        account={
            "id": 2001,
            "auth_type": "session",
            "session_state_decrypt_failed": True,
        },
    )

    state = await wf._verify_one("1001", "2001", "douyin", "My Title", 0)

    assert state == wf.VERIFY_PENDING
    assert adapter.calls == []
    assert "[decrypt_failed]" in tasks.recorded[0]["detail"]


@pytest.mark.asyncio
async def test_a_deleted_account_ends_the_row_rather_than_stranding_it(wiring):
    tasks, _, _ = wiring(
        outcome=_FakeVerifyResult({"status": "published", "detail": {}}), account=None
    )

    state = await wf._verify_one("1001", "2001", "douyin", "My Title", 0)

    assert state == wf.VERDICT_MISSING
    assert tasks.recorded[0]["state"] == wf.VERIFY_ABANDONED


@pytest.mark.asyncio
async def test_the_plaintext_session_never_outlives_the_call(wiring):
    """spec §7.6：明文 storage_state 只在这一帧的栈上活着。"""
    captured: dict[str, Any] = {}

    class _Capturing(_FakeAdapter):
        async def verify_publish(self, account, title):
            captured["account"] = account
            return await super().verify_publish(account, title)

    tasks, accounts, _ = wiring(
        outcome=_FakeVerifyResult({"status": "published", "detail": {}}),
        account={
            "id": 2001,
            "auth_type": "session",
            "session_state": '{"cookies": [{"name": "sessionid"}]}',
        },
    )
    # 换成会捕获入参的 adapter
    import app.services.distribution.registry as registry

    adapter = _Capturing(_FakeVerifyResult({"status": "published", "detail": {}}))
    registry.get_session_adapter = lambda platform: adapter  # type: ignore[assignment]

    await wf._verify_one("1001", "2001", "douyin", "My Title", 0)

    assert "session_state" not in captured["account"]
