import pytest

from app.platforms import get_validator, register, supported_platforms
from app.platforms.douyin import validate_session

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
