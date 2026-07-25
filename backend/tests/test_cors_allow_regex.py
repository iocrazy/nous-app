"""Regression guards for the CORS ``allow_origin_regex``.

CORS misconfiguration is silent in prod: a missed origin returns no
``Access-Control-Allow-Origin`` header and the browser blocks the request
with zero server-side trace. This test pins the patterns we expect the
regex to accept and reject so no future edit loosens ("everything goes")
or tightens ("Pages previews break again") without a loud CI failure.
"""

from __future__ import annotations

import re

from app.main import _CORS_ALLOW_REGEX

_RX = re.compile(_CORS_ALLOW_REGEX)


def test_localhost_any_port_accepted() -> None:
    """Local dev on localhost / 127.0.0.1 must pass any port."""
    assert _RX.match("http://localhost:5173")
    assert _RX.match("http://localhost:5177")
    assert _RX.match("http://127.0.0.1:8080")
    assert _RX.match("https://localhost:3000")


def test_rfc1918_internal_accepted() -> None:
    """Internal-network dev (e.g. LAN access to NAS) must still work."""
    assert _RX.match("http://10.0.0.5:8080")
    assert _RX.match("http://192.168.50.9:9080")


def test_localhost_without_port_rejected() -> None:
    """Regex requires explicit port to stay scoped to dev use."""
    assert not _RX.match("http://localhost")
    assert not _RX.match("http://127.0.0.1")


def test_pages_preview_branch_accepted() -> None:
    """Cloudflare Pages branch alias per PR must pass."""
    assert _RX.match("https://ai-library-nav-v5.nous-app.pages.dev")
    assert _RX.match("https://master.nous-app.pages.dev")


def test_pages_immutable_deployment_accepted() -> None:
    """Pages deployment URL (hash, not branch) must also pass."""
    assert _RX.match("https://abc123def.nous-app.pages.dev")
    assert _RX.match("https://55ccbbyylnxzmsfozfkyybuka7cw.nous-app.pages.dev")


def test_pages_other_project_rejected() -> None:
    """A Pages deployment under a different project must NOT pass — we
    only trust our own project's deployments."""
    assert not _RX.match("https://attacker.evil-app.pages.dev")
    assert not _RX.match("https://nous-app-evil.pages.dev")


def test_pages_nested_subdomain_rejected() -> None:
    """``[a-z0-9-]+`` must not swallow dots — an attacker-controlled
    deeper subdomain (e.g. ``evil.com.nous-app.pages.dev`` style nesting)
    must not slip through."""
    assert not _RX.match("https://evil.attacker.nous-app.pages.dev")
    assert not _RX.match("https://nous-app.pages.dev.evil.com")


def test_http_pages_rejected() -> None:
    """HTTP (not HTTPS) Pages is rejected — prevents downgrade attacks."""
    assert not _RX.match("http://master.nous-app.pages.dev")


def test_prod_domain_not_matched_by_regex() -> None:
    """The production domain lives in ``CORS_ORIGINS`` (allowlist), not
    the regex. If it ever shifts into the regex that's a config
    regression — callers should see this fail loudly."""
    # app.nous.ink and the cn/api entrypoints are handled by
    # allow_origins=[...], not allow_origin_regex.
    assert not _RX.match("https://app.nous.ink")
    assert not _RX.match("https://cn.nous.ink:88")
    assert not _RX.match("https://api.nous.ink")


def test_random_untrusted_origins_rejected() -> None:
    assert not _RX.match("https://evil.example.com")
    assert not _RX.match("https://nous.evil.com")
    assert not _RX.match("")
