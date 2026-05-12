"""Smoke test for /api/version — the endpoint CI deploy-verify polls.

Background:
On 2026-05-12 the deploy workflow kept reporting ✅ success because the
Watchtower webhook returned 200 without actually triggering a pull, so
the new image sat in ACR while prod kept the old one. The new CI step
"Verify deploy (poll prod for new commit SHA)" polls /api/version and
fails LOUDLY if the response doesn't carry the just-built sha — making
this endpoint a hard load-bearing contract.

Shape contract:
    GET /api/version → {
      "commit_sha":   "<7-hex-short or null>",
      "commit_count": <int or null>,
      "service":      "backend",
      "version":      "<semver or 'latest'>",
      "available":    true|false,
    }

available=false when build-info.json is missing (dev / fresh checkout),
so the contract holds in both dev and prod containers.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


def _client() -> TestClient:
    # Import inside fn so tests don't pay the cost when collected only
    from app.main import app

    return TestClient(app)


def test_version_endpoint_missing_build_info_returns_available_false(tmp_path):
    """In dev (no /app/build-info.json baked into Docker), endpoint must
    still respond with {available: false} — not 500. This lets CI
    distinguish 'unreachable' from 'deployed but stale'."""

    def fake_exists(self):
        return False

    with patch.object(Path, "exists", fake_exists):
        client = _client()
        r = client.get("/api/version")
        assert r.status_code == 200
        body = r.json()
        assert body["available"] is False
        assert body["commit_sha"] is None


def test_version_endpoint_reads_build_info_when_present(tmp_path):
    """In prod (build-info.json baked at CI build time), endpoint
    returns the commit_sha. CI's deploy-verify step compares the first
    7 chars of this against github.sha."""

    real_read_text = Path.read_text

    def fake_exists(self):
        return str(self) == "/app/build-info.json"

    def fake_read_text(self, encoding="utf-8"):
        if str(self) == "/app/build-info.json":
            return json.dumps(
                {
                    "service": "backend",
                    "version": "latest",
                    "commit_sha": "abc1234",
                    "commit_count": 5,
                }
            )
        return real_read_text(self, encoding=encoding)

    with (
        patch.object(Path, "exists", fake_exists),
        patch.object(Path, "read_text", fake_read_text),
    ):
        client = _client()
        r = client.get("/api/version")
        assert r.status_code == 200
        body = r.json()
        assert body["available"] is True
        assert body["commit_sha"] == "abc1234"
        assert body["commit_count"] == 5
        assert body["service"] == "backend"
        assert body["version"] == "latest"


def test_version_endpoint_malformed_build_info_does_not_500(tmp_path):
    """A corrupt build-info.json must not 500 the endpoint — CI's
    deploy-verify reads from it on every prod, including ones running
    pre-this-PR images. Be defensive."""
    real_read_text = Path.read_text

    def fake_exists(self):
        return str(self) == "/app/build-info.json"

    def fake_read_text(self, encoding="utf-8"):
        if str(self) == "/app/build-info.json":
            return "{not valid json"
        return real_read_text(self, encoding=encoding)

    with (
        patch.object(Path, "exists", fake_exists),
        patch.object(Path, "read_text", fake_read_text),
    ):
        client = _client()
        r = client.get("/api/version")
        assert r.status_code == 200
        body = r.json()
        assert body["available"] is False
        assert body["commit_sha"] is None
