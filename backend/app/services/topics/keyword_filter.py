from __future__ import annotations

from app.services.topics.adapters.base import HotspotCandidate


def keyword_filter(
    candidates: list[HotspotCandidate],
    *,
    include: list[str],
    exclude: list[str],
) -> list[HotspotCandidate]:
    inc = [w.lower() for w in include if w.strip()]
    exc = [w.lower() for w in exclude if w.strip()]
    out: list[HotspotCandidate] = []
    for c in candidates:
        hay = f"{c.title} {c.content}".lower()
        if exc and any(w in hay for w in exc):
            continue
        if inc and not any(w in hay for w in inc):
            continue
        out.append(c)
    return out
