"""Sprint 8 — link_understanding fetcher tests."""
from __future__ import annotations

import httpx
import pytest
import respx

from app.boundary.types import ValidatedURL
from app.services.ai.prompts import link_understanding as lu
from app.services.ai.prompts.link_understanding import (
    DEFAULT_BODY_EXCERPT_CHARS,
    LinkSummary,
    LinkUnderstandingError,
    understand_link,
)


@pytest.fixture(autouse=True)
def _stub_boundary(monkeypatch):
    """Bypass boundary URL/DNS guards so tests don't hit real DNS
    (CI environments + ISPs that hijack example.com both bite us).
    The boundary primitives are exhaustively covered by test_url_guard.py
    + test_safe_http.py + test_pinned_dns.py — here we just need them
    to pass through so respx can intercept the HTTP layer.

    Three stub points:
      - link_understanding's direct validate_url_async call
      - safe_http's redirect re-validation hook
      - PinnedDNSResolver.resolve_and_validate (also called by safe_http)
    """
    from app.boundary import pinned_dns, safe_http

    async def _passthrough_url(url: str, **_kw) -> ValidatedURL:
        return ValidatedURL(url)

    async def _passthrough_pin(self, host: str) -> str:
        return "127.0.0.1"  # placeholder — respx intercepts before connect

    monkeypatch.setattr(lu, "validate_url_async", _passthrough_url)
    monkeypatch.setattr(safe_http, "validate_url_async", _passthrough_url)
    monkeypatch.setattr(
        pinned_dns.PinnedDNSResolver, "resolve_and_validate", _passthrough_pin
    )


HTML_BODY = """
<!DOCTYPE html>
<html>
<head>
    <title>Test Page Title</title>
    <meta name="description" content="a useful description">
    <meta property="og:title" content="OG Title">
    <meta property="og:description" content="og description here">
</head>
<body>
    <script>var secret = 'should be stripped';</script>
    <style>.x { color: red; }</style>
    <h1>Heading</h1>
    <p>First paragraph of the body.</p>
    <p>Second paragraph    with extra   spaces.</p>
</body>
</html>
"""


# ─── Happy path ───────────────────────────────────────────────────────


@pytest.mark.unit
@respx.mock
async def test_understand_html_returns_populated_summary():
    respx.get("https://example.com/article").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text=HTML_BODY,
        )
    )
    summary = await understand_link("https://example.com/article")
    assert isinstance(summary, LinkSummary)
    assert summary.status_code == 200
    assert summary.title == "Test Page Title"
    assert summary.description == "a useful description"
    assert "First paragraph" in summary.body_text
    assert "Second paragraph" in summary.body_text
    # Script body removed
    assert "secret" not in summary.body_text
    # Style body removed
    assert "color: red" not in summary.body_text
    # Whitespace collapsed
    assert "  " not in summary.body_text  # two consecutive spaces gone
    # Neutralized payload populated and ready to inject
    assert summary.neutralized is not None
    assert "EXTERNAL_CONTENT_" in summary.neutralized.wrapped


@pytest.mark.unit
@respx.mock
async def test_meta_og_description_used_when_no_meta_description():
    """og:description is the fallback when <meta name=description> is absent."""
    body = """
    <html><head>
        <title>X</title>
        <meta property="og:description" content="og only">
    </head><body>hi</body></html>
    """
    respx.get("https://example.com/").mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/html"}, text=body
        )
    )
    summary = await understand_link("https://example.com/")
    assert summary.description == "og only"


@pytest.mark.unit
@respx.mock
async def test_non_html_returns_empty_body():
    """PDF / image / video bodies — keep headers, skip parsing."""
    respx.get("https://example.com/file.pdf").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "application/pdf"},
            content=b"%PDF-1.4 binary",
        )
    )
    summary = await understand_link("https://example.com/file.pdf")
    assert summary.content_type == "application/pdf"
    assert summary.body_text == ""
    assert summary.title is None
    assert summary.neutralized is None


# ─── Error paths ──────────────────────────────────────────────────────


@pytest.mark.unit
@respx.mock
async def test_4xx_raises():
    respx.get("https://example.com/missing").mock(
        return_value=httpx.Response(404, text="not found")
    )
    with pytest.raises(LinkUnderstandingError, match="404"):
        await understand_link("https://example.com/missing")


@pytest.mark.unit
@respx.mock
async def test_5xx_raises():
    respx.get("https://example.com/").mock(
        return_value=httpx.Response(503, text="down")
    )
    with pytest.raises(LinkUnderstandingError, match="503"):
        await understand_link("https://example.com/")


@pytest.mark.unit
async def test_invalid_url_rejected_at_boundary(monkeypatch):
    """SSRF / scheme / DNS-rebinding rejection bubbles up as our error type.

    Re-installs the real validator on top of the autouse stub so this
    test sees the actual boundary rejection rather than the passthrough.
    """
    from app.boundary import url_guard
    monkeypatch.setattr(lu, "validate_url_async", url_guard.validate_url_async)
    with pytest.raises(LinkUnderstandingError, match="boundary"):
        await understand_link("file:///etc/passwd")


@pytest.mark.unit
@respx.mock
async def test_oversized_body_rejected():
    big = "x" * (3 * 1024 * 1024)  # 3 MiB > default 2 MiB cap
    respx.get("https://example.com/big").mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/html"}, text=big
        )
    )
    with pytest.raises(LinkUnderstandingError, match="byte cap"):
        await understand_link("https://example.com/big")


# ─── Excerpt + neutralization ─────────────────────────────────────────


@pytest.mark.unit
@respx.mock
async def test_long_body_truncated_to_excerpt():
    """The neutralized excerpt obeys body_excerpt_chars; full body_text
    is also retained for the caller."""
    long_html = "<html><head><title>T</title></head><body>" + ("x " * 5000) + "</body></html>"
    respx.get("https://example.com/long").mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/html"}, text=long_html
        )
    )
    summary = await understand_link(
        "https://example.com/long", body_excerpt_chars=200
    )
    # Caller gets the FULL cleaned body (their choice what to do with it)
    assert len(summary.body_text) > 200
    # Neutralized excerpt is bounded
    assert summary.neutralized is not None
    assert len(summary.neutralized.body) <= 200 + 100  # truncation marker overhead


@pytest.mark.unit
def test_to_summary_dict_omits_full_body():
    """Telemetry-friendly form: byte/char counts only, never the body."""
    s = LinkSummary(
        url="https://x/",
        status_code=200,
        content_type="text/html",
        title="t",
        description="d",
        body_text="hello world",
    )
    out = s.to_summary_dict()
    assert "body_text" not in out
    assert "neutralized" not in out
    assert out["body_chars"] == len("hello world")


@pytest.mark.unit
def test_default_excerpt_under_neutralizer_hard_limit():
    """Sanity: the default excerpt cap fits comfortably in the neutralizer's
    HARD_LIMIT_CHARS (catches future tweaks that would break inject path)."""
    from app.boundary.external_text import HARD_LIMIT_CHARS

    assert DEFAULT_BODY_EXCERPT_CHARS < HARD_LIMIT_CHARS
