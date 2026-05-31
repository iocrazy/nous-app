"""Tests for qishui cookie platform registration + validate-on-save."""


def test_qishui_in_supported_platforms():
    from app.api.user_settings_router import SUPPORTED_PLATFORMS

    assert "qishui" in SUPPORTED_PLATFORMS
