"""nginx direct-serve signer (million-files P3).

Pins the secure_link signature formula (must match the nginx template
byte-for-byte), the DB-toggle gating, and the cover endpoint's 302 branch
with its FileResponse fallback.
"""

from __future__ import annotations

import base64
import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request

from app.services.media.nginx_direct import (
    DirectServeConfig,
    _cache,
    direct_serve_config,
    maybe_direct_redirect,
    sign_direct_url,
)


def _request() -> Request:
    """Minimal ASGI Request for handlers routed through serve_stored_file."""
    return Request({"type": "http", "method": "GET", "path": "/", "headers": []})


CFG = DirectServeConfig(
    base_url="https://host:8081", ttl_seconds=86400, secret="s3cret"
)


@pytest.fixture(autouse=True)
def _reset_cache():
    _cache["at"] = 0.0
    _cache["cfg"] = None
    yield
    _cache["at"] = 0.0
    _cache["cfg"] = None


def test_signature_matches_nginx_secure_link_formula():
    """nginx computes md5("$secure_link_expires$uri secret") over the
    PERCENT-DECODED $uri, base64url-encoded without padding. Recompute
    independently from the emitted URL and require equality."""
    url = sign_direct_url(CFG, "teams/42/uploads/rid/v1/файл name.jpg")
    # Parse st/e back out.
    query = url.split("?", 1)[1]
    params = dict(p.split("=", 1) for p in query.split("&"))
    expires = params["e"]
    # The DECODED uri (what nginx sees in $uri).
    decoded_uri = "/f/teams/42/uploads/rid/v1/файл name.jpg"
    expected = (
        base64.urlsafe_b64encode(
            hashlib.md5(f"{expires}{decoded_uri} s3cret".encode()).digest()
        )
        .rstrip(b"=")
        .decode()
    )
    assert params["st"] == expected
    # And the path in the URL is percent-encoded (space, cyrillic).
    assert "%20" in url and "%D1" in url


@pytest.mark.asyncio
async def test_config_disabled_without_secret_env(monkeypatch):
    monkeypatch.delenv("NGINX_SECURE_LINK_SECRET", raising=False)
    fetch = AsyncMock()
    with patch("app.db.engine.fetch_one", new=fetch):
        assert await direct_serve_config() is None
    fetch.assert_not_awaited()  # no secret → never even hits the DB


@pytest.mark.asyncio
async def test_config_reads_db_toggle(monkeypatch):
    monkeypatch.setenv("NGINX_SECURE_LINK_SECRET", "s3cret")
    with patch(
        "app.db.engine.fetch_one",
        new=AsyncMock(
            return_value={"value": '{"enabled": true, "base_url": "https://h:8081/"}'}
        ),
    ):
        cfg = await direct_serve_config()
    assert cfg is not None
    assert cfg.base_url == "https://h:8081"  # trailing slash stripped
    assert cfg.ttl_seconds == 86400  # default


@pytest.mark.asyncio
async def test_config_off_or_error_returns_none(monkeypatch):
    monkeypatch.setenv("NGINX_SECURE_LINK_SECRET", "s3cret")
    with patch(
        "app.db.engine.fetch_one",
        new=AsyncMock(return_value={"value": {"enabled": False}}),
    ):
        assert await direct_serve_config() is None
    _cache["at"] = 0.0
    with patch(
        "app.db.engine.fetch_one", new=AsyncMock(side_effect=RuntimeError("db"))
    ):
        assert await direct_serve_config() is None  # never raises


@pytest.mark.asyncio
async def test_cover_redirects_when_enabled(tmp_path, monkeypatch):
    from app.api.resources_crud_router import serve_resource_cover

    res = {
        "id": "1",
        "media_id": None,
        "thumbnail_path": "thumbs/t.webp",
        "cover_image_path": None,
        "file_path": "a.mp4",
        "mime_type": "video/mp4",
        "file_size_bytes": 5,
    }
    (tmp_path / "thumbs").mkdir()
    (tmp_path / "thumbs/t.webp").write_bytes(b"webp")

    repo = MagicMock()
    repo.get_resource_by_id = AsyncMock(return_value=res)
    with (
        patch("app.api.resources_crud_router.ResourcesRepository", return_value=repo),
        patch("app.core.config.settings.DOWNLOAD_PATH", str(tmp_path)),
        patch(
            "app.services.media.nginx_direct.direct_serve_config",
            new=AsyncMock(return_value=CFG),
        ),
    ):
        resp = await serve_resource_cover("1", _request())

    assert resp.status_code == 302
    assert resp.headers["location"].startswith("https://host:8081/f/thumbs/t.webp?st=")
    assert resp.headers["cache-control"] == "private, max-age=600"


@pytest.mark.asyncio
async def test_cover_falls_back_to_fileresponse_when_disabled(tmp_path):
    from app.api.resources_crud_router import serve_resource_cover

    res = {
        "id": "1",
        "media_id": None,
        "thumbnail_path": "thumbs/t.webp",
        "cover_image_path": None,
        "file_path": "a.mp4",
        "mime_type": "video/mp4",
        "file_size_bytes": 5,
    }
    (tmp_path / "thumbs").mkdir()
    (tmp_path / "thumbs/t.webp").write_bytes(b"webp")

    repo = MagicMock()
    repo.get_resource_by_id = AsyncMock(return_value=res)
    with (
        patch("app.api.resources_crud_router.ResourcesRepository", return_value=repo),
        patch("app.core.config.settings.DOWNLOAD_PATH", str(tmp_path)),
        patch(
            "app.services.media.nginx_direct.direct_serve_config",
            new=AsyncMock(return_value=None),
        ),
    ):
        resp = await serve_resource_cover("1", _request())

    assert resp.__class__.__name__ == "FileResponse"


def test_template_and_signer_share_the_formula():
    """The nginx template's secure_link_md5 line must keep the exact shape
    the signer implements — drift here 403s every cover in production."""
    from pathlib import Path

    template_path = (
        Path(__file__).resolve().parents[2]
        / "docker"
        / "nginx-templates"
        / "default.conf.template"
    )
    template = template_path.read_text(encoding="utf-8")
    assert (
        'secure_link_md5 "$secure_link_expires$uri ${NGINX_SECURE_LINK_SECRET}";'
        in template
    )
    assert "location /f/" in template
    assert "alias /app/downloads/;" in template.split("location /f/")[1].split("}")[0]
