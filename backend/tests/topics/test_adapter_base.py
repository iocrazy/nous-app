from app.services.topics.adapters.base import HotspotCandidate, make_dedup_key


def test_dedup_key_prefers_url_over_title():
    k1 = make_dedup_key("123", url="https://a.com/x", title="Hello")
    k2 = make_dedup_key("123", url="https://a.com/x", title="Different")
    assert k1 == k2  # same url -> same key regardless of title


def test_dedup_key_falls_back_to_title_when_no_url():
    k = make_dedup_key("123", url=None, title="Hello World")
    assert "123" in k and k != make_dedup_key("123", url=None, title="Other")


def test_candidate_defaults():
    c = HotspotCandidate(title="t", url="u", source_label="src")
    assert c.media_url is None
    assert c.content == ""
