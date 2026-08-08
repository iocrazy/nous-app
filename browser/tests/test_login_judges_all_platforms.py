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

from app.login import LoginPageSnapshot
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

    profile = spec.parse_profile({}, [])
    assert profile is not None
    # 字段可以为空,但类型必须对 —— 下游会直接写进 social_accounts
    assert isinstance(profile.username, str)
    assert isinstance(profile.platform_user_id, str)


@pytest.mark.parametrize("platform", sorted(login_platforms()))
def test_cookie_fallback_identity_works(platform: str):
    """所有显示选择器都失效时,cookie 兜底要能给出身份。

    抖音当初就靠这个:三个 profile 选择器全错,账号至少还有个 id 可用。
    """
    spec = get_login_flow(platform)
    module = __import__(
        f"app.platforms.{platform}", fromlist=["USER_ID_COOKIES"]
    )
    cookies = [{"name": name, "value": "fallback-id"} for name in module.USER_ID_COOKIES]

    profile = spec.parse_profile({}, cookies)
    assert profile.platform_user_id == "fallback-id", (
        f"{platform}: 所有选择器失效时,cookie 兜底没生效"
    )
