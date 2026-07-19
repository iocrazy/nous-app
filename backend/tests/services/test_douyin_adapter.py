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


# ── official create API: private_status / download_type ──────────────────


class _CapturingResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _CapturingClient:
    """Minimal async httpx.AsyncClient stand-in that records the last POST and
    returns a canned Douyin success envelope (item_id)."""

    last: dict = {}

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **kw):
        _CapturingClient.last = {"url": url, **kw}
        return _CapturingResponse({"data": {"error_code": 0, "item_id": "item-xyz"}})


@pytest.mark.asyncio
async def test_create_video_post_includes_private_status_and_download_type(
    monkeypatch,
):
    import app.services.distribution.douyin_adapter as mod

    monkeypatch.setattr(mod.httpx, "AsyncClient", _CapturingClient)
    item = await DouyinAdapter(CREDS)._create_video_post(
        access_token="at",
        open_id="oid",
        video_id="vid",
        title="Hello",
        private_status=1,
        download_type=1,
    )
    assert item == "item-xyz"
    body = _CapturingClient.last["json"]
    # official create API enums: private_status 1=self only, download_type
    # 1=not allowed (0/1 — NOT the H5 schema's 1/2).
    assert body["private_status"] == 1
    assert body["download_type"] == 1
    assert body["text"] == "Hello"


@pytest.mark.asyncio
async def test_create_video_post_defaults_are_public_downloadable(monkeypatch):
    import app.services.distribution.douyin_adapter as mod

    monkeypatch.setattr(mod.httpx, "AsyncClient", _CapturingClient)
    await DouyinAdapter(CREDS)._create_video_post(
        access_token="at", open_id="oid", video_id="vid", title="Hi"
    )
    body = _CapturingClient.last["json"]
    assert body["private_status"] == 0
    assert body["download_type"] == 0


# ── H5 share schema: share_type / video_path / private_status / download_type


def _query(url: str) -> dict:
    from urllib.parse import parse_qs

    return parse_qs(url.split("?", 1)[1])


@pytest.mark.asyncio
async def test_share_url_repairs_schema_params(monkeypatch):
    adapter = DouyinAdapter(CREDS)

    async def fake_ticket():
        return "tkt"

    monkeypatch.setattr(adapter, "_get_ticket", fake_ticket)
    url = await adapter.generate_share_url(
        video_url="https://cdn/x.mp4",
        title="Hi",
        share_id="sid",
        private_status=2,
        allow_download=False,
    )
    assert url.startswith("snssdk1128://openplatform/share?")
    q = _query(url)
    # share_type was missing before — must now be the fixed "h5" discriminator.
    assert q["share_type"] == ["h5"]
    # the media key is video_path, not the old (wrong) video_url.
    assert q["video_path"] == ["https://cdn/x.mp4"]
    assert "video_url" not in q
    assert q["state"] == ["sid"]
    assert q["private_status"] == ["2"]
    # H5 download_type is 1/2 (allowed/not) — False → 2, NOT the official 0/1.
    assert q["download_type"] == ["2"]


@pytest.mark.asyncio
async def test_share_url_download_type_true_maps_to_one(monkeypatch):
    adapter = DouyinAdapter(CREDS)

    async def fake_ticket():
        return "tkt"

    monkeypatch.setattr(adapter, "_get_ticket", fake_ticket)
    # defaults: private_status 0, allow_download True.
    url = await adapter.generate_share_url(video_url="u", title="t", share_id="s")
    q = _query(url)
    assert q["private_status"] == ["0"]
    # allowed → 1 under the H5 schema (distinct from official 0).
    assert q["download_type"] == ["1"]


# ── H5 image / gallery (图文/note) share schema ──────────────────────────


@pytest.mark.asyncio
async def test_image_share_url_emits_image_list_note_and_download(monkeypatch):
    """Image share must emit the note-post schema: image_list_path (a JsonArray
    string preserving order), feature=note, share_type=h5 — and NO video_path.
    private_status/download_type follow the H5 enums (2=friends, 2=no-download)."""
    adapter = DouyinAdapter(CREDS)

    async def fake_ticket():
        return "tkt"

    monkeypatch.setattr(adapter, "_get_ticket", fake_ticket)
    url = await adapter.generate_image_share_url(
        image_urls=["https://cdn/1.jpg", "https://cdn/2.jpg"],
        title="Gallery",
        share_id="sid-img",
        hashtags=["城市"],
        private_status=2,
        allow_download=False,
    )
    assert url.startswith("snssdk1128://openplatform/share?")
    q = _query(url)
    assert q["share_type"] == ["h5"]
    assert q["feature"] == ["note"]
    assert q["state"] == ["sid-img"]
    # image_list_path is a JsonArray string preserving the picked order.
    assert json.loads(q["image_list_path"][0]) == [
        "https://cdn/1.jpg",
        "https://cdn/2.jpg",
    ]
    # the media key is image_list_path — never the video schema's video_path.
    assert "video_path" not in q
    assert q["private_status"] == ["2"]
    # H5 download_type 1/2 — not-allowed → 2.
    assert q["download_type"] == ["2"]
    assert json.loads(q["hashtag_list"][0]) == ["城市"]


@pytest.mark.asyncio
async def test_image_share_url_single_image_still_uses_list(monkeypatch):
    adapter = DouyinAdapter(CREDS)

    async def fake_ticket():
        return "tkt"

    monkeypatch.setattr(adapter, "_get_ticket", fake_ticket)
    url = await adapter.generate_image_share_url(
        image_urls=["https://cdn/only.jpg"], title="One", share_id="s1"
    )
    q = _query(url)
    # single image rides image_list_path as a one-element array (image_path unused).
    assert json.loads(q["image_list_path"][0]) == ["https://cdn/only.jpg"]
    assert "image_path" not in q
    # defaults: public + downloadable.
    assert q["private_status"] == ["0"]
    assert q["download_type"] == ["1"]
    # no topics → no hashtag_list param.
    assert "hashtag_list" not in q


@pytest.mark.asyncio
async def test_image_share_url_rejects_empty_list(monkeypatch):
    adapter = DouyinAdapter(CREDS)

    async def fake_ticket():
        return "tkt"

    monkeypatch.setattr(adapter, "_get_ticket", fake_ticket)
    with pytest.raises(ValueError):
        await adapter.generate_image_share_url(image_urls=[], title="x", share_id="s")
