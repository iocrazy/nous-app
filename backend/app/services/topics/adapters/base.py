from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class HotspotCandidate:
    title: str
    url: Optional[str] = None
    origin_url: Optional[str] = None
    content: str = ""
    source_label: str = ""
    captured_at: Optional[datetime] = None
    media_url: Optional[str] = None
    cover_url: Optional[str] = None

    def __post_init__(self) -> None:
        if self.captured_at is None:
            self.captured_at = datetime.now(timezone.utc)
        if self.origin_url is None:
            self.origin_url = self.url


def make_dedup_key(source_id: str, *, url: Optional[str], title: str) -> str:
    """Stable dedup key. Prefer normalized url; fall back to (source_id, title).

    When a URL is present the key is a SHA1 of the normalized URL so two
    different titles pointing at the same URL collapse into one candidate.

    When no URL is available the key is ``<source_id>:<sha1(title)>`` — the
    literal source_id prefix makes the key human-readable and ensures keys from
    different sources never collide even if titles match.
    """
    basis = (url or "").strip().lower().rstrip("/")
    if basis:
        return hashlib.sha1(basis.encode("utf-8")).hexdigest()  # nosec - non-crypto
    title_hash = hashlib.sha1(
        title.strip().lower().encode("utf-8")
    ).hexdigest()  # nosec
    return f"{source_id}:{title_hash}"


class SourceAdapter:
    """Normalize any source into HotspotCandidate list. Subclass per kind."""

    async def fetch(self, source: dict) -> list[HotspotCandidate]:
        raise NotImplementedError
