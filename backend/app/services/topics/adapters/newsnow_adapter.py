from __future__ import annotations

import httpx

from app.core.config import settings
from app.services.topics.adapters.base import HotspotCandidate, SourceAdapter

_OK_STATUS = {"success", "cache"}


class NewsNowAdapter(SourceAdapter):
    def __init__(self, api_url: str | None = None) -> None:
        # NEWSNOW_API_URL comes from the bind-mounted /app/.env via pydantic
        # Settings (NOT os.environ) — read it off `settings`, not os.getenv.
        self.api_url = (api_url or settings.NEWSNOW_API_URL).rstrip("/")

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
        url = f"{self.api_url}/api/s?id={platform_id}&latest"
        data = await self._get_json(url, timeout=15.0)
        status = data.get("status", "unknown")
        if status not in _OK_STATUS:
            raise ValueError(f"newsnow status not ok: {status}")
        items = data.get("items") or []
        label = source.get("name", platform_id)
        out: list[HotspotCandidate] = []
        for it in items:
            title = (it.get("title") or "").strip()
            if not title:
                continue
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
                )
            )
        if not out:
            raise ValueError(f"newsnow produced 0 items: {platform_id}")
        return out
