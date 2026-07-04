from __future__ import annotations

import httpx
from loguru import logger

from app.core.config import settings

# Reused settings reader (degrade-safe, never raises) — the same seam that
# backs platform.ai_providers (#990). Admin-manageable config lives in the
# DATABASE per repo convention; env is bootstrap fallback only.
from app.services.ai.governance.ai_governance import _read_raw
from app.services.topics.adapters.base import HotspotCandidate, SourceAdapter

_OK_STATUS = {"success", "cache"}

# system_settings key holding the NewsNow endpoint. Non-empty DB value wins
# over the NEWSNOW_API_URL env fallback; an explicit constructor override
# (tests / one-off scripts) wins over both and skips the DB read entirely.
NEWSNOW_API_URL_KEY = "newsnow.api_url"


class NewsNowAdapter(SourceAdapter):
    def __init__(self, api_url: str | None = None) -> None:
        # Explicit override is a deliberate pin — resolved eagerly, no DB read.
        self._explicit_api_url = api_url.rstrip("/") if api_url else None

    async def _resolve_api_url(self) -> str:
        """DB-first endpoint resolution: explicit override > system_settings
        ``newsnow.api_url`` > ``settings.NEWSNOW_API_URL`` (env fallback).
        Reader failures degrade to the env fallback — a settings-table blip
        must never break hotspot fetching."""
        if self._explicit_api_url:
            return self._explicit_api_url
        db_url = ""
        try:
            raw = await _read_raw(NEWSNOW_API_URL_KEY)
            db_url = str(raw).strip() if raw is not None else ""
        except Exception as exc:  # noqa: BLE001 — degrade to env fallback
            logger.warning(f"[newsnow] settings read failed ({exc!r}); using env")
        return (db_url or settings.NEWSNOW_API_URL).rstrip("/")

    async def _get_json(self, url: str, timeout: float) -> dict:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(url, timeout=timeout)
            resp.raise_for_status()
            return resp.json()

    async def fetch(self, source: dict) -> list[HotspotCandidate]:
        cfg = source.get("config") or {}
        platform_id = cfg.get("platform_id")
        if not platform_id:
            raise ValueError("newsnow source missing config.platform_id")
        api_url = await self._resolve_api_url()
        url = f"{api_url}/api/s?id={platform_id}&latest"
        data = await self._get_json(url, timeout=15.0)
        status = data.get("status", "unknown")
        if status not in _OK_STATUS:
            raise ValueError(f"newsnow status not ok: {status}")
        items = data.get("items") or []
        label = source.get("name", platform_id)
        out: list[HotspotCandidate] = []
        # newsnow returns an ordered leaderboard — list position IS the board
        # rank. 1-based, counting only kept items so skipped (titleless)
        # entries don't inflate later ranks.
        rank = 0
        for it in items:
            title = (it.get("title") or "").strip()
            if not title:
                continue
            rank += 1
            out.append(
                HotspotCandidate(
                    title=title,
                    url=it.get("url"),
                    content=(
                        (it.get("extra", {}) or {}).get("info", "")
                        if isinstance(it.get("extra"), dict)
                        else ""
                    ),
                    source_label=label,
                    rank=rank,
                )
            )
        if not out:
            raise ValueError(f"newsnow produced 0 items: {platform_id}")
        return out
