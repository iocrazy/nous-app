import asyncio

from app.services.topics.content_fetcher import (
    content_fetch_payload,
    default_content_fetch_config,
    fetch_article_text,
    merge_content_fetch_config,
)


def test_default_is_disabled():
    # opt-in: a config-read failure / missing key must NOT start fetching
    assert default_content_fetch_config().enabled is False


def test_merge_overrides_and_clamps():
    c = merge_content_fetch_config(
        {
            "enabled": True,
            "tier_max": 3,
            "max_items": 999,  # clamped to 200
            "concurrency": 99,  # clamped to 16
            "timeout_s": 1,  # clamped to 3
            "min_chars": 50,
        }
    )
    assert c.enabled is True
    assert c.tier_max == 3
    assert c.max_items == 200
    assert c.concurrency == 16
    assert c.timeout_s == 3
    assert c.min_chars == 50


def test_merge_garbage_falls_back_to_defaults():
    d = default_content_fetch_config()
    assert merge_content_fetch_config(None) == d
    assert merge_content_fetch_config("nope") == d
    bad = merge_content_fetch_config({"tier_max": "x", "enabled": "yes"})
    assert bad.tier_max == d.tier_max
    assert bad.enabled is d.enabled  # non-bool → default (disabled)


def test_payload_roundtrips_through_merge():
    payload = content_fetch_payload(default_content_fetch_config())
    assert set(payload.keys()) == {
        "enabled",
        "tier_max",
        "max_items",
        "concurrency",
        "timeout_s",
        "min_chars",
    }
    assert merge_content_fetch_config(payload) == default_content_fetch_config()


def test_fetch_article_text_rejects_non_http():
    # guards the loop from junk urls without touching the network
    assert asyncio.run(fetch_article_text("")) is None
    assert asyncio.run(fetch_article_text("not-a-url")) is None
    assert asyncio.run(fetch_article_text("ftp://x")) is None


def test_boilerplate_guard_drops_nav_shell():
    from app.services.topics.content_fetcher import _looks_like_boilerplate

    # the cls.cn nav shell trafilatura sometimes latches onto
    nav = "关于我们\n网站声明\n联系方式\n用户反馈\n网站地图\n首页\n电报\n话题"
    assert _looks_like_boilerplate(nav) is True
    # a real article (no/one marker) passes
    article = "财联社6月29日电，Omdia指出预计到2030年RGB LED电视将占全球电视市场13%。"
    assert _looks_like_boilerplate(article) is False
    # one stray marker alone isn't enough to drop a real article
    assert _looks_like_boilerplate("某公司更新了联系方式并发布新品。") is False
