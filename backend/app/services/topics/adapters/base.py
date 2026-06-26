from __future__ import annotations

import hashlib
from dataclasses import dataclass
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
    # 1-based board position for ranked sources (newsnow). None for unranked
    # sources (RSS) — feeds heat's persistence-only fallback.
    rank: Optional[int] = None

    def __post_init__(self) -> None:
        if self.captured_at is None:
            self.captured_at = datetime.now(timezone.utc)
        if self.origin_url is None:
            self.origin_url = self.url


def make_dedup_key(source_id: str, *, url: Optional[str], title: str) -> str:
    """Stable per-source dedup key: ``<source_id>:<sha1(title)>``.

    Keyed on (source_id, title), NOT url. Hot-list items (Weibo/Douyin/…) carry
    URLs that drift between fetches (search links with changing params), so a
    url-based key spawned a NEW row for the same headline every cycle — visible
    duplicates AND broken heat accumulation (the same topic never merged, so its
    on-board persistence never counted). Title is the stable identity for a
    hot-list entry; keying on it collapses the same headline into one row that
    MERGES across fetches (heat accumulates). Cross-source duplicates (the same
    news on two platforms) are intentionally separate rows here — clustering
    (topic_groups / source_count) handles that signal. Falls back to a url hash
    only when there's no title.
    """
    norm_title = title.strip().lower()
    if norm_title:
        title_hash = hashlib.sha1(norm_title.encode("utf-8")).hexdigest()  # nosec
        return f"{source_id}:{title_hash}"
    basis = (url or "").strip().lower().rstrip("/")
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()  # nosec - non-crypto


class SourceAdapter:
    """Normalize any source into HotspotCandidate list. Subclass per kind."""

    async def fetch(self, source: dict) -> list[HotspotCandidate]:
        raise NotImplementedError
