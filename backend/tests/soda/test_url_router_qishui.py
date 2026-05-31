# backend/tests/soda/test_url_router_qishui.py
from app.services.media.parsers.url_router import URLRouter


def test_qishui_short_link_detected_as_soda():
    platform, handler = URLRouter.detect_platform(
        "https://qishui.douyin.com/s/iABCDEF/"
    )
    assert platform == "qishui"
    assert handler == "soda"


def test_music_douyin_detected_as_qishui_not_douyin():
    # music.douyin.com is a douyin.com subdomain — qishui MUST win
    platform, handler = URLRouter.detect_platform(
        "https://music.douyin.com/qishui/share/track?track_id=7123"
    )
    assert platform == "qishui"


def test_plain_douyin_still_detected_as_douyin():
    platform, handler = URLRouter.detect_platform("https://www.douyin.com/video/7123")
    assert platform == "douyin"
    assert handler == "ytdlp"
