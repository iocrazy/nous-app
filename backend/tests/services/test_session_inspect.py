"""只读勘探的 backend 侧（T0）：锁、明文纪律、以及每条失败路径都类型化。

这一层没有 DOM、没有浏览器 —— 它的全部职责是**在把明文会话交出去之前**做对
三件事：拿到账号级串行锁、不让明文外泄、失败时给出可分支的 reason。所以用例
也只围着这三件事转。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

import app.repositories.social_accounts_repository as sar
import app.services.distribution.session_lock as lock_mod
from app.services.distribution.browser_client import (
    InspectResult,
    SessionErrorKind,
    SessionOpResult,
    SessionStatus,
)
from app.services.distribution.session_inspect import (
    REASON_ACCOUNT_BUSY,
    REASON_ACCOUNT_MISSING,
    REASON_AUTH_TYPE_MISMATCH,
    inspect_account_page,
)

ACCOUNT_ID = 337271352171182
URL = "https://creator.douyin.com/creator-micro/content/upload"
PLAINTEXT_STATE = '{"cookies": [{"name": "sessionid", "value": "s3cr3t"}]}'


def _account(**overrides) -> dict:
    row = {
        "id": ACCOUNT_ID,
        "platform": "douyin",
        "auth_type": "session",
        "session_state": PLAINTEXT_STATE,
        "environment": None,
        sar.SESSION_STATE_DECRYPT_FAILED: False,
    }
    row.update(overrides)
    return row


class _FakeRepo:
    """只实现被调到的两个方法 —— 多一个就等于在测别的东西。"""

    def __init__(self, row: dict | None):
        self._row = row
        self.written: list[str] = []

    async def get_with_session(self, account_id: int):
        return dict(self._row) if self._row is not None else None

    async def update_session_state(self, account_id, session_state=None, **kw):
        self.written.append(session_state)


class _FakeBrowser:
    def __init__(self, result: InspectResult | None = None):
        self.calls: list[dict] = []
        self._result = result or InspectResult(
            result=SessionOpResult(
                success=True,
                status=SessionStatus.SESSION_VALID.value,
                message="page read",
                detail={"stage": "observe"},
            ),
            observation={"texts": {"定时发布": {"exact": 1}}, "input_total": 4},
        )

    async def inspect_page(self, platform, storage_state, url, **kw):
        self.calls.append(
            {"platform": platform, "storage_state": storage_state, "url": url, **kw}
        )
        return self._result


@pytest.fixture
def repo(monkeypatch):
    holder: dict = {}

    def _install(row: dict | None):
        fake = _FakeRepo(row)
        holder["repo"] = fake
        monkeypatch.setattr(sar, "SocialAccountsRepository", lambda: fake)
        return fake

    return _install


@pytest.fixture
def lock(monkeypatch):
    """替换账号级串行锁，记录它是否真的被取过。"""
    calls: list[dict] = []

    def _install(acquired: bool = True):
        @asynccontextmanager
        async def _lock(account_id, *, attempts=10, retry_seconds=3.0):
            calls.append({"account_id": account_id, "attempts": attempts})
            yield acquired

        monkeypatch.setattr(lock_mod, "account_session_lock", _lock)
        return calls

    return _install


# ── 锁 ───────────────────────────────────────────────────────


async def test_the_browser_is_only_reached_while_holding_the_account_lock(repo, lock):
    """同账号两个活动 context 会互相踢下线（spec §7.5）。勘探是第四个并发源，
    所以它必须走与发布 / 巡检 / 回读同一把锁。"""
    repo(_account())
    calls = lock(acquired=True)
    browser = _FakeBrowser()

    out = await inspect_account_page(ACCOUNT_ID, URL, client=browser)

    assert calls == [{"account_id": ACCOUNT_ID, "attempts": 1}]
    assert len(browser.calls) == 1
    assert out["success"] is True


async def test_a_busy_account_is_refused_and_no_browser_is_started(repo, lock):
    """``attempts=1``：抢不到锁说明那个账号此刻正在发布或巡检。勘探让开 ——
    它绝不该让一次真实发布排队等它。"""
    repo(_account())
    lock(acquired=False)
    browser = _FakeBrowser()

    out = await inspect_account_page(ACCOUNT_ID, URL, client=browser)

    assert browser.calls == []
    assert out["success"] is False
    assert out["detail"]["reason"] == REASON_ACCOUNT_BUSY
    # 不是基建故障：这是一条确定结论（那个账号有别的会话在跑）。
    assert "error_kind" not in out["detail"]


# ── 明文纪律（spec §7.6） ────────────────────────────────────


async def test_the_plaintext_session_never_appears_in_the_returned_envelope(repo, lock):
    repo(_account())
    lock(acquired=True)
    browser = _FakeBrowser(
        InspectResult(
            result=SessionOpResult(
                success=True,
                status=SessionStatus.SESSION_VALID.value,
                message="page read",
            ),
            observation={"body_text_excerpt": "hello"},
            updated_storage_state={"cookies": [{"name": "sessionid", "value": "new"}]},
        )
    )

    out = await inspect_account_page(ACCOUNT_ID, URL, client=browser)

    assert "storage_state" not in out
    assert "updated_storage_state" not in out
    assert "s3cr3t" not in repr(out)
    assert "sessionid" not in repr(out)
    # 会话被续期这件事仍然要说出来 —— 那是布尔量，不是凭证。
    assert out["session_refreshed"] is True


async def test_the_renewed_session_is_written_back(repo, lock):
    """一次已认证的页面加载会让平台下发新 cookie。不写回等于让勘探净消耗
    会话寿命（与发布 / 回读同一条论证）。"""
    fake = repo(_account())
    lock(acquired=True)
    browser = _FakeBrowser(
        InspectResult(
            result=SessionOpResult(
                success=True, status=SessionStatus.SESSION_VALID.value, message=""
            ),
            updated_storage_state={"cookies": [{"name": "sessionid", "value": "new"}]},
        )
    )

    await inspect_account_page(ACCOUNT_ID, URL, client=browser)

    assert len(fake.written) == 1
    assert "new" in fake.written[0]


async def test_no_write_back_when_the_platform_returned_nothing(repo, lock):
    """空 storage_state 写回去等于把账号的会话抹掉，比不写坏得多。"""
    fake = repo(_account())
    lock(acquired=True)

    await inspect_account_page(ACCOUNT_ID, URL, client=_FakeBrowser())

    assert fake.written == []


# ── 类型化失败 ───────────────────────────────────────────────


async def test_a_missing_account_is_typed_not_an_exception(repo, lock):
    repo(None)
    lock(acquired=True)
    out = await inspect_account_page(ACCOUNT_ID, URL, client=_FakeBrowser())
    assert out["detail"]["reason"] == REASON_ACCOUNT_MISSING
    assert out["observation"] == {}


async def test_an_oauth_account_is_refused_rather_than_improvised_on(repo, lock):
    """OAuth 账号压根没有 storage_state。静默 no-op 不可接受。"""
    repo(_account(auth_type="oauth"))
    lock(acquired=True)
    browser = _FakeBrowser()
    out = await inspect_account_page(ACCOUNT_ID, URL, client=browser)
    assert out["detail"]["reason"] == REASON_AUTH_TYPE_MISMATCH
    assert browser.calls == []


async def test_a_decrypt_failure_is_infra_not_a_dead_account(repo, lock):
    """密文在、打不开 = 我们的密钥错了，平台会话八成好得很。把它记成账号掉线
    会让用户白扫一次码。"""
    repo(_account(**{sar.SESSION_STATE_DECRYPT_FAILED: True}))
    lock(acquired=True)
    browser = _FakeBrowser()

    out = await inspect_account_page(ACCOUNT_ID, URL, client=browser)

    assert out["detail"]["error_kind"] == SessionErrorKind.DECRYPT_FAILED.value
    assert out["status"] != SessionStatus.SESSION_INVALID.value
    assert browser.calls == []


async def test_a_missing_session_state_is_a_business_reason(repo, lock):
    repo(_account(session_state=None))
    lock(acquired=True)
    out = await inspect_account_page(ACCOUNT_ID, URL, client=_FakeBrowser())
    assert out["status"] == SessionStatus.SESSION_INVALID.value
    assert out["detail"]["reason"] == "no_session_state"


async def test_probes_and_options_are_passed_through_untouched(repo, lock):
    """上下界由浏览器侧的模型兜住 —— backend 复述一遍就是第二处声明。"""
    repo(_account())
    lock(acquired=True)
    browser = _FakeBrowser()

    await inspect_account_page(
        ACCOUNT_ID,
        URL,
        text_probes=["定时发布", "允许"],
        selector_probes=['input[type="file"]'],
        options={"settle_ms": 6000, "budget_s": None},
        client=browser,
    )

    call = browser.calls[0]
    assert call["text_probes"] == ["定时发布", "允许"]
    assert call["selector_probes"] == ['input[type="file"]']
    assert call["options"]["settle_ms"] == 6000
