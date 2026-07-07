import pytest

from app.services.distribution.credentials import (
    CredentialsNotConfigured,
    _parse_douyin_settings,
)


def test_parse_ok():
    creds = _parse_douyin_settings(
        {"client_key": "ck", "client_secret": "cs", "redirect_uri": "https://x/cb"}
    )
    assert creds.client_key == "ck" and creds.redirect_uri == "https://x/cb"


@pytest.mark.parametrize("bad", [None, {}, {"client_key": "ck"}])
def test_parse_missing_raises(bad):
    with pytest.raises(CredentialsNotConfigured):
        _parse_douyin_settings(bad)
