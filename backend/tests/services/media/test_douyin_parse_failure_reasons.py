"""抖音解析失败带类型化理由（captcha / Argus 拦截 / 未知），不再 return None。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.media.parsers.douyin_parse.failures import (
    DouyinFailure,
    DouyinParseError,
    message_for,
)


@pytest.fixture
def _both_methods_on():
    with patch(
        "app.services.media.parsers.douyin_parse.parse_chain.get_douyin_method_flags",
        new=AsyncMock(return_value={"abogus": True, "drissionpage": True}),
    ):
        yield


async def _chain(url="https://v.douyin.com/ddpVj2Mx6Cw/", **kw):
    from app.services.media.parsers.douyin_parse.parse_chain import (
        fetch_douyin_detail,
    )

    return await fetch_douyin_detail(url, **kw)


def _patch_methods(abogus, drission):
    """两个 parser 在 ``fetch_douyin_detail`` 里是**函数内 import**，所以只能打在"""
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(
        patch(
            "app.services.media.parsers.douyin_parse.abogus_parser"
            ".ABogusDouyinParser.parse",
            new=abogus,
        )
    )
    stack.enter_context(
        patch(
            "app.services.media.parsers.douyin_parse.drissionpage_parser"
            ".DrissionPageParser.fetch_one_video",
            new=drission,
        )
    )
    return stack


# ---------------------------------------------------------------------------
# 用户那一次，原样重放
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_reported_failure_now_says_douyin_asked_for_verification(
    _both_methods_on,
):
    """abogus 被签名拒绝、drissionpage 撞验证码 —— 生产 13:05 的原样。"""
    abogus = AsyncMock(
        side_effect=DouyinParseError(
            DouyinFailure.SIGNATURE_REJECTED,
            "Blocked by ArgusSecurityPlugin Uifid Not Found",
        )
    )
    drission = AsyncMock(
        side_effect=DouyinParseError(
            DouyinFailure.CAPTCHA, 'captcha via xpath://iframe[@src*="verifycenter"]'
        )
    )

    with _patch_methods(abogus, drission):
        with pytest.raises(DouyinParseError) as exc:
            await _chain()

    # 链路的最后一句话说了算 —— 用户下一步该做什么，由最后那次尝试决定。
    assert exc.value.kind is DouyinFailure.CAPTCHA
    assert "verification" in str(exc.value).lower()
    # 而且要告诉他们怎么办，不只是出了什么事。
    assert "try again" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_a_rejected_request_says_wait_not_keep_clicking(_both_methods_on):
    """14 天日志实测：同一条签名路径在被拦的同一天里成功了 1-10 次（09-04 是"""
    boom = AsyncMock(
        side_effect=DouyinParseError(
            DouyinFailure.SIGNATURE_REJECTED, "Uifid Not Found"
        )
    )
    with _patch_methods(boom, boom):
        with pytest.raises(DouyinParseError) as exc:
            await _chain()

    msg = str(exc.value).lower()
    assert exc.value.kind is DouyinFailure.SIGNATURE_REJECTED
    assert "intermittent" in msg, "别把限流说成永久损坏"
    assert "few minutes" in msg, "要给出等多久，不是一句「稍后再试」"
    # 反向：不能鼓励立刻再点一次，那正是把窗口焐热的动作。
    assert "try again now" not in msg


@pytest.mark.asyncio
async def test_an_unexplained_failure_says_so_instead_of_guessing(_both_methods_on):
    """没有方法给出理由时，答案是「不知道」，不是随便挑一个。"""
    with _patch_methods(AsyncMock(return_value=None), AsyncMock(return_value=None)):
        with pytest.raises(DouyinParseError) as exc:
            await _chain()

    assert exc.value.kind is DouyinFailure.UNKNOWN


@pytest.mark.asyncio
async def test_a_failing_method_does_not_stop_a_later_one_from_succeeding(
    _both_methods_on,
):
    """带理由的失败仍然只是**这一个方法**失败了 —— 链路必须继续往下走。"""
    detail = {"aweme_id": "1"}
    abogus = AsyncMock(
        side_effect=DouyinParseError(DouyinFailure.SIGNATURE_REJECTED, "nope")
    )
    drission = AsyncMock(return_value=detail)

    with _patch_methods(abogus, drission):
        with patch(
            "app.services.media.parsers.douyin_parse.formatter.DouyinFormatter"
        ) as fmt:
            fmt.parse_aweme_detail = AsyncMock(return_value={"platform_id": "1"})
            result = await _chain()

    assert result is not None
    _detail, parsed, method = result
    assert method == "drissionpage"
    assert parsed["platform_id"] == "1"


# ---------------------------------------------------------------------------
# 契约的另一侧：re-parse 的裸 aweme_id 兜底不能被新异常吃掉
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reparse_still_falls_back_to_the_bare_aweme_id():
    """**改契约最容易压坏的地方。**"""
    from app.services.media.parsers.douyin_parse import parse_chain

    calls: list[str] = []

    async def _fake(src, **kw):
        calls.append(src)
        if src.startswith("http"):
            raise DouyinParseError(DouyinFailure.CAPTCHA, "captcha")
        return ({"aweme_id": src}, {"platform_id": src}, "abogus")

    with patch.object(parse_chain, "fetch_douyin_detail", new=_fake):
        parsed, method = await parse_chain.reparse_douyin(
            original_url="https://v.douyin.com/x/", platform_id="7685326513314845797"
        )

    assert calls == ["https://v.douyin.com/x/", "7685326513314845797"]
    assert parsed["platform_id"] == "7685326513314845797"
    assert method == "abogus"


@pytest.mark.asyncio
async def test_reparse_reports_the_last_reason_when_every_source_fails():
    """两个 source 都失败时，理由不该在路上丢掉。"""
    from app.services.media.parsers.douyin_parse import parse_chain

    async def _fake(src, **kw):
        raise DouyinParseError(DouyinFailure.CAPTCHA, "captcha")

    with patch.object(parse_chain, "fetch_douyin_detail", new=_fake):
        with pytest.raises(DouyinParseError) as exc:
            await parse_chain.reparse_douyin(
                original_url="https://v.douyin.com/x/", platform_id="123"
            )

    assert exc.value.kind is DouyinFailure.CAPTCHA


# ---------------------------------------------------------------------------
# 文案本身
# ---------------------------------------------------------------------------


def test_every_kind_has_a_message_that_says_what_to_do():
    """一个只说「失败了」的理由等于没有理由 —— 读的人没有下一步。"""
    for kind in DouyinFailure:
        msg = message_for(kind)
        assert msg and msg != kind.value, f"{kind} 没有文案"
        assert len(msg) > 40, f"{kind} 的文案太短，说不出该怎么办：{msg!r}"


def test_a_typed_failure_is_still_a_runtime_error():
    """现存的 ``except RuntimeError`` 站点必须原样继续工作 —— 这次改动是给失败"""
    err = DouyinParseError(DouyinFailure.CAPTCHA, "x")
    assert isinstance(err, RuntimeError)


# ---------------------------------------------------------------------------
# uifid：值我们有，只是没放对地方
# ---------------------------------------------------------------------------


def test_the_uifid_is_pulled_out_of_the_cookie_by_whole_name():
    """``NOTUIFID=`` 不能当成 ``UIFID=`` —— 子串匹配会送出别人的值。"""
    from app.services.media.parsers.douyin_parse.abogus_parser import _cookie_value

    assert _cookie_value("a=1; UIFID=xyz; b=2", "UIFID") == "xyz"
    assert _cookie_value("UIFID=lead; b=2", "UIFID") == "lead"
    assert _cookie_value("NOTUIFID=bad; UIFID=good", "UIFID") == "good"
    assert _cookie_value("a=1", "UIFID") == ""
    assert _cookie_value("", "UIFID") == ""


@pytest.mark.asyncio
async def test_the_detail_request_sends_uifid_as_a_header_too():
    """抖音的 Argus 从 ``uifid`` **请求头**读设备指纹，不是从同名 cookie 读 ——"""
    from unittest.mock import MagicMock

    from app.services.media.parsers.douyin_parse import abogus_parser as ab

    captured: dict = {}

    class _Resp:
        status_code = 200
        text = '{"aweme_detail": {"aweme_id": "1"}}'

        def json(self):
            import json as _j

            return _j.loads(self.text)

    class _Client:
        async def get(self, url, headers=None):
            captured.update(headers or {})
            return _Resp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    with patch.object(ab, "safe_async_client", lambda **kw: _Client()):
        out = await ab.ABogusDouyinParser._fetch_detail(
            "https://www.douyin.com/aweme/v1/web/aweme/detail/?a=1",
            "UA/1.0",
            "ttwid=t; UIFID=the-fingerprint; sessionid=s",
            {},
        )

    assert out == {"aweme_id": "1"}
    assert captured["uifid"] == "the-fingerprint"
    # 仍然照常带 cookie —— 这是补一个头，不是把 cookie 挪走。
    assert "UIFID=the-fingerprint" in captured["Cookie"]


@pytest.mark.asyncio
async def test_no_uifid_in_the_cookie_means_no_empty_header():
    """发一个空的 ``uifid:`` 头，比不发更糟 —— 那是在断言一个我们没有的值。"""
    from app.services.media.parsers.douyin_parse import abogus_parser as ab

    captured: dict = {}

    class _Resp:
        status_code = 200
        text = '{"aweme_detail": {"aweme_id": "1"}}'

        def json(self):
            import json as _j

            return _j.loads(self.text)

    class _Client:
        async def get(self, url, headers=None):
            captured.update(headers or {})
            return _Resp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    with patch.object(ab, "safe_async_client", lambda **kw: _Client()):
        await ab.ABogusDouyinParser._fetch_detail("https://x/", "UA/1.0", "ttwid=t", {})

    assert "uifid" not in captured


@pytest.mark.asyncio
async def test_an_argus_rejection_is_reported_as_a_signature_failure():
    """抖音的反爬用纯文本回绝，不是 JSON。之前这里只 ``return None``，理由就地蒸发。"""
    from app.services.media.parsers.douyin_parse import abogus_parser as ab

    class _Resp:
        status_code = 403
        text = "Blocked by ArgusSecurityPlugin Uifid Not Found"

        def json(self):
            raise __import__("json").JSONDecodeError("no", "d", 0)

    class _Client:
        async def get(self, url, headers=None):
            return _Resp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    with patch.object(ab, "safe_async_client", lambda **kw: _Client()):
        with pytest.raises(DouyinParseError) as exc:
            await ab.ABogusDouyinParser._fetch_detail("https://x/", "UA/1.0", "", {})

    assert exc.value.kind is DouyinFailure.SIGNATURE_REJECTED
