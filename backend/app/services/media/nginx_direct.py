"""nginx direct-serve signer (million-files P3; design:
docs/superpowers/specs/2026-07-06-million-files-single-user-design.md).

File BYTES leave the Python process: when enabled, cover/file endpoints
answer with a 302 to the existing HLS nginx container (:8081), which
serves the file straight off the shared volume via sendfile. FastAPI keeps
what it's good at — auth + the lazy-thumbnail trigger — and stops being a
byte pump (one browse screen = 50 covers = 50 Python copy loops today).

Contract with docker/nginx-templates/default.conf.template:

    location /f/ {
        alias /app/downloads/;
        secure_link $arg_st,$arg_e;
        secure_link_md5 "$secure_link_expires$uri ${NGINX_SECURE_LINK_SECRET}";
        ...
    }

nginx's $uri is the PERCENT-DECODED path — the md5 input must use the raw
(unquoted) path even though the emitted URL quotes it.

Config:
  - toggle + base_url live in the DB (env→DB convention):
      system_settings key 'nginx_direct_serve'
      value: {"enabled": true, "base_url": "https://host:8081",
              "ttl_seconds": 86400}
    OFF by default / missing row = disabled → callers fall back to
    FileResponse, byte-for-byte today's behavior.
  - the shared secret is deployment infrastructure (nginx can't read the
    DB) → env NGINX_SECURE_LINK_SECRET on BOTH containers, an allowed
    env-whitelist entry like DBOS_DATABASE_URL.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

from loguru import logger

_SETTINGS_KEY = "nginx_direct_serve"
_CACHE_TTL_S = 60.0

_cache: dict = {"at": 0.0, "cfg": None}


@dataclass(frozen=True)
class DirectServeConfig:
    base_url: str
    ttl_seconds: int
    secret: str


async def direct_serve_config() -> Optional[DirectServeConfig]:
    """The active config, or None when disabled/unconfigured. Cached 60s
    in-process (the sidebar-count RPC pattern: hot path, slow-moving row)."""
    now = time.monotonic()
    if now - _cache["at"] < _CACHE_TTL_S:
        return _cache["cfg"]

    cfg: Optional[DirectServeConfig] = None
    try:
        secret = os.environ.get("NGINX_SECURE_LINK_SECRET", "")
        if secret:
            from sqlalchemy import select

            from app.db.session import read_scope
            from app.models import SystemSettings

            async with read_scope() as session:
                value = await session.scalar(
                    select(SystemSettings.value).where(
                        SystemSettings.key == _SETTINGS_KEY
                    )
                )
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except ValueError:
                    value = None
            if (
                isinstance(value, dict)
                and value.get("enabled")
                and value.get("base_url")
            ):
                ttl = value.get("ttl_seconds") or 86400
                cfg = DirectServeConfig(
                    base_url=str(value["base_url"]).rstrip("/"),
                    ttl_seconds=int(ttl),
                    secret=secret,
                )
    except Exception as e:  # config lookup must never break file serving
        logger.warning(f"[nginx_direct] config lookup failed: {e}")
        cfg = None

    _cache["at"] = now
    _cache["cfg"] = cfg
    return cfg


def sign_direct_url(cfg: DirectServeConfig, rel_path: str) -> str:
    """Signed URL for nginx secure_link: md5 input is
    '{expires}{decoded_uri} {secret}', base64url without padding —
    the exact server-side formula in the template above."""
    rel = rel_path.lstrip("/")
    uri = f"/f/{rel}"
    expires = int(time.time()) + cfg.ttl_seconds
    digest = hashlib.md5(f"{expires}{uri} {cfg.secret}".encode()).digest()
    sig = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return f"{cfg.base_url}/f/{quote(rel)}?st={sig}&e={expires}"


async def maybe_direct_redirect(rel_path: str):
    """A 302 RedirectResponse to the signed nginx URL when direct serve is
    on — else None (caller falls back to FileResponse). The 302 itself is
    browser-cacheable for 10 minutes (well inside the signature TTL), which
    absorbs most repeat hits without re-touching the gateway."""
    cfg = await direct_serve_config()
    if cfg is None:
        return None
    from fastapi.responses import RedirectResponse

    return RedirectResponse(
        url=sign_direct_url(cfg, rel_path),
        status_code=302,
        headers={"Cache-Control": "private, max-age=600"},
    )
