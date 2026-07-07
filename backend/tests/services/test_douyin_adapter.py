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
