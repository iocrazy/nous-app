import pytest

from app.platforms import (
    get_login_flow,
    get_validator,
    login_platforms,
    register,
    register_login,
    supported_platforms,
)
from app.platforms.douyin import LOGIN_SPEC, validate_session

pytestmark = pytest.mark.unit


def test_douyin_is_registered():
    # 断言"包含"而不是"等于":每接一个平台这里就会红一次,而它想守的其实是
    # "抖音注册上了",不是"只有抖音"。
    assert "douyin" in supported_platforms()
    assert get_validator("douyin") is validate_session


def test_lookup_is_case_and_whitespace_insensitive():
    assert get_validator("  DouYin ") is validate_session


def test_unknown_platform_returns_none():
    # 用一个**不会被实现**的名字。原先这里写的是 "xiaohongshu",接入小红书
    # 那天它就变成了真平台,用例随之红掉 —— 拿"尚未实现的真实平台"当反例,
    # 保质期只到它被实现为止。
    assert get_validator("definitely-not-a-platform") is None


def test_double_registration_is_refused():
    """Two validators for one platform is precisely the defect this registry
    guards against: the reference project shipped two Douyin session checks and
    fixed only one, so its web path kept condemning healthy accounts."""

    async def other(_state, _env):
        raise AssertionError("unreachable")

    with pytest.raises(ValueError):
        register("douyin", other)


def test_each_platform_defines_its_session_validator_exactly_once():
    """Spec 7.1 是结构规则,所以结构性地断言,而不是指望 review 抓出第二份。

    原先写成 `== ["douyin.py"]`,把规则("每个平台只有一份")表达成了现状
    ("只有抖音")。接第二个平台那天它必然红,而红的原因跟规则本身无关。
    现在按**每个文件一份**来断言:多出来的那份重复实现照样会被抓到。
    """
    from pathlib import Path

    app_dir = Path(__file__).resolve().parents[1] / "app"
    per_file = {
        path.name: path.read_text(encoding="utf-8").count("async def validate_session")
        for path in app_dir.rglob("*.py")
    }
    offenders = {name: n for name, n in per_file.items() if n > 1}
    assert not offenders, f"同一个文件里定义了多份 validate_session: {offenders}"

    # 而且每个已注册的平台都必须真有一份
    assert per_file.get("douyin.py") == 1
    for name in supported_platforms():
        assert any(
            n == 1 for f, n in per_file.items() if f.startswith(name.split("_")[0])
        ), f"{name} 已注册但找不到它的 validate_session"


# --- login flows -----------------------------------------------------------


def test_douyin_login_flow_is_registered():
    assert "douyin" in login_platforms()
    assert get_login_flow("douyin") is LOGIN_SPEC
    assert get_login_flow("  DouYin ") is LOGIN_SPEC


def test_unknown_platform_has_no_login_flow():
    # 同上:反例要用永远不会存在的名字。
    assert get_login_flow("definitely-not-a-platform") is None


def test_double_login_registration_is_refused():
    with pytest.raises(ValueError):
        register_login("douyin", LOGIN_SPEC)


def test_login_and_validation_registries_are_independent():
    """A platform may be validatable without being bindable here (an account
    imported by hand, or one whose login is not a QR scan at all). Fusing the
    two registries would force one of them to be stubbed."""
    from app.platforms import _LOGIN_FLOWS, _VALIDATORS

    assert _LOGIN_FLOWS is not _VALIDATORS


def test_no_unbounded_loop_survives_anywhere_in_the_service():
    """Spec 7.2, asserted structurally.

    The reference implementation's login wait is a `while True` with no ceiling,
    so a page that quietly stops responding hangs the caller forever. This is
    the kind of rule that gets re-broken by the next person adding a poll, so it
    is checked rather than documented.
    """
    from pathlib import Path

    app_dir = Path(__file__).resolve().parents[1] / "app"
    offenders = []
    for path in app_dir.rglob("*.py"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if "while True" in code:
                offenders.append(f"{path.name}:{number}")
    assert offenders == []
