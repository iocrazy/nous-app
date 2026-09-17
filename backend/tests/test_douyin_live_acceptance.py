"""Real-request acceptance for the douyin HTTP ABogus tier.

OPT-IN. Every test here is skipped unless you point it at a live cookie::

    DOUYIN_ACCEPTANCE_COOKIE_FILE=/path/to/douyin_cookie.txt \
        uv run pytest tests/test_douyin_live_acceptance.py -v -m live_douyin

Without that variable a normal `pytest` run — and CI — never contacts douyin.
These hit a third-party production service with real session credentials and
are rate-limited by it, so a failure can mean "douyin is throttling us today"
rather than "the code broke". They are a release gate you run on purpose.

Why they exist: the unit suite pins ordering and classification against fakes,
which is exactly the kind of test that stays green while the signature chain
rots. Only a real request proves `a_bogus` + `webSignUrl` + the `uifid` header
still satisfy Argus.

The cookie is injected at the repository boundary: what is under test is the
signing and request path, not where credentials are stored.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.live_douyin

#: A video that exists and is publicly reachable. If douyin deletes it these
#: tests fail loudly rather than passing on an empty result.
TARGET_URL = "https://v.douyin.com/-eB_iaHFsoU/"
TARGET_AWEME_ID = "7684532239228740849"

_COOKIE_ENV = "DOUYIN_ACCEPTANCE_COOKIE_FILE"
_ABOGUS = "app.services.media.parsers.douyin_parse.abogus_parser.ABogusDouyinParser"


@pytest.fixture
def cookie() -> str:
    path = os.environ.get(_COOKIE_ENV, "").strip()
    if not path:
        pytest.skip(f"set {_COOKIE_ENV} to run the live douyin acceptance")
    cookie_file = Path(path)
    if not cookie_file.is_file():
        pytest.skip(f"{_COOKIE_ENV} does not point at a file")
    text = cookie_file.read_text(encoding="utf-8").strip()
    if "UIFID=" not in text:
        # Without UIFID the tier cannot sign at all; that is a stale cookie,
        # not a code defect.
        pytest.skip("acceptance cookie has no UIFID — export a fresh one")
    return text


@pytest.fixture
def cookie_source(cookie: str):
    with (
        patch(
            f"{_ABOGUS}._cookie_from_user_config",
            new=AsyncMock(return_value=(cookie, {})),
        ),
        patch(f"{_ABOGUS}._cookie_from_redis", new=AsyncMock(return_value="")),
    ):
        yield


def _media_url_count(detail: dict) -> int:
    video = detail.get("video") or {}
    play = (video.get("play_addr") or {}).get("url_list") or []
    download = (video.get("download_addr") or {}).get("url_list") or []
    images = detail.get("images") or []
    return len(play) + len(download) + len(images)


@pytest.mark.asyncio
async def test_http_abogus_returns_the_target_video(cookie_source):
    from app.services.media.parsers.douyin_parse.abogus_parser import (
        ABogusDouyinParser,
    )

    detail = await ABogusDouyinParser.parse(TARGET_URL, user_id="acceptance")

    assert isinstance(detail, dict) and detail, "no aweme_detail returned"
    assert str(detail.get("aweme_id")) == TARGET_AWEME_ID
    assert _media_url_count(detail) > 0, "no play/download/image URLs"


@pytest.mark.asyncio
async def test_http_request_satisfies_both_argus_gates(cookie_source):
    """Wire level: HTTP 200, `status_code=0`, the `uifid` HEADER, and all
    signature fields in the URL. The header is the half that is easy to lose —
    UIFID sent only as a cookie still returns `Uifid Not Found`."""
    import httpx

    from app.services.media.parsers.douyin_parse.abogus_parser import (
        ABogusDouyinParser,
    )

    seen: dict = {}
    real_get = httpx.AsyncClient.get

    async def spy_get(self, url, **kwargs):
        resp = await real_get(self, url, **kwargs)
        if "aweme/detail" in str(url):
            seen["status"] = resp.status_code
            seen["headers"] = {k.lower() for k in (kwargs.get("headers") or {})}
            seen["query"] = str(url)
            try:
                seen["status_code"] = resp.json().get("status_code")
            except ValueError:
                seen["status_code"] = "<non-json>"
        return resp

    with patch.object(httpx.AsyncClient, "get", spy_get):
        await ABogusDouyinParser.parse(TARGET_URL, user_id="acceptance")

    assert seen.get("status") == 200, f"HTTP {seen.get('status')}"
    assert seen.get("status_code") == 0, f"douyin status_code={seen.get('status_code')}"
    assert "uifid" in seen.get("headers", set()), "request lost the uifid header"
    for field in ("a_bogus=", "timestamp=", "x-secsdk-web-signature="):
        assert field in seen["query"], f"signed URL missing {field}"


@pytest.mark.asyncio
async def test_chain_succeeds_via_http_without_touching_the_browser(cookie_source):
    from app.services.media.parsers.douyin_parse import parse_chain
    from app.services.media.parsers.douyin_parse.drissionpage_parser import (
        DrissionPageParser,
    )

    browser = AsyncMock(return_value=None)
    with (
        patch.object(
            parse_chain,
            "get_douyin_method_flags",
            AsyncMock(return_value={"abogus": True, "drissionpage": True}),
        ),
        patch.object(DrissionPageParser, "fetch_one_video", browser),
    ):
        result = await parse_chain.fetch_douyin_detail(TARGET_URL, user_id="acceptance")

    assert result is not None, "chain returned nothing"
    _detail, parsed, method = result
    assert method == "abogus", f"expected abogus, got {method}"
    browser.assert_not_called()
    assert str(parsed.get("platform_id")) == TARGET_AWEME_ID
