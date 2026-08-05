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
    assert supported_platforms() == ["douyin"]
    assert get_validator("douyin") is validate_session


def test_lookup_is_case_and_whitespace_insensitive():
    assert get_validator("  DouYin ") is validate_session


def test_unknown_platform_returns_none():
    assert get_validator("xiaohongshu") is None


def test_double_registration_is_refused():
    """Two validators for one platform is precisely the defect this registry
    guards against: the reference project shipped two Douyin session checks and
    fixed only one, so its web path kept condemning healthy accounts."""

    async def other(_state, _env):
        raise AssertionError("unreachable")

    with pytest.raises(ValueError):
        register("douyin", other)


def test_only_one_douyin_session_validator_exists_in_the_tree():
    """Spec 7.1 is a structural rule, so assert it structurally rather than
    trusting review to catch a second copy."""
    from pathlib import Path

    app_dir = Path(__file__).resolve().parents[1] / "app"
    definitions = [
        path
        for path in app_dir.rglob("*.py")
        if "async def validate_session" in path.read_text(encoding="utf-8")
    ]
    assert [p.name for p in definitions] == ["douyin.py"]


# --- login flows -----------------------------------------------------------


def test_douyin_login_flow_is_registered():
    assert login_platforms() == ["douyin"]
    assert get_login_flow("douyin") is LOGIN_SPEC
    assert get_login_flow("  DouYin ") is LOGIN_SPEC


def test_unknown_platform_has_no_login_flow():
    assert get_login_flow("xiaohongshu") is None


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
