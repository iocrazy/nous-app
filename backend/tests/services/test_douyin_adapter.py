import json
from urllib.parse import parse_qs, quote, urlparse

import pytest

from app.services.distribution.douyin_adapter import DouyinAdapter, DouyinCredentials
from app.services.distribution.registry import get_adapter

CREDS = DouyinCredentials(
    client_key="ck_test",
    client_secret="cs_test",
    redirect_uri="https://mediahubserver.heygo.cn:88/api/v1/distribution/accounts/oauth/douyin/callback",
)


def test_auth_url_contains_key_state_redirect():
    url = DouyinAdapter(CREDS).get_auth_url(state="st_abc")
    assert url.startswith("https://open.douyin.com/platform/oauth/connect/")
    assert "client_key=ck_test" in url and "state=st_abc" in url
    assert "redirect_uri=" in url and "response_type=code" in url


def test_h5_signature_is_md5_of_sorted_params():
    # 已验证原型的签名口径：md5("nonce_str=..&ticket=..&timestamp=..")
    sig = DouyinAdapter._generate_signature(
        ticket="tkt", timestamp=1700000000, nonce_str="nnn"
    )
    import hashlib

    assert (
        sig == hashlib.md5(b"nonce_str=nnn&ticket=tkt&timestamp=1700000000").hexdigest()
    )


def test_registry_resolves_and_rejects():
    assert isinstance(get_adapter("douyin", CREDS), DouyinAdapter)
    with pytest.raises(ValueError):
        get_adapter("myspace", CREDS)


@pytest.mark.asyncio
async def test_share_url_encodes_hashtags_as_json_array(monkeypatch):
    """H5 schema's hashtag_list must be a URL-encoded JsonArray string
    (`["城市","4k"]`) — Douyin's documented format, NOT comma-separated."""
    adapter = DouyinAdapter(CREDS)

    async def _fake_ticket():
        return "tkt"

    monkeypatch.setattr(adapter, "_get_ticket", _fake_ticket)

    url = await adapter.generate_share_url(
        video_url="https://cdn/x.mp4",
        title="Hi",
        share_id="sid1",
        hashtags=["城市", "4k"],
    )
    # The value in the query string is percent-encoded; decode + parse it back.
    qs = parse_qs(urlparse(url).query)
    assert "hashtag_list" in qs
    assert json.loads(qs["hashtag_list"][0]) == ["城市", "4k"]
    # And it is a JSON array, not a comma-joined string.
    assert quote('["城市","4k"]', safe="") in url


@pytest.mark.asyncio
async def test_share_url_omits_hashtag_list_when_empty(monkeypatch):
    adapter = DouyinAdapter(CREDS)

    async def _fake_ticket():
        return "tkt"

    monkeypatch.setattr(adapter, "_get_ticket", _fake_ticket)

    url = await adapter.generate_share_url(
        video_url="https://cdn/x.mp4", title="Hi", share_id="sid2", hashtags=[]
    )
    assert "hashtag_list" not in parse_qs(urlparse(url).query)
