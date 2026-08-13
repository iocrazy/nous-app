"""打字式勘探的 backend 侧：锁、明文纪律、以及重放实验的设计本身。

这一层没有 DOM、没有浏览器。它的职责是三件事，用例也只围着这三件转：

1. 交出明文会话之前先拿到账号级串行锁；
2. **两样**明文都不外泄 —— session_state（老规矩）与 replay_targets 里那些
   未脱敏的完整 URL（新的那个）；
3. 重放实验本身是对的：只换关键词、其余（含签名参数）原样，且 cookie 按域挑。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

import app.repositories.social_accounts_repository as sar
import app.services.distribution.session_lock as lock_mod
from app.services.distribution.browser_client import (
    ProbeResult,
    SessionOpResult,
    SessionStatus,
)
from app.services.distribution.session_probe import (
    REASON_ACCOUNT_BUSY,
    cookies_for_host,
    probe_account_page,
    summarise_replay_body,
    swap_query_value,
)

ACCOUNT_ID = 337271352171182
URL = "https://creator.douyin.com/creator-micro/content/upload"
PLAINTEXT_STATE = (
    '{"cookies": [{"name": "sessionid", "value": "s3cr3t", "domain": ".douyin.com"}]}'
)
SELECTORS = ['[contenteditable="true"]']
SIGNED_URL = (
    "https://creator.douyin.com/web/api/sug/?keyword=%E5%8D%97%E9%80%9A"
    "&a_bogus=SIGNATURE-MATERIAL&msToken=TOKEN-MATERIAL"
)


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
    def __init__(self, row: dict | None):
        self._row = row
        self.written: list[str] = []

    async def get_with_session(self, account_id: int):
        return dict(self._row) if self._row is not None else None

    async def update_session_state(self, account_id, session_state=None, **kw):
        self.written.append(session_state)


class _FakeBrowser:
    def __init__(self, result: ProbeResult | None = None):
        self.calls: list[dict] = []
        self._result = result or ProbeResult(
            result=SessionOpResult(
                success=True,
                status=SessionStatus.SESSION_VALID.value,
                message="typed probe complete",
                detail={"stage": "observe"},
            ),
            observation={"typed_text_landed": True, "captures": []},
        )

    async def probe_page(self, platform, storage_state, url, **kw):
        self.calls.append(
            {"platform": platform, "storage_state": storage_state, "url": url, **kw}
        )
        return self._result


@pytest.fixture
def repo(monkeypatch):
    def _install(row: dict | None):
        fake = _FakeRepo(row)
        monkeypatch.setattr(sar, "SocialAccountsRepository", lambda: fake)
        return fake

    return _install


@pytest.fixture
def lock(monkeypatch):
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
    repo(_account())
    calls = lock(acquired=True)
    browser = _FakeBrowser()

    out = await probe_account_page(
        ACCOUNT_ID, URL, probe_text="#南通", target_selectors=SELECTORS, client=browser
    )

    assert calls == [{"account_id": ACCOUNT_ID, "attempts": 1}]
    assert out["success"] is True


async def test_a_busy_account_is_refused_and_no_browser_is_started(repo, lock):
    """勘探绝不该让一次真实发布排队等它。"""
    repo(_account())
    lock(acquired=False)
    browser = _FakeBrowser()

    out = await probe_account_page(
        ACCOUNT_ID, URL, probe_text="#南通", target_selectors=SELECTORS, client=browser
    )

    assert browser.calls == []
    assert out["detail"]["reason"] == REASON_ACCOUNT_BUSY
    assert "error_kind" not in out["detail"]


# ── 明文纪律（两样，不是一样） ────────────────────────────────


async def test_neither_the_session_nor_the_raw_replay_urls_leave_this_layer(repo, lock):
    """``replay_targets`` 是这条链里唯一未脱敏的东西，它必须止步于此。"""
    repo(_account())
    lock(acquired=True)
    browser = _FakeBrowser(
        ProbeResult(
            result=SessionOpResult(
                success=True, status=SessionStatus.SESSION_VALID.value, message=""
            ),
            observation={"captures": []},
            replay_targets=[
                {
                    "url": SIGNED_URL,
                    "keyword_param": "keyword",
                    "keyword_value": "南通",
                    "referer": URL,
                }
            ],
            replay_user_agent="Mozilla/5.0",
            updated_storage_state={"cookies": [{"name": "sessionid", "value": "new"}]},
        )
    )

    # replay_mutation_text 留空 ⇒ 不发起任何外网请求，这条用例只测"不外泄"。
    out = await probe_account_page(
        ACCOUNT_ID, URL, probe_text="#南通", target_selectors=SELECTORS, client=browser
    )

    rendered = repr(out)
    assert "replay_targets" not in out
    assert "SIGNATURE-MATERIAL" not in rendered
    assert "TOKEN-MATERIAL" not in rendered
    assert "s3cr3t" not in rendered
    assert "updated_storage_state" not in out
    assert out["session_refreshed"] is True
    assert out["replay"] == []


async def test_the_renewed_session_is_written_back(repo, lock):
    fake = repo(_account())
    lock(acquired=True)
    browser = _FakeBrowser(
        ProbeResult(
            result=SessionOpResult(
                success=True, status=SessionStatus.SESSION_VALID.value, message=""
            ),
            updated_storage_state={"cookies": [{"name": "sessionid", "value": "new"}]},
        )
    )

    await probe_account_page(
        ACCOUNT_ID, URL, probe_text="#南通", target_selectors=SELECTORS, client=browser
    )

    assert len(fake.written) == 1 and "new" in fake.written[0]


# ── 重放实验的设计 ────────────────────────────────────────────


def test_only_the_keyword_moves_and_the_signature_stays():
    """实验设计的全部：**只**动关键词，看签名还认不认。签名跟着变就什么也
    没证明。"""
    mutated = swap_query_value(SIGNED_URL, "keyword", "上海")
    assert "a_bogus=SIGNATURE-MATERIAL" in mutated
    assert "msToken=TOKEN-MATERIAL" in mutated
    assert "keyword=%E4%B8%8A%E6%B5%B7" in mutated
    assert "%E5%8D%97%E9%80%9A" not in mutated


def test_swapping_a_parameter_that_is_not_there_changes_nothing():
    assert swap_query_value(SIGNED_URL, "nope", "x") == SIGNED_URL


@pytest.mark.parametrize(
    "domain,host,expected",
    [
        (".douyin.com", "creator.douyin.com", True),
        ("creator.douyin.com", "creator.douyin.com", True),
        ("creator.douyin.com", "www.douyin.com", False),
        (".douyin.com", "douyin.com.evil.example", False),
        (".other.com", "creator.douyin.com", False),
    ],
)
def test_cookies_are_picked_by_their_own_domain_rule(domain, host, expected):
    """全带上等于把一个站点的会话 cookie 发给另一个站点。"""
    jar = cookies_for_host(
        [{"name": "sessionid", "value": "v", "domain": domain}], host
    )
    assert bool(jar) is expected


def test_a_cookie_without_a_domain_is_never_sent():
    assert cookies_for_host([{"name": "x", "value": "v"}], "creator.douyin.com") == []


def test_the_replay_summary_is_structural_and_carries_no_body():
    """内容形状浏览器侧那份脱敏捕获已经给过了；这里只回答"它答没答"。"""
    body = '{"status_code":0,"sug_list":[{"content":"上海热点"},{"content":"上海"}]}'
    out = summarise_replay_body(body, needles={"mutation": "上海", "original": "南通"})

    assert out["json"] is True
    assert out["json_status_field"] == "status_code=0"
    assert out["first_list_key"] == "sug_list"
    assert out["first_list_len"] == 2
    # 决定性的那一位：换了词之后，答案里出现的是新词。
    assert out["contains_mutation"] is True
    assert out["contains_original"] is False
    assert "body_excerpt" not in out
    assert "上海热点" not in repr(out)


def test_a_non_json_replay_still_produces_a_typed_answer():
    out = summarise_replay_body("<html>blocked</html>", needles={"mutation": "上海"})
    assert out["json"] is False
    assert out["contains_mutation"] is False
    assert out["body_chars"] == 20
