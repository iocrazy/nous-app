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
