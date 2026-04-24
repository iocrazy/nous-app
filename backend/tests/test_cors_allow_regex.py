"""Regression guards for the CORS ``allow_origin_regex``.

CORS misconfiguration is silent in prod: a missed origin returns no
``Access-Control-Allow-Origin`` header and the browser blocks the request
with zero server-side trace. This test pins the patterns we expect the
regex to accept and reject so no future edit loosens ("everything goes")
or tightens ("Vercel previews break again") without a loud CI failure.
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


def test_vercel_preview_branch_accepted() -> None:
    """Vercel branch preview URL per PR must pass."""
    assert _RX.match(
        "https://mediahub-git-ai-library-nav-v5-heygos-projects.vercel.app"
    )
    assert _RX.match("https://mediahub-git-master-heygos-projects.vercel.app")


def test_vercel_immutable_deployment_accepted() -> None:
    """Vercel deployment URL (hash, not branch) must also pass."""
    assert _RX.match("https://mediahub-abc123def-heygos-projects.vercel.app")
    assert _RX.match(
        "https://mediahub-55ccbbyylnxzmsfozfkyybuka7cw-heygos-projects.vercel.app"
    )


def test_vercel_non_project_owner_rejected() -> None:
    """A Vercel preview under a different org must NOT pass — we only
    trust our own project's deployments."""
    assert not _RX.match(
        "https://mediahub-git-attacker-branch-otherorg-projects.vercel.app"
    )
    assert not _RX.match("https://mediahub-git-branch-random.vercel.app")


def test_vercel_non_project_name_rejected() -> None:
    """Only ``mediahub-`` prefix is allowed; other projects rejected."""
    assert not _RX.match("https://notmediahub-git-branch-heygos-projects.vercel.app")
    assert not _RX.match("https://otherapp-heygos-projects.vercel.app")


def test_http_vercel_rejected() -> None:
    """HTTP (not HTTPS) Vercel is rejected — prevents downgrade attacks."""
    assert not _RX.match("http://mediahub-git-branch-heygos-projects.vercel.app")


def test_prod_domain_not_matched_by_regex() -> None:
    """The production domain lives in ``CORS_ORIGINS`` (allowlist), not
    the regex. If it ever shifts into the regex that's a config
    regression — callers should see this fail loudly."""
    # mediahub.heygo.cn is handled by allow_origins=[...], not
    # allow_origin_regex — so the regex should NOT match it.
    assert not _RX.match("https://mediahub.heygo.cn")


def test_random_untrusted_origins_rejected() -> None:
    assert not _RX.match("https://evil.example.com")
    assert not _RX.match("https://mediahub.evil.com")
    assert not _RX.match("")
