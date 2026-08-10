"""每个平台的登录 judge,每条分支都要真的被执行一次。

起因:`bilibili.py` / `xiaohongshu.py` 里写了 `SessionStatus.WAITING_CONFIRM`
和 `WAITING_SMS` —— **两个都不存在**(真实的是 `SCANNED` / `SMS_REQUIRED`)。
用户扫码时才炸:

    AttributeError: type object 'SessionStatus' has no attribute 'WAITING_CONFIRM'

而当时 488 个测试全绿。因为 Python 的属性访问是运行时解析的,写错的枚举名
只有**那一行真的被执行**才会报错 —— 而那两个 judge 一次都没被调用过。
"import 成功" 不等于 "函数体正确"。

所以这里对**每个注册了登录流程的平台**跑一遍它的 judge,把每条分支都走到。
新平台接进来会自动被覆盖:用例从注册表取平台,不是手写清单 —— 否则下一个
平台又会带着同样的错静默上线。
"""

from __future__ import annotations

import pytest

from app.login import LoginPageSnapshot, identity_from_cookies
from app.platforms import get_login_flow, login_platforms
from app.schemas import SessionStatus

pytestmark = pytest.mark.unit


def _snapshot(**kw) -> LoginPageSnapshot:
    base = dict(
        url="https://example.test/login",
        login_texts=(),
        scanned_texts=(),
        expired_texts=(),
        sms_input_visible=False,
        qrcode_visible=False,
    )
    base.update(kw)
    return LoginPageSnapshot(**base)


@pytest.mark.parametrize("platform", sorted(login_platforms()))
def test_every_branch_of_every_judge_actually_runs(platform: str):
    """把每条分支都执行一遍,并确认返回的是**真实存在**的枚举成员。

    断言 `is` 同一个枚举对象,而不是只看返回值不为 None:一个写错的名字在
    这里会直接 AttributeError,而这正是要抓的东西。
    """
    spec = get_login_flow(platform)
    assert spec is not None

    snapshots = [
        _snapshot(expired_texts=("二维码已失效",)),
        _snapshot(scanned_texts=("扫码成功",)),
        _snapshot(sms_input_visible=True),
        _snapshot(qrcode_visible=True),
        _snapshot(login_texts=("登录",)),
        _snapshot(url="https://unexpected.example.com/"),
        _snapshot(),
    ]

    for snap in snapshots:
        judgement = spec.judge(snap)
        assert isinstance(judgement.status, SessionStatus), (
            f"{platform} 的 judge 返回了非法状态: {judgement.status!r}"
        )
        assert judgement.reason, f"{platform} 的 judge 返回了空 reason"


@pytest.mark.parametrize("platform", sorted(login_platforms()))
def test_profile_parser_survives_empty_input(platform: str):
    """选择器全落空时,parse_profile 必须降级而不是抛异常。

    这条同样是"只有真被调用才会暴露"的一类:平台改版让每个选择器都匹配不到
    是常态,那时 parse_profile 收到的是空 dict —— 它必须还能返回一个
    LoginProfile,否则用户已经扫完的码就白扫了。
    """
    spec = get_login_flow(platform)
    assert spec is not None

    profile = spec.parse_profile({})
    assert profile is not None
    # 字段可以为空,但类型必须对 —— 下游会直接写进 social_accounts
    assert isinstance(profile.username, str)
    assert isinstance(profile.platform_handle, str)


@pytest.mark.parametrize("platform", sorted(login_platforms()))
def test_identity_comes_from_the_declared_cookie(platform: str):
    """身份键只认 spec 声明的那一个 cookie。

    ⚠️ 这条原本叫 "cookie 兜底"—— cookie 是**兜底**正是 2026-08-09 那个
    重复绑定的病根:DOM 抖音号先赢,cookie 只在选择器落空时接手,两个命名空间
    轮流当唯一键,同一个账号于是有了两行。现在 cookie 不是兜底而是唯一来源,
    这条测试也跟着从"兜底能生效"改成"只有它能生效"。
    """
    spec = get_login_flow(platform)
    cookies = [{"name": spec.identity_cookie, "value": "the-real-id"}]

    assert identity_from_cookies(spec.identity_cookie, cookies) == "the-real-id"
    # 别的 cookie 一律不算数,哪怕名字看起来像同一个东西。
    assert identity_from_cookies(spec.identity_cookie, [{"name": "userId2", "value": "x"}]) == ""


# --- 登录"成功"必须用轮询认的那个状态 ---------------------------------------
#
# 同一天栽了两次:先是 WAITING_CONFIRM(枚举根本不存在,扫码时 AttributeError),
# 再是 SESSION_VALID(枚举存在、import 正常、judge 也跑得通 —— 但
# login_sessions.py 的轮询**只认 SUCCESS**,于是页面明明登录好了,弹窗永远停在
# "已扫码,请在手机上确认")。
#
# 第二个尤其阴险:所有测试都绿,judge 返回的也是合法枚举,只有把它跟**消费方**
# 对照才看得出错。所以这里断言的不是"返回值合法",而是"返回值正是轮询要的那个"。

_LOGGED_IN_URL = {
    "douyin": "https://creator.douyin.com/creator-micro/home",
    "xiaohongshu": "https://creator.xiaohongshu.com/new/home",
    "bilibili": "https://member.bilibili.com/platform/home",
}


@pytest.mark.parametrize("platform", sorted(login_platforms()))
def test_a_logged_in_page_reports_the_status_the_poller_waits_for(platform: str):
    """已登录页面必须判成 SUCCESS —— 轮询只认这一个值。"""
    from app.login_sessions import SessionStatus as PollerStatus

    url = _LOGGED_IN_URL.get(platform)
    assert url, (
        f"{platform} 没有登录后 URL 样本 —— 新平台请在 _LOGGED_IN_URL 里补一条,"
        "否则它的成功判定不会被任何用例覆盖"
    )

    spec = get_login_flow(platform)
    judgement = spec.judge(_snapshot(url=url))

    assert judgement.status is PollerStatus.SUCCESS, (
        f"{platform} 在已登录页面上返回 {judgement.status.name},"
        "而 login_sessions 的轮询只认 SUCCESS —— 登录会永远完不成"
    )
