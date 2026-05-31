from app.workflows.parse import is_soda_platform


def test_qishui_uses_soda_download():
    assert is_soda_platform("qishui") is True


def test_other_platforms_use_normal_download():
    assert is_soda_platform("douyin") is False
    assert is_soda_platform("youtube") is False
