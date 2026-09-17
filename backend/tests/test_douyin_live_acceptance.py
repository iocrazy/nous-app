"""Real-request acceptance for the douyin parse chain (spec §6, 2026-09-16).

OPT-IN. Every test here is skipped unless you point it at a live cookie::

    DOUYIN_ACCEPTANCE_COOKIE_FILE=/path/to/douyin_cookie.txt \
        uv run pytest tests/test_douyin_live_acceptance.py -v -m live_douyin

Without that variable a normal `pytest` run — and CI — never contacts
douyin. That is deliberate: these hit a third-party production service with
real session credentials, they are rate-limited by that service, and a
failure here can mean "douyin is throttling us today" rather than "the code
broke". They are a release gate you run on purpose, not a unit test.

Why they exist at all: the unit suite pins ordering and classification
against fakes, which is exactly the kind of test that stays green while the
signature chain rots. Only a real request proves `a_bogus` + `webSignUrl` +
the `uifid` header still satisfy Argus, and only a real browser proves the
challenge detector has not started firing on its own shadow — both of which
this migration got wrong at least once before these checks caught it.

The cookie is injected at the repository boundary because what is under
test is the SIGNING and REQUEST path, not where credentials are stored.
Everything downstream runs exactly as it does in production.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.live_douyin

#: A video that exists and is publicly reachable. If douyin ever deletes it
#: these tests fail loudly rather than silently passing on an empty result.
TARGET_URL = "https://v.douyin.com/-eB_iaHFsoU/"
TARGET_AWEME_ID = "7684532239228740849"

_COOKIE_ENV = "DOUYIN_ACCEPTANCE_COOKIE_FILE"


def _load_cookie() -> str:
    path = os.environ.get(_COOKIE_ENV, "").strip()
    if not path:
        pytest.skip(f"set {_COOKIE_ENV} to run the live douyin acceptance")
    cookie_file = Path(path)
    if not cookie_file.is_file():
        pytest.skip(f"{_COOKIE_ENV} does not point at a file: {path}")
    cookie = cookie_file.read_text(encoding="utf-8").strip()
    if "UIFID=" not in cookie:
        # Without UIFID the HTTP tier cannot sign at all, and the failure
        # would look like a code defect instead of a stale cookie.
        pytest.skip("acceptance cookie has no UIFID — export a fresh one")
    return cookie


@pytest.fixture
def cookie() -> str:
    return _load_cookie()


@pytest.fixture
def cookie_sources(cookie: str):
    """Feed the real cookie to both tiers for the duration of a test."""
    abogus = "app.services.media.parsers.douyin_parse.abogus_parser.ABogusDouyinParser"
    camoufox = "app.services.media.parsers.douyin_parse.camoufox_parser.CamoufoxParser"
    with (
        patch(
            f"{abogus}._cookie_from_user_config",
            new=AsyncMock(return_value=(cookie, {})),
        ),
        patch(f"{abogus}._cookie_from_redis", new=AsyncMock(return_value="")),
        patch(f"{camoufox}._get_user_cookie_text", new=AsyncMock(return_value=cookie)),
    ):
        yield


@pytest.fixture(autouse=True)
async def _close_browser():
    """Never leave a Firefox behind, however the test ends."""
    yield
    from app.services.media.parsers.douyin_parse.camoufox_parser import CamoufoxParser

    await CamoufoxParser.close()


def _media_url_count(detail: dict) -> int:
    video = detail.get("video") or {}
    play = (video.get("play_addr") or {}).get("url_list") or []
    download = (video.get("download_addr") or {}).get("url_list") or []
    images = detail.get("images") or []
    return len(play) + len(download) + len(images)


def _running_browsers() -> set[str]:
    """PIDs of real browser binaries, for the 'no browser started' checks.

    Matching on command-line text does not work here: a developer shell can
    carry the word "camoufox" in its own argv, which once made this report
    eight browsers while none were running.
    """
    import subprocess

    names = {"camoufox-bin", "camoufox", "firefox-bin", "chrome", "chromium"}
    out = subprocess.run(
        ["ps", "-eo", "pid=,comm="], capture_output=True, text=True, check=False
    ).stdout
    found = set()
    for line in out.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[1].strip() in names:
            found.add(parts[0])
    return found


# ------------------------------------------------------------------ §6.1


@pytest.mark.asyncio
async def test_http_abogus_returns_the_target_video(cookie_sources):
    """§6.1 — the HTTP tier alone, no browser, real signature."""
    from app.services.media.parsers.douyin_parse.abogus_parser import (
        ABogusDouyinParser,
    )

    before = _running_browsers()
    detail = await ABogusDouyinParser.parse(TARGET_URL, user_id="acceptance")
    # Set difference: the developer's own Chrome churns renderers
    # constantly, so comparing counts would fail for unrelated reasons.
    assert _running_browsers() - before == set(), "HTTP tier started a browser"

    assert isinstance(detail, dict) and detail, "no aweme_detail returned"
    assert str(detail.get("aweme_id")) == TARGET_AWEME_ID
    assert _media_url_count(detail) > 0, "no play/download/image URLs"


@pytest.mark.asyncio
async def test_http_request_satisfies_both_argus_gates(cookie_sources):
    """§6.1 wire level — HTTP 200, `status_code=0`, and the `uifid` HEADER.

    The header is the half that is easy to lose: sending UIFID only as a
    cookie returns `Uifid Not Found` even with a full 59-cookie session, so
    a refactor that drops the header still "sends the cookie" and still
    fails 100% of the time.
    """
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
            except Exception:
                seen["status_code"] = "<non-json>"
        return resp

    with patch.object(httpx.AsyncClient, "get", spy_get):
        await ABogusDouyinParser.parse(TARGET_URL, user_id="acceptance")

    assert seen.get("status") == 200, f"HTTP {seen.get('status')}"
    assert seen.get("status_code") == 0, f"douyin status_code={seen.get('status_code')}"
    assert "uifid" in seen.get("headers", set()), "request lost the uifid header"
    for field in ("a_bogus=", "timestamp=", "x-secsdk-web-signature="):
        assert field in seen["query"], f"signed URL missing {field}"


# ------------------------------------------------------------------ §6.2


@pytest.mark.asyncio
async def test_camoufox_returns_the_target_video(cookie_sources):
    """§6.2 — the browser tier alone, against a real page.

    A `DouyinParseError(CAPTCHA)` here is a REAL acceptance failure per
    spec §6.2, not a skip. It means either douyin is challenging this
    session or the detector has regressed — and the detector regressing is
    exactly what happened twice during this migration.
    """
    from app.services.media.parsers.douyin_parse.camoufox_parser import CamoufoxParser

    detail = await CamoufoxParser.fetch_one_video(TARGET_URL, user_id="acceptance")

    assert isinstance(detail, dict) and detail, "no aweme_detail captured"
    assert str(detail.get("aweme_id")) == TARGET_AWEME_ID
    assert _media_url_count(detail) > 0, "no play/download/image URLs"


@pytest.mark.asyncio
async def test_drissionpage_is_gone_from_the_runtime():
    """§6.2 — the dependency is deleted, not merely unused."""
    with pytest.raises(ImportError):
        import DrissionPage  # noqa: F401


# ------------------------------------------------------------------ §6.3


@pytest.mark.asyncio
async def test_chain_prefers_http_and_never_starts_a_browser(cookie_sources):
    """§6.3 — ordering is the whole point of the tier split."""
    from app.services.media.parsers.douyin_parse import parse_chain
    from app.services.media.parsers.douyin_parse.camoufox_parser import CamoufoxParser

    calls: list[str] = []

    async def spy_camoufox(url, **kwargs):
        calls.append(url)
        return None

    with (
        patch.object(
            parse_chain,
            "get_douyin_method_flags",
            AsyncMock(return_value={"abogus": True, "camoufox": True}),
        ),
        patch.object(CamoufoxParser, "fetch_one_video", spy_camoufox),
    ):
        result = await parse_chain.fetch_douyin_detail(TARGET_URL, user_id="acceptance")

    assert result is not None, "chain returned nothing"
    _detail, parsed, method = result
    assert method == "abogus", f"expected abogus first, got {method}"
    assert calls == [], "Camoufox was invoked even though HTTP succeeded"
    assert str(parsed.get("platform_id")) == TARGET_AWEME_ID


@pytest.mark.asyncio
async def test_chain_falls_back_to_a_real_browser_when_http_fails(cookie_sources):
    """§6.3 — the fallback is wired to a browser that actually works.

    Forces the HTTP tier to come back empty, so Camoufox has to carry the
    request end to end. A fallback nobody ever exercises is a fallback that
    is broken and nobody knows.
    """
    from app.services.media.parsers.douyin_parse import parse_chain
    from app.services.media.parsers.douyin_parse.abogus_parser import (
        ABogusDouyinParser,
    )

    with (
        patch.object(
            parse_chain,
            "get_douyin_method_flags",
            AsyncMock(return_value={"abogus": True, "camoufox": True}),
        ),
        patch.object(ABogusDouyinParser, "parse", AsyncMock(return_value=None)),
    ):
        result = await parse_chain.fetch_douyin_detail(TARGET_URL, user_id="acceptance")

    assert result is not None, "chain produced nothing via the browser tier"
    _detail, parsed, method = result
    assert method == "camoufox", f"expected camoufox fallback, got {method}"
    assert str(parsed.get("platform_id")) == TARGET_AWEME_ID
