from app.services.topics.adapters.base import HotspotCandidate, make_dedup_key


def test_dedup_key_is_source_plus_title_ignoring_url_drift():
    # same (source, title) → same key even when the url drifts between fetches
    # (the hot-list duplicate bug). Title is the stable identity.
    k1 = make_dedup_key("123", url="https://a.com/x?t=1", title="Hello")
    k2 = make_dedup_key("123", url="https://a.com/x?t=2", title="Hello")
    assert k1 == k2 and "123" in k1
    # different title under the same source → different key
    assert make_dedup_key("123", url=None, title="Other") != k1
    # same title, different source → different key (per-source rows; clustering
    # handles the cross-source merge)
    assert make_dedup_key("999", url="https://a.com/x", title="Hello") != k1


def test_dedup_key_falls_back_to_url_when_no_title():
    k = make_dedup_key("123", url="https://a.com/x", title="   ")
    assert k == make_dedup_key("123", url="https://a.com/x", title="")


def test_candidate_defaults():
    c = HotspotCandidate(title="t", url="u", source_label="src")
    assert c.media_url is None
    assert c.content == ""
